# Changelog

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
