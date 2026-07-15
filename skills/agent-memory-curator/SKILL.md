---
name: agent-memory-curator
description: Model-curated Agent Rails memory consolidation after task completion. Use at final handoff when a task may have produced reusable project knowledge, workflow rules, recurring failure modes, verification recipes, preferences, or memory-worthy corrections, and decide automatically whether to skip, create, update, or merge memory.
---

# Agent Memory Curator

Use this skill after delivery, before the final response is closed out.

## Curator Prompt

You are the memory curator for this local Agent Rails workspace. Your job is not to summarize the whole conversation. Your job is to decide whether any durable lesson should become future retrieval context.

Classify the outcome as exactly one:

- `skip`: no durable memory value, duplicate, too speculative, too broad, or unsafe.
- `create`: a new small card is warranted.
- `update`: an existing card should be replaced or tightened.
- `merge`: multiple lessons should be collapsed into one smaller card.

## Value Test

Store only if all are true:

- The lesson is likely to help a future coding/review/debug task.
- It is project-specific or user-workflow-specific, not generic advice.
- It is evidence-backed by files, commands, errors, docs, or a verified user preference.
- It has a clear trigger phrase or path scope.
- It is safe to retrieve later without exposing secrets, cookies, tokens, AccessKeys, internal raw responses, or private personal data.

Prefer `skip` for:

- One-off command output, temporary branch names, transient IDs, speculative guesses, or generic coding tips.
- Facts already in `README`, `AGENTS.md`, project docs, or an existing memory card.
- Large summaries of what changed. Memory cards should encode reusable rules, not transcripts.

## Workflow

1. Read the Task Pack Memory Cards and relevant changed-file evidence.
2. Inspect existing local cards in the profile's `MEMORY_LOCAL_DIR` when available.
3. Treat memory as the cross-session long-term truth and Task Pack as the current-session slice. If they disagree, mark the older memory as stale in your reasoning and prefer an update/merge decision over silently relying on the Task Pack.
4. Decide `skip`, `create`, `update`, or `merge`.
5. If skipping, record the reason:

```bash
agent-rails memory suggest --project /path/to/project --profile /path/to/profile --decision skip --reason "<why this should not become memory>"
```

6. If creating/updating/merging, write one local card with the smallest durable lesson:

```bash
agent-rails memory suggest \
  --project /path/to/project \
  --profile /path/to/profile \
  --decision keep \
  --write-local \
  --title "<short title>" \
  --trigger "<specific trigger>" \
  --applies-to "<smallest useful scope>" \
  --verify "<command or file check>" \
  --caution "<scope limits or staleness risk>" \
  "<1-3 sentence reusable lesson>"
```

Use `--decision update` or `--decision merge` when that better describes the action. Use `--id` and `--force` only when intentionally replacing an existing local card.

## Rules

- Do not ask the user to approve local memory writes; the model is responsible for the value judgment.
- This kit writes only curated local cards. The external online memory Adapter is read-only and owns its own credentials and provider protocol.
- Write at most one card per task unless the task produced clearly independent durable lessons.
- Keep `staleness: verify-first` unless the fact is a stable user preference or repo convention.
- Do not rely on a Task Pack to carry durable progress; update or merge the memory card when reusable facts changed.
- Mention in final delivery whether memory was skipped or written, with the path when written.
