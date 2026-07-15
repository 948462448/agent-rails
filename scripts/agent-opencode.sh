#!/usr/bin/env bash
# Install, inspect, or remove the local Agent Rails opencode adapter.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails opencode install [--project PATH] [--profile PATH] [--dry-run] [--force]
       agent-rails opencode doctor [--project PATH]
       agent-rails opencode uninstall [--project PATH] [--dry-run] [--force]

opencode install writes a project-local .opencode/plugins/ adapter and ignores
it locally in git repositories. It does not modify ~/.config/opencode.
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

subcommand="${1:-}"
[[ -n "$subcommand" ]] || { usage >&2; exit 2; }
shift || true

project="$PWD"
profile_path=""
dry_run=0
force=0

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

opencode_dir="$project_abs/.opencode"
skills_dir="$opencode_dir/skills"
commands_dir="$opencode_dir/command"
plugins_dir="$opencode_dir/plugins"
guide_path="$opencode_dir/AGENT_RAILS.md"
plugin_path="$plugins_dir/agent-rails.mjs"
pack_command_path="$commands_dir/agent-rails-pack.md"
lite_command_path="$commands_dir/agent-rails-lite.md"
check_command_path="$commands_dir/agent-rails-check.md"
opencode_config_path="$opencode_dir/opencode.json"
opencode_instruction_path="$guide_path"
managed_skills_path="$opencode_dir/.agent-rails-managed-skills"
agent_adapter_workspace_init \
  "$guide_path" \
  "$pack_command_path" \
  "$lite_command_path" \
  "$check_command_path" \
  "$managed_skills_path"

