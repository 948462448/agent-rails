#!/usr/bin/env bash
# Install, inspect, or remove the local Agent Rails opencode adapter.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails opencode install [--project PATH] [--profile PATH] [--dry-run] [--force]
       agent-rails opencode doctor [--project PATH]
       agent-rails opencode uninstall [--project PATH] [--dry-run] [--force]

opencode install writes a project-local .opencode/ adapter and ignores it
locally in git repositories. It does not modify ~/.config/opencode.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-adapter-lifecycle.sh
source "$AGENT_RAILS_HOME/scripts/agent-adapter-lifecycle.sh"
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

project_abs="$(cd "$project" && pwd)"
if git_root_for_project="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
  project_abs="$(cd "$git_root_for_project" && pwd)"
fi
project_name="$(basename "$project_abs")"
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
PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
if [[ -n "$PROJECT_WORKTREE_SLUG_PRESET" ]]; then
  PROJECT_WORKTREE_SLUG="$PROJECT_WORKTREE_SLUG_PRESET"
else
  PROJECT_WORKTREE_SLUG="$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")"
fi
task_pack_path="${TASK_PACK_PATH:-$(agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG")}"

opencode_dir="$project_abs/.opencode"
skills_dir="$opencode_dir/skills"
commands_dir="$opencode_dir/command"
guide_path="$opencode_dir/AGENT_RAILS.md"
pack_command_path="$commands_dir/agent-rails-pack.md"
lite_command_path="$commands_dir/agent-rails-lite.md"
check_command_path="$commands_dir/agent-rails-check.md"
opencode_config_path="$opencode_dir/opencode.json"
opencode_instruction_path="$guide_path"
managed_skills_path="$opencode_dir/.agent-rails-managed-skills"
agent_adapter_lifecycle_init \
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

say_write() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would write %s\n' "$1"
  else
    printf 'Wrote %s\n' "$1"
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

write_managed_skills() {
  if [[ "$force" -ne 1 ]] && is_tracked_file "$managed_skills_path"; then
    printf 'Keeping tracked managed skill inventory in local mode: %s\n' "$managed_skills_path"
    return 0
  fi
  agent_adapter_write_managed_skills "$opencode_dir" "$dry_run"
}

agent_adapter_load_managed_skills
legacy_adapter=0
if [[ ! -f "$managed_skills_path" ]] && agent_adapter_is_generated_file "$guide_path"; then
  legacy_adapter=1
fi

write_file() {
  local path="$1"
  local content="$2"

  if [[ "$force" -ne 1 ]] && is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi

  if [[ -e "$path" && "$force" -ne 1 ]] && ! agent_adapter_is_generated_file "$path"; then
    printf 'Keeping unmanaged existing file: %s\n' "$path"
    return 0
  fi

  if [[ -e "$path" && "$force" -ne 1 ]]; then
    printf 'Refreshing Agent Rails-generated %s\n' "$path"
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    say_write "$path"
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  say_write "$path"
}

install_skills() {
  local args=(--dest "$skills_dir")
  local selected_skills=()
  local selected_skill_count=0
  local skill_dir skill_name target_dir
  [[ "$dry_run" -eq 1 ]] && args+=(--dry-run)

  if [[ -d "$AGENT_RAILS_HOME/skills" ]]; then
    while IFS= read -r skill_dir; do
      skill_name="$(basename "$skill_dir")"
      target_dir="$skills_dir/$skill_name"
      if [[ "$force" -ne 1 ]] && is_tracked_prefix ".opencode/skills/$skill_name"; then
        printf 'Keeping tracked skill directory in local mode: %s\n' "$project_abs/.opencode/skills/$skill_name"
      elif [[ -e "$target_dir" && "$force" -ne 1 && "$legacy_adapter" -ne 1 ]] \
        && ! agent_adapter_managed_skill_is_recorded "$skill_name"; then
        printf 'Keeping unmanaged existing skill directory: %s\n' "$target_dir"
      else
        selected_skills+=("$skill_name")
        selected_skill_count=$((selected_skill_count + 1))
        agent_adapter_record_managed_skill "$skill_name"
      fi
    done < <(find "$AGENT_RAILS_HOME/skills" -mindepth 1 -maxdepth 1 -type d | sort)
  fi

  if [[ "$selected_skill_count" -eq 0 ]]; then
    printf 'No Agent Rails skills to install.\n'
    return 0
  fi

  args+=("${selected_skills[@]}")
  "$AGENT_RAILS_HOME/scripts/agent-install-skills.sh" "${args[@]}"
}

