---
id: open-eval-english-only-commits
title: All git commit messages must be in English
triggers:
  - git commit
  - commit message
applies_to:
  - .git/
staleness: stable
source:
  - AGENTS.md
---

## Rule

Git commit messages must always be in English — title, body, and bullet points. Never use Chinese in commit messages.

## Why It Matters

Team consistency and readability across the monorepo. Technical terms (file names, function names, parameter names) stay as-is.

## Verify

```bash
git log --oneline -5
```

All messages should be readable without a Chinese IME.
