---
name: agent-context-pack
description: Generate and use an Agent Rails Task Pack for a project. Use when starting substantial engineering work, switching branches/projects, preparing a fresh session, using context/memory, or when the user asks to "整理上下文", "生成 task pack", "接着做", or "开始落地".
---

# Agent Context Pack

Use this skill to narrow context before work begins.

## Workflow

1. Identify the project root and selected profile.
2. Run the context compiler:

```bash
agent-rails pack "<goal>"
```

Use `--profile <path>` when the project does not use the default profile. If `agent-rails` is not on PATH, run `/Users/songlei/workspace/agent-rails/bin/agent-rails`.

3. Read `.scratch/agent-context/task-pack.md`.
4. Follow the Task Pack:
   - Load only listed entry docs and files.
   - Treat context gaps as findings, not silent assumptions.
   - Check the Memory Provider section. If OpenMemory is skipped or failed, continue with local cards instead of blocking.
   - Use selected Memory Cards as hypotheses when `staleness=verify-first`.
5. Continue implementation or planning from the Task Pack.

## Rules

- Do not load every repo doc just because it exists.
- Do not put secrets into Task Pack output.
- If the generated pack is too broad, narrow the goal and regenerate it.
- If an entry doc is missing, use the fallback file named by the Task Pack and add a follow-up to fill the gap.
- Do not silently write online memory during task execution. Produce a memory candidate and ask for confirmation before updating OpenMemory.

## Delivery

When handing off, mention the Task Pack path and any context gaps.
