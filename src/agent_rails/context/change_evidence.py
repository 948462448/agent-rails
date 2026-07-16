"""Collect and render Task Pack Git evidence behind one Python Interface."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Optional, Sequence, Tuple

from agent_rails.context.markdown import markdown_code, markdown_fence, valid_utf8
from agent_rails.git._runner import run_git
from agent_rails.git.scope import (
    GitScope,
    GitScopeError,
    GitScopeSnapshot,
    resolve_git_scope,
    write_git_scope_snapshot,
)
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    redact_sensitive_output,
)


_STOP_WORDS = frozenset(
    "agent agents rails task pack project repo code change changes work continue "
    "continuing optimize optimization reduce reducing keep keeping with without "
    "from into this that".split()
)
_BINARY_SUFFIXES = (
    ".bmp",
    ".gif",
    ".ico",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".webp",
    ".avif",
    ".heic",
    ".mp3",
    ".mp4",
    ".mov",
    ".ttf",
    ".woff",
    ".woff2",
    ".zip",
    ".gz",
    ".tgz",
    ".bz2",
    ".xz",
    ".7z",
    ".jar",
    ".war",
    ".class",
    ".pyc",
)
_CODE_SUFFIX = re.compile(r"\.(sh|py|js|jsx|ts|tsx|java|kt|go|rs|mjs|cjs|rb|php|swift)$")
_TEST_PATH = re.compile(r"(^|/)(test|tests|spec|specs)/")
_TEST_FILE = re.compile(r"(test|spec)\.(sh|py|js|ts|tsx|jsx)$")
_ENTRY_DOC = re.compile(r"(^|/)(agents|claude|readme|context)([-_.a-z0-9]*)?\.md$")
_BUILD_CONFIG = re.compile(
    r"(^|/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|pom\.xml|"
    r"build\.gradle|pyproject\.toml|requirements.*\.txt|go\.mod|cargo\.toml)$"
)
_TASK_SUFFIXES = (
    ".sh", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".go", ".rs", ".mjs", ".cjs", ".rb", ".php", ".swift", ".md",
    ".txt", ".toml", ".yaml", ".yml", ".json", ".xml", ".properties",
    ".gradle", ".cfg", ".ini",
)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_CJK_STOP_PHRASES = (
    "实现",
    "修复",
    "新增",
    "增加",
    "支持",
    "优化",
    "重构",
    "减少",
    "无关",
    "代码",
    "项目",
    "任务",
    "功能",
    "问题",
    "当前",
    "进行",
    "以及",
)
_SYMBOL_PATTERNS = (
    re.compile(r"^\s*(?:async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?"
        r"(?:function|class|interface|type|enum|record|struct|trait)\s+"
        r"([A-Za-z_$][A-Za-z0-9_$]*)"
    ),
    re.compile(
        r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?"
        r"(?:fn|struct|enum|trait|impl)\s+([A-Za-z_][A-Za-z0-9_]*)"
    ),
    re.compile(
        r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"
    ),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{"),
)
_TASK_BLOB_MAX_BYTES = 262_144
_TASK_SEARCH_FILE_LIMIT = 512
_TASK_PATHSPECS = tuple(f"*{suffix}" for suffix in _TASK_SUFFIXES)


class ChangeEvidenceError(RuntimeError):
    pass


@dataclass(frozen=True)
class ChangeEvidencePolicy:
    sort_mode: str
    excerpt_limit: int
    excerpt_chars: int
    changed_files_chars: int
    status_chars: int


@dataclass(frozen=True)
class ChangeEvidenceRequest:
    project: Path
    project_name: str
    goal: str
    is_git_repo: bool
    target_ref: str
    base_ref: str
    target_ref_explicit: bool
    policy: ChangeEvidencePolicy


@dataclass(frozen=True)
class RankedPath:
    path: str
    score: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class FileExcerpt:
    path: str
    format_name: str
    text: str


@dataclass(frozen=True)
class TaskCodeRecord:
    path: str
    line: int
    symbol: str
    score: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class ChangeEvidence:
    scope: Optional[GitScope]
    branch: str
    head_sha: str
    merge_base: str
    base_ref: str
    status: str
    changed_paths: Tuple[str, ...]
    ranked_paths: Tuple[RankedPath, ...]
    excerpts: Tuple[FileExcerpt, ...]
    task_code_records: Tuple[TaskCodeRecord, ...]
    task_code_status: str


def collect_change_evidence(request: ChangeEvidenceRequest) -> ChangeEvidence:
    if request.policy.sort_mode not in {"smart", "path"}:
        raise ChangeEvidenceError(
            f"Unknown changed-file sort mode: {request.policy.sort_mode}"
        )

    if not request.is_git_repo:
        if request.target_ref_explicit:
            raise ChangeEvidenceError(
                f"Target ref requires a git repository: {request.target_ref}"
            )
        return ChangeEvidence(
            scope=None,
            branch="no-git",
            head_sha="n/a",
            merge_base="n/a",
            base_ref=request.base_ref,
            status="No git repository detected; git state is unavailable.\n",
            changed_paths=(),
            ranked_paths=(),
            excerpts=(),
            task_code_records=(),
            task_code_status="Unavailable without a Git repository.",
        )

    scope = resolve_git_scope(
        request.project,
        target_ref=request.target_ref,
        base_ref=request.base_ref,
        base_policy="project",
    )
    with tempfile.TemporaryDirectory(prefix="agent-rails-git-evidence-") as temp_dir:
        snapshot = write_git_scope_snapshot(
            request.project,
            scope,
            Path(temp_dir),
            include_worktree=not request.target_ref_explicit,
        )

    if request.target_ref_explicit:
        branch = request.target_ref
        status_text = "Target ref mode: current working tree changes are not included.\n"
    else:
        branch = _current_branch(request.project)
        status_text = snapshot.status

    goal_tokens = select_goal_tokens(request.goal, request.project_name)
    ranked_paths = rank_changed_paths(
        request.project,
        snapshot,
        scope,
        goal_tokens,
        include_worktree=not request.target_ref_explicit,
        sort_mode=request.policy.sort_mode,
    )
    excerpts = collect_file_excerpts(
        request.project,
        snapshot,
        scope,
        ranked_paths,
        include_worktree=not request.target_ref_explicit,
        limit=request.policy.excerpt_limit,
        chars_per_file=request.policy.excerpt_chars,
    )
    if snapshot.changed_paths:
        task_code_records = ()
        task_code_status = (
            "Skipped because changed-path evidence is available for this task."
        )
    else:
        task_code_records = collect_task_code_records(
            request.project,
            scope,
            goal_tokens,
            limit=request.policy.excerpt_limit,
        )
        task_code_status = (
            ""
            if task_code_records
            else "No task-relevant tracked code evidence matched the goal."
        )
    return ChangeEvidence(
        scope=scope,
        branch=branch,
        head_sha=scope.target_short_sha,
        merge_base=scope.merge_base,
        base_ref=scope.base_ref,
        status=status_text,
        changed_paths=snapshot.changed_paths,
        ranked_paths=ranked_paths,
        excerpts=excerpts,
        task_code_records=task_code_records,
        task_code_status=task_code_status,
    )


def select_goal_tokens(goal: str, project_name: str) -> Tuple[str, ...]:
    ignored = set(_STOP_WORDS)
    for token in re.split(r"[^0-9A-Za-z]+", project_name.casefold()):
        if len(token) >= 3:
            ignored.add(token)

    selected = []
    seen = set()
    for token in re.split(r"[^0-9A-Za-z_.-]+", goal.casefold()):
        if len(token) < 3 or token in ignored or token in seen:
            continue
        selected.append(token)
        seen.add(token)
        if len(selected) == 6:
            break

    cjk_goal = goal
    for phrase in _CJK_STOP_PHRASES:
        cjk_goal = cjk_goal.replace(phrase, " ")
    for run in _CJK_RUN.findall(cjk_goal):
        candidates = [run[index : index + 2] for index in range(len(run) - 1)]
        if len(run) >= 2:
            candidates.append(run)
        for token in candidates:
            if len(token) < 2 or token in seen:
                continue
            selected.append(token)
            seen.add(token)
            if len(selected) == 6:
                return tuple(selected)
    return tuple(selected)


def collect_task_code_records(
    project: Path,
    scope: GitScope,
    goal_tokens: Sequence[str],
    *,
    limit: int,
) -> Tuple[TaskCodeRecord, ...]:
    if limit <= 0 or not goal_tokens:
        return ()

    paths = _tracked_task_paths(project, scope)
    content_paths = _task_content_paths(project, scope, goal_tokens)
    ranked = []
    for path in paths:
        record = _score_task_path(
            path,
            goal_tokens,
            path in content_paths,
        )
        if record is not None:
            ranked.append(record)
    ranked.sort(key=lambda item: (item.score, item.path), reverse=True)

    records = []
    for ranked_path in ranked:
        line, symbol = _task_location(
            project,
            scope,
            ranked_path.path,
            goal_tokens,
        )
        records.append(
            TaskCodeRecord(
                path=ranked_path.path,
                line=line,
                symbol=symbol,
                score=ranked_path.score,
                reasons=ranked_path.reasons,
            )
        )
        if len(records) >= limit:
            break
    return tuple(records)


def rank_changed_paths(
    project: Path,
    snapshot: GitScopeSnapshot,
    scope: GitScope,
    goal_tokens: Sequence[str],
    *,
    include_worktree: bool,
    sort_mode: str,
) -> Tuple[RankedPath, ...]:
    if sort_mode == "path":
        return tuple(RankedPath(path, 10, ("path",)) for path in snapshot.changed_paths)

    content_matches = _content_matches_by_path(
        project,
        snapshot,
        scope,
        goal_tokens,
        include_worktree=include_worktree,
    )
    records = [
        _score_path(path, goal_tokens, content_matches.get(path, ()))
        for path in snapshot.changed_paths
    ]
    return tuple(
        sorted(records, key=lambda item: (item.score, item.path), reverse=True)
    )


def collect_file_excerpts(
    project: Path,
    snapshot: GitScopeSnapshot,
    scope: GitScope,
    ranked_paths: Sequence[RankedPath],
    *,
    include_worktree: bool,
    limit: int,
    chars_per_file: int,
) -> Tuple[FileExcerpt, ...]:
    if limit <= 0 or chars_per_file <= 0:
        return ()

    untracked = set(snapshot.untracked_paths)
    excerpts = []
    for ranked in ranked_paths:
        path = ranked.path
        diff = _changed_diff_text(
            project, scope, path, include_worktree=include_worktree
        )
        if diff:
            format_name = "diff"
            raw = diff
        elif path in untracked:
            raw = _read_untracked_text(
                project,
                path,
                max_bytes=max(65_536, min(4_194_304, chars_per_file * 8)),
            )
            if raw is None:
                continue
            format_name = "text"
        else:
            continue

        try:
            safe = redact_sensitive_output(raw, format_name=format_name)
        except (SensitiveOutputError, UnicodeError, OSError):
            safe = "[excerpt omitted: sensitive-output guard failed]\n"
        excerpts.append(
            FileExcerpt(
                path=path,
                format_name=format_name,
                text=truncate_complete_lines(safe, chars_per_file),
            )
        )
        if len(excerpts) >= limit:
            break
    return tuple(excerpts)


def render_change_sections(
    evidence: ChangeEvidence,
    request: ChangeEvidenceRequest,
) -> str:
    changed_lines = (
        "".join(f"- {markdown_code(record.path)}\n" for record in evidence.ranked_paths)
        if evidence.ranked_paths
        else "- None detected.\n"
    )
    priority_lines = (
        "".join(
            f"- {markdown_code(record.path)} score={record.score} "
            f"({', '.join(record.reasons)})\n"
            for record in evidence.ranked_paths
        )
        if evidence.ranked_paths
        else "- None detected.\n"
    )
    excerpt_text = "".join(_render_excerpt(excerpt) for excerpt in evidence.excerpts)
    if not excerpt_text:
        excerpt_text = "- No changed text file excerpts selected.\n"
    task_code_text = (
        "".join(_render_task_code_record(record) for record in evidence.task_code_records)
        if evidence.task_code_records
        else f"- {valid_utf8(evidence.task_code_status)}\n"
    )

    parts = [
        "## Current Git State\n\n",
        f"- Project: {markdown_code(request.project_name)}\n",
        f"- Branch: {markdown_code(evidence.branch or 'detached')}\n",
        f"- Target ref: {markdown_code(request.target_ref)}\n",
        f"- HEAD: {markdown_code(evidence.head_sha)}\n",
        f"- Base ref: {markdown_code(evidence.base_ref or 'none')}\n",
        f"- Merge base: {markdown_code(evidence.merge_base[:12])}\n\n",
        "## Changed Files\n\n",
        truncate_complete_lines(
            changed_lines, request.policy.changed_files_chars
        ),
        "\n## Changed File Priority\n\n",
        truncate_complete_lines(
            priority_lines, request.policy.changed_files_chars
        ),
        "\n## Changed File Excerpts\n\n",
        excerpt_text,
        "\n## Task Code Evidence\n\n",
        truncate_complete_lines(
            task_code_text, request.policy.changed_files_chars
        ),
        "\n## Working Tree Status\n\n",
    ]
    if evidence.status:
        status_text = truncate_complete_lines(
            valid_utf8(evidence.status), request.policy.status_chars
        )
        fence = markdown_fence(status_text, "`", 3)
        parts.extend([f"{fence}text\n", status_text, f"{fence}\n"])
    else:
        parts.append("Clean.\n")
    parts.append("\n")
    return "".join(parts)


def write_change_evidence_bundle(
    output_dir: Path,
    evidence: ChangeEvidence,
    request: ChangeEvidenceRequest,
) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "changed-paths0").write_bytes(
            b"".join(
                path.encode("utf-8", errors="surrogateescape") + b"\0"
                for path in evidence.changed_paths
            )
        )
        (output_dir / "base-ref").write_text(
            evidence.base_ref, encoding="utf-8", errors="surrogateescape"
        )
        (output_dir / "target-sha").write_text(
            evidence.scope.target_sha if evidence.scope is not None else "",
            encoding="ascii",
        )
        (output_dir / "sections.md").write_text(
            render_change_sections(evidence, request),
            encoding="utf-8",
            errors="strict",
        )
    except OSError as exc:
        raise ChangeEvidenceError(
            f"Unable to write Task Pack Git evidence: {output_dir}"
        ) from exc


def truncate_complete_lines(text: str, budget: int) -> str:
    if budget <= 0:
        return text
    rendered = []
    used = 0
    truncated = False
    for line in text.splitlines(keepends=True):
        if used + len(line) > budget:
            truncated = True
            break
        rendered.append(line)
        used += len(line)
    if truncated:
        rendered.append("\n...[truncated by Agent Rails budget]...\n")
    return "".join(rendered)


def _current_branch(project: Path) -> str:
    try:
        result = run_git(project, ("branch", "--show-current"))
    except OSError as exc:
        raise ChangeEvidenceError("Git command is unavailable.") from exc
    return result.stdout.strip() if result.returncode == 0 else ""


def _changed_diff_text(
    project: Path,
    scope: GitScope,
    path: str,
    *,
    include_worktree: bool,
) -> str:
    commands = [
        (
            "diff",
            "--no-ext-diff",
            "--no-color",
            "--no-prefix",
            "--unified=2",
            scope.merge_base,
            scope.target_sha,
            "--",
            path,
        )
    ]
    if include_worktree:
        commands.extend(
            [
                (
                    "diff",
                    "--cached",
                    "--no-ext-diff",
                    "--no-color",
                    "--no-prefix",
                    "--unified=2",
                    "--",
                    path,
                ),
                (
                    "diff",
                    "--no-ext-diff",
                    "--no-color",
                    "--no-prefix",
                    "--unified=2",
                    "--",
                    path,
                ),
            ]
        )

    output = []
    for command in commands:
        try:
            result = run_git(project, command)
        except OSError as exc:
            raise ChangeEvidenceError("Git command is unavailable.") from exc
        if result.returncode != 0:
            raise ChangeEvidenceError(f"Unable to read Git diff for path: {path}")
        output.append(result.stdout)
    return "".join(output)


def _content_matches_by_path(
    project: Path,
    snapshot: GitScopeSnapshot,
    scope: GitScope,
    goal_tokens: Sequence[str],
    *,
    include_worktree: bool,
) -> dict[str, Tuple[str, ...]]:
    matches: dict[str, list[str]] = {path: [] for path in snapshot.changed_paths}
    untracked_text = {
        path: _read_untracked_text(project, path, max_bytes=1_048_576)
        for path in snapshot.untracked_paths
    }
    for token in goal_tokens:
        matched_paths = set()
        regex = _case_insensitive_git_regex(token)
        commands = [
            (
                "diff",
                "--name-only",
                "-z",
                f"-G{regex}",
                scope.merge_base,
                scope.target_sha,
                "--",
            )
        ]
        if include_worktree:
            commands.extend(
                [
                    ("diff", "--cached", "--name-only", "-z", f"-G{regex}", "--"),
                    ("diff", "--name-only", "-z", f"-G{regex}", "--"),
                ]
            )
        for command in commands:
            try:
                result = run_git(project, command)
            except OSError as exc:
                raise ChangeEvidenceError("Git command is unavailable.") from exc
            if result.returncode != 0:
                raise ChangeEvidenceError(
                    f"Unable to rank changed files for goal token: {token}"
                )
            matched_paths.update(field for field in result.stdout.split("\0") if field)
        for path, text in untracked_text.items():
            if text is not None and token in text.casefold():
                matched_paths.add(path)
        for path in snapshot.changed_paths:
            if path in matched_paths and len(matches[path]) < 2:
                matches[path].append(token)
    return {path: tuple(tokens) for path, tokens in matches.items() if tokens}


def _tracked_task_paths(project: Path, scope: GitScope) -> Tuple[str, ...]:
    try:
        result = run_git(
            project,
            ("ls-tree", "-r", "-z", "--name-only", scope.target_sha, "--"),
        )
    except OSError as exc:
        raise ChangeEvidenceError("Git command is unavailable.") from exc
    if result.returncode != 0:
        raise ChangeEvidenceError("Unable to list tracked task code paths.")
    return tuple(
        path
        for path in result.stdout.split("\0")
        if path and _is_task_code_path(path)
    )


def _task_content_paths(
    project: Path,
    scope: GitScope,
    goal_tokens: Sequence[str],
) -> set[str]:
    arguments = ["grep", "-l", "-z", "-I", "-i", "-F"]
    for token in goal_tokens:
        arguments.extend(("-e", token))
    arguments.extend((scope.target_sha, "--", *_TASK_PATHSPECS))
    prefix = f"{scope.target_sha}:"
    try:
        result = run_git(project, tuple(arguments))
    except OSError as exc:
        raise ChangeEvidenceError("Git command is unavailable.") from exc
    if result.returncode == 1:
        return set()
    if result.returncode != 0:
        raise ChangeEvidenceError("Unable to search tracked task code.")

    matches = set()
    for value in result.stdout.split("\0"):
        if not value:
            continue
        path = value[len(prefix) :] if value.startswith(prefix) else value
        if _is_task_code_path(path):
            matches.add(path)
        if len(matches) >= _TASK_SEARCH_FILE_LIMIT:
            break
    return matches


def _score_task_path(
    path: str,
    goal_tokens: Sequence[str],
    content_match: bool,
) -> Optional[RankedPath]:
    lowered = path.casefold()
    score = 0
    reasons = []
    for token in goal_tokens:
        if token.casefold() in lowered:
            score += 80
            reasons.append(f"path:{token}")
    if content_match:
        score += 45
        reasons.append("content")
    if not reasons:
        return None
    if lowered.startswith(("src/", "lib/", "app/", "backend/", "frontend/", "runtime/")):
        score += 35
        reasons.append("source")
    if _CODE_SUFFIX.search(lowered):
        score += 40
        reasons.append("code")
    if _TEST_PATH.search(lowered) or _TEST_FILE.search(lowered):
        score += 25
        reasons.append("tests")
    if _ENTRY_DOC.search(lowered):
        score += 15
        reasons.append("entry-doc")
    if _BUILD_CONFIG.search(lowered):
        score += 15
        reasons.append("build-config")
    return RankedPath(path, score, tuple(reasons))


def _task_location(
    project: Path,
    scope: GitScope,
    path: str,
    goal_tokens: Sequence[str],
) -> tuple[int, str]:
    text = _read_git_blob_text(project, scope, path)
    if text is None:
        return 0, ""
    lines = text.splitlines()
    symbols = []
    matched_lines = []
    folded_tokens = tuple(token.casefold() for token in goal_tokens)
    for line_number, line in enumerate(lines, start=1):
        folded = line.casefold()
        if any(token in folded for token in folded_tokens):
            matched_lines.append(line_number)
        for pattern in _SYMBOL_PATTERNS:
            matched = pattern.match(line)
            if matched:
                symbols.append((line_number, matched.group(1)))
                break

    best_symbol = max(
        (
            (
                sum(token in symbol.casefold() for token in folded_tokens),
                -line_number,
                line_number,
                symbol,
            )
            for line_number, symbol in symbols
        ),
        default=(0, 0, 0, ""),
    )
    if best_symbol[0] > 0:
        return best_symbol[2], best_symbol[3]
    if matched_lines:
        match_line = matched_lines[0]
        preceding = [
            (line_number, symbol)
            for line_number, symbol in symbols
            if line_number <= match_line
        ]
        if preceding:
            return preceding[-1]
        return match_line, ""
    if symbols:
        return symbols[0]
    return 0, ""


def _read_git_blob_text(
    project: Path,
    scope: GitScope,
    path: str,
) -> Optional[str]:
    object_name = f"{scope.target_sha}:{path}"
    try:
        size_result = run_git(project, ("cat-file", "-s", object_name))
    except OSError as exc:
        raise ChangeEvidenceError("Git command is unavailable.") from exc
    if size_result.returncode != 0:
        return None
    try:
        size = int(size_result.stdout.strip())
    except ValueError:
        return None
    if size > _TASK_BLOB_MAX_BYTES:
        return None
    try:
        result = run_git(project, ("show", object_name))
    except OSError as exc:
        raise ChangeEvidenceError("Git command is unavailable.") from exc
    if result.returncode != 0 or "\0" in result.stdout[:8192]:
        return None
    return result.stdout


def _is_task_code_path(path: str) -> bool:
    lowered = path.casefold()
    if lowered.endswith(_BINARY_SUFFIXES):
        return False
    return bool(
        _CODE_SUFFIX.search(lowered)
        or _TEST_PATH.search(lowered)
        or _TEST_FILE.search(lowered)
        or _ENTRY_DOC.search(lowered)
        or _BUILD_CONFIG.search(lowered)
        or lowered.endswith(_TASK_SUFFIXES)
    )


def _case_insensitive_git_regex(token: str) -> str:
    parts = []
    for character in token:
        lower = character.lower()
        upper = character.upper()
        if lower != upper:
            parts.append(f"[{re.escape(lower)}{re.escape(upper)}]")
        elif character == ".":
            parts.append(r"\.")
        else:
            parts.append(re.escape(character))
    return "".join(parts)


def _score_path(
    path: str, goal_tokens: Sequence[str], content_matches: Sequence[str]
) -> RankedPath:
    lowered = path.casefold()
    score = 0
    reasons = []
    for token in goal_tokens:
        if token in lowered:
            score += 80
            reasons.append(f"goal:{token}")
    for token in content_matches:
        score += 45
        reasons.append(f"change:{token}")
    if _ENTRY_DOC.search(lowered):
        score += 70
        reasons.append("entry-doc")
    if lowered.startswith(("bin/", "scripts/", "profiles/", "skills/", "templates/")):
        score += 55
        reasons.append("agent-rails-control")
    if _TEST_PATH.search(lowered) or _TEST_FILE.search(lowered):
        score += 45
        reasons.append("tests")
    if _CODE_SUFFIX.search(lowered):
        score += 40
        reasons.append("code")
    if _BUILD_CONFIG.search(lowered):
        score += 35
        reasons.append("build-config")
    if score == 0:
        return RankedPath(path, 10, ("path",))
    return RankedPath(path, score, tuple(reasons))


def _read_untracked_text(
    project: Path, path: str, *, max_bytes: int
) -> Optional[str]:
    if path.casefold().endswith(_BINARY_SUFFIXES):
        return None
    full_path = project / path
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(full_path), flags)
    except OSError:
        return None
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None
        data = os.read(descriptor, max_bytes + 1)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    if b"\0" in data[:8192]:
        return None
    return data[:max_bytes].decode("utf-8", errors="replace")


def _render_excerpt(excerpt: FileExcerpt) -> str:
    safe_path = markdown_code(excerpt.path)
    text = valid_utf8(excerpt.text)
    fence = markdown_fence(text, "~", 3)
    return (
        f"### {safe_path}\n\n"
        f"{fence}{excerpt.format_name}\n"
        f"{text}"
        f"{fence}\n\n"
    )


def _render_task_code_record(record: TaskCodeRecord) -> str:
    location = record.path if record.line <= 0 else f"{record.path}:{record.line}"
    symbol = (
        f" symbol={markdown_code(record.symbol)}"
        if record.symbol
        else ""
    )
    reasons = ", ".join(record.reasons)
    return (
        f"- {markdown_code(location)} score={record.score}{symbol} "
        f"({valid_utf8(reasons)})\n"
    )
