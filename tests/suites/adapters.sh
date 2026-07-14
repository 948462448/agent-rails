# Claude and OpenCode Local Adapter lifecycle tests.

test_opencode_install_doctor_and_uninstall() {
  local repo="$TMP_ROOT/opencode-install"
  local repo_abs
  local output
  local exclude_path
  mkdir -p "$repo"
  repo_abs="$(cd "$repo" && pwd -P)"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" opencode install --project "$repo")"

  assert_contains "$output" "Agent Rails opencode Install"
  assert_contains "$output" "opencode adapter ready"
  assert_contains "$output" "Restart opencode"
  assert_file_contains "$repo/.opencode/opencode.json" "\"$repo_abs/.opencode/AGENT_RAILS.md\""
  assert_file_contains "$repo/.opencode/AGENT_RAILS.md" "Visible session marker protocol"
  assert_file_contains "$repo/.opencode/command/agent-rails-pack.md" '$ARGUMENTS'
  assert_file_contains "$repo/.opencode/command/agent-rails-lite.md" "--pack-mode lite"
  assert_file_contains "$repo/.opencode/command/agent-rails-check.md" "CHECK-ONLY"
  assert_file_contains "$repo/.opencode/skills/agent-context-pack/SKILL.md" "agent-context-pack"

  exclude_path="$(git -C "$repo" rev-parse --git-path info/exclude)"
  case "$exclude_path" in
    /*) ;;
    *) exclude_path="$repo/$exclude_path" ;;
  esac
  assert_file_contains "$exclude_path" ".opencode/opencode.json"
  assert_file_contains "$exclude_path" ".opencode/skills/agent-*/"

  output="$("$AGENT_RAILS_BIN" opencode doctor --project "$repo")"
  assert_contains "$output" "Agent Rails opencode Doctor"
  assert_contains "$output" "[OK] opencode Agent Rails guide"
  assert_contains "$output" "[OK] opencode config loads Agent Rails instructions"

  mkdir -p "$repo/.opencode/skills/agent-custom"
  printf 'user-owned\n' > "$repo/.opencode/skills/agent-custom/SKILL.md"

  output="$("$AGENT_RAILS_BIN" opencode uninstall --project "$repo" --dry-run)"
  assert_contains "$output" "Agent Rails opencode Uninstall"
  assert_contains "$output" "Would remove Agent Rails instructions"
  assert_contains "$output" "Would remove $repo_abs/.opencode/AGENT_RAILS.md"

  "$AGENT_RAILS_BIN" opencode uninstall --project "$repo" >/dev/null
  assert_file_not_exists "$repo/.opencode/opencode.json"
  assert_file_not_exists "$repo/.opencode/AGENT_RAILS.md"
  assert_file_not_exists "$repo/.opencode/command/agent-rails-pack.md"
  assert_file_not_exists "$repo/.opencode/skills/agent-context-pack"
  assert_file_exists "$repo/.opencode/skills/agent-custom/SKILL.md"
  assert_file_contains "$repo/.opencode/skills/agent-custom/SKILL.md" "user-owned"
}

