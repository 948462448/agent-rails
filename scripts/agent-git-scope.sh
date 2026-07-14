#!/usr/bin/env bash
# Shared Git ref resolution and changed-path snapshots for Agent Rails adapters.

agent_git_scope_reset() {
  AGENT_GIT_SCOPE_TARGET_REF=""
  AGENT_GIT_SCOPE_TARGET_SHA=""
  AGENT_GIT_SCOPE_TARGET_SHORT_SHA=""
  AGENT_GIT_SCOPE_BASE_REF=""
  AGENT_GIT_SCOPE_BASE_SHA=""
  AGENT_GIT_SCOPE_MERGE_BASE=""
}

agent_git_scope_default_base_ref() {
  [[ "$#" -eq 1 ]] || {
    printf 'agent_git_scope_default_base_ref expects project|publish policy.\n' >&2
    return 2
  }
  local policy="$1"
  local ref
  local -a candidates=()
  case "$policy" in
    project)
      candidates=(origin/main origin/master main master)
      ;;
    publish)
      candidates=('@{upstream}' origin/main origin/master main master)
      ;;
    *)
      printf 'Unknown Git scope base policy: %s\n' "$policy" >&2
      return 2
      ;;
  esac

  for ref in "${candidates[@]}"; do
    if git rev-parse --verify --quiet "$ref^{commit}" >/dev/null; then
      printf '%s\n' "$ref"
      return 0
    fi
  done
}

agent_git_scope_resolve() {
  [[ "$#" -eq 3 ]] || {
    printf 'agent_git_scope_resolve expects target ref, optional base ref, and project|publish policy.\n' >&2
    return 2
  }
  local target_ref="$1"
  local base_ref="$2"
  local base_policy="$3"
  local target_sha base_sha merge_base

  agent_git_scope_reset
  case "$base_policy" in
    project|publish) ;;
    *)
      printf 'Unknown Git scope base policy: %s\n' "$base_policy" >&2
      return 2
      ;;
  esac

  if ! target_sha="$(git rev-parse --verify "$target_ref^{commit}" 2>/dev/null)"; then
    printf 'Target ref not found: %s\n' "$target_ref" >&2
    return 2
  fi
  if [[ -z "$base_ref" ]]; then
    base_ref="$(agent_git_scope_default_base_ref "$base_policy" || true)"
  fi

  base_sha=""
  if [[ -n "$base_ref" ]]; then
    if ! base_sha="$(git rev-parse --verify "$base_ref^{commit}" 2>/dev/null)"; then
      printf 'Base ref not found: %s\n' "$base_ref" >&2
      return 2
    fi
    if ! merge_base="$(git merge-base "$target_ref" "$base_ref")"; then
      printf 'Merge base not found between %s and %s.\n' "$target_ref" "$base_ref" >&2
      return 2
    fi
  else
    merge_base="$target_sha"
  fi

  AGENT_GIT_SCOPE_TARGET_REF="$target_ref"
  AGENT_GIT_SCOPE_TARGET_SHA="$target_sha"
  AGENT_GIT_SCOPE_TARGET_SHORT_SHA="$(git rev-parse --short "$target_ref")"
  AGENT_GIT_SCOPE_BASE_REF="$base_ref"
  AGENT_GIT_SCOPE_BASE_SHA="$base_sha"
  AGENT_GIT_SCOPE_MERGE_BASE="$merge_base"
}

agent_git_scope_write_snapshot() {
  [[ "$#" -eq 2 ]] || {
    printf 'agent_git_scope_write_snapshot expects output directory and include-worktree flag.\n' >&2
    return 2
  }
  local output_dir="$1"
  local include_worktree="$2"
  [[ -n "${AGENT_GIT_SCOPE_TARGET_REF:-}" && -n "${AGENT_GIT_SCOPE_MERGE_BASE:-}" ]] || {
    printf 'Git scope must be resolved before writing a snapshot.\n' >&2
    return 2
  }
  case "$include_worktree" in
    0|1) ;;
    *)
      printf 'Git scope include-worktree flag must be 0 or 1.\n' >&2
      return 2
      ;;
  esac

  mkdir -p "$output_dir"
  local status_file="$output_dir/status"
  local committed_paths_file="$output_dir/committed-paths"
  local worktree_paths_file="$output_dir/worktree-paths"
  local changed_paths_file="$output_dir/changed-paths"

  if [[ "$include_worktree" -eq 1 ]]; then
    git status --porcelain=v1 -uall > "$status_file"
    awk '
      function path_from_status(line) {
        path = substr(line, 4)
        sub(/^.* -> /, "", path)
        return path
      }
      NF { print path_from_status($0) }
    ' "$status_file" | sort -u > "$worktree_paths_file"
  else
    : > "$status_file"
    : > "$worktree_paths_file"
  fi

  if [[ -n "$AGENT_GIT_SCOPE_BASE_REF" ]]; then
    git diff --name-only \
      "$AGENT_GIT_SCOPE_MERGE_BASE"..."$AGENT_GIT_SCOPE_TARGET_REF" \
      | awk 'NF' | sort -u > "$committed_paths_file"
  else
    : > "$committed_paths_file"
  fi

  cat "$committed_paths_file" "$worktree_paths_file" \
    | awk 'NF' | sort -u > "$changed_paths_file"
}

agent_git_scope_reset
