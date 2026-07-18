from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Sequence

from . import estimate, public_cli
from .adapters.content import (
    AdapterArtifact,
    AdapterContentError,
    AdapterContentRequest,
    AdapterType,
    extract_adapter_profile,
    render_adapter_content,
    render_profile_argument,
)
from .adapters.claude import (
    ClaudeAdapterError,
    ClaudeAdapterInputError,
    ClaudeEventStream,
    ClaudeInstallMode,
    ClaudeInstallRequest,
    ClaudeUninstallRequest,
    run_claude_adapter,
)
from .adapters.codex import (
    CodexAdapterError,
    CodexAdapterInputError,
    CodexDoctorRequest,
    CodexEventStream,
    CodexInstallMode,
    CodexInstallRequest,
    CodexUninstallRequest,
    run_codex_adapter,
)
from .adapters.opencode import (
    OpenCodeAdapterError,
    OpenCodeAdapterInputError,
    OpenCodeDoctorRequest,
    OpenCodeEventStream,
    OpenCodeInstallMode,
    OpenCodeInstallRequest,
    OpenCodeUninstallRequest,
    run_opencode_adapter,
)
from .config import profile_init
from .config.profile import ProfileLoadError
from .config.target_project import TargetProjectError, resolve_target_project
from .diagnostics.doctor import (
    DoctorError,
    DoctorEventStream,
    DoctorInputError,
    DoctorRequest,
    run_doctor,
)
from .context.change_evidence import (
    ChangeEvidenceError,
    ChangeEvidencePolicy,
    ChangeEvidenceRequest,
    collect_change_evidence,
    write_change_evidence_bundle,
)
from .context.contract_sections import (
    ContractSectionsError,
    ContractSectionsRequest,
    render_contract_sections,
    write_contract_sections_bundle,
)
from .context.memory_evidence import (
    MemoryEvidenceError,
    MemoryEvidenceRequest,
    collect_memory_evidence,
    write_memory_evidence_bundle,
)
from .context.pack_application import (
    PackApplicationError,
    PackApplicationRequest,
    PackCliOverrides,
    generate_task_pack,
)
from .context.task_contract import TaskContractError
from .context.pack_policy import PackPolicyInput, resolve_pack_policy
from .context.pack_renderer import (
    PackRendererError,
    RenderedPackSections,
    TaskPackRenderRequest,
    TokenizerSettings,
    write_task_pack,
)
from .context.project_docs import (
    ProjectDocsError,
    ProjectDocsRequest,
    collect_project_docs,
    write_project_docs_bundle,
)
from .git.scope import (
    GitScopeError,
    read_nul_paths,
    resolve_git_scope,
    write_git_scope_snapshot,
)
from .init_application import (
    InitInputError,
    InitRequest,
    InitShell,
    run_init,
)
from .memory.online import OnlineMemoryError, OnlineMemoryQuery, query_online_memory
from .memory.suggestion import (
    ArtifactKind,
    MemoryDecision,
    MemoryStaleness,
    MemorySuggestionInputError,
    MemorySuggestionPublishError,
    MemorySuggestionRequest,
    PublishedArtifact,
    suggest_memory,
)
from .release.build import (
    ReleaseBuildDependencies,
    ReleaseBuildError,
    ReleaseBuildInputError,
    ReleaseBuildRequest,
    build_release,
)
from .models.presets import resolve_model
from .run_application import (
    RunApplicationError,
    RunApplicationRequest,
    RunCliOverrides,
    RunEventStream,
    RunInputError,
    RunMode,
    run_agent_rails,
)
from .setup_application import (
    SetupApplicationError,
    SetupEvent,
    SetupEventStream,
    SetupInputError,
    SetupInstallMode,
    SetupRequest,
    SetupTool,
    run_setup,
)
from .skills_install import (
    SkillsInstallError,
    SkillsInstallInputError,
    SkillsInstallRequest,
    install_skills,
)
from .update_application import (
    UpdateApplicationError,
    UpdateDependencies,
    UpdateEvent,
    UpdateEventStream,
    UpdateInputError,
    UpdateInstallMode,
    UpdateMode,
    UpdateRequest,
    UpdateTool,
    resolve_release_defaults,
    run_update,
)
from .security.sensitive_output import (
    SensitiveOutputError,
    redact_sensitive_records,
    scan_sensitive_records,
)
from .session_start import (
    SessionStartError,
    SessionStartRequest,
    run_session_start,
)
from .verification.check_application import (
    CheckApplicationError,
    CheckApplicationRequest,
    CheckCliOverrides,
    CheckInputError,
    CheckMode,
    execute_check,
    prepare_check,
    render_check_report,
)
from .verification.publish_check import (
    PublishCheckError,
    PublishCheckCliOverrides,
    PublishCheckInputError,
    PublishCheckRequest,
    run_publish_check,
)
from .verification.verify_application import (
    VerifyApplicationError,
    VerifyInputError,
    VerifyMode,
    VerifyRequest,
    run_verify,
)
from .verification.plan import (
    VerificationCommands,
    VerificationPlanError,
    VerificationPlanRequest,
    build_verification_plan,
    write_verification_plan_bundle,
)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Agent Rails Python CLI requires a command.", file=sys.stderr)
        return 2

    command = args.pop(0)
    if command == "public":
        return public_cli.main(args)
    if command == "estimate":
        return estimate.main(args)
    if command == "target-context":
        return _target_context(args)
    if command == "profile-init":
        return _profile_init(args)
    if command == "online-memory":
        return _online_memory(args)
    if command == "git-scope":
        return _git_scope(args)
    if command == "sensitive-output":
        return _sensitive_output(args)
    if command == "pack-policy":
        return _pack_policy(args)
    if command == "model-known":
        return _model_known(args)
    if command == "task-pack-git-evidence":
        return _task_pack_git_evidence(args)
    if command == "task-pack-project-docs":
        return _task_pack_project_docs(args)
    if command == "verification-plan":
        return _verification_plan(args)
    if command == "task-pack-memory-evidence":
        return _task_pack_memory_evidence(args)
    if command == "task-pack-contract-sections":
        return _task_pack_contract_sections(args)
    if command == "task-pack-render":
        return _task_pack_render(args)
    if command == "task-pack":
        return _task_pack(args)
    if command == "memory-suggest":
        return _memory_suggest(args)
    if command == "adapter-content":
        return _adapter_content(args)
    if command == "adapter-profile":
        return _adapter_profile(args)
    if command == "adapter-profile-argument":
        return _adapter_profile_argument(args)
    if command == "agent-check":
        return _agent_check(args)
    if command == "publish-check":
        return _publish_check(args)
    if command == "doctor-application":
        return _doctor_application(args)
    if command == "run-application":
        return _run_application(args)
    if command == "setup-application":
        return _setup_application(args)
    if command == "verify-application":
        return _verify_application(args)
    if command == "update-application":
        return _update_application(args)
    if command == "release-build":
        return _release_build(args)
    if command == "session-start":
        return _session_start(args)
    if command == "init-application":
        return _init_application(args)
    if command == "skills-install":
        return _skills_install(args)
    if command == "claude-adapter":
        return _claude_adapter(args)
    if command == "codex-adapter":
        return _codex_adapter(args)
    if command == "opencode-adapter":
        return _opencode_adapter(args)

    print(f"Unknown Agent Rails Python command: {command}", file=sys.stderr)
    return 2


_AGENT_CHECK_USAGE = (
    "Usage: agent-rails check [--profile PATH] [--base REF] "
    "[--target-ref REF] [--run|--print-only|--suggestions-only]"
)


_PUBLISH_CHECK_USAGE = """Usage: agent-rails publish check [--profile PATH] [--base REF] [--target-ref REF] [--no-secret-scan]

Summarizes local commit/push scope, scans changed files for likely secrets with
redacted output, and embeds the normal Agent Rails verification suggestions."""


_DOCTOR_APPLICATION_USAGE = """Usage: agent-rails doctor [--project PATH] [--profile PATH] [--online-memory-smoke] [--fix] [--mode local|project] [--session-hook] [--global-reminder] [--dry-run]

Checks project/Profile wiring, Claude adapter files, local Git visibility,
skills, model presets, optional online memory readiness, and required tools.
--fix refreshes the Claude adapter after diagnostics pass."""


