"""OpenCode local-adapter application lifecycle."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from enum import Enum
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
from typing import Mapping, Optional, Tuple, Union

from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectContextMismatch,
    TargetProjectError,
    resolve_target_project,
    validate_target_project_context,
)
from agent_rails.core.terminal import terminal_literal as _terminal_literal

from .content import (
    AdapterArtifact,
    AdapterContentError,
    AdapterContentRequest,
    AdapterType,
    render_adapter_content,
)
from .events import (
    AdapterError,
    AdapterEvent as OpenCodeEvent,
    AdapterEventStream as OpenCodeEventStream,
    AdapterOutput,
    append_stdout as _stdout,
    append_stdout_many as _stdout_many,
    sanitize_events as _sanitize_events,
)
from .workspace import (
    ManagedAdapterWorkspace,
    ManagedAdapterWorkspaceConfig,
    ManagedAdapterWorkspaceError,
    resolve_local_ignore_path,
)


OPENCODE_PROFILE_VARIABLES = (
    "AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE",
    "AGENT_RAILS_TOKENIZER",
    "AGENT_RAILS_TOKENIZER_CMD",
    "AGENT_RAILS_TOKENIZER_PATH",
    "AGENT_RAILS_TIKTOKEN_ENCODING",
    "AGENT_RAILS_OPENCODE_CONTEXT_PERCENT",
    "AGENT_RAILS_OPENCODE_MAX_PACK_TOKENS",
    "AGENT_RAILS_OPENCODE_MIN_PACK_TOKENS",
    "AGENT_RAILS_OPENCODE_RESERVE_PERCENT",
    "AGENT_RAILS_OPENCODE_RESERVE_TOKENS",
    "AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS",
)

_IGNORE_MARKER = "# Agent Rails opencode adapter"
_IGNORE_END_MARKER = "# Agent Rails opencode adapter end"
_IGNORE_ENTRIES = (
    ".opencode/AGENT_RAILS.md",
    ".opencode/.agent-rails-managed-skills",
    ".opencode/.agent-rails-state.json",
    ".opencode/opencode.json",
    ".opencode/plugins/agent-rails.mjs",
    ".opencode/command/agent-rails-pack.md",
    ".opencode/command/agent-rails-lite.md",
    ".opencode/command/agent-rails-check.md",
    ".opencode/skills/agent-*/",
    ".agent-rails/",
)
_PLUGIN_CONFIG_MARKER = "__AGENT_RAILS_CONFIG__"
_POSITIVE_INTEGER = re.compile(r"^[0-9]+$")
_DEFAULT_SCHEMA = "https://opencode.ai/config.json"
_STATE_FORMAT = "agent-rails-opencode-state-v1"


class OpenCodeAdapterError(AdapterError):
    """The OpenCode adapter request could not be completed."""


class OpenCodeAdapterInputError(OpenCodeAdapterError):
    """The caller supplied an invalid typed OpenCode adapter request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class OpenCodeConfigError(OpenCodeAdapterError):
    """An existing OpenCode config or plugin template is invalid."""


class OpenCodeAction(str, Enum):
    INSTALL = "install"
    DOCTOR = "doctor"
    UNINSTALL = "uninstall"


class OpenCodeInstallMode(str, Enum):
    LOCAL = "local"
    PROJECT = "project"


@dataclass(frozen=True)
class OpenCodeInstallRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    mode: OpenCodeInstallMode
    dry_run: bool
    force: bool
    environment: Mapping[str, str]


@dataclass(frozen=True)
class OpenCodeDoctorRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    environment: Mapping[str, str]


@dataclass(frozen=True)
class OpenCodeUninstallRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    dry_run: bool
    force: bool
    environment: Mapping[str, str]


OpenCodeAdapterRequest = Union[
    OpenCodeInstallRequest,
    OpenCodeDoctorRequest,
    OpenCodeUninstallRequest,
]


@dataclass(frozen=True)
class OpenCodeAdapterResult(AdapterOutput):
    action: OpenCodeAction
    project_root: Path
    profile_path: str
    task_pack_path: str
    mode: OpenCodeInstallMode
    events: Tuple[OpenCodeEvent, ...]

@dataclass(frozen=True)
class _OpenCodeLayout:
    opencode_dir: Path
    skills_dir: Path
    commands_dir: Path
    plugins_dir: Path
    guide_path: Path
    pack_command_path: Path
    lite_command_path: Path
    check_command_path: Path
    config_path: Path
    plugin_path: Path
    plugin_template_path: Path
    managed_skills_path: Path
    state_path: Path
    local_ignore_path: Path


