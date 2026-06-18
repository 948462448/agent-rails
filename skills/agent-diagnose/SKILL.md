---
name: agent-diagnose
description: Diagnose hard bugs and regressions with a search-read-verify-deliver loop. Use when the user reports something broken, failing, slow, flaky, throwing, says "debug/diagnose/排查", or when two attempts have failed.
---

# Agent Diagnose

Use this skill for bugs, regressions, and repeated failures.

## Core Discipline

Search -> read -> verify -> deliver.

Do not guess from symptoms. Build a feedback loop first.

## Workflow

1. Generate or refresh the Task Pack if the work is non-trivial.
2. Reproduce the symptom with the tightest available loop:
   - test
   - curl
   - CLI
   - browser script
   - replayed payload
   - minimal harness
3. Minimize the repro.
4. Form 3-5 falsifiable hypotheses.
5. Test one hypothesis at a time.
6. Fix the smallest correct surface.
7. Re-run the original loop and the nearest regression test.
8. Search for the same bug pattern in nearby files/modules.

## Evidence Rules

- Every root cause needs file/line or command evidence.
- Temporary debug logs must have a unique tag and be removed.
- If no tight feedback loop is possible, say what was tried and what artifact/access is needed.

## Delivery

Use this shape:

1. Root cause
2. Fix
3. Verification
4. Same-pattern search
5. Remaining risks
6. Next action suggestions
