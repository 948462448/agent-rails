#!/usr/bin/env bash
# Remove Agent Rails Claude Code adapter files from a target project.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails claude uninstall [--project PATH] [--global-reminder] [--session-hook] [--dry-run]

Removes only Agent Rails generated Claude adapter files:
  .claude/AGENT_RAILS.md
  .claude/commands/agent-rails-pack.md
  .claude/commands/agent-rails-lite.md
  .claude/commands/agent-rails-check.md
  .claude/skills/<Agent Rails skill names>
  the marked Agent Rails block in CLAUDE.local.md
  the marked Agent Rails block in CLAUDE.md for project mode or legacy local installs
  the marked Agent Rails global reminder in ~/.claude/CLAUDE.md when --global-reminder is passed
  the personal Claude Code SessionStart hook in ~/.claude/settings.json when --session-hook is passed
  the marked local ignore block when present
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-adapter-workspace.sh
source "$AGENT_RAILS_HOME/scripts/agent-adapter-workspace.sh"

project="$PWD"
dry_run=0
global_reminder=0
session_hook=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
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
claude_dir="$project_abs/.claude"
skills_dir="$claude_dir/skills"
commands_dir="$claude_dir/commands"
guide_path="$claude_dir/AGENT_RAILS.md"
pack_command_path="$commands_dir/agent-rails-pack.md"
lite_command_path="$commands_dir/agent-rails-lite.md"
check_command_path="$commands_dir/agent-rails-check.md"
managed_skills_path="$claude_dir/.agent-rails-managed-skills"
agent_adapter_workspace_init \
  "$guide_path" \
  "$pack_command_path" \
  "$lite_command_path" \
  "$check_command_path" \
  "$managed_skills_path"
claude_project_md_path="$project_abs/CLAUDE.md"
claude_local_md_path="$project_abs/CLAUDE.local.md"
claude_user_md_path="${AGENT_RAILS_CLAUDE_USER_MD:-$HOME/.claude/CLAUDE.md}"
claude_settings_path="${AGENT_RAILS_CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
session_hook_path="$AGENT_RAILS_HOME/hooks/agent-rails-session-start.sh"
session_hook_settings_script="$AGENT_RAILS_HOME/scripts/agent-claude-session-hook-settings.py"

agent_adapter_workspace_load_managed_skills
legacy_adapter=0
if [[ ! -f "$managed_skills_path" ]] && {
  agent_adapter_workspace_is_generated_file "$guide_path" \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_local_md_path" 2>/dev/null \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_project_md_path" 2>/dev/null
}; then
  legacy_adapter=1
fi
agent_adapter_workspace_configure \
  "$project_abs" \
  ".claude/skills" \
  "$dry_run" \
  0 \
  0 \
  "$legacy_adapter"

remove_agent_rails_block() {
  local claude_md_path="$1"
  [[ -f "$claude_md_path" ]] || return 0
  if ! grep -q '<!-- agent-rails:start -->' "$claude_md_path"; then
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove Agent Rails block from %s\n' "$claude_md_path"
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  awk '
    /^<!-- agent-rails:start -->$/ {
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

  if grep -q '[^[:space:]]' "$tmp_file"; then
    mv "$tmp_file" "$claude_md_path"
    printf 'Removed Agent Rails block from %s\n' "$claude_md_path"
  else
    rm -f "$tmp_file" "$claude_md_path"
    printf 'Removed empty %s\n' "$claude_md_path"
  fi
}

remove_global_reminder_block() {
  local claude_md_path="$claude_user_md_path"
  local marker='<!-- agent-rails:global-reminder:start -->'
  local end_marker='<!-- agent-rails:global-reminder:end -->'

  [[ -f "$claude_md_path" ]] || return 0
  if ! grep -q "$marker" "$claude_md_path"; then
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove global Agent Rails reminder from %s\n' "$claude_md_path"
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  awk -v marker="$marker" -v end_marker="$end_marker" '
    $0 == marker {
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

  if grep -q '[^[:space:]]' "$tmp_file"; then
    mv "$tmp_file" "$claude_md_path"
    printf 'Removed global Agent Rails reminder from %s\n' "$claude_md_path"
  else
    rm -f "$tmp_file" "$claude_md_path"
    printf 'Removed empty %s\n' "$claude_md_path"
  fi
}

remove_session_hook_settings() {
  if [[ ! -f "$session_hook_settings_script" ]]; then
    printf 'Session hook settings helper is missing: %s\n' "$session_hook_settings_script" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required to update Claude settings for --session-hook.\n' >&2
    exit 1
  fi

  local args=(uninstall --settings "$claude_settings_path" --hook "$session_hook_path")
  [[ "$dry_run" -eq 1 ]] && args+=(--dry-run)
  python3 -E "$session_hook_settings_script" "${args[@]}"
}

local_ignore_paths() {
  if command -v git >/dev/null 2>&1 && git -C "$project_abs" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    git_ignore_path="$(git -C "$project_abs" rev-parse --git-path info/exclude)"
    case "$git_ignore_path" in
      /*) printf '%s\n' "$git_ignore_path" ;;
      *) printf '%s\n' "$project_abs/$git_ignore_path" ;;
    esac
  fi
  printf '%s\n' "$project_abs/.gitignore"
}

agent_adapter_workspace_remove_generated_file "$guide_path"
agent_adapter_workspace_remove_generated_file "$pack_command_path"
agent_adapter_workspace_remove_generated_file "$lite_command_path"
agent_adapter_workspace_remove_generated_file "$check_command_path"
agent_adapter_workspace_remove_managed_skills
agent_adapter_workspace_remove_managed_skills_file
remove_agent_rails_block "$claude_local_md_path"
remove_agent_rails_block "$claude_project_md_path"
if [[ "$global_reminder" -eq 1 ]]; then
  remove_global_reminder_block
fi
if [[ "$session_hook" -eq 1 ]]; then
  remove_session_hook_settings
fi

while IFS= read -r ignore_path; do
  [[ -n "$ignore_path" ]] || continue
  agent_adapter_workspace_remove_ignore_block \
    "$ignore_path" \
    "# Agent Rails local adapter" \
    "# Agent Rails local adapter end" \
    "Would remove Agent Rails local ignore block from" \
    "Removed Agent Rails local ignore block from" \
    ".claude/" \
    ".claude/AGENT_RAILS.md" \
    ".claude/.agent-rails-managed-skills" \
    ".claude/commands/agent-rails-pack.md" \
    ".claude/commands/agent-rails-lite.md" \
    ".claude/commands/agent-rails-check.md" \
    ".claude/skills/agent-*/" \
    ".agent-rails/" \
    "CLAUDE.md" \
    "CLAUDE.local.md"
done < <(local_ignore_paths | awk 'NF' | sort -u)

if [[ "$dry_run" -ne 1 ]]; then
  rmdir "$commands_dir" "$skills_dir" "$claude_dir" 2>/dev/null || true
fi

printf '\nClaude adapter removed.\n'
printf 'Project: %s\n' "$project_abs"
if [[ "$global_reminder" -eq 1 ]]; then
  printf 'Global Reminder: %s\n' "$claude_user_md_path"
fi
if [[ "$session_hook" -eq 1 ]]; then
  printf 'Session Hook: %s\n' "$claude_settings_path"
fi