@dataclass(frozen=True)
class _OpenCodeOwnershipState:
    config_existed_before_install: bool
    schema_inserted: bool
    inserted_plugin_entries: Tuple[str, ...]


@dataclass(frozen=True)
class _OpenCodeConfigPlan:
    data: Optional[dict]
    state: Optional[_OpenCodeOwnershipState]
    messages: Tuple[str, ...]
    dry_run: bool


@dataclass(frozen=True)
class _OpenCodeConfigRemovalPlan:
    data: Optional[dict]
    remove_config: bool
    remove_state: bool
    messages: Tuple[str, ...]
    dry_run: bool


def run_opencode_adapter(
    request: OpenCodeAdapterRequest,
    *,
    context: Optional[TargetProjectContext] = None,
) -> OpenCodeAdapterResult:
    """Resolve once, then apply one typed OpenCode adapter lifecycle request."""

    action, mode, dry_run, force = _request_policy(request)
    environment = dict(request.environment)
    kit_home = Path(os.path.realpath(os.fspath(request.kit_home)))
    if context is None:
        context = resolve_target_project(
            request.requested_project,
            kit_home=kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            require_profile=True,
            load_profile=True,
            load_environment_file=False,
            profile_variables=OPENCODE_PROFILE_VARIABLES,
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
    events = []  # type: list[OpenCodeEvent]
    try:
        layout = _build_layout(context, kit_home, environment)
        workspace = ManagedAdapterWorkspace(
            ManagedAdapterWorkspaceConfig(
                home=kit_home,
                project=context.root,
                skills_relative_dir=Path(".opencode/skills"),
                guide_path=layout.guide_path,
                pack_command_path=layout.pack_command_path,
                lite_command_path=layout.lite_command_path,
                check_command_path=layout.check_command_path,
                managed_skills_path=layout.managed_skills_path,
                dry_run=dry_run,
                force=force,
                protect_tracked=mode is OpenCodeInstallMode.LOCAL,
            )
        )
        workspace.validate_managed_path(layout.config_path)
        workspace.validate_managed_path(layout.plugin_path)
        workspace.validate_managed_path(layout.state_path)
        for message in workspace.load_managed_skills():
            events.append(OpenCodeEvent(OpenCodeEventStream.STDERR, message))
        if action is OpenCodeAction.INSTALL:
            _install(
                context=context,
                kit_home=kit_home,
                version=version,
                mode=mode,
                environment=environment,
                layout=layout,
                workspace=workspace,
                events=events,
            )
        elif action is OpenCodeAction.DOCTOR:
            _doctor(
                context=context,
                version=version,
                environment=environment,
                layout=layout,
                events=events,
            )
        else:
            _uninstall(
                layout=layout,
                workspace=workspace,
                force=force,
                dry_run=dry_run,
                events=events,
            )
    except OpenCodeAdapterError as exc:
        exc.events = _sanitize_events((*events, *exc.events))
        raise
    except (AdapterContentError, ManagedAdapterWorkspaceError) as exc:
        raise OpenCodeAdapterError(str(exc), events=tuple(events)) from exc

    return OpenCodeAdapterResult(
        action=action,
        project_root=context.root,
        profile_path=context.profile_path,
        task_pack_path=context.task_pack_path,
        mode=mode,
        events=tuple(events),
    )


def _request_policy(
    request: OpenCodeAdapterRequest,
) -> Tuple[OpenCodeAction, OpenCodeInstallMode, bool, bool]:
    if isinstance(request, OpenCodeInstallRequest):
        if not isinstance(request.mode, OpenCodeInstallMode):
            raise OpenCodeAdapterInputError("Invalid OpenCode adapter install mode.")
        action = OpenCodeAction.INSTALL
        mode = request.mode
        dry_run = request.dry_run
        force = request.force
    elif isinstance(request, OpenCodeDoctorRequest):
        action = OpenCodeAction.DOCTOR
        mode = OpenCodeInstallMode.LOCAL
        dry_run = False
        force = False
    elif isinstance(request, OpenCodeUninstallRequest):
        action = OpenCodeAction.UNINSTALL
        mode = OpenCodeInstallMode.LOCAL
        dry_run = request.dry_run
        force = request.force
    else:
        raise OpenCodeAdapterInputError("Invalid OpenCode adapter request.")

    for name, value in (("dry_run", dry_run), ("force", force)):
        if not isinstance(value, bool):
            raise OpenCodeAdapterInputError(
                f"OpenCode adapter {name} policy must be boolean."
            )
    if not isinstance(request.requested_project, Path):
        raise OpenCodeAdapterInputError(
            "OpenCode adapter requested project must be a Path."
        )
    if not isinstance(request.kit_home, Path):
        raise OpenCodeAdapterInputError("OpenCode adapter kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise OpenCodeAdapterInputError(
            "OpenCode adapter explicit Profile must be text."
        )
    if not isinstance(request.environment, Mapping):
        raise OpenCodeAdapterInputError(
            "OpenCode adapter environment must be a mapping."
        )
    return action, mode, dry_run, force


def _validate_pre_resolved_context(
    *,
    context: TargetProjectContext,
    requested_project: Path,
    kit_home: Path,
    explicit_profile: Optional[str],
    environment: Mapping[str, str],
) -> None:
    """Reject a context that was resolved for a different invocation."""

    try:
        validate_target_project_context(
            context,
            requested_project=requested_project,
            kit_home=kit_home,
            explicit_profile=explicit_profile,
            environment=environment,
        )
    except TargetProjectContextMismatch as exc:
        raise OpenCodeAdapterInputError(exc.message("OpenCode adapter")) from exc
    except TargetProjectError as exc:
        raise OpenCodeAdapterInputError(str(exc)) from exc


def _build_layout(
    context: TargetProjectContext,
    kit_home: Path,
    environment: Mapping[str, str],
) -> _OpenCodeLayout:
    opencode_dir = context.root / ".opencode"
    skills_dir = opencode_dir / "skills"
    commands_dir = opencode_dir / "command"
    plugins_dir = opencode_dir / "plugins"
    local_ignore_path = resolve_local_ignore_path(
        context.root,
        is_git_repo=context.is_git_repo,
        environment=environment,
    )
    return _OpenCodeLayout(
        opencode_dir=opencode_dir,
        skills_dir=skills_dir,
        commands_dir=commands_dir,
        plugins_dir=plugins_dir,
        guide_path=opencode_dir / "AGENT_RAILS.md",
        pack_command_path=commands_dir / "agent-rails-pack.md",
        lite_command_path=commands_dir / "agent-rails-lite.md",
        check_command_path=commands_dir / "agent-rails-check.md",
        config_path=opencode_dir / "opencode.json",
        plugin_path=plugins_dir / "agent-rails.mjs",
        plugin_template_path=kit_home / "templates" / "opencode-agent-rails-plugin.mjs",
        managed_skills_path=opencode_dir / ".agent-rails-managed-skills",
        state_path=opencode_dir / ".agent-rails-state.json",
        local_ignore_path=local_ignore_path,
    )


def _install(
    *,
    context: TargetProjectContext,
    kit_home: Path,
    version: str,
    mode: OpenCodeInstallMode,
    environment: Mapping[str, str],
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    events: list[OpenCodeEvent],
) -> None:
    executable = (
        "agent-rails"
        if mode is OpenCodeInstallMode.PROJECT
        else str(kit_home / "bin" / "agent-rails")
    )
    profile = "" if mode is OpenCodeInstallMode.PROJECT else context.profile_path
    content_request = AdapterContentRequest(
        adapter=AdapterType.OPENCODE,
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
    plugin_content = _render_plugin(
        context=context,
        kit_home=kit_home,
        version=version,
        mode=mode,
        environment=environment,
        template_path=layout.plugin_template_path,
    )
    _preflight_install_plugin(layout, workspace)
    config_plan = _prepare_install_config(layout, workspace, mode)
    workspace.validate_ignore_path(layout.local_ignore_path)

    _stdout(events, "Agent Rails opencode Install")
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
    _stdout_many(
        events, workspace.write_generated_file(layout.plugin_path, plugin_content)
    )
    _stdout_many(events, _apply_install_config(layout, workspace, config_plan))
    _stdout_many(events, workspace.write_managed_skills())
    if mode is OpenCodeInstallMode.LOCAL:
        _stdout_many(
            events,
            workspace.ensure_ignore_block(
                layout.local_ignore_path,
                _IGNORE_MARKER,
                _IGNORE_END_MARKER,
                _IGNORE_ENTRIES,
            ),
        )
    else:
        _stdout_many(
            events,
            workspace.remove_ignore_block(
                layout.local_ignore_path,
                _IGNORE_MARKER,
                _IGNORE_END_MARKER,
                "Would remove local ignore entries from",
                "Removed local ignore entries from",
                _IGNORE_ENTRIES,
            ),
        )
    _stdout(events, "")
    _stdout(events, "opencode adapter ready.")
    _stdout(events, f"Task Pack: {context.task_pack_path}")
    _stdout(
        events,
        "Restart opencode or open a new opencode session for config changes "
        "to take effect.",
    )


def _doctor(
    *,
    context: TargetProjectContext,
    version: str,
    environment: Mapping[str, str],
    layout: _OpenCodeLayout,
    events: list[OpenCodeEvent],
) -> None:
    _stdout(events, "Agent Rails opencode Doctor")
    _stdout(events, f"Version: {version}")
    _stdout(events, f"Project: {context.root}")
    executable = shutil.which("opencode", path=environment.get("PATH"))
    if executable is None:
        _stdout(events, "[WARN] opencode CLI not found.")
    else:
        _stdout(events, f"[OK] opencode CLI: {executable}")
        try:
            completed = subprocess.run(
                [executable, "--version"],
                env=dict(environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                encoding="utf-8",
                errors="surrogateescape",
                check=False,
            )
        except OSError:
            completed = None
        if completed is not None:
            for line in completed.stdout.splitlines():
                _stdout(events, f"Version: {line}")

    if _file_contains(layout.guide_path, ("Visible session marker protocol",)):
        _stdout(events, f"[OK] opencode Agent Rails guide: {layout.guide_path}")
    else:
        _stdout(
            events,
            f"[WARN] opencode Agent Rails guide is missing: {layout.guide_path}",
        )
    if _file_contains(
        layout.plugin_path,
        ("experimental.chat.system.transform", "client.session.messages"),
    ):
        _stdout(events, f"[OK] opencode request hook: {layout.plugin_path}")
    else:
        _stdout(
            events,
            "[WARN] opencode request hook is missing or incomplete: "
            f"{layout.plugin_path}",
        )
    if _file_contains(layout.config_path, (str(layout.plugin_path),)):
        _stdout(
            events,
            f"[OK] opencode config loads Agent Rails plugin: {layout.config_path}",
        )
    elif layout.plugin_path.is_file():
        _stdout(
            events,
            "[OK] opencode auto-discovers Agent Rails plugin from the project "
            "plugin directory.",
        )
    else:
        _stdout(
            events,
            "[WARN] opencode config does not load Agent Rails plugin: "
            f"{layout.config_path}",
        )
    for path in (
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        if path.is_file():
            _stdout(events, f"[OK] opencode command: {path}")
        else:
            _stdout(events, f"[WARN] opencode command missing: {path}")


def _uninstall(
    *,
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    force: bool,
    dry_run: bool,
    events: list[OpenCodeEvent],
) -> None:
    workspace.validate_ignore_path(layout.local_ignore_path)
    workspace.preflight_removal()
    config_plan = _prepare_remove_config(layout, workspace, force, dry_run)
    _preflight_uninstall_artifacts(layout, workspace, force)
    _preflight_config_removal(layout, workspace, config_plan)
    _stdout(events, "Agent Rails opencode Uninstall")
    _stdout_many(events, _remove_config(layout, workspace, config_plan))
    for path in (
        layout.plugin_path,
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        _stdout_many(events, workspace.remove_generated_file(path))
    _stdout_many(events, workspace.remove_managed_skills())
    _stdout_many(events, workspace.remove_managed_skills_file())
    if workspace.removal_has_survivors:
        _stdout(
            events,
            "Keeping local ignore entries for preserved managed skills.",
        )
    else:
        _stdout_many(
            events,
            workspace.remove_ignore_block(
                layout.local_ignore_path,
                _IGNORE_MARKER,
                _IGNORE_END_MARKER,
                "Would remove local ignore entries from",
                "Updated local ignore file:",
                _IGNORE_ENTRIES,
            ),
        )
    if not dry_run:
        for path in (
            layout.commands_dir,
            layout.plugins_dir,
            layout.skills_dir,
            layout.opencode_dir,
        ):
            try:
                path.rmdir()
            except OSError:
                pass


def _render_plugin(
    *,
    context: TargetProjectContext,
    kit_home: Path,
    version: str,
    mode: OpenCodeInstallMode,
    environment: Mapping[str, str],
    template_path: Path,
) -> str:
    if not template_path.is_file():
        raise OpenCodeConfigError(
            f"OpenCode plugin template is missing: {template_path}"
        )
    try:
        template = template_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OpenCodeConfigError(
            f"Unable to read OpenCode plugin template: {template_path}"
        ) from exc
    if template.count(_PLUGIN_CONFIG_MARKER) != 1:
        raise OpenCodeConfigError(
            f"Expected one {_PLUGIN_CONFIG_MARKER} marker in {template_path}"
        )

    values = context.profile_values

    def setting(name: str, fallback: str) -> str:
        return values.get(name, environment.get(name, fallback))

    local = mode is OpenCodeInstallMode.LOCAL
    tokenizer = setting("AGENT_RAILS_TOKENIZER", "auto")
    if not local and tokenizer in {"command", "huggingface", "hf"}:
        tokenizer = "auto"
    config = {
        "version": version,
        "bin": str(kit_home / "bin" / "agent-rails") if local else "agent-rails",
        "assembler": (
            str(kit_home / "scripts" / "agent-context-assemble.py")
            if local
            else ""
        ),
        "project": str(context.root) if local else "",
        "profile": context.profile_path if local else "",
        "tokenizer": tokenizer,
        "tokenizerCommand": (
            setting("AGENT_RAILS_TOKENIZER_CMD", "") if local else ""
        ),
        "tokenizerPath": (
            setting("AGENT_RAILS_TOKENIZER_PATH", "") if local else ""
        ),
        "tiktokenEncoding": setting(
            "AGENT_RAILS_TIKTOKEN_ENCODING", "cl100k_base"
        ),
        "charsPerToken": _positive_integer(
            setting("AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE", "2"), 2
        ),
        "contextPercent": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_CONTEXT_PERCENT", "25"), 25
        ),
        "maxPackTokens": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_MAX_PACK_TOKENS", "60000"), 60000
        ),
        "minPackTokens": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_MIN_PACK_TOKENS", "512"), 512
        ),
        "reservePercent": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_RESERVE_PERCENT", "5"), 5
        ),
        "reserveTokens": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_RESERVE_TOKENS", "2048"), 2048
        ),
        "hookTimeoutMs": _positive_integer(
            setting("AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS", "30000"), 30000
        ),
    }
    rendered_config = json.dumps(config, ensure_ascii=False, indent=2)
    return template.replace(_PLUGIN_CONFIG_MARKER, rendered_config)


