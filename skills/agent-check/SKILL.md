---
name: agent-check
description: Select and optionally run verification commands based on changed project paths. Use after code/doc/script changes, before final delivery, before review, or when the user asks to verify/check/test/lint the current work.
---

# Agent Check

Use this skill to prevent "changed but not verified" handoffs.

## Workflow

1. Print suggested checks:

```bash
agent-rails check --print-only
```

2. Decide whether to run checks:
   - Run lightweight checks immediately.
   - For heavy component CI, run when the touched files justify it or the user asks for full validation.

3. To run selected commands:

```bash
agent-rails check --run
```

If `agent-rails` is not on PATH, run `/Users/songlei/workspace/agent-rails/bin/agent-rails`.

## Verification Levels

- Quick: shell syntax, markdown sanity, py_compile.
- Targeted: nearest unit/integration test or component lint.
- Full: component CI, codegen check, e2e, deployment smoke.

## Delivery

Always report:

- Commands run.
- Commands not run and why.
- Results.
- Next action suggestions: fix / do not fix / later.
