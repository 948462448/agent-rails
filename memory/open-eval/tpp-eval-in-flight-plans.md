---
id: open-eval-tpp-eval-in-flight-plans
title: TPP Eval in-flight plans and implementation handbook index
triggers:
  - tpp-eval
  - grill
  - implementation plan
  - ADR
  - T13c
  - trajectory
  - ODPS data source
applies_to:
  - .claude/grill/
  - backend/
  - runtime/
  - dolphin/
staleness: verify-first
source:
  - .claude/grill/tpp-eval-progress.md
  - AGENTS.md
---

## Rule

Before starting any TPP eval subtask (T1–T15), check the in-flight plans card at `~/.claude/projects/-Users-songlei-workspace-open-eval/memory/tpp-eval-in-flight.md` for an existing implementation handbook. If one exists, read it and execute — do not re-grill. Only start a new grill if the handbook is missing or stale.

## Why It Matters

The TPP eval feature spans 15+ subtasks across backend, runtime, dolphin, and frontend. Each subtask has been through a grilling session that produced an `implementation-plan.md` in `.claude/grill/`. Re-grilling wastes time and risks inconsistent decisions.

Key completed subtasks with handbooks: T5b, T5c, T10, T11, T13, T13d, T14, T13c-1 through T13c-4, T13c-2-rework, Stage 2 vipserver rewrite, Trajectory Eval POC.

Key pending subtasks with handbooks ready to execute: T13c-6 (EvalFlow framework alignment), T13c-7 (Ray migration), T13c-8 (user-defined prompt + multi-dimension), ODPS data source, eval result details page.

## Key ADRs

- ADR-0001: Sub-table meta JSON + Jackson polymorphism for cross-business differences
- ADR-0006: Dual ODPS source/sink + vipServer domain from backend extra (adapter calls no OPS API)
- ADR-0007: Runtime aggregation + POST done reporting; permissive status
- ADR-0011: ODPS credentials from openlm, not stored locally
- ADR-0013: Ray Job submit + attach + poll pattern

## Verify

```bash
ls .claude/grill/*-implementation-plan.md
cat .claude/grill/tpp-eval-progress.md
```

## Delivery Note

When working on any TPP eval subtask, state which T-number it is and whether an existing handbook was followed or a new grill was needed.
