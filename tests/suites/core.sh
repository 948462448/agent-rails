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

test_upgrade_self_only_skips_project_refresh() {
  local output

  output="$(cd "$TMP_ROOT" && "$AGENT_RAILS_BIN" upgrade self --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Agent Rails Update"
  assert_contains "$output" "Mode: self"
  assert_contains "$output" "Skip git pull (--skip-pull)."
  assert_contains "$output" "Skip tests (--skip-tests)."
  assert_not_contains "$output" "Profile not found"
  assert_not_contains "$output" "Run pre-upgrade doctor"
  assert_not_contains "$output" "Refresh target adapter and skills"
  assert_contains "$output" "Agent Rails update complete."
}

prepare_release_fixture() {
  RELEASE_FIXTURE_DIST="$TMP_ROOT/release-dist"
  RELEASE_FIXTURE_SERVER="$TMP_ROOT/release-server"

  if [[ ! -f "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz" ]]; then
    bash "$ROOT_DIR/scripts/build-release.sh" --output "$RELEASE_FIXTURE_DIST" --include-worktree >/dev/null
  fi

  mkdir -p "$RELEASE_FIXTURE_SERVER/releases/download/v$EXPECTED_AGENT_RAILS_VERSION"
  cp "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz" \
    "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz.sha256" \
    "$RELEASE_FIXTURE_SERVER/releases/download/v$EXPECTED_AGENT_RAILS_VERSION/"
}

test_release_build_creates_installable_assets() {
  local listing

  prepare_release_fixture

  assert_file_exists "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz"
  assert_file_exists "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz.sha256"
  assert_file_exists "$RELEASE_FIXTURE_DIST/install.sh"
  listing="$(tar -tzf "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz")"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/bin/agent-rails"

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$RELEASE_FIXTURE_DIST" && sha256sum -c agent-rails.tar.gz.sha256 >/dev/null)
  else
    (cd "$RELEASE_FIXTURE_DIST" && shasum -a 256 -c agent-rails.tar.gz.sha256 >/dev/null)
  fi
}

test_release_installer_supports_non_git_self_upgrade() {
  local install_root="$TMP_ROOT/release-install"
  local bin_dir="$TMP_ROOT/release-bin"
  local current_physical home_physical output

  prepare_release_fixture

  output="$(AGENT_RAILS_RELEASE_BASE_URL="file://$RELEASE_FIXTURE_SERVER" \
    bash "$ROOT_DIR/scripts/agent-release-install.sh" \
      --version "$EXPECTED_AGENT_RAILS_VERSION" \
      --install-root "$install_root" \
      --bin-dir "$bin_dir")"

  assert_contains "$output" "Installed Agent Rails $EXPECTED_AGENT_RAILS_VERSION"
  assert_file_exists "$install_root/releases/$EXPECTED_AGENT_RAILS_VERSION/VERSION"
  assert_file_contains "$install_root/release-repository" "948462448/agent-rails"
  assert_file_contains "$install_root/release-bin-dir" "$bin_dir"
  assert_file_not_exists "$install_root/releases/$EXPECTED_AGENT_RAILS_VERSION/.git"
  [[ -L "$install_root/current" ]] || { printf 'Expected current to be a symlink.\n' >&2; return 1; }
  [[ -L "$bin_dir/agent-rails" ]] || { printf 'Expected CLI entrypoint to be a symlink.\n' >&2; return 1; }

  output="$("$bin_dir/agent-rails" --version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"
  output="$("$bin_dir/agent-rails" home)"
  home_physical="$(cd "$output" && pwd -P)"
  current_physical="$(cd "$install_root/current" && pwd -P)"
  [[ "$home_physical" == "$current_physical" ]] || {
    printf 'Expected CLI home to resolve through the current release.\n' >&2
    return 1
  }

  output="$(AGENT_RAILS_RELEASE_BASE_URL="file://$RELEASE_FIXTURE_SERVER" \
    "$bin_dir/agent-rails" upgrade self --version "$EXPECTED_AGENT_RAILS_VERSION" --skip-tests)"
  assert_contains "$output" "Mode: self"
  assert_contains "$output" "Agent Rails $EXPECTED_AGENT_RAILS_VERSION is already installed."
  assert_not_contains "$output" "not a git repository"
  assert_not_contains "$output" "Profile not found"
}

