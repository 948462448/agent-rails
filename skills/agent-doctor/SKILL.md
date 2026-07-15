---
name: agent-doctor
description: Diagnose whether Agent Rails is correctly wired into a target project. Use when setup may be stale, Claude is not using Agent Rails, profile/model/online memory Adapter config may be wrong, or the user asks whether a project is ready.
---

# Agent Doctor

Use this skill to inspect setup before debugging Agent Rails behavior.

## Command

```bash
agent-rails doctor --project /path/to/project --profile /path/to/profile
```

If `agent-rails` is not on PATH, run:

```bash
agent-rails doctor --project /path/to/project --profile /path/to/profile
```

To test the provider-neutral online memory read path, run explicitly:

```bash
agent-rails doctor --project /path/to/project --profile /path/to/profile --online-memory-smoke
```

## Checks

- Agent Rails home and CLI.
- Project git status availability.
- Profile sourceability.
- Model preset and pack mode.
- Required commands.
- Online memory Adapter readiness when provider is `hybrid` or `online`.
- Optional online memory read smoke with `--online-memory-smoke`.
- Claude adapter guide, slash commands, and `CLAUDE.md` block.
- Project skills installed under `.claude/skills`.
- Git visibility for local/project Claude adapter mode.

## Rules

- Doctor does not write files.
- Online memory smoke is opt-in and read-only. The external Adapter owns its credentials and provider protocol; Doctor checks only the generic command interface.
- Treat `[FAIL]` as a setup blocker.
- Treat `[WARN]` as a follow-up unless it explains the current symptom.
- Use the Suggested Commands section as the next action list.
