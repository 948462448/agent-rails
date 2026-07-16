"""Render the stable contract sections of an Agent Rails Task Pack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agent_rails.context.markdown import display_text


class ContractSectionsError(RuntimeError):
    """Raised when contract sections cannot be rendered or written safely."""


@dataclass(frozen=True)
class ContractSectionsRequest:
    pack_mode: str = "normal"
    trigger_rules: str = ""
    role_rules: str = ""
    workflow_rules: str = ""
    target_scope_rules: str = ""
    sensitive_output_rules: str = ""
    grill_rules: str = ""
    memory_sync_rules: str = ""
    quality_gates: str = ""
    failure_rules: str = ""
    subagent_result_contract: str = ""


@dataclass(frozen=True)
class ContractSections:
    agent_contract: str
    subagent_contract: str
    delivery_checklist: str


def render_contract_sections(request: ContractSectionsRequest) -> ContractSections:
    """Render independently placeable contract sections in stable order."""

    contract_parts = [
        "## Agent Rails Contract\n\n",
        "### Trigger Matrix\n\n",
        _render_bullets(request.trigger_rules),
        "\n### Role In This Task\n\n",
        _render_bullets(request.role_rules),
        "\n### Workflow Rules\n\n",
        _render_bullets(request.workflow_rules),
        "\n### Target Scope Rules\n\n",
        _render_bullets(request.target_scope_rules),
        "\n### Sensitive Output Rules\n\n",
        _render_bullets(request.sensitive_output_rules),
        "\n### Grill Gate\n\n",
    ]
    if request.pack_mode == "lite":
        contract_parts.append(
            "- Lite mode active: do not run a full grill; preserve scope, "
            "memory, verification, and checklist value.\n"
        )
    contract_parts.extend(
        [
            _render_bullets(request.grill_rules),
            "\n### Memory Sync Rules\n\n",
            _render_bullets(request.memory_sync_rules),
            "\n### Quality Gates\n\n",
            _render_bullets(request.quality_gates),
            "\n### Failure Rules\n\n",
            _render_bullets(request.failure_rules),
            "\n",
        ]
    )

    subagent_contract = (
        "## Subagent Result Contract\n\n"
        "When delegating work to a subagent, require the final subagent response "
        "to include:\n\n"
        f"{_render_bullets(request.subagent_result_contract)}\n"
    )
    delivery_checklist = (
        "## Delivery Checklist\n\n"
        "- What changed\n"
        "- What was verified\n"
        "- What was not verified\n"
        "- Residual risks\n"
        "- Next action suggestions: fix / do not fix / later\n"
    )
    return ContractSections(
        agent_contract="".join(contract_parts),
        subagent_contract=subagent_contract,
        delivery_checklist=delivery_checklist,
    )


def write_contract_sections_bundle(
    output_dir: Path, sections: ContractSections
) -> None:
    """Write the three sections separately so callers can preserve pack order."""

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_utf8(output_dir / "agent-contract.md", sections.agent_contract)
        _write_utf8(output_dir / "subagent-contract.md", sections.subagent_contract)
        _write_utf8(output_dir / "delivery-checklist.md", sections.delivery_checklist)
    except (OSError, UnicodeError) as exc:
        raise ContractSectionsError(
            f"Unable to write Task Pack contract sections: {output_dir}"
        ) from exc


def _render_bullets(value: str) -> str:
    if value == "":
        return "- None configured.\n"

    rendered: list[str] = []
    for line in value.split("\n"):
        if line == "":
            continue
        rendered.append(f"- {display_text(line)}\n")
    return "".join(rendered)


def _write_utf8(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8", errors="strict"))
