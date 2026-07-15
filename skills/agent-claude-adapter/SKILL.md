---
name: agent-claude-adapter
description: Install the Agent Rails Claude Code adapter so Claude gets an Agent Rails skill set and slash commands for context orchestration. Use when the user wants Claude Code to prefer Agent Rails pack/check before substantial work, lite POC work, or deploy-prep checks.
---

# Agent Claude Adapter

Use this skill to install Agent Rails into a Claude Code project.

## Workflow

1. Identify the target project and profile.
2. Preview installation:

```bash
agent-rails claude install --project /path/to/project --profile /path/to/profile --dry-run
```

Project-mode adapters expect `agent-rails` on PATH. Local adapters may use the absolute CLI path generated for that machine.

3. Install project-local adapter files:

```bash
agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local
```

This installs:

- `.claude/skills/agent-*`
- `.claude/commands/agent-rails-pack.md`
- `.claude/commands/agent-rails-lite.md`
- `.claude/commands/agent-rails-check.md`
- `.claude/AGENT_RAILS.md`
- local `CLAUDE.local.md`
- local ignore entries for `.claude/` and `CLAUDE.local.md`

The installed adapter should enforce this visible session marker protocol:

- Pack or lite: `AGENT RAILS: ON (mode=<mode>, pack=<task-pack-path>)`
- Check only: `AGENT RAILS: CHECK-ONLY (reason=<reason>)`
- Skip: `AGENT RAILS: SKIPPED (reason=<reason>)`

Slash commands must resolve the current worktree root at runtime with `git rev-parse --show-toplevel`; do not hardcode the install-time project path into pack/check commands. The agent should read the Task Pack path printed by the command.

4. If Claude Code tends to load the local block too late, add a personal global reminder without touching the target repo:

```bash
agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local --global-reminder
```

This writes a short marked block to `~/.claude/CLAUDE.md` that tells Claude to use the local Agent Rails adapter when one exists.

5. To make the Claude config project-owned and commit-ready, ask for explicit confirmation, then run:

```bash
agent-rails claude install --project /path/to/project --profile /path/to/profile --mode project
```

6. To refresh an existing adapter after Agent Rails changes:

```bash
agent-rails claude upgrade --project /path/to/project --profile /path/to/profile --mode local --global-reminder
```

7. To remove the adapter, preview first:

```bash
agent-rails claude uninstall --project /path/to/project --global-reminder --dry-run
```

Then run without `--dry-run` only when the user wants the generated adapter removed.

## Rules

- Do not claim this hard-controls Claude Code's internal context window.
- Local mode writes `CLAUDE.local.md` and ignore entries; in git repos it uses `.git/info/exclude` instead of changing tracked `.gitignore` or team `CLAUDE.md`.
- `--global-reminder` writes only a marked personal block in `~/.claude/CLAUDE.md`; it does not write Agent Rails files to the business repo.
- Project mode writes files that may be committed.
- Uninstall removes only Agent Rails generated files, the marked `CLAUDE.local.md` block, the marked `CLAUDE.md` block for project mode or legacy local installs, and the marked global reminder when `--global-reminder` is passed.
- Use `--dry-run` first when installing into a business repo.
- Prefer project-specific profiles over the generic default profile.
- Do not hide whether the session used, checked, or skipped Agent Rails.

## Delivery

Report the installed paths and the command Claude should use:

```text
/agent-rails-pack <goal>
/agent-rails-lite <goal>
/agent-rails-check
```
