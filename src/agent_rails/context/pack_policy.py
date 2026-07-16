"""Resolve Task Pack model, density, and context-budget policy."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Optional

from agent_rails.models.presets import ResolvedModel, resolve_model, shell_values as model_shell_values


@dataclass(frozen=True)
class PackPolicyInput:
    model: str = "generic"
    pack_mode: str = "normal"
    context_budget_chars: str = "0"
    context_budget_tokens: str = ""
    chars_per_token: str = "2"
    candidate_output: str = "0"
    git_percent: str = "20"
    memory_percent: str = "40"
    verify_percent: str = "20"
    contract_percent: str = "20"
    local_memory_card_chars: str = "1600"
    changed_file_excerpt_limit: str = "8"
    changed_file_excerpt_chars: str = "4000"
    changed_file_sort: str = "smart"
    grill_max_questions: str = "8"


@dataclass(frozen=True)
class PackDensity:
    mode: str
    local_memory_card_chars: int
    changed_file_excerpt_limit: int
    changed_file_excerpt_chars: int
    changed_file_sort: str
    grill_max_questions: int


@dataclass(frozen=True)
class ContextBudget:
    chars_per_token: int
    total_chars: int
    effective_tokens: Optional[int]
    source: str
    token_allocator_active: bool
    candidate_output_active: bool
    git_percent: int
    memory_percent: int
    verify_percent: int
    contract_percent: int
    git_chars: int
    memory_chars: int
    verify_chars: int
    contract_chars: int
    changed_files_chars: int
    status_chars: int


@dataclass(frozen=True)
class PackPolicy:
    model: ResolvedModel
    density: PackDensity
    budget: ContextBudget

    def shell_values(self) -> Mapping[str, str]:
        values = dict(model_shell_values(self.model.requested))
        values.update(
            {
                "AGENT_RAILS_PACK_MODE": self.density.mode,
                "AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE": str(self.budget.chars_per_token),
                "AGENT_RAILS_CONTEXT_BUDGET_CHARS": str(self.budget.total_chars),
                "AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE": _optional_int(
                    self.budget.effective_tokens
                ),
                "AGENT_RAILS_CONTEXT_BUDGET_SOURCE": self.budget.source,
                "AGENT_RAILS_TOKEN_ALLOCATOR_ACTIVE": _bool_int(
                    self.budget.token_allocator_active
                ),
                "AGENT_RAILS_CANDIDATE_OUTPUT_ACTIVE": _bool_int(
                    self.budget.candidate_output_active
                ),
                "AGENT_RAILS_BUDGET_GIT_PERCENT": str(self.budget.git_percent),
                "AGENT_RAILS_BUDGET_MEMORY_PERCENT": str(self.budget.memory_percent),
                "AGENT_RAILS_BUDGET_VERIFY_PERCENT": str(self.budget.verify_percent),
                "AGENT_RAILS_BUDGET_CONTRACT_PERCENT": str(self.budget.contract_percent),
                "AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS": str(
                    self.density.local_memory_card_chars
                ),
                "AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT": str(
                    self.density.changed_file_excerpt_limit
                ),
                "AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS": str(
                    self.density.changed_file_excerpt_chars
                ),
                "AGENT_RAILS_CHANGED_FILE_SORT": self.density.changed_file_sort,
                "AGENT_RAILS_GRILL_MAX_QUESTIONS": str(
                    self.density.grill_max_questions
                ),
                "git_budget_chars": str(self.budget.git_chars),
                "memory_budget_chars": str(self.budget.memory_chars),
                "verify_budget_chars": str(self.budget.verify_chars),
                "contract_budget_chars": str(self.budget.contract_chars),
                "changed_files_budget_chars": str(self.budget.changed_files_chars),
                "status_budget_chars": str(self.budget.status_chars),
            }
        )
        return values


def resolve_pack_policy(settings: PackPolicyInput) -> PackPolicy:
    mode = settings.pack_mode if settings.pack_mode in {"lite", "normal", "deep", "audit"} else "normal"
    model = resolve_model(settings.model)
    chars_per_token = _positive_or_default(settings.chars_per_token, 2)
    budget_chars_input = _optional_positive(settings.context_budget_chars)
    budget_tokens_input = _optional_positive(settings.context_budget_tokens)

    total_chars = 0
    effective_tokens: Optional[int] = None
    source = "unbounded"
    token_allocator_active = False
    candidate_output_active = False

    if budget_chars_input is not None:
        total_chars = budget_chars_input
        effective_tokens = total_chars // chars_per_token
        source = "char budget"
    elif budget_tokens_input is not None:
        effective_tokens = budget_tokens_input
        total_chars = effective_tokens * chars_per_token
        source = "token budget"
        token_allocator_active = True
    else:
        preset_tokens = model.budget_for_mode(mode)
        if preset_tokens is not None:
            effective_tokens = preset_tokens
            total_chars = effective_tokens * chars_per_token
            source = "model preset"
            token_allocator_active = True

    if settings.candidate_output == "1":
        total_chars = 0
        effective_tokens = None
        source = "request-hook candidate output"
        token_allocator_active = False
        candidate_output_active = True

    git_percent = _percent_or_default(settings.git_percent, 20)
    memory_percent = _percent_or_default(settings.memory_percent, 40)
    verify_percent = _percent_or_default(settings.verify_percent, 20)
    contract_percent = _percent_or_default(settings.contract_percent, 20)

    local_memory_card_chars = _nonnegative_or_default(
        settings.local_memory_card_chars, 1600
    )
    changed_file_excerpt_limit = _nonnegative_or_default(
        settings.changed_file_excerpt_limit, 5
    )
    changed_file_excerpt_chars = _nonnegative_or_default(
        settings.changed_file_excerpt_chars, 4000
    )
    grill_max_questions = _nonnegative_or_default(settings.grill_max_questions, 8)

    caps = {
        "lite": (4, 900, 700),
        "normal": (6, 1600, 1000),
        "deep": (8, 2200, 1400),
    }.get(mode)
    if caps is not None:
        excerpt_limit_cap, excerpt_chars_cap, memory_chars_cap = caps
        changed_file_excerpt_limit = min(changed_file_excerpt_limit, excerpt_limit_cap)
        changed_file_excerpt_chars = min(changed_file_excerpt_chars, excerpt_chars_cap)
        local_memory_card_chars = min(local_memory_card_chars, memory_chars_cap)

    changed_file_sort = settings.changed_file_sort
    if changed_file_sort not in {"smart", "path"}:
        changed_file_sort = "smart"

    def section_budget(percent: int) -> int:
        if candidate_output_active or token_allocator_active or total_chars <= 0:
            return 0
        return total_chars * percent // 100

    git_chars = section_budget(git_percent)
    memory_chars = section_budget(memory_percent)
    verify_chars = section_budget(verify_percent)
    contract_chars = section_budget(contract_percent)
    changed_files_chars = git_chars
    status_chars = git_chars
    if git_chars > 0:
        changed_files_chars = max(1, git_chars // 2)
        status_chars = git_chars - git_chars // 2

    return PackPolicy(
        model=model,
        density=PackDensity(
            mode=mode,
            local_memory_card_chars=local_memory_card_chars,
            changed_file_excerpt_limit=changed_file_excerpt_limit,
            changed_file_excerpt_chars=changed_file_excerpt_chars,
            changed_file_sort=changed_file_sort,
            grill_max_questions=grill_max_questions,
        ),
        budget=ContextBudget(
            chars_per_token=chars_per_token,
            total_chars=total_chars,
            effective_tokens=effective_tokens,
            source=source,
            token_allocator_active=token_allocator_active,
            candidate_output_active=candidate_output_active,
            git_percent=git_percent,
            memory_percent=memory_percent,
            verify_percent=verify_percent,
            contract_percent=contract_percent,
            git_chars=git_chars,
            memory_chars=memory_chars,
            verify_chars=verify_chars,
            contract_chars=contract_chars,
            changed_files_chars=changed_files_chars,
            status_chars=status_chars,
        ),
    )


def _positive_or_default(value: str, default: int) -> int:
    parsed = _ascii_integer(value)
    return parsed if parsed is not None and parsed > 0 else default


def _nonnegative_or_default(value: str, default: int) -> int:
    parsed = _ascii_integer(value)
    return parsed if parsed is not None else default


def _optional_positive(value: str) -> Optional[int]:
    parsed = _ascii_integer(value)
    return parsed if parsed is not None and parsed > 0 else None


def _percent_or_default(value: str, default: int) -> int:
    parsed = _ascii_integer(value)
    return parsed if parsed is not None and parsed <= 100 else default


def _ascii_integer(value: str) -> Optional[int]:
    if re.fullmatch(r"[0-9]+", value) is None:
        return None
    return int(value)


def _optional_int(value: Optional[int]) -> str:
    return "" if value is None else str(value)


def _bool_int(value: bool) -> str:
    return "1" if value else "0"
