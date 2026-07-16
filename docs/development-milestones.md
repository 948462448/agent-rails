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

## 0.6.0 - Local Adapters, Release Distribution, And Safety

- Added GitHub Release bundles with SHA-256 verification and versioned local installation directories.
- Split self-upgrade from target-project refresh so Release users can update the CLI without a source checkout or project Profile.
- Added tag-to-Release automation with a strict `v<VERSION>` gate and full pre-release tests.
- Added a first-class, project-local OpenCode adapter with install, doctor, and uninstall lifecycle commands.
- Made generated Claude adapter files safely refreshable while preserving user-authored content outside managed blocks.
- Added repository/worktree profile-boundary guidance to SessionStart and Task Packs.
- Hardened Task Pack permissions to `0600` and documented sensitive-output handling.
- Bounded Task Pack evidence density by mode and compacted SessionStart/default contracts to reduce recurring token cost without removing capability sections.
- Switched tracked-file excerpts to actual diff hunks and deepened Agent Check with a narrow Verification Plan Interface for integrations.
- Added a shared Sensitive Output Guard, bounded changed-content scoring, and UTF-8-safe truncation so denser Task Packs remain safer and more relevant.
- Made Task Pack output transactional and extracted a shared Git Scope Module for consistent ref validation and changed-path snapshots across `pack`, `check`, and `publish check`.
- Made publish checks require an explicit deployed-source baseline when the implicit deployment delta cannot be established.
- Split Sensitive Output Guard evidence policy by Interface: conservative Task Pack redaction and higher-precision publish scanning over the same detection grammar.
- Mapped publish diff findings back to source lines and excluded unchanged tracked content while retaining full scans for untracked files.
- Extracted a shared Model Preset Module so `pack`, `estimate`, and `doctor` consume one model alias, limit, and Pack Mode budget contract.
- Added a Target Project Context Module so command adapters share one project-root, Profile, worktree slug, and default Task Pack path contract.
- Deepened the adapter lifecycle into a Managed Adapter Workspace Module so Claude and OpenCode share ownership, tracked-path, skill, write, and local-ignore mechanics while retaining tool-specific configuration behavior.
- Added setup/run/verify as the default three-command user journey while retaining every lower-level command for compatibility and advanced use.
- Reorganized the bilingual README and CLI reference around progressive disclosure instead of exposing implementation-oriented commands during onboarding.
- Made the default shell bootstrap project-neutral and rewrote the bilingual README around installation, connection, normal conversation, visible activation, and common recovery paths.
- Recorded the design, tradeoffs, verification, and follow-ups in [Local Adapters And Release Safety](./local-adapters-and-release-safety.md).

## 0.6.1 - Release Reliability, Live Context Budgets, Evaluation Evidence, And Python Migration

