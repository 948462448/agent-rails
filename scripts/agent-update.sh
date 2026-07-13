#!/usr/bin/env bash
# Update the Agent Rails kit and refresh a target project's local adapter.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails update [--project PATH] [--profile PATH] [--mode local|project] [--session-hook] [--global-reminder] [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
       agent-rails upgrade self [same options]

Runs a safe local update loop:
  git pull --ff-only for the Agent Rails kit
  bash tests/run.sh
  doctor on the target project
  refresh the target adapter and bundled skills
  final doctor on the target project
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

project="$PWD"
profile_path=""
install_mode="local"
session_hook=0
global_reminder=0
skip_pull=0
skip_tests=0
skip_doctor=0
skip_adapter=0
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
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        local|project) install_mode="$2" ;;
        *) usage >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --session-hook)
      session_hook=1
      shift
      ;;
    --global-reminder)
      global_reminder=1
      shift
      ;;
    --skip-pull)
      skip_pull=1
      shift
      ;;
    --skip-tests)
      skip_tests=1
      shift
      ;;
    --skip-doctor)
      skip_doctor=1
      shift
      ;;
    --skip-adapter)
      skip_adapter=1
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

print_command() {
  local first=1 arg
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

run_step() {
  local title="$1"
  shift
  printf '\n%s\n' "$title"
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would run: '
    print_command "$@"
  else
    "$@"
  fi
}

resolve_project() {
  if [[ ! -d "$project" ]]; then
    printf 'Project directory not found: %s\n' "$project" >&2
    exit 2
  fi

  project_abs="$(cd "$project" && pwd)"
  if git_root_for_project="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
    project_abs="$(cd "$git_root_for_project" && pwd)"
  fi
  project_name="$(basename "$project_abs")"
  profile_path="$(agent_rails_resolve_profile "$project_abs" "$project_name" "$profile_path")"
  if [[ ! -f "$profile_path" ]]; then
    printf 'Profile not found: %s\n' "$profile_path" >&2
    exit 2
  fi
}

pull_command() {
  local branch upstream
  if ! git -C "$AGENT_RAILS_HOME" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'Agent Rails home is not a git repository: %s\n' "$AGENT_RAILS_HOME" >&2
    exit 2
  fi

  if [[ "$dry_run" -ne 1 && -n "$(git -C "$AGENT_RAILS_HOME" status --porcelain)" ]]; then
    printf 'Agent Rails kit has local changes; commit/stash them or pass --skip-pull.\n' >&2
    exit 1
  fi

  if upstream="$(git -C "$AGENT_RAILS_HOME" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
    printf 'git -C %q pull --ff-only\n' "$AGENT_RAILS_HOME"
  else
    branch="$(git -C "$AGENT_RAILS_HOME" branch --show-current)"
    [[ -n "$branch" ]] || branch="main"
    printf 'git -C %q pull --ff-only origin %q\n' "$AGENT_RAILS_HOME" "$branch"
  fi
}

run_pull() {
  local branch upstream
  if [[ "$skip_pull" -eq 1 ]]; then
    printf '\nSkip git pull (--skip-pull).\n'
    return 0
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf '\nUpdate Agent Rails kit\n'
    printf 'Would run: %s\n' "$(pull_command)"
    return 0
  fi

  if ! git -C "$AGENT_RAILS_HOME" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'Agent Rails home is not a git repository: %s\n' "$AGENT_RAILS_HOME" >&2
    exit 2
  fi
  if [[ -n "$(git -C "$AGENT_RAILS_HOME" status --porcelain)" ]]; then
    printf 'Agent Rails kit has local changes; commit/stash them or pass --skip-pull.\n' >&2
    exit 1
  fi
  if upstream="$(git -C "$AGENT_RAILS_HOME" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null)"; then
    run_step "Update Agent Rails kit" git -C "$AGENT_RAILS_HOME" pull --ff-only
  else
    branch="$(git -C "$AGENT_RAILS_HOME" branch --show-current)"
    [[ -n "$branch" ]] || branch="main"
    run_step "Update Agent Rails kit" git -C "$AGENT_RAILS_HOME" pull --ff-only origin "$branch"
  fi
}

resolve_project

printf 'Agent Rails Update\n'
printf 'Kit: %s\n' "$AGENT_RAILS_HOME"
printf 'Project: %s\n' "$project_abs"
printf 'Profile: %s\n' "$profile_path"
printf 'Mode: %s\n' "$install_mode"

run_pull

if [[ "$skip_tests" -eq 1 ]]; then
  printf '\nSkip tests (--skip-tests).\n'
else
  run_step "Run Agent Rails tests" bash "$AGENT_RAILS_HOME/tests/run.sh"
fi

if [[ "$skip_doctor" -eq 1 ]]; then
  printf '\nSkip pre-upgrade doctor (--skip-doctor).\n'
else
  run_step "Run pre-upgrade doctor" "$AGENT_RAILS_BIN" doctor --project "$project_abs" --profile "$profile_path"
fi

if [[ "$skip_adapter" -eq 1 ]]; then
  printf '\nSkip adapter upgrade (--skip-adapter).\n'
else
  upgrade_args=(--project "$project_abs" --profile "$profile_path" --mode "$install_mode")
  [[ "$session_hook" -eq 1 ]] && upgrade_args+=(--session-hook)
  [[ "$global_reminder" -eq 1 ]] && upgrade_args+=(--global-reminder)
  run_step "Refresh target adapter and skills" "$AGENT_RAILS_HOME/scripts/agent-install-claude.sh" "${upgrade_args[@]}"
fi

if [[ "$skip_doctor" -eq 1 ]]; then
  printf '\nSkip final doctor (--skip-doctor).\n'
else
  run_step "Run final doctor" "$AGENT_RAILS_BIN" doctor --project "$project_abs" --profile "$profile_path"
fi

printf '\nAgent Rails update complete.\n'
