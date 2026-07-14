# Agent Rails Context

Agent Rails is a personal, repository-independent kit. It reads a target project through `--project`; it does not make the target project part of this repository.

## Core vocabulary

- **Target Project**: The repository or worktree Agent Rails inspects and equips. Its root and profile must be resolved for every invocation.
- **Target Project Context**: The canonical Target Project root, Git presence, resolved Profile, effective project name, worktree slug, and derived local Task Pack path for one invocation.
- **Local Adapter**: Tool-specific files that connect Claude Code or OpenCode to Agent Rails inside a Target Project. Local Adapters are personal artifacts and must not become team defaults accidentally.
- **Managed Artifact**: A file or skill directory owned by Agent Rails and therefore safe for Agent Rails to refresh or remove.
- **Generated File**: A Managed Artifact identified by the current generated marker or a supported legacy signature.
- **Generated Adapter Content**: The rendered guide and pack/lite/check command text written as Managed Artifacts for one Local Adapter.
- **Managed Skill Inventory**: The adapter-local list of skill directory names Agent Rails installed. It is the source of truth for safe refresh and uninstall behavior.
- **Managed Adapter Workspace**: The shared Module that applies ownership, tracked-path protection, managed-skill, and local-ignore rules while installing, refreshing, or removing a Local Adapter.
- **Profile**: Configuration resolved for one Target Project or worktree. A Profile must not leak into sibling repositories.
- **Task Pack**: A generated, task-scoped context artifact consumed by an agent run.
- **Pack Mode**: The evidence-density policy for a Task Pack. Lite, Normal, and Deep bound repeated excerpts while preserving capability sections; Audit keeps the Profile's configured maxima.
- **Model Preset**: A canonical model identity with aliases, context limits, throughput limits, and Pack Mode token budgets shared by all consuming commands.
- **Verification Plan**: The de-duplicated commands selected from changed paths. A full check report owns Git scope; integrations may consume only the Verification Plan.
- **Git Scope**: The resolved target commit, optional base commit, merge base, and committed/worktree path snapshot used by Task Pack, Agent Check, and publish checks.
- **Sensitive Output Guard**: The shared Module that detects supported secret-bearing assignments, headers, and private-key blocks, then applies the evidence policy required by each Adapter.
- **User Journey Facade**: A small public command that composes existing Interfaces around one user goal without taking ownership of their domain rules. Setup, Run, and Verify form the default journey.
- **Test Suite**: A domain-grouped set of regression tests loaded by the test runner. Suites share assertions and temporary-workspace setup but own their test cases and execution labels.

## Architectural boundaries

- Adapter entrypoints own tool-specific CLI behavior, paths, configuration merges, and reminder or instruction blocks.
- The shared Managed Adapter Workspace Module owns Generated File recognition, Managed Skill Inventory mechanics, tracked-path protection, generated-file writes, managed-skill installation/removal, and local-ignore block lifecycle. Its Interface preserves tool-specific entries and output policy without duplicating workspace mutation rules in each Adapter.
- The shared Adapter Content module owns Generated Adapter Content rendering and tool-specific frontmatter; Adapter entrypoints only select the tool and write the rendered artifacts.
- The Task Pack generator owns Pack Mode density caps and must retain the goal, Git state, prioritized changes, entry docs, memory, contract, grill, verification, delegation, and delivery seams in every mode; truncation must preserve complete lines and valid UTF-8. Output is staged beside its destination and atomically replaced only after rendering succeeds.
- Changed File Excerpts are diff-first for tracked paths and prefix-based only for untracked text, so the evidence seam favors changed behavior over file headers.
- Smart sorting may use a bounded set of meaningful goal tokens against paths and actual changes; project-name and workflow-generic tokens do not earn priority.
- The Sensitive Output Guard owns one detection Implementation for Task Pack and publish Adapters. Its redaction Interface stays conservative and fails closed; its scan Interface suppresses recognizable code expressions and can map added diff lines back to source paths and line numbers. Publish scanning composes committed, staged, and unstaged diffs with full untracked-file scans, retaining literal secret and private-key evidence without promoting unchanged tracked content.
- The Agent Check module exposes a full-report Interface and a narrow Verification Plan Interface; Task Pack and publish integrations consume the narrow Interface instead of parsing or duplicating scope.
- The shared Git Scope Module owns default-base policy, commit-ref validation, merge-base resolution, and committed/worktree path snapshots. Task Pack, Agent Check, and publish checks are Adapters at this Seam.
- The shared Model Preset Module owns model alias normalization, known-model status, numeric limits, and Pack Mode budgets. Task Pack, Estimate, and Doctor are Adapters at this Seam and must not duplicate model tables.
- The shared Target Project Context Module owns explicit-project canonicalization, Git-root discovery, Profile resolution/loading status, Profile-aware naming, worktree slug policy, and the derived Task Pack path. Command entrypoints retain their own user-facing failure and output policy.
- Setup, Run, and Verify are User Journey Facades. Setup delegates adapter mutation and diagnosis to existing installers and Doctors; Verify delegates change selection and release scope to Agent Check and publish check. Facades must not duplicate Adapter Workspace, Git Scope, Sensitive Output Guard, or Verification Plan rules.
- The SessionStart hook carries only stable routing and safety guardrails; task-specific evidence and the full execution contract belong in the on-demand Task Pack.
- The test runner owns suite selection and global test setup; each Test Suite owns one coherent workflow area.
- Shared modules must preserve the public CLI, existing adapter paths, and on-disk compatibility unless a migration is explicitly designed.
