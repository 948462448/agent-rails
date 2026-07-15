# Local Adapters And Release Safety

This note records the design decisions behind the current local-adapter and release-safety iteration. Agent Rails remains a generic, personal kit: target repositories supply their own project context, while the kit provides reusable workflow guardrails.

For a narrative overview of the complete system and its diagrams, see [How Agent Rails Works](./how-agent-rails-works.en.md) or [Agent Rails 工作原理](./how-agent-rails-works.zh-CN.md).

## Scope

### First-Class OpenCode Adapter

- `agent-rails opencode install` creates a project-local request plugin plus guide, command, skill, and configuration files.
- The request plugin uses OpenCode's `experimental.chat.system.transform` hook. It reads the current session, derives the input ceiling from `model.limit`, reserves response/safety space, and injects a token-budgeted Pack on each model request.
- Candidate context is refreshed for each user message and reused within that turn. The plugin never trims OpenCode's own conversation history.
- Installation uses local Git excludes by default and does not modify the user's global OpenCode configuration.
- Installed skill names are recorded in adapter-local inventories (`.claude/.agent-rails-managed-skills` and `.opencode/.agent-rails-managed-skills`); uninstall removes only those exact names and leaves unrelated skills intact.
- `doctor` verifies the generated integration, while `uninstall` removes only Agent Rails-managed artifacts and preserves tracked files unless explicitly forced.

### Safe Adapter Refresh

- Claude and OpenCode adapter files carry an Agent Rails generated marker. Installation refreshes recognized generated guides and commands without requiring a destructive force flag.
- An existing same-path file without the marker or a compatible legacy signature is treated as user-authored and preserved.
- The automatic `update` flow uses the ownership-aware refresh path without `--force`; `doctor --fix` keeps its explicit repair semantics for corrupted managed files.
- Existing user-authored content outside the managed Agent Rails block remains intact.
- Codex, Claude, and OpenCode resolve profiles through the same project-aware rules.

Generated-file recognition, managed-skill inventory, tracked-path protection, generated-file writes, skill installation/removal, and local-ignore block lifecycle live in the shared Managed Adapter Workspace Module. Guide and pack/lite/check command rendering lives in the shared Adapter Content Module, with tool-specific guides and frontmatter behind one rendering Interface. Adapter entrypoints retain tool-specific CLI behavior, paths, configuration merges, and reminder or instruction blocks; the shared Interfaces preserve existing on-disk formats and output semantics.

### Shared Model Presets

Model aliases, canonical names, context and throughput limits, and Pack Mode token budgets live in one Model Preset Module. Task Pack generation and token estimation load the same preset data, while Doctor uses the same known-model Interface. `generic` remains a valid model name without numeric limits, and unknown names remain warning-only in Doctor so existing custom-model workflows stay compatible.

### Progressive CLI Surface

The default user journey is intentionally limited to `setup`, `run`, and `verify`. These are orchestration facades, not replacement domain implementations: Setup resolves Target Project Context and delegates to existing adapter installers and Doctors; Run keeps the existing Task Pack workflow; Verify delegates plan execution to Agent Check and optionally adds publish check. All lower-level commands remain compatible and are documented in the bilingual CLI reference.

Automatic Setup proceeds only when exactly one supported coding-agent CLI is detected. Multiple detected tools require an explicit `--tool` selection, while `--tool all` records deliberate intent to install every supported personal integration. This avoids turning convenience into an unexpected user-level or project-local mutation.

### Target And Profile Boundaries

- A profile is scoped to the source repository that produced it.
- Another worktree of the same repository may reuse that repository profile, but commands must still pass the exact target worktree root through `--project`.
- A sibling or unrelated repository must resolve its own adapter/profile instead of inheriting the current repository's profile.
- Task Packs must be regenerated after the target repository or worktree changes.

These checks are guidance rather than a basename equality gate. Linked worktrees often have different directory names, and `PROJECT_NAME` may intentionally be customized, so basename matching would reject valid setups without proving repository identity.

The shared Target Project Context Module canonicalizes explicit `--project` paths to their Git root, resolves the applicable Profile, records whether the target is a Git repository, and derives the effective project name, worktree slug, and default Task Pack path after configuration is loaded. Claude, OpenCode, Doctor, Run, Update, Codex, Agent Check, publish check, and memory suggestion entrypoints consume this Interface while retaining their own user-facing output and failure policy.

### Task Pack Output Safety

