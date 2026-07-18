from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Optional


@dataclass(frozen=True)
class ModelPreset:
    canonical: str
    context_tokens: int
    max_input_tokens: int
    max_output_tokens: int
    max_input_thinking_tokens: Optional[int] = None
    max_reasoning_tokens: Optional[int] = None
    rpm: Optional[int] = None
    tpm: Optional[int] = None
    pack_budgets: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class ResolvedModel:
    requested: str
    canonical: str
    known: bool
    preset: Optional[ModelPreset]

    def budget_for_mode(self, mode: str) -> Optional[int]:
        if self.preset is None:
            return None
        return self.preset.pack_budgets.get(mode)


_PRESETS = {
    "qwen3.7-max": ModelPreset(
        canonical="qwen3.7-max",
        context_tokens=1_000_000,
        max_input_tokens=991_000,
        max_input_thinking_tokens=983_000,
        max_output_tokens=64_000,
        max_reasoning_tokens=256_000,
        pack_budgets={"lite": 24_000, "normal": 60_000, "deep": 160_000, "audit": 320_000},
    ),
    "deepseek-v4-pro": ModelPreset(
        canonical="deepseek-v4-pro",
        context_tokens=1_000_000,
        max_input_tokens=1_000_000,
        max_output_tokens=384_000,
        rpm=15_000,
        tpm=1_200_000,
        pack_budgets={"lite": 24_000, "normal": 60_000, "deep": 160_000, "audit": 320_000},
    ),
    "deepseek-v4-flash": ModelPreset(
        canonical="deepseek-v4-flash",
        context_tokens=1_000_000,
        max_input_tokens=1_000_000,
        max_output_tokens=384_000,
        rpm=15_000,
        tpm=1_200_000,
        pack_budgets={"lite": 24_000, "normal": 60_000, "deep": 160_000, "audit": 320_000},
    ),
    "glm5.1": ModelPreset(
        canonical="glm5.1",
        context_tokens=202_000,
        max_input_tokens=202_000,
        max_input_thinking_tokens=166_000,
        max_output_tokens=128_000,
        pack_budgets={"lite": 12_000, "normal": 24_000, "deep": 60_000, "audit": 100_000},
    ),
}

_ALIASES = {
    "qwen3.7-max": "qwen3.7-max",
    "qwen-3.7-max": "qwen3.7-max",
    "qwen3.7max": "qwen3.7-max",
    "deepseek-v4-pro": "deepseek-v4-pro",
    "deepseekv4pro": "deepseek-v4-pro",
    "deepseek-v4pro": "deepseek-v4-pro",
    "deepseek-v4": "deepseek-v4-pro",
    "deepseek4-pro": "deepseek-v4-pro",
    "deepseek-v4-flash": "deepseek-v4-flash",
    "deepseekv4flash": "deepseek-v4-flash",
    "deepseek-v4flash": "deepseek-v4-flash",
    "deepseek4-flash": "deepseek-v4-flash",
    "glm5.1": "glm5.1",
    "glm-5.1": "glm5.1",
    "glm51": "glm5.1",
}


def normalize_model_key(model: str) -> str:
    return model.lower().replace(" ", "-").replace("_", "-")


def resolve_model(model: str) -> ResolvedModel:
    key = normalize_model_key(model)
    if key == "generic":
        return ResolvedModel(requested=model, canonical=model, known=True, preset=None)

    canonical = _ALIASES.get(key)
    if canonical is None:
        return ResolvedModel(requested=model, canonical=model, known=False, preset=None)

    preset = _PRESETS[canonical]
    return ResolvedModel(requested=model, canonical=preset.canonical, known=True, preset=preset)


def shell_values(model: str) -> Mapping[str, str]:
    resolved = resolve_model(model)
    preset = resolved.preset
    values = {
        "AGENT_RAILS_MODEL_KNOWN": "1" if resolved.known else "0",
        "AGENT_RAILS_MODEL_PRESET_FOUND": "1" if preset is not None else "0",
        "AGENT_RAILS_MODEL_CANONICAL": resolved.canonical,
        "AGENT_RAILS_MODEL_CONTEXT_TOKENS": "",
        "AGENT_RAILS_MODEL_MAX_INPUT_TOKENS": "",
        "AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS": "",
        "AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS": "",
        "AGENT_RAILS_MODEL_MAX_REASONING_TOKENS": "",
        "AGENT_RAILS_MODEL_RPM": "",
        "AGENT_RAILS_MODEL_TPM": "",
        "AGENT_RAILS_MODEL_LITE_TOKENS": "",
        "AGENT_RAILS_MODEL_NORMAL_TOKENS": "",
        "AGENT_RAILS_MODEL_DEEP_TOKENS": "",
        "AGENT_RAILS_MODEL_AUDIT_TOKENS": "",
    }
    if preset is not None:
        values.update(
            {
                "AGENT_RAILS_MODEL_CONTEXT_TOKENS": str(preset.context_tokens),
                "AGENT_RAILS_MODEL_MAX_INPUT_TOKENS": str(preset.max_input_tokens),
                "AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS": _optional_int(
                    preset.max_input_thinking_tokens
                ),
                "AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS": str(preset.max_output_tokens),
                "AGENT_RAILS_MODEL_MAX_REASONING_TOKENS": _optional_int(
                    preset.max_reasoning_tokens
                ),
                "AGENT_RAILS_MODEL_RPM": _optional_int(preset.rpm),
                "AGENT_RAILS_MODEL_TPM": _optional_int(preset.tpm),
                "AGENT_RAILS_MODEL_LITE_TOKENS": str(preset.pack_budgets["lite"]),
                "AGENT_RAILS_MODEL_NORMAL_TOKENS": str(preset.pack_budgets["normal"]),
                "AGENT_RAILS_MODEL_DEEP_TOKENS": str(preset.pack_budgets["deep"]),
                "AGENT_RAILS_MODEL_AUDIT_TOKENS": str(preset.pack_budgets["audit"]),
            }
        )
    return values


def _optional_int(value: Optional[int]) -> str:
    return "" if value is None else str(value)
