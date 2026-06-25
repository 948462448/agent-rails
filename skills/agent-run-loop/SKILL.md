---
name: agent-run-loop
description: Start an Agent Rails guided work loop that generates a Task Pack, estimates context size, prints verification commands, and prepares model-curated memory follow-up. Use when beginning a substantial task, POC/deploy-prep lite task, or when the user wants Agent Rails to orchestrate the session.
---

# Agent Run Loop

Use this skill as the default entrypoint for substantial work. Use lite mode for quick POCs and deploy prep that still need scope, memory, checklist, and verification guidance.

## Command

```bash
agent-rails run --project /path/to/project --profile /path/to/profile "<goal>"
```

For POCs or deploy prep:

```bash
agent-rails run --project /path/to/project --profile /path/to/profile --pack-mode lite "<goal>"
```

If `agent-rails` is not on PATH, run:

```bash
/Users/songlei/workspace/agent-rails/bin/agent-rails run --project /path/to/project --profile /path/to/profile "<goal>"
```

## Workflow

1. Run `agent-rails run`.
2. Tell the user the printed marker: `AGENT RAILS: ON (mode=<mode>, pack=<path>)`.
3. Read the generated Task Pack.
4. Apply the Grill Gate from the Task Pack before architecture, refactor, migration, API contract, data model, or ambiguous product work. In lite mode, skip full grill and keep only blocker questions plus deferred decisions.
5. Follow Session Marker, Context Budget, Changed File Priority, Memory Cards, Verification Suggestions, and Subagent Result Contract.
6. Before final delivery, run the check command printed by `agent-rails run`.
7. After final delivery, use `agent-memory-curator` to decide `skip/create/update/merge`.

## Rules

- This wrapper does not hard-control Claude/Codex internals.
- Do not skip reading the generated Task Pack.
- Do not hide the session marker. If Agent Rails is skipped, say `AGENT RAILS: SKIPPED (reason=<reason>)`.
- Do not force a full grill for lite POC/deploy-prep work.
- Do not write OpenMemory from this kit.
- Local memory writes are allowed only after `agent-memory-curator` decides the lesson is durable.
