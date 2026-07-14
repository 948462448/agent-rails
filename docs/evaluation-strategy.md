# Agent Rails Evaluation Strategy

Status: design proposal

## Decision Summary

Agent Rails must first prove that it is better than running the same coding
agent without Agent Rails. Only after that causal comparison is established do
we compare the Agent Rails levels.

The primary evaluation is therefore a paired experiment with a true `off`
control and explicit Agent Rails treatments:

1. `off`
2. `session-only`
3. `lite`
4. `normal`
5. `deep`
6. `audit`
7. `auto`

Every fixed-mode task is run against the same repository snapshot, task,
model, agent harness, tool policy, and acceptance checks. `auto` is evaluated
only after the fixed modes establish what should be selected for each task
class.

The result must not be reduced to a single score. The product decision is a
quality/safety/token Pareto comparison:

- Does Agent Rails improve task success or prevent material mistakes?
- At equal quality, does it reduce total tokens?
- At equal token budget, does it improve quality?
- Does a higher Pack Mode provide enough marginal value to justify its added
  context?

## Why Evaluation Lives Outside Agent Rails

Evaluation is evidence about Agent Rails, not an Agent Rails runtime
capability. The product CLI should remain focused on context generation,
injection, adapters, and deterministic checks. A standalone harness owns the
experimental treatments, captured TUI artifacts, judge integration, and
paired reports.

The former built-in run logger was removed because a baseline label did not
create a true baseline: it still invoked Agent Rails surfaces, treated command
completion as task success, and measured Task Pack estimates instead of full
provider usage. Keeping that behavior in the product CLI made the boundary
look stronger than the evidence.

Fixing the true baseline remains the first evaluation milestone. A larger task
set is premature until `off` can bypass every Agent Rails surface.

## TUI Black-Box Execution

The harness does not need to automate the development TUI. Run the same task
manually in two isolated worktrees and fresh TUI sessions, then capture the
patch, final response, verification output, and optional provider usage. A
standalone Python tool can anonymize those artifacts and invoke any trusted LLM
judge command over stdin.

The default blind comparison uses two mirrored rounds: the second round swaps
Response A and Response B. A winner is position-consistent only when both
rounds map back to the same treatment. See
[the Chinese TUI A/B runbook](./tui-ab-eval.zh-CN.md) for the runnable flow.

## Evaluation Questions

### Q1: Does Agent Rails Help?

Compare each Agent Rails treatment with `off` on the same tasks.

Primary outcomes:

- task success;
- critical scope, worktree, verification, and release mistakes;
- total provider-reported tokens.

This is the product existence test. If no treatment improves safety or quality
and none matches baseline quality with fewer tokens, more sophisticated routing
is not justified.

### Q2: Which Level Helps Which Task?

Compare the fixed Agent Rails levels with each other. Higher context density is
not assumed to be better. In particular, `deep` and `audit` may help complex
tasks while hurting focused tasks through extra context and token cost.

The evaluation should identify the cheapest non-inferior level for each task
class, not one globally highest level.

### Q3: Can Auto Routing Recover The Best Trade-Off?

After fixed-mode results are available, freeze a routing policy and compare
`auto` with:

- `off`;
- the best single fixed mode;
- a post-hoc oracle that selects the cheapest successful fixed mode per task.

The oracle is an analysis ceiling, not a runnable treatment. `auto` must never
use outcome data from the run it is routing.

## Treatment Contract

| Treatment | Agent Rails surfaces allowed | Purpose |
| --- | --- | --- |
| `off` | None: no SessionStart injection, Local Adapter, Profile-derived context, Task Pack, Agent Rails skill, memory provider, or Agent Check guidance | True control |
| `session-only` | Stable SessionStart/Local Adapter routing and safety guidance; Task Pack generation is disabled | Measure the recurring lightweight guardrail by itself |
| `lite` | Stable startup guidance plus a forced Lite Task Pack | Measure the smallest task-scoped context |
| `normal` | Stable startup guidance plus a forced Normal Task Pack | Measure the current default density |
| `deep` | Stable startup guidance plus a forced Deep Task Pack | Measure denser evidence and full planning/grill behavior |
| `audit` | Stable startup guidance plus a forced Audit Task Pack using Profile maxima | Establish a high-context ceiling, not a default recommendation |
| `auto` | Stable startup guidance plus `agent-rails run` without an explicit Pack Mode | Evaluate the frozen routing policy |

A Profile is configuration, not a standalone treatment. It affects the model
only through a rendered adapter, Task Pack, memory card, or other injected
artifact. Profile values must be frozen across the Agent Rails arms.

The initial experiment keeps optional memory providers disabled. Memory-on vs
memory-off is a later factorial experiment; otherwise memory quality is
confounded with the core Task Pack effect.

