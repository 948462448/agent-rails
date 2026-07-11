# Local Adapters And Release Safety

This note records the design decisions behind the current local-adapter and release-safety iteration. Agent Rails remains a generic, personal kit: target repositories supply their own project context, while the kit provides reusable workflow guardrails.

## Scope

### First-Class OpenCode Adapter

- `agent-rails opencode install` creates project-local guide, command, skill, and configuration files.
- Installation uses local Git excludes by default and does not modify the user's global OpenCode configuration.
- `doctor` verifies the generated integration, while `uninstall` removes only Agent Rails-managed artifacts and preserves tracked files unless explicitly forced.

### Safe Adapter Refresh

- Claude adapter installation refreshes generated guides and command files without requiring a destructive force flag.
- Existing user-authored content outside the managed Agent Rails block remains intact.
- Codex, Claude, and OpenCode resolve profiles through the same project-aware rules.

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

This iteration adds behavioral guardrails and file-permission hardening. Automatic content sanitization remains a separate follow-up because it needs format-aware detection and false-positive handling.

### Publish Baseline Safety

The remote branch tip is a source-control baseline, not proof of what is deployed. When the implicit upstream is missing or is identical to the target revision, publish checking reports `Deployment delta: UNRESOLVED` and requires:

```bash
agent-rails publish check --project <target> --base <currently-deployed-source-revision>
```

This prevents a clean push comparison from being misreported as a verified deployment delta.

## Verification

The implementation is covered by the repository test suite, including:

- OpenCode install, doctor, refresh, uninstall, configuration merge, and local-ignore behavior.
- Claude managed-file refresh behavior.
- SessionStart target/profile and sensitive-output guidance.
- Task Pack `0600` permissions and generated guidance sections.
- Resolved and unresolved publish-baseline cases.

Run the release checks with:

```bash
bash tests/run.sh
git diff --check
bin/agent-rails check --project "$(pwd)" --print-only
```

## Follow-Ups

- Add a shared, format-aware sensitive-content guard for generated Task Packs and diagnostic output, including encoded envelopes.
- Add an optional publish receipt that records artifact identity, configuration propagation, and smoke-test evidence.
- Keep all future examples and defaults repository-neutral; business-project behavior belongs in project-local profiles.
