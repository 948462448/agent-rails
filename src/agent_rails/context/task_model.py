"""Build a bounded, evidence-backed task decomposition for Task Packs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from agent_rails.context.markdown import display_text, markdown_code
from agent_rails.evidence.code import CodeEvidenceRecord, CodeEvidenceRole
from agent_rails.verification.plan import VerificationPlan


_MAX_EVIDENCE_PER_ROLE = 2
_MAX_CHANGED_PATHS = 8


@dataclass(frozen=True)
class TaskModelRequest:
    goal: str
    changed_paths: Tuple[str, ...]
    code_evidence: Tuple[CodeEvidenceRecord, ...]
    verification: VerificationPlan


@dataclass(frozen=True)
class TaskModel:
    behavior_invariants: Tuple[str, ...]
    change_plan: Tuple[str, ...]
    acceptance_criteria: Tuple[str, ...]
    do_not_change: Tuple[str, ...]
    open_assumptions: Tuple[str, ...]


def build_task_model(request: TaskModelRequest) -> TaskModel:
    """Derive a planning frame from frozen evidence without inventing intent."""

    implementation = _evidence_for_role(
        request.code_evidence, CodeEvidenceRole.IMPLEMENTATION
    )
    verification = _evidence_for_role(
        request.code_evidence, CodeEvidenceRole.VERIFICATION
    )
    scope = request.changed_paths[:_MAX_CHANGED_PATHS]
    behavior_invariants = [
        "Preserve behavior outside the planned scope; do not revert unrelated worktree changes.",
        "Treat code evidence as a candidate location, then verify callers, ownership, and tests before editing.",
    ]
    if request.verification.steps:
        behavior_invariants.append(
            "Do not mark the task complete until the selected Verification Plan has passed."
        )

    change_plan = []
    if implementation:
        change_plan.append(
            "Inspect likely implementation locations: "
            + ", ".join(_location(record) for record in implementation)
            + "."
        )
    if scope:
        change_plan.append(
            "Keep the first edit inside the observed change scope: "
            + ", ".join(markdown_code(path) for path in scope)
            + "."
        )
    else:
        change_plan.append(
            "Choose the first edit only after confirming the candidate implementation location."
        )
    if verification:
        change_plan.append(
            "Update or add a focused verification location: "
            + ", ".join(_location(record) for record in verification)
            + "."
        )
    else:
        change_plan.append(
            "Identify a focused test or reproducible fixture before changing behavior."
        )
    if request.verification.steps:
        change_plan.append("Run the selected Verification Plan after the focused change.")

    acceptance = [
        "Demonstrate the requested outcome with a focused fixture or targeted test: "
        + markdown_code(_single_line(request.goal))
        + ".",
        "Explain any changed path outside this task model before treating the result as complete.",
    ]
    if request.verification.steps:
        acceptance.extend(
            "Verification category passes: " + markdown_code(step.reason) + "."
            for step in request.verification.steps
        )
    else:
        acceptance.append(
            "No automated verification command was selected; record a manual acceptance check."
        )

    do_not_change = [
        "Do not move the Git target or base ref while applying this model.",
        "Do not copy credentials, opaque Profile commands, or raw failure output into the plan.",
    ]
    if scope:
        do_not_change.append(
            "Do not overwrite pre-existing worktree changes outside the listed scope."
        )

    assumptions = [
        "Code evidence is a candidate location, not proof of root cause or complete call coverage.",
        "Product-specific acceptance remains unresolved until a fixture, test, or domain rule proves it."
        if not request.verification.steps
        else "Selected verification proves configured checks only; inspect domain-specific edge cases separately.",
    ]
    return TaskModel(
        behavior_invariants=tuple(behavior_invariants),
        change_plan=tuple(change_plan),
        acceptance_criteria=tuple(acceptance),
        do_not_change=tuple(do_not_change),
        open_assumptions=tuple(assumptions),
    )


def render_task_model(model: TaskModel) -> str:
    return "".join(
        (
            "## Task Model\n\n",
            _render_group("Behavior Invariants", model.behavior_invariants),
            _render_group("Change Plan", model.change_plan),
            _render_group("Acceptance Criteria", model.acceptance_criteria),
            _render_group("Do Not Change", model.do_not_change),
            _render_group("Open Assumptions", model.open_assumptions),
        )
    )


def _evidence_for_role(
    records: Tuple[CodeEvidenceRecord, ...], role: CodeEvidenceRole
) -> Tuple[CodeEvidenceRecord, ...]:
    return tuple(record for record in records if record.role is role)[:_MAX_EVIDENCE_PER_ROLE]


def _location(record: CodeEvidenceRecord) -> str:
    suffix = f":{record.line}" if record.line > 0 else ""
    return markdown_code(f"{record.path}{suffix}")


def _render_group(title: str, values: Tuple[str, ...]) -> str:
    rendered = "".join(f"- {display_text(value)}\n" for value in values)
    return f"### {title}\n\n{rendered}\n"


def _single_line(value: str) -> str:
    return " ".join(display_text(value).split())[:240] or "Unspecified goal"