test_managed_adapter_workspace_module_contract() {
  local project_dir="$TMP_ROOT/managed-adapter-workspace-module"
  local adapter_dir="$project_dir/.adapter"
  local guide_path="$adapter_dir/AGENT_RAILS.md"
  local pack_path="$adapter_dir/command/agent-rails-pack.md"
  local lite_path="$adapter_dir/command/agent-rails-lite.md"
  local check_path="$adapter_dir/command/agent-rails-check.md"
  local inventory_path="$adapter_dir/.agent-rails-managed-skills"
  local unmanaged_path="$adapter_dir/command/unmanaged.md"
  local ignore_path="$project_dir/.git/info/exclude"
  local listed_skills output
  mkdir -p "$adapter_dir/command" "$adapter_dir/skills/agent-check"
  git -C "$project_dir" init -q

  # shellcheck source=scripts/agent-adapter-workspace.sh
  if [[ ! -f "$ROOT_DIR/scripts/agent-adapter-workspace.sh" ]]; then
    printf 'Missing Managed Adapter Workspace Module.\n' >&2
    return 1
  fi
  source "$ROOT_DIR/scripts/agent-adapter-workspace.sh" || return 1
  agent_adapter_workspace_init \
    "$guide_path" \
    "$pack_path" \
    "$lite_path" \
    "$check_path" \
    "$inventory_path"

  printf 'team-owned\n' > "$guide_path"
  printf 'team-owned-skill\n' > "$adapter_dir/skills/agent-check/SKILL.md"
  git -C "$project_dir" add \
    .adapter/AGENT_RAILS.md \
    .adapter/skills/agent-check/SKILL.md
  git_commit "$project_dir" init

  printf 'Generate and read the Agent Rails Task Pack\nAGENT RAILS: ON\n' > "$pack_path"
  printf '<!-- agent-rails:generated -->\nstale\n' > "$lite_path"
  printf 'user-owned\n' > "$unmanaged_path"
  agent_adapter_workspace_is_generated_file "$pack_path"
  agent_adapter_workspace_is_generated_file "$lite_path"
  if agent_adapter_workspace_is_generated_file "$unmanaged_path"; then
    printf 'Expected unmanaged file not to be recognized as generated: %s\n' "$unmanaged_path" >&2
    exit 1
  fi

  {
    printf 'agent-context-pack\n'
    printf 'agent-context-pack\n'
    printf '../invalid\n'
    printf 'agent-check\n'
  } > "$inventory_path"
  agent_adapter_workspace_load_managed_skills 2>/dev/null
  agent_adapter_workspace_record_managed_skill "agent-check"
  agent_adapter_workspace_record_managed_skill "agent-release"
  listed_skills="$(agent_adapter_workspace_list_managed_skills)"
  assert_contains "$listed_skills" "agent-context-pack"
  assert_contains "$listed_skills" "agent-release"

  agent_adapter_workspace_configure "$project_dir" ".adapter/skills" 0 0 1 0

  output="$(agent_adapter_workspace_write_generated_file "$guide_path" "replacement")"
  assert_contains "$output" "Keeping tracked file in local mode"
  assert_file_contains "$guide_path" "team-owned"

  output="$(agent_adapter_workspace_write_generated_file "$unmanaged_path" "replacement")"
  assert_contains "$output" "Keeping unmanaged existing file"
  assert_file_contains "$unmanaged_path" "user-owned"

  agent_adapter_workspace_write_generated_file \
    "$lite_path" \
    $'<!-- agent-rails:generated -->\nfresh' >/dev/null
  assert_file_contains "$lite_path" "fresh"
  assert_file_not_contains "$lite_path" "stale"

  agent_adapter_workspace_install_skills >/dev/null
  agent_adapter_workspace_write_managed_skills >/dev/null
  assert_file_contains "$adapter_dir/skills/agent-check/SKILL.md" "team-owned-skill"
  assert_file_contains "$inventory_path" "agent-context-pack"
  assert_file_contains "$inventory_path" "agent-check"
  assert_file_contains "$inventory_path" "agent-release"
  assert_file_not_contains "$inventory_path" "../invalid"
  if [[ "$(grep -Fxc 'agent-context-pack' "$inventory_path")" -ne 1 ]]; then
    printf 'Expected managed skill inventory to be de-duplicated.\n' >&2
    exit 1
  fi

  printf 'user-ignore\n' >> "$ignore_path"
  agent_adapter_workspace_ensure_ignore_block \
    "$ignore_path" \
    "# Agent Rails test adapter" \
    "# Agent Rails test adapter end" \
    ".adapter/AGENT_RAILS.md" \
    ".adapter/skills/agent-*/" >/dev/null
  agent_adapter_workspace_ensure_ignore_block \
    "$ignore_path" \
    "# Agent Rails test adapter" \
    "# Agent Rails test adapter end" \
    ".adapter/AGENT_RAILS.md" \
    ".adapter/skills/agent-*/" >/dev/null
  assert_file_contains "$ignore_path" "user-ignore"
  if [[ "$(grep -Fxc '# Agent Rails test adapter' "$ignore_path")" -ne 1 ]]; then
    printf 'Expected local ignore block to be idempotent.\n' >&2
    exit 1
  fi

  agent_adapter_workspace_remove_generated_file "$lite_path" >/dev/null
  agent_adapter_workspace_remove_managed_skills >/dev/null
  agent_adapter_workspace_remove_managed_skills_file >/dev/null
  agent_adapter_workspace_remove_ignore_block \
    "$ignore_path" \
    "# Agent Rails test adapter" \
    "# Agent Rails test adapter end" \
    "Would remove test ignore block from" \
    "Removed test ignore block from" \
    ".adapter/AGENT_RAILS.md" \
    ".adapter/skills/agent-*/" >/dev/null
  assert_file_not_exists "$lite_path"
  assert_file_not_exists "$adapter_dir/skills/agent-context-pack"
  assert_file_contains "$adapter_dir/skills/agent-check/SKILL.md" "team-owned-skill"
  assert_file_not_exists "$inventory_path"
  assert_file_contains "$ignore_path" "user-ignore"
  assert_file_not_contains "$ignore_path" "# Agent Rails test adapter"
}

