#!/usr/bin/env bash
# Shared Target Project identity, Profile, and worktree-specific path context.

agent_target_project_resolve() {
  [[ "$#" -eq 2 ]] || {
    printf 'agent_target_project_resolve expects a project path and optional Profile path.\n' >&2
    return 2
  }
  agent_rails_init_paths

  local requested_path="$1"
  local profile_path_arg="$2"
  local project_root git_root

  AGENT_TARGET_PROJECT_ROOT=""
  AGENT_TARGET_PROJECT_DEFAULT_NAME=""
  AGENT_TARGET_PROJECT_PROFILE_PATH=""
  AGENT_TARGET_PROJECT_PROFILE_STATUS="unresolved"
  AGENT_TARGET_PROJECT_IS_GIT_REPO=0
  AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET=""
  AGENT_TARGET_PROJECT_TASK_PACK_PATH=""
  if [[ ! -d "$requested_path" ]]; then
    AGENT_TARGET_PROJECT_PROFILE_STATUS="project-missing"
    return 1
  fi

  project_root="$(cd "$requested_path" && pwd)"
  if command -v git >/dev/null 2>&1 \
    && git_root="$(git -C "$project_root" rev-parse --show-toplevel 2>/dev/null)"; then
    project_root="$(cd "$git_root" && pwd)"
    AGENT_TARGET_PROJECT_IS_GIT_REPO=1
  fi

  AGENT_TARGET_PROJECT_ROOT="$project_root"
  AGENT_TARGET_PROJECT_DEFAULT_NAME="$(basename "$project_root")"
  AGENT_TARGET_PROJECT_PROFILE_PATH="$(
    agent_rails_resolve_profile \
      "$project_root" \
      "$AGENT_TARGET_PROJECT_DEFAULT_NAME" \
      "$profile_path_arg"
  )"
  AGENT_TARGET_PROJECT_PROFILE_STATUS="unloaded"
  AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
  PROJECT_ROOT="$project_root"
  PROJECT_NAME="${PROJECT_NAME:-$AGENT_TARGET_PROJECT_DEFAULT_NAME}"

  agent_target_project_finalize
}

agent_target_project_load_profile() {
  [[ "$#" -le 1 ]] || {
    printf 'agent_target_project_load_profile accepts only optional required mode.\n' >&2
    return 2
  }
  local mode="${1:-inspect}"
  case "$mode" in
    inspect|required) ;;
    *)
      printf 'Unknown Target Project Profile load mode: %s\n' "$mode" >&2
      return 2
      ;;
  esac
  [[ -n "${AGENT_TARGET_PROJECT_PROFILE_PATH:-}" ]] || {
    printf 'Resolve a Target Project before loading its Profile.\n' >&2
    return 2
  }
  if [[ ! -f "$AGENT_TARGET_PROJECT_PROFILE_PATH" ]]; then
    AGENT_TARGET_PROJECT_PROFILE_STATUS="missing"
    if [[ "$mode" == "required" ]]; then
      printf 'Profile not found: %s\n' "$AGENT_TARGET_PROJECT_PROFILE_PATH" >&2
    fi
    return 1
  fi

  # shellcheck source=/dev/null
  if ! source "$AGENT_TARGET_PROJECT_PROFILE_PATH"; then
    AGENT_TARGET_PROJECT_PROFILE_STATUS="invalid"
    if [[ "$mode" == "required" ]]; then
      printf 'Profile could not be sourced: %s\n' "$AGENT_TARGET_PROJECT_PROFILE_PATH" >&2
    fi
    return 1
  fi
  AGENT_TARGET_PROJECT_PROFILE_STATUS="loaded"
  agent_target_project_finalize
}

agent_target_project_finalize() {
  [[ "$#" -eq 0 ]] || {
    printf 'agent_target_project_finalize does not accept arguments.\n' >&2
    return 2
  }
  [[ -n "${AGENT_TARGET_PROJECT_ROOT:-}" ]] || {
    printf 'Resolve a Target Project before finalizing its context.\n' >&2
    return 2
  }

  PROJECT_ROOT="$AGENT_TARGET_PROJECT_ROOT"
  PROJECT_NAME="${PROJECT_NAME:-$AGENT_TARGET_PROJECT_DEFAULT_NAME}"
  if [[ -n "$AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET" ]]; then
    PROJECT_WORKTREE_SLUG="$AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET"
  else
    PROJECT_WORKTREE_SLUG="$(
      agent_rails_project_worktree_slug "$AGENT_TARGET_PROJECT_ROOT" "$PROJECT_NAME"
    )"
  fi

  AGENT_TARGET_PROJECT_TASK_PACK_PATH="${TASK_PACK_PATH:-$(
    agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG"
  )}"
}
