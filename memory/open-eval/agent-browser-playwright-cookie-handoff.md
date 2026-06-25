---
id: open-eval-agent-browser-playwright-cookie-handoff
title: agent-browser and Playwright do not share cookies automatically
triggers:
  - agent-browser
  - playwright
  - cookie
  - login
  - BUC
  - ensure-buc-login
  - webapp-testing
applies_to:
  - .claude/skills/ensure-buc-login/
  - uda-mcp-server/skills/webapp-testing/
staleness: stable
source:
  - .claude/skills/ensure-buc-login/SKILL.md
  - uda-mcp-server/skills/webapp-testing/SKILL.md
---

## Rule

`ensure-buc-login` skill logs in via `agent-browser`, saving cookies to `~/.qoder/main-auth-state.json`. The `webapp-testing` skill uses Playwright which starts a fresh browser context with **no cookies** by default.

## Why It Matters

When a task requires authenticated access (e.g., internal BUC sites), the LLM may see Playwright fail with a login redirect and assume cookies are broken. In reality, cookies are fine in agent-browser — they just weren't transferred to Playwright.

## Solution

agent-browser's state file format (`cookies` + `origins`) is **identical** to Playwright's `storageState` format. Load it directly:

```python
import os
STATE_FILE = os.path.expanduser('~/.qoder/main-auth-state.json')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(
        storage_state=STATE_FILE if os.path.exists(STATE_FILE) else None
    )
    page = context.new_page()
```

## Verify

```bash
# Check state file exists
ls -la ~/.qoder/main-auth-state.json

# Verify format compatibility
python3 -c "
import json
with open('$HOME/.qoder/main-auth-state.json') as f:
    d = json.load(f)
    print('cookies:', len(d.get('cookies', [])))
    print('origins:', len(d.get('origins', [])))
"
```

Should show non-zero cookie count if user has logged in via agent-browser.

## Caution

- State file is overwritten (not appended) on each `state save`
- If user hasn't logged in via agent-browser yet, the file won't exist
- For clean-session testing, explicitly skip loading the state file
