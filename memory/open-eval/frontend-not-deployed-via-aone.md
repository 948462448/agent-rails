---
id: open-eval-frontend-not-deployed-via-aone
title: Frontend is not deployed via Aone pipeline
triggers:
  - frontend
  - deploy
  - pipeline
  - aone
  - 部署
applies_to:
  - frontend/
  - deploy-open-eval skill
staleness: stable
source:
  - user feedback 2026-06-23
---

## Rule

OpenEval's **frontend does NOT use Aone pipeline** for deployment. Only backend code should trigger `a1 app pipeline run`.

## Why It Matters

The user explicitly corrected this on 2026-06-23 after the AI mistakenly triggered 3 pipeline runs for frontend-only changes. Frontend has its own independent deployment path (mechanism TBD).

## Correct Workflow

| Change type | Action |
|-------------|--------|
| Backend only | commit + push → Aone pipeline deploy (66 pre / 67 prod) |
| Frontend only | commit + push, **do NOT trigger pipeline** |
| Both | Backend goes through pipeline; frontend deploys independently |

## Verify

```bash
# Check what changed
git diff --name-only HEAD~1

# If only frontend/ files changed, do NOT run:
# a1 app pipeline run --pipeline-id 66  ❌
```

## Caution

Never trigger `a1 app pipeline run` for frontend-only changes. If unsure about frontend deployment mechanism, ask the user first.
