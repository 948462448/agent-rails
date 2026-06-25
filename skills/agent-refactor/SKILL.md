---
name: agent-refactor
description: Refactor code safely while preserving behavior, improving structure, testability, and AI-navigability. Use when the user asks for refactor/重构, reduce coupling, remove duplication, improve architecture, clean technical debt, or make code easier to test without changing product behavior.
---

# Agent Refactor

Use this skill for implementation refactors, not feature changes.

## Core Rules

- Preserve externally visible behavior unless the user explicitly asks for behavior change.
- Do not mix refactor and feature work in the same change.
- Build or identify a safety net before moving logic.
- Refactor in small reversible steps.
- Keep public interfaces stable unless the refactor goal is specifically to change the interface.
- Prefer fewer, deeper modules over many shallow pass-through modules.

## Workflow

1. Generate or refresh the Task Pack for the refactor goal.
2. Identify the refactor target:
   - duplication
   - long method or tangled control flow
   - shallow pass-through module
   - feature envy
   - primitive obsession
   - hidden coupling
   - hard-to-test behavior
3. State the behavior that must remain unchanged.
4. Find the safety net:
   - existing tests
   - characterization test
   - type/build check
   - focused CLI/curl/manual smoke
5. Make the smallest useful structural change.
6. Run the nearest verification after each meaningful step.
7. Search nearby callers/modules for same-pattern fallout.
8. Stop before broad architecture changes unless the user confirmed that scope.

## Refactor Choices

- Extract only when the extracted unit has a clearer interface than the code it replaces.
- Inline shallow helpers when they force readers to jump without hiding real complexity.
- Move behavior toward the data or domain concept that owns the invariant.
- Introduce an interface only when there are at least two real implementations, a hard testing boundary, or a clear external seam.
- Prefer behavior-level tests that survive internal restructuring.

## Guardrails

- Do not rename or move public API paths casually.
- Do not rewrite unrelated style while refactoring.
- Do not remove tests just because they are awkward; replace implementation-coupled tests with behavior tests.
- If no safety net exists, say so and propose one before a risky refactor.
- If the refactor reveals a behavior bug, pause and report it as a separate fix candidate.

## Delivery

Report:

1. Refactor goal
2. Behavior preserved
3. Structural changes
4. Verification run
5. Same-pattern search
6. Remaining risks
7. Next action suggestions: fix / do not fix / later
