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
# shellcheck source=scripts/agent-target-project.sh
source "$AGENT_RAILS_HOME/scripts/agent-target-project.sh"
# shellcheck source=scripts/agent-adapter-workspace.sh
source "$AGENT_RAILS_HOME/scripts/agent-adapter-workspace.sh"
# shellcheck source=scripts/agent-adapter-content.sh
source "$AGENT_RAILS_HOME/scripts/agent-adapter-content.sh"
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

agent_target_project_resolve "$project" "$profile_path" || exit $?
agent_target_project_load_profile required || exit 2
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"
is_git_repo="$AGENT_TARGET_PROJECT_IS_GIT_REPO"
task_pack_path="$AGENT_TARGET_PROJECT_TASK_PACK_PATH"
profile_pack_mode="${AGENT_RAILS_PACK_MODE:-normal}"

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

agent_adapter_workspace_load_managed_skills
legacy_adapter=0
if [[ ! -f "$managed_skills_path" ]] && {
  agent_adapter_workspace_is_generated_file "$guide_path" \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_local_md_path" 2>/dev/null \
    || grep -Fq '<!-- agent-rails:start -->' "$claude_project_md_path" 2>/dev/null
}; then
  legacy_adapter=1
fi
protect_tracked=0
[[ "$install_mode" == "local" ]] && protect_tracked=1
agent_adapter_workspace_configure \
  "$project_abs" \
  ".claude/skills" \
  "$dry_run" \
  "$force" \
  "$protect_tracked" \
  "$legacy_adapter"

write_claude_md_block() {
  local claude_md_path="$claude_rules_path"
  local claude_md_name
  claude_md_name="$(basename "$claude_md_path")"

  if [[ -f "$claude_md_path" ]] && grep -q '<!-- agent-rails:start -->' "$claude_md_path"; then
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

agent_adapter_content_init claude "$AGENT_RAILS_VERSION" "$AGENT_RAILS_BIN" "$profile_path"
guide_content="$(agent_adapter_content_render guide)"
pack_command_content="$(agent_adapter_content_render pack)"
lite_command_content="$(agent_adapter_content_render lite)"
check_command_content="$(agent_adapter_content_render check)"

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

agent_adapter_workspace_install_skills
agent_adapter_workspace_write_managed_skills
agent_adapter_workspace_write_generated_file "$guide_path" "$guide_content"
agent_adapter_workspace_write_generated_file "$pack_command_path" "$pack_command_content"
agent_adapter_workspace_write_generated_file "$lite_command_path" "$lite_command_content"
agent_adapter_workspace_write_generated_file "$check_command_path" "$check_command_content"
write_claude_md_block

if [[ "$global_reminder" -eq 1 ]]; then
  write_global_reminder_block
fi

if [[ "$session_hook" -eq 1 ]]; then
  write_session_hook_settings
fi

if [[ "$install_mode" == "local" ]]; then
  agent_adapter_workspace_ensure_ignore_block \
    "$local_ignore_path" \
    "# Agent Rails local adapter" \
    "# Agent Rails local adapter end" \
    ".claude/AGENT_RAILS.md" \
    ".claude/.agent-rails-managed-skills" \
    ".claude/commands/agent-rails-pack.md" \
    ".claude/commands/agent-rails-lite.md" \
    ".claude/commands/agent-rails-check.md" \
    ".claude/skills/agent-*/" \
    ".agent-rails/" \
    "CLAUDE.local.md" \
    --cleanup-only \
    ".claude/" \
    "CLAUDE.md"
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
