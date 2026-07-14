#!/usr/bin/env bash
# Shared workspace lifecycle for managed local Agent Rails adapter artifacts.

_agent_adapter_workspace_home="${AGENT_RAILS_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
_agent_adapter_workspace_guide_path=""
_agent_adapter_workspace_pack_command_path=""
_agent_adapter_workspace_lite_command_path=""
_agent_adapter_workspace_check_command_path=""
_agent_adapter_workspace_managed_skills_path=""
_agent_adapter_workspace_managed_skill_names=()
_agent_adapter_workspace_managed_skill_count=0
_agent_adapter_workspace_project=""
_agent_adapter_workspace_adapter_dir=""
_agent_adapter_workspace_skills_dir=""
_agent_adapter_workspace_skills_rel_dir=""
_agent_adapter_workspace_dry_run=0
_agent_adapter_workspace_force=0
_agent_adapter_workspace_protect_tracked=0
_agent_adapter_workspace_legacy_adapter=0
_agent_adapter_workspace_is_git_repo=0

agent_adapter_workspace_init() {
  [[ "$#" -eq 5 ]] || {
    printf 'agent_adapter_workspace_init expects five paths.\n' >&2
    return 2
  }

  _agent_adapter_workspace_guide_path="$1"
  _agent_adapter_workspace_pack_command_path="$2"
  _agent_adapter_workspace_lite_command_path="$3"
  _agent_adapter_workspace_check_command_path="$4"
  _agent_adapter_workspace_managed_skills_path="$5"
  _agent_adapter_workspace_managed_skill_names=()
  _agent_adapter_workspace_managed_skill_count=0
}

agent_adapter_workspace_configure() {
  [[ "$#" -eq 6 ]] || {
    printf 'agent_adapter_workspace_configure expects project, skills path, dry-run, force, tracked protection, and legacy mode.\n' >&2
    return 2
  }

  _agent_adapter_workspace_project="$1"
  _agent_adapter_workspace_skills_rel_dir="${2#/}"
  _agent_adapter_workspace_skills_dir="$_agent_adapter_workspace_project/$_agent_adapter_workspace_skills_rel_dir"
  _agent_adapter_workspace_adapter_dir="$(dirname "$_agent_adapter_workspace_managed_skills_path")"
  _agent_adapter_workspace_dry_run="$3"
  _agent_adapter_workspace_force="$4"
  _agent_adapter_workspace_protect_tracked="$5"
  _agent_adapter_workspace_legacy_adapter="$6"
  _agent_adapter_workspace_is_git_repo=0
  if command -v git >/dev/null 2>&1 \
    && git -C "$_agent_adapter_workspace_project" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    _agent_adapter_workspace_is_git_repo=1
  fi
}

agent_adapter_workspace_is_generated_file() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  if grep -Fq '<!-- agent-rails:generated -->' "$path"; then
    return 0
  fi

  case "$path" in
    "$_agent_adapter_workspace_guide_path")
      grep -Fq 'Agent Rails Version:' "$path" \
        && grep -Fq 'Visible session marker protocol' "$path"
      ;;
    "$_agent_adapter_workspace_pack_command_path")
      grep -Fq 'Generate and read the Agent Rails Task Pack' "$path" \
        && grep -Fq 'AGENT RAILS: ON' "$path"
      ;;
    "$_agent_adapter_workspace_lite_command_path")
      grep -Fq 'lite Agent Rails Task Pack' "$path" \
        && grep -Fq -- '--pack-mode lite' "$path"
      ;;
    "$_agent_adapter_workspace_check_command_path")
      grep -Fq 'Agent Rails verification suggestions' "$path" \
        && grep -Fq 'AGENT RAILS: CHECK-ONLY' "$path"
      ;;
    *)
      return 1
      ;;
  esac
}

