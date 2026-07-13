# Local Adapters And Release Safety

This note records the design decisions behind the current local-adapter and release-safety iteration. Agent Rails remains a generic, personal kit: target repositories supply their own project context, while the kit provides reusable workflow guardrails.

## Scope

### First-Class OpenCode Adapter

- `agent-rails opencode install` creates project-local guide, command, skill, and configuration files.
- Installation uses local Git excludes by default and does not modify the user's global OpenCode configuration.
- Installed skill names are recorded in adapter-local inventories (`.claude/.agent-rails-managed-skills` and `.opencode/.agent-rails-managed-skills`); uninstall removes only those exact names and leaves unrelated skills intact.
- `doctor` verifies the generated integration, while `uninstall` removes only Agent Rails-managed artifacts and preserves tracked files unless explicitly forced.

### Safe Adapter Refresh

- Claude and OpenCode adapter files carry an Agent Rails generated marker. Installation refreshes recognized generated guides and commands without requiring a destructive force flag.
- An existing same-path file without the marker or a compatible legacy signature is treated as user-authored and preserved.
- The automatic `update` flow uses the ownership-aware refresh path without `--force`; `doctor --fix` keeps its explicit repair semantics for corrupted managed files.
- Existing user-authored content outside the managed Agent Rails block remains intact.
- Codex, Claude, and OpenCode resolve profiles through the same project-aware rules.

Generated-file recognition and managed-skill inventory mechanics live in the shared Adapter Lifecycle module. Guide and pack/lite/check command rendering lives in the shared Adapter Content module, with tool-specific guides and frontmatter behind one rendering Interface. Adapter entrypoints retain tool-specific CLI behavior, paths, ignore blocks, and tracked-file policy; these shared Interfaces stay narrow while preserving existing on-disk formats.

### Target And Profile Boundaries

- A profile is scoped to the source repository that produced it.
- Another worktree of the same repository may reuse that repository profile, but commands must still pass the exact target worktree root through `--project`.
- A sibling or unrelated repository must resolve its own adapter/profile instead of inheriting the current repository's profile.
- Task Packs must be regenerated after the target repository or worktree changes.

These checks are guidance rather than a basename equality gate. Linked worktrees often have different directory names, and `PROJECT_NAME` may intentionally be customized, so basename matching would reject valid setups without proving repository identity.

### Task Pack Output Safety

- SessionStart and generated Task Packs now state the target-scope and sensitive-output rules explicitly.
- Base64 and URL encoding are treated as representation changes, not redaction.
- Operators should project only required fields from logs, DOM snapshots, job tables, and similar sources, and must not repeat an exposed secret in later output.
- Task Pack files are created with mode `0600`, including an existing destination that is overwritten.
- Task Pack excerpts and publish secret findings share a Sensitive Output Guard for supported shell/YAML/JSON assignments, Authorization headers, and PEM private-key blocks.

Automatic sanitization is deliberately conservative: placeholders and tokenizer configuration remain readable, while supported secret-bearing values are replaced with `<redacted>`. Encoded envelopes and unnamed high-entropy material still require explicit operator care.

### Publish Baseline Safety

The remote branch tip is a source-control baseline, not proof of what is deployed. When the implicit upstream is missing or is identical to the target revision, publish checking reports `Deployment delta: UNRESOLVED` and requires:

```bash
agent-rails publish check --project <target> --base <currently-deployed-source-revision>
```

This prevents a clean push comparison from being misreported as a verified deployment delta.
An explicit base must resolve to a Git commit; invalid refs fail before any diff or readiness summary is produced. The same ref validation contract applies to `pack` and `check`.

## Verification

The implementation is covered by the repository test suite, including:

- OpenCode install, doctor, refresh, exact-inventory uninstall, configuration merge, user-file preservation, legacy inventory migration, and local-ignore behavior.
- Claude managed-file refresh, exact-inventory uninstall, and same-path user-file preservation behavior.
- The shared Adapter Lifecycle Interface, including legacy generated-file signatures, inventory validation, and de-duplication.
- The shared Adapter Content Interface, with byte-for-byte compatibility checks for Claude and OpenCode generated guides and commands.
- SessionStart target/profile and sensitive-output guidance.
- Task Pack `0600` permissions and generated guidance sections.
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
