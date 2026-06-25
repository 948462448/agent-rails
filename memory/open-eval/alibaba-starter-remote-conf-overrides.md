---
id: open-eval-alibaba-starter-remote-conf-overrides
title: Alibaba internal starters override local config with server-pushed conf
triggers:
  - BUC
  - application.properties
  - spring.buc
  - exclusions
  - pull config
  - SSOFilter
  - Diamond
applies_to:
  - backend/agent-eval-start/src/main/resources/
  - backend/
staleness: stable
source:
  - backend/AGENTS.md
---

## Rule

When diagnosing "local config looks correct but runtime behavior is wrong" for Alibaba internal starters (BUC, Diamond, config-client, dpath, etc.), the first action is **not** to read `application.properties` or jar bytecode. Instead:

1. Start the backend locally
2. Grep startup logs for `pull xxx config success` / `Init xxx success` / `getConf.json`
3. Inspect the actual JSON response from the server
4. Compare the server-pushed values against expected values — the difference is the answer

## Why It Matters

Alibaba internal starters (at least `buc-spring-boot-starter` 1.9.3-jakarta) actively HTTP-fetch server conf at startup and **override** local `application.properties` and Filter init-params. A property marked `@Deprecated` in `spring-configuration-metadata.json` with the note "new versions can only configure via server" should be taken literally — even if bytecode shows the property is still being read, the server value wins.

Observed on 2026-05-20: `spring.buc.exclusions=/openapi/*` was set locally but BUC server conf returned `"exclusions":"/checkpreload.htm,/status.taobao"` — the local config was silently ignored. Two rounds of incorrect diagnosis (looking at properties file, then at bytecode) before checking startup logs.

## Verify

```bash
# After starting backend, grep for BUC conf pull
grep -i "pull.*config.*success\|getConf.json" backend/logs/*.log
```

## Caution

Jar bytecode confirming an init-param is injected does NOT mean it takes effect at runtime. Alibaba starters frequently override in `Filter.init()` or `@PostConstruct`.
