"""Claude Code adapter application lifecycle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import secrets
import shlex
import stat
from typing import Any, Mapping, Optional, Tuple, Union
import unicodedata

from agent_rails.config.target_project import (
    TargetProjectContext,
    resolve_project_root_identity,
    resolve_target_project,
)
from agent_rails.core.paths import AgentRailsPaths, canonical_path
from agent_rails.git._runner import run_git

from .content import (
    AdapterArtifact,
    AdapterContentError,
    AdapterContentRequest,
    AdapterType,
    render_adapter_content,
)
from .workspace import (
    ManagedAdapterWorkspace,
    ManagedAdapterWorkspaceConfig,
    ManagedAdapterWorkspaceError,
)


CLAUDE_PROFILE_VARIABLES = (
    "AGENT_RAILS_CLAUDE_USER_MD",
    "AGENT_RAILS_CLAUDE_SETTINGS",
)

_RULES_MARKER = "<!-- agent-rails:start -->"
_RULES_END_MARKER = "<!-- agent-rails:end -->"
_GLOBAL_MARKER = "<!-- agent-rails:global-reminder:start -->"
_GLOBAL_END_MARKER = "<!-- agent-rails:global-reminder:end -->"
_IGNORE_MARKER = "# Agent Rails local adapter"
_IGNORE_END_MARKER = "# Agent Rails local adapter end"
_IGNORE_ENTRIES = (
    ".claude/AGENT_RAILS.md",
    ".claude/.agent-rails-managed-skills",
    ".claude/commands/agent-rails-pack.md",
    ".claude/commands/agent-rails-lite.md",
    ".claude/commands/agent-rails-check.md",
    ".claude/skills/agent-*/",
    ".agent-rails/",
    "CLAUDE.local.md",
)
_IGNORE_CLEANUP_ENTRIES = (
    ".claude/",
    "CLAUDE.md",
)
_ALL_IGNORE_ENTRIES = tuple(
    dict.fromkeys((*_IGNORE_ENTRIES, *_IGNORE_CLEANUP_ENTRIES))
)
_HOOK_BASENAME = "agent-rails-session-start.sh"

_GLOBAL_REMINDER_BLOCK = """<!-- agent-rails:global-reminder:start -->
## Agent Rails

When a repository contains a local Agent Rails adapter (`CLAUDE.local.md` with `agent-rails:start` or `.claude/AGENT_RAILS.md`), treat it as mandatory for substantial engineering work and useful in lite mode for POCs or deploy prep.

If neither marker exists in the current repository, ignore this Agent Rails reminder and follow normal project instructions.

Visible session marker protocol:

- If using pack or lite, first tell the user: `AGENT RAILS: ON (mode=<mode>, pack=<task-pack-path>)`.
- If using check-only, first tell the user: `AGENT RAILS: CHECK-ONLY (reason=<reason>)`.
- If intentionally skipping Agent Rails, first tell the user: `AGENT RAILS: SKIPPED (reason=<reason>)`.

Before broad context reads or edits, run `/agent-rails-pack <goal>` if available; otherwise read the local Agent Rails block and run its `agent-rails pack` command. Use `/agent-rails-lite <goal>` or `--pack-mode lite` for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, or continuation from an existing handbook. Read the generated Task Pack and follow its contract, memory cards, verification suggestions, subagent result contract, and delivery checklist.

