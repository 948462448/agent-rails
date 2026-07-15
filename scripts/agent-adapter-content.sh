#!/usr/bin/env bash
# Render generated Agent Rails guides and commands for local tool adapters.

_agent_adapter_content_adapter=""
_agent_adapter_content_version=""
_agent_adapter_content_bin=""
_agent_adapter_content_profile=""

agent_adapter_content_init() {
  _agent_adapter_content_adapter=""
  _agent_adapter_content_version=""
  _agent_adapter_content_bin=""
  _agent_adapter_content_profile=""

  [[ "$#" -eq 4 ]] || {
    printf 'agent_adapter_content_init expects adapter, version, bin, and profile.\n' >&2
    return 2
  }
  case "$1" in
    claude|opencode) ;;
    *)
      printf 'Unknown Agent Rails adapter content type: %s\n' "$1" >&2
      return 2
      ;;
  esac

  _agent_adapter_content_adapter="$1"
  _agent_adapter_content_version="$2"
  _agent_adapter_content_bin="$3"
  _agent_adapter_content_profile="$4"
}

agent_adapter_content_render() {
  [[ "$#" -eq 1 ]] || {
    printf 'agent_adapter_content_render expects one artifact.\n' >&2
    return 2
  }
  local artifact="$1"
  [[ -n "$_agent_adapter_content_adapter" ]] || {
    printf 'Agent Rails adapter content is not initialized.\n' >&2
    return 2
  }

  case "$artifact" in
    guide)
      case "$_agent_adapter_content_adapter" in
        claude) _agent_adapter_content_render_claude_guide ;;
        opencode) _agent_adapter_content_render_opencode_guide ;;
      esac
      ;;
    pack|lite|check)
      _agent_adapter_content_render_command "$artifact"
      ;;
    *)
      printf 'Unknown Agent Rails adapter artifact: %s\n' "$artifact" >&2
      return 2
      ;;
  esac
}

