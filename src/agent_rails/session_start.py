"""Render the stable Agent Rails SessionStart guardrail for configured projects."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import shlex
import stat
import subprocess
from typing import Mapping, Optional
import unicodedata

from .adapters.content import (
    AdapterContentError,
    extract_adapter_profile,
    render_profile_argument,
)
from .context.markdown import display_text
from .core.paths import AgentRailsPaths


_MAX_MARKER_BYTES = 1024 * 1024
_REPO_LOCAL_GIT_VARIABLES = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_WORK_TREE",
}


@dataclass(frozen=True)
class SessionStartRequest:
    kit_home: Path
    invocation_cwd: Path
    environment: Mapping[str, str]
    host_input: str = ""


@dataclass(frozen=True)
class SessionStartResult:
    active: bool
    project_root: Optional[Path]
    profile_path: str
    stdout: str
    stderr: str
    exit_code: int


class SessionStartError(RuntimeError):
    """SessionStart context could not be rendered without weakening safety."""


def run_session_start(request: SessionStartRequest) -> SessionStartResult:
    """Return quiet success or one complete host-specific SessionStart payload."""

    _validate_request(request)
    kit_home = _canonical_directory(request.kit_home, required=True)
    invocation_cwd = _canonical_directory(request.invocation_cwd, required=False)
    if kit_home is None:
        raise SessionStartError("Agent Rails kit home is unavailable.")
    if invocation_cwd is None:
        return _inactive()

    environment = dict(request.environment)
    project_root = _resolve_project_root(
        request.host_input,
        environment,
        invocation_cwd,
    )
    if project_root is None or not _has_agent_rails_marker(project_root):
        return _inactive(project_root)

    explicit_profile = ""
    for relative in (
        "CLAUDE.local.md",
        "CLAUDE.md",
        ".claude/AGENT_RAILS.md",
        ".opencode/AGENT_RAILS.md",
    ):
        content = _read_marker_text(project_root, relative)
        if content is None:
            continue
        try:
            explicit_profile = extract_adapter_profile(content)
        except AdapterContentError as exc:
            raise SessionStartError(
                "Invalid Agent Rails adapter profile metadata."
            ) from exc
        if explicit_profile:
            break

    profile_path = ""
    if explicit_profile:
        paths = AgentRailsPaths.from_environment(kit_home, environment)
        profile_path = paths.resolve_profile(
            project_root,
            project_root.name,
            explicit_profile,
        )
    try:
        profile_argument = render_profile_argument(profile_path)
    except AdapterContentError as exc:
        raise SessionStartError("Invalid Agent Rails profile path.") from exc

    context = _render_context(
        project_root,
        kit_home / "bin/agent-rails",
        profile_argument,
    )
    if environment.get("PLUGIN_DATA", ""):
        payload = {
            "systemMessage": "AGENT RAILS:ON",
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": context,
            },
        }
        stdout = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    else:
        stdout = context + "\n"
    return SessionStartResult(
        active=True,
        project_root=project_root,
        profile_path=profile_path,
        stdout=stdout,
        stderr="",
        exit_code=0,
    )


def _validate_request(request: SessionStartRequest) -> None:
    if not isinstance(request, SessionStartRequest):
        raise SessionStartError("Invalid SessionStart request.")
    if not isinstance(request.kit_home, Path):
        raise SessionStartError("SessionStart kit home must be a Path.")
    if not isinstance(request.invocation_cwd, Path):
        raise SessionStartError("SessionStart cwd must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise SessionStartError("SessionStart environment must be a mapping.")
    if not isinstance(request.host_input, str):
        raise SessionStartError("SessionStart host input must be text.")
    for key, value in request.environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise SessionStartError(
                "SessionStart environment keys and values must be text."
            )


def _inactive(project_root: Optional[Path] = None) -> SessionStartResult:
    return SessionStartResult(
        active=False,
        project_root=project_root,
        profile_path="",
        stdout="",
        stderr="",
        exit_code=0,
    )


def _canonical_directory(path: Path, *, required: bool) -> Optional[Path]:
    try:
        canonical = Path(os.path.realpath(path))
    except (OSError, TypeError, ValueError) as exc:
        if required:
            raise SessionStartError("SessionStart directory is invalid.") from exc
        return None
    if not canonical.is_dir():
        if required:
            raise SessionStartError("SessionStart directory is unavailable.")
        return None
    return canonical


def _resolve_project_root(
    host_input: str,
    environment: Mapping[str, str],
    invocation_cwd: Path,
) -> Optional[Path]:
    host_cwd = _host_cwd(host_input)
    if host_cwd:
        candidate = _canonical_directory(Path(host_cwd), required=False)
        if candidate is not None:
            return candidate

    project_hint = environment.get("CLAUDE_PROJECT_DIR", "")
    if project_hint:
        candidate = _canonical_directory(Path(project_hint), required=False)
        if candidate is not None:
            return candidate

    git_root = _git_root(invocation_cwd, environment)
    return git_root if git_root is not None else invocation_cwd


def _host_cwd(host_input: str) -> str:
    if not host_input:
        return ""
    try:
        payload = json.loads(host_input)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    if not isinstance(payload, dict):
        return ""
    cwd = payload.get("cwd")
    return cwd if isinstance(cwd, str) else ""


def _git_root(
    invocation_cwd: Path,
    environment: Mapping[str, str],
) -> Optional[Path]:
    isolated_environment = {
        key: value
        for key, value in environment.items()
        if key not in _REPO_LOCAL_GIT_VARIABLES
    }
    try:
        completed = subprocess.run(
            ("git", "rev-parse", "--show-toplevel"),
            cwd=str(invocation_cwd),
            env=isolated_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except (FileNotFoundError, PermissionError, OSError):
        return None
    if completed.returncode != 0:
        return None
    try:
        value = completed.stdout.decode("utf-8", errors="strict").rstrip("\r\n")
    except UnicodeDecodeError:
        return None
    if not value:
        return None
    return _canonical_directory(Path(value), required=False)


def _has_agent_rails_marker(project_root: Path) -> bool:
    for relative in ("CLAUDE.local.md", "CLAUDE.md"):
        content = _read_marker_text(project_root, relative)
        if content is not None and "agent-rails:start" in content:
            return True
    for relative in (
        ".claude/AGENT_RAILS.md",
        ".opencode/AGENT_RAILS.md",
    ):
        content = _read_marker_text(project_root, relative)
        if content is not None and "Visible session marker protocol" in content:
            return True
    plugin = _read_marker_text(project_root, ".codex-plugin/plugin.json")
    return plugin is not None and '"name": "agent-rails"' in plugin


def _read_marker_text(project_root: Path, relative_path: str) -> Optional[str]:
    relative = PurePosixPath(relative_path)
    if (
        relative.is_absolute()
        or not relative.parts
        or any(part in {"", ".", ".."} for part in relative.parts)
    ):
        return None
    directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    file_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        current = os.open(project_root, directory_flags)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    try:
        for part in relative.parts[:-1]:
            next_descriptor = os.open(part, directory_flags, dir_fd=current)
            os.close(current)
            current = next_descriptor
        descriptor = os.open(relative.parts[-1], file_flags, dir_fd=current)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    finally:
        os.close(current)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_MARKER_BYTES:
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(descriptor, min(64 * 1024, _MAX_MARKER_BYTES - total + 1))
            if not block:
                break
            total += len(block)
            if total > _MAX_MARKER_BYTES:
                return None
            chunks.append(block)
    finally:
        os.close(descriptor)
    try:
        return b"".join(chunks).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None


def _render_context(
    project_root: Path,
    cli: Path,
    profile_argument: str,
) -> str:
    return f'''AGENT RAILS SESSION HOOK ACTIVE

Before broad reads/edits, choose the smallest path and show its marker.

Markers:
- Pack/lite: relay the printed AGENT RAILS: ON marker.
- Check-only: AGENT RAILS: CHECK-ONLY (reason=<reason>).
- Skip: AGENT RAILS: SKIPPED (reason=<reason>).

Trigger matrix:
- Deep: cross-subproject, contract/schema/model, ADR, migration/refactor, ambiguous product work.
- Lite: POC, deploy prep, codegen check, focused continuation.
- Check-only: branch-consuming deploy/release/upload or final verification.
- Skip: read-only/fixed work with no branch risk.

Target scope:
- Session root: {display_text(str(project_root))}
- Worktree: pass its exact root to pack/check.
- Other repo: do not reuse this --profile; resolve its profile.
- Target changed: regenerate pack; verify Current Git State.

Sensitive output:
- Base64 and URL encoding are not redaction.
- Read only decision fields; avoid auth-bearing context.
- Do not repeat secrets; narrow reads and report exposure.

Commands:
{_render_cli_assignment(cli)}
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
"$ar" pack --project "$project_root"{profile_argument} "<goal>"
"$ar" pack --project "$project_root"{profile_argument} --pack-mode lite "<goal>"
"$ar" check --project "$project_root"{profile_argument} --print-only

Read the generated pack; the project adapter has exact details.'''


def _render_cli_assignment(cli: Path) -> str:
    value = str(cli)
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
        for character in value
    ):
        encoded = value.encode("utf-8", errors="strict")
        literal = "$'" + "".join(f"\\x{byte:02x}" for byte in encoded) + "'"
        return f"ar={literal}"
    if not any(character in value for character in ('\\', '"', '$', '`')):
        return f'ar="{value}"'
    return f"ar={shlex.quote(value)}"


__all__ = (
    "SessionStartError",
    "SessionStartRequest",
    "SessionStartResult",
    "run_session_start",
)
