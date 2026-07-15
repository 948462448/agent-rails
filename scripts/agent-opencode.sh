#!/usr/bin/env bash
# Install, inspect, or remove the local Agent Rails opencode adapter.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails opencode install [--project PATH] [--profile PATH] [--mode local|project] [--dry-run] [--force]
       agent-rails opencode doctor [--project PATH]
       agent-rails opencode uninstall [--project PATH] [--dry-run] [--force]

opencode install writes a project-local .opencode/ adapter. Mode local ignores
the generated files in git repositories; mode project makes them committable.
It does not modify ~/.config/opencode.
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
install_mode="local"
install_mode_explicit=0
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
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        local|project) install_mode="$2" ;;
        *) usage >&2; exit 2 ;;
      esac
      install_mode_explicit=1
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

if [[ "$subcommand" != "install" && "$install_mode_explicit" -eq 1 ]]; then
  printf '%s\n' '--mode is only supported by agent-rails opencode install.' >&2
  exit 2
fi

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
pack_command_path="$commands_dir/agent-rails-pack.md"
lite_command_path="$commands_dir/agent-rails-lite.md"
check_command_path="$commands_dir/agent-rails-check.md"
opencode_config_path="$opencode_dir/opencode.json"
plugin_path="$plugins_dir/agent-rails.mjs"
plugin_template_path="$AGENT_RAILS_HOME/templates/opencode-agent-rails-plugin.mjs"
legacy_opencode_instruction_path="$guide_path"
legacy_relative_instruction_path=".opencode/AGENT_RAILS.md"
managed_skills_path="$opencode_dir/.agent-rails-managed-skills"
opencode_ignore_entries=(
  ".opencode/AGENT_RAILS.md"
  ".opencode/.agent-rails-managed-skills"
  ".opencode/opencode.json"
  ".opencode/plugins/agent-rails.mjs"
  ".opencode/command/agent-rails-pack.md"
  ".opencode/command/agent-rails-lite.md"
  ".opencode/command/agent-rails-check.md"
  ".opencode/skills/agent-*/"
  ".agent-rails/"
)
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
protect_tracked=0
[[ "$install_mode" == "local" ]] && protect_tracked=1
agent_adapter_workspace_configure \
  "$project_abs" \
  ".opencode/skills" \
  "$dry_run" \
  "$force" \
  "$protect_tracked" \
  "$legacy_adapter"

require_python_for_config() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required to update opencode config.\n' >&2
    exit 127
  fi
}

normalize_positive_integer() {
  local value="$1"
  local fallback="$2"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$fallback"
  fi
}

render_opencode_plugin() {
  local plugin_bin="$AGENT_RAILS_BIN"
  local plugin_assembler="$AGENT_RAILS_HOME/scripts/agent-context-assemble.py"
  local plugin_project="$project_abs"
  local plugin_profile="$profile_path"
  require_python_for_config
  [[ -f "$plugin_template_path" ]] || {
    printf 'OpenCode plugin template is missing: %s\n' "$plugin_template_path" >&2
    return 1
  }

  if [[ "$install_mode" == "project" ]]; then
    plugin_bin="agent-rails"
    plugin_assembler=""
    plugin_project=""
    plugin_profile=""
  fi

  python3 -E - \
    "$plugin_template_path" \
    "$AGENT_RAILS_VERSION" \
    "$plugin_bin" \
    "$plugin_assembler" \
    "$plugin_project" \
    "$plugin_profile" \
    "${AGENT_RAILS_TOKENIZER:-auto}" \
    "${AGENT_RAILS_TOKENIZER_CMD:-}" \
    "${AGENT_RAILS_TOKENIZER_PATH:-}" \
    "${AGENT_RAILS_TIKTOKEN_ENCODING:-cl100k_base}" \
    "$(normalize_positive_integer "${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}" 2)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_CONTEXT_PERCENT:-25}" 25)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_MAX_PACK_TOKENS:-60000}" 60000)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_MIN_PACK_TOKENS:-512}" 512)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_RESERVE_PERCENT:-5}" 5)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_RESERVE_TOKENS:-2048}" 2048)" \
    "$(normalize_positive_integer "${AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS:-30000}" 30000)" <<'PY'
import json
from pathlib import Path
import sys

