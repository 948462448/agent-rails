"""Orchestrate Task Pack generation through one Python Application Service."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Mapping, Optional, Tuple

from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import (
    TargetProjectContext,
    TargetProjectError,
    resolve_target_project,
)
from agent_rails.context.change_evidence import (
    ChangeEvidencePolicy,
    ChangeEvidenceRequest,
    collect_change_evidence,
    render_change_sections,
)
from agent_rails.context.contract_sections import ContractSectionsRequest, render_contract_sections
from agent_rails.context.memory_evidence import (
    MemoryEvidenceRequest,
    collect_memory_evidence,
    render_memory_sections,
)
from agent_rails.context.pack_policy import PackPolicy, PackPolicyInput, resolve_pack_policy
from agent_rails.context.task_model import (
    TaskModelRequest,
    build_task_model,
    render_task_model,
)
from agent_rails.context.pack_renderer import (
    PackRendererError,
    RenderedPackSections,
    TaskPackRenderRequest,
    TaskPackRenderResult,
    TokenizerSettings,
    write_task_pack,
)
from agent_rails.context.project_docs import (
    ProjectDocsRequest,
    collect_project_docs,
    render_configuration_section,
    render_entry_sections,
)
from agent_rails.core.paths import AgentRailsPaths
from agent_rails.verification.plan import (
    VerificationCommands,
    VerificationPlan,
    VerificationPlanRequest,
    build_verification_plan,
    render_suggestions,
)


PACK_PROFILE_VARIABLES = (
    "BASE_REF",
    "MEMORY_LOCAL_DIR",
    "MEMORY_PROVIDER",
    "AGENT_RAILS_ONLINE_MEMORY_CMD",
    "AGENT_RAILS_ONLINE_MEMORY_LIMIT",
    "AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS",
    "AGENT_RAILS_MODEL",
    "AGENT_RAILS_PACK_MODE",
    "AGENT_RAILS_GRILL_MAX_QUESTIONS",
    "AGENT_RAILS_CONTEXT_BUDGET_CHARS",
    "AGENT_RAILS_CONTEXT_BUDGET_TOKENS",
    "AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE",
    "AGENT_RAILS_TOKENIZER",
    "AGENT_RAILS_TOKENIZER_CMD",
    "AGENT_RAILS_TOKENIZER_PATH",
    "AGENT_RAILS_TIKTOKEN_ENCODING",
    "AGENT_RAILS_CANDIDATE_OUTPUT",
    "AGENT_RAILS_BUDGET_GIT_PERCENT",
    "AGENT_RAILS_BUDGET_MEMORY_PERCENT",
    "AGENT_RAILS_BUDGET_VERIFY_PERCENT",
    "AGENT_RAILS_BUDGET_CONTRACT_PERCENT",
    "AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS",
    "AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT",
    "AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS",
    "AGENT_RAILS_CHANGED_FILE_SORT",
    "ENTRY_DOC_ROOT",
    "ENTRY_DOC_BACKEND",
    "ENTRY_DOC_RUNTIME",
    "ENTRY_DOC_FRONTEND",
    "ENTRY_DOC_DOLPHIN",
    "ENTRY_DOC_CONTRACTS",
    "DOMAIN_DOC_MAP",
    "DOMAIN_DOC_ROOT",
    "ADR_DIR",
    "AGENT_DOC_DIR",
    "ISSUE_TRACKER_DOC",
    "TRIAGE_LABELS_DOC",
    "AGENT_RAILS_TRIGGER_RULES",
    "AGENT_RAILS_ROLE_RULES",
    "AGENT_RAILS_WORKFLOW_RULES",
    "AGENT_RAILS_TARGET_SCOPE_RULES",
    "AGENT_RAILS_SENSITIVE_OUTPUT_RULES",
    "AGENT_RAILS_GRILL_RULES",
    "AGENT_RAILS_MEMORY_SYNC_RULES",
    "AGENT_RAILS_QUALITY_GATES",
    "AGENT_RAILS_FAILURE_RULES",
    "AGENT_RAILS_SUBAGENT_RESULT_CONTRACT",
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

_TOKENIZER_MODES = frozenset(
    {"auto", "char", "command", "tiktoken", "huggingface", "hf"}
)
_VERIFICATION_FALLBACK = "Run agent-rails check after it is available.\n"


class PackApplicationError(RuntimeError):
    """The Pack request or its Target Project context is invalid."""


@dataclass(frozen=True)
class PackCliOverrides:
    base_ref: Optional[str] = None
    target_ref: str = "HEAD"
    target_ref_explicit: bool = False
    output: Optional[str] = None
    model: Optional[str] = None
    pack_mode: Optional[str] = None
    context_budget_chars: Optional[str] = None
    context_budget_tokens: Optional[str] = None
    tokenizer: Optional[str] = None
    tokenizer_command: Optional[str] = None
    tokenizer_path: Optional[str] = None


@dataclass(frozen=True)
class PackApplicationRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    goal: str
    overrides: PackCliOverrides
    environment: Mapping[str, str]


@dataclass(frozen=True)
class OutputTarget:
    display_path: str
    filesystem_path: Path


@dataclass(frozen=True)
class PreparedPackApplication:
    """Resolved, read-only Pack inputs reusable by composing facades."""

    request: PackApplicationRequest
    context: TargetProjectContext
    values: Mapping[str, str]
    output: OutputTarget
    paths: AgentRailsPaths
    policy: PackPolicy
    tokenizer: TokenizerSettings
    base_ref: str


@dataclass(frozen=True)
class PackApplicationResult:
    project_root: Path
    profile_path: Path
    output: OutputTarget
    pack_mode: str
    resolved_target_sha: Optional[str]
    changed_paths: Tuple[str, ...]
    verification_fallback_used: bool
    policy: PackPolicy
    tokenizer: TokenizerSettings
    render_result: TaskPackRenderResult


def prepare_task_pack(request: PackApplicationRequest) -> PreparedPackApplication:
    """Resolve Pack configuration without collecting evidence or writing output."""

    context = resolve_target_project(
        request.requested_project,
        kit_home=request.kit_home,
        explicit_profile=request.explicit_profile,
        environment=request.environment,
        require_profile=True,
        load_profile=True,
        load_environment_file=True,
        profile_variables=PACK_PROFILE_VARIABLES,
        capture_profile_environment=True,
    )
    values = context.profile_values
    overrides = request.overrides
    config_home = _value(values, "AGENT_RAILS_CONFIG_HOME", _default_config_home(request))
    paths = AgentRailsPaths(request.kit_home, config_home)

    output_display = _override_or_value(
        overrides.output, values, "TASK_PACK_PATH", context.task_pack_path
    )
    output = OutputTarget(
        display_path=output_display,
        filesystem_path=_project_path(context.root, output_display),
    )
    tokenizer_mode = _override_or_value(
        overrides.tokenizer, values, "AGENT_RAILS_TOKENIZER", "auto"
    )
    if tokenizer_mode not in _TOKENIZER_MODES:
        raise PackApplicationError(f"Unknown tokenizer: {tokenizer_mode}")

    policy = _resolve_policy(values, overrides)
    base_ref = _override_or_value(overrides.base_ref, values, "BASE_REF", "")
    tokenizer = TokenizerSettings(
        mode=tokenizer_mode,
        command=_override_or_value(
            overrides.tokenizer_command,
            values,
            "AGENT_RAILS_TOKENIZER_CMD",
            "",
        ),
        path=_project_path_text(
            context.root,
            _override_or_value(
                overrides.tokenizer_path,
                values,
                "AGENT_RAILS_TOKENIZER_PATH",
                "",
            ),
        ),
        tiktoken_encoding=_value(
            values, "AGENT_RAILS_TIKTOKEN_ENCODING", "cl100k_base"
        ),
        working_directory=context.root,
        environment=context.profile_environment,
    )
    return PreparedPackApplication(
        request=request,
        context=context,
        values=values,
        output=output,
        paths=paths,
        policy=policy,
        tokenizer=tokenizer,
        base_ref=base_ref,
    )


def generate_task_pack(request: PackApplicationRequest) -> PackApplicationResult:
    """Resolve configuration, collect evidence, and publish one Task Pack."""

    try:
        return _generate_task_pack(request)
    except (
        FileNotFoundError,
        TargetProjectError,
        ProfileLoadError,
        PackApplicationError,
        PackRendererError,
    ):
        raise
    except Exception as exc:
        raise PackApplicationError(str(exc)) from exc


def _generate_task_pack(request: PackApplicationRequest) -> PackApplicationResult:
    """Internal generation after the public Application error boundary."""

    prepared = prepare_task_pack(request)
    context = prepared.context
    values = prepared.values
    overrides = request.overrides
    output = prepared.output
    policy = prepared.policy
    base_ref = prepared.base_ref
    change_request = ChangeEvidenceRequest(
        project=context.root,
        project_name=context.project_name,
        goal=request.goal,
        is_git_repo=context.is_git_repo,
        target_ref=overrides.target_ref,
        base_ref=base_ref,
        target_ref_explicit=overrides.target_ref_explicit,
        policy=ChangeEvidencePolicy(
            sort_mode=policy.density.changed_file_sort,
            excerpt_limit=policy.density.changed_file_excerpt_limit,
            excerpt_chars=policy.density.changed_file_excerpt_chars,
            changed_files_chars=policy.budget.changed_files_chars,
            status_chars=policy.budget.status_chars,
        ),
    )
    change_evidence = collect_change_evidence(change_request)
    resolved_target_sha = (
        change_evidence.scope.target_sha if change_evidence.scope is not None else None
    )
    downstream_target = overrides.target_ref
    if overrides.target_ref_explicit:
        if not resolved_target_sha:
            raise PackApplicationError(
                "Task Pack Git evidence did not resolve target ref: "
                f"{overrides.target_ref}"
            )
        downstream_target = resolved_target_sha

    project_docs = collect_project_docs(
        ProjectDocsRequest(
            project=context.root,
            is_git_repo=context.is_git_repo,
            target_ref=downstream_target,
            target_ref_explicit=overrides.target_ref_explicit,
            changed_paths=change_evidence.changed_paths,
            entry_docs={
                "root": _value(values, "ENTRY_DOC_ROOT", ""),
                "backend": _value(values, "ENTRY_DOC_BACKEND", ""),
                "runtime": _value(values, "ENTRY_DOC_RUNTIME", ""),
                "frontend": _value(values, "ENTRY_DOC_FRONTEND", ""),
                "dolphin": _value(values, "ENTRY_DOC_DOLPHIN", ""),
                "contracts": _value(values, "ENTRY_DOC_CONTRACTS", ""),
            },
            configuration_docs={
                "Domain map": _value(values, "DOMAIN_DOC_MAP", ""),
                "Domain docs": _value(values, "DOMAIN_DOC_ROOT", ""),
                "ADR directory": _value(values, "ADR_DIR", ""),
                "Agent docs": _value(values, "AGENT_DOC_DIR", ""),
                "Issue tracker": _value(values, "ISSUE_TRACKER_DOC", ""),
                "Triage labels": _value(values, "TRIAGE_LABELS_DOC", ""),
            },
        )
    )
    contract_sections = render_contract_sections(
        ContractSectionsRequest(
            pack_mode=policy.density.mode,
            trigger_rules=_value(values, "AGENT_RAILS_TRIGGER_RULES", ""),
            role_rules=_value(values, "AGENT_RAILS_ROLE_RULES", ""),
            workflow_rules=_value(values, "AGENT_RAILS_WORKFLOW_RULES", ""),
            target_scope_rules=_value(values, "AGENT_RAILS_TARGET_SCOPE_RULES", ""),
            sensitive_output_rules=_value(values, "AGENT_RAILS_SENSITIVE_OUTPUT_RULES", ""),
            grill_rules=_value(values, "AGENT_RAILS_GRILL_RULES", ""),
            memory_sync_rules=_value(values, "AGENT_RAILS_MEMORY_SYNC_RULES", ""),
            quality_gates=_value(values, "AGENT_RAILS_QUALITY_GATES", ""),
            failure_rules=_value(values, "AGENT_RAILS_FAILURE_RULES", ""),
            subagent_result_contract=_value(
                values, "AGENT_RAILS_SUBAGENT_RESULT_CONTRACT", ""
            ),
        )
    )

    memory_local_dir = _value(
        values,
        "MEMORY_LOCAL_DIR",
        prepared.paths.default_memory_dir(context.project_name),
    )
    memory_request = MemoryEvidenceRequest(
        project_name=context.project_name,
        goal=request.goal,
        changed_paths=change_evidence.changed_paths,
        provider=_value(values, "MEMORY_PROVIDER", "local"),
        local_dir=_project_path(context.root, memory_local_dir),
        online_command=_value(values, "AGENT_RAILS_ONLINE_MEMORY_CMD", ""),
        online_limit=_positive_or_default(
            _value(values, "AGENT_RAILS_ONLINE_MEMORY_LIMIT", "5"), 5
        ),
        online_timeout_seconds=_positive_or_default(
            _value(values, "AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS", "8"), 8
        ),
        memory_chars=policy.budget.memory_chars,
        local_card_chars=policy.density.local_memory_card_chars,
        working_directory=context.root,
    )
    memory_evidence = collect_memory_evidence(memory_request)

    verification_fallback_used = False
    verification = VerificationPlan(steps=())
    try:
        verification = build_verification_plan(
            VerificationPlanRequest(
                project=context.root,
                changed_paths=change_evidence.changed_paths,
                target_ref=downstream_target,
                target_ref_explicit=overrides.target_ref_explicit,
                commands=_verification_commands(values),
            )
        )
        verification_suggestions = render_suggestions(verification)
    except Exception:
        # Verification suggestions are optional evidence. Preserve the legacy
        # non-fatal boundary even if a Target Project command is malformed.
        verification_fallback_used = True
        verification_suggestions = _VERIFICATION_FALLBACK

    task_model = render_task_model(
        build_task_model(
            TaskModelRequest(
                goal=request.goal,
                changed_paths=change_evidence.changed_paths,
                code_evidence=change_evidence.task_code_records,
                verification=verification,
            )
        )
    )

    render_request = TaskPackRenderRequest(
        goal=request.goal,
        display_path=output.display_path,
        policy=policy,
        sections=RenderedPackSections(
            git_evidence=render_change_sections(change_evidence, change_request),
            project_docs_entry=render_entry_sections(project_docs),
            task_model=task_model,
            agent_contract=contract_sections.agent_contract,
            subagent_contract=contract_sections.subagent_contract,
            project_configuration=render_configuration_section(project_docs),
            memory_evidence=render_memory_sections(memory_evidence, memory_request),
            verification_suggestions=verification_suggestions,
            delivery_checklist=contract_sections.delivery_checklist,
        ),
        tokenizer=prepared.tokenizer,
    )
    render_result = write_task_pack(output.filesystem_path, render_request)
    return PackApplicationResult(
        project_root=context.root,
        profile_path=Path(context.profile_path),
        output=output,
        pack_mode=policy.density.mode,
        resolved_target_sha=resolved_target_sha,
        changed_paths=change_evidence.changed_paths,
        verification_fallback_used=verification_fallback_used,
        policy=policy,
        tokenizer=prepared.tokenizer,
        render_result=render_result,
    )


def _resolve_policy(values: Mapping[str, str], overrides: PackCliOverrides) -> PackPolicy:
    return resolve_pack_policy(
        PackPolicyInput(
            model=_override_or_value(overrides.model, values, "AGENT_RAILS_MODEL", "generic"),
            pack_mode=_override_or_value(
                overrides.pack_mode, values, "AGENT_RAILS_PACK_MODE", "normal"
            ),
            context_budget_chars=_override_or_value(
                overrides.context_budget_chars,
                values,
                "AGENT_RAILS_CONTEXT_BUDGET_CHARS",
                "0",
            ),
            context_budget_tokens=_override_or_value(
                overrides.context_budget_tokens,
                values,
                "AGENT_RAILS_CONTEXT_BUDGET_TOKENS",
                "",
            ),
            chars_per_token=_value(values, "AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE", "2"),
            candidate_output=_value(values, "AGENT_RAILS_CANDIDATE_OUTPUT", "0"),
            git_percent=_value(values, "AGENT_RAILS_BUDGET_GIT_PERCENT", "20"),
            memory_percent=_value(values, "AGENT_RAILS_BUDGET_MEMORY_PERCENT", "40"),
            verify_percent=_value(values, "AGENT_RAILS_BUDGET_VERIFY_PERCENT", "20"),
            contract_percent=_value(values, "AGENT_RAILS_BUDGET_CONTRACT_PERCENT", "20"),
            local_memory_card_chars=_value(
                values, "AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS", "1600"
            ),
            changed_file_excerpt_limit=_value(
                values, "AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT", "8"
            ),
            changed_file_excerpt_chars=_value(
                values, "AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS", "4000"
            ),
            changed_file_sort=_value(values, "AGENT_RAILS_CHANGED_FILE_SORT", "smart"),
            grill_max_questions=_value(values, "AGENT_RAILS_GRILL_MAX_QUESTIONS", "8"),
        )
    )


def _verification_commands(values: Mapping[str, str]) -> VerificationCommands:
    return VerificationCommands(
        contracts=_value(values, "VERIFY_CONTRACTS", ""),
        backend=_value(values, "VERIFY_BACKEND", ""),
        runtime=_value(values, "VERIFY_RUNTIME", ""),
        frontend=_value(values, "VERIFY_FRONTEND", ""),
        node=_value(values, "VERIFY_NODE", ""),
        python=_value(values, "VERIFY_PYTHON", ""),
        java=_value(values, "VERIFY_JAVA", ""),
        go=_value(values, "VERIFY_GO", ""),
        rust=_value(values, "VERIFY_RUST", ""),
        dolphin=_value(values, "VERIFY_DOLPHIN", ""),
        shell=_value(values, "VERIFY_SHELL", ""),
        tests=_value(values, "VERIFY_TESTS", ""),
        project=_value(values, "VERIFY_PROJECT", ""),
    )


def _value(values: Mapping[str, str], name: str, default: str) -> str:
    return values.get(name) or default


def _override_or_value(
    override: Optional[str], values: Mapping[str, str], name: str, default: str
) -> str:
    return override or _value(values, name, default)


def _default_config_home(request: PackApplicationRequest) -> str:
    configured = request.environment.get("AGENT_RAILS_CONFIG_HOME")
    if configured:
        return configured
    home = request.environment.get("HOME", str(Path.home()))
    return f"{home}/.agent-rails"


def _project_path(project: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else project / path


def _project_path_text(project: Path, value: str) -> str:
    return str(_project_path(project, value)) if value else ""


def _positive_or_default(value: str, default: int) -> int:
    return int(value) if re.fullmatch(r"[0-9]+", value) and int(value) > 0 else default
