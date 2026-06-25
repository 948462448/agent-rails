---
id: open-eval-pandora-boot-multi-ctor-autowired
title: Pandora Boot multi-constructor beans require explicit @Autowired
triggers:
  - Pandora Boot
  - @Autowired
  - constructor
  - NoSuchMethodException
  - BeanInstantiationException
  - @Component
applies_to:
  - backend/
staleness: stable
source:
  - backend/AGENTS.md
---

## Rule

When a `@Component` class has two or more non-default constructors (e.g. a public primary constructor plus a package-private test constructor), the primary constructor **must** have `@Autowired`. Pandora Boot's Spring container does not auto-select the public constructor in this scenario.

## Why It Matters

Community Spring 4.3+ auto-selects when there is a single public constructor, but Pandora Boot falls back to searching for a no-arg default constructor when multiple constructors exist. This causes `BeanInstantiationException: No default constructor found` at startup, which kills the entire ApplicationContext.

Observed on 2026-05-18 with `TppOpsClient`: 4-arg public `@Value` constructor + 3-arg package-private test constructor → pre-deploy kept restarting. Adding `@Autowired` to the primary constructor fixed it immediately.

## Verify

For any `@Component` with multiple constructors, check that the primary one has `@Autowired`:

```bash
grep -rn '@Autowired' backend/agent-eval-infrastructure/src/main/java/ | grep -i 'ctor\|constructor'
```

## Delivery Note

When adding a test constructor to an existing `@Component`, verify the primary constructor already has `@Autowired`. If not, add it.
