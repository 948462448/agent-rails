#!/usr/bin/env bash
# Update the Agent Rails kit and optionally refresh a target project's adapter.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails update [--project PATH] [--profile PATH] [--mode local|project] [--session-hook] [--global-reminder] [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
       agent-rails upgrade self [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--skip-tests] [--dry-run]

Update source depends on how the kit was installed:
  Git checkout     git pull --ff-only
  GitHub Release   verified release archive + atomic version switch

`upgrade self` updates only the kit and does not require a target project.
`update` runs source tests only for a Git checkout, then Doctor and the Claude
adapter refresh unless skipped. Refresh Codex or OpenCode with `setup --tool`.
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

original_args=("$@")
project="$PWD"
profile_path=""
install_mode="local"
session_hook=0
global_reminder=0
requested_version="latest"
default_install_root="${XDG_DATA_HOME:-$HOME/.local/share}/agent-rails"
if [[ -n "${AGENT_RAILS_INSTALL_ROOT:-}" ]]; then
  install_root="$AGENT_RAILS_INSTALL_ROOT"
elif [[ "$(basename "$AGENT_RAILS_HOME")" == "current" ]]; then
  install_root="$(dirname "$AGENT_RAILS_HOME")"
elif [[ "$(basename "$(dirname "$AGENT_RAILS_HOME")")" == "releases" ]]; then
  install_root="$(dirname "$(dirname "$AGENT_RAILS_HOME")")"
else
  install_root="$default_install_root"
fi
if [[ -n "${AGENT_RAILS_RELEASE_REPOSITORY:-}" ]]; then
  repository="$AGENT_RAILS_RELEASE_REPOSITORY"
elif [[ -f "$install_root/release-repository" ]]; then
  repository="$(awk 'NF { print $1; exit }' "$install_root/release-repository")"
else
  repository="948462448/agent-rails"
fi
if [[ -n "${AGENT_RAILS_BIN_DIR:-}" ]]; then
  bin_dir="$AGENT_RAILS_BIN_DIR"
elif [[ -f "$install_root/release-bin-dir" ]]; then
  bin_dir="$(sed -n '1p' "$install_root/release-bin-dir")"
else
  bin_dir="$HOME/.local/bin"
fi
skip_pull=0
skip_tests=0
skip_doctor=0
skip_adapter=0
self_only=0
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
    --version)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      requested_version="${2#v}"
      shift 2
      ;;
    --repository)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      repository="$2"
      shift 2
      ;;
    --install-root)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      install_root="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      bin_dir="$2"
      shift 2
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
    --self-only)
      self_only=1
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

if [[ "$self_only" -eq 1 ]]; then
  skip_doctor=1
  skip_adapter=1
fi

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

  agent_target_project_resolve "$project" "$profile_path" || exit $?
  project_abs="$AGENT_TARGET_PROJECT_ROOT"
  profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"
  if [[ ! -f "$profile_path" ]]; then
    printf 'Profile not found: %s\n' "$profile_path" >&2
    exit 2
  fi
}

kit_is_git_checkout() {
  git -C "$AGENT_RAILS_HOME" rev-parse --is-inside-work-tree >/dev/null 2>&1
}

