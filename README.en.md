# Agent Rails

[简体中文](./README.md) | [English](./README.en.md)

Help your coding agent read the right project before it starts and run the right checks before delivery.

Agent Rails is a personal, local guardrail for Claude Code, Codex, and OpenCode. Once connected, you keep talking to your agent normally—there is no new command set to memorize for daily work.

## When It Helps

- You work across multiple projects or worktrees and want to avoid wrong-branch, wrong-directory, or wrong-configuration mistakes.
- You want complex tasks to begin with focused project context and end with checks against the real change scope.
- You want these capabilities for yourself without committing personal tooling into a business repository.

## Start in Five Minutes

Prerequisites: Git, Bash, and at least one of Claude Code, Codex, or OpenCode. Claude's startup hook also requires Python 3.

### 1. Make Agent Rails available in your shell

Enter the Agent Rails repository:

```bash
cd /path/to/agent-rails
bin/agent-rails init
```

Follow the printed instructions to add a small block to `~/.zshrc`, `~/.bashrc`, or your Fish configuration, then reload the shell.

Confirm the installation:

```bash
agent-rails --version
```

### 2. Connect your project

Enter the project and select the coding agent you actually use:

```bash
cd /path/to/your-project
agent-rails setup --tool codex
```

Replace `codex` with `claude` or `opencode` as needed. Add `--dry-run` to preview the changes first.

If only one supported tool is installed, this is enough:

```bash
agent-rails setup
```

### 3. Restart your coding agent

- Codex: open a new task.
- Claude Code: reopen the session.
- OpenCode: restart it or open a new session.

You are ready to work normally.

The OpenCode integration installs a project-local
`.opencode/plugins/agent-rails.mjs`. Like Ponytail, it uses a plugin hook to
inject a compact ruleset on every turn, so no `/agent-rails-lite` command is
required. Focused single-area work uses a capsule capped at 1200 characters;
only cross-area, contract, migration, and similarly broad work generates a
Task Pack.

## Daily Use

Talk to the agent as usual from inside the project. For example:

```text
Review the current branch and find issues that must be fixed.
Refactor this module without changing its behavior.
We are preparing a release; check the scope, tests, and sensitive output.
```

Agent Rails chooses how much project context is useful and whether the task needs a full pack or only verification. At the start, the agent shows one of these states:

```text
AGENT RAILS: ON (...)
AGENT RAILS: ON (mode=capsule)
AGENT RAILS: CHECK-ONLY (...)
AGENT RAILS: SKIPPED (...)
```

Any of these confirms that Agent Rails was considered explicitly. `SKIPPED` also explains why it was unnecessary for that task.

## One More Check Before Delivery

You can ask the agent for a pre-commit check, or run:

```bash
agent-rails verify
```

It selects and executes checks from the actual changes. Before a release or deployment, provide the source revision currently deployed when known:

```bash
agent-rails verify --publish --base <deployed-source-revision>
```

## Common Questions

### No `AGENT RAILS` state appeared

Restart the coding agent first, then run this inside the target project:

```bash
agent-rails doctor --project .           # Claude
agent-rails codex doctor --project .     # Codex
agent-rails opencode doctor --project .  # OpenCode
```

If the project is not connected, rerun the matching `agent-rails setup --tool ...` command.

### More than one coding agent is installed

`setup` does not guess. Choose `--tool claude`, `--tool codex`, or `--tool opencode`. Use `--tool all` only when every integration is intentional.

### You switched projects or worktrees

Start the coding agent from the exact new directory. Do not reuse another repository's local connection; run `setup` again in the new location when needed.

### Will it pollute the business repository?

Personal integrations use local ignores by default and preserve tracked or user-authored same-path files. Agent Rails never commits, pushes, or releases on your behalf.

## Privacy and Safety

- Do not put access keys, cookies, or tokens in the Agent Rails repository.
- Base64 and URL encoding are not redaction.
- When reading logs, pages, or job tables, ask the agent to extract only fields needed for the decision.

## When You Need More Control

Daily use ends here. Updating, uninstalling, custom models, context budgets, Profiles, OpenMemory, and complete tool commands live in the reference documentation:

- [English CLI Reference](./docs/cli-reference.en.md)
- [中文 CLI 参考](./docs/cli-reference.zh-CN.md)
- [Design and Safety Boundaries](./docs/local-adapters-and-release-safety.md)
- [Changelog](./CHANGELOG.md)
