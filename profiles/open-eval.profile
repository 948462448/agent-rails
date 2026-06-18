# OpenEval Project Profile for Agent Rails.
# This file is sourced by Agent Rails pack/check commands.

PROJECT_NAME="open-eval"
BASE_REF="origin/master"
TASK_PACK_PATH=".scratch/agent-context/task-pack.md"
MEMORY_LOCAL_DIR="${AGENT_RAILS_HOME}/memory/open-eval"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"

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