local_ignore_path="$project_abs/.gitignore"
if [[ "$is_git_repo" -eq 1 ]]; then
  git_ignore_path="$(git -C "$project_abs" rev-parse --git-path info/exclude)"
  case "$git_ignore_path" in
    /*) local_ignore_path="$git_ignore_path" ;;
    *) local_ignore_path="$project_abs/$git_ignore_path" ;;
  esac
fi

agent_adapter_workspace_load_managed_skills
legacy_adapter=0
if [[ ! -f "$managed_skills_path" ]] && agent_adapter_workspace_is_generated_file "$guide_path"; then
  legacy_adapter=1
fi
agent_adapter_workspace_configure \
  "$project_abs" \
  ".opencode/skills" \
  "$dry_run" \
  "$force" \
  1 \
  "$legacy_adapter"

require_python_for_config() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required to update opencode config.\n' >&2
    exit 127
  fi
}

remove_legacy_opencode_config_instruction() {
  [[ -f "$opencode_config_path" ]] || return 0
  if [[ "$force" -ne 1 ]] && agent_adapter_workspace_is_tracked_file "$opencode_config_path"; then
    printf 'Keeping tracked opencode config in local mode: %s\n' "$opencode_config_path"
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove legacy Agent Rails instructions from %s\n' "$opencode_config_path"
    return 0
  fi

  require_python_for_config
  python3 - "$opencode_config_path" "$opencode_instruction_path" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
instruction_path = sys.argv[2]

try:
    data = json.loads(config_path.read_text())
except Exception as exc:
    raise SystemExit(f"Failed to parse {config_path}: {exc}")
if not isinstance(data, dict):
    raise SystemExit(f"{config_path} must contain a JSON object.")

instructions = data.get("instructions")
if isinstance(instructions, list):
    data["instructions"] = [item for item in instructions if item != instruction_path]
    if not data["instructions"]:
        data.pop("instructions", None)

schema_only = set(data.keys()) <= {"$schema"}
if schema_only:
    config_path.unlink()
else:
    config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
  if [[ -f "$opencode_config_path" ]]; then
    printf 'Updated %s\n' "$opencode_config_path"
  else
    printf 'Removed empty %s\n' "$opencode_config_path"
  fi
}

print_status() {
  printf 'Project: %s\n' "$project_abs"
  if command -v opencode >/dev/null 2>&1; then
    printf '[OK] opencode CLI: %s\n' "$(command -v opencode)"
    opencode --version 2>/dev/null | sed 's/^/Version: /' || true
  else
    printf '[WARN] opencode CLI not found.\n'
  fi

  if [[ -f "$plugin_path" ]] \
    && grep -Fq 'experimental.chat.system.transform' "$plugin_path" \
    && grep -Fq 'AGENT_RAILS_CONTEXT_MAX_CHARS = 1200' "$plugin_path"; then
    printf '[OK] opencode Agent Rails plugin: %s\n' "$plugin_path"
  else
    printf '[WARN] opencode Agent Rails plugin is missing or invalid: %s\n' "$plugin_path"
  fi

  if [[ -f "$guide_path" ]] && grep -Fq 'Visible session marker protocol' "$guide_path"; then
    printf '[OK] opencode Agent Rails guide: %s\n' "$guide_path"
  else
    printf '[WARN] opencode Agent Rails guide is missing: %s\n' "$guide_path"
  fi

  if [[ -f "$opencode_config_path" ]] && grep -Fq "$opencode_instruction_path" "$opencode_config_path"; then
    printf '[WARN] legacy opencode instructions still load the long Agent Rails guide: %s\n' "$opencode_config_path"
  fi

  for command_path in "$pack_command_path" "$lite_command_path" "$check_command_path"; do
    if [[ -f "$command_path" ]]; then
      printf '[OK] opencode command: %s\n' "$command_path"
    else
      printf '[WARN] opencode command missing: %s\n' "$command_path"
    fi
  done
}

agent_adapter_content_init opencode "$AGENT_RAILS_VERSION" "$AGENT_RAILS_BIN" "$profile_path"
guide_content="$(agent_adapter_content_render guide)"
plugin_content="$(agent_adapter_content_render plugin)"
pack_command_content="$(agent_adapter_content_render pack)"
lite_command_content="$(agent_adapter_content_render lite)"
check_command_content="$(agent_adapter_content_render check)"

case "$subcommand" in
  install)
    printf 'Agent Rails opencode Install\n'
    printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
    printf 'Project: %s\n' "$project_abs"
    printf 'Profile: %s\n' "$profile_path"
    agent_adapter_workspace_install_skills
    agent_adapter_workspace_write_generated_file "$plugin_path" "$plugin_content"
    agent_adapter_workspace_write_generated_file "$guide_path" "$guide_content"
    agent_adapter_workspace_write_generated_file "$pack_command_path" "$pack_command_content"
    agent_adapter_workspace_write_generated_file "$lite_command_path" "$lite_command_content"
    agent_adapter_workspace_write_generated_file "$check_command_path" "$check_command_content"
    remove_legacy_opencode_config_instruction
    agent_adapter_workspace_write_managed_skills
    agent_adapter_workspace_ensure_ignore_block \
      "$local_ignore_path" \
      "# Agent Rails opencode adapter" \
      "# Agent Rails opencode adapter end" \
      ".opencode/AGENT_RAILS.md" \
      ".opencode/.agent-rails-managed-skills" \
      ".opencode/opencode.json" \
      ".opencode/plugins/agent-rails.mjs" \
      ".opencode/command/agent-rails-pack.md" \
      ".opencode/command/agent-rails-lite.md" \
      ".opencode/command/agent-rails-check.md" \
      ".opencode/skills/agent-*/" \
      ".agent-rails/"
    printf '\nopencode adapter ready.\n'
    printf 'Task Pack: %s\n' "$task_pack_path"
    printf 'Restart opencode or open a new opencode session for config changes to take effect.\n'
    ;;
  doctor)
    printf 'Agent Rails opencode Doctor\n'
    printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
    print_status
    ;;
  uninstall)
    printf 'Agent Rails opencode Uninstall\n'
    remove_legacy_opencode_config_instruction
    agent_adapter_workspace_remove_generated_file "$plugin_path"
    agent_adapter_workspace_remove_generated_file "$guide_path"
    agent_adapter_workspace_remove_generated_file "$pack_command_path"
    agent_adapter_workspace_remove_generated_file "$lite_command_path"
    agent_adapter_workspace_remove_generated_file "$check_command_path"
    agent_adapter_workspace_remove_managed_skills
    agent_adapter_workspace_remove_managed_skills_file
    agent_adapter_workspace_remove_ignore_block \
      "$local_ignore_path" \
      "# Agent Rails opencode adapter" \
      "# Agent Rails opencode adapter end" \
      "Would remove local ignore entries from" \
      "Updated local ignore file:" \
      ".opencode/AGENT_RAILS.md" \
      ".opencode/.agent-rails-managed-skills" \
      ".opencode/opencode.json" \
      ".opencode/plugins/agent-rails.mjs" \
      ".opencode/command/agent-rails-pack.md" \
      ".opencode/command/agent-rails-lite.md" \
      ".opencode/command/agent-rails-check.md" \
      ".opencode/skills/agent-*/" \
      ".agent-rails/"
    if [[ "$dry_run" -ne 1 ]]; then
      rmdir "$commands_dir" "$plugins_dir" "$skills_dir" "$opencode_dir" 2>/dev/null || true
    fi
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
