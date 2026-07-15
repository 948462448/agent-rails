# Agent Rails CLI Reference

[简体中文](./cli-reference.zh-CN.md) | [English](./cli-reference.en.md)

This document is for Agent Rails customization, troubleshooting, and development. The daily path needs only `setup`, `run`, and `verify` from the README.

## User-facing Facades

### `setup`

```bash
agent-rails setup \
  [--project PATH] \
  [--profile PATH] \
  [--tool auto|claude|codex|opencode|all] \
  [--no-session-hook] \
  [--dry-run]
```

- `auto` proceeds only when exactly one supported CLI is detected.
- Multiple tools require an explicit choice; `all` means every personal install is intentional.
- Claude uses local mode and enables the SessionStart hook by default.
- Codex reuses the existing plugin install and project-repair flow.
- OpenCode writes only its project-local adapter and does not modify global OpenCode configuration.

### `run`

```bash
agent-rails run \
  [--project PATH] \
  [--profile PATH] \
  [--model NAME] \
  [--pack-mode lite|normal|deep|audit] \
  [--budget CHARS|--token-budget TOKENS] \
  [--tokenizer auto|char|tiktoken|command|huggingface] \
  [--tokenizer-command CMD] \
  [--tokenizer-path PATH] \
  [--print-only] \
  "goal"
```

`run` orchestrates `pack`, `estimate`, `check`, and the memory handoff without controlling the coding agent's internal execution.

With `--token-budget`, `pack` allocates sections by weight, redistributes unused category shares, and hard-caps the final token count. `huggingface` loads a local tokenizer from `--tokenizer-path`; `command` reads the file named by `AGENT_RAILS_TOKENIZER_INPUT`.

### `verify`

```bash
agent-rails verify \
  [--project PATH] \
  [--profile PATH] \
  [--print-only] \
  [--publish] \
  [--base REF] \
  [--target-ref REF] \
  [--no-secret-scan]
```

- The default executes the Verification Plan through `check --run`.
- `--print-only` previews it.
- `--publish` runs the read-only `publish check` after normal verification.
- `--no-secret-scan` is accepted only with `--publish`.

## Advanced Command Groups

| Scenario | Commands |
| --- | --- |
| Context | `pack`, `estimate` |
| Verification and release | `check`, `publish check`, `doctor` |
| Profiles | `profile init` |
| Adapters | `claude install/uninstall`, `codex install/doctor/uninstall`, `opencode install/doctor/uninstall` |
| Maintenance | `update`, `upgrade self`, `init`, `home` |
| Extensions | `skills install`, `memory suggest` |

Use `agent-rails <command> --help` as the exact option reference.

TUI A/B evaluation is not part of the Agent Rails product CLI. Use the standalone `python3 tools/ab_eval.py`; the capture, blind-judge, and trajectory flow is documented in [the Chinese TUI A/B runbook](./tui-ab-eval.zh-CN.md).

Plain `agent-rails init` prints only project-neutral shell command setup. It emits pinned project/Profile compatibility variables only when `--project` is passed explicitly or the corresponding environment is already configured.

## Installation and self-update

The default GitHub Release layout is:

- Version directory: `~/.local/share/agent-rails/releases/<version>`
- Active version: `~/.local/share/agent-rails/current`
- CLI entrypoint: `~/.local/bin/agent-rails`

Update only the kit without resolving the current directory as a project or Profile:

```bash
agent-rails upgrade self [--version VERSION] [--repository OWNER/REPO] \
  [--install-root PATH] [--bin-dir PATH] [--skip-tests] [--dry-run]
```

Update the kit and maintain one project Adapter in the same flow:

```bash
agent-rails update --tool claude|codex|opencode [--project PATH] [--profile PATH] \
  [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
```

`update` requires one explicit tool, then runs that tool's pre-update Doctor, Adapter refresh, and final Doctor. Claude additionally supports `--mode local|project`, `--session-hook`, and `--global-reminder`; those options are rejected for Codex and OpenCode. A source checkout uses `git pull --ff-only` and runs source tests. A Release Install downloads an archive, verifies SHA-256, atomically switches versions, and skips source-only tests. In either mode, `--skip-pull` skips the kit update itself.

## Profiles and Project Scope

Profile resolution order:

1. Explicit `--profile`
2. `<project>/.agent-rails/profile`
3. `<project>/.agent-rails/profile.sh`
4. `~/.agent-rails/profiles/projects/<project>.profile`
5. `~/.agent-rails/profiles/<project>.profile`
6. The kit's `profiles/default.profile`

Worktrees of the same repository may reuse its Profile but must pass their exact root. Another repository must not inherit the Profile injected by the current SessionStart context.

## Pack Modes

| Mode | Intended use |
| --- | --- |
| `lite` | POCs, deployment preparation, focused continuation of an existing plan |
| `normal` | Regular implementation |
| `deep` | Refactors, migrations, architecture, diagnosis, and reviews |
| `audit` | Explicit high-density audits |

Modes change evidence density, not capability sections. The Model Preset Module owns model aliases, limits, and budgets.

## Adapter Ownership

The Managed Adapter Workspace refreshes or removes only generated artifacts with an Agent Rails ownership marker and skills recorded in the exact managed inventory. Tracked files, user-authored same-path files, and unrelated `agent-*` skills are preserved. Git repositories prefer `.git/info/exclude` and do not modify the team's `.gitignore`.

## Publish Baseline

The `publish check` base should be the source revision currently deployed. An upstream branch is a source-control baseline, not deployment evidence; unresolved deployment deltas are reported as `Deployment delta: UNRESOLVED`.

## Memory and Sensitive Output

Local cards live under `~/.agent-rails/memory/<project>/`. Online memory is an optional read provider; this kit does not write to OpenMemory. Access keys, cookies, and tokens must not enter the repository. Base64 and URL encoding are not redaction.

## Related Design

- [Agent Rails Context](../CONTEXT.md)
- [How Agent Rails Works](./how-agent-rails-works.en.md)
- [Agent Rails 工作原理](./how-agent-rails-works.zh-CN.md)
- [Local Adapters And Release Safety](./local-adapters-and-release-safety.md)
- [GitHub Release Distribution](./github-release-distribution.md)
- [Development Milestones](./development-milestones.md)