## Two Evaluation Tracks

### Track A: Agent Outcome And Token Efficiency

Track A runs the coding agent and evaluates the resulting patch, review, or
diagnosis. It covers:

- focused changes;
- defect repair;
- code review and diagnosis;
- cross-module changes and ambiguous product work.

Agent Check and publish checks are not added after only the Agent Rails arms in
this track because that would make the treatment boundaries unequal. Any
post-run verification available to the model must be declared as part of the
treatment before the run.

### Track B: Workflow Guardrails

Track B evaluates deterministic Agent Rails guardrails separately from model
problem solving. Fixtures deliberately contain mistakes such as:

- wrong worktree or sibling-repository Profile reuse;
- out-of-scope changed files;
- invalid or unresolved base refs;
- a source-control baseline incorrectly treated as a deployed baseline;
- supported secret-bearing additions;
- missing or unsafe verification steps.

Track B reports detection rate, false-positive rate, false-negative severity,
and token/interaction overhead. This prevents strong deterministic checks from
being hidden inside a model-quality average.

## Experimental Unit

One experimental unit is:

```text
task fixture + repository SHA + model + agent harness version
+ treatment + repetition
```

Each task fixture must be replayable from a clean worktree and contain enough
ground truth to judge success without reading another treatment's artifacts.

Suggested task schema:

```yaml
id: focused-bugfix-001
category: bugfix
difficulty: medium
repo: https://example.invalid/repository.git
setup_ref: 0123456789abcdef
task: "Fix the described behavior without changing unrelated files."

acceptance:
  commands:
    - "bash tests/run.sh focused-bugfix-001"
  required_exit: 0

scope:
  allowed_paths:
    - "src/**"
    - "tests/**"
  forbidden_paths:
    - "deployment/**"

expected:
  required_behaviors:
    - "the original failure is fixed"
  forbidden_behaviors:
    - "unrelated API changes"

diagnostics:
  gold_files: []
  gold_symbols: []
```

`diagnostics` is optional. Gold context helps explain why a treatment worked,
but task success remains the primary outcome.

## Fairness And Isolation Rules

Every paired comparison must enforce the following:

1. Start from the same immutable repository SHA in a fresh worktree or
   container.
2. Use the same model version, agent harness, system prompt outside the
   treatment, tool permissions, network policy, time limit, and maximum token
   budget.
3. Remove Agent Rails plugins, hooks, environment variables, generated files,
   skills, and user memory from the `off` environment. Merely asking the model
   to ignore them is not a valid control.
4. Do not share conversation state, working-tree changes, run summaries, or
   memory writes between treatments.
5. Randomize treatment order within each task and repetition block.
6. Run at least two repetitions during smoke evaluation and three during the
   expanded evaluation. Repetitions from one task are not counted as
   independent tasks.
7. Classify environment/setup failures before scoring. Exclude or rerun the
   same experimental unit under every treatment, never only the losing arm.
8. Record the exact injected SessionStart text and Task Pack artifact so the
   treatment can be audited later.

Dependency caches may be shared only when they are read-only and treatment
independent. Agent history, repository summaries, and semantic indexes are
part of the treatment and must not leak across arms.

## Metrics

### Primary Quality And Safety

- `task_success`: all mandatory acceptance checks pass and no critical
  forbidden behavior occurs.
- `acceptance_pass_rate`: fraction of executable acceptance commands that pass.
- `scope_violation`: changed or reported artifacts outside declared scope.
- `critical_mistake`: wrong repository/worktree, destructive action, secret
  exposure, invalid release conclusion, or another task-specific blocker.
- `regression_count`: previously passing required checks that fail after the
  run.

For review or diagnosis tasks without an executable patch, use a frozen rubric
with required findings, prohibited false claims, evidence quality, and action
scope. Human adjudication should be blind to treatment where practical.

### Token And Cost

Capture provider-reported usage when available:

- startup/system input tokens;
- Task Pack input tokens;
- other prompt and tool-result input tokens;
- cached input tokens;
- reasoning tokens;
- output tokens;
- total billed tokens and cost.

Keep Task Pack estimates as a diagnostic field, not the total-token result. The
report must expose Agent Rails overhead explicitly rather than hiding it inside
the run total.

### Operational Diagnostics

- elapsed time;
- model turns and tool calls;
- files and symbols inspected;
- verification commands executed;
- context precision/recall when a gold context exists;
- selected Pack Mode and auto-routing reason.

Context precision/recall explains behavior but does not replace task success.

## Paired Analysis

For treatment `t` and the true control:

```text
quality_uplift(t) = success(t) - success(off)
token_delta(t)    = total_tokens(t) - total_tokens(off)
token_saving(t)   = 1 - total_tokens(t) / total_tokens(off)
```

Report the following rather than only an aggregate mean:

