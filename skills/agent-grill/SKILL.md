---
name: agent-grill
description: Stress-test a plan before implementation. Use before architecture, refactor, migration, API contract, data model, ambiguous product work, or when the user asks to "grill", "拷问方案", "压一下方案", or validate a plan before coding.
---

# Agent Grill

Use this skill to clarify a plan before expensive work begins.

## Workflow

1. Generate or refresh the Task Pack when repo context matters.
2. Decide whether grill is needed:
   - Use it for architecture, refactor, migration, API contract, data model, rollout, or ambiguous product work.
   - Skip full grill in `--pack-mode lite`; ask only blocker questions and record deferred decisions.
   - Skip it for small mechanical edits with clear requirements, and say why.
3. Walk the decision tree one question at a time.
4. For each question:
   - Give your recommended answer first.
   - Cite the code, docs, ADR, test, Task Pack section, or memory card that supports it.
   - If the answer can be found locally, inspect the repo instead of asking the user.
5. Stop when goal, constraints, non-goals, success criteria, and verification loop are clear enough to act.
6. Stop after the Task Pack question budget, default 8 questions, unless the user explicitly asks to keep grilling.

## Question Shape

Prefer questions that resolve implementation direction:

- What are we optimizing for?
- What is explicitly out of scope?
- Which contract or behavior must stay stable?
- What is the smallest tracer-bullet slice?
- What evidence would prove this worked?
- What failure would make us change direction?

## Rules

- Ask one question at a time when user input is needed.
- Do not turn straightforward tasks into ceremony.
- Do not ask the user for facts that can be discovered from local files or commands.
- Keep a running list of locked decisions and open questions.
- Move non-blocking questions over budget into "deferred decisions" in the implementation handoff.
- Once decisions are locked, proceed with the normal Agent Rails loop.

## Delivery

Before implementation, summarize:

- Locked decisions
- Non-goals
- Evidence inspected
- Verification loop
- Deferred decisions
- Remaining risks or open questions
