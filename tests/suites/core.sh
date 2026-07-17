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

test_python_init_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_init_application.py"
}

test_python_skills_install_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_skills_install.py"
}

test_version_command_reads_version_file() {
  local output

  output="$("$AGENT_RAILS_BIN" --version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"

  output="$("$AGENT_RAILS_BIN" version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"
}

test_python_public_cli_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_public_cli.py"
}

test_top_level_shell_is_thin_python_bootstrap() {
  local forbidden line_count
  line_count="$(wc -l < "$ROOT_DIR/bin/agent-rails")"
  if [[ "$line_count" -gt 20 ]]; then
    printf 'Expected top-level Shell bootstrap to stay at or below 20 lines, got %s.\n' \
      "$line_count" >&2
    return 1
  fi
  assert_file_contains "$ROOT_DIR/bin/agent-rails" "python3 -E"
  assert_file_contains "$ROOT_DIR/bin/agent-rails" "agent-python-cli.py\" public"
  assert_file_contains "$ROOT_DIR/bin/agent-rails" "export AGENT_RAILS_HOME="
  for forbidden in \
    "case " "run_in_project" "usage()" "source " "eval " "agent-setup.sh" \
    "agent-run.sh" "agent-verify.sh" "agent-update.sh"; do
    assert_file_not_contains "$ROOT_DIR/bin/agent-rails" "$forbidden"
  done
}

test_top_level_python_bootstrap_ignores_shadow_and_stale_home() {
  local repo="$TMP_ROOT/public-cli-shadow-project"
  local shadow_marker="$TMP_ROOT/public-cli-shadow-marker"
  local output root_physical output_physical
  mkdir -p "$repo"
  install_target_python_shadow_package "$repo"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_HOME="$TMP_ROOT/stale-agent-rails-home" \
    AGENT_RAILS_VERSION="stale-version" \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" home)"

  root_physical="$(cd "$ROOT_DIR" && pwd -P)"
  output_physical="$(cd "$output" && pwd -P)"
  [[ "$output_physical" == "$root_physical" ]] || {
    printf 'Expected top-level CLI to resolve the current kit home.\n' >&2
    return 1
  }
  assert_file_not_exists "$shadow_marker"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_HOME="$TMP_ROOT/stale-agent-rails-home" \
    AGENT_RAILS_VERSION="stale-version" \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" --version)"
  assert_contains "$output" "agent-rails $EXPECTED_AGENT_RAILS_VERSION"
  assert_not_contains "$output" "stale-version"
  assert_file_not_exists "$shadow_marker"
}

test_plugin_manifests_match_version_file() {
  assert_file_contains "$ROOT_DIR/.codex-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
  assert_file_contains "$ROOT_DIR/.claude-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
  assert_file_contains "$ROOT_DIR/codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json" "\"version\": \"$EXPECTED_AGENT_RAILS_VERSION\""
}

test_changelog_contains_version_file() {
  assert_file_contains "$ROOT_DIR/CHANGELOG.md" "## $EXPECTED_AGENT_RAILS_VERSION"
}

prepare_update_repo() {
  local repo="$1"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
}

test_python_update_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_update_application.py"
}

test_update_requires_explicit_tool() {
  local repo="$TMP_ROOT/update-requires-tool"
  local output
  prepare_update_repo "$repo"

  if output="$("$AGENT_RAILS_BIN" update --project "$repo" --skip-pull --skip-tests --dry-run 2>&1)"; then
    printf 'Expected project update to require --tool.\n' >&2
    return 1
  fi

  assert_contains "$output" "--tool is required for agent-rails update"
  assert_contains "$output" "--tool claude, codex, or opencode"
}

test_update_claude_dry_run_sequences_project_refresh() {
  local repo="$TMP_ROOT/update-claude-dry-run"
  local output
  prepare_update_repo "$repo"

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --tool claude --skip-pull --skip-tests --dry-run --session-hook)"

  assert_contains "$output" "Agent Rails Update"
  assert_contains "$output" "Tool: claude"
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    assert_contains "$output" "Skip git pull (--skip-pull)."
  else
    assert_contains "$output" "Skip release download (--skip-pull)."
  fi
  assert_contains "$output" "Skip tests (--skip-tests)."
  assert_contains "$output" "Run pre-upgrade doctor"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN doctor --project"
  assert_contains "$output" "Refresh target adapter and skills"
  assert_contains "$output" "$AGENT_RAILS_BIN claude install --project"
  assert_not_contains "$output" "agent-install-claude.sh"
  assert_contains "$output" "--session-hook"
  assert_contains "$output" "Run final doctor"
}