Before final delivery, and as Step 0 for deploy/release/upload workflows that consume the current branch, run `/agent-rails-check` if available; otherwise run the local Agent Rails check command.
<!-- agent-rails:global-reminder:end -->
"""


class ClaudeAdapterError(RuntimeError):
    """The Claude adapter request could not be completed."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Tuple["ClaudeEvent", ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.events = _sanitize_events(events)

    @property
    def stdout(self) -> str:
        return _render_event_stream(self.events, ClaudeEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_event_stream(self.events, ClaudeEventStream.STDERR)


class ClaudeAdapterInputError(ClaudeAdapterError):
    """The caller supplied an invalid typed Claude adapter request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class ClaudeAction(str, Enum):
    INSTALL = "install"
    UNINSTALL = "uninstall"


class ClaudeInstallMode(str, Enum):
    LOCAL = "local"
    PROJECT = "project"


class ClaudeEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class ClaudeInstallRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    mode: ClaudeInstallMode
    dry_run: bool
    force: bool
    global_reminder: bool
    session_hook: bool
    environment: Mapping[str, str]


@dataclass(frozen=True)
class ClaudeUninstallRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    dry_run: bool
    force: bool
    global_reminder: bool
    session_hook: bool
    environment: Mapping[str, str]


ClaudeAdapterRequest = Union[ClaudeInstallRequest, ClaudeUninstallRequest]


@dataclass(frozen=True)
class ClaudeEvent:
    stream: ClaudeEventStream
    text: str


@dataclass(frozen=True)
class ClaudeAdapterResult:
    action: ClaudeAction
    project_root: Path
    profile_path: str
    task_pack_path: str
    mode: ClaudeInstallMode
    events: Tuple[ClaudeEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_event_stream(self.events, ClaudeEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_event_stream(self.events, ClaudeEventStream.STDERR)


@dataclass(frozen=True)
class _ClaudeLayout:
    claude_dir: Path
    skills_dir: Path
    commands_dir: Path
    guide_path: Path
    pack_command_path: Path
    lite_command_path: Path
    check_command_path: Path
    managed_skills_path: Path
    project_rules_path: Path
    local_rules_path: Path
    local_ignore_path: Path
    project_ignore_path: Path
    user_rules_path: Path
    settings_path: Path
    session_hook_path: Path


@dataclass(frozen=True)
class _TextPlan:
    path: Path
    content: Optional[str]
    changed: bool
    messages: Tuple[str, ...]


@dataclass(frozen=True)
class _SettingsPlan:
    path: Path
    data: Mapping[str, Any]
    changed: bool
    messages: Tuple[str, ...]


def run_claude_adapter(
    request: ClaudeAdapterRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> ClaudeAdapterResult:
    """Resolve once, preflight fully, then apply one Claude lifecycle request."""

    action, mode, dry_run, force = _request_policy(request)
    environment = dict(request.environment)
    kit_home = Path(os.path.realpath(os.fspath(request.kit_home)))
    install = action is ClaudeAction.INSTALL
    if context is None:
        context = resolve_target_project(
            request.requested_project,
            kit_home=kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            require_profile=install,
            load_profile=install,
            load_environment_file=False,
            profile_variables=CLAUDE_PROFILE_VARIABLES,
        )
    else:
        _validate_pre_resolved_context(
            context=context,
            requested_project=request.requested_project,
            kit_home=kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
        )
    version = _resolve_version(kit_home, environment)
    layout = _build_layout(context, kit_home, environment)
    events: list[ClaudeEvent] = []

    try:
        workspace = ManagedAdapterWorkspace(
            ManagedAdapterWorkspaceConfig(
                home=kit_home,
                project=context.root,
                skills_relative_dir=Path(".claude/skills"),
                guide_path=layout.guide_path,
                pack_command_path=layout.pack_command_path,
                lite_command_path=layout.lite_command_path,
                check_command_path=layout.check_command_path,
                managed_skills_path=layout.managed_skills_path,
                dry_run=dry_run,
                force=force,
                protect_tracked=mode is ClaudeInstallMode.LOCAL,
            )
        )
        for path in (layout.local_rules_path, layout.project_rules_path):
            workspace.validate_managed_path(path)
        _stdout_many(events, workspace.load_managed_skills())
        if install:
            _install(
                context=context,
                kit_home=kit_home,
                version=version,
                mode=mode,
                dry_run=dry_run,
                force=force,
                global_reminder=request.global_reminder,
                session_hook=request.session_hook,
                layout=layout,
                workspace=workspace,
                events=events,
            )
        else:
            _uninstall(
                context=context,
                dry_run=dry_run,
                force=force,
                global_reminder=request.global_reminder,
                session_hook=request.session_hook,
                layout=layout,
                workspace=workspace,
                events=events,
            )
    except ClaudeAdapterError as exc:
        exc.events = _sanitize_events((*events, *exc.events))
        raise
    except (AdapterContentError, ManagedAdapterWorkspaceError) as exc:
        raise ClaudeAdapterError(str(exc), events=tuple(events)) from exc

    return ClaudeAdapterResult(
        action=action,
        project_root=context.root,
        profile_path=context.profile_path,
        task_pack_path=context.task_pack_path,
        mode=mode,
        events=tuple(events),
    )


def _request_policy(
    request: ClaudeAdapterRequest,
) -> Tuple[ClaudeAction, ClaudeInstallMode, bool, bool]:
    if isinstance(request, ClaudeInstallRequest):
        if not isinstance(request.mode, ClaudeInstallMode):
            raise ClaudeAdapterInputError("Invalid Claude adapter install mode.")
        action = ClaudeAction.INSTALL
        mode = request.mode
    elif isinstance(request, ClaudeUninstallRequest):
        action = ClaudeAction.UNINSTALL
        mode = ClaudeInstallMode.LOCAL
    else:
        raise ClaudeAdapterInputError("Invalid Claude adapter request.")

    for name in ("dry_run", "force", "global_reminder", "session_hook"):
        if not isinstance(getattr(request, name), bool):
            raise ClaudeAdapterInputError(
                f"Claude adapter {name} policy must be boolean."
            )
    if not isinstance(request.requested_project, Path):
        raise ClaudeAdapterInputError(
            "Claude adapter requested project must be a Path."
        )
    if not isinstance(request.kit_home, Path):
        raise ClaudeAdapterInputError("Claude adapter kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise ClaudeAdapterInputError(
            "Claude adapter explicit Profile must be text."
        )
    if not isinstance(request.environment, Mapping):
        raise ClaudeAdapterInputError(
            "Claude adapter environment must be a mapping."
        )
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise ClaudeAdapterInputError(
            "Claude adapter environment keys and values must be text."
        )
    return action, mode, request.dry_run, request.force


def _validate_pre_resolved_context(
    *,
    context: TargetProjectContext,
    requested_project: Path,
    kit_home: Path,
    explicit_profile: Optional[str],
    environment: Mapping[str, str],
) -> None:
    """Reject a context that was resolved for a different invocation."""

    if not isinstance(context, TargetProjectContext):
        raise ClaudeAdapterInputError(
            "Claude adapter context must be a TargetProjectContext."
        )
    if not isinstance(context.root, Path) or not isinstance(
        context.profile_path, str
    ):
        raise ClaudeAdapterInputError(
            "Claude adapter context has invalid project or Profile fields."
        )
    if not requested_project.is_dir():
        raise ClaudeAdapterInputError(
            f"Project directory not found: {requested_project}"
        )
    requested_root, _ = resolve_project_root_identity(
        requested_project, environment
    )
    if canonical_path(context.root) != requested_root:
        raise ClaudeAdapterInputError(
            "Claude adapter context does not match the requested project."
        )
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
        raise ClaudeAdapterInputError(
            "Claude adapter context does not match the requested Profile or kit."
        )
    _validate_context_kit(context, kit_home)


def _canonical_profile_path(value: str) -> Path:
    return canonical_path(Path(os.path.abspath(value)))


def _validate_context_kit(
    context: TargetProjectContext, kit_home: Path
) -> None:
    resolved_kit = context.profile_environment.get("AGENT_RAILS_HOME", "")
    if resolved_kit and canonical_path(Path(resolved_kit)) != kit_home:
        raise ClaudeAdapterInputError(
            "Claude adapter context does not match the requested Profile or kit."
        )


def _build_layout(
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
) -> _ClaudeLayout:
    claude_dir = context.root / ".claude"
    commands_dir = claude_dir / "commands"
    local_ignore_path = context.root / ".gitignore"
    if context.is_git_repo:
        try:
            completed = run_git(
                context.root,
                ("rev-parse", "--git-path", "info/exclude"),
                environment=environment,
            )
        except OSError as exc:
            raise ClaudeAdapterError(
                "Unable to resolve the Target Project local Git exclude file."
            ) from exc
        if completed.returncode != 0 or not completed.stdout.strip():
            raise ClaudeAdapterError(
                "Unable to resolve the Target Project local Git exclude file."
            )
        candidate = Path(completed.stdout.strip())
        local_ignore_path = (
            candidate if candidate.is_absolute() else context.root / candidate
        )

    profile_values = context.profile_values
    user_rules_value = profile_values.get(
        "AGENT_RAILS_CLAUDE_USER_MD",
        environment.get("AGENT_RAILS_CLAUDE_USER_MD", ""),
    )
    settings_value = profile_values.get(
        "AGENT_RAILS_CLAUDE_SETTINGS",
        environment.get("AGENT_RAILS_CLAUDE_SETTINGS", ""),
    )
    home = environment.get("HOME", "")
    if not user_rules_value:
        if not home:
            raise ClaudeAdapterError(
                "HOME is required to resolve the Claude user rules path."
            )
        user_rules_value = str(Path(home) / ".claude" / "CLAUDE.md")
    if not settings_value:
        if not home:
            raise ClaudeAdapterError(
                "HOME is required to resolve the Claude settings path."
            )
        settings_value = str(Path(home) / ".claude" / "settings.json")

    return _ClaudeLayout(
        claude_dir=claude_dir,
        skills_dir=claude_dir / "skills",
        commands_dir=commands_dir,
        guide_path=claude_dir / "AGENT_RAILS.md",
        pack_command_path=commands_dir / "agent-rails-pack.md",
        lite_command_path=commands_dir / "agent-rails-lite.md",
        check_command_path=commands_dir / "agent-rails-check.md",
        managed_skills_path=claude_dir / ".agent-rails-managed-skills",
        project_rules_path=context.root / "CLAUDE.md",
        local_rules_path=context.root / "CLAUDE.local.md",
        local_ignore_path=Path(os.path.abspath(local_ignore_path)),
        project_ignore_path=context.root / ".gitignore",
        user_rules_path=_environment_path(user_rules_value, home),
        settings_path=_environment_path(settings_value, home),
        session_hook_path=kit_home / "hooks" / _HOOK_BASENAME,
    )


def _install(
    *,
    context: TargetProjectContext,
    kit_home: Path,
    version: str,
    mode: ClaudeInstallMode,
    dry_run: bool,
    force: bool,
    global_reminder: bool,
    session_hook: bool,
    layout: _ClaudeLayout,
    workspace: ManagedAdapterWorkspace,
    events: list[ClaudeEvent],
) -> None:
    executable = (
        "agent-rails"
        if mode is ClaudeInstallMode.PROJECT
        else str(kit_home / "bin" / "agent-rails")
    )
    profile = "" if mode is ClaudeInstallMode.PROJECT else context.profile_path
    content_request = AdapterContentRequest(
        adapter=AdapterType.CLAUDE,
        version=version,
        executable=executable,
        profile=profile,
    )
    generated = {
        layout.guide_path: render_adapter_content(
            content_request, AdapterArtifact.GUIDE
        ),
        layout.pack_command_path: render_adapter_content(
            content_request, AdapterArtifact.PACK
        ),
        layout.lite_command_path: render_adapter_content(
            content_request, AdapterArtifact.LITE
        ),
        layout.check_command_path: render_adapter_content(
            content_request, AdapterArtifact.CHECK
        ),
    }
    rules_block = render_adapter_content(
        content_request, AdapterArtifact.CLAUDE_BLOCK
    )

    # Complete every fallible input/state validation before the first write.
    for path in generated:
        _preflight_generated_install(path, workspace)
    _preflight_project_file_write(
        layout.managed_skills_path,
        context.root,
        "managed skill inventory",
    )
    _preflight_project_directory_write(
        layout.skills_dir,
        context.root,
        "managed skill directory",
    )
    workspace.validate_ignore_path(layout.local_ignore_path)
    _validate_ignore_block(layout.local_ignore_path)
    target_rules = (
        layout.local_rules_path
        if mode is ClaudeInstallMode.LOCAL
        else layout.project_rules_path
    )
    tracked_local_rules = (
        mode is ClaudeInstallMode.LOCAL
        and not force
        and workspace.is_tracked_file(target_rules)
    )
    target_plan = (
        _TextPlan(
            path=target_rules,
            content=None,
            changed=False,
            messages=(
                f"Keeping tracked file in local mode: {target_rules}",
            ),
        )
        if tracked_local_rules
        else _prepare_block_install(
            target_rules,
            _RULES_MARKER,
            _RULES_END_MARKER,
            rules_block,
            "Agent Rails block",
        )
    )
    stale_local_plan = _TextPlan(
        path=layout.local_rules_path,
        content=None,
        changed=False,
        messages=(),
    )
    if mode is ClaudeInstallMode.PROJECT:
        stale_local_plan = _prepare_block_removal(
            layout.local_rules_path,
            _RULES_MARKER,
            _RULES_END_MARKER,
            "local Agent Rails block",
        )
    _preflight_project_text_plan(target_plan, context.root)
    _preflight_project_text_plan(stale_local_plan, context.root)

    global_plan: Optional[_TextPlan] = None
    if global_reminder:
        _validate_external_path(
            layout.user_rules_path,
            "Claude user rules",
            require_writable=not dry_run,
        )
        global_plan = _prepare_block_install(
            layout.user_rules_path,
            _GLOBAL_MARKER,
            _GLOBAL_END_MARKER,
            _GLOBAL_REMINDER_BLOCK,
            "global Agent Rails reminder",
            replace_existing=force,
        )

    settings_plan: Optional[_SettingsPlan] = None
    if session_hook:
        _validate_session_hook(layout.session_hook_path)
        _validate_external_path(
            layout.settings_path,
            "Claude settings",
            require_writable=not dry_run,
        )
        settings_plan = _prepare_settings_install(
            layout.settings_path, layout.session_hook_path
        )
    _validate_external_collisions(layout, global_reminder, session_hook)

    _stdout(events, "Agent Rails Claude Install")
    _stdout(events, f"Version: {version}")
    _stdout(events, f"Project: {context.root}")
    _stdout(events, f"Profile: {context.profile_path}")
    _stdout(events, f"Mode: {mode.value}")
    _stdout_many(events, workspace.install_skills())
    for path in (
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        _stdout_many(events, workspace.write_generated_file(path, generated[path]))
    _stdout_many(events, workspace.write_managed_skills())
    if mode is ClaudeInstallMode.PROJECT:
        _apply_project_text_plan(stale_local_plan, workspace, dry_run, events)
    _apply_project_text_plan(target_plan, workspace, dry_run, events)
    if global_plan is not None:
        _apply_external_text_plan(global_plan, dry_run, events)
    if settings_plan is not None:
        _apply_settings_plan(settings_plan, dry_run, events)

    if mode is ClaudeInstallMode.LOCAL:
        _stdout_many(
            events,
            workspace.ensure_ignore_block(
                layout.local_ignore_path,
                _IGNORE_MARKER,
                _IGNORE_END_MARKER,
                _IGNORE_ENTRIES,
                cleanup_only_entries=_IGNORE_CLEANUP_ENTRIES,
            ),
        )
    else:
        _stdout_many(
            events,
            workspace.remove_ignore_block(
                layout.local_ignore_path,
                _IGNORE_MARKER,
                _IGNORE_END_MARKER,
                "Would remove Agent Rails local ignore block from",
                "Removed Agent Rails local ignore block from",
                _ALL_IGNORE_ENTRIES,
            ),
        )

    _stdout(events, "")
    _stdout(events, "Claude adapter ready.")
    _stdout(events, f"Mode: {mode.value}")
    _stdout(events, f"Version: {version}")
    _stdout(events, f"Project: {context.root}")
    _stdout(events, f"Profile: {context.profile_path}")
    _stdout(events, f"Task Pack: {context.task_pack_path}")
    if global_reminder:
        _stdout(events, f"Global Reminder: {layout.user_rules_path}")
    if session_hook:
        _stdout(events, f"Session Hook: {layout.settings_path}")


def _uninstall(
    *,
    context: TargetProjectContext,
    dry_run: bool,
    force: bool,
    global_reminder: bool,
    session_hook: bool,
    layout: _ClaudeLayout,
    workspace: ManagedAdapterWorkspace,
    events: list[ClaudeEvent],
) -> None:
    ignore_paths = tuple(
        dict.fromkeys((layout.local_ignore_path, layout.project_ignore_path))
    )
    for path in ignore_paths:
        workspace.validate_ignore_path(path)
        _validate_ignore_block(path)
    for path in (
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        _preflight_generated_removal(path, workspace)
    _preflight_inventory_removal(layout.managed_skills_path, workspace)
    _preflight_managed_skill_removal(layout.skills_dir, workspace)
    workspace.preflight_removal()
    local_rules_plan = _prepare_block_removal(
        layout.local_rules_path,
        _RULES_MARKER,
        _RULES_END_MARKER,
        "Agent Rails block",
    )
    project_rules_plan = _prepare_block_removal(
        layout.project_rules_path,
        _RULES_MARKER,
        _RULES_END_MARKER,
        "Agent Rails block",
    )
    _preflight_project_text_plan(local_rules_plan, context.root)
    _preflight_project_text_plan(project_rules_plan, context.root)
    global_plan: Optional[_TextPlan] = None
    if global_reminder:
        _validate_external_path(
            layout.user_rules_path,
            "Claude user rules",
            require_writable=not dry_run,
        )
        global_plan = _prepare_block_removal(
            layout.user_rules_path,
            _GLOBAL_MARKER,
            _GLOBAL_END_MARKER,
            "global Agent Rails reminder",
        )
    settings_plan: Optional[_SettingsPlan] = None
    if session_hook:
        _validate_external_path(
            layout.settings_path,
            "Claude settings",
            require_writable=not dry_run,
        )
        settings_plan = _prepare_settings_removal(layout.settings_path)
    _validate_external_collisions(layout, global_reminder, session_hook)

    _stdout(events, "Agent Rails Claude Uninstall")
    for path in (
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        _stdout_many(events, workspace.remove_generated_file(path))
    _stdout_many(events, workspace.remove_managed_skills())
    _stdout_many(events, workspace.remove_managed_skills_file())
    _apply_project_text_plan(local_rules_plan, workspace, dry_run, events)
    _apply_project_text_plan(project_rules_plan, workspace, dry_run, events)
    if global_plan is not None:
        _apply_external_text_plan(global_plan, dry_run, events)
    if settings_plan is not None:
        _apply_settings_plan(settings_plan, dry_run, events)

    if workspace.removal_has_survivors:
        _stdout(
            events,
            "Keeping local ignore entries for preserved managed skills.",
        )
    else:
        for path in ignore_paths:
            _stdout_many(
                events,
                workspace.remove_ignore_block(
                    path,
                    _IGNORE_MARKER,
                    _IGNORE_END_MARKER,
                    "Would remove Agent Rails local ignore block from",
                    "Removed Agent Rails local ignore block from",
                    _ALL_IGNORE_ENTRIES,
                ),
            )
    if not dry_run:
        for path in (layout.commands_dir, layout.skills_dir, layout.claude_dir):
            try:
                path.rmdir()
            except OSError:
                pass

    _stdout(events, "")
    _stdout(events, "Claude adapter removed.")
    _stdout(events, f"Project: {context.root}")
    if global_reminder:
        _stdout(events, f"Global Reminder: {layout.user_rules_path}")
    if session_hook:
        _stdout(events, f"Session Hook: {layout.settings_path}")


def _prepare_block_install(
    path: Path,
    marker: str,
    end_marker: str,
    block: str,
    label: str,
    *,
    replace_existing: bool = True,
) -> _TextPlan:
    content = _read_optional_text(path, label)
    bounds = _block_bounds(content, marker, end_marker, path, label)
    if bounds is None:
        rendered = content + ("\n" if content else "") + block.rstrip("\n") + "\n"
        return _TextPlan(
            path=path,
            content=rendered,
            changed=True,
            messages=(f"Appended {label} to {path}",),
        )
    if not replace_existing:
        return _TextPlan(
            path=path,
            content=content,
            changed=False,
            messages=(
                f"Global Agent Rails reminder already exists: {path}",
                "Use --force to replace it.",
            ),
        )
    start, end, lines = bounds
    rendered = "".join(lines[:start]) + block.rstrip("\n") + "\n" + "".join(
        lines[end + 1 :]
    )
    return _TextPlan(
        path=path,
        content=rendered,
        changed=rendered != content,
        messages=(f"Replaced {label} in {path}",),
    )


def _prepare_block_removal(
    path: Path,
    marker: str,
    end_marker: str,
    label: str,
) -> _TextPlan:
    content = _read_optional_text(path, label)
    bounds = _block_bounds(content, marker, end_marker, path, label)
    if bounds is None:
        return _TextPlan(path=path, content=content, changed=False, messages=())
    start, end, lines = bounds
    rendered = "".join((*lines[:start], *lines[end + 1 :]))
    remove_file = not rendered.strip()
    message = (
        f"Removed empty {path}"
        if remove_file
        else f"Removed {label} from {path}"
    )
    return _TextPlan(
        path=path,
        content=None if remove_file else rendered,
        changed=True,
        messages=(message,),
    )


def _block_bounds(
    content: str,
    marker: str,
    end_marker: str,
    path: Path,
    label: str,
) -> Optional[Tuple[int, int, list[str]]]:
    lines = content.splitlines(keepends=True)
    values = [line.rstrip("\r\n") for line in lines]
    starts = [index for index, value in enumerate(values) if value == marker]
    ends = [index for index, value in enumerate(values) if value == end_marker]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
        raise ClaudeAdapterError(f"Malformed {label} markers in {path}")
    return starts[0], ends[0], lines


def _validate_ignore_block(path: Path) -> None:
    content = _read_optional_text(
        path,
        "local ignore file",
        errors="surrogateescape",
    )
    bounds = _block_bounds(
        content,
        _IGNORE_MARKER,
        _IGNORE_END_MARKER,
        path,
        "Agent Rails local ignore block",
    )
    if bounds is None:
        return
    start, end, lines = bounds
    allowed = set(_ALL_IGNORE_ENTRIES)
    values = [line.rstrip("\r\n") for line in lines[start + 1 : end]]
    if any(value not in allowed for value in values):
        raise ClaudeAdapterError(
            f"Malformed Agent Rails local ignore block in {path}"
        )


def _prepare_settings_install(path: Path, hook_path: Path) -> _SettingsPlan:
    settings = _load_settings(path)
    updated = copy.deepcopy(settings)
    _remove_existing_session_hook(updated)
    hooks = updated.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ClaudeAdapterError(
            f"Claude settings key 'hooks' must be a JSON object: {path}"
        )
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise ClaudeAdapterError(
            f"Claude settings hooks.SessionStart must be a JSON array: {path}"
        )
    groups.append(
        {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": f"bash {shlex.quote(str(hook_path))} ; exit 0",
                    "timeout": 5,
                    "statusMessage": "Loading Agent Rails...",
                }
            ],
        }
    )
    return _SettingsPlan(
        path=path,
        data=updated,
        changed=updated != settings,
        messages=(
            f"Installed Agent Rails SessionStart hook: {hook_path}",
            f"Wrote {path}",
        ),
    )


def _prepare_settings_removal(path: Path) -> _SettingsPlan:
    settings = _load_settings(path)
    updated = copy.deepcopy(settings)
    changed = _remove_existing_session_hook(updated)
    message = (
        f"Removed Agent Rails SessionStart hook from {path}"
        if changed
        else f"Agent Rails SessionStart hook not present in {path}"
    )
    messages = (message, f"Wrote {path}") if changed else (message,)
    return _SettingsPlan(
        path=path,
        data=updated,
        changed=changed,
        messages=messages,
    )


def _load_settings(path: Path) -> dict[str, Any]:
    content = _read_optional_text(path, "Claude settings", encoding="utf-8-sig")
    if not content.strip():
        return {}
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ClaudeAdapterError(f"Invalid Claude settings JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ClaudeAdapterError(f"Claude settings must be a JSON object: {path}")
    hooks = data.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        raise ClaudeAdapterError(
            f"Claude settings key 'hooks' must be a JSON object: {path}"
        )
    if isinstance(hooks, dict):
        groups = hooks.get("SessionStart")
        if groups is not None and not isinstance(groups, list):
            raise ClaudeAdapterError(
                f"Claude settings hooks.SessionStart must be a JSON array: {path}"
            )
    return data


def _remove_existing_session_hook(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("SessionStart")
    if not isinstance(groups, list):
        return False
    changed = False
    next_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            next_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            next_groups.append(group)
            continue
        next_handlers = [
            handler
            for handler in handlers
            if not (
                isinstance(handler, dict)
                and _HOOK_BASENAME in str(handler.get("command", ""))
            )
        ]
        if len(next_handlers) != len(handlers):
            changed = True
        if next_handlers:
            next_group = dict(group)
            next_group["hooks"] = next_handlers
            next_groups.append(next_group)
    if changed:
        if next_groups:
            hooks["SessionStart"] = next_groups
        else:
            hooks.pop("SessionStart", None)
        if not hooks:
            settings.pop("hooks", None)
    return changed


def _apply_project_text_plan(
    plan: _TextPlan,
    workspace: ManagedAdapterWorkspace,
    dry_run: bool,
    events: list[ClaudeEvent],
) -> None:
    if not plan.changed:
        _stdout_many(events, plan.messages)
        return
    if dry_run:
        for message in plan.messages:
            if message.startswith("Appended "):
                _stdout(events, "Would append " + message[len("Appended ") :])
            elif message.startswith("Replaced "):
                _stdout(events, "Would replace " + message[len("Replaced ") :])
            elif message.startswith("Removed empty "):
                _stdout(events, "Would remove " + message[len("Removed empty ") :])
            elif message.startswith("Removed "):
                _stdout(events, "Would remove " + message[len("Removed ") :])
        return
    if plan.content is None:
        workspace.unlink_managed_file(plan.path)
    else:
        workspace.replace_text_file(plan.path, plan.content)
    _stdout_many(events, plan.messages)


def _apply_external_text_plan(
    plan: _TextPlan,
    dry_run: bool,
    events: list[ClaudeEvent],
) -> None:
    if not plan.changed:
        _stdout_many(events, plan.messages)
        return
    if dry_run:
        for message in plan.messages:
            if message.startswith("Appended "):
                _stdout(events, "Would append " + message[len("Appended ") :])
            elif message.startswith("Replaced "):
                _stdout(events, "Would replace " + message[len("Replaced ") :])
            elif message.startswith("Removed empty "):
                _stdout(events, "Would remove " + message[len("Removed empty ") :])
            elif message.startswith("Removed "):
                _stdout(events, "Would remove " + message[len("Removed ") :])
        return
    if plan.content is None:
        _unlink_external_file(plan.path)
    else:
        _atomic_write_external_text(plan.path, plan.content)
    _stdout_many(events, plan.messages)


def _apply_settings_plan(
    plan: _SettingsPlan,
    dry_run: bool,
    events: list[ClaudeEvent],
) -> None:
    if dry_run:
        first = plan.messages[0]
        if first.startswith("Installed "):
            first = "Would install " + first[len("Installed ") :]
        elif first.startswith("Removed "):
            first = "Would remove " + first[len("Removed ") :]
        _stdout(events, first)
        if plan.changed:
            _stdout(events, f"Would write {plan.path}")
        return
    if plan.changed:
        rendered = json.dumps(plan.data, indent=2, ensure_ascii=False) + "\n"
        _atomic_write_external_text(plan.path, rendered)
    _stdout_many(events, plan.messages)


def _preflight_generated_install(
    path: Path,
    workspace: ManagedAdapterWorkspace,
) -> None:
    mode, writable_parent = _inspect_project_file(
        workspace.config.project,
        path,
        "generated Claude artifact",
    )
    if mode is not None and not stat.S_ISREG(mode):
        raise ClaudeAdapterError(
            f"Generated Claude artifact is not a regular file: {path}"
        )
    if (
        workspace.config.protect_tracked
        and not workspace.config.force
        and workspace.is_tracked_file(path)
    ):
        return
    if (
        mode is not None
        and not workspace.config.force
        and not workspace.is_generated_file(path)
    ):
        return
    _require_project_parent_writable(
        writable_parent,
        "generated Claude artifact",
        path,
    )


def _preflight_generated_removal(
    path: Path,
    workspace: ManagedAdapterWorkspace,
) -> None:
    mode, writable_parent = _inspect_project_file(
        workspace.config.project,
        path,
        "generated Claude artifact",
        allow_leaf_symlink=True,
    )
    if mode is None:
        return
    if stat.S_ISLNK(mode):
        if workspace.config.force:
            _require_project_parent_writable(
                writable_parent,
                "generated Claude artifact",
                path,
            )
        return
    if not stat.S_ISREG(mode):
        if workspace.config.force:
            raise ClaudeAdapterError(
                f"Generated Claude artifact is not a removable regular file: {path}"
            )
        return
    if workspace.config.force or workspace.is_generated_file(path):
        _require_project_parent_writable(
            writable_parent,
            "generated Claude artifact",
            path,
        )


def _preflight_inventory_removal(
    path: Path,
    workspace: ManagedAdapterWorkspace,
) -> None:
    mode, writable_parent = _inspect_project_file(
        workspace.config.project,
        path,
        "managed skill inventory",
    )
    if mode is None:
        return
    if not stat.S_ISREG(mode):
        raise ClaudeAdapterError(
            f"Managed skill inventory is not a regular file: {path}"
        )
    _require_project_parent_writable(
        writable_parent,
        "managed skill inventory",
        path,
    )


def _preflight_managed_skill_removal(
    path: Path,
    workspace: ManagedAdapterWorkspace,
) -> None:
    if not workspace.state.managed_skills:
        return
    mode, writable_parent = _inspect_project_file(
        workspace.config.project,
        path,
        "managed skill directory",
    )
    if mode is None:
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ClaudeAdapterError(
            f"Managed skill root is not a directory: {path}"
        )
    _require_project_parent_writable(
        writable_parent,
        "managed skill directory",
        path,
    )
    if not os.access(path, os.W_OK | os.X_OK):
        raise ClaudeAdapterError(
            f"Managed skill directory is not writable: {path}"
        )


def _preflight_project_file_write(
    path: Path,
    project: Path,
    label: str,
) -> None:
    mode, writable_parent = _inspect_project_file(project, path, label)
    if mode is not None and not stat.S_ISREG(mode):
        raise ClaudeAdapterError(f"{label.capitalize()} is not a regular file: {path}")
    _require_project_parent_writable(writable_parent, label, path)


def _preflight_project_directory_write(
    path: Path,
    project: Path,
    label: str,
) -> None:
    mode, writable_parent = _inspect_project_file(
        project,
        path,
        label,
        allow_leaf_directory=True,
    )
    if mode is None:
        _require_project_parent_writable(writable_parent, label, path)
        return
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ClaudeAdapterError(f"{label.capitalize()} is not a directory: {path}")
    if not os.access(path, os.W_OK | os.X_OK):
        raise ClaudeAdapterError(f"{label.capitalize()} is not writable: {path}")


def _preflight_project_text_plan(plan: _TextPlan, project: Path) -> None:
    if not plan.changed:
        return
    mode, writable_parent = _inspect_project_file(
        project,
        plan.path,
        "Claude rules file",
    )
    if mode is not None and not stat.S_ISREG(mode):
        raise ClaudeAdapterError(
            f"Claude rules file is not a regular file: {plan.path}"
        )
    _require_project_parent_writable(
        writable_parent,
        "Claude rules file",
        plan.path,
    )


def _inspect_project_file(
    project: Path,
    path: Path,
    label: str,
    *,
    allow_leaf_symlink: bool = False,
    allow_leaf_directory: bool = False,
) -> Tuple[Optional[int], Path]:
    try:
        relative = path.relative_to(project)
    except ValueError as exc:
        raise ClaudeAdapterError(f"{label.capitalize()} is outside the project: {path}") from exc
    if relative == Path("."):
        raise ClaudeAdapterError(f"{label.capitalize()} cannot replace the project")
    current = project
    for part in relative.parts[:-1]:
        candidate = current / part
        try:
            mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            return None, current
        except OSError as exc:
            raise ClaudeAdapterError(
                f"Unable to inspect {label} parent: {candidate}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ClaudeAdapterError(
                f"Refusing symbolic-link {label} parent: {candidate}"
            )
        if not stat.S_ISDIR(mode):
            raise ClaudeAdapterError(f"{label.capitalize()} parent is not a directory: {candidate}")
        current = candidate
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return None, current
    except OSError as exc:
        raise ClaudeAdapterError(f"Unable to inspect {label}: {path}") from exc
    if stat.S_ISLNK(mode) and not allow_leaf_symlink:
        raise ClaudeAdapterError(f"Refusing symbolic-link {label}: {path}")
    if stat.S_ISDIR(mode) and not allow_leaf_directory:
        return mode, current
    return mode, current


def _require_project_parent_writable(
    parent: Path,
    label: str,
    path: Path,
) -> None:
    if not os.access(parent, os.W_OK | os.X_OK):
        raise ClaudeAdapterError(
            f"{label.capitalize()} parent is not writable: {path.parent}"
        )


def _read_optional_text(
    path: Path,
    label: str,
    *,
    encoding: str = "utf-8",
    errors: str = "strict",
) -> str:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return ""
    except OSError as exc:
        raise ClaudeAdapterError(f"Unable to inspect {label}: {path}") from exc
    if stat.S_ISLNK(mode):
        raise ClaudeAdapterError(f"Refusing symbolic-link {label}: {path}")
    if not stat.S_ISREG(mode):
        raise ClaudeAdapterError(f"{label.capitalize()} is not a regular file: {path}")
    try:
        return path.read_text(encoding=encoding, errors=errors)
    except (OSError, UnicodeError) as exc:
        raise ClaudeAdapterError(f"Unable to read {label}: {path}") from exc


def _validate_session_hook(path: Path) -> None:
    try:
        mode = os.lstat(path).st_mode
    except OSError as exc:
        raise ClaudeAdapterError(
            f"Session hook script is missing or not executable: {path}"
        ) from exc
    if (
        stat.S_ISLNK(mode)
        or not stat.S_ISREG(mode)
        or not os.access(path, os.X_OK)
    ):
        raise ClaudeAdapterError(
            f"Session hook script is missing or not executable: {path}"
        )


def _validate_external_collisions(
    layout: _ClaudeLayout,
    global_reminder: bool,
    session_hook: bool,
) -> None:
    selected: list[tuple[str, Path]] = []
    if global_reminder:
        selected.append(("Claude user rules", layout.user_rules_path))
    if session_hook:
        selected.append(("Claude settings", layout.settings_path))
    project_paths = {
        layout.local_rules_path,
        layout.project_rules_path,
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
        layout.managed_skills_path,
    }
    seen: dict[Path, str] = {}
    for label, path in selected:
        if path in project_paths:
            raise ClaudeAdapterError(
                f"{label} path collides with a project adapter path: {path}"
            )
        previous = seen.get(path)
        if previous is not None:
            raise ClaudeAdapterError(
                f"{label} path collides with {previous}: {path}"
            )
        seen[path] = label


def _validate_external_path(
    path: Path,
    label: str,
    *,
    require_writable: bool = True,
) -> None:
    if not path.is_absolute():
        raise ClaudeAdapterError(f"{label} path must be absolute: {path}")
    current = Path(path.anchor)
    parts = path.parts[1:]
    for part in parts[:-1]:
        candidate = current / part
        try:
            mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            if require_writable and not os.access(current, os.W_OK | os.X_OK):
                raise ClaudeAdapterError(
                    f"{label} parent is not writable: {current}"
                )
            return
        except OSError as exc:
            raise ClaudeAdapterError(
                f"Unable to validate {label} parent: {candidate}"
            ) from exc
        if stat.S_ISLNK(mode):
            raise ClaudeAdapterError(
                f"Refusing symbolic-link {label} parent: {candidate}"
            )
        if not stat.S_ISDIR(mode):
            raise ClaudeAdapterError(f"{label} parent is not a directory: {candidate}")
        current = candidate
    if require_writable and not os.access(current, os.W_OK | os.X_OK):
        raise ClaudeAdapterError(f"{label} parent is not writable: {current}")
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ClaudeAdapterError(f"Unable to validate {label}: {path}") from exc
    if stat.S_ISLNK(mode):
        raise ClaudeAdapterError(f"Refusing symbolic-link {label}: {path}")
    if not stat.S_ISREG(mode):
        raise ClaudeAdapterError(f"{label} is not a regular file: {path}")


def _environment_path(value: str, home: str) -> Path:
    if value == "~" or value.startswith("~/"):
        if not home:
            raise ClaudeAdapterError("HOME is required to expand a Claude path.")
        value = str(Path(home) / value[2:]) if value != "~" else home
    path = Path(os.path.abspath(value))
    # macOS exposes /var as a stable system symlink to /private/var.  Canonicalize
    # only that first, trusted filesystem component; deeper user-controlled
    # symlink parents and the leaf remain visible to the no-follow preflight.
    if len(path.parts) > 1:
        first = Path(path.anchor) / path.parts[1]
        try:
            first_mode = os.lstat(first).st_mode
        except OSError:
            first_mode = 0
        if stat.S_ISLNK(first_mode):
            canonical_first = Path(os.path.realpath(first))
            path = canonical_first.joinpath(*path.parts[2:])
    return path


def _open_external_parent(path: Path, *, create: bool) -> Tuple[int, str]:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    if no_follow == 0:
        raise ClaudeAdapterError(
            "This platform cannot provide no-follow Claude adapter writes."
        )
    flags = os.O_RDONLY | no_follow | directory | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path.anchor or os.sep, flags)
    try:
        for part in path.parts[1:-1]:
            try:
                child = os.open(part, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=0o777, dir_fd=descriptor)
                child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, path.name
    except BaseException:
        os.close(descriptor)
        raise


def _atomic_write_external_text(path: Path, content: str) -> None:
    payload = content.encode("utf-8")
    parent, name = _open_external_parent(path, create=True)
    temporary = ""
    descriptor = -1
    try:
        existing_mode: Optional[int] = None
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
                raise ClaudeAdapterError(
                    f"Refusing unsafe Claude adapter target: {path}"
                )
            existing_mode = stat.S_IMODE(current.st_mode)
        for _ in range(128):
            temporary = f".{name}.agent-rails-{secrets.token_hex(8)}"
            try:
                descriptor = os.open(
                    temporary,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_CLOEXEC", 0),
                    0o666,
                    dir_fd=parent,
                )
                break
            except FileExistsError:
                continue
        if descriptor < 0:
            raise ClaudeAdapterError(
                f"Unable to allocate temporary Claude adapter file: {path}"
            )
        if existing_mode is not None:
            os.fchmod(descriptor, existing_mode)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        temporary = ""
        os.fsync(parent)
    except ClaudeAdapterError:
        raise
    except OSError as exc:
        raise ClaudeAdapterError(f"Unable to write Claude adapter file: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=parent)
            except OSError:
                pass
        os.close(parent)


def _unlink_external_file(path: Path) -> None:
    try:
        parent, name = _open_external_parent(path, create=False)
    except FileNotFoundError:
        return
    try:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode):
            raise ClaudeAdapterError(f"Refusing unsafe Claude adapter target: {path}")
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    except ClaudeAdapterError:
        raise
    except OSError as exc:
        raise ClaudeAdapterError(f"Unable to remove Claude adapter file: {path}") from exc
    finally:
        os.close(parent)


def _resolve_version(kit_home: Path, environment: Mapping[str, str]) -> str:
    override = environment.get("AGENT_RAILS_VERSION_OVERRIDE", "")
    if override:
        return override
    path = kit_home / "VERSION"
    if not path.is_file():
        return "0.0.0-dev"
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if fields:
                return fields[0]
    except (OSError, UnicodeError) as exc:
        raise ClaudeAdapterError(f"Unable to read Agent Rails version: {path}") from exc
    return ""


def _stdout(events: list[ClaudeEvent], text: str) -> None:
    events.append(ClaudeEvent(ClaudeEventStream.STDOUT, text))


def _stdout_many(events: list[ClaudeEvent], messages: Tuple[str, ...]) -> None:
    for message in messages:
        _stdout(events, message)


def _sanitize_events(events: Tuple[ClaudeEvent, ...]) -> Tuple[ClaudeEvent, ...]:
    return tuple(
        ClaudeEvent(event.stream, _terminal_literal(str(event.text)))
        for event in events
    )


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


def _render_event_stream(
    events: Tuple[ClaudeEvent, ...], stream: ClaudeEventStream
) -> str:
    selected = [event.text for event in events if event.stream is stream]
    return "" if not selected else "\n".join(selected) + "\n"