append_local_ignore() {
  local marker="# Agent Rails opencode adapter"
  local end_marker="# Agent Rails opencode adapter end"

  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would ensure local ignore entries in %s\n' "$local_ignore_path"
    printf '  .opencode/AGENT_RAILS.md\n'
    printf '  .opencode/.agent-rails-managed-skills\n'
    printf '  .opencode/opencode.json\n'
    printf '  .opencode/command/agent-rails-pack.md\n'
    printf '  .opencode/command/agent-rails-lite.md\n'
    printf '  .opencode/command/agent-rails-check.md\n'
    printf '  .opencode/skills/agent-*/\n'
    printf '  .agent-rails/\n'
    return 0
  fi

  mkdir -p "$(dirname "$local_ignore_path")"
  if [[ -f "$local_ignore_path" ]] && grep -Fxq "$marker" "$local_ignore_path"; then
    local tmp_file
    tmp_file="$(mktemp)"
    awk -v marker="$marker" -v end_marker="$end_marker" '
      $0 == marker { in_block = 1; next }
      in_block && $0 == end_marker { in_block = 0; next }
      !in_block { print }
    ' "$local_ignore_path" > "$tmp_file"
    mv "$tmp_file" "$local_ignore_path"
  fi

  if ! {
    [[ -s "$local_ignore_path" ]] && printf '\n'
    printf '%s\n' "$marker"
    printf '.opencode/AGENT_RAILS.md\n'
    printf '.opencode/.agent-rails-managed-skills\n'
    printf '.opencode/opencode.json\n'
    printf '.opencode/command/agent-rails-pack.md\n'
    printf '.opencode/command/agent-rails-lite.md\n'
    printf '.opencode/command/agent-rails-check.md\n'
    printf '.opencode/skills/agent-*/\n'
    printf '.agent-rails/\n'
    printf '%s\n' "$end_marker"
  } >> "$local_ignore_path"; then
    printf 'Failed to update local ignore file: %s\n' "$local_ignore_path" >&2
    exit 1
  fi
  printf 'Updated local ignore file: %s\n' "$local_ignore_path"
}

remove_local_ignore() {
  local marker="# Agent Rails opencode adapter"
  local end_marker="# Agent Rails opencode adapter end"

  [[ -f "$local_ignore_path" ]] || return 0
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove local ignore entries from %s\n' "$local_ignore_path"
    return 0
  fi

  local tmp_file
  tmp_file="$(mktemp)"
  awk -v marker="$marker" -v end_marker="$end_marker" '
    $0 == marker { in_block = 1; next }
    in_block && $0 == end_marker { in_block = 0; next }
    !in_block { print }
  ' "$local_ignore_path" > "$tmp_file"
  mv "$tmp_file" "$local_ignore_path"
  printf 'Updated local ignore file: %s\n' "$local_ignore_path"
}

require_python_for_config() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required to update opencode config.\n' >&2
    exit 127
  fi
}

merge_opencode_config() {
  if [[ "$force" -ne 1 ]] && is_tracked_file "$opencode_config_path"; then
    printf 'Keeping tracked opencode config in local mode: %s\n' "$opencode_config_path"
    if grep -Fq "$opencode_instruction_path" "$opencode_config_path" 2>/dev/null; then
      printf '[OK] Tracked opencode config already references Agent Rails instructions.\n'
    else
      printf '[WARN] Add this instruction path to tracked opencode config manually: %s\n' "$opencode_instruction_path"
    fi
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    if [[ -f "$opencode_config_path" ]]; then
      printf 'Would merge Agent Rails instructions into %s\n' "$opencode_config_path"
    else
      printf 'Would write %s\n' "$opencode_config_path"
    fi
    return 0
  fi

  require_python_for_config
  mkdir -p "$(dirname "$opencode_config_path")"
  python3 - "$opencode_config_path" "$opencode_instruction_path" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
instruction_path = sys.argv[2]

if config_path.exists():
    try:
        data = json.loads(config_path.read_text())
    except Exception as exc:
        raise SystemExit(
            f"Failed to parse {config_path}: {exc}. "
            "Fix the file first; Agent Rails will not overwrite existing opencode config."
        )
    if not isinstance(data, dict):
        raise SystemExit(f"{config_path} must contain a JSON object.")
else:
    data = {}

data.setdefault("$schema", "https://opencode.ai/config.json")
instructions = data.setdefault("instructions", [])
if not isinstance(instructions, list) or not all(isinstance(item, str) for item in instructions):
    raise SystemExit(f"{config_path} field 'instructions' must be an array of strings.")
if instruction_path not in instructions:
    instructions.append(instruction_path)

config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
  printf 'Merged Agent Rails instructions into %s\n' "$opencode_config_path"
}

