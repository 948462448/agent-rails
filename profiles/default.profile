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

: "${AGENT_RAILS_TRIGGER_RULES:=Use pack --pack-mode deep before work that touches 2+ subprojects, changes APIs/contracts/schemas/data models, creates or updates ADRs/handbooks, performs migrations/refactors, or has ambiguous product decisions.
Use pack --pack-mode lite for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook where full grill is too heavy.
Use check --print-only before deploy/release/upload flows that consume the current branch, and after code/doc/script changes even when no Task Pack was generated.
Skip pack for read-only status queries, simple command output, or fixed operational skills with no repo change and no branch-consumption risk.
If a read-only or ops task turns into editing, deployment, or cross-component reasoning, generate a lite pack before continuing.}"

: "${AGENT_RAILS_WORKFLOW_RULES:=Search -> read -> verify -> deliver; do not guess when repo evidence is available.
Use the smallest useful context: entry docs, changed files, selected memory cards, then nearest code.
Build a fast feedback loop before debugging or implementation.
Prefer vertical slices and tracer bullets over broad horizontal changes.
Use project domain language from CONTEXT.md and respect ADRs when present.}"

: "${AGENT_RAILS_TARGET_SCOPE_RULES:=The session-injected profile is scoped to the session project root that supplied it.
When the user names another worktree in the same repository, resolve and pass that exact worktree root before pack/check; the repository profile may still be reused.
When work moves to a sibling or different git repository, do not reuse the current --profile; omit --profile so the target can auto-resolve, or use the adapter/profile owned by that target repository.
After any target change, regenerate the Task Pack and verify its Current Git State before broad reads or edits.}"

: "${AGENT_RAILS_SENSITIVE_OUTPUT_RULES:=Base64 and URL encoding are transport encodings, not redaction.
When inspecting logs, DOM, job tables, or command output, project only the fields needed for the decision; do not broadly dump entrypoints, command lines, environments, request bodies, or auth-bearing contexts.
If a tool exposes sensitive values, do not repeat them; narrow subsequent reads, report the affected surface, and recommend rotation when live credentials may be compromised.}"

: "${AGENT_RAILS_ROLE_RULES:=Agent Rails should actively shape the work, not only summarize it.
Use changed-file priority and excerpts to pick the first files to inspect, then verify by reading the real source.
Use memory cards only when they match the task or changed paths; do not force generic cards into the task.
For refactors, identify behavior invariants, dead-code hypotheses, and verification loops before editing.
When Agent Rails was useful or useless, say so in delivery so the workflow can be improved.}"

: "${AGENT_RAILS_GRILL_RULES:=Before architecture, refactor, migration, API contract, data model, or ambiguous product work, run a short grill first.
Ask one decision question at a time and provide your recommended answer with evidence.
If the answer can be found in the codebase, docs, ADRs, tests, or Task Pack, inspect those sources instead of asking the user.
Stop grilling when the goal, constraints, non-goals, success criteria, and verification loop are clear enough to act.
Default to at most ${AGENT_RAILS_GRILL_MAX_QUESTIONS} grill questions; move remaining non-blocking choices into deferred decisions in the implementation handoff.
In lite mode, skip full grill. Ask only blockers needed to proceed, otherwise record assumptions and deferred decisions.
For small mechanical edits with clear requirements, skip grill and state that the task is straightforward.}"

: "${AGENT_RAILS_MEMORY_SYNC_RULES:=Memory is the cross-session long-term truth; Task Pack is the current-session slice.
If Task Pack evidence disagrees with memory, treat memory as stale until verified and call that out in delivery.
Task Pack generation must not write memory. At final delivery, run memory curator or record an explicit skip reason.
When durable progress, decisions, or release facts changed, update or create one small local memory card rather than relying on the Task Pack to carry history.}"

: "${AGENT_RAILS_QUALITY_GATES:=Code changes need a runnable verification command or a clear reason it was not run.
Reviews need file/line evidence for each finding.
Bug fixes need original symptom verification plus a same-pattern search.
Deploy/release skills should start with Agent Rails check print-only as Step 0 when they consume the current branch.
Delivery must state what changed, what was verified, what was not verified, residual risks, and next actions.}"

: "${AGENT_RAILS_FAILURE_RULES:=After two failed attempts, change strategy instead of retrying the same path.
After three failed attempts, summarize facts proven, causes ruled out, and the next falsifiable hypothesis.
Ask the user only for information that cannot be discovered from local files, commands, or accessible services.}"

: "${AGENT_RAILS_SUBAGENT_RESULT_CONTRACT:=Goal handled
Scope and files inspected
Findings or output
Changes made
Evidence collected with file paths, line references, or command output summaries
Commands run
Verification not run and why
Memory candidates, if any, as draft-only suggestions
Open questions or blockers
Recommended next action}"

DOMAIN_DOC_ROOT="CONTEXT.md"
DOMAIN_DOC_MAP="CONTEXT-MAP.md"
ADR_DIR="docs/adr"
AGENT_DOC_DIR="docs/agents"
ISSUE_TRACKER_DOC="docs/agents/issue-tracker.md"
TRIAGE_LABELS_DOC="docs/agents/triage-labels.md"
