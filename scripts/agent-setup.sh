#!/usr/bin/env bash
# Configure one personal Agent Rails integration for the current Target Project.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails setup [--project PATH] [--profile PATH] [--tool auto|claude|codex|opencode|all] [--no-session-hook] [--dry-run]

With --tool auto, setup proceeds only when exactly one supported coding-agent
CLI is detected. Choose --tool explicitly when multiple tools are installed;
use --tool all only when every supported integration is intentionally wanted.

Claude setup uses local mode and enables the personal SessionStart hook by
default. Pass --no-session-hook to install only the project-local adapter.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-target-project.sh
source "$AGENT_RAILS_HOME/scripts/agent-target-project.sh"
agent_rails_init_paths

project="$PWD"
profile_path=""
tool="auto"
session_hook=1
dry_run=0

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
    --tool)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        auto|claude|codex|opencode|all) tool="$2" ;;
        *) usage >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --no-session-hook)
      session_hook=0
      shift
      ;;
    --dry-run)
      dry_run=1
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

agent_target_project_resolve "$project" "$profile_path" || exit $?
agent_target_project_load_profile required || exit 2
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"

detected_tools=()
detect_supported_tools() {
  local candidate
  detected_tools=()
  for candidate in claude codex opencode; do
    if command -v "$candidate" >/dev/null 2>&1; then
      detected_tools+=("$candidate")
    fi
  done
}

join_tools() {
  local joined=""
  local candidate
  for candidate in "$@"; do
    joined="${joined}${joined:+, }${candidate}"
  done
  printf '%s\n' "$joined"
}

selected_tools=()
case "$tool" in
  auto)
    detect_supported_tools
    case "${#detected_tools[@]}" in
      0)
        printf 'No supported coding-agent CLI detected. Choose --tool claude, codex, or opencode.\n' >&2
        exit 2
        ;;
      1)
        selected_tools=("${detected_tools[0]}")
        printf 'Detected tool: %s\n' "${selected_tools[0]}"
        ;;
      *)
        printf 'Multiple supported tools detected: %s\n' "$(join_tools "${detected_tools[@]}")" >&2
        printf 'Choose one with --tool claude|codex|opencode, or use --tool all intentionally.\n' >&2
        exit 2
        ;;
    esac
    ;;
  all)
    selected_tools=(claude codex opencode)
    ;;
  *)
    selected_tools=("$tool")
    ;;
esac

print_command() {
  local first=1
  local arg
  for arg in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      first=0
    else
      printf ' '
    fi
    printf '%q' "$arg"
  done
  printf '\n'
}

run_doctor() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would run: '
    print_command "$@"
  else
    "$@"
  fi
}

setup_tool() {
  local selected_tool="$1"
  local install_args=()
  local doctor_args=()

  printf '\nTool: %s\n' "$selected_tool"
  case "$selected_tool" in
    claude)
      install_args=(claude install --project "$project_abs" --profile "$profile_path" --mode local)
      [[ "$session_hook" -eq 1 ]] && install_args+=(--session-hook)
      [[ "$dry_run" -eq 1 ]] && install_args+=(--dry-run)
      "$AGENT_RAILS_BIN" "${install_args[@]}"
      doctor_args=("$AGENT_RAILS_BIN" doctor --project "$project_abs" --profile "$profile_path")
      run_doctor "${doctor_args[@]}"
      ;;
    codex)
      install_args=(codex install --project "$project_abs" --profile "$profile_path" --fix-project)
      [[ "$dry_run" -eq 1 ]] && install_args+=(--dry-run)
      "$AGENT_RAILS_BIN" "${install_args[@]}"
      doctor_args=("$AGENT_RAILS_BIN" codex doctor --project "$project_abs")
      run_doctor "${doctor_args[@]}"
      ;;
    opencode)
      install_args=(opencode install --project "$project_abs" --profile "$profile_path")
      [[ "$dry_run" -eq 1 ]] && install_args+=(--dry-run)
      "$AGENT_RAILS_BIN" "${install_args[@]}"
      doctor_args=("$AGENT_RAILS_BIN" opencode doctor --project "$project_abs")
      run_doctor "${doctor_args[@]}"
      ;;
  esac
}

printf 'Agent Rails Setup\n'
printf 'Project: %s\n' "$project_abs"
printf 'Profile: %s\n' "$profile_path"
for selected_tool in "${selected_tools[@]}"; do
  setup_tool "$selected_tool"
done

printf '\nAgent Rails setup complete.\n'
printf 'Next: cd %q && agent-rails run "<goal>"\n' "$project_abs"
