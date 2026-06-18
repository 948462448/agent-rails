---
id: open-eval-contracts-first
title: OpenEval contracts-first workflow
triggers:
  - contracts
  - backend api
  - frontend type
  - codegen
  - Result<T>
applies_to:
  - contracts/
  - backend/
  - frontend/
staleness: stable
source:
  - AGENTS.md
  - backend/AGENTS.md
---

## Rule

Cross-project API shape changes must start from `contracts/`. Update OpenAPI / JSON Schema first, run codegen, then implement backend / frontend changes in the same MR.

## Why It Matters

OpenEval spans `backend/`, `frontend/`, `runtime/`, and `contracts/`. If implementation moves before the contract, generated TypeScript types and backend behavior drift silently.

## Verify

```bash
make codegen-check
```

## Delivery Note

If `contracts/**` changed, the final answer should explicitly say whether generated artifacts were refreshed and whether `make codegen-check` passed.