remove_opencode_config_instruction() {
  [[ -f "$opencode_config_path" ]] || return 0
  if [[ "$force" -ne 1 ]] && is_tracked_file "$opencode_config_path"; then
    printf 'Keeping tracked opencode config in local mode: %s\n' "$opencode_config_path"
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove Agent Rails instructions from %s\n' "$opencode_config_path"
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

remove_generated_path() {
  local path="$1"
  if [[ "$force" -ne 1 ]] && is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi
  if [[ ! -e "$path" ]]; then
    return 0
  fi
  if [[ "$path" != "$managed_skills_path" && "$force" -ne 1 ]] \
    && ! agent_adapter_is_generated_file "$path"; then
    printf 'Keeping unmanaged existing file: %s\n' "$path"
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove %s\n' "$path"
  else
    rm -f "$path"
    printf 'Removed %s\n' "$path"
  fi
}

remove_generated_skills() {
  local index skill_name skill_dir
  local skills_to_remove=()
  local skills_to_remove_count=0
  [[ -d "$skills_dir" ]] || return 0

  if [[ -f "$managed_skills_path" ]]; then
    while IFS= read -r skill_name; do
      skills_to_remove+=("$skill_name")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(agent_adapter_list_managed_skills)
  elif [[ "$legacy_adapter" -eq 1 && -d "$AGENT_RAILS_HOME/skills" ]]; then
    while IFS= read -r skill_dir; do
      skills_to_remove+=("$(basename "$skill_dir")")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(find "$AGENT_RAILS_HOME/skills" -mindepth 1 -maxdepth 1 -type d | sort)
  fi

  for ((index = 0; index < skills_to_remove_count; index++)); do
    skill_name="${skills_to_remove[$index]}"
    agent_adapter_is_valid_managed_skill_name "$skill_name" || continue
    skill_dir="$skills_dir/$skill_name"
    [[ -e "$skill_dir" ]] || continue
    if [[ "$force" -ne 1 ]] && is_tracked_prefix ".opencode/skills/$skill_name"; then
      printf 'Keeping tracked skill directory in local mode: %s\n' "$skill_dir"
      continue
    fi
    if [[ "$dry_run" -eq 1 ]]; then
      printf 'Would remove %s\n' "$skill_dir"
    else
      rm -rf "$skill_dir"
      printf 'Removed %s\n' "$skill_dir"
    fi
  done
}

print_status() {
  printf 'Project: %s\n' "$project_abs"
  if command -v opencode >/dev/null 2>&1; then
    printf '[OK] opencode CLI: %s\n' "$(command -v opencode)"
    opencode --version 2>/dev/null | sed 's/^/Version: /' || true
  else
    printf '[WARN] opencode CLI not found.\n'
  fi

  if [[ -f "$guide_path" ]] && grep -Fq 'Visible session marker protocol' "$guide_path"; then
    printf '[OK] opencode Agent Rails guide: %s\n' "$guide_path"
  else
    printf '[WARN] opencode Agent Rails guide is missing: %s\n' "$guide_path"
  fi

  if [[ -f "$opencode_config_path" ]] && grep -Fq "$opencode_instruction_path" "$opencode_config_path"; then
    printf '[OK] opencode config loads Agent Rails instructions: %s\n' "$opencode_config_path"
  else
    printf '[WARN] opencode config does not load Agent Rails instructions: %s\n' "$opencode_config_path"
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
pack_command_content="$(agent_adapter_content_render pack)"
lite_command_content="$(agent_adapter_content_render lite)"
check_command_content="$(agent_adapter_content_render check)"

case "$subcommand" in
  install)
    printf 'Agent Rails opencode Install\n'
    printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
    printf 'Project: %s\n' "$project_abs"
    printf 'Profile: %s\n' "$profile_path"
    install_skills
    write_file "$guide_path" "$guide_content"
    write_file "$pack_command_path" "$pack_command_content"
    write_file "$lite_command_path" "$lite_command_content"
    write_file "$check_command_path" "$check_command_content"
    merge_opencode_config
    write_managed_skills
    append_local_ignore
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
    remove_opencode_config_instruction
    remove_generated_path "$guide_path"
    remove_generated_path "$pack_command_path"
    remove_generated_path "$lite_command_path"
    remove_generated_path "$check_command_path"
    remove_generated_skills
    remove_generated_path "$managed_skills_path"
    remove_local_ignore
    if [[ "$dry_run" -ne 1 ]]; then
      rmdir "$commands_dir" "$skills_dir" "$opencode_dir" 2>/dev/null || true
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
