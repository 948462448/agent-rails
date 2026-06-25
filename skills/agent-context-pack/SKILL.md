---
name: agent-context-pack
description: Generate and use an Agent Rails Task Pack for a project. Use when starting substantial engineering work, switching branches/projects, preparing a fresh session, using context/memory, doing POC/deploy-prep lite work, or when the user asks to "整理上下文", "生成 task pack", "接着做", or "开始落地".
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
Use `--budget <chars>` when the pack should be bounded by an approximate character budget.
Use `--model qwen3.7-max|glm5.1|deepseek-v4-pro --pack-mode lite|normal|deep|audit` to select a built-in model budget preset.
Use `--pack-mode lite` for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook where full grill is too heavy.
Use `agent-rails estimate --file <path>` when you need to check an existing file or generated pack size.
Use `agent-rails estimate --tokenizer command --tokenizer-command '<cmd using $AGENT_RAILS_TOKENIZER_INPUT>'` when an exact local Qwen/GLM tokenizer is available.
Use `AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT` and `AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS` in the profile when changed file excerpts need to be smaller or disabled.
Use `AGENT_RAILS_CHANGED_FILE_SORT=path` to disable smart changed-file ordering.

3. Tell the user the marker printed by the pack command: `AGENT RAILS: ON (mode=<mode>, pack=<path>)`.
4. Read the Task Pack path printed by the command. By default it is under `~/.agent-rails/agent-context/`.
5. Follow the Task Pack:
   - Start from the Session Marker section and keep it visible in the user-facing session.
   - Use the Trigger Matrix to decide whether this should have been lite, deep, check-only, or skipped.
   - Load only listed entry docs and files.
   - Treat context gaps as findings, not silent assumptions.
   - Check the Memory Provider section. If OpenMemory is skipped or failed, continue with local cards instead of blocking.
   - Use Changed File Priority to decide what to open first.
   - Read Changed File Excerpts first when they are present, then open the full files only when the task needs more context.
   - Use selected Memory Cards as hypotheses when `staleness=verify-first`; local card excerpts are embedded directly in the pack.
   - Respect the Context Budget section when it is bounded.
   - In lite mode, skip full grill and keep only blocker questions plus deferred decisions.
6. Continue implementation or planning from the Task Pack.

## Rules

- Do not load every repo doc just because it exists.
- Do not hide the session marker. If this task intentionally skips Agent Rails, say `AGENT RAILS: SKIPPED (reason=<reason>)`.
- Do not put secrets into Task Pack output.
- If the generated pack is too broad, narrow the goal and regenerate it.
- If an entry doc is missing, use the fallback file named by the Task Pack and add a follow-up to fill the gap.
- Do not write online memory during task execution. Use `agent-memory-curator` after delivery for local memory decisions.

## Delivery

When handing off, mention the Task Pack path and any context gaps.
