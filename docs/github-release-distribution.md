# GitHub Release Distribution

Agent Rails is a multi-file Python kit with small host bootstraps, not a standalone binary. A Release therefore ships one complete kit archive instead of a wrapper that still depends on a source checkout.

This document defines the Release-specific contract. For the complete runtime architecture and update flow, see [How Agent Rails Works](./how-agent-rails-works.en.md) or [Agent Rails 工作原理](./how-agent-rails-works.zh-CN.md).

## Distribution contract

Every GitHub Release contains fixed asset names:

- `agent-rails.tar.gz`: the Git-metadata-free kit under `agent-rails-<version>/`.
- `agent-rails.tar.gz.sha256`: the archive digest consumed by the installer.
- `install.sh`: the cold-start Shell bootstrap, copied from `scripts/agent-release-install.sh`.
- `release_install.py`: the standalone standard-library installer that owns download, validation, extraction, rollback, and symlink policy.

Fixed asset names make the GitHub `/releases/latest/download/...` redirect usable without a JSON parser. The version still lives inside the archive's `VERSION` file and in the Release tag.

The installer downloads both archive and digest before mutating the active install. It rejects checksum mismatches, unsafe archive paths, version mismatches, malformed existing release directories, and user-authored non-symlink `current` or CLI paths.

The Builder safely unpacks its final archive and imports the complete CLI before publication. Asset publication is no-clobber and transactional; if an old asset cannot be restored, its private recovery directory is retained instead of being deleted.

## Local layout and switching

The default layout is:

```text
~/.local/share/agent-rails/
├── releases/
│   ├── 0.6.0/
│   └── <future-version>/
└── current -> releases/<active-version>

~/.local/bin/agent-rails -> ~/.local/share/agent-rails/current/bin/agent-rails
```

Installation finishes by replacing temporary symlinks with `current` and the stable CLI path. Previous release directories remain available for rollback. The CLI resolves its kit home from its own symlink-aware path; an old shell export cannot silently redirect the Release CLI to a previous source checkout.

## Install, update, and rollback

Initial installation does not need `git clone`:

```bash
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/install.sh \
  -o /tmp/agent-rails-install.sh
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/release_install.py \
  -o /tmp/release_install.py
less /tmp/agent-rails-install.sh /tmp/release_install.py
bash /tmp/agent-rails-install.sh
```

Update only the installed kit:

```bash
agent-rails upgrade self
```

Install or roll back to an exact published version:

```bash
agent-rails upgrade self --version 0.7.0
```

`agent-rails update --tool claude|codex|opencode` is the wider project maintenance loop: kit update, source tests when running from a Git checkout, the selected tool's target-project Doctor, Adapter refresh, and final Doctor. In a Release Install it uses the verified archive path and skips the source-only test suite. Tool selection is mandatory so maintenance cannot silently refresh the wrong Adapter.

## Local release candidate

Before tagging, build the same four assets through the isolated Python helper:

```bash
AGENT_RAILS_HOME="$PWD" \
  python3 -I scripts/agent-python-cli.py release-build --output dist
```

For local testing of uncommitted refactor files, use a separate output directory and opt in explicitly:

```bash
AGENT_RAILS_HOME="$PWD" \
  python3 -I scripts/agent-python-cli.py release-build \
    --output dist/0.7.0-candidate \
    --include-worktree
```

`--include-worktree` is only for a local candidate. Published assets must be rebuilt from the clean tagged tree by the Release workflow.

## Publishing a release

Release automation is tag-driven and intentionally refuses ambiguous input:

1. Update `VERSION`, all plugin manifests, `CHANGELOG.md`, and the milestone documentation.
2. Merge the release commit into `main`.
3. Run `bash tests/run.sh` and `bin/agent-rails publish check --project "$(pwd)" --base <last-release-tag>`.
4. Create and push the exact version tag:

   ```bash
   git tag -a v0.7.0 -m "Agent Rails 0.7.0"
   git push origin v0.7.0
   ```

The workflow requires the tag to equal `v<VERSION>`, requires the tagged commit to be contained in `origin/main`, reruns the full tests, rebuilds and verifies the assets, then publishes the GitHub Release with generated notes.

SHA-256 detects corruption or asset mismatch; it is not a substitute for release signing or artifact attestations. Those can be added later without changing the versioned-directory or atomic-switch contract.
