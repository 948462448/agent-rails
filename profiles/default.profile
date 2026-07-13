# Generic Agent Rails profile.
# Project-specific profiles can override these values.

AGENT_RAILS_CONFIG_HOME="${AGENT_RAILS_CONFIG_HOME:-$HOME/.agent-rails}"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"

AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-generic}"
AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-normal}"
AGENT_RAILS_GRILL_MAX_QUESTIONS="${AGENT_RAILS_GRILL_MAX_QUESTIONS:-8}"
AGENT_RAILS_CONTEXT_BUDGET_TOKENS="${AGENT_RAILS_CONTEXT_BUDGET_TOKENS:-}"
AGENT_RAILS_CONTEXT_BUDGET_CHARS="${AGENT_RAILS_CONTEXT_BUDGET_CHARS:-0}"
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}"
AGENT_RAILS_TOKENIZER="${AGENT_RAILS_TOKENIZER:-auto}"
AGENT_RAILS_TOKENIZER_CMD="${AGENT_RAILS_TOKENIZER_CMD:-}"
AGENT_RAILS_TIKTOKEN_ENCODING="${AGENT_RAILS_TIKTOKEN_ENCODING:-cl100k_base}"
AGENT_RAILS_BUDGET_GIT_PERCENT="${AGENT_RAILS_BUDGET_GIT_PERCENT:-20}"
AGENT_RAILS_BUDGET_MEMORY_PERCENT="${AGENT_RAILS_BUDGET_MEMORY_PERCENT:-40}"
AGENT_RAILS_BUDGET_VERIFY_PERCENT="${AGENT_RAILS_BUDGET_VERIFY_PERCENT:-20}"
AGENT_RAILS_BUDGET_CONTRACT_PERCENT="${AGENT_RAILS_BUDGET_CONTRACT_PERCENT:-20}"
AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="${AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS:-1600}"
AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT="${AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT:-8}"
AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS="${AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS:-4000}"
AGENT_RAILS_CHANGED_FILE_SORT="${AGENT_RAILS_CHANGED_FILE_SORT:-smart}"

ENTRY_DOC_ROOT="AGENTS.md"

: "${AGENT_RAILS_TRIGGER_RULES:=Use deep pack for cross-subproject, contract/schema/model, ADR, migration/refactor, or ambiguous product work; use lite for POCs, deploy prep, codegen checks, and focused continuation.
Use check --print-only before deploy/release/upload flows that consume the branch, and after code/doc/script changes even without a pack.
Skip pack for read-only/fixed operations; if they turn into edits, deploy, or cross-component reasoning, generate lite before continuing.}"

: "${AGENT_RAILS_WORKFLOW_RULES:=Search -> read -> verify -> deliver; prefer repo evidence over guesses.
Read the smallest useful context: entry docs, prioritized changes, matching memory, then nearest source.
Build a fast feedback loop; prefer vertical slices/tracer bullets; use CONTEXT.md language and respect ADRs.}"

: "${AGENT_RAILS_TARGET_SCOPE_RULES:=A session profile belongs to its source repository. Pass the exact root for another same-repo worktree.
For a sibling/different repository, do not reuse the current --profile; let the target resolve or use its own profile.
After a target change, regenerate the pack and verify Current Git State before broad reads or edits.}"

: "${AGENT_RAILS_SENSITIVE_OUTPUT_RULES:=Base64 and URL encoding are transport encodings, not redaction.
From logs, DOM, tables, and command output, project only decision fields; avoid auth-bearing context and broad environment/request dumps.
Do not repeat exposed secrets; narrow reads, report the surface, and recommend rotation when live credentials may be compromised.}"

: "${AGENT_RAILS_ROLE_RULES:=Use priority/excerpts to choose first reads, then verify against real source; use only task-matching memory.
For refactors, define behavior invariants, dead-code hypotheses, and verification loops before editing.
In delivery, state whether Agent Rails helped so the workflow can improve.}"

: "${AGENT_RAILS_GRILL_RULES:=Before architecture/refactor/migration/contract/model or ambiguous product work, grill briefly; ask one evidence-backed decision at a time.
Inspect code/docs/ADRs/tests/pack instead of asking discoverable questions; stop when goal, constraints, non-goals, success, and verification are clear.
Ask at most ${AGENT_RAILS_GRILL_MAX_QUESTIONS}; defer non-blockers. Lite asks only blockers and records assumptions; clear mechanical edits skip grill.}"

: "${AGENT_RAILS_MEMORY_SYNC_RULES:=Memory is the cross-session long-term truth; Task Pack is the current-session slice.
When they conflict, verify and mark memory stale. Pack generation never writes memory.
At delivery, curate one small durable card when facts changed, or record why memory sync was skipped.}"

: "${AGENT_RAILS_QUALITY_GATES:=Code needs a runnable verification command or a reason; reviews need file/line evidence; bug fixes also need symptom verification and same-pattern search.
Branch-consuming deploy/release starts with check --print-only.
Delivery states changes, verified/unverified work, residual risks, and next actions.}"

: "${AGENT_RAILS_FAILURE_RULES:=After two failures, change strategy; after three, summarize proven facts, ruled-out causes, and the next falsifiable hypothesis.
Ask users only for information unavailable from local files, commands, or accessible services.}"

: "${AGENT_RAILS_SUBAGENT_RESULT_CONTRACT:=Goal and scope handled; files inspected
Findings/output and changes made
Evidence with paths/lines/output summaries; commands run
Verification omitted and why
Draft-only memory candidates
Open questions/blockers and recommended next action}"

DOMAIN_DOC_ROOT="CONTEXT.md"
DOMAIN_DOC_MAP="CONTEXT-MAP.md"
ADR_DIR="docs/adr"
AGENT_DOC_DIR="docs/agents"
ISSUE_TRACKER_DOC="docs/agents/issue-tracker.md"
TRIAGE_LABELS_DOC="docs/agents/triage-labels.md"
