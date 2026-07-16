#!/usr/bin/env bash
# Claude Code / Codex SessionStart compatibility bootstrap.
set -euo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="$(cd "$script_dir/.." && pwd)"
export AGENT_RAILS_HOME
export PYTHONDONTWRITEBYTECODE=1

exec python3 -I "$AGENT_RAILS_HOME/scripts/agent-python-cli.py" \
  session-start "$@"