_RUN_APPLICATION_USAGE = """Usage: agent-rails run [--project PATH] [--profile PATH] [--model NAME] [--pack-mode lite|normal|deep|audit] [--budget CHARS] [--token-budget TOKENS] [--tokenizer auto|char|tiktoken|command|huggingface] [--tokenizer-command CMD] [--tokenizer-path PATH] [--print-only] [goal text...]

Generates a Task Pack, estimates its size, and prints the next commands/instructions
for an agent session. This wrapper does not hard-control Claude/Codex internals."""


_SETUP_APPLICATION_USAGE = """Usage: agent-rails setup [--project PATH] [--profile PATH] [--tool auto|claude|codex|opencode|all] [--mode local|project] [--no-session-hook] [--dry-run]

With --tool auto, setup proceeds only when exactly one supported coding-agent
CLI is detected. Choose --tool explicitly when multiple tools are installed;
use --tool all only when every supported integration is intentionally wanted.

Adapter mode defaults to local, which keeps generated files out of Git. Project
mode makes generated Adapter files visible for intentional team adoption.
Claude enables the personal SessionStart hook by default; pass --no-session-hook
to install only its project-local Adapter files."""


_VERIFY_APPLICATION_USAGE = """Usage: agent-rails verify [--project PATH] [--profile PATH] [--print-only] [--publish] [--base REF] [--target-ref REF] [--no-secret-scan]

By default, verify executes the Verification Plan selected by `agent-rails check`.
Use --print-only to preview it. With --publish, the same command also runs the
read-only publish scope and secret scan after the normal plan succeeds."""


_UPDATE_APPLICATION_USAGE = """Usage: agent-rails update --tool claude|codex|opencode [--project PATH] [--profile PATH] [--mode local|project] [--session-hook] [--global-reminder] [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
       agent-rails upgrade self [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--skip-tests] [--dry-run]

Update source depends on how the kit was installed:
  Git checkout     git pull --ff-only
  GitHub Release   verified release archive + atomic version switch

`upgrade self` updates only the kit and does not require a target project.
`update` requires an explicit coding-agent tool. It runs source tests only for
a Git checkout, then the selected Adapter's Doctor and refresh unless skipped.
Adapter mode defaults to local; project mode writes files that may be committed.

--session-hook and --global-reminder apply only to --tool claude."""


_RELEASE_BUILD_USAGE = """Usage: python3 -I scripts/agent-python-cli.py release-build [--output DIR] [--include-worktree]

Builds:
  agent-rails.tar.gz
  agent-rails.tar.gz.sha256
  install.sh
  release_install.py

The default archive contains Git-tracked files. --include-worktree also includes
untracked, non-ignored files and is intended only for local pre-commit testing."""


_INIT_APPLICATION_USAGE = """Usage: agent-rails init [--shell zsh|bash|fish] [--project PATH] [--profile PATH]

Prints a copy-paste setup guide for making `agent-rails` available as a normal
local command. This command does not edit shell rc files."""


_SKILLS_INSTALL_USAGE = """Usage: agent-rails skills install --dest PATH [--dry-run] [skill-name...]

Examples:
  agent-rails skills install --dest "$HOME/.codex/skills" --dry-run
  agent-rails skills install --dest "$HOME/.codex/skills" agent-context-pack agent-check

The source of truth stays under $AGENT_RAILS_HOME/skills/."""


def _skills_install(args: Sequence[str]) -> int:
    destination: str | None = None
    dry_run = False
    selected: list[str] = []
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_SKILLS_INSTALL_USAGE)
            return 0
        if argument == "--dest":
            if destination is not None or index + 1 >= len(args):
                print(_SKILLS_INSTALL_USAGE, file=sys.stderr)
                return 2
            destination = args[index + 1]
            index += 2
            continue
        if argument == "--dry-run":
            if dry_run:
                print(_SKILLS_INSTALL_USAGE, file=sys.stderr)
                return 2
            dry_run = True
            index += 1
            continue
        if argument.startswith("-"):
            print(_SKILLS_INSTALL_USAGE, file=sys.stderr)
            return 2
        selected.append(argument)
        index += 1

    if destination is None or not destination:
        print(_SKILLS_INSTALL_USAGE, file=sys.stderr)
        return 2
    kit_home = os.environ.get("AGENT_RAILS_HOME", "")
    if not kit_home:
        print("AGENT_RAILS_HOME is required for skills install.", file=sys.stderr)
        return 2
    try:
        result = install_skills(
            SkillsInstallRequest(
                kit_home=Path(kit_home),
                destination=Path(destination),
                selected_skills=tuple(selected),
                dry_run=dry_run,
            )
        )
    except (SkillsInstallInputError, SkillsInstallError) as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.exit_code


def _init_application(args: Sequence[str]) -> int:
    requested_shell: InitShell | None = None
    project: Path | None = None
    profile: Path | None = None
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_INIT_APPLICATION_USAGE)
            return 0
        if argument not in {"--shell", "--project", "--profile"}:
            print(_INIT_APPLICATION_USAGE, file=sys.stderr)
            return 2
        if index + 1 >= len(args):
            print(_INIT_APPLICATION_USAGE, file=sys.stderr)
            return 2
        value = args[index + 1]
        if argument == "--shell":
            try:
                requested_shell = InitShell(value)
            except ValueError:
                print(f"Unsupported shell: {value}", file=sys.stderr)
                print("Supported shells: zsh, bash, fish", file=sys.stderr)
                return 2
        elif argument == "--project":
            project = Path(value) if value else None
        else:
            profile = Path(value) if value else None
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME", "")
    if not kit_home:
        print("AGENT_RAILS_HOME is required for agent-rails init.", file=sys.stderr)
        return 2
    try:
        result = run_init(
            InitRequest(
                requested_shell=requested_shell,
                requested_project=project,
                explicit_profile=profile,
                kit_home=Path(kit_home),
                environment=dict(os.environ),
            )
        )
    except InitInputError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    sys.stdout.write(result.output)
    return result.exit_code


