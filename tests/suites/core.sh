# Core CLI, version, update, and Codex tests.

test_init_prints_shell_setup() {
  local output

  output="$("$AGENT_RAILS_BIN" init --shell zsh --project /tmp/sample-project --profile /tmp/sample-project.profile)"

  assert_contains "$output" "Agent Rails Init"
  assert_contains "$output" 'export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
  assert_contains "$output" 'alias ar="agent-rails"'
  assert_contains "$output" 'export AGENT_RAILS_PROJECT="/tmp/sample-project"'
  assert_contains "$output" 'export AGENT_RAILS_PROFILE="/tmp/sample-project.profile"'
  assert_contains "$output" 'ar doctor --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE"'
}

test_init_without_project_stays_project_neutral() {
  local output

  output="$(AGENT_RAILS_PROJECT= AGENT_RAILS_PROFILE= "$AGENT_RAILS_BIN" init --shell zsh)"

  assert_contains "$output" 'export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
  assert_not_contains "$output" 'export AGENT_RAILS_PROJECT='
  assert_not_contains "$output" 'export AGENT_RAILS_PROFILE='
  assert_contains "$output" 'cd /path/to/project'
  assert_contains "$output" 'agent-rails setup --tool claude'
  assert_contains "$output" 'agent-rails verify'
}

test_version_command_reads_version_file() {
  local output

  output="$("$AGENT_RAILS_BIN" --version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"

  output="$("$AGENT_RAILS_BIN" version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"
}

test_plugin_manifests_match_version_file() {
  assert_file_contains "$ROOT_DIR/.codex-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
  assert_file_contains "$ROOT_DIR/.claude-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
  assert_file_contains "$ROOT_DIR/codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
}

test_changelog_contains_version_file() {
  assert_file_contains "$ROOT_DIR/CHANGELOG.md" "## $EXPECTED_AGENT_RAILS_VERSION"
}

test_update_dry_run_sequences_project_refresh() {
  local repo="$TMP_ROOT/update-dry-run"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --skip-pull --skip-tests --dry-run --session-hook)"

  assert_contains "$output" "Agent Rails Update"
  assert_contains "$output" "Skip git pull (--skip-pull)."
  assert_contains "$output" "Skip tests (--skip-tests)."
  assert_contains "$output" "Run pre-upgrade doctor"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN doctor --project"
  assert_contains "$output" "Refresh target adapter and skills"
  assert_contains "$output" "agent-install-claude.sh"
  assert_not_contains "$output" "agent-install-claude.sh --force"
  assert_contains "$output" "--session-hook"
  assert_contains "$output" "Run final doctor"
}

test_update_falls_back_from_missing_legacy_kit_profile() {
  local repo="$TMP_ROOT/update-legacy-profile"
  local legacy_profile="$ROOT_DIR/profiles/__missing_legacy_profile_for_test__.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  assert_file_not_exists "$legacy_profile"

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --profile "$legacy_profile" --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Profile: $ROOT_DIR/profiles/default.profile"
  assert_not_contains "$output" "$legacy_profile"
  assert_contains "$output" "Refresh target adapter and skills"
}

test_upgrade_self_alias_uses_update_flow() {
  local repo="$TMP_ROOT/upgrade-self"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" upgrade self --project "$repo" --skip-pull --skip-tests --skip-doctor --skip-adapter --dry-run)"

  assert_contains "$output" "Agent Rails Update"
  assert_contains "$output" "Skip pre-upgrade doctor (--skip-doctor)."
  assert_contains "$output" "Skip adapter upgrade (--skip-adapter)."
  assert_contains "$output" "Agent Rails update complete."
}

