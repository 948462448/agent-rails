"""Update one Agent Rails installation and optionally refresh one Adapter."""

from __future__ import annotations

import codecs
from dataclasses import dataclass, field
from enum import Enum
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import time
from typing import Callable, Mapping, Optional, Sequence, Tuple
import unicodedata

from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_target_project,
)
from agent_rails.git._runner import isolated_git_environment


class UpdateMode(str, Enum):
    PROJECT = "project"
    SELF = "self"


class UpdateTool(str, Enum):
    CLAUDE = "claude"
    CODEX = "codex"
    OPENCODE = "opencode"


class UpdateInstallMode(str, Enum):
    LOCAL = "local"
    PROJECT = "project"


class UpdateSource(str, Enum):
    GIT = "git"
    RELEASE = "release"


class UpdateAction(str, Enum):
    GIT_PROBE = "git-probe"
    GIT_STATUS = "git-status"
    GIT_UPSTREAM = "git-upstream"
    GIT_BRANCH = "git-branch"
    GIT_PULL = "git-pull"
    RELEASE_INSTALL = "release-install"
    SOURCE_TESTS = "source-tests"
    PRE_DOCTOR = "pre-doctor"
    ADAPTER_INSTALL = "adapter-install"
    FINAL_DOCTOR = "final-doctor"


class UpdateEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class UpdateEvent:
    stream: UpdateEventStream
    text: str


@dataclass(frozen=True)
class UpdateCommand:
    action: UpdateAction
    argv: Tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]
    emit: Optional[Callable[[UpdateEvent], None]] = field(
        default=None, compare=False, repr=False
    )


@dataclass(frozen=True)
class UpdateCommandResult:
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    output_emitted: bool = False


@dataclass(frozen=True)
class UpdateReexecRequest:
    argv: Tuple[str, ...]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class UpdateStep:
    action: UpdateAction
    argv: Tuple[str, ...]
    exit_code: int


@dataclass(frozen=True)
class _ProjectCommand:
    argv: Tuple[str, ...]
    display_argv: Tuple[str, ...]


@dataclass(frozen=True)
class UpdateRequest:
    mode: UpdateMode
    requested_project: Optional[Path]
    kit_home: Path
    explicit_profile: Optional[str]
    tool: Optional[UpdateTool]
    install_mode: UpdateInstallMode
    session_hook: bool
    global_reminder: bool
    requested_version: str
    repository: str
    install_root: Path
    bin_dir: Path
    skip_pull: bool
    skip_tests: bool
    skip_doctor: bool
    skip_adapter: bool
    dry_run: bool
    original_arguments: Tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class UpdateResult:
    mode: UpdateMode
    source: UpdateSource
    project_root: Optional[Path]
    profile_path: Optional[str]
    steps: Tuple[UpdateStep, ...]
    exit_code: int
    failed_action: Optional[UpdateAction]
    events: Tuple[UpdateEvent, ...]
    reexec_requested: bool = False

    @property
    def stdout(self) -> str:
        return _render_events(self.events, UpdateEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, UpdateEventStream.STDERR)


class UpdateApplicationError(RuntimeError):
    """Update orchestration could not safely continue."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        failed_action: Optional[UpdateAction] = None,
        steps: Tuple[UpdateStep, ...] = (),
        events: Tuple[UpdateEvent, ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.failed_action = failed_action
        self.steps = steps
        self.events = events

    @property
    def stdout(self) -> str:
        return _render_events(self.events, UpdateEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, UpdateEventStream.STDERR)


class UpdateInputError(UpdateApplicationError):
    """The caller supplied an invalid Update request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


UpdateRunner = Callable[[UpdateCommand], UpdateCommandResult]
UpdateReexec = Callable[[UpdateReexecRequest], None]
TargetResolver = Callable[..., TargetProjectContext]
UpdateEventSink = Callable[[UpdateEvent], None]


