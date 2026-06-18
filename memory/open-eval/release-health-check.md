---
id: open-eval-release-health-check
title: End verification with actionable next steps
triggers:
  - deploy
  - release
  - e2e
  - verify
  - smoke
  - checkpreload
applies_to:
  - docs/
  - deploy/
  - backend/
  - runtime/
staleness: stable
source:
  - AGENTS.md
  - docs/release/checklist.md
---

## Rule

Verification output must end with next action suggestions. Do not stop at a raw report.

## Delivery Shape

Use three buckets:

- Fix: blocker or high-confidence issue to address now.
- Do not fix: confirmed non-issue or out of scope.
- Later: low-risk follow-up or separate cleanup.

## Verify

For service readiness checks, use the environment-specific health endpoint when available:

```bash
curl http://127.0.0.1:7001/checkpreload.htm
```
