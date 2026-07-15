# Changelog

## Unreleased

- Added hard token-budget Task Pack assembly with required-section floors, weighted category allocation, unused-share redistribution, exact external/Hugging Face tokenizer support, and a cached long-lived counting service.
- Replaced OpenCode's static instruction-only integration with a project-local per-request plugin that reads the current session, derives available input space from `model.limit`, and injects a proportional Agent Rails Pack without trimming OpenCode history.
- Removed the built-in `agent-rails eval` logger and `agent-eval` skill so the product CLI stays focused on runtime capabilities; standalone Python tools now own TUI artifact capture, mirrored blind judging, and Codex/OpenCode trajectory conversion to Run IR, OTel, and ATIF.
- Added a durable Shell-to-Python refactor handoff with research sources, implementation status, compatibility contracts, migration gates, and the recommended first tracer bullet.

## 0.6.0 - 2026-07-15

- Added bilingual how-it-works documentation with diagrams for the system architecture, task lifecycle, Target Project and Profile isolation, Adapter ownership, shared Git Scope, and GitHub Release updates.
- Added versioned GitHub Release archives, SHA-256 assets, and a standalone installer that does not require a source checkout.
- Made release archive inspection portable across BSD and GNU tar by avoiding an early-closing `grep -q` pipeline under `pipefail`.
- Made `agent-rails upgrade self` project-neutral and able to update Release installations through an atomic `current` symlink switch while preserving Git-checkout updates.
- Added a tag-driven GitHub Actions workflow that validates `v<VERSION>`, runs the full test suite, builds release assets, and publishes them through GitHub CLI.
- Added `agent-rails setup` and `agent-rails verify` as compatible user-journey facades over the existing adapter, Doctor, Agent Check, and publish-check commands.
- Reduced the bilingual README path to setup, run, and verify, moving customization and troubleshooting details into bilingual CLI reference documents.
- Made plain `agent-rails init` project-neutral so first-time shell setup no longer pins one repository or prints the advanced workflow; explicit `--project` and `--profile` remain compatible.
- Stopped Agent Check from adding deleted shell entrypoints to generated `bash -n` verification commands.
- Added `agent-rails opencode install`, `agent-rails opencode doctor`, and `agent-rails opencode uninstall` for a project-local opencode adapter backed by `.opencode/` instructions, commands, and skills.
- Scoped SessionStart profiles to their source repository, with explicit worktree and sibling-repository guidance.
- Added sensitive-output rules to SessionStart and Task Packs, and restricted new Task Packs to owner-only file permissions.
- Added an unresolved deployment-baseline warning when `publish check` would otherwise compare a target to an identical implicit upstream.
- Rejected invalid base refs consistently in `pack`, `check`, and `publish check` instead of silently falling back to an empty diff.
- Added generated-file ownership markers and exact Claude/OpenCode managed-skill inventories so refresh and uninstall preserve user-authored files and unrelated `agent-*` skills.
- Deepened generated-file ownership and managed-skill inventory into a Managed Adapter Workspace Module that also centralizes tracked-path protection, managed writes, skill lifecycle, and local-ignore mechanics for Claude and OpenCode.
- Extracted Claude/OpenCode guide and command rendering into a shared Adapter Content module with tool-specific guide implementations and shared command bodies.
- Split the monolithic shell regression runner into selectable core, adapters, workflows, and context Test Suites while preserving the default test order and output.
- Stopped the automatic `update` flow from forcing Claude adapter overwrites; explicit `doctor --fix` remains the repair path for damaged managed files.
- Reduced recurring context cost with mode-specific Task Pack evidence caps, compact default contracts, and a smaller SessionStart payload while preserving all capability sections and profile overrides.
- Made changed-file evidence diff-first and added `check --suggestions-only`, improving changed-line coverage while removing repeated Git scope from Task Pack integrations.
- Added a shared Sensitive Output Guard for Task Pack and publish integrations, bounded changed-content scoring for more relevant smart-sort excerpts, and UTF-8-safe line-boundary truncation.
- Made Task Pack output fail closed through a same-directory staging file and atomic replacement, so failed writes no longer report success or mutate non-file destinations.
- Extracted Git target/base validation, merge-base resolution, and committed/worktree path snapshots into a shared Git Scope module used by `pack`, `check`, and `publish check`.
- Kept Task Pack redaction conservative while making publish secret scans ignore recognizable code expressions, reducing false positives without skipping test fixtures.
- Scoped publish secret findings to added committed/staged/unstaged lines plus full untracked files, with source line mapping for diff evidence.
- Extracted model aliases, limits, and Pack Mode budgets into a shared Model Preset module used by `pack`, `estimate`, and `doctor`.
- Extracted explicit-project identity, Profile loading, worktree slug, and default Task Pack path rules into a shared Target Project Context module used across command adapters.
- Kept deleted shell paths in Agent Check scope reports while excluding them from executable `bash -n` verification commands, including explicit target-ref checks.

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