_agent_adapter_content_render_claude_guide() {
  local profile_arg=""
  [[ -n "$_agent_adapter_content_profile" ]] \
    && profile_arg=" --profile \"$_agent_adapter_content_profile\""
  cat <<EOF
<!-- agent-rails:generated -->
# Agent Rails

This project is configured to use Agent Rails for context orchestration.

Agent Rails Version: $_agent_adapter_content_version

Before work, choose the smallest useful Agent Rails path:

- Deep pack: 2+ subprojects, API/contracts/schema/data-model changes, ADR/handbook work, migrations/refactors, or ambiguous product decisions.
- Lite pack: POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook.
- Check only: read-only deploy/release/upload workflows that consume the current branch.
- Skip: pure status queries, simple command output, or fixed operations with no repo change and no branch-consumption risk.

Visible session marker protocol:

- Pack or lite: tell the user the AGENT RAILS: ON marker printed by the pack command before continuing.
- Check only: tell the user AGENT RAILS: CHECK-ONLY (reason=<reason>) before continuing.
- Skip: tell the user AGENT RAILS: SKIPPED (reason=<reason>) before continuing.

Generate and read a Task Pack when the matrix says pack:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg "<goal>"
\`\`\`

For lite POC/deploy-prep work:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg --pack-mode lite "<goal>"
\`\`\`

Task Pack path is worktree-specific. Read the path printed by the pack command, not a stale pack from another worktree.

Follow the Task Pack sections in order:

1. Agent Rails Contract
2. Relevant Entry Docs
3. Memory Cards
4. Grill Gate
5. Verification Suggestions
6. Subagent Result Contract
7. Delivery Checklist

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

When delegating to a subagent, require the subagent to return the Subagent Result Contract from the Task Pack.

Use \`project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; $_agent_adapter_content_bin check --project "\$project_root"$profile_arg --print-only\` before final delivery, and as Step 0 for deploy/release/upload workflows that consume this branch.

After delivery, use \`agent-memory-curator\` to decide whether this task produced reusable memory. If not, record a skip reason:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin memory suggest --project "\$project_root"$profile_arg --decision skip --reason "<why no durable memory>"
\`\`\`

If the lesson is durable, write one small local card:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin memory suggest --project "\$project_root"$profile_arg --decision keep --write-local --title "<short title>" --trigger "<trigger>" --applies-to "<scope>" --verify "<check>" --caution "<scope limits>" "<brief reusable lesson>"
\`\`\`

This kit writes only curated local memory. Treat the external online memory Adapter as read-only; its credentials and provider protocol stay outside Agent Rails.
EOF
}

_agent_adapter_content_render_opencode_guide() {
  local profile_arg=""
  [[ -n "$_agent_adapter_content_profile" ]] \
    && profile_arg=" --profile \"$_agent_adapter_content_profile\""
  cat <<EOF
<!-- agent-rails:generated -->
## Agent Rails

Agent Rails Version: $_agent_adapter_content_version

This project has a local opencode adapter for Agent Rails. Treat Agent Rails as active before broad repository reads or file edits when this work touches 2+ subprojects, APIs/contracts/schemas/data models, ADRs/handbooks, migrations/refactors, or ambiguous product decisions. For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use \`--pack-mode lite\`. Pure status queries or fixed operations with no repo change and no branch-consumption risk can skip pack.

Visible session marker protocol:

- If using pack or lite, first tell the user the AGENT RAILS: ON marker printed by the pack command.
- If using check-only, first tell the user: AGENT RAILS: CHECK-ONLY (reason=<reason>).
- If intentionally skipping Agent Rails, first tell the user: AGENT RAILS: SKIPPED (reason=<reason>).

Generate the Task Pack:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg "<goal>"
\`\`\`

For lite mode:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg --pack-mode lite "<goal>"
\`\`\`

Read the generated Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist before making changes.

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

Before final delivery, print verification suggestions:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin check --project "\$project_root"$profile_arg --print-only
\`\`\`

For deploy/release/upload workflows that consume the current branch, treat that check command as Step 0.
EOF
}

_agent_adapter_content_render_command() {
  local artifact="$1"
  local description metadata profile_arg=""
  [[ -n "$_agent_adapter_content_profile" ]] \
    && profile_arg=" --profile \"$_agent_adapter_content_profile\""

  case "$_agent_adapter_content_adapter:$artifact" in
    claude:pack)
      description="Generate and read the Agent Rails Task Pack before engineering work; use --pack-mode lite for POCs and deploy prep"
      metadata="argument-hint: [goal]"
      ;;
    claude:lite)
      description="Generate and read a lite Agent Rails Task Pack for POCs, deploy prep, codegen checks, and quick continuation work"
      metadata="argument-hint: [goal]"
      ;;
    claude:check)
      description="Print Agent Rails verification suggestions for the current project"
      metadata="argument-hint: [optional check args]"
      ;;
    opencode:pack)
      description="Generate and read the Agent Rails Task Pack before engineering work; use lite mode for POCs and deploy prep."
      metadata="agent: build"
      ;;
    opencode:lite)
      description="Generate and read a lite Agent Rails Task Pack for POCs, deploy prep, codegen checks, and quick continuation work."
      metadata="agent: build"
      ;;
    opencode:check)
      description="Print Agent Rails verification suggestions for the current project."
      metadata="agent: build"
      ;;
  esac

  cat <<EOF
---
description: $description
$metadata
---

<!-- agent-rails:generated -->

Run this command:

EOF

  case "$artifact" in
    pack)
      cat <<EOF
\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg "\$ARGUMENTS"
\`\`\`

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist before making changes.
EOF
      ;;
    lite)
      cat <<EOF
\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin pack --project "\$project_root"$profile_arg --pack-mode lite "\$ARGUMENTS"
\`\`\`

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Use lite mode for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook. Skip full grill; keep only blocker questions, assumptions, deferred decisions, Memory Cards, Verification Suggestions, and Delivery Checklist.
EOF
      ;;
    check)
      cat <<EOF
\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$_agent_adapter_content_bin check --project "\$project_root"$profile_arg --print-only \$ARGUMENTS
\`\`\`

Before continuing, tell the user:

\`\`\`text
AGENT RAILS: CHECK-ONLY (reason=verification)
\`\`\`

Use the output to decide which verification commands to run before final delivery.
EOF
      ;;
  esac
}
