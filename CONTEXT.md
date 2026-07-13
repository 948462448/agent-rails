# Agent Rails Context

Agent Rails is a personal, repository-independent kit. It reads a target project through `--project`; it does not make the target project part of this repository.

## Core vocabulary

- **Target Project**: The repository or worktree Agent Rails inspects and equips. Its root and profile must be resolved for every invocation.
- **Local Adapter**: Tool-specific files that connect Claude Code or OpenCode to Agent Rails inside a Target Project. Local Adapters are personal artifacts and must not become team defaults accidentally.
- **Managed Artifact**: A file or skill directory owned by Agent Rails and therefore safe for Agent Rails to refresh or remove.
- **Generated File**: A Managed Artifact identified by the current generated marker or a supported legacy signature.
- **Generated Adapter Content**: The rendered guide and pack/lite/check command text written as Managed Artifacts for one Local Adapter.
- **Managed Skill Inventory**: The adapter-local list of skill directory names Agent Rails installed. It is the source of truth for safe refresh and uninstall behavior.
- **Profile**: Configuration resolved for one Target Project or worktree. A Profile must not leak into sibling repositories.
- **Task Pack**: A generated, task-scoped context artifact consumed by an agent run.
- **Pack Mode**: The evidence-density policy for a Task Pack. Lite, Normal, and Deep bound repeated excerpts while preserving capability sections; Audit keeps the Profile's configured maxima.
- **Verification Plan**: The de-duplicated commands selected from changed paths. A full check report owns Git scope; integrations may consume only the Verification Plan.
- **Sensitive Output Guard**: The shared Module that detects and redacts supported secret-bearing assignments, headers, and private-key blocks before an Adapter renders them.
- **Test Suite**: A domain-grouped set of regression tests loaded by the test runner. Suites share assertions and temporary-workspace setup but own their test cases and execution labels.

## Architectural boundaries

- Adapter entrypoints own tool-specific CLI behavior, paths, ignore blocks, and tracked-file policy.
- The shared Adapter Lifecycle module owns Generated File recognition and Managed Skill Inventory mechanics.
- The shared Adapter Content module owns Generated Adapter Content rendering and tool-specific frontmatter; Adapter entrypoints only select the tool and write the rendered artifacts.
- The Task Pack generator owns Pack Mode density caps and must retain the goal, Git state, prioritized changes, entry docs, memory, contract, grill, verification, delegation, and delivery seams in every mode; truncation must preserve complete lines and valid UTF-8.
- Changed File Excerpts are diff-first for tracked paths and prefix-based only for untracked text, so the evidence seam favors changed behavior over file headers.
- Smart sorting may use a bounded set of meaningful goal tokens against paths and actual changes; project-name and workflow-generic tokens do not earn priority.
- The Sensitive Output Guard owns one detection Implementation for Task Pack and publish Adapters and fails closed when excerpt redaction cannot complete.
- The Agent Check module exposes a full-report Interface and a narrow Verification Plan Interface; Task Pack and publish integrations consume the narrow Interface instead of parsing or duplicating scope.
- The SessionStart hook carries only stable routing and safety guardrails; task-specific evidence and the full execution contract belong in the on-demand Task Pack.
- The test runner owns suite selection and global test setup; each Test Suite owns one coherent workflow area.
- Shared modules must preserve the public CLI, existing adapter paths, and on-disk compatibility unless a migration is explicitly designed.
