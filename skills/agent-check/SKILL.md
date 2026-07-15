---
name: agent-check
description: Select and optionally run verification commands based on changed project paths. Use after code/doc/script changes, before final delivery, before review, before deploy/release/upload workflows that consume the current branch, or when the user asks to verify/check/test/lint the current work.
---

# Agent Check

Use this skill to prevent "changed but not verified" handoffs.

## Workflow

1. Print suggested checks:

```bash
agent-rails check --print-only
```

Tell the user the printed marker before continuing: `AGENT RAILS: CHECK-ONLY (reason=verification)`.

2. Decide whether to run checks:
   - Run lightweight checks immediately.
   - For heavy component CI, run when the touched files justify it or the user asks for full validation.
   - For deploy/release/upload skills, treat print-only check as Step 0 before push or submission.

3. To run selected commands:

```bash
agent-rails check --run
```

Commands run in a child shell (`bash -lc` by default). Set `AGENT_RAILS_RUN_SHELL=sh` or another shell when a project requires it.

Project-mode adapters expect `agent-rails` on PATH. Local adapters may use the absolute CLI path generated for that machine.

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
