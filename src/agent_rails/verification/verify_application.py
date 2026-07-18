"""Compose delivery checks and optional publish readiness in one process."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping, Optional, Sequence, TextIO, Tuple

from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_target_project,
)
from agent_rails.core.terminal import (
    render_chunk_events as _render_events,
    terminal_literal as _terminal_literal,
    terminal_stream_text as _terminal_text,
)
from agent_rails.core.paths import AgentRailsPaths
from agent_rails.git.scope import (
    GitScopeError,
    fingerprint_git_worktree,
    resolve_git_head,
)
from agent_rails.memory.candidate import (
    MemoryCandidateError,
    MemoryCandidateRequest,
    MemoryCandidateResult,
    publish_memory_candidate,
)

from .check_application import (
    CHECK_PROFILE_VARIABLES,
    CheckApplicationError,
    CheckApplicationRequest,
    CheckCliOverrides,
    CheckExecutionResult,
    CheckInputError,
    CheckMode,
    PreparedCheck,
    execute_check,
    prepare_check,
    render_check_report,
)
from .plan import VerificationPlanError
from .failure_protocol import (
    FailureHistory,
    clear_failure_history,
    failure_history_path,
    observe_failure,
    read_failure_history,
)
from .repair_pack import RepairPackRequest, render_repair_pack
from .publish_check import (
    PublishCheckCliOverrides,
    PublishCheckError,
    PublishCheckEventStream,
    PublishCheckInputError,
    PublishCheckRequest,
    PublishCheckResult,
    PreparedPublishCheck,
    run_publish_check,
)


class VerifyMode(str, Enum):
    DELIVERY = "delivery"
    PUBLISH = "publish"


class VerifyEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class VerifyRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    mode: VerifyMode
    print_only: bool
    base_ref: Optional[str]
    target_ref: Optional[str]
    no_secret_scan: bool
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class VerifyEvent:
    stream: VerifyEventStream
    text: str


class VerifyApplicationError(RuntimeError):
    """Verify stopped before completing the requested journey."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Tuple[VerifyEvent, ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.events = events

    @property
    def stdout(self) -> str:
        return _render_events(self.events, VerifyEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, VerifyEventStream.STDERR)


class VerifyInputError(VerifyApplicationError):
    """The caller supplied an invalid Verify request or target scope."""

    def __init__(
        self, message: str, *, events: Tuple[VerifyEvent, ...] = ()
    ) -> None:
        super().__init__(message, exit_code=2, events=events)


