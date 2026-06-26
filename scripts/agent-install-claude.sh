#!/usr/bin/env bash
# Install Agent Rails adapters for Claude Code.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails claude install [--project PATH] [--profile PATH] [--mode local|project] [--global-reminder] [--session-hook] [--dry-run] [--force]

Examples:
  agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local
  agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local --global-reminder
  agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local --session-hook
  agent-rails claude install --project /path/to/project --profile /path/to/profile --mode project
  agent-rails claude install --project /path/to/project --profile /path/to/profile --write-claude-md

Modes:
  local   Write .claude/ and CLAUDE.local.md locally, then ignore them via .git/info/exclude.
  project Write .claude/ and CLAUDE.md as project files that may be committed.

--write-claude-md is kept as an alias for --mode project.
--global-reminder writes a short personal reminder to ~/.claude/CLAUDE.md so Claude sees the Agent Rails hook before project-local context.
--session-hook writes a personal Claude Code SessionStart hook to ~/.claude/settings.json so Agent Rails is injected as startup context for projects that already have an Agent Rails adapter marker.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths
AGENT_RAILS_VERSION="$(agent_rails_version)"

project="$PWD"
profile_path=""
dry_run=0
force=0
install_mode="local"
global_reminder=0
session_hook=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --force)
      force=1
      shift
      ;;
    --global-reminder)
      global_reminder=1
      shift
      ;;
    --session-hook)
      session_hook=1
      shift
      ;;
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        local|project)
          install_mode="$2"
          ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --local)
      install_mode="local"
      shift
      ;;
    --write-claude-md)
      install_mode="project"
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "$project" ]]; then
  printf 'Project directory not found: %s\n' "$project" >&2
  exit 2
fi

project_abs="$(cd "$project" && pwd)"
if git_root_for_project="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
  project_abs="$(cd "$git_root_for_project" && pwd)"
fi
project_name="$(basename "$project_abs")"
PROJECT_ROOT="$project_abs"
PROJECT_NAME="${PROJECT_NAME:-$project_name}"
PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
PROJECT_WORKTREE_SLUG="${PROJECT_WORKTREE_SLUG:-$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")}"

is_git_repo=0
if command -v git >/dev/null 2>&1 && git -C "$project_abs" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  is_git_repo=1
fi

profile_path="$(agent_rails_resolve_profile "$project_abs" "$project_name" "$profile_path")"

if [[ ! -f "$profile_path" ]]; then
  printf 'Profile not found: %s\n' "$profile_path" >&2
  exit 2
fi

# shellcheck source=/dev/null
source "$profile_path"
PROJECT_NAME="${PROJECT_NAME:-$project_name}"
if [[ -n "$PROJECT_WORKTREE_SLUG_PRESET" ]]; then
  PROJECT_WORKTREE_SLUG="$PROJECT_WORKTREE_SLUG_PRESET"
else
  PROJECT_WORKTREE_SLUG="$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")"
fi
task_pack_path="${TASK_PACK_PATH:-$(agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG")}"
profile_pack_mode="${AGENT_RAILS_PACK_MODE:-normal}"

claude_dir="$project_abs/.claude"
skills_dir="$claude_dir/skills"
commands_dir="$claude_dir/commands"
guide_path="$claude_dir/AGENT_RAILS.md"
pack_command_path="$commands_dir/agent-rails-pack.md"
lite_command_path="$commands_dir/agent-rails-lite.md"
check_command_path="$commands_dir/agent-rails-check.md"
claude_project_md_path="$project_abs/CLAUDE.md"
claude_local_md_path="$project_abs/CLAUDE.local.md"
claude_user_md_path="${AGENT_RAILS_CLAUDE_USER_MD:-$HOME/.claude/CLAUDE.md}"
claude_settings_path="${AGENT_RAILS_CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
session_hook_path="$AGENT_RAILS_HOME/hooks/agent-rails-session-start.sh"
session_hook_settings_script="$AGENT_RAILS_HOME/scripts/agent-claude-session-hook-settings.py"
if [[ "$install_mode" == "local" ]]; then
  claude_rules_path="$claude_local_md_path"
else
  claude_rules_path="$claude_project_md_path"
