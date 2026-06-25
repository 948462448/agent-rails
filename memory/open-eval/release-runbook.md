---
id: open-eval-release-runbook
title: OpenEval release runbook location and key invariants
triggers:
  - release
  - deploy
  - publish
  - globalParam
  - TOKEN_SECRET_MAP
  - Holo
  - rollback
applies_to:
  - docs/release/
  - backend/
  - runtime/
  - deploy/
staleness: verify-first
source:
  - docs/release/checklist.md
  - AGENTS.md
---

## Rule

The release runbook lives at `docs/release/checklist.md` and is maintained in-place for each release. Daily deploys use skills: `/deploy-open-eval` (backend), `/deploy-open-eval-runtime` (runtime).

Before any release, verify these four invariants:

1. **backend ↔ runtime contracts are not backward-compatible**: omega ETL time parameters changed from ds/hh to start_time/end_time(ms), output table name format changed from `_{scene}_{yyyyMMdd}` to `_{startTimeMs}`. Rolling back one side alone will break the other — both must roll back together.

2. **Switch Center `TOKEN_SECRET_MAP` is a Map type — server value fully overrides code defaults**: must **append** the `open_eval_runtime` token entry in the UI, not replace the entire map — otherwise existing `agent_eval` / `iflow` tokens break. Keep the actual token outside this repo.

3. **Production DS space (project-code `152416397487968`) is newly created and empty**: must fill 12 globalParams (8 ODPS + 4 callback) before deploying backend, otherwise runtime rawScript `check_required_param` fails immediately.

4. **Holo reuses model-evaluate team's Hologres instance `model_eval` database**: `backend/.../application.properties:84` hardcodes the URL. Prerequisites: (a) Holo team has created external catalog `lightning_compute` in `model_eval`, (b) Switch Center `HOLO_USER` / `HOLO_PASSWORD` are filled (same ODPS AK/SK).

## Why It Matters

These are "seemingly harmless but will bite on release day" issues discovered through grilling. Each one has caused or nearly caused a release failure.

## Verify

```bash
cat docs/release/checklist.md
```

Check section 〇 "上线日时序总览" for the current release sequence.

## Delivery Note

After any release, update the checklist with lessons learned. If a new invariant is discovered, add it to both the checklist and this card.
