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
from agent_rails.evidence.code import (
    BINARY_SUFFIXES as _BINARY_SUFFIXES,
    BUILD_CONFIG as _BUILD_CONFIG,
    CODE_SUFFIX as _CODE_SUFFIX,
    ENTRY_DOC as _ENTRY_DOC,
    TEST_FILE as _TEST_FILE,
    TEST_PATH as _TEST_PATH,
    CodeEvidenceError,
    CodeEvidenceRecord as TaskCodeRecord,
    CodeEvidenceRequest,
    collect_code_evidence,
    select_code_tokens,
)
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
        try:
            task_code_records = collect_code_evidence(
                CodeEvidenceRequest(
                    project=request.project,
                    target_sha=scope.target_sha,
                    query=request.goal,
                    ignored_text=request.project_name,
                    limit=request.policy.excerpt_limit,
                )
            )
        except CodeEvidenceError as exc:
            raise ChangeEvidenceError(str(exc)) from exc
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
    """Compatibility wrapper for the Task Pack token selector."""
    return select_code_tokens(goal, project_name)


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
        f"- {markdown_code(location)} role={record.role.value} "
        f"score={record.score}{symbol} "
        f"({valid_utf8(reasons)})\n"
    )
