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

## 0.6.0 - User Install Mode

- Added `install.sh` and `agent-rails self install` to install the kit into `~/.agent-rails/kit` without requiring a manual source checkout.
- Added `agent-rails self update` and non-git `agent-rails update` fallback for tarball-based user installs.
- Kept source checkout mode as the development path, where updates still use `git pull --ff-only` plus the full test/doctor loop.

## Near-Term Backlog

- Add a clearer repair command or doctor warning for stale adapter profile paths.
- Investigate Task Pack overwrite warnings under `~/.agent-rails/agent-context`.
- Keep release docs focused on verify and rollback flows.