test_update_codex_uses_codex_install_and_doctor() {
  local repo="$TMP_ROOT/update-codex-dry-run"
  local output
  prepare_update_repo "$repo"

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --tool codex --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Tool: codex"
  assert_contains "$output" "Adapter mode: local"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN codex doctor --project"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN codex install --project"
  assert_contains "$output" "--fix-project"
  assert_contains "$output" "--mode local"
  assert_not_contains "$output" "claude install"
  assert_not_contains "$output" "opencode install"
}

test_update_opencode_uses_selected_adapter_mode() {
  local repo="$TMP_ROOT/update-opencode-dry-run"
  local output
  prepare_update_repo "$repo"

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --tool opencode --mode project --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Tool: opencode"
  assert_contains "$output" "Adapter mode: project"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN opencode doctor --project"
  assert_contains "$output" "Would run: $AGENT_RAILS_BIN opencode install --project"
  assert_contains "$output" "--mode project"
  assert_not_contains "$output" "claude install"
  assert_not_contains "$output" "codex install"
}

test_update_rejects_claude_hooks_for_other_tools() {
  local repo="$TMP_ROOT/update-tool-options"
  local output
  prepare_update_repo "$repo"

  if output="$("$AGENT_RAILS_BIN" update --project "$repo" --tool opencode --session-hook --skip-pull --skip-tests --dry-run 2>&1)"; then
    printf 'Expected OpenCode update to reject Claude-only options.\n' >&2
    return 1
  fi

  assert_contains "$output" "--session-hook and --global-reminder are only supported with --tool claude"
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

  output="$("$AGENT_RAILS_BIN" update --project "$repo" --profile "$legacy_profile" --tool claude --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Profile: $ROOT_DIR/profiles/default.profile"
  assert_not_contains "$output" "$legacy_profile"
  assert_contains "$output" "Refresh target adapter and skills"
}

test_update_uses_python_target_context_without_loading_profile() {
  local repo="$TMP_ROOT/update-python-target-context"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/update-python-target-context.profile"
  local missing_profile="$TMP_ROOT/update-python-target-context-missing.profile"
  local profile_marker="$TMP_ROOT/update-python-target-context-profile-marker"
  local shadow_marker="$TMP_ROOT/update-python-target-context-shadow-marker"
  local output status
  mkdir -p "$nested"
  repo="$(cd "$repo" && pwd -P)"
  nested="$repo/nested/path"
  git -C "$repo" init -q
  printf '# update Python Target Project Context\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  {
    printf 'touch "%s"\n' "$profile_marker"
    printf 'exit 97\n'
  } > "$profile"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" update \
        --project "$nested" \
        --profile "$profile" \
        --tool claude \
        --skip-pull \
        --skip-tests \
        --dry-run)"

  assert_contains "$output" "Project: $repo"
  assert_contains "$output" "Profile: $profile"
  assert_file_not_exists "$profile_marker"
  assert_file_not_exists "$shadow_marker"

  set +e
  output="$("$AGENT_RAILS_BIN" update \
    --project "$repo" \
    --profile "$missing_profile" \
    --tool claude \
    --skip-pull \
    --skip-tests \
    --dry-run 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Profile not found: $missing_profile"
}

test_upgrade_self_rejects_tool() {
  local output

  if output="$(cd "$TMP_ROOT" && "$AGENT_RAILS_BIN" upgrade self --tool claude --skip-pull --skip-tests --dry-run 2>&1)"; then
    printf 'Expected upgrade self to reject --tool.\n' >&2
    return 1
  fi

  assert_contains "$output" "--tool is not supported by agent-rails upgrade self"
}

test_upgrade_self_only_skips_project_refresh() {
  local output

  output="$(cd "$TMP_ROOT" && "$AGENT_RAILS_BIN" upgrade self --skip-pull --skip-tests --dry-run)"

  assert_contains "$output" "Agent Rails Update"
  assert_contains "$output" "Mode: self"
  if git -C "$ROOT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    assert_contains "$output" "Skip git pull (--skip-pull)."
  else
    assert_contains "$output" "Skip release download (--skip-pull)."
  fi
  assert_contains "$output" "Skip tests (--skip-tests)."
  assert_not_contains "$output" "Profile not found"
  assert_not_contains "$output" "Run pre-upgrade doctor"
  assert_not_contains "$output" "Refresh target adapter and skills"
  assert_contains "$output" "Agent Rails update complete."
}

