# Changelog

## Unreleased

- Added the first P1 Repair Pack tracer bullet: failed `agent-rails verify` steps now retain bounded complete-line evidence and append a redacted, terminal-safe diagnostic summary without changing live output, exit status, or publish short-circuit behavior.
- Started the developer-first coding-agent roadmap with bounded Task Code Evidence: a clean Target Project snapshot can now use literal Goal terms to select relevant tracked files, lightweight symbols, and tests without adding a public command, third-party dependency, or source-body expansion.
- Added a durable coding-agent evolution document that records the GitHub research, product boundary, retrieval/repair/decomposition/model-routing roadmap, Memory role, non-goals, and paired evaluation gates.

## 0.6.1 - 2026-07-16

- Started the Shell-to-Python migration with a standard-library `src/agent_rails` package: `agent-rails estimate` now runs through Python Model Preset, Tokenizer, and rendering Modules while its Compatibility Shell only loads the existing Profile and forwards arguments.
- Reused the Python Tokenizer Interface from the Context Budget Assembler, added direct Python unit coverage for tokenizer failover/cache behavior, and preserved the estimate CLI's profile, input, output, and error contracts through black-box tests.
- Added Python Paths, Shell Profile Adapter, and Target Project Context Modules, moved every production caller onto an explicit trusted Python seam, and deleted the superseded Target Project Shell implementation while preserving Git-root discovery, Profile precedence, POSIX worktree slugs, environment-file overlays, lexical config paths, caller-specific Profile fields, and child `HOME` isolation.
- Made the Python Model Preset registry the single data source, moved Task Pack mode normalization, model/explicit budget precedence, density caps, and section budgets into a typed Pack Policy Module, switched Doctor to the Python known-model Interface, and deleted the superseded Model Preset Shell.
- Removed the vendor-specific HTTP, credential, table, and response-parsing implementation from Task Pack, replacing it with a provider-neutral Python Online Memory Interface whose external command Adapter owns credentials and protocol, returns UTF-8 Markdown, and fails back to local memory without blocking Pack generation.
- Added an absolute Trusted Python Bootstrap so Target Projects cannot shadow the `agent_rails` package, and hardened online Adapter execution with streaming output limits, whole-process-group deadlines, suppressed diagnostics, and an untrusted-data Markdown envelope.
- Replaced Doctor's remaining Shell diagnostics and repair orchestration with a typed Python Application Service: Profile/env attribution, bounded no-follow reads, visible terminal escaping, provider-neutral online-memory smoke, model/plugin/adapter/Git checks, and Claude `--fix` now live behind one Interface; the former 517-line Shell is a 12-line trusted bootstrap.
- Replaced the 251-line Shell `profile init` implementation with a Python Module for canonical Target Project resolution, structured verification-command detection, safely escaped rendering, and atomic `0600` writes; project-scoped writes use no-follow directory handles, inherited repo-local `GIT_*` variables cannot redirect root discovery, and the remaining Shell file is only a trusted bootstrap wrapper.
- Moved Git Scope into a Python Module shared by Check, Publish Check, and Task Pack, preserving base-policy, ref, merge-base, worktree/target-only, deleted/rename, and inherited `GIT_*` isolation contracts before deleting the old Shell implementation; NUL-safe path classification now preserves spaces, arrows, Unicode, and leading dashes while control-character paths fail closed.
- Moved the Sensitive Output Guard into one streaming Python detection Implementation with conservative Task Pack redaction and higher-precision Publish scanning, preserving placeholder/code-expression suppression, PEM/PGP private-key blocks, added-diff line mapping, non-UTF-8 byte round-tripping, and fail-closed unreadable-file behavior before deleting the old AWK/Shell implementation.
- Moved the Context Budget Assembler into `src/agent_rails/context`, leaving the existing script path as a 19-line Trusted Python Bootstrap while preserving hard-cap allocation, tokenizer-service, metadata, and Task Pack caller contracts through direct package and black-box tests; OpenCode now starts the resident service with isolated Python startup, and malformed JSONL requests return errors without terminating it.
- Moved Task Pack Change Evidence into Python: one Module now composes isolated Git Scope, Unicode-safe goal ranking, diff-first Sensitive Output redaction, no-follow untracked reads, complete-line truncation, safe Markdown paths/fences, and the five Git-state sections; this removed over 400 lines from the Pack Shell together with Pack Policy migration.
- Moved Task Pack Project Docs into Python: changed-path-aware Entry Docs selection, explicit-target existence checks, Context Gaps, Project Configuration status, and injection-safe Markdown rendering now live behind one Module, removing the corresponding Shell selection and rendering logic.
- Moved Verification Plan selection into Python: Check and Task Pack now share one ordered, de-duplicated Plan built from the same NUL-safe changed-path snapshot, target-tree shell checks use isolated Git and reject symlinks, Pack pins downstream consumers to the Evidence Module's resolved target commit, and Check's execution guard reads the isolated project's real HEAD instead of inherited Git context.
- Moved Task Pack Memory Evidence into Python: deterministic no-follow local-card discovery, quoted and unquoted trigger matching, local/online budget allocation, Sensitive Output redaction, safe Markdown envelopes, and non-fatal provider-neutral Online Memory fallback now live behind one context Module.
- Moved Task Pack Contract Sections into Python: Profile rules now render through typed, independently placeable Agent Rails, subagent-result, and delivery sections with stable ordering, Lite guidance, strict UTF-8, and visible control-character escaping.
- Moved final Task Pack composition and publication into Python: the Renderer now owns the fixed 17-section order, safe Goal/path display, dynamic Verification fences, PackPolicy-derived budget metadata, direct hard-cap assembly, strict UTF-8, and same-directory `0600` atomic replacement; the Pack Shell is down to Profile/env loading and context-module orchestration.
- Hardened hard-token assembly so every actual section keeps complete headings, the Grill Gate survives contract truncation, fenced evidence remains balanced, and undersized budgets fail while preserving the previous Pack instead of reporting success with empty or structurally broken output.
- Added a shared Task Pack Markdown Interface for visible control characters and collision-free code spans/fences, removing formatting dependencies between Change Evidence, Project Docs, Memory Evidence, Contract Sections, and the Final Renderer.
- Replaced the remaining 399-line Task Pack Shell orchestration with one Python Application Service that loads Profile/env once through an allowlist, resolves Pack Policy once, invokes every context Module in memory, freezes explicit target SHAs across consumers, preserves non-fatal Verification fallback, and anchors online-memory/tokenizer Adapters to the Target Project; the Shell entrypoint is now a 12-line bootstrap.
- Hardened final Markdown boundaries against raw HTML Goals, hostile Session Marker paths, and fenced fake Grill headings while preserving safe single-line Goal rendering.
- Replaced the 407-line Memory Suggest Shell with a Python Application Service that resolves Target Project/Profile once, allowlists only the local memory directory, never loads online-memory credentials, derives NUL-safe Git worktree evidence, rejects non-canonical card IDs and sensitive persisted content, and leaves a 12-line trusted bootstrap.
- Added one Private Text Publisher shared by Task Pack and Memory Suggest for complete staging, strict UTF-8, private `0600` files, safe target checks, atomic per-file publication, and explicit partial-commit reporting; Git Scope now also exposes its isolated worktree snapshot as an in-memory Interface.
- Replaced the 276-line shared Adapter Content Shell with a typed Python renderer used by Claude and OpenCode, moved the duplicate Claude project block into the same Module, retained byte-compatible normal guides/commands, and made executable/Profile interpolation shell-safe. SessionStart now reads lossless generated Profile metadata with a legacy fallback instead of relying only on a fragile quoted-string regex.
- Replaced Agent Check's Shell orchestration with a Python Application Service that loads its allowlisted Profile once, freezes Git Scope and Verification Plan in memory, rejects target or changed-path drift before execution, isolates inherited repository context, preserves the public report and exit contracts, and invokes opaque verification commands through an explicit child-shell argv; the Shell entrypoint is now a 12-line bootstrap.
- Replaced Publish Check's Shell orchestration with a Python Application Service that loads its allowlisted Profile once, freezes Git Scope and Verification Plan, scans committed/staged/unstaged diffs plus no-follow untracked files, sanitizes remote credentials and terminal data, and preserves the deployment-baseline report; the former 305-line Shell is a 12-line trusted bootstrap.
- Moved Managed Adapter Workspace ownership, tracked-path protection, skill inventory, and local-ignore mechanics into Python with no-follow directory-handle writes, then migrated OpenCode install/doctor/uninstall as the first real Adapter consumer; strict fingerprinted v2 skill ownership, atomic apply-time rechecks, survivor state, exact config ownership, and full preflight preserve user files and reject invalid plans before mutation, while its 550-line Shell entrypoint is now a 12-line bootstrap.
- Replaced Claude install/uninstall orchestration with a typed Python Application Service that composes Target Project Context, Adapter Content, and Managed Adapter Workspace; rules, global reminders, SessionStart settings, generated artifacts, strict v2 skill ownership, and local-ignore paths are preflighted before mutation, the two public Shell entrypoints are now 12-line bootstraps, and the obsolete 439-line shared Workspace Shell has been deleted.
- Replaced Codex install/doctor/uninstall orchestration with a typed Python Application Service that resolves optional Target Project context without executing its Profile, preserves exact external-command ordering and exit status, performs bounded no-follow marker checks, and keeps terminal controls inert; the former 209-line Shell is a 12-line trusted bootstrap.
- Replaced the 249-line Run journey Shell with a Python Facade that resolves Profile/environment once, prepares Task Pack in process, estimates the published artifact through the shared Tokenizer Interface, and preserves partial Pack/estimate results; its Shell entrypoint is now a 12-line trusted bootstrap.
- Replaced the 217-line Setup journey Shell with a Python Facade that resolves one Target Project Context, deliberately selects adapters, and composes Claude/Codex/OpenCode install plus Doctor services without recursively invoking the public CLI; exported Profile state is shared once and the Shell entrypoint is now a 12-line trusted bootstrap.
- Replaced the 133-line Verify journey Shell with a Python Facade that shares one Profile/Target Project Context across Agent Check and optional Publish Check, preserves live verification output and child exit status, and short-circuits publish readiness after a failed check; its Shell entrypoint is now a 12-line trusted bootstrap.
- Hardened Verify's delivery-to-publish seam so a successful Check cannot be combined with a later HEAD, worktree, changed-path, or Verification Plan snapshot; real verification child stdout/stderr now stream through bounded terminal escaping into the Facade's selected writers without dual-pipe deadlock or lost partial output.
- Replaced the 228-line top-level Shell command tree with a Python Public CLI Dispatcher that owns help/version/home, nested command validation, exact argv/environment routing, and `--project` working-directory semantics; `bin/agent-rails` is now an 11-line symlink-aware bootstrap that overrides stale kit-home state and cannot import a Target Project shadow package.
- Replaced the 407-line Update Shell with a typed Python Application Service that selects Git-checkout versus Release source, enforces clean fast-forward updates, preserves skip/dry-run gates, resolves project/Profile paths without execution, dispatches exact tool-specific Doctor/Adapter argv, and flushes structured child output before Release re-exec; the former wrapper was later deleted.
- Replaced the 279-line Release Installer Shell policy with a standalone standard-library Python Application Service that validates downloads, checksums, archive layout and version, publishes complete release directories transactionally, protects user-owned paths, and rolls back metadata and managed symlinks on failure; the remaining 16-line Shell only locates the adjacent cold-start Python asset.
- Replaced the 113-line Release Builder Shell with a deterministic Python Builder that isolates Git selection, excludes deleted worktree paths and macOS AppleDouble metadata, normalizes tar metadata, emits the fixed checksum/installer assets, safely unpacks the staged archive for an isolated full-CLI import smoke, publishes without clobbering concurrent owners, retains private recovery data after incomplete rollback, and rolls back partial asset publication; the Shell wrapper has been deleted.
- Replaced the 144-line SessionStart hook policy with a Python Application Service that resolves host/worktree context, detects no-follow project markers, round-trips generated Profile metadata, renders the stable guardrail once, and emits exact Claude text or Codex JSON without carrying host-private fields; the hook is now a 12-line trusted bootstrap.
- Replaced the 130-line Init Shell policy with a Python Application Service that owns CLI/environment precedence and literal-safe zsh, bash, and fish guide rendering; the Shell is now a 12-line trusted bootstrap and still never edits a user's shell startup files.
- Replaced the 79-line Skills Installer Shell policy with a Python Application Service that preflights source trees, rejects symlinks and traversal, preserves manifest order, and atomically refreshes complete skill directories; the Shell is now a 12-line trusted bootstrap.
- Moved Estimate's final Profile loading and precedence policy behind the typed Python Profile Interface, reducing its Shell from 55 lines to a 12-line bootstrap, then deleted the now-unused 108-line shared Paths Shell.
- Removed all 17 public-command Compatibility Shells after the Python dispatcher gained direct isolated-helper routing, then removed the Release Builder Shell and switched CI/tests to its Python entrypoint. Production Shell is now 47 lines across only the symlink-aware CLI, SessionStart host wrappers, and cold-start installer: 7,378 lines (99.4%) below the 7,425-line baseline.
- Hardened SessionStart Profile rendering against Unicode control/format/line/paragraph separators while preserving byte-exact shell-path round trips, and made Skills install retain a private recovery transaction whenever publication rollback cannot restore the user's previous tree.
- Removed Update's final Python-to-Shell-to-Python routes: Release updates now invoke the standalone installer directly, project refreshes enter the trusted Python public dispatcher while retaining stable dry-run text, and post-upgrade continuation re-execs the physical installed Python helper with a private umask.
- Deepened the Python runtime with shared Terminal Output, Adapter Output, Child Process, Target Project Context validation, local Git exclude resolution, and file-stability Modules, reducing exact cross-file clone coverage from 11.30% to 4.46% without widening tool-specific lifecycle Interfaces.
- Added Related Test Selection so each refactor slice can run the smallest safe core, adapters, workflows, or context suite while retaining the 172-entry full pre-delivery gate.
- Verified the final Python-first runtime with all 172 regression entries, deterministic Release asset construction, checksum validation, isolated cold-start installation, and a real OpenCode install/uninstall/reinstall/update/doctor/Task Pack smoke in a clean Target Project.

