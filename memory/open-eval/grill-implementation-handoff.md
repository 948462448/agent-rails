---
id: open-eval-grill-implementation-handoff
title: Grilling must produce a session-portable implementation handbook before coding
triggers:
  - grill
  - implementation plan
  - handoff
  - new session
  - ADR
applies_to:
  - .claude/grill/
staleness: stable
source:
  - CLAUDE.local.md
---

## Rule

Before closing any grilling session that produced actionable decisions, complete these three steps **before writing code**:

1. Write `<task-id>-implementation-plan.md` in `.claude/grill/` — must be self-contained without conversation context. Include: goal, architecture decisions (link to ADR), orchestration steps with code skeletons, field action matrix, file-level landing list, test strategy, scope boundaries, commit split plan.
2. Update the in-flight plans memory card with a row pointing to the new handbook.
3. Update ADR / CONTEXT files if new architecture decisions were made.

## Why It Matters

Without this handoff, a new session has no knowledge of prior grilling decisions. The user is forced to re-grill or manually paste decisions back — wasting time and risking inconsistent outcomes. This was observed and corrected on 2026-05-15.

## When Not To Apply

Skip the handbook if grilling was purely exploratory (e.g. "how should we think about X") with no actionable implementation task. In that case only update CONTEXT files.

## Verify

After writing the handbook, pretend to be a fresh session: read only MEMORY.md → in-flight reference → handbook. If you cannot start implementation immediately, the handbook needs more context.