test_release_update_skips_source_only_test_suite() {
  local release_home="$TMP_ROOT/release-update-home"
  local repo="$TMP_ROOT/release-update-project"
  local marker="$TMP_ROOT/release-update-tests-ran"
  local output
  mkdir -p "$release_home/tests" "$repo"
  cp -R \
    "$ROOT_DIR/bin" \
    "$ROOT_DIR/profiles" \
    "$ROOT_DIR/scripts" \
    "$ROOT_DIR/src" \
    "$release_home/"
  cp "$ROOT_DIR/VERSION" "$release_home/VERSION"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  cat > "$release_home/tests/run.sh" <<SH
#!/usr/bin/env bash
touch "$marker"
exit 99
SH
  chmod +x "$release_home/tests/run.sh"

  if ! output="$(
    env -u AGENT_RAILS_HOME \
      "$release_home/bin/agent-rails" update \
        --project "$repo" \
        --tool opencode \
        --skip-pull \
        --dry-run 2>&1
  )"; then
    printf 'Expected a Release project update to skip the source-only test suite.\n' >&2
    printf 'Actual output:\n%s\n' "$output" >&2
    return 1
  fi

  assert_file_not_exists "$marker"
  assert_contains "$output" "Skip source test suite for verified Release installation."
  assert_contains "$output" "opencode doctor --project"
  assert_contains "$output" "opencode install --project"
  assert_contains "$output" "Agent Rails update complete."
}

prepare_release_fixture() {
  RELEASE_FIXTURE_DIST="$TMP_ROOT/release-dist"
  RELEASE_FIXTURE_SERVER="$TMP_ROOT/release-server"

  if [[ ! -f "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz" ]]; then
    AGENT_RAILS_HOME="$ROOT_DIR" PYTHONDONTWRITEBYTECODE=1 \
      python3 -I "$ROOT_DIR/scripts/agent-python-cli.py" \
        release-build --output "$RELEASE_FIXTURE_DIST" --include-worktree >/dev/null
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
  assert_file_exists "$RELEASE_FIXTURE_DIST/release_install.py"
  listing="$(tar -tzf "$RELEASE_FIXTURE_DIST/agent-rails.tar.gz")"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/bin/agent-rails"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/git/scope.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/security/sensitive_output.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/assembler.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/pack_policy.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/change_evidence.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/project_docs.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/memory_evidence.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/contract_sections.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/pack_renderer.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/pack_application.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/core/private_text.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/memory/suggestion.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/adapters/content.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/adapters/workspace.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/adapters/opencode.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/verification/check_application.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/context/markdown.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/verification/plan.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/git/_runner.py"
  assert_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/src/agent_rails/config/target_project.py"
  assert_not_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/scripts/agent-git-scope.sh"
  assert_not_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/scripts/agent-sensitive-output.sh"
  assert_not_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/scripts/agent-target-project.sh"
  assert_not_contains "$listing" "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/scripts/agent-model-presets.sh"
  for obsolete in \
    agent-check.sh agent-codex.sh agent-context-pack.sh agent-doctor.sh \
    agent-estimate.sh agent-init-profile.sh agent-init-shell.sh \
    agent-install-claude.sh agent-install-skills.sh agent-memory-suggest.sh \
    agent-opencode.sh agent-publish-check.sh agent-run.sh agent-setup.sh \
    agent-uninstall-claude.sh agent-update.sh agent-verify.sh build-release.sh; do
    assert_not_contains "$listing" \
      "agent-rails-$EXPECTED_AGENT_RAILS_VERSION/scripts/$obsolete"
  done

  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$RELEASE_FIXTURE_DIST" && sha256sum -c agent-rails.tar.gz.sha256 >/dev/null)
  else
    (cd "$RELEASE_FIXTURE_DIST" && shasum -a 256 -c agent-rails.tar.gz.sha256 >/dev/null)
  fi
}

test_python_release_install_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_release_install.py"
}