- SessionStart and generated Task Packs now state the target-scope and sensitive-output rules explicitly.
- Base64 and URL encoding are treated as representation changes, not redaction.
- Operators should project only required fields from logs, DOM snapshots, job tables, and similar sources, and must not repeat an exposed secret in later output.
- Task Pack files are rendered to a same-directory `0600` staging file and atomically replace the destination only after successful generation. Failed writes do not print success, retain a stale pack as fresh output, or chmod a non-file destination.
- Token-budget Packs are assembled under a hard cap. Required sections receive minimum floors, weighted categories receive the remaining budget, and unused shares are redistributed to categories with unmet demand.
- Exact external or Hugging Face tokenizers are optional. The long-lived OpenCode assembler loads them once and caches counts by content hash; `auto` safely falls back to a character estimate.
- Task Pack excerpts and publish secret findings share a Sensitive Output Guard for supported shell/YAML/JSON assignments, Authorization headers, and PEM private-key blocks.

Automatic Task Pack sanitization is deliberately conservative: placeholders and tokenizer configuration remain readable, while supported secret-bearing values are replaced with `<redacted>`. Publish scanning reuses the same detection grammar but excludes recognizable code expressions. For tracked files it scans only added committed, staged, and unstaged diff lines and maps findings back to source paths and line numbers; untracked text files are scanned in full. Tests remain in scope when their lines change, while unchanged fixtures and removed values do not become release findings. Encoded envelopes and unnamed high-entropy material still require explicit operator care.

### Publish Baseline Safety

The remote branch tip is a source-control baseline, not proof of what is deployed. When the implicit upstream is missing or is identical to the target revision, publish checking reports `Deployment delta: UNRESOLVED` and requires:

```bash
agent-rails publish check --project <target> --base <currently-deployed-source-revision>
```

This prevents a clean push comparison from being misreported as a verified deployment delta.
An explicit base must resolve to a Git commit; invalid refs fail before any diff or readiness summary is produced. The same ref validation contract applies to `pack` and `check`.

Default-base policy, commit-ref validation, merge-base resolution, and committed/worktree path snapshots live in the shared Git Scope Module. Task Pack and Agent Check use the project policy (`origin/main`, `origin/master`, `main`, `master`); publish checks additionally prefer the current upstream because their Interface describes push scope.

### Release Distribution Safety

GitHub Release distribution packages the complete multi-file kit, not only `bin/agent-rails`. The standalone installer verifies the published SHA-256 digest and archive layout before creating a version directory, then switches `current` and the user CLI through temporary symlinks. Existing non-symlink paths are treated as user-owned and cause a hard failure.

Release installation does not change Target Project or Adapter ownership rules. `upgrade self` only changes the kit and keeps older version directories for rollback; the wider `update` command may subsequently refresh a target Adapter. Source checkouts remain supported through the original fast-forward pull path.

The tag workflow validates that `v<VERSION>` points to a commit contained in `main`, reruns the full suite, builds the fixed-name assets, verifies their digest, and creates the Release through GitHub CLI. See [GitHub Release Distribution](./github-release-distribution.md) for the asset and rollback contract.

## Verification

The implementation is covered by the repository test suite, including:

- OpenCode install, doctor, refresh, exact-inventory uninstall, configuration merge, user-file preservation, legacy inventory migration, and local-ignore behavior.
- Claude managed-file refresh, exact-inventory uninstall, and same-path user-file preservation behavior.
- The Managed Adapter Workspace Interface, including legacy generated-file signatures, inventory validation, tracked/unmanaged preservation, skill lifecycle, and idempotent local-ignore blocks.
- The shared Adapter Content Interface, with byte-for-byte compatibility checks for Claude and OpenCode generated guides and commands.
- SessionStart target/profile and sensitive-output guidance.
- Task Pack `0600` permissions and generated guidance sections.
- Task Pack failure behavior for non-file output destinations and the shared Git Scope Module Interface, including target-only snapshots that exclude working-tree changes.
- The shared Model Preset Interface, including alias normalization, Pack Mode budgets, `generic`, unknown models, and reset behavior between loads.
- The shared Target Project Context Interface, including nested Git paths, Profile-aware names, explicit worktree slugs, missing Profiles, and default Task Pack paths.
- Setup tool selection and dry-run orchestration, plus Verify execution, preview, and publish composition.
- Release asset construction, checksum rejection, non-Git installation, stable CLI linkage, and project-neutral self-upgrade.
- Resolved, unresolved, and invalid publish-baseline cases, plus invalid-base rejection in `pack` and `check`.

Run the release checks with:

```bash
bash tests/run.sh
git diff --check
bin/agent-rails check --project "$(pwd)" --print-only
```

## Follow-Ups

- Extend the Sensitive Output Guard to encoded envelopes and carefully bounded high-entropy detection without reintroducing tokenizer/config false positives.
- Add an optional publish receipt that records artifact identity, configuration propagation, and smoke-test evidence.
- Keep all future examples and defaults repository-neutral; business-project behavior belongs in project-local profiles.
