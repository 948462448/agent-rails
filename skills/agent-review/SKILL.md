---
name: agent-review
description: Review code or design changes with fresh-context, evidence-first findings. Use when the user asks for review/CR/code review, wants a skeptical pass, asks whether changes are safe, or before merging high-risk agent-produced work.
---

# Agent Review

Use this skill for review, not implementation.

## Workflow

1. Build review context:
   - Generate/read Task Pack when available.
   - Read changed files and nearest entry docs.
   - Read verification output if present.
2. Review behavior and contracts first:
   - API/contract drift
   - cross-component parameter shape
   - transaction/lifecycle boundaries
   - auth/tenant boundaries
   - missing tests or false confidence
3. Check same-pattern risk in nearby files.
4. Produce findings first.

## Finding Rules

- Lead with bugs, risks, regressions, and missing tests.
- Include file/line evidence.
- Do not report style nits unless they create real risk.
- If no issue is found, say so and state residual test gaps.

## Output Shape

1. Findings, severity ordered.
2. Open questions or assumptions.
3. Verification gaps.
4. Change summary only as secondary context.
5. Next action suggestions: fix / do not fix / later.