fi
local_ignore_path="$project_abs/.gitignore"
if [[ "$is_git_repo" -eq 1 ]]; then
  git_ignore_path="$(git -C "$project_abs" rev-parse --git-path info/exclude)"
  case "$git_ignore_path" in
    /*)
      local_ignore_path="$git_ignore_path"
      ;;
    *)
      local_ignore_path="$project_abs/$git_ignore_path"
      ;;
  esac
fi

say_write() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would write %s\n' "$1"
  else
    printf 'Wrote %s\n' "$1"
  fi
}

write_file() {
  local path="$1"
  local content="$2"

  if [[ "$install_mode" == "local" && "$force" -ne 1 ]] && is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi

  if [[ -e "$path" && "$force" -ne 1 ]]; then
    printf 'Keeping existing %s (use --force to overwrite).\n' "$path"
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    say_write "$path"
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  say_write "$path"
}

append_local_ignore() {
  local marker="# Agent Rails local adapter"
  local end_marker="# Agent Rails local adapter end"

  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would ensure local ignore entries in %s\n' "$local_ignore_path"
    printf '  .claude/AGENT_RAILS.md\n'
    printf '  .claude/commands/agent-rails-pack.md\n'
    printf '  .claude/commands/agent-rails-lite.md\n'
    printf '  .claude/commands/agent-rails-check.md\n'
    printf '  .claude/skills/agent-*/\n'
    printf '  .agent-rails/\n'
    printf '  CLAUDE.local.md\n'
    return 0
  fi

  mkdir -p "$(dirname "$local_ignore_path")"
  if [[ -f "$local_ignore_path" ]] && grep -Fxq "$marker" "$local_ignore_path"; then
    local tmp_file
    tmp_file="$(mktemp)"
    awk -v marker="$marker" -v end_marker="$end_marker" '
      function is_agent_rails_ignore_line(line) {
        return line == ".claude/" ||
          line == ".claude/AGENT_RAILS.md" ||
          line == ".claude/commands/agent-rails-pack.md" ||
          line == ".claude/commands/agent-rails-lite.md" ||
          line == ".claude/commands/agent-rails-check.md" ||
          line == ".claude/skills/agent-*/" ||
          line == ".agent-rails/" ||
          line == "CLAUDE.md" ||
          line == "CLAUDE.local.md"
      }
      $0 == marker {
        in_agent_rails_ignore = 1
        next
      }
      in_agent_rails_ignore && $0 == end_marker {
        in_agent_rails_ignore = 0
        next
      }
      in_agent_rails_ignore && is_agent_rails_ignore_line($0) {
        next
      }
      in_agent_rails_ignore {
        in_agent_rails_ignore = 0
      }
      { print }
    ' "$local_ignore_path" > "$tmp_file"
    mv "$tmp_file" "$local_ignore_path"
  fi

  if ! {
    [[ -s "$local_ignore_path" ]] && printf '\n'
    printf '%s\n' "$marker"
    printf '.claude/AGENT_RAILS.md\n'
    printf '.claude/commands/agent-rails-pack.md\n'
    printf '.claude/commands/agent-rails-lite.md\n'
    printf '.claude/commands/agent-rails-check.md\n'
    printf '.claude/skills/agent-*/\n'
    printf '.agent-rails/\n'
    printf 'CLAUDE.local.md\n'
    printf '%s\n' "$end_marker"
  } >> "$local_ignore_path"; then
    printf 'Failed to update local ignore file: %s\n' "$local_ignore_path" >&2
    exit 1
  fi
  printf 'Updated local ignore file: %s\n' "$local_ignore_path"
}

tracked_agent_rails_files() {
  if [[ "$is_git_repo" -ne 1 ]]; then
    return 0
  fi
  if [[ "$install_mode" == "local" ]]; then
    {
      git -C "$project_abs" ls-files \
        CLAUDE.local.md \
        .claude/AGENT_RAILS.md \
        .claude/commands/agent-rails-pack.md \
        .claude/commands/agent-rails-lite.md \
        .claude/commands/agent-rails-check.md \
        2>/dev/null || true
      if [[ -d "$AGENT_RAILS_HOME/skills" ]]; then
        while IFS= read -r skill_dir; do
          git -C "$project_abs" ls-files ".claude/skills/$(basename "$skill_dir")" 2>/dev/null || true
        done < <(find "$AGENT_RAILS_HOME/skills" -mindepth 1 -maxdepth 1 -type d | sort)
      fi
    } | awk 'NF' | sort -u
  else
    git -C "$project_abs" ls-files CLAUDE.md .claude 2>/dev/null || true
  fi
}