test_python_release_build_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_release_build.py"
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
  local next_release_dir
  local checksum output

  prepare_release_fixture
  next_release_dir="$RELEASE_FIXTURE_SERVER/releases/download/v$next_version"
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
  COPYFILE_DISABLE=1 tar -czf "$next_release_dir/agent-rails.tar.gz" \
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

  output="$("$AGENT_RAILS_BIN" codex install --project "$repo" --fix-project --mode project --dry-run)"

  assert_contains "$output" "Agent Rails Codex Install"
  assert_contains "$output" "codex plugin marketplace add"
  assert_contains "$output" "codex-marketplace"
  assert_contains "$output" "codex plugin add agent-rails@agent-rails-local"
  assert_contains "$output" "doctor --project"
  assert_contains "$output" "--fix"
  assert_contains "$output" "--mode project"
  assert_contains "$output" "Open a new Codex thread"

  output="$("$AGENT_RAILS_BIN" codex uninstall --dry-run)"
  assert_contains "$output" "Agent Rails Codex Uninstall"
  assert_contains "$output" "codex plugin remove agent-rails@agent-rails-local"
}

test_python_codex_adapter_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_codex_adapter.py"
}

test_codex_rejects_action_specific_options() {
  local args output status
  local -a argv
  for args in \
    "doctor --profile /tmp/profile" \
    "doctor --mode local" \
    "doctor --fix-project" \
    "doctor --dry-run" \
    "uninstall --project /tmp/project" \
    "uninstall --profile /tmp/profile" \
    "uninstall --mode project" \
    "uninstall --fix-project"; do
    read -r -a argv <<< "$args"
    set +e
    output="$("$AGENT_RAILS_BIN" codex "${argv[@]}" 2>&1)"
    status=$?
    set -e
    if [[ "$status" -ne 2 ]]; then
      printf 'Expected `agent-rails codex %s` to exit 2, got %s.\n' \
        "$args" "$status" >&2
      return 1
    fi
    assert_contains "$output" "only supported by agent-rails codex"
  done
}

test_codex_preserves_external_command_exit_status() {
  local fake_bin="$TMP_ROOT/codex-external-exit-bin"
  local status
  mkdir -p "$fake_bin"
  cat > "$fake_bin/codex" <<'SH'
#!/usr/bin/env bash
printf 'fake Codex failure\n' >&2
exit 41
SH
  chmod +x "$fake_bin/codex"

  set +e
  PATH="$fake_bin:/usr/bin:/bin" \
    "$AGENT_RAILS_BIN" codex install >/dev/null 2>&1
  status=$?
  set -e
  if [[ "$status" -ne 41 ]]; then
    printf 'Expected Codex child exit 41 to be preserved, got %s.\n' \
      "$status" >&2
    return 1
  fi
}

test_codex_uses_python_target_context_without_loading_profile() {
  local repo="$TMP_ROOT/codex-python-target-context"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/codex-python-target-context.profile"
  local missing_profile="$TMP_ROOT/codex-python-target-context-missing.profile"
  local profile_marker="$TMP_ROOT/codex-python-target-context-profile-marker"
  local shadow_marker="$TMP_ROOT/codex-python-target-context-shadow-marker"
  local missing_project="$TMP_ROOT/codex-python-target-context-missing-project"
  local output status
  mkdir -p "$nested"
  repo="$(cd "$repo" && pwd -P)"
  nested="$repo/nested/path"
  git -C "$repo" init -q
  printf '# Codex Python Target Project Context\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  {
    printf 'touch "%s"\n' "$profile_marker"
    printf 'exit 97\n'
  } > "$profile"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" codex install \
        --project "$nested" \
        --profile "$profile" \
        --dry-run)"

  assert_contains "$output" "Project: $repo"
  assert_file_not_exists "$profile_marker"
  assert_file_not_exists "$shadow_marker"

  output="$("$AGENT_RAILS_BIN" codex install \
    --project "$repo" \
    --profile "$missing_profile" \
    --fix-project \
    --dry-run)"
  assert_contains "$output" "--profile $missing_profile"

  set +e
  output="$("$AGENT_RAILS_BIN" codex install \
    --project "$missing_project" \
    --dry-run 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Project directory not found: $missing_project"
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

test_python_setup_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    python3 "$ROOT_DIR/tests/test_setup_application.py"
}

