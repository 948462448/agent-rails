#!/usr/bin/env bash
# Compatibility Shell for the Python Model Preset module.

agent_model_preset_load() {
  [[ "$#" -eq 1 ]] || return 2

  local script_dir kit_home assignments
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  kit_home="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
  assignments="$({
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -E "$kit_home/scripts/agent-python-cli.py" model-preset --shell "$1"
  })" || return $?
  eval "$assignments"
}

agent_model_preset_known() {
  [[ "$#" -eq 1 ]] || return 2
  agent_model_preset_load "$1"
  [[ "$AGENT_RAILS_MODEL_KNOWN" -eq 1 ]]
}

agent_model_preset_budget_for_mode() {
  [[ "$#" -eq 1 ]] || return 2

  case "$1" in
    lite) printf '%s\n' "${AGENT_RAILS_MODEL_LITE_TOKENS:-}" ;;
    normal) printf '%s\n' "${AGENT_RAILS_MODEL_NORMAL_TOKENS:-}" ;;
    deep) printf '%s\n' "${AGENT_RAILS_MODEL_DEEP_TOKENS:-}" ;;
    audit) printf '%s\n' "${AGENT_RAILS_MODEL_AUDIT_TOKENS:-}" ;;
  esac
}
