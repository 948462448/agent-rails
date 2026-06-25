#!/usr/bin/env bash
# Wrapper used by the local Codex marketplace install.

set -euo pipefail

AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-/Users/songlei/workspace/agent-rails}"
exec "$AGENT_RAILS_HOME/hooks/agent-rails-session-start.sh"