test_setup_preserves_child_error_events_and_exit_status() {
  local repo="$TMP_ROOT/setup-child-error"
  local fake_bin="$TMP_ROOT/setup-child-error-bin"
  local output rc
  prepare_update_repo "$repo"
  mkdir -p "$fake_bin"
  cat > "$fake_bin/codex" <<'SH'
#!/usr/bin/env bash
printf 'fake Setup child output\n'
printf 'fake Setup child failure\n' >&2
exit 41
SH
  chmod +x "$fake_bin/codex"

  set +e
  output="$(PATH="$fake_bin:/usr/bin:/bin" \
    "$AGENT_RAILS_BIN" setup \
      --project "$repo" \
      --tool codex 2>&1)"
  rc=$?
  set -e

  if [[ "$rc" -ne 41 ]]; then
    printf 'Expected Setup child exit 41 to be preserved, got %s.\n' "$rc" >&2
    return 1
  fi
  assert_contains "$output" "Agent Rails Setup"
  assert_contains "$output" "Tool: codex"
  assert_contains "$output" "fake Setup child output"
  assert_contains "$output" "fake Setup child failure"
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

test_setup_project_mode_reaches_opencode() {
  local repo="$TMP_ROOT/setup-opencode-project-mode"
  local output
  prepare_update_repo "$repo"

  output="$("$AGENT_RAILS_BIN" setup --project "$repo" --tool opencode --mode project --dry-run)"

  assert_contains "$output" "Mode: project"
  assert_contains "$output" "Agent Rails opencode Install"
  assert_not_contains "$output" "Would ensure local ignore entries"
}

test_test_runner_selects_related_suites() {
  local output

  output="$(bash "$ROOT_DIR/tests/run.sh" --list-related \
    src/agent_rails/core/terminal.py)"
  assert_contains "$output" "core"
  assert_contains "$output" "adapters"
  assert_contains "$output" "workflows"
  assert_contains "$output" "context"

  output="$(bash "$ROOT_DIR/tests/run.sh" --list-related \
    src/agent_rails/context/pack_renderer.py \
    src/agent_rails/adapters/claude.py)"
  [[ "$output" == $'adapters\ncontext' ]]

  output="$(bash "$ROOT_DIR/tests/run.sh" --list-related \
    src/agent_rails/evidence/code.py)"
  [[ "$output" == $'workflows\ncontext' ]]

  output="$(bash "$ROOT_DIR/tests/run.sh" --list-related \
    src/agent_rails/config/target_project.py)"
  [[ "$output" == $'core\nadapters\nworkflows\ncontext' ]]

  output="$(bash "$ROOT_DIR/tests/run.sh" --list-related README.md)"
  [[ -z "$output" ]]
}

test_setup_uses_python_target_context() {
  local repo="$TMP_ROOT/setup-python-target-context"
  local other_repo="$TMP_ROOT/setup-python-target-context-other"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/setup-python-target-context.profile"
  local invalid_profile="$TMP_ROOT/setup-python-target-context-invalid.profile"
  local missing_profile="$TMP_ROOT/setup-python-target-context-missing.profile"
  local profile_marker="$TMP_ROOT/setup-python-target-context-profile-marker"
  local env_file="$TMP_ROOT/setup-python-target-context.env"
  local env_marker="$TMP_ROOT/setup-python-target-context-env-marker"
  local shadow_marker="$TMP_ROOT/setup-python-target-context-shadow-marker"
  local output status
  mkdir -p "$nested" "$other_repo"
  repo="$(cd "$repo" && pwd -P)"
  nested="$repo/nested/path"
  git -C "$repo" init -q
  git -C "$other_repo" init -q
  printf '# Setup Python Target Project Context\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  {
    printf 'source "%s/profiles/default.profile"\n' "$ROOT_DIR"
    printf 'printf "loaded\\n" >> "$SETUP_PROFILE_MARKER"\n'
    printf 'AGENT_RAILS_ENV_FILE="%s"\n' "$env_file"
  } > "$profile"
  {
    printf 'touch "%s"\n' "$env_marker"
    printf 'exit 98\n'
  } > "$env_file"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
    SETUP_PROFILE_MARKER="$profile_marker" \
    GIT_DIR="$other_repo/.git" \
    GIT_WORK_TREE="$other_repo" \
    GIT_COMMON_DIR="$other_repo/.git" \
      "$AGENT_RAILS_BIN" setup \
        --project "$nested" \
        --profile "$profile" \
        --tool claude \
        --dry-run)"

  assert_contains "$output" "Project: $repo"
  assert_contains "$output" "Profile: $profile"
  assert_file_not_exists "$shadow_marker"
  assert_file_not_exists "$env_marker"
  [[ "$(wc -l < "$profile_marker" | tr -d ' ')" -eq 1 ]]

  set +e
  output="$("$AGENT_RAILS_BIN" setup \
    --project "$repo" \
    --profile "$missing_profile" \
    --tool claude \
    --dry-run 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Profile not found: $missing_profile"
  assert_not_contains "$output" "Agent Rails Setup"

  printf 'false\n' > "$invalid_profile"
  set +e
  output="$("$AGENT_RAILS_BIN" setup \
    --project "$repo" \
    --profile "$invalid_profile" \
    --tool claude \
    --dry-run 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Profile could not be sourced: $invalid_profile"
  assert_not_contains "$output" "Agent Rails Setup"

  set +e
  output="$("$AGENT_RAILS_BIN" setup \
    --project "$TMP_ROOT/setup-python-target-context-missing-project" \
    --profile "$profile" \
    --tool claude \
    --dry-run 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Project directory not found: $TMP_ROOT/setup-python-target-context-missing-project"
}