- per-task win / tie / loss against `off`;
- success rate and critical-mistake rate;
- median and distribution of token deltas;
- results split by task category and difficulty;
- marginal change from `session-only` through the fixed Pack Modes;
- the quality/token Pareto frontier.

For the expanded dataset, calculate confidence intervals by resampling tasks,
not individual repeated runs. Keep model and harness versions separate instead
of pooling them into one headline number.

## Initial Decision Gates

Smoke results are directional and do not establish statistical significance.
After the expanded run, an Agent Rails treatment is worth advancing when all of
the following hold:

1. It introduces no new class of critical mistake.
2. On its intended task cohort, it either:
   - matches baseline quality while reducing median total input tokens by at
     least 15%; or
   - improves task success by at least 10 percentage points while increasing
     median total tokens by no more than 20%.
3. Any higher Pack Mode shows positive marginal value on at least one declared
   task cohort; otherwise it should be collapsed, rerouted, or removed from the
   default path.

These are initial product gates, not permanent benchmark claims. Revise them
before the expanded run if real provider cost or task variance shows that the
thresholds are poorly calibrated.

The `auto` policy advances when it remains close to the best fixed-mode quality
for each task class while consuming materially fewer tokens than always using
the highest successful mode.

## Rollout Plan

### Phase 0: Prove The Harness

- Two replayable tasks.
- Treatments: `off`, `lite`, and `deep`.
- One run per treatment: six runs total.
- Gate: prove that `off` contains no Agent Rails artifact, the underlying agent
  actually executes, acceptance is scored, and provider token usage is stored.

### Phase 1: Directional Smoke Eval

- Eight tasks across focused, bugfix, review/diagnosis, and cross-module work.
- Treatments: `off`, `lite`, `normal`, and `deep`.
- Two repetitions: 64 runs total.
- Gate: identify whether Agent Rails has any quality/safety/token signal and
  whether Lite and Deep behave differently by task class.

### Phase 2: Full Level And Routing Eval

- Twenty to thirty tasks with at least three repetitions.
- Add `session-only`, `audit`, and frozen `auto` treatments.
- Add paired confidence intervals and category-level analysis.
- Gate: choose the default routing policy and identify levels that do not earn
  their token overhead.

### Phase 3: External Validity

- Use a small, hand-audited SWE-bench Verified subset for end-to-end repair
  sanity checks.
- Use a small ContextBench verified subset only for context-retrieval
  diagnostics.
- Do not use either external benchmark as the sole Agent Rails product gate;
  neither replaces the true `off` versus Agent Rails experiment.

## Implementation Milestones

### M1: True Baseline And Run Manifest

- Define explicit treatment contracts in a standalone run manifest.
- Capture completed black-box TUI runs without routing the `off` arm through
  Agent Rails.
- Record repository SHA, dirty-state policy, model/harness versions, treatment,
  Pack Mode, repetition, injected artifacts, and environment fingerprint.
- Make baseline contamination a hard harness failure.

### M2: Capture And Score Tasks

- Capture the final patch, final response, verification output, and optional
  usage from each TUI run.
- Restore a clean task environment for every experimental unit.
- Execute acceptance and scope checks after the agent exits.
- Distinguish setup failure, agent failure, and scored task failure.

### M3: Capture Real Usage

- Parse provider or harness usage records.
- Preserve the Task Pack estimate as a separate field.
- Store token breakdown, cost, elapsed time, turns, and tool calls in the run
  manifest.

### M4: Paired Report

- Pair treatments by task, model, harness version, and repetition block.
- Report quality uplift, token delta, safety failures, and Pareto frontiers.
- Refuse to compare incompatible repository SHAs or model/harness versions.

### M5: Task Set And External Adapters

- Land the first eight generic replayable tasks.
- Add external benchmark adapters only after the local causal harness passes
  Phase 1.
- Freeze and evaluate auto routing only after fixed-level evidence exists.

## Non-Goals

- Declaring one Pack Mode universally best.
- Optimizing only Task Pack size while ignoring full-session tokens.
- Treating TUI or capture-command success as task success.
- Using an LLM judge as the only correctness oracle when executable acceptance
  is possible.
- Training or tuning on the held-out evaluation tasks.
- Treating ContextBench, stars, or leaderboard position as proof that Agent
  Rails itself works.

## First Implementation Slice

The first implementation slice should be deliberately small:

1. run the same task in isolated `off` and forced-`lite` TUI sessions;
2. capture both patches, final responses, and verification outputs with the
   standalone Python tool;
3. execute one deterministic acceptance command;
4. store actual usage when the TUI or provider exposes it;
5. run a mirrored blind judge and reveal one paired result with token delta.

Until that slice works end to end, adding more benchmark tasks increases
volume without answering the primary causal question.