test_adapter_content_module_contract() {
  local error_output_path="$TMP_ROOT/adapter-content-error.txt"
  local output status

  # shellcheck source=scripts/agent-adapter-content.sh
  source "$ROOT_DIR/scripts/agent-adapter-content.sh"

  agent_adapter_content_init claude "9.9.9" "/kit/bin/agent-rails" "/profiles/demo.profile"
  output="$(agent_adapter_content_render guide)"
  assert_contains "$output" "# Agent Rails"
  assert_contains "$output" "Agent Rails Version: 9.9.9"
  assert_contains "$output" '/kit/bin/agent-rails pack'
  assert_contains "$output" '--profile "/profiles/demo.profile"'

  output="$(agent_adapter_content_render pack)"
  assert_contains "$output" "argument-hint: [goal]"
  assert_not_contains "$output" "agent: build"
  assert_contains "$output" '$ARGUMENTS'

  agent_adapter_content_init opencode "9.9.9" "/kit/bin/agent-rails" "/profiles/demo.profile"
  output="$(agent_adapter_content_render guide)"
  assert_contains "$output" "local opencode adapter"
  output="$(agent_adapter_content_render check)"
  assert_contains "$output" "agent: build"
  assert_not_contains "$output" "argument-hint:"
  assert_contains "$output" "AGENT RAILS: CHECK-ONLY"

  set +e
  output="$(agent_adapter_content_render unknown 2>&1)"
  status=$?
  set -e
  if [[ "$status" -ne 2 ]]; then
    printf 'Expected unknown adapter artifact to exit 2.\n%s\n' "$output" >&2
    exit 1
  fi
  assert_contains "$output" "Unknown Agent Rails adapter artifact"

  set +e
  agent_adapter_content_init unknown "9.9.9" "/kit/bin/agent-rails" "/profiles/demo.profile" \
    > "$error_output_path" 2>&1
  status=$?
  set -e
  output="$(< "$error_output_path")"
  if [[ "$status" -ne 2 ]]; then
    printf 'Expected unknown adapter content type to exit 2.\n%s\n' "$output" >&2
    exit 1
  fi
  assert_contains "$output" "Unknown Agent Rails adapter content type"

  set +e
  output="$(agent_adapter_content_render guide 2>&1)"
  status=$?
  set -e
  if [[ "$status" -ne 2 ]]; then
    printf 'Expected render after failed initialization to exit 2.\n%s\n' "$output" >&2
    exit 1
  fi
  assert_contains "$output" "not initialized"
}

test_adapter_install_preserves_unmanaged_generated_paths() {
  local opencode_repo="$TMP_ROOT/opencode-preserve-unmanaged"
  local claude_repo="$TMP_ROOT/claude-preserve-unmanaged"
  mkdir -p "$opencode_repo/.opencode/command" "$opencode_repo/.opencode/skills/agent-context-pack"
  mkdir -p "$claude_repo/.claude/commands" "$claude_repo/.claude/skills/agent-context-pack"

  git -C "$opencode_repo" init -q
  printf '# temp\n' > "$opencode_repo/README.md"
  printf 'user-owned-opencode-command\n' > "$opencode_repo/.opencode/command/agent-rails-pack.md"
  printf 'user-owned-opencode-skill\n' > "$opencode_repo/.opencode/skills/agent-context-pack/SKILL.md"
  git -C "$opencode_repo" add README.md
  git_commit "$opencode_repo" init

  "$AGENT_RAILS_BIN" opencode install --project "$opencode_repo" >/dev/null
  assert_file_contains "$opencode_repo/.opencode/command/agent-rails-pack.md" "user-owned-opencode-command"
  assert_file_contains "$opencode_repo/.opencode/skills/agent-context-pack/SKILL.md" "user-owned-opencode-skill"

  git -C "$claude_repo" init -q
  printf '# temp\n' > "$claude_repo/README.md"
  printf 'user-owned-claude-command\n' > "$claude_repo/.claude/commands/agent-rails-pack.md"
  printf 'user-owned-claude-skill\n' > "$claude_repo/.claude/skills/agent-context-pack/SKILL.md"
  git -C "$claude_repo" add README.md
  git_commit "$claude_repo" init

  "$AGENT_RAILS_BIN" claude install --project "$claude_repo" --mode local >/dev/null
  assert_file_contains "$claude_repo/.claude/commands/agent-rails-pack.md" "user-owned-claude-command"
  assert_file_contains "$claude_repo/.claude/skills/agent-context-pack/SKILL.md" "user-owned-claude-skill"

  "$AGENT_RAILS_BIN" claude uninstall --project "$claude_repo" >/dev/null
  assert_file_contains "$claude_repo/.claude/commands/agent-rails-pack.md" "user-owned-claude-command"
  assert_file_contains "$claude_repo/.claude/skills/agent-context-pack/SKILL.md" "user-owned-claude-skill"
}

