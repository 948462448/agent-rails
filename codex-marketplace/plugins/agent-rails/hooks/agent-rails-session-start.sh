#!/usr/bin/env bash
# Wrapper used by the local Codex marketplace install.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/../../../.." && pwd)}"
exec "$AGENT_RAILS_HOME/hooks/agent-rails-session-start.sh"
