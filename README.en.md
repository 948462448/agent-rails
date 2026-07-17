# Agent Rails

[简体中文](./README.md) | [English](./README.en.md)

Help your coding agent read the right project before it starts and run the right checks before delivery.

Agent Rails is a personal, local guardrail for Claude Code, Codex, and OpenCode. Once connected, you keep talking to your agent normally—there is no new command set to memorize for daily work.

## When It Helps

- You work across multiple projects or worktrees and want to avoid wrong-branch, wrong-directory, or wrong-configuration mistakes.
- You want complex tasks to begin with focused project context and end with checks against the real change scope.
- You want these capabilities for yourself without committing personal tooling into a business repository.

## Start in Five Minutes

Prerequisites: Git, Bash, Python 3.9+, and at least one of Claude Code, Codex, or OpenCode.

### 1. Install the CLI without cloning

Download the installer, inspect it, and then run it:

```bash
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/install.sh \
  -o /tmp/agent-rails-install.sh
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/release_install.py \
  -o /tmp/release_install.py
less /tmp/agent-rails-install.sh /tmp/release_install.py
bash /tmp/agent-rails-install.sh
"$HOME/.local/bin/agent-rails" init
```

Follow the `init` instructions to add a small block to `~/.zshrc`, `~/.bashrc`, or your Fish configuration, then reload the shell. If you previously ran Agent Rails from a source directory, replace the old `AGENT_RAILS_HOME` block with this new output.

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

Replace `codex` with `claude` or `opencode` as needed. Add `--dry-run` to preview the changes first. The default `--mode local` keeps Adapter files inside the project but hides them with a local Git exclude, so collaborators are not required to install Agent Rails.

After the evaluation proves useful and the team is ready to adopt it, promote the Adapter explicitly:

```bash
agent-rails setup --tool codex --mode project
```

Project mode removes the Agent Rails-managed local-ignore block and writes portable, committable files without personal absolute paths. Review the diff before committing them.

If only one supported tool is installed, this is enough:

```bash
agent-rails setup
```

### 3. Restart your coding agent

- Codex: open a new task.
- Claude Code: reopen the session.
- OpenCode: restart it or open a new session.

You are ready to work normally.

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
AGENT RAILS: CHECK-ONLY (...)
AGENT RAILS: SKIPPED (...)
```

Any of these confirms that Agent Rails was considered explicitly. `SKIPPED` also explains why it was unnecessary for that task.

## One More Check Before Delivery

You can ask the agent for a pre-commit check, or run:

```bash
agent-rails verify
```

When a verification command fails, Verify preserves its exit status and live
output, then appends a bounded, redacted Repair Pack focused on the first
diagnostic and confirmed project locations.

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

### How do I update or roll back the CLI?

Update to the latest GitHub Release without a source repository:

```bash
agent-rails upgrade self
```

Roll back or pin to a published version:

```bash
agent-rails upgrade self --version 0.6.1
```

To update the CLI, run checks, and refresh the current project's Adapter in one maintenance flow, choose the coding agent explicitly. For OpenCode:

```bash
agent-rails update --tool opencode
```

Run it from the target repository root or any subdirectory; Agent Rails resolves the Git root automatically. Pass `--project PATH` only when operating from outside the repository.

`update` never guesses the tool. Use `--tool claude` or `--tool codex` for those Adapters. Initial project connection still uses `setup --tool ...`.

### Will it pollute the business repository?

Personal integrations default to `--mode local` and preserve tracked or user-authored same-path files. Only explicit `--mode project` makes managed Adapter files visible to Git. Agent Rails still never commits, pushes, or releases on your behalf.

## Privacy and Safety

- Do not put access keys, cookies, or tokens in the Agent Rails repository.
- Base64 and URL encoding are not redaction.
- When reading logs, pages, or job tables, ask the agent to extract only fields needed for the decision.

## When You Need More Control

Daily use ends here. Updating, uninstalling, custom models, context budgets, Profiles, online memory Adapters, and complete tool commands live in the reference documentation:

- [English CLI Reference](./docs/cli-reference.en.md)
- [中文 CLI 参考](./docs/cli-reference.zh-CN.md)
- [How Agent Rails Works (with architecture and flow diagrams)](./docs/how-agent-rails-works.en.md)
- [Agent Rails 工作原理](./docs/how-agent-rails-works.zh-CN.md)
- [Design and Safety Boundaries](./docs/local-adapters-and-release-safety.md)
- [GitHub Release Distribution](./docs/github-release-distribution.md)
- [Coding Agent Evolution and GitHub Research (Chinese)](./docs/coding-agent-evolution.zh-CN.md)
- [Changelog](./CHANGELOG.md)
