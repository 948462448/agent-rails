#!/usr/bin/env bash
# Shared path conventions for the Agent Rails kit, user config, and project config.

agent_rails_init_paths() {
  AGENT_RAILS_CONFIG_HOME="${AGENT_RAILS_CONFIG_HOME:-$HOME/.agent-rails}"
  AGENT_RAILS_USER_CONFIG_DIR="$AGENT_RAILS_CONFIG_HOME"
  AGENT_RAILS_USER_PROFILE_DIR="$AGENT_RAILS_CONFIG_HOME/profiles/projects"
  export AGENT_RAILS_CONFIG_HOME
  export AGENT_RAILS_USER_CONFIG_DIR
  export AGENT_RAILS_USER_PROFILE_DIR
}

agent_rails_sanitize_slug() {
  printf '%s' "$1" | tr '[:upper:] ' '[:lower:]-' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g'
}

agent_rails_project_worktree_slug() {
  local path="$1"
  local name="${2:-$(basename "$path")}"
  local slug
  local checksum
  slug="$(agent_rails_sanitize_slug "$name")"
  [[ -n "$slug" ]] || slug="project"
  checksum="$(printf '%s' "$path" | cksum | awk '{print $1}')"
  printf '%s-%s\n' "$slug" "$checksum"
}

agent_rails_project_config_dir() {
  printf '%s/.agent-rails\n' "$1"
}

agent_rails_default_task_pack_path() {
  local worktree_slug="$1"
  printf '%s/agent-context/%s-task-pack.md\n' "$AGENT_RAILS_CONFIG_HOME" "$worktree_slug"
}

agent_rails_default_memory_dir() {
  local project_name="$1"
  printf '%s/memory/%s\n' "$AGENT_RAILS_CONFIG_HOME" "$project_name"
}

agent_rails_default_memory_decision_path() {
  local project_name="$1"
  printf '%s/agent-context/%s-memory-decision.md\n' "$AGENT_RAILS_CONFIG_HOME" "$project_name"
}

agent_rails_resolve_profile() {
  local project_abs="$1"
  local project_name="$2"
  local explicit_profile="${3:-}"
  local candidate

  agent_rails_init_paths

  if [[ -n "$explicit_profile" ]]; then
    printf '%s\n' "$explicit_profile"
    return 0
  fi

  for candidate in \
    "$project_abs/.agent-rails/profile" \
    "$project_abs/.agent-rails/profile.sh" \
    "$AGENT_RAILS_CONFIG_HOME/profiles/projects/$project_name.profile" \
    "$AGENT_RAILS_CONFIG_HOME/profiles/$project_name.profile"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  printf '%s/profiles/default.profile\n' "$AGENT_RAILS_HOME"
}