def _default_runner(command: UpdateCommand) -> UpdateCommandResult:
    if command.emit is not None:
        return _stream_default_command(command)
    try:
        completed = subprocess.run(
            command.argv,
            cwd=command.working_directory,
            env=dict(command.environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )
    except FileNotFoundError as exc:
        return UpdateCommandResult(127, stderr=f"{exc}\n")
    except PermissionError as exc:
        return UpdateCommandResult(126, stderr=f"{exc}\n")
    exit_code = completed.returncode
    if exit_code < 0:
        exit_code = 128 - exit_code
    return UpdateCommandResult(
        exit_code=exit_code,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _stream_default_command(command: UpdateCommand) -> UpdateCommandResult:
    try:
        process = subprocess.Popen(
            command.argv,
            cwd=command.working_directory,
            env=dict(command.environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        return UpdateCommandResult(127, stderr=f"{exc}\n")
    except PermissionError as exc:
        return UpdateCommandResult(126, stderr=f"{exc}\n")

    selector = selectors.DefaultSelector()
    streams = (
        (process.stdout, UpdateEventStream.STDOUT),
        (process.stderr, UpdateEventStream.STDERR),
    )
    try:
        for stream, event_stream in streams:
            assert stream is not None
            os.set_blocking(stream.fileno(), False)
            decoder = codecs.getincrementaldecoder("utf-8")(
                errors="backslashreplace"
            )
            selector.register(stream, selectors.EVENT_READ, (event_stream, decoder))
        while selector.get_map():
            for key, _ in selector.select():
                stream = key.fileobj
                event_stream, decoder = key.data
                try:
                    chunk = os.read(stream.fileno(), 65_536)
                except (BlockingIOError, InterruptedError):
                    continue
                if chunk:
                    assert command.emit is not None
                    command.emit(
                        UpdateEvent(
                            event_stream,
                            _terminal_stream_text(decoder.decode(chunk)),
                        )
                    )
                    continue
                tail = decoder.decode(b"", final=True)
                if tail:
                    assert command.emit is not None
                    command.emit(
                        UpdateEvent(event_stream, _terminal_stream_text(tail))
                    )
                selector.unregister(stream)
                stream.close()
        exit_code = process.wait()
        if exit_code < 0:
            exit_code = 128 - exit_code
        return UpdateCommandResult(exit_code, output_emitted=True)
    except BaseException:
        _stop_process_group(process)
        raise
    finally:
        selector.close()
        for stream, _ in streams:
            if stream is not None and not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def _stop_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=0.25)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if _process_group_alive(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    deadline = time.monotonic() + 1
    while _process_group_alive(process_group) and time.monotonic() < deadline:
        time.sleep(0.01)


def _process_group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _default_reexec(request: UpdateReexecRequest) -> None:
    previous_umask = os.umask(0o077)
    try:
        os.execve(request.argv[0], list(request.argv), dict(request.environment))
    finally:
        os.umask(previous_umask)


@dataclass(frozen=True)
class UpdateDependencies:
    runner: UpdateRunner = _default_runner
    reexec: UpdateReexec = _default_reexec
    target_resolver: TargetResolver = resolve_target_project
    event_sink: Optional[UpdateEventSink] = None


class _UpdateEventLog(list[UpdateEvent]):
    def __init__(self, sink: Optional[UpdateEventSink]) -> None:
        super().__init__()
        self._sink = sink

    def append(self, event: UpdateEvent) -> None:
        super().append(event)
        if self._sink is not None:
            self._sink(event)


def resolve_release_defaults(
    kit_home: Path,
    environment: Mapping[str, str],
) -> tuple[str, Path, Path]:
    """Resolve Release settings without executing project or Profile code."""

    home = environment.get("HOME") or str(Path.home())
    data_home = environment.get("XDG_DATA_HOME") or f"{home}/.local/share"
    default_root = Path(data_home) / "agent-rails"
    configured_root = environment.get("AGENT_RAILS_INSTALL_ROOT")
    if configured_root:
        install_root = Path(configured_root)
    elif kit_home.name == "current":
        install_root = kit_home.parent
    elif kit_home.parent.name == "releases":
        install_root = kit_home.parent.parent
    else:
        install_root = default_root

    configured_repository = environment.get("AGENT_RAILS_RELEASE_REPOSITORY")
    repository = (
        configured_repository
        or _read_first_field(install_root / "release-repository")
        or "948462448/agent-rails"
    )
    configured_bin = environment.get("AGENT_RAILS_BIN_DIR")
    bin_dir_text = (
        configured_bin
        or _read_first_line(install_root / "release-bin-dir")
        or f"{home}/.local/bin"
    )
    return repository, install_root, Path(bin_dir_text)


def run_update(
    request: UpdateRequest,
    *,
    dependencies: Optional[UpdateDependencies] = None,
) -> UpdateResult:
    """Apply one typed Git/Release update plan and optional project refresh."""

    _validate_request(request)
    dependencies = dependencies or UpdateDependencies()
    working_directory = _canonical_directory(
        request.working_directory, "Update working directory"
    )
    kit_home = _canonical_directory(
        _anchored_path(request.kit_home, working_directory),
        "Agent Rails home",
    )
    install_root = _anchored_path(request.install_root, working_directory)
    bin_dir = _anchored_path(request.bin_dir, working_directory)
    environment = dict(request.environment)
    environment["AGENT_RAILS_HOME"] = str(kit_home)
    context = _resolve_context(
        request,
        dependencies=dependencies,
        kit_home=kit_home,
        working_directory=working_directory,
        environment=environment,
    )
    project_root = None if context is None else context.root
    profile_path = None if context is None else str(context.profile_path)
    steps: list[UpdateStep] = []
    events: list[UpdateEvent] = _UpdateEventLog(dependencies.event_sink)

    probe = _run_command(
        dependencies,
        UpdateAction.GIT_PROBE,
        ("git", "-C", str(kit_home), "rev-parse", "--show-toplevel"),
        working_directory,
        isolated_git_environment(environment),
        steps,
        events,
        include_output=False,
    )
    if probe.exit_code in {126, 127}:
        if probe.stderr:
            _chunk(events, UpdateEventStream.STDERR, probe.stderr)
        raise UpdateApplicationError(
            "Git is required to classify the Agent Rails installation.",
            exit_code=probe.exit_code,
            failed_action=UpdateAction.GIT_PROBE,
            steps=tuple(steps),
            events=tuple(events),
        )
    probe_root = probe.stdout.strip() if probe.exit_code == 0 else ""
    source = (
        UpdateSource.GIT
        if probe_root and _physical_path(Path(probe_root)) == kit_home
        else UpdateSource.RELEASE
    )

    _line(events, "Agent Rails Update")
    _line(events, f"Kit: {_terminal_literal(str(kit_home))}")
    if request.mode is UpdateMode.SELF:
        _line(events, "Mode: self")
    else:
        assert request.tool is not None
        _line(events, "Mode: project")
        _line(events, f"Tool: {request.tool.value}")
    if context is not None:
        _line(events, f"Project: {_terminal_literal(str(context.root))}")
        _line(events, f"Profile: {_terminal_literal(str(context.profile_path))}")
        _line(events, f"Adapter mode: {request.install_mode.value}")

    if source is UpdateSource.GIT:
        source_result = _update_git_source(
            request,
            dependencies=dependencies,
            kit_home=kit_home,
            working_directory=working_directory,
            environment=environment,
            steps=steps,
            events=events,
        )
    else:
        source_result = _update_release_source(
            request,
            dependencies=dependencies,
            kit_home=kit_home,
            install_root=install_root,
            bin_dir=bin_dir,
            working_directory=working_directory,
            environment=environment,
            steps=steps,
            events=events,
        )
    if source_result is not None:
        return _result(
            request,
            source,
            context,
            steps,
            events,
            exit_code=source_result[0],
            failed_action=source_result[1],
            reexec_requested=source_result[2],
        )

    tests_result = _run_source_tests(
        request,
        source=source,
        dependencies=dependencies,
        kit_home=kit_home,
        working_directory=working_directory,
        environment=environment,
        steps=steps,
        events=events,
    )
    if tests_result is not None:
        return _result(
            request,
            source,
            context,
            steps,
            events,
            exit_code=tests_result,
            failed_action=UpdateAction.SOURCE_TESTS,
        )

    if request.mode is UpdateMode.PROJECT:
        assert context is not None or (request.skip_doctor and request.skip_adapter)
        project_result = _refresh_project(
            request,
            context=context,
            dependencies=dependencies,
            kit_home=kit_home,
            working_directory=working_directory,
            environment=environment,
            steps=steps,
            events=events,
        )
        if project_result is not None:
            return _result(
                request,
                source,
                context,
                steps,
                events,
                exit_code=project_result[0],
                failed_action=project_result[1],
            )

    _line(events, "")
    _line(events, "Agent Rails update complete.")
    return _result(request, source, context, steps, events)


def _resolve_context(
    request: UpdateRequest,
    *,
    dependencies: UpdateDependencies,
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
) -> Optional[TargetProjectContext]:
    if request.mode is UpdateMode.SELF or (
        request.skip_doctor and request.skip_adapter
    ):
        return None
    assert request.requested_project is not None
    project = _anchored_path(request.requested_project, working_directory)
    if not project.is_dir():
        raise UpdateInputError(
            "Project directory not found: "
            f"{_terminal_literal(str(request.requested_project))}"
        )
    profile = request.explicit_profile
    if profile is not None and not Path(profile).is_absolute():
        profile = str(working_directory / profile)
    try:
        return dependencies.target_resolver(
            project,
            kit_home=kit_home,
            explicit_profile=profile,
            environment=environment,
            require_profile=True,
            load_profile=False,
        )
    except FileNotFoundError as exc:
        raise UpdateInputError(
            f"Profile not found: {_terminal_literal(str(exc))}"
        ) from exc
    except TargetProjectError as exc:
        raise UpdateInputError(str(exc)) from exc


def _update_git_source(
    request: UpdateRequest,
    *,
    dependencies: UpdateDependencies,
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Optional[tuple[int, Optional[UpdateAction], bool]]:
    if request.skip_pull:
        _line(events, "")
        _line(events, "Skip git pull (--skip-pull).")
        return None
    if request.requested_version != "latest":
        raise UpdateInputError(
            "--version is only supported by a GitHub Release installation."
        )

    git_environment = isolated_git_environment(environment)
    if not request.dry_run:
        status = _run_command(
            dependencies,
            UpdateAction.GIT_STATUS,
            ("git", "-C", str(kit_home), "status", "--porcelain"),
            working_directory,
            git_environment,
            steps,
            events,
            include_output=False,
        )
        if status.exit_code:
            if status.stderr:
                _chunk(events, UpdateEventStream.STDERR, status.stderr)
            return status.exit_code, UpdateAction.GIT_STATUS, False
        if status.stdout:
            raise UpdateApplicationError(
                "Agent Rails kit has local changes; commit/stash them or pass "
                "--skip-pull.",
                steps=tuple(steps),
                events=tuple(events),
            )

    pull_argv = _git_pull_argv(
        dependencies,
        kit_home=kit_home,
        working_directory=working_directory,
        environment=git_environment,
        steps=steps,
        events=events,
    )
    _line(events, "")
    _line(events, "Update Agent Rails kit")
    if request.dry_run:
        _line(events, f"Would run: {_render_command(pull_argv)}")
        return None
    result = _run_command(
        dependencies,
        UpdateAction.GIT_PULL,
        pull_argv,
        working_directory,
        git_environment,
        steps,
        events,
    )
    if result.exit_code:
        return result.exit_code, UpdateAction.GIT_PULL, False
    return None


def _git_pull_argv(
    dependencies: UpdateDependencies,
    *,
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Tuple[str, ...]:
    upstream = _run_command(
        dependencies,
        UpdateAction.GIT_UPSTREAM,
        (
            "git",
            "-C",
            str(kit_home),
            "rev-parse",
            "--abbrev-ref",
            "--symbolic-full-name",
            "@{upstream}",
        ),
        working_directory,
        environment,
        steps,
        events,
        include_output=False,
    )
    if upstream.exit_code == 0:
        return ("git", "-C", str(kit_home), "pull", "--ff-only")
    branch = _run_command(
        dependencies,
        UpdateAction.GIT_BRANCH,
        ("git", "-C", str(kit_home), "branch", "--show-current"),
        working_directory,
        environment,
        steps,
        events,
        include_output=False,
    )
    if branch.exit_code:
        if branch.stderr:
            _chunk(events, UpdateEventStream.STDERR, branch.stderr)
        raise UpdateApplicationError(
            "Unable to determine the current Agent Rails Git branch.",
            exit_code=branch.exit_code,
            failed_action=UpdateAction.GIT_BRANCH,
            steps=tuple(steps),
            events=tuple(events),
        )
    selected = branch.stdout.strip()
    return (
        "git",
        "-C",
        str(kit_home),
        "pull",
        "--ff-only",
        "origin",
        selected or "main",
    )


def _update_release_source(
    request: UpdateRequest,
    *,
    dependencies: UpdateDependencies,
    kit_home: Path,
    install_root: Path,
    bin_dir: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Optional[tuple[int, Optional[UpdateAction], bool]]:
    if request.skip_pull:
        _line(events, "")
        _line(events, "Skip release download (--skip-pull).")
        return None
    installer = kit_home / "src/agent_rails/release/install.py"
    if not installer.is_file() or not os.access(installer, os.R_OK):
        raise UpdateInputError(
            f"Release installer not found: {_terminal_literal(str(installer))}"
        )
    argv = (
        sys.executable,
        "-I",
        str(installer),
        "--version",
        request.requested_version,
        "--repository",
        request.repository,
        "--install-root",
        str(install_root),
        "--bin-dir",
        str(bin_dir),
        *(("--dry-run",) if request.dry_run else ()),
    )
    _line(events, "")
    _line(events, "Update Agent Rails release")
    result = _run_command(
        dependencies,
        UpdateAction.RELEASE_INSTALL,
        argv,
        working_directory,
        environment,
        steps,
        events,
    )
    if result.exit_code:
        return result.exit_code, UpdateAction.RELEASE_INSTALL, False
    if request.dry_run:
        return None

    new_home = install_root / "current"
    physical_new_home = _physical_path(new_home)
    new_helper = physical_new_home / "scripts/agent-python-cli.py"
    if (
        environment.get("AGENT_RAILS_UPDATE_REEXEC", "0") != "1"
        and new_helper.is_file()
        and os.access(new_helper, os.R_OK)
        and physical_new_home != _physical_path(kit_home)
    ):
        version = _read_first_field(new_home / "VERSION") or "unknown"
        _line(events, f"Continue with Agent Rails {_terminal_literal(version)}")
        reexec_environment = dict(environment)
        reexec_environment.update(
            {
                "AGENT_RAILS_UPDATE_REEXEC": "1",
                "AGENT_RAILS_HOME": str(physical_new_home),
            }
        )
        reexec = UpdateReexecRequest(
            argv=(
                sys.executable,
                "-E",
                str(new_helper),
                "public",
                "update",
                *request.original_arguments,
                "--skip-pull",
            ),
            environment=reexec_environment,
        )
        try:
            dependencies.reexec(reexec)
        except OSError as exc:
            raise UpdateApplicationError(
                "Unable to continue with the installed Agent Rails release.",
                steps=tuple(steps),
                events=tuple(events),
            ) from exc
        return 0, None, True
    return None


def _run_source_tests(
    request: UpdateRequest,
    *,
    source: UpdateSource,
    dependencies: UpdateDependencies,
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Optional[int]:
    if request.skip_tests:
        _line(events, "")
        _line(events, "Skip tests (--skip-tests).")
        return None
    if source is UpdateSource.RELEASE:
        _line(events, "")
        _line(events, "Skip source test suite for verified Release installation.")
        return None
    argv = ("bash", str(kit_home / "tests/run.sh"))
    _line(events, "")
    _line(events, "Run Agent Rails tests")
    if request.dry_run:
        _line(events, f"Would run: {_render_command(argv)}")
        return None
    result = _run_command(
        dependencies,
        UpdateAction.SOURCE_TESTS,
        argv,
        working_directory,
        environment,
        steps,
        events,
    )
    return result.exit_code or None


def _refresh_project(
    request: UpdateRequest,
    *,
    context: Optional[TargetProjectContext],
    dependencies: UpdateDependencies,
    kit_home: Path,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Optional[tuple[int, UpdateAction]]:
    if context is None:
        assert request.skip_doctor and request.skip_adapter
        _line(events, "")
        _line(events, "Skip pre-upgrade doctor (--skip-doctor).")
        _line(events, "")
        _line(events, "Skip adapter upgrade (--skip-adapter).")
        return None
    doctor, adapter = _project_commands(request, kit_home, context)
    if request.skip_doctor:
        _line(events, "")
        _line(events, "Skip pre-upgrade doctor (--skip-doctor).")
    else:
        failed = _run_project_step(
            request,
            dependencies,
            UpdateAction.PRE_DOCTOR,
            "Run pre-upgrade doctor",
            doctor,
            working_directory,
            environment,
            steps,
            events,
        )
        if failed:
            return failed, UpdateAction.PRE_DOCTOR

    if request.skip_adapter:
        _line(events, "")
        _line(events, "Skip adapter upgrade (--skip-adapter).")
    else:
        failed = _run_project_step(
            request,
            dependencies,
            UpdateAction.ADAPTER_INSTALL,
            "Refresh target adapter and skills",
            adapter,
            working_directory,
            environment,
            steps,
            events,
        )
        if failed:
            return failed, UpdateAction.ADAPTER_INSTALL

    if not request.skip_doctor:
        failed = _run_project_step(
            request,
            dependencies,
            UpdateAction.FINAL_DOCTOR,
            "Run final doctor",
            doctor,
            working_directory,
            environment,
            steps,
            events,
        )
        if failed:
            return failed, UpdateAction.FINAL_DOCTOR
    return None


def _run_project_step(
    request: UpdateRequest,
    dependencies: UpdateDependencies,
    action: UpdateAction,
    title: str,
    command: _ProjectCommand,
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
) -> Optional[int]:
    _line(events, "")
    _line(events, title)
    if request.dry_run:
        _line(events, f"Would run: {_render_command(command.display_argv)}")
        return None
    result = _run_command(
        dependencies,
        action,
        command.argv,
        working_directory,
        environment,
        steps,
        events,
    )
    return result.exit_code or None


def _project_commands(
    request: UpdateRequest,
    kit_home: Path,
    context: TargetProjectContext,
) -> tuple[_ProjectCommand, _ProjectCommand]:
    assert request.tool is not None
    python_helper = (
        sys.executable,
        "-E",
        str(kit_home / "scripts/agent-python-cli.py"),
        "public",
    )
    cli = str(kit_home / "bin/agent-rails")
    project = str(context.root)
    profile = str(context.profile_path)
    if request.tool is UpdateTool.CLAUDE:
        doctor_public = (
            "doctor",
            "--project",
            project,
            "--profile",
            profile,
        )
        adapter_public = (
            "claude",
            "install",
            "--project",
            project,
            "--profile",
            profile,
            "--mode",
            request.install_mode.value,
            *(("--session-hook",) if request.session_hook else ()),
            *(("--global-reminder",) if request.global_reminder else ()),
        )
    elif request.tool is UpdateTool.CODEX:
        doctor_public = (
            "codex",
            "doctor",
            "--project",
            project,
        )
        adapter_public = (
            "codex",
            "install",
            "--project",
            project,
            "--profile",
            profile,
            "--fix-project",
            "--mode",
            request.install_mode.value,
        )
    else:
        doctor_public = (
            "opencode",
            "doctor",
            "--project",
            project,
        )
        adapter_public = (
            "opencode",
            "install",
            "--project",
            project,
            "--profile",
            profile,
            "--mode",
            request.install_mode.value,
        )
    return (
        _ProjectCommand(
            argv=(*python_helper, *doctor_public),
            display_argv=(cli, *doctor_public),
        ),
        _ProjectCommand(
            argv=(*python_helper, *adapter_public),
            display_argv=(cli, *adapter_public),
        ),
    )


def _run_command(
    dependencies: UpdateDependencies,
    action: UpdateAction,
    argv: Sequence[str],
    working_directory: Path,
    environment: Mapping[str, str],
    steps: list[UpdateStep],
    events: list[UpdateEvent],
    *,
    include_output: bool = True,
) -> UpdateCommandResult:
    command = UpdateCommand(
        action=action,
        argv=tuple(argv),
        working_directory=working_directory,
        environment=dict(environment),
        emit=events.append if include_output else None,
    )
    try:
        result = dependencies.runner(command)
    except Exception as exc:
        raise UpdateApplicationError(
            f"Unable to run update step: {action.value}.",
            failed_action=action,
            steps=tuple(steps),
            events=tuple(events),
        ) from exc
    if not isinstance(result, UpdateCommandResult):
        raise UpdateApplicationError(
            f"Update runner returned an invalid result for {action.value}.",
            failed_action=action,
            steps=tuple(steps),
            events=tuple(events),
        )
    steps.append(UpdateStep(action, command.argv, result.exit_code))
    if include_output and not result.output_emitted:
        if result.stdout:
            _chunk(events, UpdateEventStream.STDOUT, result.stdout)
        if result.stderr:
            _chunk(events, UpdateEventStream.STDERR, result.stderr)
    return result


def _validate_request(request: UpdateRequest) -> None:
    if not isinstance(request, UpdateRequest):
        raise UpdateInputError("Update request is invalid.")
    if not isinstance(request.mode, UpdateMode):
        raise UpdateInputError("Update mode is invalid.")
    if not isinstance(request.kit_home, Path):
        raise UpdateInputError("Agent Rails home must be a Path.")
    if request.requested_project is not None and not isinstance(
        request.requested_project, Path
    ):
        raise UpdateInputError("Update project must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise UpdateInputError("Update Profile must be text.")
    if request.tool is not None and not isinstance(request.tool, UpdateTool):
        raise UpdateInputError("Update tool is invalid.")
    if not isinstance(request.install_mode, UpdateInstallMode):
        raise UpdateInputError("Adapter install mode is invalid.")
    for name in (
        "session_hook",
        "global_reminder",
        "skip_pull",
        "skip_tests",
        "skip_doctor",
        "skip_adapter",
        "dry_run",
    ):
        if type(getattr(request, name)) is not bool:
            raise UpdateInputError(f"Update {name} flag must be boolean.")
    if not isinstance(request.requested_version, str) or not request.requested_version:
        raise UpdateInputError("Update version is invalid.")
    if not isinstance(request.repository, str) or not request.repository:
        raise UpdateInputError("Release repository is invalid.")
    if not isinstance(request.install_root, Path) or not isinstance(
        request.bin_dir, Path
    ):
        raise UpdateInputError("Release install paths must be Path values.")
    if not isinstance(request.original_arguments, tuple) or not all(
        isinstance(argument, str) for argument in request.original_arguments
    ):
        raise UpdateInputError("Original update arguments are invalid.")
    if not isinstance(request.working_directory, Path):
        raise UpdateInputError("Update working directory must be a Path.")
    if not isinstance(request.environment, Mapping) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise UpdateInputError("Update environment must contain text values.")
    if request.mode is UpdateMode.PROJECT:
        if request.tool is None or request.requested_project is None:
            raise UpdateInputError(
                "Project update requires an explicit project and tool."
            )
    elif request.tool is not None:
        raise UpdateInputError("--tool is not supported by agent-rails upgrade self.")
    if request.tool is not UpdateTool.CLAUDE and (
        request.session_hook or request.global_reminder
    ):
        raise UpdateInputError(
            "--session-hook and --global-reminder are only supported with "
            "--tool claude."
        )


def _result(
    request: UpdateRequest,
    source: UpdateSource,
    context: Optional[TargetProjectContext],
    steps: Sequence[UpdateStep],
    events: Sequence[UpdateEvent],
    *,
    exit_code: int = 0,
    failed_action: Optional[UpdateAction] = None,
    reexec_requested: bool = False,
) -> UpdateResult:
    return UpdateResult(
        mode=request.mode,
        source=source,
        project_root=None if context is None else context.root,
        profile_path=None if context is None else str(context.profile_path),
        steps=tuple(steps),
        exit_code=exit_code,
        failed_action=failed_action,
        events=tuple(events),
        reexec_requested=reexec_requested,
    )


def _canonical_directory(path: Path, label: str) -> Path:
    resolved = _physical_path(path)
    if not resolved.is_dir():
        raise UpdateInputError(
            f"{label} not found: {_terminal_literal(str(path))}"
        )
    return resolved


def _physical_path(path: Path) -> Path:
    return Path(os.path.realpath(os.fspath(path)))


def _anchored_path(path: Path, working_directory: Path) -> Path:
    return path if path.is_absolute() else working_directory / path


def _read_first_field(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""
    for line in text.splitlines():
        fields = line.split()
        if fields:
            return fields[0]
    return ""


def _read_first_line(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeError):
        return ""
    lines = text.splitlines()
    return lines[0] if lines else ""


def _render_command(argv: Sequence[str]) -> str:
    return shlex.join(tuple(_terminal_literal(argument) for argument in argv))


def _line(events: list[UpdateEvent], value: str) -> None:
    events.append(UpdateEvent(UpdateEventStream.STDOUT, f"{value}\n"))


def _chunk(
    events: list[UpdateEvent], stream: UpdateEventStream, value: str
) -> None:
    events.append(UpdateEvent(stream, _terminal_stream_text(value)))


def _render_events(
    events: Sequence[UpdateEvent], stream: UpdateEventStream
) -> str:
    return "".join(event.text for event in events if event.stream is stream)


def _terminal_literal(value: str) -> str:
    return _terminal_text(value, preserve_newline=False)


def _terminal_stream_text(value: str) -> str:
    return _terminal_text(value, preserve_newline=True)


def _terminal_text(value: str, *, preserve_newline: bool) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == "\n" and preserve_newline:
            escaped.append(character)
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif (
            category in {"Cc", "Cf", "Zl", "Zp"}
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)
