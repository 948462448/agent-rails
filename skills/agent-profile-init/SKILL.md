---
name: agent-profile-init
description: Generate a user-level or project-level Agent Rails profile. Use when starting Agent Rails on a new project, when pack/check output lacks project-specific verification, or when the user asks to create generic/local agent config for a repo.
---

# Agent Profile Init

Use this skill to create a user-level profile for a target project. Use project scope only when the user explicitly wants a project-level `.agent-rails/profile`.

## Workflow

1. Identify the target project path.
2. Preview the generated profile:

```bash
agent-rails profile init --project /path/to/project --print-only
```

Project-mode adapters expect `agent-rails` on PATH. Local adapters may use the absolute CLI path generated for that machine.

3. Check that the profile:
   - sources `profiles/default.profile`
   - leaves Task Pack output at the default user-level `~/.agent-rails/agent-context/`
   - names the root entry doc
   - points to domain docs / ADR / agent docs conventions
   - includes only lightweight verification commands that actually exist
4. Write it:

```bash
agent-rails profile init --project /path/to/project
```

For project-level config:

```bash
agent-rails profile init --project /path/to/project --scope project
```

5. Use it explicitly with `pack` and `check` when needed; otherwise Agent Rails auto-discovers `/path/to/project/.agent-rails/profile` first, then `~/.agent-rails/profiles/projects/<project>.profile`:

```bash
agent-rails pack --project /path/to/project --profile /path/to/profile "goal"
agent-rails check --project /path/to/project --profile /path/to/profile --print-only
```

## Rules

- Default to user-level `~/.agent-rails` config for personal setup.
- Use project-level `.agent-rails/profile` only when requested, and keep it local unless the user explicitly wants project-mode sharing.
- Do not store secrets, tokens, cookies, or AccessKeys in profiles.
- Keep profile commands lightweight and agent-runnable.
- Prefer project-specific memory cards for hard-won facts instead of growing the profile.

## Delivery

Report the profile path and the detected verification commands.
