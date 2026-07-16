"""Diagnose one Agent Rails Target Project and optionally repair Claude."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import errno
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import tempfile
from typing import Mapping, Optional, Tuple
import unicodedata

from agent_rails.adapters.claude import (
    ClaudeAdapterError,
    ClaudeEventStream,
    ClaudeInstallMode,
    ClaudeInstallRequest,
    run_claude_adapter,
)
from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_project_root_identity,
    resolve_target_project,
)
from agent_rails.core.paths import AgentRailsPaths, canonical_path
from agent_rails.git._runner import run_git
from agent_rails.memory.online import OnlineMemoryError, OnlineMemoryQuery, query_online_memory
from agent_rails.models.presets import resolve_model


_DOCTOR_PROFILE_VARIABLES = (
    "MEMORY_PROVIDER",
    "AGENT_RAILS_ONLINE_MEMORY_CMD",
    "AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS",
    "AGENT_RAILS_MODEL",
    "AGENT_RAILS_CLAUDE_USER_MD",
    "AGENT_RAILS_CLAUDE_SETTINGS",
)
_RULES_MARKER = b"<!-- agent-rails:start -->"
_GLOBAL_MARKER = b"<!-- agent-rails:global-reminder:start -->"
_HOOK_BASENAME = b"agent-rails-session-start.sh"
_ADAPTER_VERSION = re.compile(
    rb"^Agent Rails Version:[\t ]*`?([^`\s]+)`?", re.MULTILINE
)
_MANIFEST_VERSION = re.compile(
    rb'^[\t ]*"version"[\t ]*:[\t ]*"([^"]+)"', re.MULTILINE
)
_MAX_DOCTOR_FILE_BYTES = 4 * 1024 * 1024


class DoctorError(RuntimeError):
    """Doctor could not complete its application request."""


class DoctorInputError(DoctorError):
    """The caller supplied an invalid typed Doctor request."""


class DoctorEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class DoctorRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    online_memory_smoke: bool
    fix: bool
    fix_mode: ClaudeInstallMode
    fix_session_hook: bool
    fix_global_reminder: bool
    dry_run: bool
    environment: Mapping[str, str]


@dataclass(frozen=True)
class DoctorEvent:
    stream: DoctorEventStream
    text: str


@dataclass(frozen=True)
class DoctorResult:
    project_root: Optional[Path]
    profile_path: Optional[str]
    failures: int
    warnings: int
    events: Tuple[DoctorEvent, ...]

    @property
    def exit_code(self) -> int:
        return 1 if self.failures else 0

    @property
    def stdout(self) -> str:
        return _render_events(self.events, DoctorEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, DoctorEventStream.STDERR)


class _Report:
    def __init__(self) -> None:
        self.events: list[DoctorEvent] = []
        self.failures = 0
        self.warnings = 0

    def line(self, text: str = "", stream: DoctorEventStream = DoctorEventStream.STDOUT) -> None:
        self.events.append(DoctorEvent(stream, _terminal_literal(text)))

    def ok(self, text: str) -> None:
        self.line(f"[OK] {text}")

    def info(self, text: str) -> None:
        self.line(f"[INFO] {text}")

    def warn(self, text: str) -> None:
        self.warnings += 1
        self.line(f"[WARN] {text}")

    def fail(self, text: str) -> None:
        self.failures += 1
        self.line(f"[FAIL] {text}")

    def section(self, title: str) -> None:
        self.line()
        self.line(title)


def run_doctor(
    request: DoctorRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> DoctorResult:
    """Resolve configuration once, report health, and optionally repair Claude."""

    _validate_request(request)
    environment = dict(request.environment)
    kit_home = Path(os.path.realpath(os.fspath(request.kit_home)))
    report = _Report()
    version = _resolve_version(kit_home, environment)

    report.line("Agent Rails Doctor")
    report.line()
    if kit_home.is_dir():
        report.ok(f"Agent Rails home: {kit_home}")
    else:
        report.fail(f"Agent Rails home not found: {kit_home}")
    cli_path = kit_home / "bin/agent-rails"
    if os.access(cli_path, os.X_OK) and cli_path.is_file():
        report.ok(f"Agent Rails CLI: {cli_path}")
    else:
        report.fail(f"Agent Rails CLI is not executable: {cli_path}")
    report.ok(f"Kit version: {version}")

    if not request.requested_project.is_dir():
        report.fail(f"project directory not found: {request.requested_project}")
        _finish(report)
        return _result(report, None, request.explicit_profile)

    if context is None:
        context, profile_error = _resolve_context(request, kit_home, environment)
    else:
        _validate_pre_resolved_context(
            context=context,
            request=request,
            kit_home=kit_home,
            environment=environment,
        )
        profile_error = None
    report.ok(f"Project: {context.root}")
    if context.is_git_repo:
        report.ok(f"Git repository: {context.root}")
    else:
        report.warn("No git repository detected; diff-based pack/check output will be limited.")

    profile_path = Path(context.profile_path)
    if profile_path.is_file():
        report.ok(f"Profile: {context.profile_path}")
    else:
        report.fail(f"Profile not found: {context.profile_path}")
    if profile_error is not None:
        report.fail(str(profile_error))

    values = context.profile_values
    env_file = _value(values, environment, "AGENT_RAILS_ENV_FILE", "")
    if env_file:
        if Path(env_file).is_file():
            report.ok(f"Env file: {env_file}")
        else:
            report.warn(f"Env file configured but missing: {env_file}")
    else:
        report.info("No Agent Rails env file configured.")

    pack_mode = _value(values, environment, "AGENT_RAILS_PACK_MODE", "normal")
    if pack_mode in {"lite", "normal", "deep", "audit"}:
        report.ok(f"Pack mode: {pack_mode}")
    else:
        report.warn(
            f"Unknown pack mode: {pack_mode} (expected lite, normal, deep, or audit)"
        )
    model = _value(values, environment, "AGENT_RAILS_MODEL", "generic")
    if resolve_model(model).known:
        report.ok(f"Model preset: {model}")
    else:
        report.warn(f"Unknown model preset: {model}")
    report.info(f"Task Pack path: {context.task_pack_path}")

    report.section("Tools")
    for command in ("git",):
        if shutil.which(command, path=environment.get("PATH", "")):
            report.ok(f"command available: {command}")
        else:
            report.warn(f"command missing: {command}")

    report.section("Plugin Manifests")
    for label, relative in (
        ("Codex plugin", ".codex-plugin/plugin.json"),
        ("Claude plugin", ".claude-plugin/plugin.json"),
        (
            "Codex marketplace plugin",
            "codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json",
        ),
    ):
        _check_manifest(
            report,
            label,
            kit_home / relative,
            version,
            anchor=kit_home,
        )

    provider = _value(values, environment, "MEMORY_PROVIDER", "local")
    online_command = _value(
        values, environment, "AGENT_RAILS_ONLINE_MEMORY_CMD", ""
    )
    report.section("Memory")
    report.ok(f"Memory provider: {provider}")
    uses_online = provider in {"online", "hybrid"}
    if uses_online:
        if online_command:
            report.ok("Online memory command configured.")
        else:
            report.warn("AGENT_RAILS_ONLINE_MEMORY_CMD is not configured.")
    if request.online_memory_smoke:
        _online_memory_smoke(
            report,
            request=request,
            context=context,
            provider=provider,
            command=online_command,
            timeout_text=_value(
                values,
                environment,
                "AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS",
                "8",
            ),
        )
    elif uses_online:
        report.info(
            "Online memory smoke not requested; pass --online-memory-smoke to test the read path."
        )

    layout = _ClaudeLayout.from_context(context, kit_home, values, environment)
    report.section("Claude Adapter")
    existing_hook, existing_global = _check_claude(
        report, layout, context, version
    )

    report.section("Skills")
    source_skills = _check_skills(
        report,
        kit_home / "skills",
        layout.skills_dir,
        cli_path,
        target_anchor=context.root,
    )

    report.section("Git Visibility")
    _check_git_visibility(report, context, layout, source_skills, environment)

    report.section("Suggested Commands")
    _suggest_commands(report, cli_path, context)

    if request.fix:
        report.section("Fixes")
        if report.failures:
            report.warn(
                "Skipping --fix because doctor has failures. Resolve failures first, then rerun doctor --fix."
            )
        elif request.dry_run:
            _print_fix_command(report, request, context, kit_home, existing_hook, existing_global)
        else:
            try:
                fixed = run_claude_adapter(
                    ClaudeInstallRequest(
                        requested_project=context.root,
                        kit_home=kit_home,
                        explicit_profile=context.profile_path,
                        mode=request.fix_mode,
                        dry_run=False,
                        force=True,
                        global_reminder=request.fix_global_reminder or existing_global,
                        session_hook=request.fix_session_hook or existing_hook,
                        environment=environment,
                    ),
                    context=context,
                )
            except ClaudeAdapterError as exc:
                raise DoctorError(str(exc)) from exc
            for event in fixed.events:
                stream = (
                    DoctorEventStream.STDOUT
                    if event.stream is ClaudeEventStream.STDOUT
                    else DoctorEventStream.STDERR
                )
                report.line(event.text, stream)
            report.line("Doctor fix completed. Re-run doctor to verify a clean state.")

    _finish(report)
    return _result(report, context.root, context.profile_path)


@dataclass(frozen=True)
class _ClaudeLayout:
    claude_dir: Path
    skills_dir: Path
    guide: Path
    pack: Path
    lite: Path
    check: Path
    local_rules: Path
    project_rules: Path
    settings: Path
    user_rules: Path
    hook: Path

    @classmethod
    def from_context(
        cls,
        context: TargetProjectContext,
        kit_home: Path,
        values: Mapping[str, str],
        environment: Mapping[str, str],
    ) -> "_ClaudeLayout":
        home = environment.get("HOME", "")
        settings = _value(
            values,
            environment,
            "AGENT_RAILS_CLAUDE_SETTINGS",
            str(Path(home) / ".claude/settings.json"),
        )
        user_rules = _value(
            values,
            environment,
            "AGENT_RAILS_CLAUDE_USER_MD",
            str(Path(home) / ".claude/CLAUDE.md"),
        )
        claude_dir = context.root / ".claude"
        commands = claude_dir / "commands"
        return cls(
            claude_dir=claude_dir,
            skills_dir=claude_dir / "skills",
            guide=claude_dir / "AGENT_RAILS.md",
            pack=commands / "agent-rails-pack.md",
            lite=commands / "agent-rails-lite.md",
            check=commands / "agent-rails-check.md",
            local_rules=context.root / "CLAUDE.local.md",
            project_rules=context.root / "CLAUDE.md",
            settings=_environment_path(settings, home),
            user_rules=_environment_path(user_rules, home),
            hook=kit_home / "hooks/agent-rails-session-start.sh",
        )


def _validate_request(request: DoctorRequest) -> None:
    if not isinstance(request, DoctorRequest):
        raise DoctorInputError("Invalid Doctor request.")
    if not isinstance(request.requested_project, Path):
        raise DoctorInputError("Doctor requested project must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise DoctorInputError("Doctor kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise DoctorInputError("Doctor explicit Profile must be text.")
    for name in (
        "online_memory_smoke",
        "fix",
        "fix_session_hook",
        "fix_global_reminder",
        "dry_run",
    ):
        if not isinstance(getattr(request, name), bool):
            raise DoctorInputError(f"Doctor {name} policy must be boolean.")
    if not isinstance(request.fix_mode, ClaudeInstallMode):
        raise DoctorInputError("Invalid Doctor fix mode.")
    if not isinstance(request.environment, Mapping):
        raise DoctorInputError("Doctor environment must be a mapping.")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise DoctorInputError("Doctor environment keys and values must be text.")


def _resolve_context(
    request: DoctorRequest, kit_home: Path, environment: Mapping[str, str]
) -> tuple[TargetProjectContext, Optional[ProfileLoadError]]:
    try:
        return (
            resolve_target_project(
                request.requested_project,
                kit_home=kit_home,
                explicit_profile=request.explicit_profile,
                environment=environment,
                require_profile=False,
                load_profile=True,
                load_environment_file=True,
                profile_variables=_DOCTOR_PROFILE_VARIABLES,
                capture_profile_environment=True,
            ),
            None,
        )
    except ProfileLoadError as exc:
        context = resolve_target_project(
            request.requested_project,
            kit_home=kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            require_profile=False,
            load_profile=False,
        )
        if exc.stage == "environment" and exc.path is not None:
            context = replace(
                context,
                profile_status="error",
                profile_values={"AGENT_RAILS_ENV_FILE": str(exc.path)},
            )
        return context, exc
    except TargetProjectError as exc:
        raise DoctorError(str(exc)) from exc


def _validate_pre_resolved_context(
    *,
    context: TargetProjectContext,
    request: DoctorRequest,
    kit_home: Path,
    environment: Mapping[str, str],
) -> None:
    """Reject a context that was resolved for a different invocation."""

    if not isinstance(context, TargetProjectContext):
        raise DoctorInputError("Doctor context must be a TargetProjectContext.")
    if not isinstance(context.root, Path) or not isinstance(
        context.profile_path, str
    ):
        raise DoctorInputError(
            "Doctor context has invalid project or Profile fields."
        )
    requested_root, _ = resolve_project_root_identity(
        request.requested_project, environment
    )
    if canonical_path(context.root) != requested_root:
        raise DoctorInputError(
            "Doctor context does not match the requested project."
        )
    expected_profile = AgentRailsPaths.from_environment(
        kit_home, environment
    ).resolve_profile(
        requested_root,
        requested_root.name,
        request.explicit_profile,
    )
    if _canonical_profile_path(context.profile_path) != _canonical_profile_path(
        expected_profile
    ):
        raise DoctorInputError(
            "Doctor context does not match the requested Profile or kit."
        )
    resolved_kit = context.profile_environment.get("AGENT_RAILS_HOME", "")
    if resolved_kit and canonical_path(Path(resolved_kit)) != kit_home:
        raise DoctorInputError(
            "Doctor context does not match the requested Profile or kit."
        )


def _canonical_profile_path(value: str) -> Path:
    return canonical_path(Path(os.path.abspath(value)))


def _check_manifest(
    report: _Report,
    label: str,
    path: Path,
    version: str,
    *,
    anchor: Path,
) -> None:
    raw = _read_regular(path, anchor=anchor)
    if raw is None:
        report.warn(f"{label} manifest missing: {path}")
        return
    manifest_version = ""
    try:
        payload = json.loads(raw.decode("utf-8", "strict"))
    except (UnicodeError, json.JSONDecodeError):
        match = _MANIFEST_VERSION.search(raw)
        if match:
            manifest_version = match.group(1).decode("utf-8", "surrogateescape")
    else:
        if isinstance(payload, dict) and isinstance(payload.get("version"), str):
            manifest_version = payload["version"]
    if not manifest_version:
        report.warn(f"{label} manifest has no version: {path}")
    elif manifest_version == version:
        report.ok(f"{label} manifest version: {manifest_version}")
    else:
        report.warn(
            f"{label} manifest version {manifest_version} differs from kit version {version}."
        )


def _online_memory_smoke(
    report: _Report,
    *,
    request: DoctorRequest,
    context: TargetProjectContext,
    provider: str,
    command: str,
    timeout_text: str,
) -> None:
    if provider not in {"online", "hybrid"}:
        report.warn(
            "Online memory smoke requested but MEMORY_PROVIDER is not online/hybrid."
        )
        return
    if not command:
        return
    try:
        timeout = int(timeout_text)
        if timeout <= 0:
            raise ValueError
    except ValueError:
        timeout = 8
    try:
        with tempfile.TemporaryDirectory(prefix="agent-rails-doctor-memory-") as temp:
            query_path = Path(temp) / "query.md"
            query_path.write_text(
                "Agent Rails Doctor online memory smoke.\n",
                encoding="utf-8",
                errors="strict",
            )
            adapter_environment = dict(request.environment)
            adapter_environment.update(context.profile_environment)
            query_online_memory(
                command,
                OnlineMemoryQuery(
                    query_file=query_path,
                    project=context.project_name,
                    limit=1,
                    timeout_seconds=timeout,
                    working_directory=context.root,
                ),
                environment=adapter_environment,
            )
    except (OnlineMemoryError, OSError, UnicodeError):
        report.warn("Online memory smoke failed; adapter diagnostics were suppressed.")
    else:
        report.ok("Online memory smoke read OK.")


def _check_claude(
    report: _Report, layout: _ClaudeLayout, context: TargetProjectContext, version: str
) -> tuple[bool, bool]:
    for path, present, missing in (
        (layout.guide, f"Claude guide installed: {layout.guide}", f"Missing Claude guide: {layout.guide}"),
        (layout.pack, "Claude pack command installed.", f"Missing Claude pack command: {layout.pack}"),
        (layout.lite, "Claude lite command installed.", f"Missing Claude lite command: {layout.lite}"),
        (layout.check, "Claude check command installed.", f"Missing Claude check command: {layout.check}"),
    ):
        if _read_regular(path, anchor=context.root) is not None:
            report.ok(present)
        else:
            report.warn(missing)

    local = _read_regular(layout.local_rules, anchor=context.root)
    project = _read_regular(layout.project_rules, anchor=context.root)
    if local is not None and _RULES_MARKER in local:
        report.ok("CLAUDE.local.md contains Agent Rails block.")
    elif project is not None and _RULES_MARKER in project:
        report.ok("CLAUDE.md contains Agent Rails block.")
    else:
        report.warn("CLAUDE.local.md/CLAUDE.md Agent Rails block is missing.")

    adapter_version = ""
    for path in (layout.guide, layout.local_rules, layout.project_rules):
        raw = _read_regular(path, anchor=context.root)
        match = _ADAPTER_VERSION.search(raw) if raw is not None else None
        if match:
            adapter_version = match.group(1).decode("utf-8", "surrogateescape")
            break
    if adapter_version:
        if adapter_version == version:
            report.ok(f"Claude adapter version: {adapter_version}")
        else:
            report.warn(
                f"Claude adapter version {adapter_version} differs from kit version {version}; run doctor --fix."
            )
    elif any(
        raw is not None
        for raw in (
            local,
            project,
            _read_regular(layout.guide, anchor=context.root),
        )
    ):
        report.warn("Claude adapter version missing; run doctor --fix.")

    guide = _read_regular(layout.guide, anchor=context.root)
    if guide is not None and os.fsencode(context.profile_path) not in guide:
        report.warn("Claude guide does not reference current profile path.")
    pack = _read_regular(layout.pack, anchor=context.root)
    if pack is not None and os.fsencode(context.root) in pack:
        report.warn(
            "Claude pack command hardcodes this install path; upgrade adapter to make worktrees safe."
        )
    if pack is not None and b"git rev-parse --show-toplevel" not in pack:
        report.warn("Claude pack command does not resolve the current git worktree root.")

    settings = _read_regular(layout.settings)
    existing_hook = settings is not None and _HOOK_BASENAME in settings
    if existing_hook:
        report.ok(f"Claude SessionStart hook installed: {layout.settings}")
        if os.fsencode(layout.hook) not in settings:
            report.warn(
                "Claude SessionStart hook points to a different Agent Rails path; reinstall with --session-hook if this kit moved."
            )
    else:
        report.info(
            "Claude SessionStart hook not installed; pass --session-hook to inject Agent Rails as startup context."
        )
    user_rules = _read_regular(layout.user_rules)
    existing_global = user_rules is not None and _GLOBAL_MARKER in user_rules
    return existing_hook, existing_global


def _check_skills(
    report: _Report,
    source: Path,
    target: Path,
    cli_path: Path,
    *,
    target_anchor: Path,
) -> Tuple[str, ...]:
    try:
        entries = tuple(
            sorted(
                (entry for entry in os.scandir(source) if entry.is_dir(follow_symlinks=False)),
                key=lambda entry: os.fsencode(entry.name),
            )
        )
    except (FileNotFoundError, NotADirectoryError):
        report.fail(f"Agent Rails source skills dir missing: {source}")
        return ()
    except OSError as exc:
        raise DoctorError(f"Unable to inspect Agent Rails source skills: {source}") from exc
    missing = 0
    names = []
    for entry in entries:
        names.append(entry.name)
        if (
            _read_regular(
                target / entry.name / "SKILL.md",
                anchor=target_anchor,
            )
            is not None
        ):
            report.ok(f"skill installed: {entry.name}")
        else:
            missing += 1
            report.warn(f"skill missing from project: {entry.name}")
    if missing:
        report.info(f'Install/update skills: {cli_path} skills install --dest "{target}"')
    return tuple(names)


def _check_git_visibility(
    report: _Report,
    context: TargetProjectContext,
    layout: _ClaudeLayout,
    skills: Tuple[str, ...],
    environment: Mapping[str, str],
) -> None:
    if not context.is_git_repo:
        report.info("Skipping git visibility checks outside git.")
        return
    candidates = [
        "CLAUDE.local.md",
        ".claude/AGENT_RAILS.md",
        ".claude/commands/agent-rails-pack.md",
        ".claude/commands/agent-rails-lite.md",
        ".claude/commands/agent-rails-check.md",
        *(f".claude/skills/{name}" for name in skills),
    ]
    project_rules = _read_regular(layout.project_rules, anchor=context.root)
    if project_rules is not None and _RULES_MARKER in project_rules:
        candidates.append("CLAUDE.md")
    try:
        tracked = run_git(
            context.root, ("ls-files", "--", *candidates), environment=environment
        )
    except OSError:
        tracked_text = ""
    else:
        tracked_text = tracked.stdout.strip() if tracked.returncode == 0 else ""
    if tracked_text:
        report.ok("Agent Rails adapter files are tracked; project mode is plausible.")
        return
    if layout.claude_dir.exists() or layout.local_rules.exists():
        guide_ignored = _git_ignored(context.root, ".claude/AGENT_RAILS.md", environment)
        local_ignored = _git_ignored(context.root, "CLAUDE.local.md", environment)
        if guide_ignored and local_ignored:
            report.ok("Agent Rails adapter files are ignored locally; local mode is plausible.")
        else:
            report.warn("Agent Rails adapter files exist but are neither tracked nor ignored.")
    else:
        report.info("No Claude adapter files found yet.")


def _git_ignored(project: Path, relative: str, environment: Mapping[str, str]) -> bool:
    try:
        completed = run_git(
            project, ("check-ignore", "-q", relative), environment=environment
        )
    except OSError:
        return False
    return completed.returncode == 0


def _suggest_commands(
    report: _Report, cli_path: Path, context: TargetProjectContext
) -> None:
    project = context.root
    profile = context.profile_path
    report.line(f'- Generate pack: {cli_path} pack --project "{project}" --profile "{profile}" "<goal>"')
    report.line(f'- Check verification plan: {cli_path} check --project "{project}" --profile "{profile}" --print-only')
    report.line(f'- Install Claude adapter: {cli_path} claude install --project "{project}" --profile "{profile}" --mode local')
    report.line(f'- Install Claude adapter with startup hook: {cli_path} claude install --project "{project}" --profile "{profile}" --mode local --session-hook')
    report.line(f'- Fix local Agent Rails adapter: {cli_path} doctor --project "{project}" --profile "{profile}" --fix')
    report.line(f'- Preview Claude adapter removal: {cli_path} claude uninstall --project "{project}" --dry-run')


def _print_fix_command(
    report: _Report,
    request: DoctorRequest,
    context: TargetProjectContext,
    kit_home: Path,
    existing_hook: bool,
    existing_global: bool,
) -> None:
    command = [
        str(kit_home / "bin/agent-rails"),
        "claude",
        "install",
        "--force",
        "--project",
        str(context.root),
        "--profile",
        context.profile_path,
        "--mode",
        request.fix_mode.value,
    ]
    if request.fix_session_hook or existing_hook:
        command.append("--session-hook")
    if request.fix_global_reminder or existing_global:
        command.append("--global-reminder")
    command.append("--dry-run")
    report.line(f"Would run: {shlex.join(command)}")


def _finish(report: _Report) -> None:
    report.line()
    if report.failures:
        report.line(
            f"Doctor status: FAIL ({report.failures} failure(s), {report.warnings} warning(s))"
        )
    elif report.warnings:
        report.line(f"Doctor status: OK with warnings ({report.warnings} warning(s))")
    else:
        report.line("Doctor status: OK")


def _result(
    report: _Report, project_root: Optional[Path], profile_path: Optional[str]
) -> DoctorResult:
    return DoctorResult(
        project_root=project_root,
        profile_path=profile_path,
        failures=report.failures,
        warnings=report.warnings,
        events=tuple(report.events),
    )


def _value(
    values: Mapping[str, str],
    environment: Mapping[str, str],
    name: str,
    default: str,
) -> str:
    return values.get(name, environment.get(name, default))


def _resolve_version(kit_home: Path, environment: Mapping[str, str]) -> str:
    override = environment.get("AGENT_RAILS_VERSION_OVERRIDE", "")
    if override:
        return override
    path = kit_home / "VERSION"
    raw = _read_regular(path, anchor=kit_home)
    if raw is None:
        return "0.0.0-dev"
    for line in raw.splitlines():
        fields = line.split()
        if fields:
            return fields[0].decode("utf-8", "surrogateescape")
    return ""


def _read_regular(path: Path, *, anchor: Optional[Path] = None) -> Optional[bytes]:
    if anchor is not None:
        descriptor = _open_anchored_regular(anchor, path)
        if descriptor is None:
            return None
        expected = os.fstat(descriptor)
    else:
        descriptor = None
        try:
            expected = os.lstat(path)
        except (FileNotFoundError, NotADirectoryError):
            return None
        except OSError as exc:
            raise DoctorError(f"Unable to inspect Doctor path: {path}") from exc
        if not stat.S_ISREG(expected.st_mode):
            return None
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        if hasattr(os, "O_NONBLOCK"):
            flags |= os.O_NONBLOCK
        try:
            descriptor = os.open(path, flags)
        except OSError as exc:
            raise DoctorError(f"Unable to read Doctor path: {path}") from exc
    assert descriptor is not None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != expected.st_dev
            or opened.st_ino != expected.st_ino
        ):
            raise DoctorError(f"Doctor path moved while reading: {path}")
        if opened.st_size > _MAX_DOCTOR_FILE_BYTES:
            raise DoctorError(
                f"Doctor path exceeds {_MAX_DOCTOR_FILE_BYTES} bytes: {path}"
            )
        chunks = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                raise DoctorError(f"Doctor path changed while reading: {path}")
            chunks.append(chunk)
            remaining -= len(chunk)
        closed = os.fstat(descriptor)
        if (
            closed.st_size != opened.st_size
            or closed.st_mtime_ns != opened.st_mtime_ns
            or closed.st_ctime_ns != opened.st_ctime_ns
        ):
            raise DoctorError(f"Doctor path changed while reading: {path}")
        return b"".join(chunks)
    except OSError as exc:
        raise DoctorError(f"Unable to read Doctor path: {path}") from exc
    finally:
        os.close(descriptor)


def _open_anchored_regular(anchor: Path, path: Path) -> Optional[int]:
    anchor = Path(os.path.abspath(anchor))
    path = Path(os.path.abspath(path))
    try:
        relative = path.relative_to(anchor)
    except ValueError as exc:
        raise DoctorError(f"Doctor path escapes its trusted root: {path}") from exc
    parts = relative.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise DoctorError(f"Invalid Doctor path below trusted root: {path}")

    directory_flags = os.O_RDONLY
    directory_flags |= getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptors = []
    try:
        current = os.open(anchor, directory_flags)
        descriptors.append(current)
        for part in parts[:-1]:
            current = os.open(part, directory_flags, dir_fd=current)
            descriptors.append(current)
        file_flags = os.O_RDONLY
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        file_flags |= getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(parts[-1], file_flags, dir_fd=current)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
        return descriptor
    except OSError as exc:
        if exc.errno in {
            errno.ENOENT,
            errno.ENOTDIR,
            errno.ELOOP,
            errno.EISDIR,
        }:
            return None
        raise DoctorError(f"Unable to read Doctor path: {path}") from exc
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def _environment_path(value: str, home: str) -> Path:
    if value == "~" or value.startswith("~/"):
        if not home:
            raise DoctorError("HOME is required to expand a Claude path.")
        value = home if value == "~" else str(Path(home) / value[2:])
    return Path(os.path.abspath(value))


def _terminal_literal(value: str) -> str:
    escaped = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
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
        elif category in {"Cf", "Zl", "Zp"} or 0xD800 <= codepoint <= 0xDFFF:
            if codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _render_events(
    events: Tuple[DoctorEvent, ...], stream: DoctorEventStream
) -> str:
    selected = [event.text for event in events if event.stream is stream]
    return "" if not selected else "\n".join(selected) + "\n"