test_opencode_migrates_legacy_adapter_to_managed_inventory() {
  local repo="$TMP_ROOT/opencode-legacy-inventory"
  mkdir -p "$repo/.opencode/skills/agent-context-pack" "$repo/.opencode/skills/agent-custom"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  {
    printf '## Agent Rails\n\n'
    printf 'Agent Rails Version: 0.5.1\n\n'
    printf 'Visible session marker protocol\n'
  } > "$repo/.opencode/AGENT_RAILS.md"
  printf 'legacy-managed-skill\n' > "$repo/.opencode/skills/agent-context-pack/SKILL.md"
  printf 'legacy-user-skill\n' > "$repo/.opencode/skills/agent-custom/SKILL.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" opencode install --project "$repo" >/dev/null
  assert_file_contains "$repo/.opencode/.agent-rails-managed-skills" "agent-context-pack"
  assert_file_not_contains "$repo/.opencode/skills/agent-context-pack/SKILL.md" "legacy-managed-skill"

  "$AGENT_RAILS_BIN" opencode uninstall --project "$repo" >/dev/null
  assert_file_not_exists "$repo/.opencode/skills/agent-context-pack"
  assert_file_contains "$repo/.opencode/skills/agent-custom/SKILL.md" "legacy-user-skill"
}

test_claude_force_replaces_existing_block() {
  local repo="$TMP_ROOT/force-replace"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '<!-- agent-rails:start -->\nOLD BLOCK\n<!-- agent-rails:end -->\n' > "$repo/CLAUDE.md"
  git -C "$repo" add CLAUDE.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode project --force >/dev/null

  assert_file_not_contains "$repo/CLAUDE.md" "OLD BLOCK"
  assert_file_contains "$repo/CLAUDE.md" "AGENT RAILS: ON"
  assert_file_contains "$repo/CLAUDE.md" "Subagent Result Contract"
}