agent_adapter_workspace_is_valid_managed_skill_name() {
  local skill_name="$1"
  [[ -n "$skill_name" && "$skill_name" != */* && "$skill_name" != *..* \
    && "$skill_name" =~ ^[A-Za-z0-9._-]+$ ]]
}

agent_adapter_workspace_managed_skill_is_recorded() {
  local expected="$1"
  local index skill_name
  for ((index = 0; index < _agent_adapter_workspace_managed_skill_count; index++)); do
    skill_name="${_agent_adapter_workspace_managed_skill_names[$index]}"
    [[ "$skill_name" == "$expected" ]] && return 0
  done
  return 1
}

agent_adapter_workspace_record_managed_skill() {
  local skill_name="$1"
  agent_adapter_workspace_is_valid_managed_skill_name "$skill_name" || return 1
  if ! agent_adapter_workspace_managed_skill_is_recorded "$skill_name"; then
    _agent_adapter_workspace_managed_skill_names+=("$skill_name")
    _agent_adapter_workspace_managed_skill_count=$((_agent_adapter_workspace_managed_skill_count + 1))
  fi
}

agent_adapter_workspace_load_managed_skills() {
  local skill_name
  [[ -f "$_agent_adapter_workspace_managed_skills_path" ]] || return 0
  while IFS= read -r skill_name; do
    [[ -z "$skill_name" ]] && continue
    if agent_adapter_workspace_is_valid_managed_skill_name "$skill_name"; then
      agent_adapter_workspace_record_managed_skill "$skill_name"
    else
      printf 'Ignoring invalid managed skill entry: %s\n' "$skill_name" >&2
    fi
  done < "$_agent_adapter_workspace_managed_skills_path"
}

agent_adapter_workspace_list_managed_skills() {
  local index
  for ((index = 0; index < _agent_adapter_workspace_managed_skill_count; index++)); do
    printf '%s\n' "${_agent_adapter_workspace_managed_skill_names[$index]}"
  done
}

agent_adapter_workspace_is_tracked_file() {
  local path="$1"
  local rel_path
  [[ "$_agent_adapter_workspace_is_git_repo" -eq 1 ]] || return 1
  case "$path" in
    "$_agent_adapter_workspace_project"/*)
      rel_path="${path#"$_agent_adapter_workspace_project"/}"
      ;;
    *)
      return 1
      ;;
  esac
  git -C "$_agent_adapter_workspace_project" ls-files -- "$rel_path" 2>/dev/null \
    | grep -Fxq "$rel_path"
}

agent_adapter_workspace_is_tracked_prefix() {
  local rel_path="$1"
  [[ "$_agent_adapter_workspace_is_git_repo" -eq 1 ]] || return 1
  [[ -n "$(git -C "$_agent_adapter_workspace_project" ls-files -- "$rel_path" 2>/dev/null | sed -n '1p')" ]]
}

agent_adapter_workspace_write_generated_file() {
  local path="$1"
  local content="$2"

  if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
    && "$_agent_adapter_workspace_force" -ne 1 ]] \
    && agent_adapter_workspace_is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi

  if [[ -e "$path" && "$_agent_adapter_workspace_force" -ne 1 ]] \
    && ! agent_adapter_workspace_is_generated_file "$path"; then
    printf 'Keeping unmanaged existing file: %s\n' "$path"
    return 0
  fi

  if [[ -e "$path" && "$_agent_adapter_workspace_force" -ne 1 ]]; then
    printf 'Refreshing Agent Rails-generated %s\n' "$path"
  fi

  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf 'Would write %s\n' "$path"
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  printf '%s\n' "$content" > "$path"
  printf 'Wrote %s\n' "$path"
}

agent_adapter_workspace_write_managed_skills() {
  local tmp_file
  [[ "$_agent_adapter_workspace_managed_skill_count" -gt 0 ]] || return 0

  if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
    && "$_agent_adapter_workspace_force" -ne 1 ]] \
    && agent_adapter_workspace_is_tracked_file "$_agent_adapter_workspace_managed_skills_path"; then
    printf 'Keeping tracked managed skill inventory in local mode: %s\n' \
      "$_agent_adapter_workspace_managed_skills_path"
    return 0
  fi

  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf 'Would write managed skill inventory: %s\n' "$_agent_adapter_workspace_managed_skills_path"
    return 0
  fi

  mkdir -p "$_agent_adapter_workspace_adapter_dir"
  tmp_file="$(mktemp "$_agent_adapter_workspace_managed_skills_path.XXXXXX")"
  printf '%s\n' "${_agent_adapter_workspace_managed_skill_names[@]}" | awk 'NF' | sort -u > "$tmp_file"
  mv "$tmp_file" "$_agent_adapter_workspace_managed_skills_path"
  printf 'Wrote managed skill inventory: %s\n' "$_agent_adapter_workspace_managed_skills_path"
}

agent_adapter_workspace_install_skills() {
  local args=(--dest "$_agent_adapter_workspace_skills_dir")
  local selected_skills=()
  local selected_skill_count=0
  local skill_dir skill_name target_dir
  [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]] && args+=(--dry-run)

  if [[ -d "$_agent_adapter_workspace_home/skills" ]]; then
    while IFS= read -r skill_dir; do
      skill_name="$(basename "$skill_dir")"
      target_dir="$_agent_adapter_workspace_skills_dir/$skill_name"
      if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
        && "$_agent_adapter_workspace_force" -ne 1 ]] \
        && agent_adapter_workspace_is_tracked_prefix \
          "$_agent_adapter_workspace_skills_rel_dir/$skill_name"; then
        printf 'Keeping tracked skill directory in local mode: %s\n' "$target_dir"
      elif [[ -e "$target_dir" && "$_agent_adapter_workspace_force" -ne 1 \
        && "$_agent_adapter_workspace_legacy_adapter" -ne 1 ]] \
        && ! agent_adapter_workspace_managed_skill_is_recorded "$skill_name"; then
        printf 'Keeping unmanaged existing skill directory: %s\n' "$target_dir"
      else
        selected_skills+=("$skill_name")
        selected_skill_count=$((selected_skill_count + 1))
        agent_adapter_workspace_record_managed_skill "$skill_name"
      fi
    done < <(find "$_agent_adapter_workspace_home/skills" -mindepth 1 -maxdepth 1 -type d | sort)
  fi

  if [[ "$selected_skill_count" -eq 0 ]]; then
    printf 'No Agent Rails skills to install.\n'
    return 0
  fi

  args+=("${selected_skills[@]}")
  "$_agent_adapter_workspace_home/scripts/agent-install-skills.sh" "${args[@]}"
}

agent_adapter_workspace_remove_generated_file() {
  local path="$1"
  if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
    && "$_agent_adapter_workspace_force" -ne 1 ]] \
    && agent_adapter_workspace_is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi
  [[ -e "$path" ]] || return 0
  if [[ "$_agent_adapter_workspace_force" -ne 1 ]] \
    && ! agent_adapter_workspace_is_generated_file "$path"; then
    printf 'Keeping unmanaged existing file: %s\n' "$path"
    return 0
  fi
  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf 'Would remove %s\n' "$path"
  else
    rm -f "$path"
    printf 'Removed %s\n' "$path"
  fi
}

agent_adapter_workspace_remove_managed_skills() {
  local index skill_dir skill_name
  local skills_to_remove=()
  local skills_to_remove_count=0
  [[ -d "$_agent_adapter_workspace_skills_dir" ]] || return 0

  if [[ -f "$_agent_adapter_workspace_managed_skills_path" ]]; then
    while IFS= read -r skill_name; do
      skills_to_remove+=("$skill_name")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(agent_adapter_workspace_list_managed_skills)
  elif [[ "$_agent_adapter_workspace_legacy_adapter" -eq 1 \
    && -d "$_agent_adapter_workspace_home/skills" ]]; then
    while IFS= read -r skill_dir; do
      skills_to_remove+=("$(basename "$skill_dir")")
      skills_to_remove_count=$((skills_to_remove_count + 1))
    done < <(find "$_agent_adapter_workspace_home/skills" -mindepth 1 -maxdepth 1 -type d | sort)
  fi

  for ((index = 0; index < skills_to_remove_count; index++)); do
    skill_name="${skills_to_remove[$index]}"
    agent_adapter_workspace_is_valid_managed_skill_name "$skill_name" || continue
    skill_dir="$_agent_adapter_workspace_skills_dir/$skill_name"
    [[ -e "$skill_dir" ]] || continue
    if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
      && "$_agent_adapter_workspace_force" -ne 1 ]] \
      && agent_adapter_workspace_is_tracked_prefix \
        "$_agent_adapter_workspace_skills_rel_dir/$skill_name"; then
      printf 'Keeping tracked skill directory in local mode: %s\n' "$skill_dir"
      continue
    fi
    if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
      printf 'Would remove %s\n' "$skill_dir"
    else
      rm -rf "$skill_dir"
      printf 'Removed %s\n' "$skill_dir"
    fi
  done
}

agent_adapter_workspace_remove_managed_skills_file() {
  local path="$_agent_adapter_workspace_managed_skills_path"
  if [[ "$_agent_adapter_workspace_protect_tracked" -eq 1 \
    && "$_agent_adapter_workspace_force" -ne 1 ]] \
    && agent_adapter_workspace_is_tracked_file "$path"; then
    printf 'Keeping tracked file in local mode: %s\n' "$path"
    return 0
  fi
  [[ -e "$path" ]] || return 0
  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf 'Would remove %s\n' "$path"
  else
    rm -f "$path"
    printf 'Removed %s\n' "$path"
  fi
}

_agent_adapter_workspace_strip_ignore_block() {
  local path="$1"
  local output_path="$2"
  local marker="$3"
  local end_marker="$4"
  local managed_entries=""
  local entry
  shift 4

  for entry in "$@"; do
    [[ -n "$managed_entries" ]] && managed_entries+=$'\034'
    managed_entries+="$entry"
  done

  awk \
    -v marker="$marker" \
    -v end_marker="$end_marker" \
    -v managed_entries="$managed_entries" '
      BEGIN {
        count = split(managed_entries, entries, "\034")
        for (entry_index = 1; entry_index <= count; entry_index++) {
          managed[entries[entry_index]] = 1
        }
      }
      $0 == marker {
        in_managed_block = 1
        next
      }
      in_managed_block && $0 == end_marker {
        in_managed_block = 0
        next
      }
      in_managed_block && managed[$0] {
        next
      }
      in_managed_block {
        in_managed_block = 0
      }
      { print }
    ' "$path" > "$output_path"
}

agent_adapter_workspace_ensure_ignore_block() {
  local path="$1"
  local marker="$2"
  local end_marker="$3"
  local tmp_file entry
  local entries=()
  local cleanup_entries=()
  local append_entry=1
  shift 3
  for entry in "$@"; do
    if [[ "$entry" == "--cleanup-only" ]]; then
      append_entry=0
      continue
    fi
    cleanup_entries+=("$entry")
    [[ "$append_entry" -eq 1 ]] && entries+=("$entry")
  done

  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf 'Would ensure local ignore entries in %s\n' "$path"
    for entry in "${entries[@]}"; do
      printf '  %s\n' "$entry"
    done
    return 0
  fi

  mkdir -p "$(dirname "$path")"
  if [[ -f "$path" ]] && grep -Fxq "$marker" "$path"; then
    tmp_file="$(mktemp "$path.XXXXXX")"
    _agent_adapter_workspace_strip_ignore_block \
      "$path" "$tmp_file" "$marker" "$end_marker" "${cleanup_entries[@]}"
    mv "$tmp_file" "$path"
  fi

  if ! {
    [[ -s "$path" ]] && printf '\n'
    printf '%s\n' "$marker"
    for entry in "${entries[@]}"; do
      printf '%s\n' "$entry"
    done
    printf '%s\n' "$end_marker"
  } >> "$path"; then
    printf 'Failed to update local ignore file: %s\n' "$path" >&2
    return 1
  fi
  printf 'Updated local ignore file: %s\n' "$path"
}

agent_adapter_workspace_remove_ignore_block() {
  local path="$1"
  local marker="$2"
  local end_marker="$3"
  local dry_run_prefix="$4"
  local success_prefix="$5"
  local tmp_file
  local entries=()
  shift 5
  entries=("$@")

  [[ -f "$path" ]] || return 0
  grep -Fxq "$marker" "$path" || return 0
  if [[ "$_agent_adapter_workspace_dry_run" -eq 1 ]]; then
    printf '%s %s\n' "$dry_run_prefix" "$path"
    return 0
  fi

  tmp_file="$(mktemp "$path.XXXXXX")"
  _agent_adapter_workspace_strip_ignore_block \
    "$path" "$tmp_file" "$marker" "$end_marker" "${entries[@]}"
  mv "$tmp_file" "$path"
  printf '%s %s\n' "$success_prefix" "$path"
}
