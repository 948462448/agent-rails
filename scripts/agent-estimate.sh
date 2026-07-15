#!/usr/bin/env bash
# Compatibility Shell for the Python estimate command during migration.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
export AGENT_RAILS_HOME

profile_path="$AGENT_RAILS_HOME/profiles/default.profile"
skip_profile=0
args=("$@")
index=0
while [[ "$index" -lt "${#args[@]}" ]]; do
  case "${args[$index]}" in
    --profile)
      next_index=$((index + 1))
      if [[ "$next_index" -lt "${#args[@]}" ]]; then
        profile_path="${args[$next_index]}"
      fi
      index=$((index + 2))
      ;;
    --model|--chars-per-token|--tokenizer|--tokenizer-command|--tokenizer-path|--file)
      index=$((index + 2))
      ;;
    --help|-h)
      skip_profile=1
      index=$((index + 1))
      ;;
    *)
      index=$((index + 1))
      ;;
  esac
done

if [[ "$skip_profile" -ne 1 && -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

export AGENT_RAILS_MODEL
export AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE
export AGENT_RAILS_TOKENIZER
export AGENT_RAILS_TOKENIZER_CMD
export AGENT_RAILS_TOKENIZER_PATH
export AGENT_RAILS_TIKTOKEN_ENCODING

if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required for agent-rails estimate.\n' >&2
  exit 127
fi

PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export PYTHONDONTWRITEBYTECODE
exec python3 -E "$AGENT_RAILS_HOME/scripts/agent-python-cli.py" estimate "$@"