test_claude_install_refresh_and_uninstall() {
  local repo="$TMP_ROOT/claude-refresh-uninstall"
  local claude_user_md="$TMP_ROOT/claude-refresh-CLAUDE.md"
  local claude_settings="$TMP_ROOT/claude-refresh-settings.json"
  local exclude_path
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null
  assert_file_contains "$repo/.claude/.agent-rails-managed-skills" "agent-context-pack"
  assert_file_contains "$repo/.claude/AGENT_RAILS.md" "Visible session marker protocol"
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" "AGENT RAILS: ON"
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" "git rev-parse --show-toplevel"
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" '--project "$project_root"'
  assert_file_not_contains "$repo/.claude/commands/agent-rails-pack.md" "--project \"$repo\""
  assert_file_contains "$repo/.claude/commands/agent-rails-check.md" "AGENT RAILS: CHECK-ONLY"
  assert_file_contains "$repo/.claude/commands/agent-rails-check.md" "git rev-parse --show-toplevel"
  assert_file_contains "$repo/CLAUDE.local.md" "Subagent Result Contract"
  assert_file_contains "$repo/CLAUDE.local.md" "AGENT RAILS: SKIPPED"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "pack-mode lite"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "AGENT RAILS: ON"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "git rev-parse --show-toplevel"
  assert_file_not_exists "$repo/CLAUDE.md"
  printf 'stale guide\n' > "$repo/.claude/AGENT_RAILS.md"
  AGENT_RAILS_CLAUDE_USER_MD="$claude_user_md" AGENT_RAILS_CLAUDE_SETTINGS="$claude_settings" "$AGENT_RAILS_BIN" doctor --project "$repo" --fix >/dev/null
  assert_file_contains "$repo/.claude/AGENT_RAILS.md" "Task Pack"
  assert_file_contains "$repo/CLAUDE.local.md" "Subagent Result Contract"

  "$AGENT_RAILS_BIN" claude uninstall --project "$repo" >/dev/null
  assert_file_not_exists "$repo/.claude/AGENT_RAILS.md"
  assert_file_not_exists "$repo/.claude/commands/agent-rails-pack.md"
  assert_file_not_exists "$repo/.claude/commands/agent-rails-lite.md"
  assert_file_not_exists "$repo/.claude/skills/agent-context-pack"
  assert_file_not_exists "$repo/.claude/.agent-rails-managed-skills"
  assert_file_not_exists "$repo/CLAUDE.local.md"

  exclude_path="$(git -C "$repo" rev-parse --git-path info/exclude)"
  case "$exclude_path" in
    /*) ;;
    *) exclude_path="$repo/$exclude_path" ;;
  esac
  if grep -Fq '# Agent Rails local adapter' "$exclude_path"; then
    printf 'Expected Agent Rails local ignore block to be removed.\n' >&2
    exit 1
  fi
}

test_claude_install_refreshes_generated_adapter_without_force() {
  local repo="$TMP_ROOT/claude-refresh-generated"
  local profile="$TMP_ROOT/claude-refresh-generated.profile"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="profile-refresh"\n'
  } > "$profile"

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null
  "$AGENT_RAILS_BIN" claude install --project "$repo" --profile "$profile" --mode local >/dev/null

  assert_file_contains "$repo/.claude/AGENT_RAILS.md" "--profile \"$profile\""
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" "--profile \"$profile\""
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "--profile \"$profile\""
  assert_file_contains "$repo/.claude/commands/agent-rails-check.md" "--profile \"$profile\""
  assert_file_contains "$repo/CLAUDE.local.md" "--profile \"$profile\""
  assert_file_not_contains "$repo/.claude/AGENT_RAILS.md" "$ROOT_DIR/profiles/default.profile"
}

test_claude_upgrade_alias_is_deprecated() {
  local repo="$TMP_ROOT/claude-upgrade-alias"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" claude upgrade --project "$repo" --mode local 2>&1)"

  assert_contains "$output" "Deprecated: use"
  assert_file_contains "$repo/.claude/AGENT_RAILS.md" "Task Pack"
}

test_claude_local_does_not_touch_tracked_claude_md() {
  local repo="$TMP_ROOT/local-with-team-claude"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# Team Claude Rules\n\nShared team rule.\n' > "$repo/CLAUDE.md"
  git -C "$repo" add CLAUDE.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null

  assert_file_contains "$repo/CLAUDE.md" "Shared team rule."
  assert_file_not_contains "$repo/CLAUDE.md" "agent-rails:start"
  assert_file_contains "$repo/CLAUDE.local.md" "agent-rails:start"
}

test_claude_local_can_write_global_reminder() {
  local repo="$TMP_ROOT/local-global-reminder"
  local home="$TMP_ROOT/home-global-reminder"
  mkdir -p "$repo" "$home"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  HOME="$home" "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local --global-reminder >/dev/null

  assert_file_contains "$repo/CLAUDE.local.md" "agent-rails:start"
  assert_file_not_exists "$repo/CLAUDE.md"
  assert_file_contains "$home/.claude/CLAUDE.md" "agent-rails:global-reminder:start"
  assert_file_contains "$home/.claude/CLAUDE.md" "AGENT RAILS: SKIPPED"
  assert_file_contains "$home/.claude/CLAUDE.md" "local Agent Rails adapter"
  assert_file_contains "$home/.claude/CLAUDE.md" "If neither marker exists"

  HOME="$home" "$AGENT_RAILS_BIN" claude uninstall --project "$repo" --global-reminder >/dev/null

  assert_file_not_exists "$home/.claude/CLAUDE.md"
}

test_claude_local_can_install_session_hook() {
  local repo="$TMP_ROOT/local-session-hook"
  local home="$TMP_ROOT/home-session-hook"
  local output
  mkdir -p "$repo" "$home"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  HOME="$home" "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local --session-hook >/dev/null

  assert_file_contains "$home/.claude/settings.json" "SessionStart"
  assert_file_contains "$home/.claude/settings.json" "agent-rails-session-start.sh"
  assert_file_contains "$home/.claude/settings.json" "Loading Agent Rails..."
  output="$(HOME="$home" "$AGENT_RAILS_BIN" doctor --project "$repo")"
  assert_contains "$output" "Claude SessionStart hook installed"

  HOME="$home" "$AGENT_RAILS_BIN" claude uninstall --project "$repo" --session-hook >/dev/null

  assert_file_not_contains "$home/.claude/settings.json" "agent-rails-session-start.sh"
}

test_session_start_hook_respects_project_marker() {
  local repo="$TMP_ROOT/session-hook-marker"
  local plain_repo="$TMP_ROOT/session-hook-plain"
  local output
  mkdir -p "$repo" "$plain_repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null

  output="$(CLAUDE_PROJECT_DIR="$repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"
  assert_contains "$output" "AGENT RAILS SESSION HOOK ACTIVE"
  assert_contains "$output" "Trigger matrix"
  assert_contains "$output" 'ar="'
  assert_contains "$output" '"$ar" pack'
  assert_contains "$output" "profiles/default.profile"
  assert_contains "$output" "Target scope:"
  assert_contains "$output" "do not reuse this --profile"
  assert_contains "$output" "Base64 and URL encoding are not redaction"
  if [[ "${#output}" -gt 1900 ]]; then
    printf 'Expected compact SessionStart context, got %s characters.\n' "${#output}" >&2
    exit 1
  fi

  output="$(CLAUDE_PROJECT_DIR="$plain_repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"
  if [[ -n "$output" ]]; then
    printf 'Expected hook to stay quiet without an Agent Rails marker.\n%s\n' "$output" >&2
    exit 1
  fi
}

test_session_start_hook_prefers_local_marker_profile() {
  local repo="$TMP_ROOT/session-hook-local-profile"
  local profile="$TMP_ROOT/session-hook-local-profile.profile"
  local output
  mkdir -p "$repo/.claude"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n' > "$profile"
  cat > "$repo/.claude/AGENT_RAILS.md" <<EOF
Visible session marker protocol

$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$ROOT_DIR/profiles/default.profile" "<goal>"
EOF
  cat > "$repo/CLAUDE.local.md" <<EOF
<!-- agent-rails:start -->
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile" "<goal>"
<!-- agent-rails:end -->
EOF

  output="$(CLAUDE_PROJECT_DIR="$repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"

  assert_contains "$output" "--profile \"$profile\""
  assert_not_contains "$output" "$ROOT_DIR/profiles/default.profile"
}

test_session_start_hook_resolves_missing_legacy_kit_profile() {
  local repo="$TMP_ROOT/session-hook-legacy-profile"
  local legacy_profile="$ROOT_DIR/profiles/__missing_legacy_profile_for_test__.profile"
  local output
  mkdir -p "$repo/.claude"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  assert_file_not_exists "$legacy_profile"
  cat > "$repo/.claude/AGENT_RAILS.md" <<EOF
Visible session marker protocol

$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$legacy_profile" "<goal>"
EOF

  output="$(CLAUDE_PROJECT_DIR="$repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"

  assert_contains "$output" "profiles/default.profile"
  assert_not_contains "$output" "$legacy_profile"
}

test_session_start_hook_outputs_codex_json() {
  local repo="$TMP_ROOT/session-hook-codex-json"
  local plugin_data="$TMP_ROOT/plugin-data"
  local output
  mkdir -p "$repo" "$plugin_data"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null

  output="$(PLUGIN_DATA="$plugin_data" CLAUDE_PROJECT_DIR="$repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"
  assert_contains "$output" '"systemMessage":"AGENT RAILS:ON"'
  assert_contains "$output" '"hookEventName":"SessionStart"'
  printf '%s' "$output" | jq -e '.hookSpecificOutput.additionalContext | contains("Trigger matrix")' >/dev/null
  printf '%s' "$output" | jq -e '.hookSpecificOutput.additionalContext | contains("AGENT RAILS: ON")' >/dev/null
}

test_session_start_hook_reads_opencode_marker_profile() {
  local repo="$TMP_ROOT/session-hook-opencode-marker"
  local profile="$TMP_ROOT/session-hook-opencode.profile"
  local output
  mkdir -p "$repo/.opencode"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf 'PROJECT_NAME=opencode-marker\n' > "$profile"
  cat > "$repo/.opencode/AGENT_RAILS.md" <<EOF
## Agent Rails

Visible session marker protocol

\`\`\`bash
$AGENT_RAILS_BIN pack --project "\$project_root" --profile "$profile" "<goal>"
\`\`\`
EOF

  output="$(CLAUDE_PROJECT_DIR="$repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"

  assert_contains "$output" "AGENT RAILS SESSION HOOK ACTIVE"
  assert_contains "$output" "--profile \"$profile\""
}

test_claude_local_allows_tracked_project_claude_files() {
  local repo="$TMP_ROOT/local-with-project-claude-files"
  local exclude_path
  mkdir -p "$repo/.claude/skills/publish"
  git -C "$repo" init -q
  printf '# Team Claude Rules\n\nShared team rule.\n' > "$repo/CLAUDE.md"
  printf '# Publish Skill\n\nTeam-owned skill.\n' > "$repo/.claude/skills/publish/SKILL.md"
  git -C "$repo" add CLAUDE.md .claude/skills/publish/SKILL.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null

  assert_file_contains "$repo/CLAUDE.md" "Shared team rule."
  assert_file_contains "$repo/.claude/skills/publish/SKILL.md" "Team-owned skill."
  assert_file_contains "$repo/CLAUDE.local.md" "agent-rails:start"
  assert_file_contains "$repo/.claude/skills/agent-review/SKILL.md" "agent-review"
  exclude_path="$(git -C "$repo" rev-parse --git-path info/exclude)"
  case "$exclude_path" in
    /*) ;;
    *) exclude_path="$repo/$exclude_path" ;;
  esac
  assert_file_contains "$exclude_path" ".claude/AGENT_RAILS.md"
  assert_file_contains "$exclude_path" ".claude/commands/agent-rails-lite.md"
  assert_file_contains "$exclude_path" ".claude/skills/agent-*/"
  assert_file_contains "$exclude_path" ".agent-rails/"
  if grep -Fxq ".claude/" "$exclude_path"; then
    printf 'Expected local ignore to avoid broad .claude/ ignore.\n' >&2
    sed -n '1,120p' "$exclude_path" >&2
    exit 1
  fi
}

