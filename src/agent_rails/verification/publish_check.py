"""Prepare and render one read-only Agent Rails publish readiness check."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import errno
import os
from pathlib import Path, PurePosixPath
import stat
import subprocess
import tempfile
from typing import BinaryIO, Iterable, Iterator, Mapping, Optional, Sequence, Tuple

from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectContextMismatch,
    TargetProjectError,
    resolve_target_project,
    validate_target_project_context,
)
from agent_rails.core.paths import same_file_metadata
from agent_rails.core.terminal import terminal_literal as _terminal_literal
from agent_rails.git._runner import isolated_git_environment, run_git
from agent_rails.git.scope import (
    GitScope,
    GitScopeError,
    GitScopeSnapshot,
    collect_git_scope_snapshot,
    fingerprint_git_worktree,
    resolve_git_head,
    resolve_git_scope,
)
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    scan_sensitive_records,
)

from .check_application import (
    CHECK_PROFILE_VARIABLES,
    verification_commands_from_profile,
)
from .plan import (
    VerificationPlan,
    VerificationPlanError,
    VerificationPlanRequest,
    build_verification_plan,
)


_UNTRACKED_READ_CHUNK_BYTES = 64 * 1024
_UNTRACKED_MAX_LINE_BYTES = 1024 * 1024
_DIFF_READ_CHUNK_BYTES = 64 * 1024
_DIFF_MAX_LINE_BYTES = 1024 * 1024


class PublishCheckError(RuntimeError):
    """The publish check could not be prepared or rendered safely."""


class PublishCheckInputError(PublishCheckError):
    """A typed request, Git scope, or inspected publish artifact is invalid."""


class PublishCheckEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class PublishCheckCliOverrides:
    base_ref: Optional[str] = None
    base_ref_explicit: bool = False
    target_ref: str = "HEAD"
    target_ref_explicit: bool = False
    scan_secrets: bool = True


@dataclass(frozen=True)
class PublishCheckRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    overrides: PublishCheckCliOverrides
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PublishRepository:
    branch: str
    upstream: str
    ahead: str
    behind: str
    origin_url: str


@dataclass(frozen=True)
class PublishSecretScan:
    enabled: bool
    findings: Tuple[str, ...]


@dataclass(frozen=True)
class _UnstagedChange:
    source_path: str
    index_path: str
    worktree_path: str


@dataclass(frozen=True)
class _IndexEntry:
    mode: str
    object_id: str


@dataclass(frozen=True)
class PreparedPublishCheck:
    project_root: Path
    project_name: str
    profile_path: str
    requested_target_ref: str
    target_ref_explicit: bool
    base_ref_explicit: bool
    scope: GitScope
    snapshot: GitScopeSnapshot
    repository: PublishRepository
    verification_plan: VerificationPlan
    secret_scan: PublishSecretScan
    deployment_delta_unresolved: bool


@dataclass(frozen=True)
class PublishCheckEvent:
    stream: PublishCheckEventStream
    text: str


@dataclass(frozen=True)
class PublishCheckResult:
    prepared: PreparedPublishCheck
    events: Tuple[PublishCheckEvent, ...]
    exit_code: int = 0

    @property
    def stdout(self) -> str:
        return "".join(
            event.text
            for event in self.events
            if event.stream is PublishCheckEventStream.STDOUT
        )

    @property
    def stderr(self) -> str:
        return "".join(
            event.text
            for event in self.events
            if event.stream is PublishCheckEventStream.STDERR
        )


def prepare_publish_check(
    request: PublishCheckRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> PreparedPublishCheck:
    """Freeze Profile, Git scope, worktree scope, plan, and redacted findings."""

    _validate_request(request)
    environment = dict(request.environment)
    if context is None:
        try:
            context = resolve_target_project(
                request.requested_project,
                kit_home=request.kit_home,
                explicit_profile=request.explicit_profile,
                environment=environment,
                require_profile=True,
                load_profile=True,
                load_environment_file=False,
                profile_variables=CHECK_PROFILE_VARIABLES,
            )
        except (TargetProjectError, ProfileLoadError) as exc:
            raise PublishCheckInputError(str(exc)) from exc
        except FileNotFoundError as exc:
            raise PublishCheckInputError(f"Profile not found: {exc}") from exc
    else:
        _validate_pre_resolved_context(
            context=context,
            request=request,
            environment=environment,
        )
    if not context.is_git_repo:
        raise PublishCheckInputError("publish check requires a git repository.")

    values = context.profile_values
    overrides = request.overrides
    requested_base = (
        overrides.base_ref
        if overrides.base_ref is not None
        else values.get("BASE_REF", "")
    )
    try:
        scope = resolve_git_scope(
            context.root,
            target_ref=overrides.target_ref,
            base_ref=requested_base,
            base_policy="publish",
            environment=environment,
        )
    except GitScopeError as exc:
        if str(exc) == "Git command is unavailable.":
            raise PublishCheckError("Git command is unavailable.") from exc
        raise PublishCheckInputError(str(exc)) from exc
    try:
        snapshot = collect_git_scope_snapshot(
            context.root,
            scope,
            include_worktree=True,
            environment=environment,
        )
    except GitScopeError as exc:
        if str(exc) == "Unable to read committed Git scope.":
            raise PublishCheckError(
                "Unable to inspect committed publish diff for sensitive output."
            ) from exc
        raise PublishCheckError(
            "Unable to freeze publish scope for sensitive-output scanning."
        ) from exc

    commands = verification_commands_from_profile(values)
    plan_paths = (
        snapshot.committed_paths
        if overrides.target_ref_explicit
        else snapshot.changed_paths
    )
    try:
        verification_plan = build_verification_plan(
            VerificationPlanRequest(
                project=context.root,
                changed_paths=plan_paths,
                commands=commands,
                target_ref=scope.target_sha,
                target_ref_explicit=overrides.target_ref_explicit,
            )
        )
    except VerificationPlanError as exc:
        raise PublishCheckInputError(str(exc)) from exc

    repository = _collect_repository(context.root, environment)
    if overrides.scan_secrets:
        findings = _scan_publish_scope(context.root, scope, snapshot, environment)
        secret_scan = PublishSecretScan(enabled=True, findings=findings)
    else:
        # This branch deliberately performs no worktree file open. Git metadata and
        # the Verification Plan remain available without touching untracked data.
        secret_scan = PublishSecretScan(enabled=False, findings=())

    unresolved = not overrides.base_ref_explicit and (
        not scope.base_ref or scope.base_sha == scope.target_sha
    )
    return PreparedPublishCheck(
        project_root=context.root,
        project_name=context.default_name,
        profile_path=context.profile_path,
        requested_target_ref=overrides.target_ref,
        target_ref_explicit=overrides.target_ref_explicit,
        base_ref_explicit=overrides.base_ref_explicit,
        scope=scope,
        snapshot=snapshot,
        repository=repository,
        verification_plan=verification_plan,
        secret_scan=secret_scan,
        deployment_delta_unresolved=unresolved,
    )


def render_publish_check_report(prepared: PreparedPublishCheck) -> str:
    """Render the stable, secret-safe publish report from frozen inputs."""

    repository = prepared.repository
    scope = prepared.scope
    lines = [
        "AGENT RAILS: CHECK-ONLY (reason=publish, project="
        f"{_terminal_literal(prepared.project_name)})\n\n",
        "Agent publish check\n",
        f"Project: {_terminal_literal(str(prepared.project_root))}\n",
        f"Profile: {_terminal_literal(prepared.profile_path)}\n",
        f"Branch: {_terminal_literal(repository.branch)}\n",
    ]
    if repository.upstream:
        lines.append(
            "Upstream: "
            f"{_terminal_literal(repository.upstream)} "
            f"(ahead {repository.ahead}, behind {repository.behind})\n"
        )
    else:
        lines.append("Upstream: none\n")
    lines.extend(
        (
            f"Origin: {_terminal_literal(repository.origin_url or 'none')}\n",
            f"Base ref: {_terminal_literal(scope.base_ref or 'none')}\n",
            "Target ref: "
            f"{_terminal_literal(prepared.requested_target_ref)}\n",
            f"Merge base: {_terminal_literal(scope.merge_base[:12])}\n",
        )
    )
    if prepared.deployment_delta_unresolved:
        lines.extend(
            (
                "Deployment delta: UNRESOLVED (implicit base is missing or "
                "already equals target)\n",
                "Deployment baseline action: pass --base "
                "<currently-deployed-source-revision> before claiming release "
                "readiness.\n",
            )
        )
    if prepared.target_ref_explicit:
        lines.append(
            "Mode: target ref only for committed diff; working tree status is "
            "still shown.\n"
        )

    lines.append("\nCommitted change scope:\n")
    if prepared.deployment_delta_unresolved:
        lines.append(
            "- Deployment delta unresolved; the push/upstream baseline is not "
            "proof of the currently deployed revision.\n"
        )
    elif prepared.snapshot.committed_paths:
        lines.extend(
            f"- {_terminal_literal(path)}\n"
            for path in prepared.snapshot.committed_paths
        )
    else:
        lines.append("- None against base.\n")

    lines.append("\nWorking tree scope:\n")
    _append_status_group(lines, "Staged files", prepared.snapshot.staged_paths)
    _append_status_group(lines, "Unstaged files", prepared.snapshot.unstaged_paths)
    _append_status_group(lines, "Untracked files", prepared.snapshot.untracked_paths)

    lines.append("\nSuggested commit scope:\n")
    if not prepared.snapshot.changed_paths:
        lines.append("- No local or branch changes detected.\n")
    else:
        lines.append(
            "- Changed files in publish scope: "
            f"{len(prepared.snapshot.changed_paths)}\n"
        )
        lines.append("- Top paths:\n")
        lines.extend(_render_top_paths(prepared.snapshot.changed_paths))

    lines.append("\nSecret scan:\n")
    if not prepared.secret_scan.enabled:
        lines.append("- Disabled by --no-secret-scan.\n")
    else:
        if prepared.deployment_delta_unresolved:
            lines.append(
                "- INCOMPLETE: committed secret scope has no trusted deployment "
                "baseline until an explicit deployed --base is supplied.\n"
            )
        if prepared.secret_scan.findings:
            lines.append(
                "- Potential secret matches found. Review before commit/push:\n"
            )
            lines.extend(
                f"  - {_terminal_literal(finding)}\n"
                for finding in prepared.secret_scan.findings
            )
        elif not prepared.deployment_delta_unresolved:
            lines.append("- No likely secrets found in changed text files.\n")

    lines.append("\nSuggested verification:\n")
    lines.append(_render_visible_suggestions(prepared.verification_plan))
    lines.append("\nPublish next steps:\n")
    if prepared.deployment_delta_unresolved:
        lines.append(
            "- Resolve the deployed source baseline with --base before treating "
            "this check as release readiness evidence.\n"
        )
    lines.extend(
        (
            "- Review the changed file scope and secret scan warnings.\n",
            "- Stage only intentional files, commit with a scope that matches "
            "this summary, run required checks, then push.\n",
        )
    )
    return "".join(lines)


def run_publish_check(
    request: PublishCheckRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> PublishCheckResult:
    prepared = prepare_publish_check(request, context=context)
    report = render_publish_check_report(prepared)
    return PublishCheckResult(
        prepared=prepared,
        events=(PublishCheckEvent(PublishCheckEventStream.STDOUT, report),),
    )


def _validate_pre_resolved_context(
    *,
    context: TargetProjectContext,
    request: PublishCheckRequest,
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
        raise PublishCheckInputError(exc.message("Publish check")) from exc
    except TargetProjectError as exc:
        raise PublishCheckInputError(str(exc)) from exc


def _validate_request(request: PublishCheckRequest) -> None:
    if not isinstance(request, PublishCheckRequest):
        raise PublishCheckInputError("Invalid publish check request.")
    if not isinstance(request.requested_project, Path):
        raise PublishCheckInputError("Publish check requested project must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise PublishCheckInputError("Publish check kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise PublishCheckInputError("Publish check explicit Profile must be text.")
    if not isinstance(request.overrides, PublishCheckCliOverrides):
        raise PublishCheckInputError("Invalid publish check CLI overrides.")
    overrides = request.overrides
    for name in ("base_ref_explicit", "target_ref_explicit", "scan_secrets"):
        if not isinstance(getattr(overrides, name), bool):
            raise PublishCheckInputError(
                f"Publish check {name} policy must be boolean."
            )
    if overrides.base_ref is not None and not isinstance(overrides.base_ref, str):
        raise PublishCheckInputError("Publish check base ref must be text.")
    if not isinstance(overrides.target_ref, str):
        raise PublishCheckInputError("Publish check target ref must be text.")
    if overrides.base_ref_explicit and not overrides.base_ref:
        raise PublishCheckInputError(
            "Explicit publish check base ref must not be empty."
        )
    if overrides.target_ref_explicit and not overrides.target_ref:
        raise PublishCheckInputError(
            "Explicit publish check target ref must not be empty."
        )
    if not overrides.target_ref:
        raise PublishCheckInputError("Publish check target ref must not be empty.")
    if overrides.base_ref is not None and not overrides.base_ref_explicit:
        raise PublishCheckInputError(
            "Publish check base ref value requires base_ref_explicit."
        )
    if not overrides.target_ref_explicit and overrides.target_ref != "HEAD":
        raise PublishCheckInputError(
            "Publish check non-default target ref requires target_ref_explicit."
        )
    if not isinstance(request.environment, Mapping):
        raise PublishCheckInputError("Publish check environment must be a mapping.")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise PublishCheckInputError(
            "Publish check environment keys and values must be text."
        )


def _collect_repository(
    project: Path, environment: Mapping[str, str]
) -> PublishRepository:
    branch = _git_optional(
        project, ("branch", "--show-current"), environment
    ) or "(detached)"
    upstream = _git_optional(
        project,
        ("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"),
        environment,
    )
    ahead = "n/a"
    behind = "n/a"
    if upstream:
        counts = _git_optional(
            project,
            ("rev-list", "--left-right", "--count", f"{upstream}...HEAD"),
            environment,
        ).split()
        if len(counts) == 2 and all(part.isdigit() for part in counts):
            behind, ahead = counts
    raw_origin = _git_optional(project, ("remote", "get-url", "origin"), environment)
    return PublishRepository(
        branch=branch,
        upstream=upstream,
        ahead=ahead,
        behind=behind,
        origin_url=_sanitize_remote_url(raw_origin),
    )


def _git_optional(
    project: Path, arguments: Tuple[str, ...], environment: Mapping[str, str]
) -> str:
    try:
        completed = run_git(project, arguments, environment=environment)
    except OSError:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _sanitize_remote_url(value: str) -> str:
    if not value:
        return ""
    sanitized = value.split("#", 1)[0].split("?", 1)[0]
    if "://" in sanitized:
        scheme, remainder = sanitized.split("://", 1)
        authority, separator, path = remainder.partition("/")
        if "@" in authority:
            authority = "<redacted>@" + authority.rsplit("@", 1)[1]
        return f"{scheme}://{authority}{separator}{path}"
    if "@" in sanitized:
        return "<redacted>@" + sanitized.rsplit("@", 1)[1]
    return sanitized


def _scan_publish_scope(
    project: Path,
    scope: GitScope,
    snapshot: GitScopeSnapshot,
    environment: Mapping[str, str],
) -> Tuple[str, ...]:
    before_fingerprint = _capture_publish_scan_state(
        project, scope, snapshot, environment
    )
    unstaged_changes = _collect_unstaged_changes(project, snapshot, environment)
    findings: list[str] = []
    if scope.base_ref:
        findings.extend(
            _scan_git_diff(
                project,
                (
                    f"{scope.merge_base}...{scope.target_sha}",
                ),
                "committed publish diff",
                environment,
            )
        )
    findings.extend(
        _scan_git_diff(
            project,
            ("--cached", scope.head_sha),
            "staged publish diff",
            environment,
        )
    )
    if unstaged_changes:
        try:
            with tempfile.TemporaryDirectory(
                prefix="agent-rails-publish-scan-"
            ) as temp_dir:
                temp_root = Path(temp_dir)
                os.chmod(temp_root, 0o700)
                _assert_private_temp_outside_project(project, temp_root)
                findings.extend(
                    _scan_unstaged_changes(
                        project,
                        unstaged_changes,
                        temp_root,
                        environment,
                    )
                )
        except PublishCheckError:
            raise
        except OSError as exc:
            raise PublishCheckError(
                "Unable to inspect unstaged publish diff for sensitive output."
            ) from exc
    for relative_path in snapshot.untracked_paths:
        findings.extend(_scan_untracked_path(project, relative_path))
    after_fingerprint = _capture_publish_scan_state(
        project, scope, snapshot, environment
    )
    if after_fingerprint != before_fingerprint:
        raise PublishCheckError(
            "Publish scope moved while scanning sensitive output. "
            "Run publish check again."
        )
    return tuple(sorted({_terminal_literal(finding) for finding in findings}))


def _capture_publish_scan_state(
    project: Path,
    scope: GitScope,
    expected_snapshot: GitScopeSnapshot,
    environment: Mapping[str, str],
) -> str:
    _assert_worktree_paths_nofollow(project, expected_snapshot.unstaged_paths)
    _assert_untracked_paths_inspectable(project, expected_snapshot.untracked_paths)
    try:
        current_head = resolve_git_head(project, environment=environment)
        current_snapshot = collect_git_scope_snapshot(
            project, scope, include_worktree=True, environment=environment
        )
        fingerprint = fingerprint_git_worktree(project, environment=environment)
        confirmed_snapshot = collect_git_scope_snapshot(
            project, scope, include_worktree=True, environment=environment
        )
    except GitScopeError as exc:
        raise PublishCheckError(
            "Unable to freeze publish scope for sensitive-output scanning."
        ) from exc
    if (
        current_head != scope.head_sha
        or current_snapshot != expected_snapshot
        or confirmed_snapshot != expected_snapshot
    ):
        raise PublishCheckError(
            "Publish scope moved while scanning sensitive output. "
            "Run publish check again."
        )
    return fingerprint


def _assert_worktree_paths_nofollow(
    project: Path, paths: Tuple[str, ...]
) -> None:
    for relative_path in paths:
        try:
            if (
                _read_project_symlink(
                    project, relative_path, missing_ok=True
                )
                is not None
            ):
                continue
            descriptor = _open_untracked_regular(
                project, relative_path, missing_ok=True
            )
            if descriptor is not None:
                os.close(descriptor)
        except PublishCheckError as exc:
            raise PublishCheckError(
                "Unable to inspect unstaged file for sensitive output: "
                f"{_terminal_literal(relative_path)}"
            ) from exc


def _assert_untracked_paths_inspectable(
    project: Path, paths: Tuple[str, ...]
) -> None:
    for relative_path in paths:
        try:
            if (
                _read_project_symlink(
                    project, relative_path, missing_ok=False
                )
                is not None
            ):
                continue
            descriptor = _open_untracked_regular(
                project, relative_path, missing_ok=False
            )
            if descriptor is not None:
                os.close(descriptor)
        except PublishCheckError as exc:
            raise PublishCheckError(
                "Unable to inspect untracked file for sensitive output: "
                f"{_terminal_literal(relative_path)}"
            ) from exc


def _scan_git_diff(
    project: Path,
    scope_arguments: Tuple[str, ...],
    label: str,
    environment: Mapping[str, str],
) -> Tuple[str, ...]:
    probe_arguments = (
        "diff",
        "--quiet",
        "--no-ext-diff",
        "--no-textconv",
        "--text",
        *scope_arguments,
        "--",
    )
    try:
        probe = run_git(project, probe_arguments, environment=environment)
    except OSError as exc:
        raise PublishCheckError(
            f"Unable to inspect {label} for sensitive output."
        ) from exc
    if probe.returncode not in {0, 1}:
        raise PublishCheckError(
            f"Unable to inspect {label} for sensitive output."
        )
    command = (
        "git",
        "-C",
        str(project),
        "--no-pager",
        "diff",
        "--no-ext-diff",
        "--no-color",
        "--no-prefix",
        "--unified=0",
        "--no-textconv",
        "--text",
        *scope_arguments,
        "--",
    )
    git_environment = isolated_git_environment(environment)
    git_environment["GIT_PAGER"] = "cat"
    git_environment["PAGER"] = "cat"
    return _scan_diff_command(
        command,
        cwd=None,
        environment=git_environment,
        label=label,
        allowed_returncodes=(0,),
    )


def _scan_diff_command(
    command: Sequence[str],
    *,
    cwd: Optional[Path],
    environment: Mapping[str, str],
    label: str,
    allowed_returncodes: Tuple[int, ...],
    source_override: Optional[str] = None,
) -> Tuple[str, ...]:
    process: Optional[subprocess.Popen] = None
    stream: Optional[BinaryIO] = None
    try:
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if process.stdout is None:
            raise PublishCheckError(
                f"Unable to inspect {label} for sensitive output."
            )
        stream = process.stdout
        records: Iterable[str] = _bounded_diff_records(stream, label)
        if source_override is not None:
            records = _override_diff_source(records, source_override)
        findings = tuple(
            scan_sensitive_records(
                records,
                source_name="",
                format_name="diff",
            )
        )
        stream.close()
        stream = None
        returncode = process.wait()
        if returncode not in allowed_returncodes:
            raise PublishCheckError(
                f"Unable to inspect {label} for sensitive output."
            )
        return findings
    except PublishCheckError:
        if process is not None:
            _stop_process(process)
        raise
    except SensitiveOutputError as exc:
        if process is not None:
            _stop_process(process)
        raise PublishCheckError(
            f"Unable to inspect {label} for sensitive output."
        ) from exc
    except (OSError, UnicodeError) as exc:
        if process is not None:
            _stop_process(process)
        raise PublishCheckError(
            f"Unable to inspect {label} for sensitive output."
        ) from exc
    finally:
        if stream is not None:
            stream.close()


def _bounded_diff_records(stream: BinaryIO, label: str) -> Iterator[str]:
    pending = bytearray()
    while True:
        chunk = stream.read(_DIFF_READ_CHUNK_BYTES)
        if not chunk:
            break
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            if newline > _DIFF_MAX_LINE_BYTES:
                raise PublishCheckError(
                    f"Unable to inspect {label}: diff line exceeds safe limit."
                )
            record = bytes(pending[:newline])
            del pending[: newline + 1]
            yield record.rstrip(b"\r").decode("utf-8", "surrogateescape")
        if len(pending) > _DIFF_MAX_LINE_BYTES:
            raise PublishCheckError(
                f"Unable to inspect {label}: diff line exceeds safe limit."
            )
    if pending:
        if len(pending) > _DIFF_MAX_LINE_BYTES:
            raise PublishCheckError(
                f"Unable to inspect {label}: diff line exceeds safe limit."
            )
        yield bytes(pending).rstrip(b"\r").decode("utf-8", "surrogateescape")


def _override_diff_source(
    records: Iterable[str], source_name: str
) -> Iterator[str]:
    for record in records:
        if record.startswith("+++ ") and record[4:] != "/dev/null":
            yield f"+++ {source_name}"
        else:
            yield record


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except OSError:
            return
        try:
            process.wait(timeout=1)
        except (OSError, subprocess.TimeoutExpired):
            return


def _collect_unstaged_changes(
    project: Path,
    snapshot: GitScopeSnapshot,
    environment: Mapping[str, str],
) -> Tuple[_UnstagedChange, ...]:
    try:
        completed = run_git(
            project,
            ("status", "--porcelain=v1", "-z", "-uall"),
            environment=environment,
        )
    except OSError as exc:
        raise PublishCheckError(
            "Unable to freeze unstaged publish paths for sensitive-output scanning."
        ) from exc
    if completed.returncode != 0:
        raise PublishCheckError(
            "Unable to freeze unstaged publish paths for sensitive-output scanning."
        )

    fields = completed.stdout.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    changes: list[_UnstagedChange] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4 or record[2] != " ":
            raise PublishCheckError("Git returned an invalid publish status payload.")
        status = record[:2]
        path = record[3:]
        original_path: Optional[str] = None
        if status[0] in "RC" or status[1] in "RC":
            if index >= len(fields):
                raise PublishCheckError(
                    "Git returned an invalid publish rename status payload."
                )
            original_path = fields[index]
            index += 1
        if status in {"??", "!!"} or status[1] == " ":
            continue
        if "U" in status or status in {"AA", "DD"}:
            raise PublishCheckError(
                "Unable to scan an unmerged unstaged publish path."
            )
        if status[1] in "RC":
            if original_path is None:
                raise PublishCheckError(
                    "Git returned an invalid publish rename status payload."
                )
            index_path = original_path
        else:
            index_path = path
        changes.append(
            _UnstagedChange(
                source_path=path,
                index_path=index_path,
                worktree_path=path,
            )
        )

    ordered = tuple(sorted(changes, key=lambda change: change.source_path))
    if tuple(change.source_path for change in ordered) != snapshot.unstaged_paths:
        raise PublishCheckError(
            "Publish scope moved while scanning sensitive output. "
            "Run publish check again."
        )
    return ordered


def _scan_unstaged_changes(
    project: Path,
    changes: Tuple[_UnstagedChange, ...],
    temp_root: Path,
    environment: Mapping[str, str],
) -> Tuple[str, ...]:
    private_home = temp_root / "home"
    private_home.mkdir(mode=0o700)
    no_index_environment = _no_index_environment(environment, private_home)
    findings: list[str] = []
    for number, change in enumerate(changes):
        entry_root = temp_root / f"entry-{number:06d}"
        entry_root.mkdir(mode=0o700)
        findings.extend(
            _scan_unstaged_change(
                project,
                change,
                entry_root,
                environment,
                no_index_environment,
            )
        )
    return tuple(findings)


def _scan_unstaged_change(
    project: Path,
    change: _UnstagedChange,
    entry_root: Path,
    environment: Mapping[str, str],
    no_index_environment: Mapping[str, str],
) -> Tuple[str, ...]:
    before_dir = entry_root / "before"
    after_dir = entry_root / "after"
    before_dir.mkdir(mode=0o700)
    after_dir.mkdir(mode=0o700)
    before_path = before_dir / "content"
    after_path = after_dir / "content"

    entry = _read_index_entry(project, change.index_path, environment)
    if entry is not None and entry.mode == "160000":
        return ()
    if entry is not None:
        if entry.mode not in {"100644", "100755", "120000"}:
            raise PublishCheckError(
                "Unable to inspect unstaged publish diff for sensitive output."
            )
        _copy_git_blob(project, entry.object_id, before_path, environment)
    _snapshot_worktree_path(project, change.worktree_path, after_path)

    command = (
        "git",
        "-c",
        f"core.attributesFile={os.devnull}",
        "--no-pager",
        "diff",
        "--no-index",
        "--no-ext-diff",
        "--no-color",
        "--no-prefix",
        "--unified=0",
        "--no-textconv",
        "--text",
        "--",
        before_dir.name,
        after_dir.name,
    )
    return _scan_diff_command(
        command,
        cwd=entry_root,
        environment=no_index_environment,
        label="unstaged publish diff",
        allowed_returncodes=(0, 1),
        source_override=change.source_path,
    )


def _read_index_entry(
    project: Path,
    relative_path: str,
    environment: Mapping[str, str],
) -> Optional[_IndexEntry]:
    try:
        completed = run_git(
            project,
            (
                "--literal-pathspecs",
                "ls-files",
                "--stage",
                "-z",
                "--",
                relative_path,
            ),
            environment=environment,
        )
    except OSError as exc:
        raise PublishCheckError(
            "Unable to freeze unstaged index content for sensitive-output scanning."
        ) from exc
    if completed.returncode != 0:
        raise PublishCheckError(
            "Unable to freeze unstaged index content for sensitive-output scanning."
        )

    stage_zero: Optional[_IndexEntry] = None
    has_unmerged = False
    for record in (field for field in completed.stdout.split("\0") if field):
        metadata, separator, stored_path = record.partition("\t")
        parts = metadata.split()
        if not separator or len(parts) != 3 or stored_path != relative_path:
            raise PublishCheckError("Git returned an invalid publish index payload.")
        mode, object_id, stage = parts
        if stage == "0":
            if stage_zero is not None:
                raise PublishCheckError(
                    "Git returned a duplicate publish index entry."
                )
            stage_zero = _IndexEntry(mode=mode, object_id=object_id)
        else:
            has_unmerged = True
    if has_unmerged and stage_zero is None:
        raise PublishCheckError("Unable to scan an unmerged publish index path.")
    return stage_zero


def _copy_git_blob(
    project: Path,
    object_id: str,
    destination: Path,
    environment: Mapping[str, str],
) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    process: Optional[subprocess.Popen] = None
    stream: Optional[BinaryIO] = None
    try:
        git_environment = isolated_git_environment(environment)
        git_environment["GIT_PAGER"] = "cat"
        process = subprocess.Popen(
            [
                "git",
                "-C",
                str(project),
                "--no-pager",
                "cat-file",
                "blob",
                object_id,
            ],
            env=git_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        if process.stdout is None:
            raise PublishCheckError(
                "Unable to freeze unstaged index content for sensitive-output scanning."
            )
        stream = process.stdout
        while True:
            chunk = stream.read(_DIFF_READ_CHUNK_BYTES)
            if not chunk:
                break
            _write_all(descriptor, chunk)
        stream.close()
        stream = None
        if process.wait() != 0:
            raise PublishCheckError(
                "Unable to freeze unstaged index content for sensitive-output scanning."
            )
    except PublishCheckError:
        if process is not None:
            _stop_process(process)
        raise
    except OSError as exc:
        if process is not None:
            _stop_process(process)
        raise PublishCheckError(
            "Unable to freeze unstaged index content for sensitive-output scanning."
        ) from exc
    finally:
        if stream is not None:
            stream.close()
        os.close(descriptor)


def _snapshot_worktree_path(
    project: Path, relative_path: str, destination: Path
) -> bool:
    link_target = _read_project_symlink(project, relative_path, missing_ok=True)
    if link_target is not None:
        _write_private_bytes(
            destination, link_target.encode("utf-8", "surrogateescape")
        )
        return True

    descriptor = _open_untracked_regular(
        project, relative_path, missing_ok=True
    )
    if descriptor is None:
        return False
    try:
        opened = os.fstat(descriptor)
        output = os.open(
            destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        try:
            while True:
                chunk = os.read(descriptor, _UNTRACKED_READ_CHUNK_BYTES)
                if not chunk:
                    break
                _write_all(output, chunk)
        finally:
            os.close(output)
        closed = os.fstat(descriptor)
        if not same_file_metadata(opened, closed):
            raise PublishCheckError(
                "Publish scope moved while snapshotting unstaged content."
            )
        return True
    except PublishCheckError:
        raise
    except OSError as exc:
        raise PublishCheckError(
            "Unable to snapshot unstaged content for sensitive-output scanning."
        ) from exc
    finally:
        os.close(descriptor)


def _write_private_bytes(destination: Path, payload: bytes) -> None:
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        _write_all(descriptor, payload)
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, payload: bytes) -> None:
    remaining = memoryview(payload)
    while remaining:
        written = os.write(descriptor, remaining)
        if written <= 0:
            raise OSError("short write")
        remaining = remaining[written:]


def _no_index_environment(
    environment: Mapping[str, str], private_home: Path
) -> Mapping[str, str]:
    isolated = isolated_git_environment(environment)
    for variable in ("GIT_EXTERNAL_DIFF", "GIT_DIFF_OPTS"):
        isolated.pop(variable, None)
    isolated.update(
        {
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_PAGER": "cat",
            "PAGER": "cat",
            "HOME": str(private_home),
            "XDG_CONFIG_HOME": str(private_home),
        }
    )
    return isolated


def _assert_private_temp_outside_project(project: Path, temp_root: Path) -> None:
    try:
        temp_root.resolve().relative_to(project.resolve())
    except ValueError:
        return
    raise PublishCheckError(
        "Unable to create a private publish scan workspace outside the project."
    )


def _scan_untracked_path(project: Path, relative_path: str) -> Tuple[str, ...]:
    link_target = _read_project_symlink(project, relative_path, missing_ok=False)
    if link_target is not None:
        return _scan_symlink_target(relative_path, link_target)

    descriptor = _open_untracked_regular(
        project, relative_path, missing_ok=False
    )
    if descriptor is None:
        # Close the regular-file race without following a replacement link.
        link_target = _read_project_symlink(
            project, relative_path, missing_ok=False
        )
        if link_target is not None:
            return _scan_symlink_target(relative_path, link_target)
        return ()
    try:
        opened = os.fstat(descriptor)
        if _contains_nul(descriptor):
            closed = os.fstat(descriptor)
            if not same_file_metadata(opened, closed):
                raise PublishCheckError(
                    "Unable to inspect untracked file for sensitive output: "
                    f"{_terminal_literal(relative_path)}"
                )
            return ()
        os.lseek(descriptor, 0, os.SEEK_SET)
        records = _text_records(descriptor, relative_path)
        try:
            findings = tuple(
                scan_sensitive_records(
                    records,
                    source_name=_terminal_literal(relative_path),
                    format_name="text",
                )
            )
        except SensitiveOutputError as exc:
            raise PublishCheckError(
                "Unable to inspect untracked file for sensitive output: "
                f"{_terminal_literal(relative_path)}"
            ) from exc
        closed = os.fstat(descriptor)
        if not same_file_metadata(opened, closed):
            raise PublishCheckError(
                "Unable to inspect untracked file for sensitive output: "
                f"{_terminal_literal(relative_path)}"
            )
        return findings
    except PublishCheckError:
        raise
    except (OSError, UnicodeError) as exc:
        raise PublishCheckError(
            "Unable to inspect untracked file for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        ) from exc
    finally:
        os.close(descriptor)


def _scan_symlink_target(
    relative_path: str, link_target: str
) -> Tuple[str, ...]:
    records = link_target.split("\n")
    if any(
        len(record.encode("utf-8", "surrogateescape"))
        > _UNTRACKED_MAX_LINE_BYTES
        for record in records
    ):
        raise PublishCheckError(
            "Unable to inspect untracked symlink for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        )
    try:
        return tuple(
            scan_sensitive_records(
                records,
                source_name=_terminal_literal(relative_path),
                format_name="text",
            )
        )
    except SensitiveOutputError as exc:
        raise PublishCheckError(
            "Unable to inspect untracked symlink for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        ) from exc


def _read_project_symlink(
    project: Path, relative_path: str, *, missing_ok: bool
) -> Optional[str]:
    parts = _safe_relative_parts(relative_path)
    directory_flags = _project_directory_flags()
    descriptors: list[int] = []
    try:
        current = os.open(project, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        try:
            opened = os.stat(
                parts[-1], dir_fd=current, follow_symlinks=False
            )
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        if not stat.S_ISLNK(opened.st_mode):
            return None
        target = os.readlink(parts[-1], dir_fd=current)
        closed = os.stat(parts[-1], dir_fd=current, follow_symlinks=False)
        if not same_file_metadata(opened, closed):
            raise PublishCheckError(
                "Publish scope moved while reading an untracked symlink."
            )
        return target
    except PublishCheckError:
        raise
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        raise PublishCheckError(
            "Unable to inspect project symlink for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _open_untracked_regular(
    project: Path, relative_path: str, *, missing_ok: bool
) -> Optional[int]:
    parts = _safe_relative_parts(relative_path)
    directory_flags = _project_directory_flags()
    descriptors: list[int] = []
    try:
        current = os.open(project, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            file_flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            file_flags |= os.O_NONBLOCK
        descriptor: Optional[int] = None
        try:
            descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.EISDIR}:
                return None
            if missing_ok and exc.errno == errno.ENOENT:
                return None
            raise
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                os.close(descriptor)
                return None
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise
    except PublishCheckError:
        raise
    except OSError as exc:
        if missing_ok and exc.errno == errno.ENOENT:
            return None
        raise PublishCheckError(
            "Unable to inspect project file for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        ) from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _safe_relative_parts(relative_path: str) -> Tuple[str, ...]:
    parts = PurePosixPath(relative_path).parts
    if (
        not relative_path
        or PurePosixPath(relative_path).is_absolute()
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise PublishCheckError(
            "Unable to inspect project path for sensitive output: "
            f"{_terminal_literal(relative_path)}"
        )
    return parts


def _project_directory_flags() -> int:
    directory_flags = os.O_RDONLY
    if hasattr(os, "O_DIRECTORY"):
        directory_flags |= os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        directory_flags |= os.O_NOFOLLOW
    return directory_flags


def _contains_nul(descriptor: int) -> bool:
    while True:
        chunk = os.read(descriptor, _UNTRACKED_READ_CHUNK_BYTES)
        if not chunk:
            return False
        if b"\0" in chunk:
            return True


def _text_records(descriptor: int, relative_path: str) -> Iterator[str]:
    pending = bytearray()
    while True:
        chunk = os.read(descriptor, _UNTRACKED_READ_CHUNK_BYTES)
        if not chunk:
            break
        pending.extend(chunk)
        while True:
            newline = pending.find(b"\n")
            if newline < 0:
                break
            record = bytes(pending[:newline])
            del pending[: newline + 1]
            if len(record) > _UNTRACKED_MAX_LINE_BYTES:
                raise PublishCheckError(
                    "Unable to inspect untracked file for sensitive output: "
                    f"{_terminal_literal(relative_path)}"
                )
            yield record.rstrip(b"\r").decode("utf-8", "surrogateescape")
        if len(pending) > _UNTRACKED_MAX_LINE_BYTES:
            raise PublishCheckError(
                "Unable to inspect untracked file for sensitive output: "
                f"{_terminal_literal(relative_path)}"
            )
    if pending:
        yield bytes(pending).rstrip(b"\r").decode("utf-8", "surrogateescape")


def _append_status_group(
    lines: list[str], title: str, paths: Tuple[str, ...]
) -> None:
    lines.append(f"{title} ({len(paths)}):\n")
    if paths:
        lines.extend(f"- {_terminal_literal(path)}\n" for path in paths)
    else:
        lines.append("- None\n")


def _render_top_paths(paths: Tuple[str, ...]) -> Iterable[str]:
    counts: dict[str, int] = {}
    for path in paths:
        top = path.split("/", 1)[0] or "."
        counts[top] = counts.get(top, 0) + 1
    for top, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]:
        yield f"- {_terminal_literal(top)} ({count} files)\n"


def _render_visible_suggestions(plan: VerificationPlan) -> str:
    if not plan.steps:
        return (
            "- No automated command selected. For docs-only changes, manually "
            "review rendered Markdown and links.\n"
        )
    return "".join(
        f"- [{_terminal_literal(step.reason)}] "
        f"{_terminal_literal(step.command)}\n"
        for step in plan.steps
    )
