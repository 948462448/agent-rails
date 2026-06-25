---
id: open-eval-aone-cr-list-incomplete
title: a1 app cr list --all may miss CRs — always confirm with user before creating
triggers:
  - a1
  - cr
  - deploy
  - Aone
  - change request
applies_to:
  - .claude/skills/deploy-open-eval/
staleness: stable
source:
  - AGENTS.md
---

## Rule

Before creating a new CR (Change Request) in the deploy-open-eval workflow, **always ask the user** "is there already a CR for this branch?" — regardless of what `a1 app cr list --all` returns.

If jq/python parsing of CR list output fails or returns empty, treat it as **inconclusive**, not as "no CR exists."

## Why It Matters

`a1 app cr list --all` does NOT reliably return all CRs. This has caused duplicate CRs twice:

- First: created CR 34452480 when 34416956 already existed
- Second: created CR 34472774 when 34452480 already existed (jq parse error silently treated as "no results")

Duplicate CRs pollute pipeline history and are harder to clean up than a simple confirmation dialog.

## Verify

If the user provides a CR ID, use `a1 app cr get <id>` — this is more reliable than list + filter.

```bash
a1 app cr get <known-cr-id>
```

## Caution

Never assume "no CR exists" based solely on `a1 app cr list` output. Always confirm with the user.