pull_command() {
  local branch upstream
  if ! kit_is_git_checkout; then
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

run_git_update() {
  local branch upstream
  if [[ "$skip_pull" -eq 1 ]]; then
    printf '\nSkip git pull (--skip-pull).\n'
    return 0
  fi
  if [[ "$requested_version" != "latest" ]]; then
    printf -- '--version is only supported by a GitHub Release installation.\n' >&2
    exit 2
  fi
  if [[ "$dry_run" -eq 1 ]]; then
    printf '\nUpdate Agent Rails kit\n'
    printf 'Would run: %s\n' "$(pull_command)"
    return 0
  fi

  if ! kit_is_git_checkout; then
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

run_release_update() {
  local installer="$AGENT_RAILS_HOME/scripts/agent-release-install.sh"
  local installer_args new_home old_physical new_physical
  installer_args=(
    --version "$requested_version"
    --repository "$repository"
    --install-root "$install_root"
    --bin-dir "$bin_dir"
  )

  if [[ "$skip_pull" -eq 1 ]]; then
    printf '\nSkip release download (--skip-pull).\n'
    return 0
  fi
  if [[ ! -x "$installer" ]]; then
    printf 'Release installer not found: %s\n' "$installer" >&2
    exit 2
  fi

  old_physical="$(cd "$AGENT_RAILS_HOME" && pwd -P)"
  printf '\nUpdate Agent Rails release\n'
  if [[ "$dry_run" -eq 1 ]]; then
    "$installer" "${installer_args[@]}" --dry-run
    return 0
  fi
  "$installer" "${installer_args[@]}"

  new_home="$install_root/current"
  if [[ "${AGENT_RAILS_UPDATE_REEXEC:-0}" != "1" && -x "$new_home/bin/agent-rails" ]]; then
    new_physical="$(cd "$new_home" && pwd -P)"
    if [[ "$old_physical" != "$new_physical" ]]; then
      printf 'Continue with Agent Rails %s\n' "$(awk 'NF { print $1; exit }' "$new_home/VERSION")"
      exec env \
        AGENT_RAILS_UPDATE_REEXEC=1 \
        AGENT_RAILS_HOME="$new_home" \
        "$new_home/bin/agent-rails" update "${original_args[@]}" --skip-pull
    fi
  fi
}

needs_project=1
if [[ "$skip_doctor" -eq 1 && "$skip_adapter" -eq 1 ]]; then
  needs_project=0
else
  resolve_project
fi

printf 'Agent Rails Update\n'
printf 'Kit: %s\n' "$AGENT_RAILS_HOME"
if [[ "$self_only" -eq 1 ]]; then
  printf 'Mode: self\n'
else
  printf 'Mode: project\n'
fi
if [[ "$needs_project" -eq 1 ]]; then
  printf 'Project: %s\n' "$project_abs"
  printf 'Profile: %s\n' "$profile_path"
  printf 'Adapter mode: %s\n' "$install_mode"
fi

if kit_is_git_checkout; then
  run_git_update
else
  run_release_update
fi

if [[ "$skip_tests" -eq 1 ]]; then
  printf '\nSkip tests (--skip-tests).\n'
elif ! kit_is_git_checkout; then
  printf '\nSkip source test suite for verified Release installation.\n'
else
  run_step "Run Agent Rails tests" bash "$AGENT_RAILS_HOME/tests/run.sh"
fi

if [[ "$skip_doctor" -eq 1 ]]; then
  if [[ "$self_only" -ne 1 ]]; then
    printf '\nSkip pre-upgrade doctor (--skip-doctor).\n'
  fi
else
  run_step "Run pre-upgrade doctor" "$AGENT_RAILS_BIN" doctor --project "$project_abs" --profile "$profile_path"
fi

if [[ "$skip_adapter" -eq 1 ]]; then
  if [[ "$self_only" -ne 1 ]]; then
    printf '\nSkip adapter upgrade (--skip-adapter).\n'
  fi
else
  upgrade_args=(--project "$project_abs" --profile "$profile_path" --mode "$install_mode")
  [[ "$session_hook" -eq 1 ]] && upgrade_args+=(--session-hook)
  [[ "$global_reminder" -eq 1 ]] && upgrade_args+=(--global-reminder)
  run_step "Refresh target adapter and skills" "$AGENT_RAILS_HOME/scripts/agent-install-claude.sh" "${upgrade_args[@]}"
fi

if [[ "$skip_doctor" -eq 0 ]]; then
  run_step "Run final doctor" "$AGENT_RAILS_BIN" doctor --project "$project_abs" --profile "$profile_path"
fi

printf '\nAgent Rails update complete.\n'