(
    template_path,
    version,
    agent_rails_bin,
    assembler,
    project,
    profile,
    tokenizer,
    tokenizer_command,
    tokenizer_path,
    tiktoken_encoding,
    chars_per_token,
    context_percent,
    max_pack_tokens,
    min_pack_tokens,
    reserve_percent,
    reserve_tokens,
    hook_timeout_ms,
) = sys.argv[1:]

config = {
    "version": version,
    "bin": agent_rails_bin,
    "assembler": assembler,
    "project": project,
    "profile": profile,
    "tokenizer": tokenizer,
    "tokenizerCommand": tokenizer_command,
    "tokenizerPath": tokenizer_path,
    "tiktokenEncoding": tiktoken_encoding,
    "charsPerToken": int(chars_per_token),
    "contextPercent": int(context_percent),
    "maxPackTokens": int(max_pack_tokens),
    "minPackTokens": int(min_pack_tokens),
    "reservePercent": int(reserve_percent),
    "reserveTokens": int(reserve_tokens),
    "hookTimeoutMs": int(hook_timeout_ms),
}
template = Path(template_path).read_text(encoding="utf-8")
marker = "__AGENT_RAILS_CONFIG__"
if template.count(marker) != 1:
    raise SystemExit(f"Expected one {marker} marker in {template_path}")
print(template.replace(marker, json.dumps(config, ensure_ascii=False, indent=2)), end="")
PY
}

merge_opencode_config() {
  if [[ "$install_mode" == "local" && "$force" -ne 1 ]] \
    && agent_adapter_workspace_is_tracked_file "$opencode_config_path"; then
    printf 'Keeping tracked opencode config in local mode: %s\n' "$opencode_config_path"
    if grep -Fq "$plugin_path" "$opencode_config_path" 2>/dev/null; then
      printf '[OK] Tracked opencode config already references Agent Rails plugin.\n'
    else
      printf '[OK] Keeping tracked config unchanged; OpenCode auto-discovers project plugins.\n'
    fi
    return 0
  fi

  if [[ "$dry_run" -eq 1 ]]; then
    if [[ -f "$opencode_config_path" ]]; then
      printf 'Would merge Agent Rails plugin into %s\n' "$opencode_config_path"
    else
      printf 'Would write %s\n' "$opencode_config_path"
    fi
    return 0
  fi

  require_python_for_config
  mkdir -p "$(dirname "$opencode_config_path")"
  python3 -E - \
    "$opencode_config_path" \
    "$plugin_path" \
    "$legacy_opencode_instruction_path" \
    "$legacy_relative_instruction_path" \
    "$install_mode" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
plugin_path = sys.argv[2]
legacy_instruction_path = sys.argv[3]
legacy_relative_instruction_path = sys.argv[4]
install_mode = sys.argv[5]

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
plugins = data.get("plugin")
if plugins is not None:
    if not isinstance(plugins, list) or not all(isinstance(item, str) for item in plugins):
        raise SystemExit(f"{config_path} field 'plugin' must be an array of strings.")
    plugins[:] = [item for item in plugins if item != plugin_path]
if install_mode == "local":
    plugins = data.setdefault("plugin", [])
    if plugin_path not in plugins:
        plugins.append(plugin_path)
elif plugins == []:
    data.pop("plugin", None)

# Migrate the old static-instructions adapter. Unrelated instructions stay intact.
instructions = data.get("instructions")
if isinstance(instructions, list):
    data["instructions"] = [
        item
        for item in instructions
        if item not in {legacy_instruction_path, legacy_relative_instruction_path}
    ]
    if not data["instructions"]:
        data.pop("instructions", None)

config_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
PY
  printf 'Merged Agent Rails plugin into %s\n' "$opencode_config_path"
}

