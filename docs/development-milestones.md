# Development Milestones

Agent Rails is a personal local kit. The milestones below track the main capability steps without turning any business project into a default assumption.

## 0.1.0 - Bootstrap

- Created the core Task Pack generator, verification suggestions, local memory card flow, Claude adapter installer, and shared SessionStart hook shape.
- Established the personal-only boundary: target repositories are read through `--project`, while kit files stay outside business repos.

## 0.2.0 - Versioning And Publish Checks

- Added `VERSION` as the single source for kit versioning.
- Added `agent-rails --version`, plugin manifest version checks, adapter version metadata, and publish-time scope/secret checks.

## 0.3.0 - Update And Repair Loop

- Added `agent-rails update` / `upgrade self` to pull the kit, run tests, refresh adapters, and run doctor before and after upgrade.
- Added `doctor --fix` as the safe adapter refresh path.

## 0.4.0 - Codex And SessionStart Workflow

- Added repo-local Codex plugin installation, doctor, and uninstall commands.
- Kept Claude and Codex startup behavior on one shared SessionStart hook, with Codex emitting JSON additional context.

## 0.5.0 - Generic Kit And Legacy Profile Migration

- Removed OpenEval-specific documentation, profiles, memory cards, shell variables, and tests from the kit.
- Kept project-specific behavior in user-level or project-level profiles instead of kit defaults.
- Added fallback for old adapters that still reference deleted kit-local profiles such as `profiles/open-eval.profile`.
- Centralized profile resolution across command entrypoints and startup-hook output.

## Unreleased - Local Adapters And Release Safety

- Added a first-class, project-local OpenCode adapter with install, doctor, and uninstall lifecycle commands.
- Made generated Claude adapter files safely refreshable while preserving user-authored content outside managed blocks.
- Added repository/worktree profile-boundary guidance to SessionStart and Task Packs.
- Hardened Task Pack permissions to `0600` and documented sensitive-output handling.
- Bounded Task Pack evidence density by mode and compacted SessionStart/default contracts to reduce recurring token cost without removing capability sections.
- Switched tracked-file excerpts to actual diff hunks and deepened Agent Check with a narrow Verification Plan Interface for integrations.
- Added a shared Sensitive Output Guard, bounded changed-content scoring, and UTF-8-safe truncation so denser Task Packs remain safer and more relevant.
- Made publish checks require an explicit deployed-source baseline when the implicit deployment delta cannot be established.
- Recorded the design, tradeoffs, verification, and follow-ups in [Local Adapters And Release Safety](./local-adapters-and-release-safety.md).

## Near-Term Backlog

- Add a clearer repair command or doctor warning for stale adapter profile paths.
- Add a format-aware sensitive-content guard for generated Task Packs and diagnostic output.
- Add an optional publish receipt for artifact, configuration, and smoke-test evidence.
- Keep release docs focused on personal install, upgrade, verify, and rollback flows.