is_tracked_file() {
  local path="$1"
  local rel_path
  [[ "$is_git_repo" -eq 1 ]] || return 1
  case "$path" in
    "$project_abs"/*) rel_path="${path#$project_abs/}" ;;
    *) return 1 ;;
  esac
  git -C "$project_abs" ls-files -- "$rel_path" 2>/dev/null | grep -Fxq "$rel_path"
}

is_tracked_prefix() {
  local rel_path="$1"
  [[ "$is_git_repo" -eq 1 ]] || return 1
  [[ -n "$(git -C "$project_abs" ls-files -- "$rel_path" 2>/dev/null | sed -n '1p')" ]]
}

install_skills() {
  local args=(--dest "$skills_dir")
  local selected_skills=()
  local skill_dir skill_name
  [[ "$dry_run" -eq 1 ]] && args+=(--dry-run)

  if [[ "$install_mode" == "local" && -d "$AGENT_RAILS_HOME/skills" ]]; then
    while IFS= read -r skill_dir; do
      skill_name="$(basename "$skill_dir")"
      if [[ "$force" -ne 1 ]] && is_tracked_prefix ".claude/skills/$skill_name"; then
        printf 'Keeping tracked skill directory in local mode: %s\n' "$project_abs/.claude/skills/$skill_name"
      else
        selected_skills+=("$skill_name")
      fi
    done < <(find "$AGENT_RAILS_HOME/skills" -mindepth 1 -maxdepth 1 -type d | sort)

    if [[ "${#selected_skills[@]}" -eq 0 ]]; then
      printf 'No untracked Agent Rails skills to install.\n'
      return 0
    fi
    args+=("${selected_skills[@]}")
  fi

  "$AGENT_RAILS_HOME/scripts/agent-install-skills.sh" "${args[@]}"
}

write_claude_md_block() {
  local claude_md_path="$claude_rules_path"
  local claude_md_name
  claude_md_name="$(basename "$claude_md_path")"

  if [[ -f "$claude_md_path" ]] && grep -q '<!-- agent-rails:start -->' "$claude_md_path"; then
    if [[ "$force" -ne 1 ]]; then
      printf '%s already contains an Agent Rails block: %s\n' "$claude_md_name" "$claude_md_path"
      printf 'Use --force to replace it.\n'
      return 0
    fi

    if [[ "$dry_run" -eq 1 ]]; then
      printf 'Would replace Agent Rails block in %s\n' "$claude_md_path"
      return 0
    fi

    local block_file
    local tmp_file
    block_file="$(mktemp)"
    tmp_file="$(mktemp)"
    printf '%s\n' "$claude_block" > "$block_file"
    awk -v block_file="$block_file" '
      function print_block(line) {
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
      }
      /^<!-- agent-rails:start -->$/ {
        if (!replaced) {
          print_block()
          replaced = 1
        }
        in_block = 1
        next
      }
      /^<!-- agent-rails:end -->$/ && in_block {
        in_block = 0
        next
      }
      !in_block {
        print
      }
    ' "$claude_md_path" > "$tmp_file"
    rm -f "$block_file"
    mv "$tmp_file" "$claude_md_path"
    printf 'Replaced Agent Rails block in %s\n' "$claude_md_path"
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would append Agent Rails block to %s\n' "$claude_md_path"
    return 0
  fi

  {
    [[ -f "$claude_md_path" ]] && printf '\n'
    printf '%s\n' "$claude_block"
  } >> "$claude_md_path"
  printf 'Appended Agent Rails block to %s\n' "$claude_md_path"
}

write_global_reminder_block() {
  local claude_md_path="$claude_user_md_path"
  local marker='<!-- agent-rails:global-reminder:start -->'
  local end_marker='<!-- agent-rails:global-reminder:end -->'

  if [[ -f "$claude_md_path" ]] && grep -q "$marker" "$claude_md_path"; then
    if [[ "$force" -ne 1 ]]; then
      printf 'Global Agent Rails reminder already exists: %s\n' "$claude_md_path"
      printf 'Use --force to replace it.\n'
      return 0
    fi

    if [[ "$dry_run" -eq 1 ]]; then
      printf 'Would replace global Agent Rails reminder in %s\n' "$claude_md_path"
      return 0
    fi

    local block_file
    local tmp_file
    block_file="$(mktemp)"
    tmp_file="$(mktemp)"
    printf '%s\n' "$global_reminder_block" > "$block_file"
    awk -v block_file="$block_file" -v marker="$marker" -v end_marker="$end_marker" '
      function print_block(line) {
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
      }
      $0 == marker {
        if (!replaced) {
          print_block()
          replaced = 1
        }
        in_block = 1
        next
      }
      $0 == end_marker && in_block {
        in_block = 0
        next
      }
      !in_block {
        print
      }
    ' "$claude_md_path" > "$tmp_file"
    rm -f "$block_file"
    mv "$tmp_file" "$claude_md_path"
    printf 'Replaced global Agent Rails reminder in %s\n' "$claude_md_path"
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would append global Agent Rails reminder to %s\n' "$claude_md_path"
    return 0
  fi

  mkdir -p "$(dirname "$claude_md_path")"
  {
    [[ -f "$claude_md_path" ]] && printf '\n'
    printf '%s\n' "$global_reminder_block"
  } >> "$claude_md_path"
  printf 'Appended global Agent Rails reminder to %s\n' "$claude_md_path"
}

write_session_hook_settings() {
  if [[ ! -x "$session_hook_path" ]]; then
    printf 'Session hook script is missing or not executable: %s\n' "$session_hook_path" >&2
    exit 1
  fi
  if [[ ! -f "$session_hook_settings_script" ]]; then
    printf 'Session hook settings helper is missing: %s\n' "$session_hook_settings_script" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required to update Claude settings for --session-hook.\n' >&2
    exit 1
  fi

  local args=(install --settings "$claude_settings_path" --hook "$session_hook_path")
  [[ "$dry_run" -eq 1 ]] && args+=(--dry-run)
  python3 "$session_hook_settings_script" "${args[@]}"
}

guide_content="$(cat <<EOF
# Agent Rails

This project is configured to use Agent Rails for context orchestration.

Agent Rails Version: $AGENT_RAILS_VERSION

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
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" "<goal>"
\`\`\`

For lite POC/deploy-prep work:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" --pack-mode lite "<goal>"
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

Use \`project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; $AGENT_RAILS_BIN check --project "\$project_root" --profile "$profile_path" --print-only\` before final delivery, and as Step 0 for deploy/release/upload workflows that consume this branch.

After delivery, use \`agent-memory-curator\` to decide whether this task produced reusable memory. If not, record a skip reason:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN memory suggest --project "\$project_root" --profile "$profile_path" --decision skip --reason "<why no durable memory>"
\`\`\`

If the lesson is durable, write one small local card:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN memory suggest --project "\$project_root" --profile "$profile_path" --decision keep --write-local --title "<short title>" --trigger "<trigger>" --applies-to "<scope>" --verify "<check>" --caution "<scope limits>" "<brief reusable lesson>"
\`\`\`

Do not write OpenMemory from this kit. Online memory is a read provider unless a separate integration is explicitly added.
EOF
)"

pack_command_content="$(cat <<EOF
---
description: Generate and read the Agent Rails Task Pack before engineering work; use --pack-mode lite for POCs and deploy prep
argument-hint: [goal]
---

Run this command:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" "\$ARGUMENTS"
\`\`\`

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist before making changes.
EOF
)"

lite_command_content="$(cat <<EOF
---
description: Generate and read a lite Agent Rails Task Pack for POCs, deploy prep, codegen checks, and quick continuation work
argument-hint: [goal]
---

Run this command:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" --pack-mode lite "\$ARGUMENTS"
\`\`\`

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Use lite mode for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook. Skip full grill; keep only blocker questions, assumptions, deferred decisions, Memory Cards, Verification Suggestions, and Delivery Checklist.
EOF
)"

check_command_content="$(cat <<EOF
---
description: Print Agent Rails verification suggestions for the current project
argument-hint: [optional check args]
---

Run this command:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN check --project "\$project_root" --profile "$profile_path" --print-only \$ARGUMENTS
\`\`\`

Before continuing, tell the user:

\`\`\`text
AGENT RAILS: CHECK-ONLY (reason=verification)
\`\`\`

Use the output to decide which verification commands to run before final delivery.
EOF
)"

claude_block="$(cat <<EOF
<!-- agent-rails:start -->
## Agent Rails

Agent Rails Version: $AGENT_RAILS_VERSION

Use Agent Rails before reading broad context or editing files when this work touches 2+ subprojects, APIs/contracts/schemas/data models, ADRs/handbooks, migrations/refactors, or ambiguous product decisions. For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use \`--pack-mode lite\`. Pure status queries or fixed operations with no repo change and no branch-consumption risk can skip pack.

Visible session marker protocol:

- If using pack or lite, first tell the user the AGENT RAILS: ON marker printed by the pack command.
- If using check-only, first tell the user: AGENT RAILS: CHECK-ONLY (reason=<reason>).
- If intentionally skipping Agent Rails, first tell the user: AGENT RAILS: SKIPPED (reason=<reason>).

1. Generate the Task Pack:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" "<goal>"
\`\`\`

   For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile_path" --pack-mode lite "<goal>"
\`\`\`

2. Read the generated Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

3. Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist.

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

When delegating to a subagent, require the subagent to return the Subagent Result Contract from the Task Pack.

Before final delivery, print verification suggestions:

\`\`\`bash
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN check --project "\$project_root" --profile "$profile_path" --print-only
\`\`\`

For deploy/release/upload workflows that consume the current branch, treat that check command as Step 0.
<!-- agent-rails:end -->
EOF
)"

global_reminder_block="$(cat <<'EOF'
<!-- agent-rails:global-reminder:start -->
## Agent Rails

When a repository contains a local Agent Rails adapter (`CLAUDE.local.md` with `agent-rails:start` or `.claude/AGENT_RAILS.md`), treat it as mandatory for substantial engineering work and useful in lite mode for POCs or deploy prep.

If neither marker exists in the current repository, ignore this Agent Rails reminder and follow normal project instructions.

Visible session marker protocol:

- If using pack or lite, first tell the user: `AGENT RAILS: ON (mode=<mode>, pack=<task-pack-path>)`.
- If using check-only, first tell the user: `AGENT RAILS: CHECK-ONLY (reason=<reason>)`.
- If intentionally skipping Agent Rails, first tell the user: `AGENT RAILS: SKIPPED (reason=<reason>)`.

Before broad context reads or edits, run `/agent-rails-pack <goal>` if available; otherwise read the local Agent Rails block and run its `agent-rails pack` command. Use `/agent-rails-lite <goal>` or `--pack-mode lite` for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, or continuation from an existing handbook. Read the generated Task Pack and follow its contract, memory cards, verification suggestions, subagent result contract, and delivery checklist.

Before final delivery, and as Step 0 for deploy/release/upload workflows that consume the current branch, run `/agent-rails-check` if available; otherwise run the local Agent Rails check command.
<!-- agent-rails:global-reminder:end -->
EOF
)"

tracked_files="$(tracked_agent_rails_files)"
if [[ "$install_mode" == "local" && -n "$tracked_files" ]]; then
  printf 'Warning: existing tracked Agent Rails target paths will stay tracked and local ignore will not hide them:\n%s\n' "$tracked_files" >&2
  if [[ "$force" -ne 1 ]]; then
    printf 'Keeping tracked target paths unchanged in local mode. Newly installed Agent Rails files will be ignored locally.\n' >&2
  fi
fi

install_skills
write_file "$guide_path" "$guide_content"
write_file "$pack_command_path" "$pack_command_content"
write_file "$lite_command_path" "$lite_command_content"
write_file "$check_command_path" "$check_command_content"
write_claude_md_block

if [[ "$global_reminder" -eq 1 ]]; then
  write_global_reminder_block
fi

if [[ "$session_hook" -eq 1 ]]; then
  write_session_hook_settings
fi

if [[ "$install_mode" == "local" ]]; then
  append_local_ignore
fi

printf '\nClaude adapter ready.\n'
printf 'Mode: %s\n' "$install_mode"
printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
printf 'Project: %s\n' "$project_abs"
printf 'Profile: %s\n' "$profile_path"
printf 'Task Pack: %s\n' "$task_pack_path"
if [[ "$global_reminder" -eq 1 ]]; then
  printf 'Global Reminder: %s\n' "$claude_user_md_path"
fi
if [[ "$session_hook" -eq 1 ]]; then
  printf 'Session Hook: %s\n' "$claude_settings_path"
fi