def _preflight_install_plugin(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
) -> None:
    """Reject a plugin target that the install cannot safely take over."""

    path = workspace.validate_managed_path(layout.plugin_path)
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OpenCodeConfigError(
            f"Unable to inspect OpenCode plugin target: {path}"
        ) from exc
    if not stat.S_ISREG(mode):
        raise OpenCodeConfigError(
            f"OpenCode plugin target is not a regular file: {path}"
        )
    if not workspace.config.force and not workspace.is_generated_file(path):
        raise OpenCodeConfigError(
            "Refusing to replace unmanaged OpenCode plugin target without "
            f"--force: {path}"
        )


def _prepare_install_config(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    mode: OpenCodeInstallMode,
) -> _OpenCodeConfigPlan:
    path = layout.config_path
    workspace.validate_managed_path(path)
    workspace.validate_managed_path(layout.state_path)
    workspace.validate_managed_path(layout.plugin_path)
    if (
        mode is OpenCodeInstallMode.LOCAL
        and not workspace.config.force
        and workspace.is_tracked_file(path)
    ):
        messages = [f"Keeping tracked opencode config in local mode: {path}"]
        if _file_contains(path, (str(layout.plugin_path),)):
            messages.append(
                "[OK] Tracked opencode config already references Agent Rails plugin."
            )
        else:
            messages.append(
                "[OK] Keeping tracked config unchanged; OpenCode auto-discovers "
                "project plugins."
            )
        return _OpenCodeConfigPlan(
            data=None,
            state=None,
            messages=tuple(messages),
            dry_run=workspace.config.dry_run,
        )
    if (
        workspace.config.protect_tracked
        and not workspace.config.force
        and workspace.is_tracked_file(layout.state_path)
    ):
        raise OpenCodeConfigError(
            "Refusing to update tracked OpenCode ownership state in local mode: "
            f"{layout.state_path}"
        )

    config_existed = path.is_file()
    state = _read_state(layout.state_path)
    data = _read_config(path, installing=True)
    schema_inserted = (
        state.schema_inserted if state is not None else "$schema" not in data
    )
    config_existed_before_install = (
        state.config_existed_before_install
        if state is not None
        else config_existed
    )
    plugins = data.get("plugin")
    if plugins is not None and (
        not isinstance(plugins, list)
        or not all(isinstance(item, str) for item in plugins)
    ):
        raise OpenCodeConfigError(
            f"{path} field 'plugin' must be an array of strings."
        )

    legacy_plugin, legacy_guide = _legacy_generated_entries(layout.plugin_path)
    _validate_state_entries(
        state,
        current_entry=str(layout.plugin_path),
        legacy_entry=legacy_plugin,
        state_path=layout.state_path,
    )
    owned_plugin_entries = _owned_plugin_entries(state, legacy_plugin)
    if isinstance(plugins, list):
        plugins[:] = [item for item in plugins if item not in owned_plugin_entries]

    current_entry = str(layout.plugin_path)
    if mode is OpenCodeInstallMode.LOCAL:
        plugins = data.setdefault("plugin", [])
        if current_entry not in plugins:
            plugins.append(current_entry)
            inserted_entries = (current_entry,)
        else:
            inserted_entries = ()
    else:
        if plugins == []:
            data.pop("plugin", None)
        inserted_entries = ()

    owned_instruction_entries = set()
    if workspace.is_generated_file(layout.guide_path):
        owned_instruction_entries.update(
            (str(layout.guide_path), ".opencode/AGENT_RAILS.md")
        )
    if legacy_guide:
        owned_instruction_entries.add(legacy_guide)
    _remove_owned_instructions(data, owned_instruction_entries)
    data.setdefault("$schema", _DEFAULT_SCHEMA)

    next_state = _OpenCodeOwnershipState(
        config_existed_before_install=config_existed_before_install,
        schema_inserted=schema_inserted,
        inserted_plugin_entries=inserted_entries,
    )
    messages = (
        (
            f"Would merge Agent Rails plugin into {path}"
            if config_existed
            else f"Would write {path}",
        )
        if workspace.config.dry_run
        else (f"Merged Agent Rails plugin into {path}",)
    )
    return _OpenCodeConfigPlan(
        data=data,
        state=next_state,
        messages=messages,
        dry_run=workspace.config.dry_run,
    )


