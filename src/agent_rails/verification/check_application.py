from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Optional, TextIO, Tuple

from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectContextMismatch,
    resolve_target_project,
    validate_target_project_context,
)
from agent_rails.core.process import (
    ChildProcessStreamError,
    stop_process_group as _stop_child_process,
    stream_process_output,
)
from agent_rails.core.terminal import terminal_stream_text as _terminal_stream_text
from agent_rails.git._runner import isolated_git_environment
from agent_rails.git.scope import (
    GitScope,
    collect_git_scope_snapshot,
    collect_worktree_snapshot,
    fingerprint_git_worktree,
    hidden_worktree_index_paths,
    resolve_git_head,
    resolve_git_scope,
)

from .plan import (
    VerificationCommands,
    VerificationPlan,
    VerificationPlanRequest,
    build_verification_plan,
    render_suggestions,
)


CHECK_PROFILE_VARIABLES = (
    "BASE_REF",
    "VERIFY_CONTRACTS",
    "VERIFY_BACKEND",
    "VERIFY_RUNTIME",
    "VERIFY_FRONTEND",
    "VERIFY_NODE",
    "VERIFY_PYTHON",
    "VERIFY_JAVA",
    "VERIFY_GO",
    "VERIFY_RUST",
    "VERIFY_DOLPHIN",
    "VERIFY_SHELL",
    "VERIFY_TESTS",
    "VERIFY_PROJECT",
)

_CHILD_READ_BYTES = 65_536


class CheckApplicationError(RuntimeError):
    """The Agent Check request could not be prepared or executed safely."""


class CheckInputError(CheckApplicationError):
    """A public Check input or changing target scope is invalid."""


class CheckMode(str, Enum):
    PREVIEW = "preview"
    RUN = "run"
    SUGGESTIONS_ONLY = "suggestions-only"


@dataclass(frozen=True)
class CheckCliOverrides:
    base_ref: Optional[str] = None
    target_ref: str = "HEAD"
    target_ref_explicit: bool = False
    mode: CheckMode = CheckMode.PREVIEW


@dataclass(frozen=True)
class CheckApplicationRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    overrides: CheckCliOverrides
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PreparedCheck:
    project_root: Path
    profile_path: str
    is_git_repo: bool
    requested_target_ref: str
    target_ref_explicit: bool
    target_sha: Optional[str]
    head_sha: Optional[str]
    resolved_base_ref: str
    merge_base: str
    changed_paths: Tuple[str, ...]
    commands: VerificationCommands
    plan: VerificationPlan
    scope: Optional[GitScope]
    mode: CheckMode
    suppress_marker: bool
    runner_shell: str
    environment: Mapping[str, str]
    worktree_fingerprint: Optional[str]


@dataclass(frozen=True)
class CheckExecutionResult:
    exit_code: int
    completed_steps: int