@dataclass(frozen=True)
class VerifyResult:
    project_root: Path
    profile_path: str
    mode: VerifyMode
    print_only: bool
    check_execution: CheckExecutionResult
    publish_result: Optional[PublishCheckResult]
    memory_candidate: Optional[MemoryCandidateResult]
    exit_code: int
    events: Tuple[VerifyEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_events(self.events, VerifyEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, VerifyEventStream.STDERR)


class _EventWriter:
    def __init__(
        self,
        events: list[VerifyEvent],
        stream: VerifyEventStream,
        live: Optional[TextIO],
    ) -> None:
        self._events = events
        self._stream = stream
        self._live = live

    def write(self, text: str) -> int:
        rendered = _terminal_text(str(text))
        if rendered:
            self._events.append(VerifyEvent(self._stream, rendered))
            if self._live is not None:
                self._live.write(rendered)
        return len(text)

    def flush(self) -> None:
        if self._live is not None:
            self._live.flush()


def run_verify(
    request: VerifyRequest,
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> VerifyResult:
    """Resolve one Context, execute Check, then optionally inspect Publish."""

    _validate_request(request)
    working_directory = _absolute_directory(
        _anchored_path(request.working_directory, Path.cwd()),
        "Verify working directory",
    )
    requested_project = _anchored_path(
        request.requested_project, working_directory
    )
    if not requested_project.is_dir():
        # Historical Verify behavior intentionally exposes no path or message.
        raise VerifyApplicationError("", exit_code=1)
    kit_home = _absolute_directory(
        _anchored_path(request.kit_home, working_directory),
        "Agent Rails home",
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
            profile_variables=CHECK_PROFILE_VARIABLES,
            capture_profile_environment=True,
        )
    except FileNotFoundError as exc:
        raise VerifyInputError(f"Profile not found: {exc}") from exc
    except (TargetProjectError, ProfileLoadError) as exc:
        raise VerifyInputError(str(exc)) from exc

    execution_environment = dict(
        context.profile_environment or environment
    )
    history_path = _failure_history_path(
        kit_home, execution_environment, context
    )
    events: list[VerifyEvent] = []
    out = _EventWriter(events, VerifyEventStream.STDOUT, stdout)
    err = _EventWriter(events, VerifyEventStream.STDERR, stderr)
    out.write("Agent Rails Verify\n")
    out.write(f"Project: {_terminal_literal(str(context.root))}\n")
    out.write(f"Mode: {request.mode.value}\n\n")

    target_ref = request.target_ref or "HEAD"
    check_request = CheckApplicationRequest(
        requested_project=context.root,
        kit_home=kit_home,
        explicit_profile=context.profile_path,
        overrides=CheckCliOverrides(
            base_ref=request.base_ref,
            target_ref=target_ref,
            target_ref_explicit=request.target_ref is not None,
            mode=(CheckMode.PREVIEW if request.print_only else CheckMode.RUN),
        ),
        environment=execution_environment,
    )
    try:
        prepared = prepare_check(check_request, context=context)
        out.write(render_check_report(prepared))
        out.flush()
        check_execution = execute_check(
            prepared,
            stdout=out,
            stderr=err,
        )
    except (
        TargetProjectError,
        ProfileLoadError,
        GitScopeError,
        VerificationPlanError,
        CheckInputError,
    ) as exc:
        raise VerifyInputError(str(exc), events=tuple(events)) from exc
    except CheckApplicationError as exc:
        raise VerifyApplicationError(
            str(exc),
            exit_code=_error_exit_code(exc),
            events=tuple(events),
        ) from exc
    except (OSError, UnicodeError) as exc:
        raise VerifyApplicationError(str(exc), events=tuple(events)) from exc

    if check_execution.exit_code != 0:
        if check_execution.failure is not None:
            escalation = observe_failure(
                history_path,
                prepared.target_sha or "non-git",
                check_execution.failure,
            )
            out.write(
                render_repair_pack(
                    RepairPackRequest(
                        failure=check_execution.failure,
                        changed_paths=prepared.changed_paths,
                        project=prepared.project_root,
                        target_sha=prepared.target_sha or "",
                        escalation=escalation,
                    )
                )
            )
            out.flush()
        return _result(
            request,
            context,
            check_execution,
            None,
            check_execution.exit_code,
            events,
        )

    prior_failure = None
    if not request.print_only and isinstance(prepared, PreparedCheck):
        prior_failure = read_failure_history(
            history_path, prepared.target_sha or "non-git"
        )
    if not request.print_only:
        clear_failure_history(history_path)

    publish_result: Optional[PublishCheckResult] = None
    if request.mode is VerifyMode.PUBLISH:
        out.write("\nPublish readiness\n")
        out.flush()
        publish_request = PublishCheckRequest(
            requested_project=context.root,
            kit_home=kit_home,
            explicit_profile=context.profile_path,
            overrides=PublishCheckCliOverrides(
                base_ref=request.base_ref,
                base_ref_explicit=request.base_ref is not None,
                target_ref=target_ref,
                target_ref_explicit=request.target_ref is not None,
                scan_secrets=not request.no_secret_scan,
            ),
            environment=execution_environment,
        )
        try:
            publish_result = run_publish_check(
                publish_request, context=context
            )
            _append_child_events(events, publish_result.events, stdout, stderr)
            _assert_same_verified_scope(
                prepared,
                publish_result.prepared,
            )
        except (
            TargetProjectError,
            ProfileLoadError,
            GitScopeError,
            VerificationPlanError,
            CheckInputError,
            PublishCheckInputError,
        ) as exc:
            _append_child_events(events, getattr(exc, "events", ()), stdout, stderr)
            raise VerifyInputError(str(exc), events=tuple(events)) from exc
        except PublishCheckError as exc:
            _append_child_events(events, getattr(exc, "events", ()), stdout, stderr)
            raise VerifyApplicationError(
                str(exc),
                exit_code=_error_exit_code(exc),
                events=tuple(events),
            ) from exc
        except (OSError, UnicodeError) as exc:
            raise VerifyApplicationError(str(exc), events=tuple(events)) from exc
        if publish_result.exit_code != 0:
            return _result(
                request,
                context,
                check_execution,
                publish_result,
                publish_result.exit_code,
                events,
            )
        out.write("\nAgent Rails publish verification complete.\n")
    else:
        out.write("\nAgent Rails verification complete.\n")
    memory_candidate = _publish_verified_memory_candidate(
        request=request,
        kit_home=kit_home,
        environment=execution_environment,
        context=context,
        prepared=prepared,
        check_execution=check_execution,
        prior_failure=prior_failure,
    )
    if memory_candidate is not None:
        out.write(
            "Memory Candidate: "
            f"{_terminal_literal(str(memory_candidate.path))}\n"
        )
        out.write("Curate explicitly; no local memory card was written.\n")
    out.flush()
    err.flush()
    return _result(
        request,
        context,
        check_execution,
        publish_result,
        0,
        events,
        memory_candidate=memory_candidate,
    )


def _failure_history_path(
    kit_home: Path,
    environment: Mapping[str, str],
    context: TargetProjectContext,
) -> Optional[Path]:
    config_home = Path(
        AgentRailsPaths.from_environment(kit_home, environment).config_home
    )
    if not config_home.is_absolute():
        return None
    try:
        return failure_history_path(config_home, context.root)
    except (OSError, UnicodeError, ValueError):
        return None


def _publish_verified_memory_candidate(
    *,
    request: VerifyRequest,
    kit_home: Path,
    environment: Mapping[str, str],
    context: TargetProjectContext,
    prepared: object,
    check_execution: CheckExecutionResult,
    prior_failure: Optional[FailureHistory],
) -> Optional[MemoryCandidateResult]:
    if (
        request.print_only
        or not isinstance(prepared, PreparedCheck)
        or prior_failure is None
        or not prepared.target_sha
        or check_execution.completed_steps < 1
    ):
        return None
    fingerprint = getattr(prior_failure, "fingerprint", "")
    count = getattr(prior_failure, "consecutive_count", 0)
    try:
        config_home = Path(
            AgentRailsPaths.from_environment(kit_home, environment).config_home
        )
        return publish_memory_candidate(
            MemoryCandidateRequest(
                config_home=config_home,
                project_root=context.root,
                project_name=context.project_name,
                target_sha=prepared.target_sha,
                failure_fingerprint=fingerprint,
                failure_count=count,
                changed_paths=prepared.changed_paths,
                verification=prepared.plan,
                completed_steps=check_execution.completed_steps,
            )
        )
    except (MemoryCandidateError, OSError, UnicodeError, ValueError):
        return None


def _validate_request(request: VerifyRequest) -> None:
    if not isinstance(request, VerifyRequest):
        raise VerifyInputError("Invalid Verify request.")
    if not isinstance(request.requested_project, Path):
        raise VerifyInputError("Verify requested project must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise VerifyInputError("Verify kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise VerifyInputError("Verify explicit Profile must be text.")
    if not isinstance(request.mode, VerifyMode):
        raise VerifyInputError("Invalid Verify mode.")
    if not isinstance(request.print_only, bool):
        raise VerifyInputError("Verify print-only policy must be boolean.")
    for name in ("base_ref", "target_ref"):
        value = getattr(request, name)
        if value is not None and not isinstance(value, str):
            raise VerifyInputError(f"Verify {name} must be text.")
        if value == "":
            raise VerifyInputError(f"Verify {name} must not be empty.")
    if not isinstance(request.no_secret_scan, bool):
        raise VerifyInputError("Verify secret-scan policy must be boolean.")
    if request.no_secret_scan and request.mode is not VerifyMode.PUBLISH:
        raise VerifyInputError("--no-secret-scan requires --publish.")
    if not isinstance(request.working_directory, Path):
        raise VerifyInputError("Verify working directory must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise VerifyInputError("Verify environment must be a mapping.")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise VerifyInputError("Verify environment keys and values must be text.")


def _result(
    request: VerifyRequest,
    context: TargetProjectContext,
    check_execution: CheckExecutionResult,
    publish_result: Optional[PublishCheckResult],
    exit_code: int,
    events: list[VerifyEvent],
    memory_candidate: Optional[MemoryCandidateResult] = None,
) -> VerifyResult:
    return VerifyResult(
        project_root=context.root,
        profile_path=context.profile_path,
        mode=request.mode,
        print_only=request.print_only,
        check_execution=check_execution,
        publish_result=publish_result,
        memory_candidate=memory_candidate,
        exit_code=exit_code,
        events=tuple(events),
    )


def _append_child_events(
    events: list[VerifyEvent],
    child_events: Sequence[object],
    live_stdout: Optional[TextIO],
    live_stderr: Optional[TextIO],
) -> None:
    for event in child_events:
        stream_value = getattr(getattr(event, "stream", None), "value", "stdout")
        stream = (
            VerifyEventStream.STDERR
            if stream_value == PublishCheckEventStream.STDERR.value
            else VerifyEventStream.STDOUT
        )
        writer = _EventWriter(
            events,
            stream,
            live_stderr if stream is VerifyEventStream.STDERR else live_stdout,
        )
        writer.write(str(getattr(event, "text", "")))


def _assert_same_verified_scope(
    checked: object,
    published: object,
) -> None:
    """Reject a successful Check/Publish pair that describes different state."""

    # Direct Facade tests use opaque service doubles. Production services always
    # return these typed snapshots, so only real snapshots participate here.
    if not isinstance(checked, PreparedCheck) or not isinstance(
        published, PreparedPublishCheck
    ):
        return
    if not checked.is_git_repo or checked.scope is None:
        return
    publish_paths = (
        published.snapshot.committed_paths
        if checked.target_ref_explicit
        else published.snapshot.changed_paths
    )
    moved = (
        checked.target_sha != published.scope.target_sha
        or checked.changed_paths != publish_paths
        or checked.plan != published.verification_plan
    )
    current_head = resolve_git_head(
        checked.project_root,
        environment=checked.environment,
    )
    if checked.head_sha is not None and current_head != checked.head_sha:
        moved = True
    if checked.worktree_fingerprint is not None:
        current_fingerprint = fingerprint_git_worktree(
            checked.project_root,
            environment=checked.environment,
        )
        if current_fingerprint != checked.worktree_fingerprint:
            moved = True
    if moved:
        raise CheckInputError(
            "Cannot complete verify because the checked target or worktree "
            "moved before publish readiness finished. Run verify again."
        )


def _error_exit_code(error: BaseException) -> int:
    value = getattr(error, "exit_code", 1)
    return value if isinstance(value, int) and value > 0 else 1


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


def _absolute_directory(path: Path, label: str) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if not absolute.is_dir():
        raise VerifyInputError(
            f"{label} not found: {_terminal_literal(str(path))}"
        )
    return absolute
