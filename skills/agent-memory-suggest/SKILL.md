---
name: agent-memory-suggest
description: Deterministic helper for recording Agent Rails memory curator decisions and optionally writing local memory cards. Use from agent-memory-curator after the model has decided skip/create/update/merge.
---

# Agent Memory Suggest

Use this skill only as the write/log helper after `agent-memory-curator` has made the memory value judgment.

## Commands

Record a skip decision:

```bash
agent-rails memory suggest --project /path/to/project --profile /path/to/profile --decision skip --reason "<why skipped>"
```

Write a curated local memory card:

```bash
agent-rails memory suggest \
  --project /path/to/project \
  --profile /path/to/profile \
  --decision keep \
  --write-local \
  --title "<short title>" \
  --trigger "<specific trigger>" \
  --applies-to "<small scope>" \
  --verify "<command or file check>" \
  --caution "<scope limits>" \
  "<1-3 sentence reusable lesson>"
```

Project-mode adapters expect `agent-rails` on PATH. Local adapters may use the absolute CLI path generated for that machine.

## Rules

- Do not decide memory value here; use `agent-memory-curator`.
- `--write-local` writes only to the local memory directory from the profile.
- This helper writes only local memory; it never writes through the online memory Adapter.
- Never write secrets or raw sensitive service responses.
- Prefer one small card over a broad summary.

## Delivery

Mention the decision log path and local memory path when one was written.
