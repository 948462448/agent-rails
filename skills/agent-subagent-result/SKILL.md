---
name: agent-subagent-result
description: Return a structured Agent Rails subagent summary at the end of delegated work. Use when acting as a subagent, returning results from a parallel investigation, or handing findings back to a main agent.
---

# Agent Subagent Result

Use this skill when finishing delegated work.

## Result Format

Return exactly these sections:

```markdown
## Subagent Result

### Goal handled

### Scope and files inspected

### Findings or output

### Changes made

### Evidence collected

### Commands run

### Verification not run

### Memory signals

### Open questions or blockers

### Recommended next action
```

## Rules

- Keep the result concise enough for the main agent to merge into final delivery.
- Include file paths, line references, command names, or artifact paths for evidence.
- If nothing was changed, write `None` under `Changes made`.
- If no commands were run, explain why.
- Memory signals are input for the main agent's `agent-memory-curator`; subagents should not write memory directly.
- Do not include secrets, cookies, tokens, AccessKeys, or full sensitive responses.

## Memory Signal Shape

When there may be reusable knowledge, include a short signal:

```markdown
- title:
- triggers:
- applies_to:
- staleness: verify-first
- rule:
- verify:
```
