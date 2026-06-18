---
id: open-eval-backend-pandora-boot
title: OpenEval backend runs on Pandora Boot
triggers:
  - backend
  - pandora
  - spring boot
  - checkpreload
  - mvnw
applies_to:
  - backend/
  - Makefile
staleness: stable
source:
  - backend/AGENTS.md
  - Makefile
---

## Rule

OpenEval backend is Pandora Boot, not community Spring Boot. Local backend startup uses `pandora-boot:run`; do not switch commands to `spring-boot:run`.

## Useful Commands

```bash
make backend-run
curl http://127.0.0.1:7001/checkpreload.htm
```

## Verify

For backend behavior changes, prefer this order:

1. Compile or component test.
2. Start backend with `pandora-boot:run` through the project Makefile or backend Maven command.
3. Probe `/checkpreload.htm`.

## Caution

`/checkpreload.htm` is the deployment health check path. Do not remove it or break its unauthenticated readiness behavior.
