---
name: agent-skill-author
description: Create or update Agent Rails project skills from portable blueprints. Use when the user asks to write skills, refine skill triggers, install local skills, extract patterns from skills/pi, or make agent workflows reusable across projects.
---

# Agent Skill Author

Use this skill to author small, portable skills.

## Sources To Preserve

From small engineering skills:

- Make `description` model-facing and trigger-rich.
- Keep `SKILL.md` concise.
- Put deterministic operations in scripts.
- Put long references one level deep.
- Split model-invoked and user-invoked skills deliberately.

From PI:

- Keep search -> read -> verify -> deliver.
- Escalate after repeated failure.
- Require evidence in review/debug output.
- End with next actions.

## Workflow

1. Define the task class and concrete trigger phrases.
2. Decide invocation:
   - model-invoked for safe, reusable autonomous behavior
   - user-invoked for broad meta modes or risky workflows
3. Draft `SKILL.md` under 100 lines when possible.
4. Move details to:
   - `references/` for long guidance
   - `scripts/` for deterministic commands
   - `templates/` for repeated output shapes
5. Validate frontmatter:
   - `name` is lowercase hyphen-case
   - `description` says what it does and when to use it
6. Test on one realistic task.

## Anti-Patterns

- One giant always-on meta skill.
- Repeating project docs inside every skill.
- Hardcoding one provider or one repository into generic skills.
- Installing hooks that persist sensitive prompt/tool output by default.
- Editing `CLAUDE.md` when the project rule says `AGENTS.md` is source of truth.