def _apply_install_config(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    plan: _OpenCodeConfigPlan,
) -> Tuple[str, ...]:
    if plan.dry_run or plan.data is None:
        return plan.messages
    _write_config(workspace, layout.config_path, plan.data)
    if plan.state is not None:
        _write_state(workspace, layout.state_path, plan.state)
    return plan.messages


def _prepare_remove_config(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    force: bool,
    dry_run: bool,
) -> _OpenCodeConfigRemovalPlan:
    path = layout.config_path
    workspace.validate_managed_path(path)
    workspace.validate_managed_path(layout.state_path)
    workspace.validate_managed_path(layout.plugin_path)
    if not force and workspace.is_tracked_file(path):
        return _OpenCodeConfigRemovalPlan(
            data=None,
            remove_config=False,
            remove_state=False,
            messages=(f"Keeping tracked opencode config in local mode: {path}",),
            dry_run=dry_run,
        )
    if (
        workspace.config.protect_tracked
        and not force
        and workspace.is_tracked_file(layout.state_path)
    ):
        raise OpenCodeConfigError(
            "Refusing to remove tracked OpenCode ownership state in local mode: "
            f"{layout.state_path}"
        )
    state = _read_state(layout.state_path)
    if not path.is_file():
        return _OpenCodeConfigRemovalPlan(
            data=None,
            remove_config=False,
            remove_state=state is not None,
            messages=(),
            dry_run=dry_run,
        )

    data = _read_config(path, installing=False)
    original = copy.deepcopy(data)
    legacy_plugin, legacy_guide = _legacy_generated_entries(layout.plugin_path)
    _validate_state_entries(
        state,
        current_entry=str(layout.plugin_path),
        legacy_entry=legacy_plugin,
        state_path=layout.state_path,
    )
    owned_plugin_entries = _owned_plugin_entries(state, legacy_plugin)
    plugins = data.get("plugin")
    if isinstance(plugins, list):
        data["plugin"] = [
            item for item in plugins if item not in owned_plugin_entries
        ]
        if not data["plugin"]:
            data.pop("plugin", None)

    owned_instruction_entries = set()
    if workspace.is_generated_file(layout.guide_path):
        owned_instruction_entries.update(
            (str(layout.guide_path), ".opencode/AGENT_RAILS.md")
        )
    if legacy_guide:
        owned_instruction_entries.add(legacy_guide)
    _remove_owned_instructions(data, owned_instruction_entries)
    if (
        state is not None
        and state.schema_inserted
        and data.get("$schema") == _DEFAULT_SCHEMA
    ):
        data.pop("$schema", None)

    if dry_run:
        return _OpenCodeConfigRemovalPlan(
            data=None,
            remove_config=False,
            remove_state=False,
            messages=(f"Would remove Agent Rails plugin from {path}",),
            dry_run=True,
        )

    if (
        state is not None
        and not state.config_existed_before_install
        and not data
    ):
        return _OpenCodeConfigRemovalPlan(
            data=None,
            remove_config=True,
            remove_state=True,
            messages=(f"Removed empty {path}",),
            dry_run=False,
        )

    return _OpenCodeConfigRemovalPlan(
        data=data if data != original else None,
        remove_config=False,
        remove_state=state is not None,
        messages=(f"Updated {path}",) if data != original else (),
        dry_run=False,
    )


