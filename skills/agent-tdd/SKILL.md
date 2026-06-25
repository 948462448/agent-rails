---
name: agent-tdd
description: Drive feature work and bug fixes through a red-green-refactor loop with behavior-focused tests. Use when the user asks for TDD/test-first/red-green-refactor, wants safer implementation, bug regression tests, or agent work that should be constrained by executable feedback.
---

# Agent TDD

Use this skill for feature work and bug fixes where an executable feedback loop is possible.

## Core Rules

- Write or identify one failing behavior test before implementation when feasible.
- Test observable behavior through public interfaces, not private implementation details.
- Use one vertical slice at a time: one test, minimal code, verify, then continue.
- Do not write all tests first and all implementation later.
- Do not refactor while RED. Get to GREEN first.
- After GREEN, use `agent-refactor` rules for structural cleanup.

## Workflow

1. Generate or refresh the Task Pack for the task.
2. Name the behavior being added or fixed.
3. Find the nearest executable loop:
   - existing unit/integration test
   - CLI command
   - curl/API smoke
   - compile/type check
   - minimal harness
4. RED:
   - For bug fixes, write a regression test that fails on the current bug.
   - For features, write the smallest useful behavior test.
   - If a real test is not practical, state the closest verification loop and why.
5. GREEN:
   - Implement only enough code to pass the current test.
   - Avoid speculative generality.
6. REFACTOR:
   - Only after tests pass.
   - Remove duplication, clarify names, deepen shallow modules, or move behavior toward the owning domain concept.
   - Run the test loop after each meaningful refactor step.
7. Repeat for the next behavior or edge case.
8. Run `agent-check` before final delivery.

## Good Tests

- Describe what the caller/user can observe.
- Use public APIs or stable integration boundaries.
- Survive internal refactors.
- Fail for the intended behavior change.
- Are narrow enough to diagnose quickly.

## Bad Tests

- Mock internal collaborators just to assert call order.
- Test private methods.
- Assert implementation shape instead of behavior.
- Break when code is refactored but behavior is unchanged.
- Require a large fake world before proving one behavior.

## Guardrails

- If no test framework exists, do not invent a broad framework during the task. Use the tightest existing loop and propose test setup as a follow-up.
- If the first test is hard to write, inspect whether the code lacks a useful interface; this may become a refactor or architecture finding.
- Keep fixture data minimal and domain-named.
- Do not delete failing tests to get GREEN.
- Do not hide flaky tests; report flakiness as risk.

## Delivery

Report:

1. Behavior covered
2. RED evidence
3. GREEN implementation
4. Refactor performed, if any
5. Verification run
6. Remaining untested behavior
7. Next action suggestions: fix / do not fix / later
