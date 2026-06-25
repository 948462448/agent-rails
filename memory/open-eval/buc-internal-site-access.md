---
id: open-eval-buc-internal-site-access
title: BUC internal site access requires headed agent-browser
triggers:
  - BUC
  - internal site
  - agent-browser
  - ensure-buc-login
  - alibaba-inc.com
  - atatech.org
applies_to:
  - .claude/skills/ensure-buc-login/
staleness: stable
source:
  - .claude/skills/ensure-buc-login/
---

## Rule

When accessing Alibaba internal sites (production BUC at `login.alibaba-inc.com` or test BUC at `login-test.alibaba-inc.com`), use `agent-browser` with the `--headed` flag. Headless mode cannot borrow system Chrome's BUC login state.

## Why It Matters

`agent-browser --headed` reuses system Chrome's real-time cookies for BUC authentication. Headless mode only has cookies from `state load`, which are non-cumulative (each `state save` overwrites the previous snapshot). Attempting to default to headless and switch to headed only for login has been tried and reverted — headless restarts immediately lose BUC auth.

## Prerequisites

- User must have logged into both BUC systems in system Chrome beforehand
- System Chrome must remain running (it is the cookie source)
- `agent-browser`-launched windows can be closed; system Chrome cannot

## Verify

```bash
agent-browser --session main --headed open https://ata.atatech.org
agent-browser --session main --headed get text body
```

Should return page content, not a BUC login redirect.

## Caution

Never ask the user for their BUC password in chat. If system Chrome has no active session, the headed browser will prompt the user to enter credentials directly.
