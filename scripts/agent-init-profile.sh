#!/usr/bin/env bash
# Compatibility Shell for the Python Profile Init command during migration.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
export AGENT_RAILS_HOME

if ! command -v python3 >/dev/null 2>&1; then
  printf 'python3 is required for agent-rails profile init.\n' >&2
  exit 127
fi

PYTHONDONTWRITEBYTECODE="${PYTHONDONTWRITEBYTECODE:-1}"
export PYTHONDONTWRITEBYTECODE
exec python3 -E "$AGENT_RAILS_HOME/scripts/agent-python-cli.py" \
  profile-init --agent-rails-home "$AGENT_RAILS_HOME" "$@"