run_core_tests() {
  run_test test_test_runner_selects_related_suites "test runner selects related module suites"
  run_test test_init_prints_shell_setup "init prints shell setup"
  run_test test_init_without_project_stays_project_neutral "init stays project-neutral by default"
  run_test test_python_init_application_module "Python Init Application"
  run_test test_python_skills_install_module "Python Skills Install Application"
  run_test test_version_command_reads_version_file "version command reads VERSION"
  run_test test_python_public_cli_module "Python Public CLI dispatcher"
  run_test test_top_level_shell_is_thin_python_bootstrap "top-level Shell remains a thin Python bootstrap"
  run_test test_top_level_python_bootstrap_ignores_shadow_and_stale_home "top-level Python bootstrap ignores Target Project shadow and stale home"
  run_test test_plugin_manifests_match_version_file "plugin manifests match VERSION"
  run_test test_changelog_contains_version_file "changelog contains VERSION"
  run_test test_python_update_application_module "Python Update Application Service"
  run_test test_update_requires_explicit_tool "update requires an explicit tool"
  run_test test_update_claude_dry_run_sequences_project_refresh "update refreshes Claude with Claude doctor"
  run_test test_update_codex_uses_codex_install_and_doctor "update refreshes Codex with Codex doctor"
  run_test test_update_opencode_uses_selected_adapter_mode "update forwards the selected OpenCode adapter mode"
  run_test test_update_rejects_claude_hooks_for_other_tools "update rejects Claude-only hooks for other tools"
  run_test test_update_falls_back_from_missing_legacy_kit_profile "update falls back from missing legacy kit profile"
  run_test test_update_uses_python_target_context_without_loading_profile "update uses Python Target Project Context without loading Profile"
  run_test test_upgrade_self_rejects_tool "upgrade self rejects project tool selection"
  run_test test_upgrade_self_only_skips_project_refresh "upgrade self skips project refresh"
  run_test test_release_update_skips_source_only_test_suite "release update skips source-only test suite"
  run_test test_release_build_creates_installable_assets "release build creates installable assets"
  run_test test_python_release_build_module "Python Release Build Application"
  run_test test_python_release_install_module "Python Release Install Application"
  run_test test_release_installer_supports_non_git_self_upgrade "release install supports non-git self-upgrade"
  run_test test_release_self_upgrade_switches_to_new_version "release self-upgrade switches versions"
  run_test test_release_installer_rejects_checksum_mismatch "release installer rejects checksum mismatch"
  run_test test_codex_install_and_uninstall_dry_run "codex install/uninstall dry-run"
  run_test test_python_codex_adapter_module "Python Codex Adapter Application"
  run_test test_codex_rejects_action_specific_options "Codex rejects action-specific options"
  run_test test_codex_preserves_external_command_exit_status "Codex preserves external command exit status"
  run_test test_codex_uses_python_target_context_without_loading_profile "Codex uses Python Target Project Context without loading Profile"
  run_test test_python_setup_application_module "Python Setup Application Service"
  run_test test_setup_preserves_child_error_events_and_exit_status "Setup preserves child error events and exit status"
  run_test test_setup_claude_dry_run_uses_local_adapter_and_doctor "setup configures Claude and plans doctor"
  run_test test_setup_auto_detects_single_tool "setup auto-detects one tool"
  run_test test_setup_auto_requires_choice_for_multiple_tools "setup requires a choice for multiple tools"
  run_test test_setup_project_mode_reaches_opencode "setup forwards project mode to OpenCode"
  run_test test_setup_uses_python_target_context "setup uses Python Target Project Context"
}