- Added a hard token-budget assembler with required-section floors, weighted allocation, unused-share redistribution, exact optional tokenizers, and a long-lived cached counting service.
- Replaced OpenCode's static integration with a project-local per-request plugin that uses the current session and model limits without trimming OpenCode history.
- Moved A/B evaluation outside the product CLI into standalone Python capture, mirrored blind-judge, Codex/OpenCode trajectory, OTel, and ATIF tools.
- Recorded the current research, implementation status, compatibility contracts, migration phases, and next tracer bullet in [Shell To Python Refactor Handoff](./python-refactor-handoff.zh-CN.md).
- Stopped checksum-verified Release installations from running the source-checkout-only test suite during `upgrade self` and project updates.
- Added a built-archive smoke gate to the tag-driven Release workflow.
- Unified project maintenance as `update --tool claude|codex|opencode`, with tool-specific install and Doctor dispatch and no implicit Claude default.
- Unified collaborator-safe `local` and portable, committable `project` Adapter modes across Claude, Codex, OpenCode, setup, and update.
- Migrated Git Scope and Sensitive Output Guard to standard-library Python Modules, switched their read-only Adapters one at a time under black-box parity tests, and removed the superseded Shell/AWK implementations.
- Migrated every Target Project Context caller through the trusted Python bootstrap with caller-specific Profile field allowlists, then removed the obsolete shared Shell implementation.
- Migrated the Context Budget Assembler into a Python package Module while retaining its existing script path as a minimal Trusted Python Bootstrap.
- Moved Task Pack model, density, and budget resolution into a Python Pack Policy Module and removed the last Model Preset Compatibility Shell.
- Moved Task Pack Change Evidence collection/rendering into Python, including isolated Git commands, goal ranking, redacted excerpts, no-follow untracked reads, and Git-state section rendering.
- Moved Task Pack Project Docs selection/rendering into Python, including changed-path routing, explicit-target Git isolation, context gaps, and configuration status.
- Moved Verification Plan selection/rendering into Python and made Check and Task Pack consume one NUL-safe Plan Interface without recursively reloading Profile or Git Scope.
- Moved Task Pack Memory Evidence collection/rendering into Python, keeping local cards deterministic and private while treating provider-neutral online results as bounded, redacted, untrusted, optional input.
- Moved Task Pack contract rendering into Python, preserving the established Agent Rails, subagent-result, and delivery ordering while preventing Profile control characters from forging Pack sections.
- Moved final Task Pack composition, hard-cap integration, and private atomic publication into Python; hardened the Assembler to reject structurally impossible budgets and to keep all truncated Markdown fences balanced.
- Extracted one Task Pack Markdown Interface so context Modules share control-character, code-span, fence, and UTF-8 behavior without depending on Change Evidence internals.
- Collapsed Task Pack argument/Profile/context orchestration into one Python Application Service; the 12-line Shell bootstrap now only locates the kit and executes the trusted Python entrypoint.
- Collapsed Memory Suggest argument/Profile/Git/render/publish orchestration into one Python Application Service; its 12-line Shell bootstrap no longer owns memory policy or calls online memory.
- Extracted a shared Private Text Publisher for complete staging, `0600`, safe-target checks, per-file atomic publication, and explicit partial-publication results.
- Moved generated Claude/OpenCode guides, commands, and the Claude project block into a typed Python Adapter Content Module; deleted the shared Shell renderer and made SessionStart Profile recovery lossless for shell-active paths.
- Moved Agent Check report composition, target guards, snapshot validation, and child-shell execution into a Python Application Service; reduced its Shell entrypoint to a 12-line trusted bootstrap.
- Moved Managed Adapter Workspace into Python and migrated OpenCode install, doctor, and uninstall as its first end-to-end consumer; reduced the OpenCode Shell entrypoint from 550 lines to a 12-line trusted bootstrap.
- Moved Claude install/uninstall into a Python Application Service, reduced both Shell entrypoints to 12-line trusted bootstraps, and deleted the final 439-line shared Workspace Shell after its last runtime caller moved.
- Moved Doctor diagnostics, optional online-memory smoke, and explicit Claude repair composition into a Python Application Service; reduced the former 517-line Shell to a 12-line trusted bootstrap and made diagnostic paths/control data fail closed.
- Moved Publish Check scope, deployment baseline, repository metadata, Verification suggestions, and four-layer secret scanning into a Python Application Service; reduced the former 305-line Shell to a 12-line trusted bootstrap.
- Moved Codex plugin install, doctor, uninstall, and optional project-repair composition into a typed Python Application Service; reduced the former 209-line Shell to a 12-line trusted bootstrap while preserving external command status and Profile-free project resolution.
- Moved Run Pack preparation, exact-artifact estimation, partial-result handling, and CLI precedence into a Python User Journey Facade; reduced the former 249-line Shell to a 12-line trusted bootstrap.
- Moved Setup tool selection and Adapter/Doctor composition into a Python User Journey Facade that shares one Target Project Context and exported Profile environment; reduced the former 217-line Shell to a 12-line trusted bootstrap.
- Moved Verify delivery/publish composition into a Python User Journey Facade that shares one Context across Agent Check and Publish Check while preserving live child output and failure short-circuiting; reduced the former 133-line Shell to a 12-line trusted bootstrap.
- Moved the public command tree, nested routing, authoritative home/version state, and `--project` cwd seam into a Python Public CLI Dispatcher; reduced the former 228-line top-level Shell to an 11-line symlink-aware bootstrap.
- Moved Git/Release source selection, clean fast-forward policy, source-test gates, Profile-free project resolution, Doctor/Adapter dispatch, and Release re-exec into a typed Python Update Application Service; reduced the former 407-line Shell to a 12-line trusted bootstrap.
- Moved Release archive construction, standalone installation, and SessionStart host policy into Python while retaining only deterministic build, cold-start, and host-protocol bootstraps.
- Moved Init guide rendering and Skills publication into Python, moved Estimate's final Profile policy behind the typed Python Interface, and deleted the last shared Paths Shell after all callers migrated.
- Removed all public-command Compatibility Shells and the Release Builder wrapper after switching the Python dispatcher, CI, and tests to isolated Python-helper execution. Production Shell is now 47 lines across four true operating-system/host/cold-start seams, down from 7,425.

## Near-Term Backlog

- Run one real OpenCode `off` versus `rails-lite` A/B fixture and record the non-sensitive summary.
- Run the full regression suite and one clean GitHub Release install/update smoke against the final Python-first runtime.
- Add a clearer repair command or doctor warning for stale adapter profile paths.
- Extend the Sensitive Output Guard to encoded envelopes and carefully bounded high-entropy detection without increasing configuration false positives.
- Add an optional publish receipt for artifact, configuration, and smoke-test evidence.
- Keep release docs focused on personal install, upgrade, verify, and rollback flows.