test_codex_install_and_uninstall_dry_run() {
  local repo="$TMP_ROOT/codex-install"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" codex install --project "$repo" --fix-project --dry-run)"

  assert_contains "$output" "Agent Rails Codex Install"
  assert_contains "$output" "codex plugin marketplace add"
  assert_contains "$output" "codex-marketplace"
  assert_contains "$output" "codex plugin add agent-rails@agent-rails-local"
  assert_contains "$output" "doctor --project"
  assert_contains "$output" "--fix"
  assert_contains "$output" "Open a new Codex thread"

  output="$("$AGENT_RAILS_BIN" codex uninstall --dry-run)"
  assert_contains "$output" "Agent Rails Codex Uninstall"
  assert_contains "$output" "codex plugin remove agent-rails@agent-rails-local"
}

test_setup_claude_dry_run_uses_local_adapter_and_doctor() {
  local repo="$TMP_ROOT/setup-claude"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  output="$("$AGENT_RAILS_BIN" setup --project "$repo" --tool claude --dry-run)"

  assert_contains "$output" "Agent Rails Setup"
  assert_contains "$output" "Tool: claude"
  assert_contains "$output" "Claude adapter ready"
  assert_contains "$output" "Session Hook:"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN doctor --project"
  assert_contains "$output" "Agent Rails setup complete."
  assert_file_not_exists "$repo/.claude/AGENT_RAILS.md"
}

test_setup_auto_detects_single_tool() {
  local repo="$TMP_ROOT/setup-auto-single"
  local fake_bin="$TMP_ROOT/setup-auto-single-bin"
  local output
  mkdir -p "$repo" "$fake_bin"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '#!/usr/bin/env bash\nexit 0\n' > "$fake_bin/opencode"
  chmod +x "$fake_bin/opencode"

  output="$(PATH="$fake_bin:/usr/bin:/bin" "$AGENT_RAILS_BIN" setup --project "$repo" --dry-run)"

  assert_contains "$output" "Detected tool: opencode"
  assert_contains "$output" "Tool: opencode"
  assert_contains "$output" "Agent Rails opencode Install"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN opencode doctor --project"
}

test_setup_auto_requires_choice_for_multiple_tools() {
  local repo="$TMP_ROOT/setup-auto-multiple"
  local fake_bin="$TMP_ROOT/setup-auto-multiple-bin"
  local output
  mkdir -p "$repo" "$fake_bin"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '#!/usr/bin/env bash\nexit 0\n' > "$fake_bin/claude"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$fake_bin/codex"
  chmod +x "$fake_bin/claude" "$fake_bin/codex"

  if output="$(PATH="$fake_bin:/usr/bin:/bin" "$AGENT_RAILS_BIN" setup --project "$repo" --dry-run 2>&1)"; then
    printf 'Expected setup auto-detection to require an explicit choice.\n' >&2
    return 1
  fi

  assert_contains "$output" "Multiple supported tools detected: claude, codex"
  assert_contains "$output" "Choose one with --tool"
  assert_contains "$output" "--tool all"
}

run_core_tests() {
  run_test test_init_prints_shell_setup "init prints shell setup"
  run_test test_init_without_project_stays_project_neutral "init stays project-neutral by default"
  run_test test_version_command_reads_version_file "version command reads VERSION"
  run_test test_plugin_manifests_match_version_file "plugin manifests match VERSION"
  run_test test_changelog_contains_version_file "changelog contains VERSION"
  run_test test_update_dry_run_sequences_project_refresh "update dry-run sequences project refresh"
  run_test test_update_falls_back_from_missing_legacy_kit_profile "update falls back from missing legacy kit profile"
  run_test test_upgrade_self_alias_uses_update_flow "upgrade self alias uses update flow"
  run_test test_codex_install_and_uninstall_dry_run "codex install/uninstall dry-run"
  run_test test_setup_claude_dry_run_uses_local_adapter_and_doctor "setup configures Claude and plans doctor"
  run_test test_setup_auto_detects_single_tool "setup auto-detects one tool"
  run_test test_setup_auto_requires_choice_for_multiple_tools "setup requires a choice for multiple tools"
}
