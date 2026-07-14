# Agent Rails Skill Blueprints

These folders are installable skill blueprints. They are kept inside the standalone Agent Rails kit so the engineering practice is portable across projects without committing files into those projects.

## Extracted Principles

From the `skills` repo:

- Split skills by invocation style. Automatic skills need rich trigger descriptions; manual-only skills should be explicit and rare.
- Keep `SKILL.md` short. Move details to scripts, templates, and references.
- Express dependencies as prose workflow steps, not deep cross-skill file references.
- Prefer small composable skills over one large meta skill.

From `pi`:

- Preserve the discipline: search -> read -> verify -> deliver.
- Escalate when repeated attempts fail.
- Require evidence for review and diagnosis.
- End with verification and next actions.

What not to copy:

- Do not make PI always-on for every task. It is too broad and can dilute project-specific rules.
- Do not install broad hooks that persist prompt/tool logs by default.
- Do not edit target project `CLAUDE.md` unless that project explicitly asks for it; `AGENTS.md` should remain the source of truth where the target project follows that convention.

## Skill Set

| Skill | Invocation | Purpose |
|-------|------------|---------|
| `agent-run-loop` | model or user | Start the guided pack -> estimate -> check -> memory-curator loop |
| `agent-context-pack` | model or user | Generate/read a deep or lite Task Pack before engineering work |
| `agent-grill` | model or user | Stress-test uncertain plans before implementation |
| `agent-check` | model or user | Select verification commands from changed paths |
| `agent-doctor` | model or user | Diagnose Agent Rails project/profile/adapter wiring |
| `agent-profile-init` | model or user | Generate a local project profile without writing into the target repo |
| `agent-claude-adapter` | model or user | Install Claude Code adapter files that nudge Claude to run Agent Rails first |
| `agent-memory-curator` | model or user | Decide whether a completed task should skip/create/update/merge memory |
| `agent-memory-suggest` | model or user | Record curator decisions and optionally write local memory |
| `agent-subagent-result` | model or user | Return a structured subagent summary to the main agent |
| `agent-diagnose` | model or user | Diagnose bugs with feedback-loop discipline |
| `agent-review` | model or user | Fresh diff review with evidence-first findings |
| `agent-refactor` | model or user | Safely restructure code while preserving behavior |
| `agent-tdd` | model or user | Drive feature/fix work with red-green-refactor feedback |
| `agent-skill-author` | user preferred | Create/update project skills from these blueprints |

## Installation

For a local Claude Code / Codex setup, copy selected skill folders into the tool's skill directory, or create a thin local wrapper that points back to these scripts and docs. Keep this directory as the canonical reviewed source.