- Added hard token-budget Task Pack assembly with required-section floors, weighted category allocation, unused-share redistribution, exact external/Hugging Face tokenizer support, and a cached long-lived counting service.
- Replaced OpenCode's static instruction-only integration with a project-local per-request plugin that reads the current session, derives available input space from `model.limit`, and injects a proportional Agent Rails Pack without trimming OpenCode history.
- Removed the built-in `agent-rails eval` logger and `agent-eval` skill so the product CLI stays focused on runtime capabilities; standalone Python tools now own TUI artifact capture, mirrored blind judging, and Codex/OpenCode trajectory conversion to Run IR, OTel, and ATIF.
- Added a durable Shell-to-Python refactor handoff with research sources, implementation status, compatibility contracts, migration gates, and the recommended first tracer bullet.
- Fixed `agent-rails update` in GitHub Release installations so it no longer runs the source-checkout-only test suite after a checksum-verified update.
- Added regression coverage for Release update behavior and a GitHub Actions smoke test that executes the CLI directly from the built archive.
- Unified project maintenance as `update --tool claude|codex|opencode`; each tool now uses its own install and Doctor path, and omitting `--tool` no longer defaults to Claude.
- Unified `--mode local|project` across setup and all three Adapter refresh paths. Local remains the collaborator-safe default; project mode removes managed local ignores and renders portable, committable Adapter files without personal absolute paths.
- Made the bundled Codex SessionStart wrapper and installed skill guidance location-independent for GitHub Release installs and project-mode promotion.

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