def prepare_check(
    request: CheckApplicationRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> PreparedCheck:
    """Resolve Profile, Git scope, and Verification Plan exactly once in memory."""

    if request.overrides.mode is CheckMode.SUGGESTIONS_ONLY:
        mode = CheckMode.SUGGESTIONS_ONLY
    elif request.overrides.mode is CheckMode.RUN:
        mode = CheckMode.RUN
    else:
        mode = CheckMode.PREVIEW
    environment = dict(request.environment)
    if context is None:
        context = resolve_target_project(
            request.requested_project,
            kit_home=request.kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            require_profile=True,
            load_profile=True,
            load_environment_file=False,
            profile_variables=CHECK_PROFILE_VARIABLES,
            capture_profile_environment=True,
        )
    else:
        _validate_pre_resolved_context(
            context=context,
            request=request,
            environment=environment,
        )
    profile = context.profile_values
    execution_environment = dict(context.profile_environment or environment)
    commands = verification_commands_from_profile(profile)
    requested_base = request.overrides.base_ref or profile.get("BASE_REF", "")
    scope: Optional[GitScope] = None
    target_sha: Optional[str] = None
    head_sha: Optional[str] = None
    worktree_fingerprint: Optional[str] = None

    if context.is_git_repo:
        scope = resolve_git_scope(
            context.root,
            target_ref=request.overrides.target_ref,
            base_ref=requested_base,
            base_policy="project",
            environment=environment,
        )
        target_sha = scope.target_sha
        head_sha = scope.head_sha
        if (
            mode is CheckMode.RUN
            and request.overrides.target_ref_explicit
            and target_sha != head_sha
        ):
            raise _target_guard_error(
                request.overrides.target_ref, head_sha
            )
        if mode is CheckMode.RUN:
            current_worktree = collect_worktree_snapshot(
                context.root, environment=environment
            )
            if (
                request.overrides.target_ref_explicit
                and current_worktree.changed_paths
            ):
                raise CheckInputError(
                    "Cannot --run checks for an explicit target ref while the "
                    "checkout has staged, unstaged, or untracked changes. Use "
                    "--print-only or clean the worktree first."
                )
            worktree_fingerprint = fingerprint_git_worktree(
                context.root, environment=environment
            )
            _assert_no_hidden_worktree_index(context.root, environment)
            if request.overrides.target_ref_explicit:
                current_worktree = collect_worktree_snapshot(
                    context.root, environment=environment
                )
                if current_worktree.changed_paths:
                    raise CheckInputError(
                        "Cannot --run checks for an explicit target ref while the "
                        "checkout has staged, unstaged, or untracked changes. Use "
                        "--print-only or clean the worktree first."
                    )
        snapshot = collect_git_scope_snapshot(
            context.root,
            scope,
            include_worktree=not request.overrides.target_ref_explicit,
            environment=environment,
        )
        changed_paths = snapshot.changed_paths
        resolved_base_ref = scope.base_ref
        merge_base = scope.merge_base
        plan_target_ref = scope.target_sha
    else:
        if request.overrides.target_ref_explicit:
            raise CheckInputError(
                "Target ref requires a git repository: "
                f"{_terminal_literal(request.overrides.target_ref)}"
            )
        changed_paths = ()
        resolved_base_ref = requested_base
        merge_base = "n/a"
        plan_target_ref = request.overrides.target_ref

    plan = build_verification_plan(
        VerificationPlanRequest(
            project=context.root,
            changed_paths=changed_paths,
            commands=commands,
            target_ref=plan_target_ref,
            target_ref_explicit=request.overrides.target_ref_explicit,
        )
    )
    return PreparedCheck(
        project_root=context.root,
        profile_path=context.profile_path,
        is_git_repo=context.is_git_repo,
        requested_target_ref=request.overrides.target_ref,
        target_ref_explicit=request.overrides.target_ref_explicit,
        target_sha=target_sha,
        head_sha=head_sha,
        resolved_base_ref=resolved_base_ref,
        merge_base=merge_base,
        changed_paths=changed_paths,
        commands=commands,
        plan=plan,
        scope=scope,
        mode=mode,
        suppress_marker=(
            execution_environment.get("AGENT_RAILS_SUPPRESS_MARKER") == "1"
        ),
        runner_shell=execution_environment.get("AGENT_RAILS_RUN_SHELL") or "bash",
        environment=execution_environment,
        worktree_fingerprint=worktree_fingerprint,
    )


def _validate_pre_resolved_context(
    *,
    context: TargetProjectContext,
    request: CheckApplicationRequest,
    environment: Mapping[str, str],
) -> None:
    """Reject a context resolved for a different project, Profile, or kit."""

    try:
        validate_target_project_context(
            context,
            requested_project=request.requested_project,
            kit_home=request.kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            match_git_identity=True,
        )
    except TargetProjectContextMismatch as exc:
        raise CheckInputError(exc.message("Check")) from exc
    except TargetProjectError as exc:
        raise CheckInputError(str(exc)) from exc


def render_check_report(prepared: PreparedCheck) -> str:
    """Render suggestions-only or the stable public Agent Check report."""

    if prepared.mode is CheckMode.SUGGESTIONS_ONLY:
        return render_suggestions(prepared.plan)

    output = ""
    if not prepared.suppress_marker:
        output += (
            "AGENT RAILS: CHECK-ONLY (reason=verification, project="
            f"{_terminal_literal(prepared.project_root.name)})\n\n"
        )
    base_ref = prepared.resolved_base_ref or "none"
    output += "Agent check\n"
    output += f"Base ref: {_terminal_literal(base_ref)}\n"
    output += (
        "Target ref: "
        f"{_terminal_literal(prepared.requested_target_ref)}\n"
    )
    output += f"Merge base: {_terminal_literal(prepared.merge_base[:12])}\n"
    if not prepared.is_git_repo:
        output += (
            "Mode: no git repository detected; diff-based checks are unavailable.\n"
        )
    if prepared.target_ref_explicit:
        output += (
            "Mode: target ref only; current working tree changes are not included.\n"
        )
    output += "\nChanged files:\n"
    if prepared.changed_paths:
        output += "".join(
            f"- {_terminal_literal(path)}\n" for path in prepared.changed_paths
        )
    else:
        output += "- None detected.\n"
    output += "\nSuggested verification:\n"
    output += render_suggestions(prepared.plan)
    output += "\nNext action suggestions:\n"
    output += (
        "- Fix: run the suggested command for any touched executable component "
        "before merge.\n"
    )
    output += (
        "- Do not fix: skip heavy component CI only when the diff is docs-only "
        "or explicitly out of scope.\n"
    )
    output += (
        "- Later: add missing AGENTS.md or provider config when context gaps repeat.\n"
    )
    return output


def execute_check(
    prepared: PreparedCheck,
    *,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> CheckExecutionResult:
    """Run opaque Profile commands through one explicit child-shell argv."""

    if prepared.mode is not CheckMode.RUN:
        return CheckExecutionResult(exit_code=0, completed_steps=0)
    capture_stdout = stdout is not None
    capture_stderr = stderr is not None
    out = sys.stdout if stdout is None else stdout
    err = sys.stderr if stderr is None else stderr
    _validate_execution_snapshot(prepared)
    if not prepared.plan.steps:
        return CheckExecutionResult(exit_code=0, completed_steps=0)
    child_environment = isolated_git_environment(prepared.environment)
    child_environment["PWD"] = str(prepared.project_root)
    _write_output(out, "\nRunning suggested commands...\n", flush=True)
    completed_steps = 0
    for step in prepared.plan.steps:
        _assert_current_head(prepared)
        _write_output(
            out,
            f"\n>>> {step.reason}\n{step.command}\n",
            flush=True,
        )
        try:
            exit_code = _run_check_child(
                prepared,
                step.command,
                child_environment,
                stdout=out if capture_stdout else None,
                stderr=err if capture_stderr else None,
            )
        except FileNotFoundError:
            _write_output(
                err,
                "Runner shell not found: "
                f"{_terminal_literal(prepared.runner_shell)}\n",
                flush=True,
            )
            return CheckExecutionResult(exit_code=127, completed_steps=completed_steps)
        except (PermissionError, OSError, ValueError) as exc:
            _write_output(
                err,
                "Runner shell could not be executed: "
                f"{_terminal_literal(prepared.runner_shell)}: "
                f"{_terminal_literal(str(exc))}\n",
                flush=True,
            )
            return CheckExecutionResult(exit_code=126, completed_steps=completed_steps)
        if exit_code < 0:
            exit_code = 128 - exit_code
        if exit_code != 0:
            return CheckExecutionResult(
                exit_code=exit_code, completed_steps=completed_steps
            )
        _validate_execution_snapshot(prepared)
        completed_steps += 1
    return CheckExecutionResult(exit_code=0, completed_steps=completed_steps)


def _run_check_child(
    prepared: PreparedCheck,
    command: str,
    environment: Mapping[str, str],
    *,
    stdout: Optional[TextIO],
    stderr: Optional[TextIO],
) -> int:
    """Run one command, streaming only the explicitly requested child pipes."""

    arguments = [prepared.runner_shell, "-lc", command]
    if stdout is None and stderr is None:
        return subprocess.run(
            arguments,
            cwd=prepared.project_root,
            env=environment,
            check=False,
        ).returncode

    process = subprocess.Popen(
        arguments,
        cwd=prepared.project_root,
        env=environment,
        stdout=subprocess.PIPE if stdout is not None else None,
        stderr=subprocess.PIPE if stderr is not None else None,
        bufsize=0,
        start_new_session=True,
    )
    try:
        return _stream_child_output(process, stdout=stdout, stderr=stderr)
    except CheckApplicationError:
        _stop_child_process(process)
        _close_child_pipes(process)
        raise
    except BaseException as exc:
        _stop_child_process(process)
        _close_child_pipes(process)
        if not isinstance(exc, Exception):
            raise
        raise CheckApplicationError(
            "Unable to stream verification command output."
        ) from exc


def _stream_child_output(
    process: subprocess.Popen[bytes],
    *,
    stdout: Optional[TextIO],
    stderr: Optional[TextIO],
) -> int:
    """Drain stdout and stderr concurrently without retaining child output."""

    try:
        return stream_process_output(
            process,
            stdout_sink=(
                None
                if stdout is None
                else lambda text: _write_output(
                    stdout,
                    _terminal_stream_text(text),
                    flush=True,
                )
            ),
            stderr_sink=(
                None
                if stderr is None
                else lambda text: _write_output(
                    stderr,
                    _terminal_stream_text(text),
                    flush=True,
                )
            ),
            chunk_bytes=_CHILD_READ_BYTES,
        )
    except ChildProcessStreamError as exc:
        raise CheckApplicationError(
            "Verification command output stream is unavailable."
        ) from exc


def _write_output(writer: TextIO, text: str, *, flush: bool) -> None:
    if not text and not flush:
        return
    try:
        if text:
            written = writer.write(text)
            if written != len(text):
                raise OSError("short write")
        if flush:
            writer.flush()
    except CheckApplicationError:
        raise
    except Exception as exc:
        raise CheckApplicationError(
            "Unable to write verification command output."
        ) from exc


def _close_child_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is None or stream.closed:
            continue
        try:
            stream.close()
        except OSError:
            pass


def _validate_execution_snapshot(prepared: PreparedCheck) -> None:
    if not prepared.is_git_repo or prepared.scope is None:
        return
    _assert_current_head(prepared)
    current_fingerprint = fingerprint_git_worktree(
        prepared.project_root, environment=prepared.environment
    )
    _assert_no_hidden_worktree_index(
        prepared.project_root, prepared.environment
    )
    if prepared.target_ref_explicit:
        current_worktree = collect_worktree_snapshot(
            prepared.project_root, environment=prepared.environment
        )
        if current_worktree.changed_paths:
            raise CheckInputError(
                "Cannot --run checks for an explicit target ref while the "
                "checkout has staged, unstaged, or untracked changes. Use "
                "--print-only or clean the worktree first."
            )
    if (
        prepared.worktree_fingerprint is not None
        and current_fingerprint != prepared.worktree_fingerprint
    ):
        raise CheckInputError(
            "Cannot --run checks because the working tree content moved after "
            "planning. Run check again."
        )
    if prepared.target_ref_explicit:
        return
    snapshot = collect_git_scope_snapshot(
        prepared.project_root,
        prepared.scope,
        include_worktree=True,
        environment=prepared.environment,
    )
    current_plan = build_verification_plan(
        VerificationPlanRequest(
            project=prepared.project_root,
            changed_paths=snapshot.changed_paths,
            commands=prepared.commands,
            target_ref=prepared.scope.target_sha,
            target_ref_explicit=False,
        )
    )
    if snapshot.changed_paths != prepared.changed_paths or current_plan != prepared.plan:
        raise CheckInputError(
            "Cannot --run checks because the changed file scope moved after "
            "planning. Run check again."
        )


def _assert_current_head(prepared: PreparedCheck) -> None:
    if not prepared.is_git_repo or prepared.target_sha is None:
        return
    current_head = resolve_git_head(
        prepared.project_root, environment=prepared.environment
    )
    if current_head != prepared.target_sha:
        raise _target_guard_error(prepared.requested_target_ref, current_head)


def _assert_no_hidden_worktree_index(
    project: Path, environment: Mapping[str, str]
) -> None:
    hidden_paths = hidden_worktree_index_paths(
        project, environment=environment
    )
    if not hidden_paths:
        return
    first_path = _terminal_literal(hidden_paths[0])
    suffix = "" if len(hidden_paths) == 1 else f" (+{len(hidden_paths) - 1} more)"
    raise CheckInputError(
        "Cannot --run checks while Git index hides worktree changes with "
        f"assume-unchanged or skip-worktree: {first_path}{suffix}. Clear the "
        "index flags or use --print-only."
    )


def _target_guard_error(target_ref: str, head_sha: str) -> CheckInputError:
    return CheckInputError(
        "Cannot --run checks for target ref "
        f"{_terminal_literal(target_ref)} while checkout is at HEAD "
        f"{_terminal_literal(head_sha[:12])}. Use --print-only or check out "
        "the target first."
    )


def verification_commands_from_profile(
    values: Mapping[str, str],
) -> VerificationCommands:
    """Project the allowlisted Profile values onto the Plan Interface."""

    return VerificationCommands(
        contracts=values.get("VERIFY_CONTRACTS", ""),
        backend=values.get("VERIFY_BACKEND", ""),
        runtime=values.get("VERIFY_RUNTIME", ""),
        frontend=values.get("VERIFY_FRONTEND", ""),
        node=values.get("VERIFY_NODE", ""),
        python=values.get("VERIFY_PYTHON", ""),
        java=values.get("VERIFY_JAVA", ""),
        go=values.get("VERIFY_GO", ""),
        rust=values.get("VERIFY_RUST", ""),
        dolphin=values.get("VERIFY_DOLPHIN", ""),
        shell=values.get("VERIFY_SHELL", ""),
        tests=values.get("VERIFY_TESTS", ""),
        project=values.get("VERIFY_PROJECT", ""),
    )


def _terminal_literal(value: str) -> str:
    escaped = []
    for character in value:
        codepoint = ord(character)
        if character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif codepoint == 27:
            escaped.append("\\x1b")
        elif codepoint < 32 or codepoint == 127:
            escaped.append(f"\\x{codepoint:02x}")
        elif 0xD800 <= codepoint <= 0xDFFF:
            escaped.append(f"\\u{codepoint:04x}")
        else:
            escaped.append(character)
    return "".join(escaped)
