# Changelog

## Unreleased

- Added `agent-rails opencode install`, `agent-rails opencode doctor`, and `agent-rails opencode uninstall` for a project-local opencode adapter backed by `.opencode/` instructions, commands, and skills.
- Scoped SessionStart profiles to their source repository, with explicit worktree and sibling-repository guidance.
- Added sensitive-output rules to SessionStart and Task Packs, and restricted new Task Packs to owner-only file permissions.
- Added an unresolved deployment-baseline warning when `publish check` would otherwise compare a target to an identical implicit upstream.

## 0.5.1 - 2026-06-26

- Fixed default Agent Rails config paths leaking from parent processes into child commands, which could make tests that override `HOME` still write under the parent's `AGENT_RAILS_CONFIG_HOME`.
- Added regression coverage for parent `agent_rails_init_paths` calls followed by child `HOME=... agent-rails pack` invocations.

## 0.5.0 - 2026-06-26

- Removed OpenEval-specific docs, profiles, local memory cards, shell variables, and test examples from the generic Agent Rails kit.
- Added compatibility for previously installed adapters that still reference deleted kit-local profiles such as `profiles/open-eval.profile`; those now fall back to `profiles/default.profile`.
- Centralized explicit profile resolution across `pack`, `run`, `check`, `doctor`, `update`, `claude install`, and `codex install`, while keeping missing non-kit profiles as hard failures.
- Updated the SessionStart hook to resolve stale profile paths before printing startup commands.
- Added regression coverage for legacy profile fallback, stale SessionStart profile references, and missing non-kit profile rejection.
- Added development milestone documentation in `docs/development-milestones.md`.

## 0.4.0 - 2026-06-25

- Added `agent-rails codex install`, `agent-rails codex doctor`, and `agent-rails codex uninstall` for the repo-local Codex plugin workflow.
- Deprecated the user-facing `agent-rails claude upgrade` command in favor of `agent-rails doctor --fix` and `agent-rails update`.
- Updated `doctor --fix` and `update` to call the Claude adapter installer directly instead of routing through the deprecated alias.

## 0.3.0 - 2026-06-25

- Added `agent-rails update` and `agent-rails upgrade self` to pull the kit, run tests, run doctor, refresh a target adapter, and run final doctor.
- Added `agent-rails doctor --fix` to refresh the target project's local Claude adapter and bundled skills.
- Added this changelog so version upgrades have a readable release history.

## 0.2.0 - 2026-06-25

- Added `VERSION` as the single kit version source.
- Added `agent-rails --version` and `agent-rails version`.
- Synced Claude/Codex plugin manifests with the kit version.
- Wrote Agent Rails version metadata into installed Claude adapters.
- Added doctor checks for kit, plugin manifest, and adapter version drift.
- Added `agent-rails publish check` for commit/push scope summaries, secret scan, and verification suggestions.

## 0.1.0 - 2026-06-24

- Added the initial personal Agent Rails kit with task packs, verification suggestions, local memory cards, Claude adapter install, and Codex/Claude SessionStart hook support.