test_release_self_upgrade_switches_to_new_version() {
  local install_root="$TMP_ROOT/release-upgrade-install"
  local bin_dir="$TMP_ROOT/release-upgrade-bin"
  local next_version="$EXPECTED_AGENT_RAILS_VERSION-next"
  local next_workspace="$TMP_ROOT/release-next-workspace"
  local next_release_dir="$RELEASE_FIXTURE_SERVER/releases/download/v$next_version"
  local checksum output

  prepare_release_fixture
  AGENT_RAILS_RELEASE_BASE_URL="file://$RELEASE_FIXTURE_SERVER" \
    bash "$ROOT_DIR/scripts/agent-release-install.sh" \
      --version "$EXPECTED_AGENT_RAILS_VERSION" \
      --install-root "$install_root" \
      --bin-dir "$bin_dir" >/dev/null

  mkdir -p "$next_workspace" "$next_release_dir"
  tar -xzf "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz" -C "$next_workspace"
  mv "$next_workspace/agent-rails-$EXPECTED_AGENT_RAILS_VERSION" \
    "$next_workspace/agent-rails-$next_version"
  printf '%s\n' "$next_version" > "$next_workspace/agent-rails-$next_version/VERSION"
  tar -czf "$next_release_dir/agent-rails.tar.gz" \
    -C "$next_workspace" "agent-rails-$next_version"
  if command -v sha256sum >/dev/null 2>&1; then
    checksum="$(sha256sum "$next_release_dir/agent-rails.tar.gz" | awk '{print $1}')"
  else
    checksum="$(shasum -a 256 "$next_release_dir/agent-rails.tar.gz" | awk '{print $1}')"
  fi
  printf '%s  agent-rails.tar.gz\n' "$checksum" > "$next_release_dir/agent-rails.tar.gz.sha256"

  output="$(AGENT_RAILS_RELEASE_BASE_URL="file://$RELEASE_FIXTURE_SERVER" \
    "$bin_dir/agent-rails" upgrade self --version "$next_version" --skip-tests)"

  assert_contains "$output" "Installed Agent Rails $next_version"
  assert_contains "$output" "Continue with Agent Rails $next_version"
  assert_contains "$output" "Agent Rails update complete."
  output="$("$bin_dir/agent-rails" --version)"
  assert_contains "$output" "agent-rails $next_version"
  [[ "$(readlink "$install_root/current")" == "releases/$next_version" ]] || {
    printf 'Expected current to point at releases/%s.\n' "$next_version" >&2
    return 1
  }
}

test_release_installer_rejects_checksum_mismatch() {
  local bad_server="$TMP_ROOT/release-server-bad"
  local install_root="$TMP_ROOT/release-install-bad"
  local bin_dir="$TMP_ROOT/release-bin-bad"
  local release_dir="$bad_server/releases/download/v$EXPECTED_AGENT_RAILS_VERSION"
  local output

  prepare_release_fixture
  mkdir -p "$release_dir"
  cp "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz" \
    "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz.sha256" \
    "$release_dir/"
  printf 'corrupt\n' >> "$release_dir/agent-rails.tar.gz"

  if output="$(AGENT_RAILS_RELEASE_BASE_URL="file://$bad_server" \
    bash "$ROOT_DIR/scripts/agent-release-install.sh" \
      --version "$EXPECTED_AGENT_RAILS_VERSION" \
      --install-root "$install_root" \
      --bin-dir "$bin_dir" 2>&1)"; then
    printf 'Expected release installer to reject a checksum mismatch.\n' >&2
    return 1
  fi

  assert_contains "$output" "Checksum mismatch"
  assert_file_not_exists "$install_root/current"
  assert_file_not_exists "$bin_dir/agent-rails"
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
  run_test test_upgrade_self_only_skips_project_refresh "upgrade self skips project refresh"
  run_test test_release_build_creates_installable_assets "release build creates installable assets"
  run_test test_release_installer_supports_non_git_self_upgrade "release install supports non-git self-upgrade"
  run_test test_release_self_upgrade_switches_to_new_version "release self-upgrade switches versions"
  run_test test_release_installer_rejects_checksum_mismatch "release installer rejects checksum mismatch"
  run_test test_codex_install_and_uninstall_dry_run "codex install/uninstall dry-run"
  run_test test_setup_claude_dry_run_uses_local_adapter_and_doctor "setup configures Claude and plans doctor"
  run_test test_setup_auto_detects_single_tool "setup auto-detects one tool"
  run_test test_setup_auto_requires_choice_for_multiple_tools "setup requires a choice for multiple tools"
}
