"""Codex plugin lifecycle as a typed Python application service."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import os
from pathlib import Path
import selectors
import shlex
import shutil
import signal
import stat
import subprocess
from typing import Mapping, Optional, Tuple, Union
import unicodedata

from agent_rails.adapters.claude import ClaudeInstallMode
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_project_root_identity,
    resolve_target_project,
)
from agent_rails.core.paths import AgentRailsPaths, canonical_path
from agent_rails.diagnostics.doctor import (
    DoctorError,
    DoctorEventStream,
    DoctorRequest,
    run_doctor,
)


_PLUGIN_SELECTOR = "agent-rails@agent-rails-local"
_RULES_MARKER = b"<!-- agent-rails:start -->"
_MAX_MARKER_BYTES = 4 * 1024 * 1024
_MAX_CHILD_OUTPUT_BYTES = 1_000_000
_CHILD_READ_BYTES = 64 * 1024


class CodexAdapterError(RuntimeError):
    """A Codex lifecycle request could not be completed."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Tuple["CodexEvent", ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.events = events

    @property
    def stdout(self) -> str:
        return _render_events(self.events, CodexEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, CodexEventStream.STDERR)


class CodexAdapterInputError(CodexAdapterError):
    """The caller supplied an invalid typed Codex lifecycle request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class CodexAction(str, Enum):
    INSTALL = "install"
    DOCTOR = "doctor"
    UNINSTALL = "uninstall"


class CodexInstallMode(str, Enum):
    LOCAL = "local"
    PROJECT = "project"


class CodexEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class CodexInstallRequest:
    requested_project: Optional[Path]
    kit_home: Path
    explicit_profile: Optional[str]
    mode: CodexInstallMode
    fix_project: bool
    dry_run: bool
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class CodexDoctorRequest:
    requested_project: Optional[Path]
    kit_home: Path
    explicit_profile: Optional[str]
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class CodexUninstallRequest:
    kit_home: Path
    dry_run: bool
    working_directory: Path
    environment: Mapping[str, str]


CodexAdapterRequest = Union[
    CodexInstallRequest,
    CodexDoctorRequest,
    CodexUninstallRequest,
]


@dataclass(frozen=True)
class CodexEvent:
    stream: CodexEventStream
    text: str


@dataclass(frozen=True)
class CodexAdapterResult:
    action: CodexAction
    project_root: Optional[Path]
    profile_path: Optional[str]
    mode: CodexInstallMode
    exit_code: int
    events: Tuple[CodexEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_events(self.events, CodexEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, CodexEventStream.STDERR)


@dataclass(frozen=True)
class _CodexCommandResult:
    returncode: int
    stdout: str
    stderr: str


def run_codex_adapter(
    request: CodexAdapterRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> CodexAdapterResult:
    """Resolve optional project context once and apply one Codex lifecycle."""

    action, mode, dry_run = _validate_request(request)
    environment = dict(request.environment)
    kit_home = Path(os.path.realpath(os.fspath(request.kit_home)))
    working_directory = _canonical_directory(request.working_directory)
    if context is None:
        context = _resolve_context(request, kit_home, environment)
    else:
        _validate_pre_resolved_context(
            request=request,
            context=context,
            kit_home=kit_home,
            environment=environment,
        )
    events: list[CodexEvent] = []

    if action is CodexAction.INSTALL:
        assert isinstance(request, CodexInstallRequest)
        exit_code = _install(
            request=request,
            context=context,
            kit_home=kit_home,
            working_directory=working_directory,
            environment=environment,
            events=events,
        )
    elif action is CodexAction.DOCTOR:
        executable = _find_codex(environment)
        _stdout(events, "Agent Rails Codex Doctor")
        _stdout(events, f"Version: {_resolve_version(kit_home, environment)}")
        if executable is None:
            _stdout(events, "[WARN] Codex CLI not found.")
        else:
            _stdout(events, f"[OK] Codex CLI: {executable}")
            _stdout(events, f"Marketplace: {kit_home / 'codex-marketplace'}")
            _stdout(events, f"Plugin: {_PLUGIN_SELECTOR}")
            for arguments in (
                ("plugin", "marketplace", "list"),
                ("plugin", "list"),
            ):
                completed = _execute_codex(
                    executable,
                    arguments,
                    working_directory=working_directory,
                    environment=environment,
                    prefix_events=events,
                )
                _stdout_process(events, completed.stdout)
        _project_status(events, context)
        exit_code = 0
    else:
        assert isinstance(request, CodexUninstallRequest)
        _stdout(events, "Agent Rails Codex Uninstall")
        command = ("plugin", "remove", _PLUGIN_SELECTOR)
        if dry_run:
            _stdout(events, f"Would run: {shlex.join(('codex', *command))}")
        else:
            executable = _require_codex(environment)
            completed = _execute_codex(
                executable,
                command,
                working_directory=working_directory,
                environment=environment,
                prefix_events=events,
            )
            _record_command_result(events, completed)
        exit_code = 0

    return CodexAdapterResult(
        action=action,
        project_root=None if context is None else context.root,
        profile_path=None if context is None else context.profile_path,
        mode=mode,
        exit_code=exit_code,
        events=tuple(events),
    )


def _validate_request(
    request: CodexAdapterRequest,
) -> tuple[CodexAction, CodexInstallMode, bool]:
    if isinstance(request, CodexInstallRequest):
        action = CodexAction.INSTALL
        mode = request.mode
        dry_run = request.dry_run
        if not isinstance(mode, CodexInstallMode):
            raise CodexAdapterInputError("Invalid Codex adapter install mode.")
        for name in ("fix_project", "dry_run"):
            if not isinstance(getattr(request, name), bool):
                raise CodexAdapterInputError(
                    f"Codex adapter {name} policy must be boolean."
                )
        if request.fix_project and request.requested_project is None:
            raise CodexAdapterInputError("--fix-project requires --project.")
    elif isinstance(request, CodexDoctorRequest):
        action = CodexAction.DOCTOR
        mode = CodexInstallMode.LOCAL
        dry_run = False
    elif isinstance(request, CodexUninstallRequest):
        action = CodexAction.UNINSTALL
        mode = CodexInstallMode.LOCAL
        dry_run = request.dry_run
        if not isinstance(dry_run, bool):
            raise CodexAdapterInputError(
                "Codex adapter dry_run policy must be boolean."
            )
    else:
        raise CodexAdapterInputError("Invalid Codex adapter request.")

    requested_project = getattr(request, "requested_project", None)
    if requested_project is not None and not isinstance(requested_project, Path):
        raise CodexAdapterInputError(
            "Codex adapter requested project must be a Path."
        )
    if not isinstance(request.kit_home, Path):
        raise CodexAdapterInputError("Codex adapter kit home must be a Path.")
    explicit_profile = getattr(request, "explicit_profile", None)
    if explicit_profile is not None and not isinstance(explicit_profile, str):
        raise CodexAdapterInputError(
            "Codex adapter explicit Profile must be text."
        )
    if not isinstance(request.working_directory, Path):
        raise CodexAdapterInputError(
            "Codex adapter working directory must be a Path."
        )
    if not isinstance(request.environment, Mapping):
        raise CodexAdapterInputError(
            "Codex adapter environment must be a mapping."
        )
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise CodexAdapterInputError(
            "Codex adapter environment keys and values must be text."
        )
    return action, mode, dry_run


def _resolve_context(
    request: CodexAdapterRequest,
    kit_home: Path,
    environment: Mapping[str, str],
) -> Optional[TargetProjectContext]:
    requested_project = getattr(request, "requested_project", None)
    if requested_project is None:
        return None
    try:
        return resolve_target_project(
            requested_project,
            kit_home=kit_home,
            explicit_profile=getattr(request, "explicit_profile", None),
            environment=environment,
            require_profile=False,
            load_profile=False,
        )
    except TargetProjectError as exc:
        raise CodexAdapterInputError(str(exc)) from exc


def _validate_pre_resolved_context(
    *,
    request: CodexAdapterRequest,
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
) -> None:
    """Reject a context that was resolved for a different invocation."""

    if not isinstance(context, TargetProjectContext):
        raise CodexAdapterInputError(
            "Codex adapter context must be a TargetProjectContext."
        )
    if not isinstance(context.root, Path) or not isinstance(
        context.profile_path, str
    ):
        raise CodexAdapterInputError(
            "Codex adapter context has invalid project or Profile fields."
        )
    requested_project = getattr(request, "requested_project", None)
    if requested_project is None:
        raise CodexAdapterInputError(
            "Codex adapter context requires a project-scoped request."
        )
    if not requested_project.is_dir():
        raise CodexAdapterInputError(
            f"Project directory not found: {requested_project}"
        )
    requested_root, _ = resolve_project_root_identity(
        requested_project, environment
    )
    if canonical_path(context.root) != requested_root:
        raise CodexAdapterInputError(
            "Codex adapter context does not match the requested project."
        )
    explicit_profile = getattr(request, "explicit_profile", None)
    expected_profile = AgentRailsPaths.from_environment(
        kit_home, environment
    ).resolve_profile(
        requested_root,
        requested_root.name,
        explicit_profile,
    )
    if _canonical_profile_path(context.profile_path) != _canonical_profile_path(
        expected_profile
    ):
        raise CodexAdapterInputError(
            "Codex adapter context does not match the requested Profile or kit."
        )
    _validate_context_kit(context, kit_home)


def _canonical_profile_path(value: str) -> Path:
    return canonical_path(Path(os.path.abspath(value)))


def _validate_context_kit(
    context: TargetProjectContext, kit_home: Path
) -> None:
    resolved_kit = context.profile_environment.get("AGENT_RAILS_HOME", "")
    if resolved_kit and canonical_path(Path(resolved_kit)) != kit_home:
        raise CodexAdapterInputError(
            "Codex adapter context does not match the requested Profile or kit."
        )


def _install(
    *,
    request: CodexInstallRequest,
    context: Optional[TargetProjectContext],
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    events: list[CodexEvent],
) -> int:
    _stdout(events, "Agent Rails Codex Install")
    _stdout(events, f"Version: {_resolve_version(kit_home, environment)}")
    _stdout(events, f"Marketplace: {kit_home / 'codex-marketplace'}")
    _stdout(events, f"Plugin: {_PLUGIN_SELECTOR}")
    _stdout(events, f"Mode: {request.mode.value}")

    executable = None if request.dry_run else _require_codex(environment)
    for command in (
        ("plugin", "marketplace", "add", str(kit_home / "codex-marketplace")),
        ("plugin", "add", _PLUGIN_SELECTOR),
    ):
        if request.dry_run:
            _stdout(events, f"Would run: {shlex.join(('codex', *command))}")
            continue
        assert executable is not None
        completed = _execute_codex(
            executable,
            command,
            working_directory=working_directory,
            environment=environment,
            prefix_events=events,
        )
        _record_command_result(events, completed)

    exit_code = 0
    if request.fix_project:
        assert context is not None
        if request.dry_run:
            command = [
                str(kit_home / "bin/agent-rails"),
                "doctor",
                "--project",
                str(context.root),
                "--fix",
                "--mode",
                request.mode.value,
            ]
            if context.profile_path:
                command.extend(("--profile", context.profile_path))
            command.append("--dry-run")
            _stdout(events, f"Would run: {shlex.join(command)}")
        else:
            exit_code = _fix_project(
                request=request,
                context=context,
                kit_home=kit_home,
                environment=environment,
                events=events,
            )
    else:
        _project_status(events, context)
    _stdout(events, "Open a new Codex thread for SessionStart context to take effect.")
    return exit_code


def _fix_project(
    *,
    request: CodexInstallRequest,
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
    events: list[CodexEvent],
) -> int:
    try:
        result = run_doctor(
            DoctorRequest(
                requested_project=context.root,
                kit_home=kit_home,
                explicit_profile=context.profile_path,
                online_memory_smoke=False,
                fix=True,
                fix_mode=(
                    ClaudeInstallMode.LOCAL
                    if request.mode is CodexInstallMode.LOCAL
                    else ClaudeInstallMode.PROJECT
                ),
                fix_session_hook=False,
                fix_global_reminder=False,
                dry_run=False,
                environment=environment,
            ),
            context=context,
        )
    except DoctorError as exc:
        raise CodexAdapterError(str(exc)) from exc
    for event in result.events:
        stream = (
            CodexEventStream.STDOUT
            if event.stream is DoctorEventStream.STDOUT
            else CodexEventStream.STDERR
        )
        _event(events, stream, event.text)
    return result.exit_code


def _project_status(
    events: list[CodexEvent], context: Optional[TargetProjectContext]
) -> None:
    if context is None:
        return
    _stdout(events, f"Project: {context.root}")
    if _project_has_marker(context.root):
        _stdout(events, "[OK] Project has Agent Rails marker.")
    else:
        _stdout(
            events,
            "[WARN] Project has no Agent Rails marker yet. "
            f'Run `agent-rails doctor --project "{context.root}" --fix` '
            "or pass --fix-project.",
        )


def _project_has_marker(project: Path) -> bool:
    for relative in (
        Path(".codex-plugin/plugin.json"),
        Path(".claude/AGENT_RAILS.md"),
        Path(".opencode/AGENT_RAILS.md"),
    ):
        if _read_project_regular(project, relative) is not None:
            return True
    for relative in (Path("CLAUDE.local.md"), Path("CLAUDE.md")):
        raw = _read_project_regular(project, relative)
        if raw is not None and _RULES_MARKER in raw:
            return True
    return False


def _read_project_regular(project: Path, relative: Path) -> Optional[bytes]:
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise CodexAdapterError("Invalid Codex project marker path.")
    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptors: list[int] = []
    try:
        current = os.open(project, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        flags = os.O_RDONLY
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(parts[-1], flags, dir_fd=current)
        descriptors.append(descriptor)
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            return None
        if opened.st_size > _MAX_MARKER_BYTES:
            raise CodexAdapterError(
                f"Codex project marker exceeds {_MAX_MARKER_BYTES} bytes."
            )
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise CodexAdapterError(
                    "Codex project marker changed while reading."
                )
            chunks.append(chunk)
            remaining -= len(chunk)
        closed = os.fstat(descriptor)
        if (
            closed.st_size != opened.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
            or closed.st_ctime_ns != opened.st_ctime_ns
        ):
            raise CodexAdapterError("Codex project marker changed while reading.")
        return b"".join(chunks)
    except OSError as exc:
        if exc.errno in {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
            errno.EISDIR,
        }:
            return None
        raise CodexAdapterError("Unable to inspect Codex project marker.") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _canonical_directory(path: Path) -> Path:
    canonical = Path(os.path.realpath(os.fspath(path)))
    if not canonical.is_dir():
        raise CodexAdapterError(f"Working directory not found: {path}")
    return canonical


def _find_codex(environment: Mapping[str, str]) -> Optional[str]:
    search_directories = tuple(
        entry
        for entry in environment.get("PATH", "").split(os.pathsep)
        if entry and os.path.isabs(entry)
    )
    if not search_directories:
        return None
    value = shutil.which("codex", path=os.pathsep.join(search_directories))
    return None if value is None else os.path.realpath(value)


def _require_codex(environment: Mapping[str, str]) -> str:
    executable = _find_codex(environment)
    if executable is None:
        raise CodexAdapterError(
            "Codex CLI not found. Install Codex first, then rerun this command.",
            exit_code=127,
        )
    return executable


def _execute_codex(
    executable: str,
    arguments: tuple[str, ...],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    prefix_events: list[CodexEvent],
) -> _CodexCommandResult:
    process: Optional[subprocess.Popen[bytes]] = None
    try:
        process = subprocess.Popen(
            (executable, *arguments),
            cwd=working_directory,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        raise CodexAdapterError(
            "Unable to execute Codex CLI.", events=tuple(prefix_events)
        ) from exc

    try:
        stdout, stderr = _read_child_output(process)
        returncode = process.wait()
    except CodexAdapterError as exc:
        _terminate_process_group(process)
        if exc.events:
            raise
        raise CodexAdapterError(
            str(exc),
            exit_code=exc.exit_code,
            events=tuple(prefix_events),
        ) from exc
    except OSError as exc:
        _terminate_process_group(process)
        raise CodexAdapterError(
            "Unable to read Codex CLI output.", events=tuple(prefix_events)
        ) from exc
    return _CodexCommandResult(
        returncode=returncode,
        stdout=stdout.decode("utf-8", "surrogateescape"),
        stderr=stderr.decode("utf-8", "surrogateescape"),
    )


def _read_child_output(
    process: subprocess.Popen[bytes],
) -> tuple[bytes, bytes]:
    if process.stdout is None or process.stderr is None:
        raise CodexAdapterError("Codex CLI output streams are unavailable.")
    selector = selectors.DefaultSelector()
    streams = {
        process.stdout.fileno(): (process.stdout, bytearray()),
        process.stderr.fileno(): (process.stderr, bytearray()),
    }
    total = 0
    try:
        for descriptor, (stream, _) in streams.items():
            os.set_blocking(descriptor, False)
            selector.register(stream, selectors.EVENT_READ, descriptor)
        while selector.get_map():
            for key, _ in selector.select():
                descriptor = key.data
                try:
                    chunk = os.read(
                        descriptor,
                        max(
                            1,
                            min(
                                _CHILD_READ_BYTES,
                                _MAX_CHILD_OUTPUT_BYTES - total + 1,
                            ),
                        ),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                streams[descriptor][1].extend(chunk)
                total += len(chunk)
                if total > _MAX_CHILD_OUTPUT_BYTES:
                    raise CodexAdapterError(
                        "Codex CLI output exceeds 1000000 bytes."
                    )
        return (
            bytes(streams[process.stdout.fileno()][1]),
            bytes(streams[process.stderr.fileno()][1]),
        )
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=0.25)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except OSError:
        pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _record_command_result(
    events: list[CodexEvent], completed: _CodexCommandResult
) -> None:
    _stdout_process(events, completed.stdout)
    _stderr_process(events, completed.stderr)
    if completed.returncode != 0:
        exit_code = _shell_exit_code(completed.returncode)
        raise CodexAdapterError(
            f"Codex CLI command failed with exit status {exit_code}.",
            exit_code=exit_code,
            events=tuple(events),
        )


def _shell_exit_code(returncode: int) -> int:
    return returncode if returncode >= 0 else 128 + abs(returncode)


def _resolve_version(kit_home: Path, environment: Mapping[str, str]) -> str:
    override = environment.get("AGENT_RAILS_VERSION_OVERRIDE", "")
    if override:
        return override
    raw = _read_project_regular(kit_home, Path("VERSION"))
    if raw is None:
        return "0.0.0-dev"
    for line in raw.splitlines():
        fields = line.split()
        if fields:
            return fields[0].decode("utf-8", "surrogateescape")
    return ""


def _stdout(events: list[CodexEvent], text: str) -> None:
    _event(events, CodexEventStream.STDOUT, text)


def _stdout_process(events: list[CodexEvent], text: str) -> None:
    lines = text.splitlines()
    for line in lines:
        _stdout(events, line)


def _stderr_process(events: list[CodexEvent], text: str) -> None:
    for line in text.splitlines():
        _event(events, CodexEventStream.STDERR, line)


def _event(
    events: list[CodexEvent], stream: CodexEventStream, text: str
) -> None:
    events.append(CodexEvent(stream, _terminal_literal(text)))


def _terminal_literal(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif category in {"Cc", "Cf", "Zl", "Zp"} or 0xD800 <= codepoint <= 0xDFFF:
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _render_events(
    events: Tuple[CodexEvent, ...], stream: CodexEventStream
) -> str:
    selected = [event.text for event in events if event.stream is stream]
    return "" if not selected else "\n".join(selected) + "\n"
