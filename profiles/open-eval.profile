# OpenEval Project Profile for Agent Rails.
# This file is sourced by Agent Rails pack/check commands.

# Model preset: set BEFORE sourcing default.profile so its `:-` defaults pick these up.
# qwen3.7-max: 1M context, normal=60k tokens, deep=160k, audit=320k.
AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-qwen3.7-max}"
AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-deep}"

# shellcheck source=/dev/null
source "$AGENT_RAILS_HOME/profiles/default.profile"

PROJECT_NAME="open-eval"
BASE_REF="origin/master"
# Leave TASK_PACK_PATH unset by default so each git worktree gets an isolated pack:
# ${AGENT_RAILS_CONFIG_HOME}/agent-context/${PROJECT_WORKTREE_SLUG}-task-pack.md
MEMORY_LOCAL_DIR="${AGENT_RAILS_CONFIG_HOME}/memory/open-eval"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"
AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-$AGENT_RAILS_CONFIG_HOME/openmemory.env}"

# Optional online provider. Keep secrets out of this file.
# 1. Create a Memory in https://openmemory.alibaba-inc.com/memory?projectName=open-eval
# 2. Create a table with the Agent Rails memory card fields.
# 3. Export the AccessKey locally, then switch MEMORY_PROVIDER to "hybrid" or "openmemory".
#
# MEMORY_PROVIDER="hybrid"
# OPENMEMORY_BASE_URL="https://debug-openmemory.alibaba-inc.com"
# OPENMEMORY_MEMORY="open_eval_agent_rails"
# OPENMEMORY_INSTANCE="agent_rails_memory_card"
# OPENMEMORY_TOKEN_ENV="OPENMEMORY_ACCESS_KEY"
# OPENMEMORY_LIMIT="5"
# OPENMEMORY_USER_ID="agent-rails"
# OPENMEMORY_SESSION_ID=""
# OPENMEMORY_CARD_ID_FILTER=""
# OPENMEMORY_TAG_FILTER=""
# OPENMEMORY_VECTOR_FIELD="body_vector"
# OPENMEMORY_VECTOR_SOURCE_FIELD="body"

ENTRY_DOC_ROOT="AGENTS.md"
ENTRY_DOC_BACKEND="backend/AGENTS.md"
ENTRY_DOC_RUNTIME="runtime/AGENTS.md"
ENTRY_DOC_FRONTEND="frontend/AGENTS.md"
ENTRY_DOC_DOLPHIN="dolphin/AGENTS.md"
ENTRY_DOC_CONTRACTS="contracts/README.md"

VERIFY_CONTRACTS="make codegen-check"
VERIFY_BACKEND="bash scripts/ci/backend.sh"
VERIFY_RUNTIME="bash scripts/ci/runtime.sh"
VERIFY_FRONTEND="cd frontend && npm run lint"
VERIFY_DOLPHIN="python3 -m py_compile dolphin/tpp_eval_node/rawscript/tpp_eval_dolphin_main.py dolphin/tpp_eval_node/ray_entry/tpp_eval_main.py"