def _session_start(args: Sequence[str]) -> int:
    if args:
        return 0 if list(args) in (["--help"], ["-h"]) else 2
    kit_home_value = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home_value:
        print("SessionStart requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    host_input = ""
    if not sys.stdin.isatty():
        host_input = sys.stdin.read(1024 * 1024 + 1)
        if len(host_input) > 1024 * 1024:
            host_input = ""
    try:
        result = run_session_start(
            SessionStartRequest(
                kit_home=Path(kit_home_value),
                invocation_cwd=Path.cwd(),
                environment=dict(os.environ),
                host_input=host_input,
            )
        )
    except SessionStartError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.exit_code


def _release_build(args: Sequence[str]) -> int:
    output: str | None = None
    include_worktree = False
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_RELEASE_BUILD_USAGE)
            return 0
        if argument == "--include-worktree":
            include_worktree = True
            index += 1
            continue
        if argument == "--output" and index + 1 < len(args):
            output = args[index + 1]
            index += 2
            continue
        print(_RELEASE_BUILD_USAGE, file=sys.stderr)
        return 2

    kit_home_value = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home_value:
        print("release-build requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    source_root = Path(kit_home_value)
    if output == "":
        print(_RELEASE_BUILD_USAGE, file=sys.stderr)
        return 2
    output_dir = Path(output) if output is not None else source_root / "dist"
    try:
        result = build_release(
            ReleaseBuildRequest(
                source_root=source_root,
                output_dir=output_dir,
                include_worktree=include_worktree,
                environment=dict(os.environ),
            ),
            dependencies=ReleaseBuildDependencies(),
        )
    except (ReleaseBuildInputError, ReleaseBuildError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        f"Built Agent Rails {result.version} release assets in {result.output_dir}"
    )
    return 0


def _update_application(args: Sequence[str]) -> int:
    original_arguments = tuple(args)
    values: dict[str, str] = {}
    tool: UpdateTool | None = None
    install_mode = UpdateInstallMode.LOCAL
    self_only = False
    session_hook = False
    global_reminder = False
    skip_pull = False
    skip_tests = False
    skip_doctor = False
    skip_adapter = False
    dry_run = False
    index = 0
    value_options = {
        "--project": "project",
        "--profile": "profile",
        "--version": "version",
        "--repository": "repository",
        "--install-root": "install_root",
        "--bin-dir": "bin_dir",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_UPDATE_APPLICATION_USAGE)
            return 0
        if argument == "--self-only":
            self_only = True
            index += 1
            continue
        if argument == "--session-hook":
            session_hook = True
            index += 1
            continue
        if argument == "--global-reminder":
            global_reminder = True
            index += 1
            continue
        if argument == "--skip-pull":
            skip_pull = True
            index += 1
            continue
        if argument == "--skip-tests":
            skip_tests = True
            index += 1
            continue
        if argument == "--skip-doctor":
            skip_doctor = True
            index += 1
            continue
        if argument == "--skip-adapter":
            skip_adapter = True
            index += 1
            continue
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument in {"--tool", "--mode"}:
            if index + 1 >= len(args):
                print(_UPDATE_APPLICATION_USAGE, file=sys.stderr)
                return 2
            try:
                if argument == "--tool":
                    tool = UpdateTool(args[index + 1])
                else:
                    install_mode = UpdateInstallMode(args[index + 1])
            except ValueError:
                print(_UPDATE_APPLICATION_USAGE, file=sys.stderr)
                return 2
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_UPDATE_APPLICATION_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    kit_home_value = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home_value:
        print("update requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    if not self_only and tool is None:
        print("--tool is required for agent-rails update.", file=sys.stderr)
        print("Choose --tool claude, codex, or opencode.", file=sys.stderr)
        return 2
    if "project" in values and values["project"] == "":
        print("Project directory not found: ", file=sys.stderr)
        return 2

    environment = dict(os.environ)
    kit_home = Path(kit_home_value)
    repository, install_root, bin_dir = resolve_release_defaults(
        kit_home, environment
    )
    repository = values.get("repository", repository)
    install_root = Path(values.get("install_root", str(install_root)))
    bin_dir = Path(values.get("bin_dir", str(bin_dir)))
    mode = UpdateMode.SELF if self_only else UpdateMode.PROJECT
    if self_only:
        skip_doctor = True
        skip_adapter = True
    requested_version = values.get("version", "latest")
    if requested_version.startswith("v"):
        requested_version = requested_version[1:]

    try:
        result = run_update(
            UpdateRequest(
                mode=mode,
                requested_project=(
                    None
                    if self_only
                    else Path(values.get("project", os.getcwd()))
                ),
                kit_home=kit_home,
                explicit_profile=values.get("profile"),
                tool=tool,
                install_mode=install_mode,
                session_hook=session_hook,
                global_reminder=global_reminder,
                requested_version=requested_version,
                repository=repository,
                install_root=install_root,
                bin_dir=bin_dir,
                skip_pull=skip_pull,
                skip_tests=skip_tests,
                skip_doctor=skip_doctor,
                skip_adapter=skip_adapter,
                dry_run=dry_run,
                original_arguments=original_arguments,
                working_directory=Path.cwd(),
                environment=environment,
            ),
            dependencies=UpdateDependencies(event_sink=_emit_update_event),
        )
    except UpdateInputError as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except UpdateApplicationError as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    return result.exit_code


def _emit_update_event(event: UpdateEvent) -> None:
    stream = (
        sys.stdout
        if event.stream is UpdateEventStream.STDOUT
        else sys.stderr
    )
    stream.write(event.text)
    stream.flush()


def _verify_application(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    print_only = False
    publish = False
    no_secret_scan = False
    index = 0
    value_options = {
        "--project": "project",
        "--profile": "profile",
        "--base": "base_ref",
        "--target-ref": "target_ref",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_VERIFY_APPLICATION_USAGE)
            return 0
        if argument == "--print-only":
            print_only = True
            index += 1
            continue
        if argument == "--publish":
            publish = True
            index += 1
            continue
        if argument == "--no-secret-scan":
            no_secret_scan = True
            index += 1
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_VERIFY_APPLICATION_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("verify requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    if "project" in values and values["project"] == "":
        # Preserve Verify's historical silent exit for an explicitly empty path.
        return 1
    try:
        result = run_verify(
            VerifyRequest(
                requested_project=Path(values.get("project", os.getcwd())),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                mode=VerifyMode.PUBLISH if publish else VerifyMode.DELIVERY,
                print_only=print_only,
                base_ref=values.get("base_ref"),
                target_ref=values.get("target_ref"),
                no_secret_scan=no_secret_scan,
                working_directory=Path.cwd(),
                environment=dict(os.environ),
            ),
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    except VerifyInputError as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except VerifyApplicationError as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return result.exit_code


def _setup_application(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    tool = SetupTool.AUTO
    mode = SetupInstallMode.LOCAL
    session_hook = True
    dry_run = False
    index = 0
    value_options = {"--project": "project", "--profile": "profile"}
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_SETUP_APPLICATION_USAGE)
            return 0
        if argument == "--no-session-hook":
            session_hook = False
            index += 1
            continue
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument in {"--tool", "--mode"}:
            if index + 1 >= len(args):
                print(_SETUP_APPLICATION_USAGE, file=sys.stderr)
                return 2
            try:
                if argument == "--tool":
                    tool = SetupTool(args[index + 1])
                else:
                    mode = SetupInstallMode(args[index + 1])
            except ValueError:
                print(_SETUP_APPLICATION_USAGE, file=sys.stderr)
                return 2
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_SETUP_APPLICATION_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("setup requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        result = run_setup(
            SetupRequest(
                requested_project=Path(values.get("project", os.getcwd())),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                tool=tool,
                mode=mode,
                session_hook=session_hook,
                dry_run=dry_run,
                working_directory=Path.cwd(),
                environment=dict(os.environ),
            )
        )
    except SetupInputError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except SetupApplicationError as exc:
        _replay_setup_events(exc.events)
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _replay_setup_events(result.events)
    return result.exit_code


def _replay_setup_events(events: Sequence[SetupEvent]) -> None:
    for event in events:
        stream = (
            sys.stdout
            if event.stream is SetupEventStream.STDOUT
            else sys.stderr
        )
        print(event.text, file=stream)
        stream.flush()


def _run_application(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    goal_parts: list[str] = []
    print_only = False
    index = 0
    value_options = {
        "--project": "project",
        "--profile": "profile",
        "--model": "model",
        "--pack-mode": "pack_mode",
        "--budget": "context_budget_chars",
        "--context-budget": "context_budget_chars",
        "--token-budget": "context_budget_tokens",
        "--tokenizer": "tokenizer",
        "--tokenizer-command": "tokenizer_command",
        "--tokenizer-path": "tokenizer_path",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_RUN_APPLICATION_USAGE)
            return 0
        if argument == "--print-only":
            print_only = True
            index += 1
            continue
        destination = value_options.get(argument)
        if destination is not None:
            if index + 1 >= len(args):
                print(_RUN_APPLICATION_USAGE, file=sys.stderr)
                return 2
            values[destination] = args[index + 1]
            index += 2
            continue
        goal_parts.append(argument)
        index += 1

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("run application requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        result = run_agent_rails(
            RunApplicationRequest(
                requested_project=Path(values.get("project", os.getcwd())),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                goal=" ".join(goal_parts),
                overrides=RunCliOverrides(
                    mode=(
                        RunMode.PRINT_ONLY if print_only else RunMode.EXECUTE
                    ),
                    model=values.get("model"),
                    pack_mode=values.get("pack_mode"),
                    context_budget_chars=values.get("context_budget_chars"),
                    context_budget_tokens=values.get("context_budget_tokens"),
                    tokenizer=values.get("tokenizer"),
                    tokenizer_command=values.get("tokenizer_command"),
                    tokenizer_path=values.get("tokenizer_path"),
                ),
                working_directory=Path.cwd(),
                environment=dict(os.environ),
            )
        )
    except RunInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RunApplicationError as exc:
        for event in exc.events:
            stream = sys.stdout if event.stream is RunEventStream.STDOUT else sys.stderr
            print(event.text, file=stream)
            stream.flush()
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream is RunEventStream.STDOUT else sys.stderr
        print(event.text, file=stream)
        stream.flush()
    return result.exit_code


def _doctor_application(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    online_memory_smoke = False
    fix = False
    fix_mode = ClaudeInstallMode.LOCAL
    fix_session_hook = False
    fix_global_reminder = False
    dry_run = False
    index = 0
    value_options = {"--project": "project", "--profile": "profile"}
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_DOCTOR_APPLICATION_USAGE)
            return 0
        if argument == "--online-memory-smoke":
            online_memory_smoke = True
            index += 1
            continue
        if argument == "--fix":
            fix = True
            index += 1
            continue
        if argument == "--session-hook":
            fix_session_hook = True
            index += 1
            continue
        if argument == "--global-reminder":
            fix_global_reminder = True
            index += 1
            continue
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument == "--mode":
            if index + 1 >= len(args):
                print(_DOCTOR_APPLICATION_USAGE, file=sys.stderr)
                return 2
            try:
                fix_mode = ClaudeInstallMode(args[index + 1])
            except ValueError:
                print(_DOCTOR_APPLICATION_USAGE, file=sys.stderr)
                return 2
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_DOCTOR_APPLICATION_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("doctor requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        result = run_doctor(
            DoctorRequest(
                requested_project=Path(values.get("project", os.getcwd())),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                online_memory_smoke=online_memory_smoke,
                fix=fix,
                fix_mode=fix_mode,
                fix_session_hook=fix_session_hook,
                fix_global_reminder=fix_global_reminder,
                dry_run=dry_run,
                environment=dict(os.environ),
            )
        )
    except DoctorInputError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (DoctorError, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream is DoctorEventStream.STDOUT else sys.stderr
        print(event.text, file=stream)
        stream.flush()
    return result.exit_code


def _agent_check(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    run_commands = False
    suggestions_only = False
    target_ref_explicit = False
    index = 0
    value_options = {
        "--profile": "profile",
        "--base": "base_ref",
        "--target-ref": "target_ref",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_AGENT_CHECK_USAGE)
            return 0
        if argument == "--run":
            run_commands = True
            index += 1
            continue
        if argument == "--print-only":
            run_commands = False
            index += 1
            continue
        if argument == "--suggestions-only":
            suggestions_only = True
            index += 1
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_AGENT_CHECK_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        if argument == "--target-ref":
            target_ref_explicit = True
        index += 2

    if suggestions_only and run_commands:
        print("--suggestions-only cannot be combined with --run.", file=sys.stderr)
        return 2
    mode = (
        CheckMode.SUGGESTIONS_ONLY
        if suggestions_only
        else CheckMode.RUN
        if run_commands
        else CheckMode.PREVIEW
    )
    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("agent-check requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        prepared = prepare_check(
            CheckApplicationRequest(
                requested_project=Path(os.getcwd()),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                overrides=CheckCliOverrides(
                    base_ref=values.get("base_ref"),
                    target_ref=values.get("target_ref", "HEAD"),
                    target_ref_explicit=target_ref_explicit,
                    mode=mode,
                ),
                environment=dict(os.environ),
            )
        )
        print(render_check_report(prepared), end="")
        sys.stdout.flush()
        execution = execute_check(prepared)
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (
        TargetProjectError,
        ProfileLoadError,
        GitScopeError,
        VerificationPlanError,
        CheckInputError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CheckApplicationError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return execution.exit_code


def _publish_check(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    base_ref_explicit = False
    target_ref_explicit = False
    scan_secrets = True
    index = 0
    value_options = {
        "--profile": "profile",
        "--base": "base_ref",
        "--target-ref": "target_ref",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_PUBLISH_CHECK_USAGE)
            return 0
        if argument == "--no-secret-scan":
            scan_secrets = False
            index += 1
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_PUBLISH_CHECK_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        if argument == "--base":
            base_ref_explicit = True
        elif argument == "--target-ref":
            target_ref_explicit = True
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("publish check requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        result = run_publish_check(
            PublishCheckRequest(
                requested_project=Path(os.getcwd()),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                overrides=PublishCheckCliOverrides(
                    base_ref=values.get("base_ref"),
                    base_ref_explicit=base_ref_explicit,
                    target_ref=values.get("target_ref", "HEAD"),
                    target_ref_explicit=target_ref_explicit,
                    scan_secrets=scan_secrets,
                ),
                environment=dict(os.environ),
            )
        )
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (
        TargetProjectError,
        ProfileLoadError,
        GitScopeError,
        VerificationPlanError,
        PublishCheckInputError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except PublishCheckError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream.value == "stdout" else sys.stderr
        stream.write(event.text)
        stream.flush()
    return result.exit_code


_OPENCODE_ADAPTER_USAGE = """Usage: agent-rails opencode install [--project PATH] [--profile PATH] [--mode local|project] [--dry-run] [--force]
       agent-rails opencode doctor [--project PATH]
       agent-rails opencode uninstall [--project PATH] [--dry-run] [--force]

opencode install writes a project-local .opencode/ adapter. Mode local ignores
the generated files in git repositories; mode project makes them committable.
It does not modify ~/.config/opencode."""


_CLAUDE_ADAPTER_USAGE = """Usage: agent-rails claude install [--project PATH] [--profile PATH] [--mode local|project] [--global-reminder] [--session-hook] [--dry-run] [--force]
       agent-rails claude uninstall [--project PATH] [--profile PATH] [--global-reminder] [--session-hook] [--dry-run] [--force]

Claude local mode writes .claude/ plus CLAUDE.local.md and ignores the
generated files locally. Project mode writes portable, committable files.
--local aliases --mode local; --write-claude-md aliases --mode project."""


_CODEX_ADAPTER_USAGE = """Usage: agent-rails codex install [--project PATH] [--profile PATH] [--fix-project] [--mode local|project] [--dry-run]
       agent-rails codex doctor [--project PATH]
       agent-rails codex uninstall [--dry-run]

Codex install registers the repo-local Agent Rails marketplace and installs
agent-rails@agent-rails-local. Project marker/adapter refresh is explicit via
--fix-project so business repositories are not changed by surprise. Adapter mode
defaults to local; project mode makes the generated marker files committable."""


def _claude_adapter(args: Sequence[str]) -> int:
    if not args:
        print(_CLAUDE_ADAPTER_USAGE, file=sys.stderr)
        return 2
    subcommand = args[0]
    if subcommand in {"--help", "-h"}:
        print(_CLAUDE_ADAPTER_USAGE)
        return 0
    if subcommand not in {"install", "uninstall"}:
        print(_CLAUDE_ADAPTER_USAGE, file=sys.stderr)
        return 2

    values: dict[str, str] = {}
    mode = ClaudeInstallMode.LOCAL
    mode_explicit = False
    dry_run = False
    force = False
    global_reminder = False
    session_hook = False
    index = 1
    value_options = {"--project": "project", "--profile": "profile"}
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_CLAUDE_ADAPTER_USAGE)
            return 0
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument == "--force":
            force = True
            index += 1
            continue
        if argument == "--global-reminder":
            global_reminder = True
            index += 1
            continue
        if argument == "--session-hook":
            session_hook = True
            index += 1
            continue
        if argument == "--local":
            mode = ClaudeInstallMode.LOCAL
            mode_explicit = True
            index += 1
            continue
        if argument == "--write-claude-md":
            mode = ClaudeInstallMode.PROJECT
            mode_explicit = True
            index += 1
            continue
        if argument == "--mode":
            if index + 1 >= len(args):
                print(_CLAUDE_ADAPTER_USAGE, file=sys.stderr)
                return 2
            try:
                mode = ClaudeInstallMode(args[index + 1])
            except ValueError:
                print(_CLAUDE_ADAPTER_USAGE, file=sys.stderr)
                return 2
            mode_explicit = True
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_CLAUDE_ADAPTER_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    if subcommand != "install" and mode_explicit:
        print(
            "--mode/--local/--write-claude-md are only supported by "
            "agent-rails claude install.",
            file=sys.stderr,
        )
        return 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("claude adapter requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    common = {
        "requested_project": Path(values.get("project", os.getcwd())),
        "kit_home": Path(kit_home),
        "explicit_profile": values.get("profile"),
        "dry_run": dry_run,
        "force": force,
        "global_reminder": global_reminder,
        "session_hook": session_hook,
        "environment": dict(os.environ),
    }
    if subcommand == "install":
        request = ClaudeInstallRequest(**common, mode=mode)
    else:
        request = ClaudeUninstallRequest(**common)
    try:
        result = run_claude_adapter(request)
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (TargetProjectError, ProfileLoadError, ClaudeAdapterInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (ClaudeAdapterError, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream is ClaudeEventStream.STDOUT else sys.stderr
        print(event.text, file=stream)
        stream.flush()
    return 0


def _codex_adapter(args: Sequence[str]) -> int:
    if not args:
        print(_CODEX_ADAPTER_USAGE, file=sys.stderr)
        return 2
    subcommand = args[0]
    if subcommand in {"--help", "-h"}:
        print(_CODEX_ADAPTER_USAGE)
        return 0
    if subcommand not in {"install", "doctor", "uninstall"}:
        print(_CODEX_ADAPTER_USAGE, file=sys.stderr)
        return 2

    values: dict[str, str] = {}
    mode = CodexInstallMode.LOCAL
    fix_project = False
    dry_run = False
    supplied_options: list[str] = []
    index = 1
    value_options = {"--project": "project", "--profile": "profile"}
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_CODEX_ADAPTER_USAGE)
            return 0
        if argument == "--fix-project":
            fix_project = True
            supplied_options.append(argument)
            index += 1
            continue
        if argument == "--dry-run":
            dry_run = True
            supplied_options.append(argument)
            index += 1
            continue
        if argument == "--mode":
            if index + 1 >= len(args):
                print(_CODEX_ADAPTER_USAGE, file=sys.stderr)
                return 2
            try:
                mode = CodexInstallMode(args[index + 1])
            except ValueError:
                print(_CODEX_ADAPTER_USAGE, file=sys.stderr)
                return 2
            supplied_options.append(argument)
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_CODEX_ADAPTER_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        supplied_options.append(argument)
        index += 2

    allowed_options = {
        "install": {"--project", "--profile", "--fix-project", "--mode", "--dry-run"},
        "doctor": {"--project"},
        "uninstall": {"--dry-run"},
    }[subcommand]
    invalid_options = [
        option for option in supplied_options if option not in allowed_options
    ]
    if invalid_options:
        joined = "/".join(dict.fromkeys(invalid_options))
        print(
            f"{joined} is only supported by agent-rails codex actions that "
            "advertise that option.",
            file=sys.stderr,
        )
        return 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("codex adapter requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    requested_project = (
        Path(values["project"]) if "project" in values else None
    )
    common = {
        "kit_home": Path(kit_home),
        "working_directory": Path.cwd(),
        "environment": dict(os.environ),
    }
    if subcommand == "install":
        request = CodexInstallRequest(
            **common,
            requested_project=requested_project,
            explicit_profile=values.get("profile"),
            mode=mode,
            fix_project=fix_project,
            dry_run=dry_run,
        )
    elif subcommand == "doctor":
        request = CodexDoctorRequest(
            **common,
            requested_project=requested_project,
            explicit_profile=None,
        )
    else:
        request = CodexUninstallRequest(**common, dry_run=dry_run)

    try:
        result = run_codex_adapter(request)
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (TargetProjectError, ProfileLoadError, CodexAdapterInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except CodexAdapterError as exc:
        for event in exc.events:
            stream = (
                sys.stdout
                if event.stream is CodexEventStream.STDOUT
                else sys.stderr
            )
            print(event.text, file=stream)
            stream.flush()
        print(str(exc), file=sys.stderr)
        return exc.exit_code
    except (OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream is CodexEventStream.STDOUT else sys.stderr
        print(event.text, file=stream)
        stream.flush()
    return result.exit_code


def _opencode_adapter(args: Sequence[str]) -> int:
    if not args:
        print(_OPENCODE_ADAPTER_USAGE, file=sys.stderr)
        return 2
    subcommand = args[0]
    values: dict[str, str] = {}
    mode = OpenCodeInstallMode.LOCAL
    mode_explicit = False
    dry_run = False
    force = False
    index = 1
    value_options = {"--project": "project", "--profile": "profile"}
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_OPENCODE_ADAPTER_USAGE)
            return 0
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        if argument == "--force":
            force = True
            index += 1
            continue
        if argument == "--mode":
            if index + 1 >= len(args):
                print(_OPENCODE_ADAPTER_USAGE, file=sys.stderr)
                return 2
            try:
                mode = OpenCodeInstallMode(args[index + 1])
            except ValueError:
                print(_OPENCODE_ADAPTER_USAGE, file=sys.stderr)
                return 2
            mode_explicit = True
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None or index + 1 >= len(args):
            print(_OPENCODE_ADAPTER_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    if subcommand in {"--help", "-h"}:
        print(_OPENCODE_ADAPTER_USAGE)
        return 0
    if subcommand not in {"install", "doctor", "uninstall"}:
        print(_OPENCODE_ADAPTER_USAGE, file=sys.stderr)
        return 2
    if subcommand != "install" and mode_explicit:
        print(
            "--mode is only supported by agent-rails opencode install.",
            file=sys.stderr,
        )
        return 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("opencode adapter requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    common = {
        "requested_project": Path(values.get("project", os.getcwd())),
        "kit_home": Path(kit_home),
        "explicit_profile": values.get("profile"),
        "environment": dict(os.environ),
    }
    if subcommand == "install":
        request = OpenCodeInstallRequest(
            **common,
            mode=mode,
            dry_run=dry_run,
            force=force,
        )
    elif subcommand == "doctor":
        request = OpenCodeDoctorRequest(**common)
    else:
        request = OpenCodeUninstallRequest(
            **common,
            dry_run=dry_run,
            force=force,
        )
    try:
        result = run_opencode_adapter(request)
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (TargetProjectError, ProfileLoadError, OpenCodeAdapterInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OpenCodeAdapterError, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    for event in result.events:
        stream = sys.stdout if event.stream is OpenCodeEventStream.STDOUT else sys.stderr
        print(event.text, file=stream)
        stream.flush()
    return 0


def _adapter_profile_argument(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python adapter-profile-argument", add_help=False
    )
    parser.add_argument("--profile", required=True)
    try:
        options = parser.parse_args(list(args))
        rendered = render_profile_argument(options.profile)
    except SystemExit:
        return 2
    except AdapterContentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(rendered, end="")
    return 0


def _adapter_profile(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python adapter-profile", add_help=False
    )
    parser.add_argument("--input", required=True)
    try:
        options = parser.parse_args(list(args))
        input_path = Path(options.input)
        with input_path.open("r", encoding="utf-8", errors="strict") as handle:
            content = handle.read(1_048_577)
        if len(content) > 1_048_576:
            raise AdapterContentError("Agent Rails adapter content is too large.")
        profile = extract_adapter_profile(content)
    except SystemExit:
        return 2
    except (AdapterContentError, OSError, UnicodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if profile:
        print(profile)
    return 0


def _adapter_content(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python adapter-content", add_help=False
    )
    parser.add_argument("--adapter", required=True, choices=[item.value for item in AdapterType])
    parser.add_argument("--artifact", required=True, choices=[item.value for item in AdapterArtifact])
    parser.add_argument("--version", required=True)
    parser.add_argument("--bin", required=True)
    parser.add_argument("--profile", default="")
    try:
        options = parser.parse_args(list(args))
        rendered = render_adapter_content(
            AdapterContentRequest(
                adapter=AdapterType(options.adapter),
                version=options.version,
                executable=options.bin,
                profile=options.profile,
            ),
            AdapterArtifact(options.artifact),
        )
    except SystemExit:
        return 2
    except AdapterContentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(rendered, end="")
    return 0


_MEMORY_SUGGEST_USAGE = """Usage: agent-rails memory suggest [--project PATH] [--profile PATH] [--output PATH]
                                [--decision keep|skip|update|merge]
                                [--write-local] [--force]
                                [--id ID] [--title TITLE]
                                [--trigger TEXT] [--applies-to TEXT]
                                [--verify TEXT] [--caution TEXT]
                                [--reason TEXT]
                                [--staleness stable|verify-first]
                                [notes...]

Examples:
  agent-rails memory suggest --project /path/to/project --decision skip --reason "one-off output"
  agent-rails memory suggest --project /path/to/project --write-local --title "Backend runs on Pandora Boot" "Pandora Boot may serve stale BOOT-INF jars after backend edits."

The model decides whether the lesson is valuable. This helper records that
decision. It writes local memory only with --write-local; it never writes
through an online memory Adapter."""


def _memory_suggest(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    triggers: list[str] = []
    applies_to: list[str] = []
    note_parts: list[str] = []
    write_local = False
    force = False
    value_options = {
        "--project": "project",
        "--profile": "profile",
        "--output": "output",
        "--title": "title",
        "--id": "memory_id",
        "--decision": "decision",
        "--reason": "reason",
        "--verify": "verify",
        "--caution": "caution",
        "--staleness": "staleness",
    }
    index = 0
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_MEMORY_SUGGEST_USAGE)
            return 0
        if argument == "--write-local":
            write_local = True
            index += 1
            continue
        if argument == "--force":
            force = True
            index += 1
            continue
        if argument in {"--trigger", "--applies-to"}:
            if index + 1 >= len(args):
                print(_MEMORY_SUGGEST_USAGE, file=sys.stderr)
                return 2
            target = triggers if argument == "--trigger" else applies_to
            target.append(args[index + 1])
            index += 2
            continue
        destination = value_options.get(argument)
        if destination is None:
            note_parts.append(argument)
            index += 1
            continue
        if index + 1 >= len(args):
            print(_MEMORY_SUGGEST_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        index += 2

    decision_value = values.get("decision", MemoryDecision.KEEP.value)
    staleness_value = values.get(
        "staleness", MemoryStaleness.VERIFY_FIRST.value
    )
    try:
        decision = MemoryDecision(decision_value)
        staleness = MemoryStaleness(staleness_value)
    except ValueError:
        print(_MEMORY_SUGGEST_USAGE, file=sys.stderr)
        return 2
    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("memory-suggest requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    try:
        result = suggest_memory(
            MemorySuggestionRequest(
                requested_project=Path(values.get("project", os.getcwd())),
                invocation_cwd=Path(os.getcwd()),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                output=values.get("output"),
                decision=decision,
                write_local=write_local,
                force=force,
                memory_id=values.get("memory_id"),
                title=values.get("title"),
                triggers=tuple(triggers),
                applies_to=tuple(applies_to),
                verify=values.get("verify", ""),
                caution=values.get("caution", ""),
                reason=values.get("reason", ""),
                staleness=staleness,
                notes=" ".join(note_parts),
                environment=dict(os.environ),
            )
        )
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except (TargetProjectError, ProfileLoadError, MemorySuggestionInputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except MemorySuggestionPublishError as exc:
        _print_memory_published(exc.published)
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, UnicodeError, GitScopeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    _print_memory_published(result.published)
    return 0


def _print_memory_published(published: Sequence[PublishedArtifact]) -> None:
    for artifact in published:
        if artifact.kind is ArtifactKind.DECISION:
            print(f"Wrote {artifact.target.display_path}")
        else:
            print(f"Wrote local memory {artifact.target.display_path}")


_TASK_PACK_USAGE = (
    "Usage: agent-rails pack [--profile PATH] [--base REF] "
    "[--target-ref REF] [--output PATH] [--model NAME] "
    "[--task-file PATH] [--rubric-file PATH] "
    "[--pack-mode lite|normal|deep|audit] [--budget CHARS] "
    "[--token-budget TOKENS] "
    "[--tokenizer auto|char|tiktoken|command|huggingface] "
    "[--tokenizer-command CMD] [--tokenizer-path PATH] [goal text...]"
)


def _task_pack(args: Sequence[str]) -> int:
    values: dict[str, str] = {}
    goal_parts: list[str] = []
    target_ref_explicit = False
    index = 0
    value_options = {
        "--profile": "profile",
        "--base": "base_ref",
        "--target-ref": "target_ref",
        "--output": "output",
        "--model": "model",
        "--pack-mode": "pack_mode",
        "--budget": "context_budget_chars",
        "--context-budget": "context_budget_chars",
        "--token-budget": "context_budget_tokens",
        "--tokenizer": "tokenizer",
        "--tokenizer-command": "tokenizer_command",
        "--tokenizer-path": "tokenizer_path",
        "--task-file": "task_file",
        "--rubric-file": "rubric_file",
    }
    while index < len(args):
        argument = args[index]
        if argument in {"--help", "-h"}:
            print(_TASK_PACK_USAGE)
            return 0
        destination = value_options.get(argument)
        if destination is None:
            goal_parts.append(argument)
            index += 1
            continue
        if index + 1 >= len(args):
            print(_TASK_PACK_USAGE, file=sys.stderr)
            return 2
        values[destination] = args[index + 1]
        if argument == "--target-ref":
            target_ref_explicit = True
        index += 2

    kit_home = os.environ.get("AGENT_RAILS_HOME")
    if not kit_home:
        print("task-pack requires AGENT_RAILS_HOME.", file=sys.stderr)
        return 2
    goal = " ".join(goal_parts) or "TODO: describe the concrete user goal."
    try:
        result = generate_task_pack(
            PackApplicationRequest(
                requested_project=Path(os.getcwd()),
                kit_home=Path(kit_home),
                explicit_profile=values.get("profile"),
                goal=goal,
                overrides=PackCliOverrides(
                    base_ref=values.get("base_ref"),
                    target_ref=values.get("target_ref", "HEAD"),
                    target_ref_explicit=target_ref_explicit,
                    output=values.get("output"),
                    model=values.get("model"),
                    pack_mode=values.get("pack_mode"),
                    context_budget_chars=values.get("context_budget_chars"),
                    context_budget_tokens=values.get("context_budget_tokens"),
                    tokenizer=values.get("tokenizer"),
                    tokenizer_command=values.get("tokenizer_command"),
                    tokenizer_path=values.get("tokenizer_path"),
                    task_file=values.get("task_file"),
                    rubric_file=values.get("rubric_file"),
                ),
                environment=dict(os.environ),
            )
        )
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except PackRendererError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except (
        OSError,
        UnicodeError,
        TargetProjectError,
        ProfileLoadError,
        GitScopeError,
        ChangeEvidenceError,
        ProjectDocsError,
        ContractSectionsError,
        TaskContractError,
        MemoryEvidenceError,
        PackApplicationError,
    ) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"AGENT RAILS: ON (mode={result.pack_mode}, "
        f"pack={result.output.display_path})"
    )
    print(f"Wrote {result.output.display_path}")
    return 0


def _target_context(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python target-context", add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--agent-rails-home", required=True)
    parser.add_argument("--required-profile", action="store_true")
    parser.add_argument("--skip-profile-load", action="store_true")
    parser.add_argument("--load-env-file", action="store_true")
    parser.add_argument("--profile-variable", action="append", default=[])
    parser.add_argument("--shell", action="store_true")
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    if not options.shell:
        print("target-context currently requires --shell.", file=sys.stderr)
        return 2
    invalid_variable = next(
        (
            name
            for name in options.profile_variable
            if re.fullmatch(r"[A-Z][A-Z0-9_]*", name) is None
        ),
        None,
    )
    if invalid_variable is not None:
        print(f"Invalid Profile variable name: {invalid_variable}", file=sys.stderr)
        return 2
    try:
        context = resolve_target_project(
            Path(options.project),
            kit_home=Path(options.agent_rails_home),
            explicit_profile=options.profile,
            require_profile=options.required_profile,
            load_profile=not options.skip_profile_load,
            load_environment_file=options.load_env_file,
            profile_variables=options.profile_variable,
        )
    except TargetProjectError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except ProfileLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for name, value in context.shell_values().items():
        print(f"{name}={shlex.quote(value)}")
    return 0


def _online_memory(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python online-memory", add_help=False)
    parser.add_argument("--command", required=True)
    parser.add_argument("--query-file", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--output", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    try:
        output = query_online_memory(
            options.command,
            OnlineMemoryQuery(
                query_file=Path(options.query_file),
                project=options.project,
                limit=options.limit,
                timeout_seconds=options.timeout_seconds,
            ),
        )
    except OnlineMemoryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    Path(options.output).write_text(output, encoding="utf-8")
    return 0


def _git_scope(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python git-scope", add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--base", default="")
    parser.add_argument("--policy", required=True)
    parser.add_argument("--snapshot-dir")
    parser.add_argument("--include-worktree", action="store_true")
    parser.add_argument("--shell", action="store_true")
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    if options.include_worktree and not options.snapshot_dir:
        print("git-scope --include-worktree requires --snapshot-dir.", file=sys.stderr)
        return 2
    try:
        scope = resolve_git_scope(
            Path(options.project),
            target_ref=options.target_ref,
            base_ref=options.base,
            base_policy=options.policy,
        )
        if options.snapshot_dir:
            write_git_scope_snapshot(
                Path(options.project),
                scope,
                Path(options.snapshot_dir),
                include_worktree=options.include_worktree,
            )
    except GitScopeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if options.shell:
        for name, value in scope.shell_values().items():
            print(f"{name}={shlex.quote(value)}")
    return 0


def _sensitive_output(args: Sequence[str]) -> int:
    if not args or args[0] not in {"redact", "scan"}:
        print("sensitive-output requires redact or scan.", file=sys.stderr)
        return 2
    action = args[0]
    parser = argparse.ArgumentParser(
        prog=f"agent-rails-python sensitive-output {action}", add_help=False
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--format", default="text")
    if action == "redact":
        parser.add_argument("--output", required=True)
    try:
        options = parser.parse_args(list(args[1:]))
    except SystemExit:
        return 2

    input_path = Path(options.input)
    if action == "redact" and input_path == Path(options.output):
        print(
            "Sensitive-output redaction requires different input and output paths.",
            file=sys.stderr,
        )
        return 2
    try:
        if action == "redact":
            records = redact_sensitive_records(
                _read_text_records(input_path), format_name=options.format
            )
            with Path(options.output).open(
                "w", encoding="utf-8", errors="surrogateescape"
            ) as output_file:
                for record in records:
                    output_file.write(f"{record}\n")
        else:
            findings = scan_sensitive_records(
                _read_text_records(input_path),
                source_name=str(input_path),
                format_name=options.format,
            )
            for finding in findings:
                print(finding)
    except (OSError, UnicodeError, SensitiveOutputError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _pack_policy(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python pack-policy", add_help=False
    )
    _add_pack_policy_arguments(parser)
    parser.add_argument("--shell", action="store_true")
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    if not options.shell:
        print("pack-policy currently requires --shell.", file=sys.stderr)
        return 2

    policy = resolve_pack_policy(_pack_policy_input(options))
    for name, value in policy.shell_values().items():
        print(f"{name}={shlex.quote(value)}")
    return 0


def _add_pack_policy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--model", default="generic")
    parser.add_argument("--pack-mode", default="normal")
    parser.add_argument("--context-budget-chars", default="0")
    parser.add_argument("--context-budget-tokens", default="")
    parser.add_argument("--chars-per-token", default="2")
    parser.add_argument("--candidate-output", default="0")
    parser.add_argument("--git-percent", default="20")
    parser.add_argument("--memory-percent", default="40")
    parser.add_argument("--verify-percent", default="20")
    parser.add_argument("--contract-percent", default="20")
    parser.add_argument("--local-memory-card-chars", default="1600")
    parser.add_argument("--changed-file-excerpt-limit", default="8")
    parser.add_argument("--changed-file-excerpt-chars", default="4000")
    parser.add_argument("--changed-file-sort", default="smart")
    parser.add_argument("--grill-max-questions", default="8")


def _pack_policy_input(options: argparse.Namespace) -> PackPolicyInput:
    return PackPolicyInput(
        model=options.model,
        pack_mode=options.pack_mode,
        context_budget_chars=options.context_budget_chars,
        context_budget_tokens=options.context_budget_tokens,
        chars_per_token=options.chars_per_token,
        candidate_output=options.candidate_output,
        git_percent=options.git_percent,
        memory_percent=options.memory_percent,
        verify_percent=options.verify_percent,
        contract_percent=options.contract_percent,
        local_memory_card_chars=options.local_memory_card_chars,
        changed_file_excerpt_limit=options.changed_file_excerpt_limit,
        changed_file_excerpt_chars=options.changed_file_excerpt_chars,
        changed_file_sort=options.changed_file_sort,
        grill_max_questions=options.grill_max_questions,
    )


def _model_known(args: Sequence[str]) -> int:
    if len(args) != 1:
        print("model-known requires one model name.", file=sys.stderr)
        return 2
    return 0 if resolve_model(args[0]).known else 1


def _task_pack_git_evidence(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python task-pack-git-evidence", add_help=False
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--goal-file", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--base", default="")
    parser.add_argument("--target-ref-explicit", action="store_true")
    parser.add_argument("--git-repo", action="store_true")
    parser.add_argument("--sort", choices=["smart", "path"], default="smart")
    parser.add_argument("--excerpt-limit", type=int, default=0)
    parser.add_argument("--excerpt-chars", type=int, default=0)
    parser.add_argument("--changed-files-chars", type=int, default=0)
    parser.add_argument("--status-chars", type=int, default=0)
    parser.add_argument("--output-dir", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        goal = Path(options.goal_file).read_text(encoding="utf-8", errors="replace")
        request = ChangeEvidenceRequest(
            project=Path(options.project),
            project_name=options.project_name,
            goal=goal,
            is_git_repo=options.git_repo,
            target_ref=options.target_ref,
            base_ref=options.base,
            target_ref_explicit=options.target_ref_explicit,
            policy=ChangeEvidencePolicy(
                sort_mode=options.sort,
                excerpt_limit=max(0, options.excerpt_limit),
                excerpt_chars=max(0, options.excerpt_chars),
                changed_files_chars=max(0, options.changed_files_chars),
                status_chars=max(0, options.status_chars),
            ),
        )
        evidence = collect_change_evidence(request)
        write_change_evidence_bundle(Path(options.output_dir), evidence, request)
    except (OSError, GitScopeError, ChangeEvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _task_pack_project_docs(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python task-pack-project-docs", add_help=False
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--changed-paths0", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--target-ref-explicit", action="store_true")
    parser.add_argument("--git-repo", action="store_true")
    parser.add_argument("--entry-root", default="")
    parser.add_argument("--entry-backend", default="")
    parser.add_argument("--entry-runtime", default="")
    parser.add_argument("--entry-frontend", default="")
    parser.add_argument("--entry-dolphin", default="")
    parser.add_argument("--entry-contracts", default="")
    parser.add_argument("--domain-map", default="")
    parser.add_argument("--domain-docs", default="")
    parser.add_argument("--adr-directory", default="")
    parser.add_argument("--agent-docs", default="")
    parser.add_argument("--issue-tracker", default="")
    parser.add_argument("--triage-labels", default="")
    parser.add_argument("--output-dir", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        request = ProjectDocsRequest(
            project=Path(options.project),
            is_git_repo=options.git_repo,
            target_ref=options.target_ref,
            target_ref_explicit=options.target_ref_explicit,
            changed_paths=read_nul_paths(Path(options.changed_paths0)),
            entry_docs={
                "root": options.entry_root,
                "backend": options.entry_backend,
                "runtime": options.entry_runtime,
                "frontend": options.entry_frontend,
                "dolphin": options.entry_dolphin,
                "contracts": options.entry_contracts,
            },
            configuration_docs={
                "Domain map": options.domain_map,
                "Domain docs": options.domain_docs,
                "ADR directory": options.adr_directory,
                "Agent docs": options.agent_docs,
                "Issue tracker": options.issue_tracker,
                "Triage labels": options.triage_labels,
            },
        )
        project_docs = collect_project_docs(request)
        write_project_docs_bundle(Path(options.output_dir), project_docs)
    except (OSError, UnicodeError, GitScopeError, ProjectDocsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _verification_plan(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python verification-plan", add_help=False
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--changed-paths0", required=True)
    parser.add_argument("--target-ref", default="HEAD")
    parser.add_argument("--target-ref-explicit", action="store_true")
    parser.add_argument("--verify-contracts", default="")
    parser.add_argument("--verify-backend", default="")
    parser.add_argument("--verify-runtime", default="")
    parser.add_argument("--verify-frontend", default="")
    parser.add_argument("--verify-node", default="")
    parser.add_argument("--verify-python", default="")
    parser.add_argument("--verify-java", default="")
    parser.add_argument("--verify-go", default="")
    parser.add_argument("--verify-rust", default="")
    parser.add_argument("--verify-dolphin", default="")
    parser.add_argument("--verify-shell", default="")
    parser.add_argument("--verify-tests", default="")
    parser.add_argument("--verify-project", default="")
    parser.add_argument("--output-dir", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        request = VerificationPlanRequest(
            project=Path(options.project),
            changed_paths=read_nul_paths(Path(options.changed_paths0)),
            target_ref=options.target_ref,
            target_ref_explicit=options.target_ref_explicit,
            commands=VerificationCommands(
                contracts=options.verify_contracts,
                backend=options.verify_backend,
                runtime=options.verify_runtime,
                frontend=options.verify_frontend,
                node=options.verify_node,
                python=options.verify_python,
                java=options.verify_java,
                go=options.verify_go,
                rust=options.verify_rust,
                dolphin=options.verify_dolphin,
                shell=options.verify_shell,
                tests=options.verify_tests,
                project=options.verify_project,
            ),
        )
        plan = build_verification_plan(request)
        write_verification_plan_bundle(Path(options.output_dir), plan)
    except (OSError, UnicodeError, GitScopeError, VerificationPlanError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _task_pack_memory_evidence(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python task-pack-memory-evidence", add_help=False
    )
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--goal-file", required=True)
    parser.add_argument("--changed-paths0", required=True)
    parser.add_argument("--local-dir", required=True)
    parser.add_argument("--provider", default="local")
    parser.add_argument("--online-command", default="")
    parser.add_argument("--online-limit", default="5")
    parser.add_argument("--online-timeout-seconds", default="8")
    parser.add_argument("--memory-chars", type=int, default=0)
    parser.add_argument("--local-card-chars", type=int, default=0)
    parser.add_argument("--output-dir", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        goal = Path(options.goal_file).read_text(encoding="utf-8", errors="replace")
        request = MemoryEvidenceRequest(
            project_name=options.project_name,
            goal=goal,
            changed_paths=read_nul_paths(Path(options.changed_paths0)),
            provider=options.provider,
            local_dir=Path(options.local_dir),
            online_command=options.online_command,
            online_limit=_positive_int_or_default(options.online_limit, 5),
            online_timeout_seconds=_positive_int_or_default(
                options.online_timeout_seconds, 8
            ),
            memory_chars=max(0, options.memory_chars),
            local_card_chars=max(0, options.local_card_chars),
        )
        evidence = collect_memory_evidence(request)
        write_memory_evidence_bundle(Path(options.output_dir), evidence, request)
    except (OSError, UnicodeError, GitScopeError, MemoryEvidenceError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _task_pack_contract_sections(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python task-pack-contract-sections", add_help=False
    )
    parser.add_argument("--pack-mode", default="normal")
    parser.add_argument("--trigger-rules", default="")
    parser.add_argument("--role-rules", default="")
    parser.add_argument("--workflow-rules", default="")
    parser.add_argument("--target-scope-rules", default="")
    parser.add_argument("--sensitive-output-rules", default="")
    parser.add_argument("--grill-rules", default="")
    parser.add_argument("--memory-sync-rules", default="")
    parser.add_argument("--quality-gates", default="")
    parser.add_argument("--failure-rules", default="")
    parser.add_argument("--subagent-result-contract", default="")
    parser.add_argument("--output-dir", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        request = ContractSectionsRequest(
            pack_mode=options.pack_mode,
            trigger_rules=options.trigger_rules,
            role_rules=options.role_rules,
            workflow_rules=options.workflow_rules,
            target_scope_rules=options.target_scope_rules,
            sensitive_output_rules=options.sensitive_output_rules,
            grill_rules=options.grill_rules,
            memory_sync_rules=options.memory_sync_rules,
            quality_gates=options.quality_gates,
            failure_rules=options.failure_rules,
            subagent_result_contract=options.subagent_result_contract,
        )
        sections = render_contract_sections(request)
        write_contract_sections_bundle(Path(options.output_dir), sections)
    except (OSError, UnicodeError, ContractSectionsError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


def _task_pack_render(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-rails-python task-pack-render", add_help=False
    )
    _add_pack_policy_arguments(parser)
    parser.add_argument("--goal-file", required=True)
    parser.add_argument("--git-evidence", required=True)
    parser.add_argument("--project-docs-entry", required=True)
    parser.add_argument("--agent-contract", required=True)
    parser.add_argument("--subagent-contract", required=True)
    parser.add_argument("--project-configuration", required=True)
    parser.add_argument("--memory-evidence", required=True)
    parser.add_argument("--verification-suggestions", required=True)
    parser.add_argument("--delivery-checklist", required=True)
    parser.add_argument(
        "--tokenizer",
        choices=["auto", "char", "command", "tiktoken", "huggingface", "hf"],
        default="auto",
    )
    parser.add_argument("--tokenizer-command", default="")
    parser.add_argument("--tokenizer-path", default="")
    parser.add_argument("--tiktoken-encoding", default="cl100k_base")
    parser.add_argument("--output", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2

    try:
        request = TaskPackRenderRequest(
            goal=_read_strict_utf8(Path(options.goal_file)),
            display_path=options.output,
            policy=resolve_pack_policy(_pack_policy_input(options)),
            sections=RenderedPackSections(
                git_evidence=_read_strict_utf8(Path(options.git_evidence)),
                project_docs_entry=_read_strict_utf8(
                    Path(options.project_docs_entry)
                ),
                agent_contract=_read_strict_utf8(Path(options.agent_contract)),
                subagent_contract=_read_strict_utf8(
                    Path(options.subagent_contract)
                ),
                project_configuration=_read_strict_utf8(
                    Path(options.project_configuration)
                ),
                memory_evidence=_read_strict_utf8(Path(options.memory_evidence)),
                verification_suggestions=_read_strict_utf8(
                    Path(options.verification_suggestions)
                ),
                delivery_checklist=_read_strict_utf8(
                    Path(options.delivery_checklist)
                ),
            ),
            tokenizer=TokenizerSettings(
                mode=options.tokenizer,
                command=options.tokenizer_command,
                path=options.tokenizer_path,
                tiktoken_encoding=options.tiktoken_encoding,
            ),
        )
        write_task_pack(Path(options.output), request)
    except (OSError, UnicodeError, PackRendererError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


def _positive_int_or_default(value: str, default: int) -> int:
    return int(value) if re.fullmatch(r"[0-9]+", value) and int(value) > 0 else default


def _read_text_records(path: Path):
    with path.open("r", encoding="utf-8", errors="surrogateescape") as input_file:
        for line in input_file:
            if line.endswith("\n"):
                yield line[:-1]
            else:
                yield line


def _read_strict_utf8(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="strict")


def _profile_init(args: Sequence[str]) -> int:
    if len(args) < 2 or args[0] != "--agent-rails-home":
        print("profile-init requires --agent-rails-home PATH.", file=sys.stderr)
        return 2
    return profile_init.main(args[2:], kit_home=Path(args[1]))