def _preflight_uninstall_artifacts(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    force: bool,
) -> None:
    """Validate every generated-file removal before config/state mutation."""

    for path in (
        layout.plugin_path,
        layout.guide_path,
        layout.pack_command_path,
        layout.lite_command_path,
        layout.check_command_path,
    ):
        _preflight_generated_removal(path, workspace, force)


def _preflight_generated_removal(
    path: Path,
    workspace: ManagedAdapterWorkspace,
    force: bool,
) -> None:
    # Validate the parent chain while allowing an existing leaf symlink to be
    # preserved by a normal uninstall or unlinked by an explicit forced one.
    workspace.validate_managed_path(path.parent / ".agent-rails-preflight")
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise OpenCodeConfigError(
            f"Unable to inspect OpenCode adapter artifact: {path}"
        ) from exc

    if (
        workspace.config.protect_tracked
        and not force
        and workspace.is_tracked_file(path)
    ):
        return
    if not force and (
        stat.S_ISLNK(mode) or not workspace.is_generated_file(path)
    ):
        return
    if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
        raise OpenCodeConfigError(
            "OpenCode adapter artifact scheduled for removal is not a regular "
            f"file or symbolic link: {path}"
        )
    _require_writable_parent(path, "OpenCode adapter artifact")


def _preflight_config_removal(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    plan: _OpenCodeConfigRemovalPlan,
) -> None:
    if plan.dry_run:
        return
    if plan.remove_config or plan.data is not None:
        _require_writable_parent(layout.config_path, "OpenCode config")
    if plan.remove_state:
        _require_writable_parent(layout.state_path, "OpenCode ownership state")
    inventory = layout.managed_skills_path
    if inventory.exists() and not (
        workspace.config.protect_tracked
        and not workspace.config.force
        and workspace.is_tracked_file(inventory)
    ):
        _require_writable_parent(inventory, "managed skill inventory")


