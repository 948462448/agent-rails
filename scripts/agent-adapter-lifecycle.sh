#!/usr/bin/env bash
# Shared ownership and managed-inventory lifecycle for local Agent Rails adapters.

_agent_adapter_guide_path=""
_agent_adapter_pack_command_path=""
_agent_adapter_lite_command_path=""
_agent_adapter_check_command_path=""
_agent_adapter_managed_skills_path=""
_agent_adapter_managed_skill_names=()
_agent_adapter_managed_skill_count=0

agent_adapter_lifecycle_init() {
  [[ "$#" -eq 5 ]] || {
    printf 'agent_adapter_lifecycle_init expects five paths.\n' >&2
    return 2
  }

  _agent_adapter_guide_path="$1"
  _agent_adapter_pack_command_path="$2"
  _agent_adapter_lite_command_path="$3"
  _agent_adapter_check_command_path="$4"
  _agent_adapter_managed_skills_path="$5"
  _agent_adapter_managed_skill_names=()
  _agent_adapter_managed_skill_count=0
}

agent_adapter_is_generated_file() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  if grep -Fq '<!-- agent-rails:generated -->' "$path"; then
    return 0
  fi

  case "$path" in
    "$_agent_adapter_guide_path")
      grep -Fq 'Agent Rails Version:' "$path" \
        && grep -Fq 'Visible session marker protocol' "$path"
      ;;
    "$_agent_adapter_pack_command_path")
      grep -Fq 'Generate and read the Agent Rails Task Pack' "$path" \
        && grep -Fq 'AGENT RAILS: ON' "$path"
      ;;
    "$_agent_adapter_lite_command_path")
      grep -Fq 'lite Agent Rails Task Pack' "$path" \
        && grep -Fq -- '--pack-mode lite' "$path"
      ;;
    "$_agent_adapter_check_command_path")
      grep -Fq 'Agent Rails verification suggestions' "$path" \
        && grep -Fq 'AGENT RAILS: CHECK-ONLY' "$path"
      ;;
    *)
      return 1
      ;;
  esac
}

agent_adapter_is_valid_managed_skill_name() {
  local skill_name="$1"
  [[ -n "$skill_name" && "$skill_name" != */* && "$skill_name" != *..* \
    && "$skill_name" =~ ^[A-Za-z0-9._-]+$ ]]
}

agent_adapter_managed_skill_is_recorded() {
  local expected="$1"
  local index skill_name
  for ((index = 0; index < _agent_adapter_managed_skill_count; index++)); do
    skill_name="${_agent_adapter_managed_skill_names[$index]}"
    [[ "$skill_name" == "$expected" ]] && return 0
  done
  return 1
}

agent_adapter_record_managed_skill() {
  local skill_name="$1"
  agent_adapter_is_valid_managed_skill_name "$skill_name" || return 1
  if ! agent_adapter_managed_skill_is_recorded "$skill_name"; then
    _agent_adapter_managed_skill_names+=("$skill_name")
    _agent_adapter_managed_skill_count=$((_agent_adapter_managed_skill_count + 1))
  fi
}

agent_adapter_load_managed_skills() {
  local skill_name
  [[ -f "$_agent_adapter_managed_skills_path" ]] || return 0
  while IFS= read -r skill_name; do
    [[ -z "$skill_name" ]] && continue
    if agent_adapter_is_valid_managed_skill_name "$skill_name"; then
      agent_adapter_record_managed_skill "$skill_name"
    else
      printf 'Ignoring invalid managed skill entry: %s\n' "$skill_name" >&2
    fi
  done < "$_agent_adapter_managed_skills_path"
}

agent_adapter_list_managed_skills() {
  local index
  for ((index = 0; index < _agent_adapter_managed_skill_count; index++)); do
    printf '%s\n' "${_agent_adapter_managed_skill_names[$index]}"
  done
}

agent_adapter_write_managed_skills() {
  local adapter_dir="$1"
  local dry_run="$2"
  local tmp_file
  [[ "$_agent_adapter_managed_skill_count" -gt 0 ]] || return 0

  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would write managed skill inventory: %s\n' "$_agent_adapter_managed_skills_path"
    return 0
  fi

  mkdir -p "$adapter_dir"
  tmp_file="$(mktemp "$_agent_adapter_managed_skills_path.XXXXXX")"
  printf '%s\n' "${_agent_adapter_managed_skill_names[@]}" | awk 'NF' | sort -u > "$tmp_file"
  mv "$tmp_file" "$_agent_adapter_managed_skills_path"
  printf 'Wrote managed skill inventory: %s\n' "$_agent_adapter_managed_skills_path"
}
