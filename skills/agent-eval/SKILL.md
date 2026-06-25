---
name: agent-eval
description: Create local evaluation datasets and record Agent Rails run logs as JSONL for comparing baseline agents against Agent Rails-guided runs. Use when the user asks to evaluate effectiveness, build eval sets, record logs, compare modes, or generate reports.
---

# Agent Eval

Use this skill to turn real engineering tasks into repeatable evaluation data.

## Commands

Initialize a local eval directory:

```bash
agent-rails eval init --dir evals
```

Record one run:

```bash
agent-rails eval record --task evals/tasks/sample-code-review.yaml --mode agentrails
```

Generate a report:

```bash
agent-rails eval report --runs evals/runs --output evals/report.md
```

## Dataset Shape

- `tasks/*.yaml`: task definition, repo path, refs, prompt, expected findings, rubric.
- `rubrics/*.yaml`: scoring dimensions and manual score template.
- `runs/**/*.jsonl`: append-only event logs.
- `runs/**/artifacts/`: captured command output.

## Rules

- Use real recurring user tasks, not toy prompts, whenever possible.
- Always compare at least two modes: `baseline` and `agentrails`.
- Keep task inputs stable: repo, refs, prompt, and rubric.
- Store secrets outside eval files and logs.
- Treat JSONL logs as evidence; do not edit old run logs by hand.

## Delivery

Report:

1. Eval directory
2. Task IDs recorded
3. Modes compared
4. Log paths
5. Report path
6. Scoring gaps that still need human judgment