def _require_writable_parent(path: Path, label: str) -> None:
    parent = path.parent
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise OpenCodeConfigError(
            f"{label} parent is not writable: {parent}"
        )


def _remove_config(
    layout: _OpenCodeLayout,
    workspace: ManagedAdapterWorkspace,
    plan: _OpenCodeConfigRemovalPlan,
) -> Tuple[str, ...]:
    if plan.dry_run:
        return plan.messages
    if plan.remove_config:
        try:
            workspace.unlink_managed_file(layout.config_path)
        except (ManagedAdapterWorkspaceError, OSError) as exc:
            raise OpenCodeConfigError(
                f"Unable to remove {layout.config_path}: {exc}"
            ) from exc
    elif plan.data is not None:
        _write_config(workspace, layout.config_path, plan.data)
    if plan.remove_state:
        _remove_state(workspace, layout.state_path)
    return plan.messages


def _read_config(path: Path, *, installing: bool) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        suffix = (
            ". Fix the file first; Agent Rails will not overwrite existing "
            "opencode config."
            if installing
            else ""
        )
        raise OpenCodeConfigError(f"Failed to parse {path}: {exc}{suffix}") from exc
    if not isinstance(data, dict):
        raise OpenCodeConfigError(f"{path} must contain a JSON object.")
    return data