test_claude_local_refreshes_legacy_ignore_block() {
  local repo="$TMP_ROOT/local-refresh-ignore"
  local exclude_path
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  exclude_path="$(git -C "$repo" rev-parse --git-path info/exclude)"
  case "$exclude_path" in
    /*) ;;
    *) exclude_path="$repo/$exclude_path" ;;
  esac
  {
    printf '# Agent Rails local adapter\n'
    printf '.claude/\n'
    printf 'CLAUDE.md\n'
  } >> "$exclude_path"

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null

  assert_file_contains "$exclude_path" "CLAUDE.local.md"
  assert_file_contains "$exclude_path" ".claude/commands/agent-rails-lite.md"
  assert_file_contains "$exclude_path" ".agent-rails/"
  assert_file_not_contains "$exclude_path" "CLAUDE.md"
}

test_doctor_reports_missing_adapter_as_warning() {
  local repo="$TMP_ROOT/doctor-warning"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" doctor --project "$repo")"

  assert_contains "$output" "Claude Adapter"
  assert_contains "$output" "CLAUDE.local.md/CLAUDE.md Agent Rails block is missing"
  assert_contains "$output" "Doctor status: OK with warnings"
}

test_doctor_ok_after_local_install() {
  local repo="$TMP_ROOT/doctor-ok"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null
  output="$("$AGENT_RAILS_BIN" doctor --project "$repo")"

  assert_contains "$output" "Kit version: $EXPECTED_AGENT_RAILS_VERSION"
  assert_contains "$output" "Codex plugin manifest version: $EXPECTED_AGENT_RAILS_VERSION"
  assert_contains "$output" "Claude plugin manifest version: $EXPECTED_AGENT_RAILS_VERSION"
  assert_contains "$output" "Claude adapter version: $EXPECTED_AGENT_RAILS_VERSION"
  assert_contains "$output" "Agent Rails adapter files are ignored locally"
  assert_contains "$output" "skill installed: agent-grill"
  assert_contains "$output" "skill installed: agent-eval"
  assert_contains "$output" "skill installed: agent-refactor"
  assert_contains "$output" "skill installed: agent-tdd"
  assert_contains "$output" "Doctor status: OK"
}

test_doctor_fix_refreshes_stale_adapter_version() {
  local repo="$TMP_ROOT/doctor-fix"
  local claude_user_md="$TMP_ROOT/doctor-fix-CLAUDE.md"
  local claude_settings="$TMP_ROOT/doctor-fix-settings.json"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  AGENT_RAILS_CLAUDE_USER_MD="$claude_user_md" AGENT_RAILS_CLAUDE_SETTINGS="$claude_settings" AGENT_RAILS_VERSION_OVERRIDE=0.1.0 "$AGENT_RAILS_BIN" claude install --project "$repo" --mode local >/dev/null
  output="$(AGENT_RAILS_CLAUDE_USER_MD="$claude_user_md" AGENT_RAILS_CLAUDE_SETTINGS="$claude_settings" "$AGENT_RAILS_BIN" doctor --project "$repo")"
  assert_contains "$output" "Claude adapter version 0.1.0 differs from kit version $EXPECTED_AGENT_RAILS_VERSION"

  output="$(AGENT_RAILS_CLAUDE_USER_MD="$claude_user_md" AGENT_RAILS_CLAUDE_SETTINGS="$claude_settings" "$AGENT_RAILS_BIN" doctor --project "$repo" --fix)"
  assert_contains "$output" "Fixes"
  assert_contains "$output" "Doctor fix completed"

  output="$(AGENT_RAILS_CLAUDE_USER_MD="$claude_user_md" AGENT_RAILS_CLAUDE_SETTINGS="$claude_settings" "$AGENT_RAILS_BIN" doctor --project "$repo")"
  assert_contains "$output" "Claude adapter version: $EXPECTED_AGENT_RAILS_VERSION"
  assert_contains "$output" "Doctor status: OK"
}

test_doctor_openmemory_smoke_dry_run() {
  local repo="$TMP_ROOT/doctor-openmemory-smoke"
  local profile="$TMP_ROOT/openmemory-smoke.profile"
  local request_path="$TMP_ROOT/openmemory-smoke-request.json"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  cat > "$profile" <<PROFILE
source "$ROOT_DIR/profiles/default.profile"
MEMORY_PROVIDER="hybrid"
OPENMEMORY_BASE_URL="https://example.invalid"
OPENMEMORY_MEMORY="agent_rails"
OPENMEMORY_INSTANCE="agent_rails_memory_card"
OPENMEMORY_TOKEN_ENV="OPENMEMORY_ACCESS_KEY"
OPENMEMORY_DRY_RUN_REQUEST="1"
OPENMEMORY_REQUEST_DUMP_PATH="$request_path"
PROFILE

  output="$(OPENMEMORY_ACCESS_KEY=dummy "$AGENT_RAILS_BIN" doctor --project "$repo" --profile "$profile" --openmemory-smoke)"

  assert_contains "$output" "OpenMemory smoke dry-run request written"
  assert_file_contains "$request_path" '"memory": "agent_rails"'
}

run_adapter_foundation_tests() {
  run_test test_opencode_install_doctor_and_uninstall "opencode install/doctor/uninstall"
  run_test test_managed_adapter_workspace_module_contract "managed adapter workspace module contract"
  run_test test_adapter_content_module_contract "shared adapter content module contract"
  run_test test_adapter_install_preserves_unmanaged_generated_paths "adapter install preserves unmanaged generated paths"
  run_test test_opencode_migrates_legacy_adapter_to_managed_inventory "opencode migrates legacy adapter inventory"
}

run_adapter_claude_tests() {
  run_test test_claude_force_replaces_existing_block "claude install --force replaces existing block"
  run_test test_claude_install_refresh_and_uninstall "claude install refresh and uninstall lifecycle"
  run_test test_claude_install_refreshes_generated_adapter_without_force "claude install refreshes generated adapter without force"
  run_test test_claude_upgrade_alias_is_deprecated "claude upgrade alias is deprecated"
  run_test test_claude_local_does_not_touch_tracked_claude_md "claude local leaves tracked CLAUDE.md alone"
  run_test test_claude_local_can_write_global_reminder "claude local can write global reminder"
  run_test test_claude_local_can_install_session_hook "claude local can install session hook"
  run_test test_session_start_hook_respects_project_marker "session start hook respects project marker"
  run_test test_session_start_hook_prefers_local_marker_profile "session start hook prefers local marker profile"
  run_test test_session_start_hook_resolves_missing_legacy_kit_profile "session start hook resolves missing legacy kit profile"
  run_test test_session_start_hook_outputs_codex_json "session start hook outputs Codex JSON"
  run_test test_session_start_hook_reads_opencode_marker_profile "session start hook reads opencode marker profile"
  run_test test_claude_local_allows_tracked_project_claude_files "claude local allows tracked project Claude files"
  run_test test_claude_local_refreshes_legacy_ignore_block "claude local refreshes legacy ignore block"
  run_test test_doctor_reports_missing_adapter_as_warning "doctor reports missing adapter as warning"
  run_test test_doctor_ok_after_local_install "doctor ok after local install"
  run_test test_doctor_fix_refreshes_stale_adapter_version "doctor --fix refreshes stale adapter version"
  run_test test_doctor_openmemory_smoke_dry_run "doctor openmemory smoke dry-run"
}

run_adapter_tests() {
  run_adapter_foundation_tests
  run_adapter_claude_tests
}
