"""Select bounded code locations from one immutable Git snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Optional, Tuple

from agent_rails.git._runner import run_git


STOP_WORDS = frozenset(
    "agent agents rails task pack project repo code change changes work continue "
    "continuing optimize optimization reduce reducing keep keeping with without "
    "from into this that".split()
)
BINARY_SUFFIXES = (
    ".bmp", ".gif", ".ico", ".jpeg", ".jpg", ".pdf", ".png", ".webp",
    ".avif", ".heic", ".mp3", ".mp4", ".mov", ".ttf", ".woff", ".woff2",
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".jar", ".war",
    ".class", ".pyc",
)
CODE_SUFFIX = re.compile(
    r"\.(sh|py|js|jsx|ts|tsx|java|kt|go|rs|mjs|cjs|rb|php|swift)$"
)
TEST_PATH = re.compile(r"(^|/)(test|tests|spec|specs)/")
TEST_FILE = re.compile(r"(test|spec)\.(sh|py|js|ts|tsx|jsx)$")
ENTRY_DOC = re.compile(r"(^|/)(agents|claude|readme|context)([-_.a-z0-9]*)?\.md$")
BUILD_CONFIG = re.compile(
    r"(^|/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|pom\.xml|"
    r"build\.gradle|pyproject\.toml|requirements.*\.txt|go\.mod|cargo\.toml)$"
)
_SEARCH_SUFFIXES = (
    ".sh", ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt",
    ".go", ".rs", ".mjs", ".cjs", ".rb", ".php", ".swift", ".md",
    ".txt", ".toml", ".yaml", ".yml", ".json", ".xml", ".properties",
    ".gradle", ".cfg", ".ini",
)
_CJK_RUN = re.compile(r"[\u3400-\u9fff]+")
_CJK_STOP_PHRASES = (
    "实现", "修复", "新增", "增加", "支持", "优化", "重构", "减少",
    "无关", "代码", "项目", "任务", "功能", "问题", "当前", "进行", "以及",
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
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(\)\s*\{"),
)
_BLOB_MAX_BYTES = 262_144
_SEARCH_FILE_LIMIT = 512
_PATHSPECS = tuple(f"*{suffix}" for suffix in _SEARCH_SUFFIXES)


class CodeEvidenceError(RuntimeError):
    pass


class CodeEvidenceRole(Enum):
    IMPLEMENTATION = "implementation"
    VERIFICATION = "verification"
    SUPPORT = "support"


@dataclass(frozen=True)
class CodeEvidenceRequest:
    project: Path
    target_sha: str
    query: str
    ignored_text: str = ""
    preferred_paths: Tuple[str, ...] = ()
    excluded_paths: Tuple[str, ...] = ()
    limit: int = 4


@dataclass(frozen=True)
class CodeEvidenceRecord:
    path: str
    line: int
    symbol: str
    role: CodeEvidenceRole
    score: int
    reasons: Tuple[str, ...]


@dataclass(frozen=True)
class _RankedCodePath:
    path: str
    role: CodeEvidenceRole
    score: int
    reasons: Tuple[str, ...]


def select_code_tokens(query: str, ignored_text: str = "") -> Tuple[str, ...]:
    ignored = set(STOP_WORDS)
    for pattern in (r"[^0-9A-Za-z_.-]+", r"[^0-9A-Za-z]+"):
        for token in re.split(pattern, ignored_text.casefold()):
            if len(token) >= 3:
                ignored.add(token)

    selected = []
    seen = set()
    for token in re.split(r"[^0-9A-Za-z_.-]+", query.casefold()):
        if len(token) < 3 or token in ignored or token in seen:
            continue
        selected.append(token)
        seen.add(token)
        if len(selected) == 6:
            break

    cjk_query = query
    for phrase in _CJK_STOP_PHRASES:
        cjk_query = cjk_query.replace(phrase, " ")
    for run in _CJK_RUN.findall(cjk_query):
        candidates = [run[index : index + 2] for index in range(len(run) - 1)]
        candidates.append(run)
        for token in candidates:
            if len(token) < 2 or token in seen:
                continue
            selected.append(token)
            seen.add(token)
            if len(selected) == 6:
                return tuple(selected)
    return tuple(selected)


def collect_code_evidence(
    request: CodeEvidenceRequest,
) -> Tuple[CodeEvidenceRecord, ...]:
    tokens = select_code_tokens(request.query, request.ignored_text)
    if request.limit <= 0 or not tokens:
        return ()

    content_paths = _content_paths(request, tokens)
    preferred = set(request.preferred_paths)
    excluded = set(request.excluded_paths)
    ranked = []
    for path in _tracked_paths(request):
        if path in excluded:
            continue
        record = _score_path(path, tokens, path in content_paths, path in preferred)
        if record is not None:
            ranked.append(record)
    ranked.sort(key=lambda item: (item.score, item.path), reverse=True)

    records = []
    for ranked_path in _select_paths(ranked, request.limit):
        line, symbol = _location(request, ranked_path.path, tokens)
        records.append(
            CodeEvidenceRecord(
                path=ranked_path.path,
                line=line,
                symbol=symbol,
                role=ranked_path.role,
                score=ranked_path.score,
                reasons=ranked_path.reasons,
            )
        )
    return tuple(records)


def _select_paths(
    ranked: list[_RankedCodePath], limit: int
) -> Tuple[_RankedCodePath, ...]:
    selected = []
    for role in (
        CodeEvidenceRole.IMPLEMENTATION,
        CodeEvidenceRole.VERIFICATION,
    ):
        match = next((item for item in ranked if item.role is role), None)
        if match is not None and len(selected) < limit:
            selected.append(match)
    selected_paths = {item.path for item in selected}
    selected.extend(
        item
        for item in ranked
        if item.path not in selected_paths
    )
    return tuple(selected[:limit])


def _tracked_paths(request: CodeEvidenceRequest) -> Tuple[str, ...]:
    result = _git(
        request,
        ("ls-tree", "-r", "-z", "--name-only", request.target_sha, "--"),
        "Unable to list tracked code paths.",
    )
    return tuple(
        path for path in result.stdout.split("\0") if path and _is_code_path(path)
    )


def _content_paths(
    request: CodeEvidenceRequest, tokens: Tuple[str, ...]
) -> set[str]:
    arguments = ["grep", "-l", "-z", "-I", "-i", "-F"]
    for token in tokens:
        arguments.extend(("-e", token))
    arguments.extend((request.target_sha, "--", *_PATHSPECS))
    try:
        result = run_git(request.project, tuple(arguments))
    except OSError as exc:
        raise CodeEvidenceError("Git command is unavailable.") from exc
    if result.returncode == 1:
        return set()
    if result.returncode != 0:
        raise CodeEvidenceError("Unable to search tracked code.")

    prefix = f"{request.target_sha}:"
    matches = set()
    for value in result.stdout.split("\0"):
        if not value:
            continue
        path = value[len(prefix) :] if value.startswith(prefix) else value
        if _is_code_path(path):
            matches.add(path)
        if len(matches) >= _SEARCH_FILE_LIMIT:
            break
    return matches


def _score_path(
    path: str,
    tokens: Tuple[str, ...],
    content_match: bool,
    preferred: bool,
) -> Optional[_RankedCodePath]:
    lowered = path.casefold()
    score = 0
    reasons = []
    for token in tokens:
        if token in lowered:
            score += 80
            reasons.append(f"path:{token}")
    if content_match:
        score += 45
        reasons.append("content")
    if not reasons:
        return None
    if preferred:
        score += 60
        reasons.append("changed")
    if lowered.startswith(("src/", "lib/", "app/", "backend/", "frontend/", "runtime/")):
        score += 35
        reasons.append("source")
    if CODE_SUFFIX.search(lowered):
        score += 40
        reasons.append("code")
    if TEST_PATH.search(lowered) or TEST_FILE.search(lowered):
        score += 25
        reasons.append("tests")
    if ENTRY_DOC.search(lowered):
        score += 15
        reasons.append("entry-doc")
    if BUILD_CONFIG.search(lowered):
        score += 15
        reasons.append("build-config")
    return _RankedCodePath(path, _path_role(lowered), score, tuple(reasons))


def _path_role(lowered_path: str) -> CodeEvidenceRole:
    if TEST_PATH.search(lowered_path) or TEST_FILE.search(lowered_path):
        return CodeEvidenceRole.VERIFICATION
    if CODE_SUFFIX.search(lowered_path):
        return CodeEvidenceRole.IMPLEMENTATION
    return CodeEvidenceRole.SUPPORT


def _location(
    request: CodeEvidenceRequest, path: str, tokens: Tuple[str, ...]
) -> tuple[int, str]:
    text = _read_blob(request, path)
    if text is None:
        return 0, ""
    symbols = []
    matched_lines = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        folded = line.casefold()
        if any(token in folded for token in tokens):
            matched_lines.append(line_number)
        for pattern in _SYMBOL_PATTERNS:
            matched = pattern.match(line)
            if matched:
                symbols.append((line_number, matched.group(1)))
                break

    best_symbol = max(
        (
            (
                sum(token in symbol.casefold() for token in tokens),
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
        preceding = [item for item in symbols if item[0] <= match_line]
        return preceding[-1] if preceding else (match_line, "")
    return symbols[0] if symbols else (0, "")


def _read_blob(request: CodeEvidenceRequest, path: str) -> Optional[str]:
    object_name = f"{request.target_sha}:{path}"
    size_result = _git(
        request,
        ("cat-file", "-s", object_name),
        "Unable to inspect tracked code blob.",
        allow_failure=True,
    )
    if size_result.returncode != 0:
        return None
    try:
        size = int(size_result.stdout.strip())
    except ValueError:
        return None
    if size > _BLOB_MAX_BYTES:
        return None
    result = _git(
        request,
        ("show", object_name),
        "Unable to read tracked code blob.",
        allow_failure=True,
    )
    if result.returncode != 0 or "\0" in result.stdout[:8192]:
        return None
    return result.stdout


def _is_code_path(path: str) -> bool:
    lowered = path.casefold()
    if lowered.endswith(BINARY_SUFFIXES):
        return False
    return bool(
        CODE_SUFFIX.search(lowered)
        or TEST_PATH.search(lowered)
        or TEST_FILE.search(lowered)
        or ENTRY_DOC.search(lowered)
        or BUILD_CONFIG.search(lowered)
        or lowered.endswith(_SEARCH_SUFFIXES)
    )


def _git(
    request: CodeEvidenceRequest,
    arguments: Tuple[str, ...],
    message: str,
    *,
    allow_failure: bool = False,
):
    try:
        result = run_git(request.project, arguments)
    except OSError as exc:
        raise CodeEvidenceError("Git command is unavailable.") from exc
    if result.returncode != 0 and not allow_failure:
        raise CodeEvidenceError(message)
    return result
