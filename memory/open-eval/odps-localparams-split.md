---
id: open-eval-odps-localparams-split
title: DS Node localParams must be split by data source path (omega vs ODPS)
triggers:
  - DolphinScheduler
  - localParams
  - SOURCE_ODPS
  - NODE_LOCAL_PARAM_NAMES
  - omega
  - workflow builder
applies_to:
  - backend/agent-eval-infrastructure/src/main/java/com/alibaba/aios/eval/infrastructure/util/EvalTaskWorkflowDefineBuilder.java
staleness: stable
source:
  - backend/AGENTS.md
  - docs/tpp-eval-phase1/adr-summary.md
---

## Rule

When adding Node localParam variables to `EvalTaskWorkflowDefineBuilder`, determine whether each variable is omega-only, ODPS-only, or both. Use the split lists:

- `OMEGA_NODE_LOCAL_PARAM_NAMES` (12 items): excludes SOURCE_* variables, allowing fallback to DS project globals (ots_etl input + lightning_compute output)
- `ODPS_NODE_LOCAL_PARAM_NAMES` (18 items = 12 + 6 SOURCE_*)

DS 3.x parameter priority: **Node localParam (even empty string) > project globalParam**. If CliUtils does not set a parameter but the Node localParam injects `value=""`, the empty string overrides the DS global value.

## Why It Matters

ADR-0011 (commit b42ba28) added `SOURCE_ODPS_ENDPOINT/PROJECT/AK/SK` + `SOURCE_TABLE/PARTITION` to a shared `NODE_LOCAL_PARAM_NAMES` list. This caused all newly created omega-path tasks to fail with `ValueError: Required parameter 'SOURCE_ODPS_ENDPOINT' is missing or empty` because the empty localParam shadowed the DS global value.

Fixed in commit b8a58e0 by splitting the list. Verified: omega taskId=84 / instId=240 SUCCEEDED in 9m50s with evalReport successCount=5.

## Verify

```bash
cd backend && ./mvnw -q test -pl agent-eval-infrastructure -Dtest=EvalTaskWorkflowDefineBuilderTest
```

The test includes an omega-path assertion that SOURCE_* must NOT appear in localParams.

## Delivery Note

When modifying `NODE_LOCAL_PARAM_NAMES` or adding new DS parameters, explicitly state which path(s) they apply to.
