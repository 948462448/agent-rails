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
# shellcheck source=scripts/agent-adapter-lifecycle.sh
source "$AGENT_RAILS_HOME/scripts/agent-adapter-lifecycle.sh"

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
agent_adapter_lifecycle_init \
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

say_remove() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove %s\n' "$1"
  else
    printf 'Removed %s\n' "$1"
  fi
}

agent_adapter_load_managed_skills
legacy_adapter=0
if [[ ! -f "$managed_skills_path" ]] && {
  agent_adapter_is_generated_file "$guide_path" \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_local_md_path" 2>/dev/null \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_project_md_path" 2>/dev/null
}; then
  legacy_adapter=1
fi

remove_path() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  if [[ "$dry_run" -eq 1 ]]; then
    say_remove "$path"
    return 0
  fi
  rm -rf "$path"
  say_remove "$path"
}

remove_generated_file() {
  local path="$1"
  [[ -e "$path" ]] || return 0
  if ! agent_adapter_is_generated_file "$path"; then
    printf 'Keeping unmanaged existing file: %s\n' "$path"
    return 0
  fi
  remove_path "$path"
}

remove_agent_rails_skills() {
  local source_skills_dir="$AGENT_RAILS_HOME/skills"
  local index skill_dir skill_name
  local skills_to_remove=()
  local skills_to_remove_count=0
  [[ -d "$skills_dir" ]] || return 0

  if [[ -f "$managed_skills_path" ]]; then
    while IFS= read -r skill_name; do
      skills_to_remove+=("$skill_name")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(agent_adapter_list_managed_skills)
  elif [[ "$legacy_adapter" -eq 1 && -d "$source_skills_dir" ]]; then
    while IFS= read -r skill_dir; do
      skills_to_remove+=("$(basename "$skill_dir")")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(find "$source_skills_dir" -mindepth 1 -maxdepth 1 -type d | sort)
  fi

  for ((index = 0; index < skills_to_remove_count; index++)); do
    skill_name="${skills_to_remove[$index]}"
    agent_adapter_is_valid_managed_skill_name "$skill_name" || continue
    remove_path "$skills_dir/$skill_name"
  done
}

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
  python3 "$session_hook_settings_script" "${args[@]}"
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

remove_local_ignore_block() {
  local path="$1"
  [[ -f "$path" ]] || return 0
  if ! grep -Fxq '# Agent Rails local adapter' "$path"; then
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove Agent Rails local ignore block from %s\n' "$path"
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  awk '
    function is_agent_rails_ignore_line(line) {
      return line == ".claude/" ||
        line == ".claude/AGENT_RAILS.md" ||
        line == ".claude/.agent-rails-managed-skills" ||
        line == ".claude/commands/agent-rails-pack.md" ||
        line == ".claude/commands/agent-rails-lite.md" ||
          line == ".claude/commands/agent-rails-check.md" ||
          line == ".claude/skills/agent-*/" ||
          line == ".agent-rails/" ||
          line == "CLAUDE.md" ||
          line == "CLAUDE.local.md"
    }
    $0 == "# Agent Rails local adapter" {
      in_agent_rails_ignore = 1
      next
    }
    in_agent_rails_ignore && $0 == "# Agent Rails local adapter end" {
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
  ' "$path" > "$tmp_file"
  mv "$tmp_file" "$path"
  printf 'Removed Agent Rails local ignore block from %s\n' "$path"
}

remove_generated_file "$guide_path"
remove_generated_file "$pack_command_path"
remove_generated_file "$lite_command_path"
remove_generated_file "$check_command_path"
remove_agent_rails_skills
remove_path "$managed_skills_path"
remove_agent_rails_block "$claude_local_md_path"
remove_agent_rails_block "$claude_project_md_path"
if [[ "$global_reminder" -eq 1 ]]; then
  remove_global_reminder_block
fi
if [[ "$session_hook" -eq 1 ]]; then
  remove_session_hook_settings
fi

while IFS= read -r ignore_path; do
  [[ -n "$ignore_path" ]] && remove_local_ignore_block "$ignore_path"
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