def _remove_owned_instructions(data: dict, owned_entries: set[str]) -> None:
    if not owned_entries:
        return
    instructions = data.get("instructions")
    if not isinstance(instructions, list):
        return
    data["instructions"] = [
        item for item in instructions if item not in owned_entries
    ]
    if not data["instructions"]:
        data.pop("instructions", None)


def _owned_plugin_entries(
    state: Optional[_OpenCodeOwnershipState],
    legacy_entry: Optional[str],
) -> set[str]:
    """Return only plugin entries with explicit or legacy ownership proof."""

    if state is not None:
        return set(state.inserted_plugin_entries)
    return {legacy_entry} if legacy_entry else set()


def _legacy_generated_entries(plugin_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Recover exact pre-state ownership from one generated plugin payload."""

    if not plugin_path.is_file() or plugin_path.is_symlink():
        return None, None
    try:
        content = plugin_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None, None
    if "// <!-- agent-rails:generated -->" not in content.splitlines():
        return None, None
    prefix = "const CONFIG = "
    start = content.find(prefix)
    if start < 0:
        return None, None
    try:
        config, _ = json.JSONDecoder().raw_decode(content[start + len(prefix) :])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    if not isinstance(config, dict):
        return None, None
    project = config.get("project")
    if not isinstance(project, str) or not project or not Path(project).is_absolute():
        return None, None
    old_root = Path(os.path.abspath(project))
    return (
        str(old_root / ".opencode/plugins/agent-rails.mjs"),
        str(old_root / ".opencode/AGENT_RAILS.md"),
    )


def _read_state(path: Path) -> Optional[_OpenCodeOwnershipState]:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise OpenCodeConfigError(
            f"OpenCode ownership state must be a regular file: {path}"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OpenCodeConfigError(
            f"Failed to parse OpenCode ownership state {path}: {exc}"
        ) from exc
    if not isinstance(data, dict) or data.get("format") != _STATE_FORMAT:
        raise OpenCodeConfigError(
            f"Refusing unmanaged OpenCode ownership state: {path}"
        )
    config_existed = data.get("configExistedBeforeInstall")
    schema_inserted = data.get("schemaInserted")
    entries = data.get("insertedPluginEntries")
    if (
        not isinstance(config_existed, bool)
        or not isinstance(schema_inserted, bool)
        or not isinstance(entries, list)
        or not all(isinstance(item, str) for item in entries)
    ):
        raise OpenCodeConfigError(f"Invalid OpenCode ownership state: {path}")
    return _OpenCodeOwnershipState(
        config_existed_before_install=config_existed,
        schema_inserted=schema_inserted,
        inserted_plugin_entries=tuple(dict.fromkeys(entries)),
    )


def _validate_state_entries(
    state: Optional[_OpenCodeOwnershipState],
    *,
    current_entry: str,
    legacy_entry: Optional[str],
    state_path: Path,
) -> None:
    if state is None:
        return
    allowed = {current_entry}
    if legacy_entry:
        allowed.add(legacy_entry)
    unexpected = [
        entry for entry in state.inserted_plugin_entries if entry not in allowed
    ]
    if unexpected:
        raise OpenCodeConfigError(
            f"OpenCode ownership state contains an unverified plugin entry: "
            f"{state_path}"
        )


def _write_state(
    workspace: ManagedAdapterWorkspace,
    path: Path,
    state: _OpenCodeOwnershipState,
) -> None:
    _write_json_file(
        workspace,
        path,
        {
            "format": _STATE_FORMAT,
            "configExistedBeforeInstall": state.config_existed_before_install,
            "schemaInserted": state.schema_inserted,
            "insertedPluginEntries": list(state.inserted_plugin_entries),
        },
        label="OpenCode ownership state",
    )


def _remove_state(workspace: ManagedAdapterWorkspace, path: Path) -> None:
    try:
        workspace.unlink_managed_file(path)
    except (ManagedAdapterWorkspaceError, OSError) as exc:
        raise OpenCodeConfigError(
            f"Unable to remove OpenCode ownership state {path}: {exc}"
        ) from exc


def _write_config(
    workspace: ManagedAdapterWorkspace, path: Path, data: dict
) -> None:
    _write_json_file(workspace, path, data, label="OpenCode config")


def _write_json_file(
    workspace: ManagedAdapterWorkspace,
    path: Path,
    data: dict,
    *,
    label: str,
) -> None:
    try:
        content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
        content.encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise OpenCodeConfigError(
            f"Unable to render {label}: {path}: {exc}"
        ) from exc
    try:
        workspace.replace_text_file(path, content)
    except (ManagedAdapterWorkspaceError, OSError, UnicodeError) as exc:
        raise OpenCodeConfigError(f"Unable to update {label} {path}: {exc}") from exc


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
        raise OpenCodeAdapterError(f"Unable to read Agent Rails version: {path}") from exc
    return ""


def _positive_integer(value: str, fallback: int) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        return fallback
    parsed = int(value)
    return parsed if parsed > 0 else fallback


def _file_contains(path: Path, fragments: Tuple[str, ...]) -> bool:
    if not path.is_file():
        return False
    try:
        content = path.read_bytes()
        return all(fragment.encode("utf-8") in content for fragment in fragments)
    except (OSError, UnicodeError):
        return False


def _is_generated_guide(path: Path) -> bool:
    return _file_contains(path, ("<!-- agent-rails:generated -->",)) or _file_contains(
        path, ("Agent Rails Version:", "Visible session marker protocol")
    )