remove_opencode_config_plugin() {
  [[ -f "$opencode_config_path" ]] || return 0
  if [[ "$force" -ne 1 ]] && agent_adapter_workspace_is_tracked_file "$opencode_config_path"; then
    printf 'Keeping tracked opencode config in local mode: %s\n' "$opencode_config_path"
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would remove Agent Rails plugin from %s\n' "$opencode_config_path"
    return 0
  fi

  require_python_for_config
  python3 -E - \
    "$opencode_config_path" \
    "$plugin_path" \
    "$legacy_opencode_instruction_path" \
    "$legacy_relative_instruction_path" <<'PY'
import json
import pathlib
import sys

config_path = pathlib.Path(sys.argv[1])
plugin_path = sys.argv[2]
legacy_instruction_path = sys.argv[3]
legacy_relative_instruction_path = sys.argv[4]

try:
    data = json.loads(config_path.read_text())
except Exception as exc:
    raise SystemExit(f"Failed to parse {config_path}: {exc}")
if not isinstance(data, dict):
    raise SystemExit(f"{config_path} must contain a JSON object.")

plugins = data.get("plugin")
if isinstance(plugins, list):
    data["plugin"] = [item for item in plugins if item != plugin_path]
    if not data["plugin"]:
        data.pop("plugin", None)

instructions = data.get("instructions")
if isinstance(instructions, list):
    data["instructions"] = [
        item
        for item in instructions
        if item not in {legacy_instruction_path, legacy_relative_instruction_path}
    ]
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

  if [[ -f "$guide_path" ]] && grep -Fq 'Visible session marker protocol' "$guide_path"; then
    printf '[OK] opencode Agent Rails guide: %s\n' "$guide_path"
  else
    printf '[WARN] opencode Agent Rails guide is missing: %s\n' "$guide_path"
  fi

  if [[ -f "$plugin_path" ]] && \
    grep -Fq 'experimental.chat.system.transform' "$plugin_path" && \
    grep -Fq 'client.session.messages' "$plugin_path"; then
    printf '[OK] opencode request hook: %s\n' "$plugin_path"
  else
    printf '[WARN] opencode request hook is missing or incomplete: %s\n' "$plugin_path"
  fi

  if [[ -f "$opencode_config_path" ]] && grep -Fq "$plugin_path" "$opencode_config_path"; then
    printf '[OK] opencode config loads Agent Rails plugin: %s\n' "$opencode_config_path"
  elif [[ -f "$plugin_path" ]]; then
    printf '[OK] opencode auto-discovers Agent Rails plugin from the project plugin directory.\n'
  else
    printf '[WARN] opencode config does not load Agent Rails plugin: %s\n' "$opencode_config_path"
  fi

  for command_path in "$pack_command_path" "$lite_command_path" "$check_command_path"; do
    if [[ -f "$command_path" ]]; then
      printf '[OK] opencode command: %s\n' "$command_path"
    else
      printf '[WARN] opencode command missing: %s\n' "$command_path"
    fi
  done
}

adapter_content_bin="$AGENT_RAILS_BIN"
adapter_content_profile="$profile_path"
if [[ "$install_mode" == "project" ]]; then
  adapter_content_bin="agent-rails"
  adapter_content_profile=""
fi
agent_adapter_content_init opencode "$AGENT_RAILS_VERSION" "$adapter_content_bin" "$adapter_content_profile"
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
    printf 'Mode: %s\n' "$install_mode"
    plugin_content="$(render_opencode_plugin)"
    agent_adapter_workspace_install_skills
    agent_adapter_workspace_write_generated_file "$guide_path" "$guide_content"
    agent_adapter_workspace_write_generated_file "$pack_command_path" "$pack_command_content"
    agent_adapter_workspace_write_generated_file "$lite_command_path" "$lite_command_content"
    agent_adapter_workspace_write_generated_file "$check_command_path" "$check_command_content"
    agent_adapter_workspace_write_generated_file "$plugin_path" "$plugin_content"
    merge_opencode_config
    agent_adapter_workspace_write_managed_skills
    if [[ "$install_mode" == "local" ]]; then
      agent_adapter_workspace_ensure_ignore_block \
        "$local_ignore_path" \
        "# Agent Rails opencode adapter" \
        "# Agent Rails opencode adapter end" \
        "${opencode_ignore_entries[@]}"
    else
      agent_adapter_workspace_remove_ignore_block \
        "$local_ignore_path" \
        "# Agent Rails opencode adapter" \
        "# Agent Rails opencode adapter end" \
        "Would remove local ignore entries from" \
        "Removed local ignore entries from" \
        "${opencode_ignore_entries[@]}"
    fi
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
    remove_opencode_config_plugin
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
      "${opencode_ignore_entries[@]}"
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
