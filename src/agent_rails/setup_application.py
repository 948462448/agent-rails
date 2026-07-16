"""Configure one or more Agent Rails integrations through typed services."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import os
from pathlib import Path
import shlex
import shutil
from typing import Mapping, Optional, Tuple

from agent_rails.adapters.claude import (
    CLAUDE_PROFILE_VARIABLES,
    ClaudeAdapterError,
    ClaudeEventStream,
    ClaudeInstallMode,
    ClaudeInstallRequest,
    run_claude_adapter,
)
from agent_rails.adapters.codex import (
    CodexAdapterError,
    CodexDoctorRequest,
    CodexEventStream,
    CodexInstallMode,
    CodexInstallRequest,
    run_codex_adapter,
)
from agent_rails.adapters.opencode import (
    OPENCODE_PROFILE_VARIABLES,
    OpenCodeAdapterError,
    OpenCodeDoctorRequest,
    OpenCodeEventStream,
    OpenCodeInstallMode,
    OpenCodeInstallRequest,
    run_opencode_adapter,
)
from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_target_project,
)
from agent_rails.core.terminal import (
    render_line_events as _render_events,
    terminal_literal as _terminal_literal,
)
from agent_rails.diagnostics.doctor import (
    DoctorError,
    DoctorEventStream,
    DoctorRequest,
    _DOCTOR_PROFILE_VARIABLES,
    run_doctor,
)


_SUPPORTED_TOOLS = ("claude", "codex", "opencode")
_SETUP_PROFILE_VARIABLES = tuple(
    dict.fromkeys(
        (
            *CLAUDE_PROFILE_VARIABLES,
            *OPENCODE_PROFILE_VARIABLES,
            *_DOCTOR_PROFILE_VARIABLES,
        )
    )
)


class SetupTool(str, Enum):
    AUTO = "auto"
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"
    ALL = "all"


class SetupInstallMode(str, Enum):
    LOCAL = "local"
    PROJECT = "project"


class SetupAction(str, Enum):
    INSTALL = "install"
    DOCTOR = "doctor"


class SetupEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class SetupRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    tool: SetupTool
    mode: SetupInstallMode
    session_hook: bool
    dry_run: bool
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class SetupStep:
    tool: SetupTool
    action: SetupAction
    exit_code: int


@dataclass(frozen=True)
class SetupEvent:
    stream: SetupEventStream
    text: str


class SetupApplicationError(RuntimeError):
    """Setup stopped after a runtime child-service failure."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        failed_tool: Optional[SetupTool] = None,
        failed_action: Optional[SetupAction] = None,
        steps: Tuple[SetupStep, ...] = (),
        events: Tuple[SetupEvent, ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.failed_tool = failed_tool
        self.failed_action = failed_action
        self.steps = steps
        self.events = events

    @property
    def stdout(self) -> str:
        return _render_events(self.events, SetupEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, SetupEventStream.STDERR)


class SetupInputError(SetupApplicationError):
    """The caller supplied an invalid Setup request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


@dataclass(frozen=True)
class SetupResult:
    project_root: Path
    profile_path: str
    selected_tools: Tuple[SetupTool, ...]
    mode: SetupInstallMode
    steps: Tuple[SetupStep, ...]
    exit_code: int
    failed_tool: Optional[SetupTool]
    failed_action: Optional[SetupAction]
    events: Tuple[SetupEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_events(self.events, SetupEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, SetupEventStream.STDERR)


def run_setup(request: SetupRequest) -> SetupResult:
    """Resolve one Context, then compose selected Adapter/Doctor services."""

    _validate_request(request)
    working_directory = _canonical_directory(
        _anchored_path(request.working_directory, Path.cwd()),
        "Setup working directory",
        SetupApplicationError,
    )
    request = replace(request, working_directory=working_directory)
    requested_project = _anchored_path(
        request.requested_project, working_directory
    )
    if not requested_project.is_dir():
        raise SetupInputError(
            "Project directory not found: "
            f"{_terminal_literal(str(request.requested_project))}"
        )
    kit_home = _canonical_directory(
        _anchored_path(request.kit_home, working_directory),
        "Agent Rails home",
        SetupApplicationError,
    )
    explicit_profile = _anchored_profile(
        request.explicit_profile, working_directory
    )
    environment = dict(request.environment)
    try:
        context = resolve_target_project(
            requested_project,
            kit_home=kit_home,
            explicit_profile=explicit_profile,
            environment=environment,
            require_profile=True,
            load_profile=True,
            load_environment_file=False,
            profile_variables=_SETUP_PROFILE_VARIABLES,
            capture_profile_environment=True,
        )
    except FileNotFoundError as exc:
        raise SetupInputError(
            f"Profile not found: {_terminal_literal(str(exc))}"
        ) from exc
    except (TargetProjectError, ProfileLoadError) as exc:
        raise SetupInputError(str(exc)) from exc

    if context.profile_environment:
        environment = dict(context.profile_environment)
    selected, detection = _select_tools(request.tool, environment)
    events: list[SetupEvent] = []
    if detection is not None:
        _stdout(events, f"Detected tool: {detection.value}")
    _stdout(events, "Agent Rails Setup")
    _stdout(events, f"Project: {context.root}")
    _stdout(events, f"Profile: {context.profile_path}")
    _stdout(events, f"Mode: {request.mode.value}")
    steps: list[SetupStep] = []

    for tool in selected:
        _stdout(events)
        _stdout(events, f"Tool: {tool.value}")
        install_exit = _run_install(
            tool,
            request=request,
            context=context,
            kit_home=kit_home,
            environment=environment,
            events=events,
            steps=steps,
        )
        steps.append(SetupStep(tool, SetupAction.INSTALL, install_exit))
        if install_exit:
            return _result(
                context,
                selected,
                request.mode,
                steps,
                events,
                install_exit,
                tool,
                SetupAction.INSTALL,
            )

        if request.dry_run:
            _stdout(events, f"Would run: {_doctor_command(tool, kit_home, context)}")
            continue

        doctor_exit = _run_tool_doctor(
            tool,
            request=request,
            context=context,
            kit_home=kit_home,
            environment=environment,
            events=events,
            steps=steps,
        )
        steps.append(SetupStep(tool, SetupAction.DOCTOR, doctor_exit))
        if doctor_exit:
            return _result(
                context,
                selected,
                request.mode,
                steps,
                events,
                doctor_exit,
                tool,
                SetupAction.DOCTOR,
            )

    _stdout(
        events,
        "Next: "
        f"cd {shlex.quote(str(context.root))} && agent-rails run \"<goal>\"",
    )
    _stdout(events)
    _stdout(events, "Agent Rails setup complete.")
    return _result(
        context,
        selected,
        request.mode,
        steps,
        events,
        0,
        None,
        None,
    )


def _validate_request(request: SetupRequest) -> None:
    if not isinstance(request, SetupRequest):
        raise SetupInputError("Invalid Setup request.")
    if not isinstance(request.requested_project, Path):
        raise SetupInputError("Setup requested project must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise SetupInputError("Setup kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise SetupInputError("Setup explicit Profile must be text.")
    if not isinstance(request.tool, SetupTool):
        raise SetupInputError("Invalid Setup tool.")
    if not isinstance(request.mode, SetupInstallMode):
        raise SetupInputError("Invalid Setup install mode.")
    for name in ("session_hook", "dry_run"):
        if not isinstance(getattr(request, name), bool):
            raise SetupInputError(f"Setup {name} policy must be boolean.")
    if not isinstance(request.working_directory, Path):
        raise SetupInputError("Setup working directory must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise SetupInputError("Setup environment must be a mapping.")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise SetupInputError("Setup environment keys and values must be text.")


def _select_tools(
    requested: SetupTool, environment: Mapping[str, str]
) -> tuple[Tuple[SetupTool, ...], Optional[SetupTool]]:
    if requested is SetupTool.ALL:
        return (
            (SetupTool.CLAUDE, SetupTool.CODEX, SetupTool.OPENCODE),
            None,
        )
    if requested is not SetupTool.AUTO:
        return ((requested,), None)
    detected = tuple(
        SetupTool(name)
        for name in _SUPPORTED_TOOLS
        if _find_executable(name, environment) is not None
    )
    if not detected:
        raise SetupInputError(
            "No supported coding-agent CLI detected. Choose --tool claude, "
            "codex, or opencode."
        )
    if len(detected) > 1:
        names = ", ".join(tool.value for tool in detected)
        raise SetupInputError(
            f"Multiple supported tools detected: {names}. Choose one with "
            "--tool claude|codex|opencode, or use --tool all intentionally."
        )
    return (detected, detected[0])


def _find_executable(
    name: str, environment: Mapping[str, str]
) -> Optional[str]:
    directories = tuple(
        entry
        for entry in environment.get("PATH", "").split(os.pathsep)
        if entry and os.path.isabs(entry)
    )
    if not directories:
        return None
    return shutil.which(name, path=os.pathsep.join(directories))


def _run_install(
    tool: SetupTool,
    *,
    request: SetupRequest,
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
    events: list[SetupEvent],
    steps: list[SetupStep],
) -> int:
    try:
        if tool is SetupTool.CLAUDE:
            result = run_claude_adapter(
                ClaudeInstallRequest(
                    requested_project=context.root,
                    kit_home=kit_home,
                    explicit_profile=context.profile_path,
                    mode=(
                        ClaudeInstallMode.LOCAL
                        if request.mode is SetupInstallMode.LOCAL
                        else ClaudeInstallMode.PROJECT
                    ),
                    dry_run=request.dry_run,
                    force=False,
                    global_reminder=False,
                    session_hook=request.session_hook,
                    environment=environment,
                ),
                context=context,
            )
            _append_events(events, result.events, ClaudeEventStream)
            return 0
        if tool is SetupTool.CODEX:
            result = run_codex_adapter(
                CodexInstallRequest(
                    requested_project=context.root,
                    kit_home=kit_home,
                    explicit_profile=context.profile_path,
                    mode=(
                        CodexInstallMode.LOCAL
                        if request.mode is SetupInstallMode.LOCAL
                        else CodexInstallMode.PROJECT
                    ),
                    fix_project=True,
                    dry_run=request.dry_run,
                    working_directory=request.working_directory,
                    environment=environment,
                ),
                context=context,
            )
            _append_events(events, result.events, CodexEventStream)
            return result.exit_code
        result = run_opencode_adapter(
            OpenCodeInstallRequest(
                requested_project=context.root,
                kit_home=kit_home,
                explicit_profile=context.profile_path,
                mode=(
                    OpenCodeInstallMode.LOCAL
                    if request.mode is SetupInstallMode.LOCAL
                    else OpenCodeInstallMode.PROJECT
                ),
                dry_run=request.dry_run,
                force=False,
                environment=environment,
            ),
            context=context,
        )
        _append_events(events, result.events, OpenCodeEventStream)
        return 0
    except (ClaudeAdapterError, CodexAdapterError, OpenCodeAdapterError) as exc:
        _raise_child_error(exc, tool, SetupAction.INSTALL, steps, events)
    raise AssertionError("unreachable")


def _run_tool_doctor(
    tool: SetupTool,
    *,
    request: SetupRequest,
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
    events: list[SetupEvent],
    steps: list[SetupStep],
) -> int:
    try:
        if tool is SetupTool.CLAUDE:
            result = run_doctor(
                DoctorRequest(
                    requested_project=context.root,
                    kit_home=kit_home,
                    explicit_profile=context.profile_path,
                    online_memory_smoke=False,
                    fix=False,
                    fix_mode=ClaudeInstallMode.LOCAL,
                    fix_session_hook=False,
                    fix_global_reminder=False,
                    dry_run=False,
                    environment=environment,
                ),
                context=context,
            )
            _append_events(events, result.events, DoctorEventStream)
            return result.exit_code
        if tool is SetupTool.CODEX:
            result = run_codex_adapter(
                CodexDoctorRequest(
                    requested_project=context.root,
                    kit_home=kit_home,
                    explicit_profile=context.profile_path,
                    working_directory=request.working_directory,
                    environment=environment,
                ),
                context=context,
            )
            _append_events(events, result.events, CodexEventStream)
            return result.exit_code
        result = run_opencode_adapter(
            OpenCodeDoctorRequest(
                requested_project=context.root,
                kit_home=kit_home,
                explicit_profile=context.profile_path,
                environment=environment,
            ),
            context=context,
        )
        _append_events(events, result.events, OpenCodeEventStream)
        return 0
    except (DoctorError, CodexAdapterError, OpenCodeAdapterError) as exc:
        _raise_child_error(exc, tool, SetupAction.DOCTOR, steps, events)
    raise AssertionError("unreachable")


def _raise_child_error(
    error: BaseException,
    tool: SetupTool,
    action: SetupAction,
    steps: list[SetupStep],
    events: list[SetupEvent],
) -> None:
    child_events = getattr(error, "events", ())
    _append_events(events, child_events, None)
    raise SetupApplicationError(
        str(error),
        exit_code=getattr(error, "exit_code", 1),
        failed_tool=tool,
        failed_action=action,
        steps=tuple(steps),
        events=tuple(events),
    ) from error


def _append_events(
    target: list[SetupEvent], child_events: Tuple[object, ...], enum_type: object
) -> None:
    del enum_type
    for event in child_events:
        stream_value = getattr(getattr(event, "stream", None), "value", "stdout")
        stream = (
            SetupEventStream.STDERR
            if stream_value == "stderr"
            else SetupEventStream.STDOUT
        )
        target.append(
            SetupEvent(stream, _terminal_literal(str(getattr(event, "text", ""))))
        )


def _doctor_command(
    tool: SetupTool, kit_home: Path, context: TargetProjectContext
) -> str:
    executable = str(kit_home / "bin/agent-rails")
    if tool is SetupTool.CLAUDE:
        arguments = (
            executable,
            "doctor",
            "--project",
            str(context.root),
            "--profile",
            context.profile_path,
        )
    else:
        arguments = (
            executable,
            tool.value,
            "doctor",
            "--project",
            str(context.root),
        )
    return shlex.join(arguments)


def _result(
    context: TargetProjectContext,
    selected: Tuple[SetupTool, ...],
    mode: SetupInstallMode,
    steps: list[SetupStep],
    events: list[SetupEvent],
    exit_code: int,
    failed_tool: Optional[SetupTool],
    failed_action: Optional[SetupAction],
) -> SetupResult:
    return SetupResult(
        project_root=context.root,
        profile_path=context.profile_path,
        selected_tools=selected,
        mode=mode,
        steps=tuple(steps),
        exit_code=exit_code,
        failed_tool=failed_tool,
        failed_action=failed_action,
        events=tuple(events),
    )


def _anchored_path(path: Path, working_directory: Path) -> Path:
    return path if path.is_absolute() else working_directory / path


def _anchored_profile(
    profile: Optional[str], working_directory: Path
) -> Optional[str]:
    if profile is None:
        return None
    path = Path(profile)
    if not path.is_absolute():
        path = working_directory / path
    return os.path.abspath(os.fspath(path))


def _canonical_directory(
    path: Path,
    label: str,
    error_type: type[SetupApplicationError],
) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_dir():
        raise error_type(f"{label} not found: {_terminal_literal(str(path))}")
    return absolute


def _stdout(events: list[SetupEvent], text: str = "") -> None:
    events.append(SetupEvent(SetupEventStream.STDOUT, _terminal_literal(text)))
