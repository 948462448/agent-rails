# Check, publish, estimate, run, path, and standalone workflow-tool tests.

test_agent_check_includes_bin_entrypoint() {
  local repo="$TMP_ROOT/check-bin"
  local output
  mkdir -p "$repo/bin"
  git -C "$repo" init -q
  printf '#!/usr/bin/env bash\nprintf "agent rails"\n' > "$repo/bin/agent-rails"
  git -C "$repo" add bin/agent-rails
  git_commit "$repo" init

  printf '\nprintf "changed"\n' >> "$repo/bin/agent-rails"
  output="$("$AGENT_RAILS_BIN" check --project "$repo" --print-only)"

  assert_contains "$output" "AGENT RAILS: CHECK-ONLY"
  assert_contains "$output" "shell entrypoints changed"
  assert_contains "$output" "bash -n bin/agent-rails"
}

test_agent_check_excludes_deleted_shell_from_command() {
  local repo="$TMP_ROOT/check-deleted-shell"
  local base_sha output suggestions target_sha target_suggestions
  mkdir -p "$repo/scripts"
  git -C "$repo" init -q
  printf '#!/usr/bin/env bash\nprintf "keep"\n' > "$repo/scripts/keep.sh"
  printf '#!/usr/bin/env bash\nprintf "delete"\n' > "$repo/scripts/delete.sh"
  git -C "$repo" add scripts/keep.sh scripts/delete.sh
  git_commit "$repo" init
  base_sha="$(git -C "$repo" rev-parse HEAD)"

  printf '\nprintf "changed"\n' >> "$repo/scripts/keep.sh"
  rm -f "$repo/scripts/delete.sh"

  output="$("$AGENT_RAILS_BIN" check --project "$repo" --print-only)"
  suggestions="$("$AGENT_RAILS_BIN" check --project "$repo" --suggestions-only)"

  assert_contains "$output" "- scripts/delete.sh"
  assert_contains "$suggestions" "bash -n scripts/keep.sh"
  assert_not_contains "$suggestions" "scripts/delete.sh"

  printf '#!/usr/bin/env bash\nprintf "target only"\n' > "$repo/scripts/target-only.sh"
  git -C "$repo" add -A
  git_commit "$repo" target
  target_sha="$(git -C "$repo" rev-parse HEAD)"
  git -C "$repo" checkout -q "$base_sha"

  target_suggestions="$(
    "$AGENT_RAILS_BIN" check \
      --project "$repo" \
      --base "$base_sha" \
      --target-ref "$target_sha" \
      --suggestions-only
  )"

  assert_contains "$target_suggestions" "scripts/keep.sh"
  assert_contains "$target_suggestions" "scripts/target-only.sh"
  assert_not_contains "$target_suggestions" "scripts/delete.sh"
}

test_agent_check_suggestions_only_omits_repeated_scope() {
  local repo="$TMP_ROOT/check-suggestions-only"
  local output
  mkdir -p "$repo/bin"
  git -C "$repo" init -q
  printf '#!/usr/bin/env bash\nprintf "agent rails"\n' > "$repo/bin/agent-rails"
  git -C "$repo" add bin/agent-rails
  git_commit "$repo" init
  printf '\nprintf "changed"\n' >> "$repo/bin/agent-rails"

  output="$("$AGENT_RAILS_BIN" check --project "$repo" --suggestions-only)"

  assert_contains "$output" "shell entrypoints changed"
  assert_contains "$output" "bash -n bin/agent-rails"
  assert_not_contains "$output" "AGENT RAILS: CHECK-ONLY"
  assert_not_contains "$output" "Agent check"
  assert_not_contains "$output" "Changed files:"
  assert_not_contains "$output" "Next action suggestions:"

  if "$AGENT_RAILS_BIN" check --project "$repo" --run --suggestions-only >/dev/null 2>&1; then
    printf 'Expected --run and --suggestions-only to be mutually exclusive.\n' >&2
    exit 1
  fi
}

test_agent_check_selects_changed_test_suites() {
  local repo="$TMP_ROOT/check-test-suites"
  local output
  mkdir -p "$repo/tests/suites"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  printf '# adapter tests\n' > "$repo/tests/suites/adapters.sh"
  printf '# context tests\n' > "$repo/tests/suites/context.sh"
  output="$("$AGENT_RAILS_BIN" check --project "$repo" --print-only)"

  assert_contains "$output" "shell tests changed"
  assert_contains "$output" "bash tests/run.sh adapters context"

  mkdir -p "$repo/tests/lib"
  printf '# shared helper\n' > "$repo/tests/lib/test-helpers.sh"
  output="$("$AGENT_RAILS_BIN" check --project "$repo" --print-only)"

  assert_contains "$output" "shell tests changed"
  assert_contains "$output" "bash tests/run.sh"
  assert_not_contains "$output" "bash tests/run.sh adapters context"
}

test_agent_check_run_uses_child_shell() {
  local repo="$TMP_ROOT/check-runner"
  local profile="$TMP_ROOT/check-runner.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '\nchanged\n' >> "$repo/README.md"
  cat > "$profile" <<'PROFILE'
VERIFY_PROJECT='printf "runner-ok\n"'
PROFILE

  output="$(AGENT_RAILS_RUN_SHELL=sh "$AGENT_RAILS_BIN" check --project "$repo" --profile "$profile" --run)"

  assert_contains "$output" "Running suggested commands"
  assert_contains "$output" "runner-ok"
}

test_agent_check_run_target_guard_ignores_inherited_worktree() {
  local repo="$TMP_ROOT/check-target-guard-repo"
  local other_worktree="$TMP_ROOT/check-target-guard-other"
  local profile="$TMP_ROOT/check-target-guard.profile"
  local marker="$TMP_ROOT/check-target-guard-ran"
  local main_sha target_sha inherited_git_dir output status
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# base\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" base
  git -C "$repo" branch -M main
  main_sha="$(git -C "$repo" rev-parse HEAD)"

  git -C "$repo" switch -qc target
  mkdir -p "$repo/backend"
  printf 'print("target")\n' > "$repo/backend/app.py"
  git -C "$repo" add backend/app.py
  git_commit "$repo" target
  target_sha="$(git -C "$repo" rev-parse HEAD)"
  git -C "$repo" switch -q main
  git -C "$repo" worktree add -q "$other_worktree" target
  inherited_git_dir="$(git -C "$other_worktree" rev-parse --absolute-git-dir)"
  [[ "$target_sha" != "$main_sha" ]]
  [[ "$(
    GIT_DIR="$inherited_git_dir" GIT_WORK_TREE="$other_worktree" \
      git rev-parse HEAD
  )" == "$target_sha" ]]

  printf 'VERIFY_BACKEND=\047touch "%s"\047\n' "$marker" > "$profile"

  status=0
  output="$(
    GIT_DIR="$inherited_git_dir" GIT_WORK_TREE="$other_worktree" \
      "$AGENT_RAILS_BIN" check \
        --project "$repo" \
        --profile "$profile" \
        --base "$main_sha" \
        --target-ref "$target_sha" \
        --run 2>&1
  )" || status=$?

  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Cannot --run checks for target ref"
  assert_contains "$output" "checkout is at HEAD ${main_sha:0:12}"
  assert_file_not_exists "$marker"
}

test_pack_and_check_share_python_verification_plan() {
  local repo="$TMP_ROOT/shared-verification-plan"
  local profile="$TMP_ROOT/shared-verification-plan.profile"
  local profile_count="$TMP_ROOT/shared-verification-plan-profile-count"
  local output="$TMP_ROOT/shared-verification-plan.md"
  local check_output suggestion
  mkdir -p "$repo/backend"
  git -C "$repo" init -q
  printf '# shared verification plan\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf 'print("changed")\n' > "$repo/backend/app.py"
  {
    printf 'source "%s/profiles/default.profile"\n' "$ROOT_DIR"
    printf 'count=0\n'
    printf '[[ ! -f "%s" ]] || count="$(cat "%s")"\n' "$profile_count" "$profile_count"
    printf 'printf "%%s\\n" "$((count + 1))" > "%s"\n' "$profile_count"
    printf 'PROJECT_NAME="shared-verification-plan"\n'
    printf 'VERIFY_BACKEND=\047printf "shared-plan-ok\\n"\047\n'
    printf 'VERIFY_PYTHON="$VERIFY_BACKEND"\n'
  } > "$profile"

  check_output="$("$AGENT_RAILS_BIN" check --project "$repo" --profile "$profile" --suggestions-only)"
  suggestion='- [backend changed] printf "shared-plan-ok\n"'
  assert_contains "$check_output" "$suggestion"
  assert_not_contains "$check_output" "python changed"

  printf '0\n' > "$profile_count"
  "$AGENT_RAILS_BIN" pack --project "$repo" --profile "$profile" --output "$output" \
    --pack-mode lite "share one verification plan" >/dev/null

  assert_file_contains "$output" "$suggestion"
  assert_file_not_contains "$output" "python changed"
  [[ "$(cat "$profile_count")" == "1" ]]
}

test_check_and_publish_use_python_target_context() {
  local repo="$TMP_ROOT/check-publish-python-target-context"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/check-publish-python-target-context.profile"
  local missing_profile="$TMP_ROOT/check-publish-python-target-context-missing.profile"
  local profile_count="$TMP_ROOT/check-publish-python-target-context-profile-count"
  local env_file="$TMP_ROOT/check-publish-python-target-context.env"
  local env_marker="$TMP_ROOT/check-publish-python-target-context-env-marker"
  local shadow_marker="$TMP_ROOT/check-publish-python-target-context-shadow-marker"
  local output status
  mkdir -p "$nested"
  repo="$(cd "$repo" && pwd -P)"
  nested="$repo/nested/path"
  git -C "$repo" init -q
  git -C "$repo" branch -M main
  printf '# Check and Publish Python Target Project Context\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  {
    printf 'source "%s/profiles/default.profile"\n' "$ROOT_DIR"
    printf 'count=0\n'
    printf '[[ ! -f "%s" ]] || count="$(cat "%s")"\n' "$profile_count" "$profile_count"
    printf 'printf "%%s\\n" "$((count + 1))" > "%s"\n' "$profile_count"
    printf 'AGENT_RAILS_ENV_FILE="%s"\n' "$env_file"
    printf 'VERIFY_PYTHON=\047printf "custom-check-ok\\n"\047\n'
    printf 'BASE_REF="main"\n'
  } > "$profile"
  {
    printf 'touch "%s"\n' "$env_marker"
    printf 'exit 98\n'
  } > "$env_file"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" check \
        --project "$nested" \
        --profile "$profile" \
        --print-only)"
  assert_contains "$output" "custom-check-ok"
  [[ "$(cat "$profile_count")" -eq 1 ]]
  assert_file_not_exists "$env_marker"
  assert_file_not_exists "$shadow_marker"

  rm -f "$profile_count"
  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
      "$AGENT_RAILS_BIN" publish check \
        --project "$nested" \
        --profile "$profile" \
        --no-secret-scan)"
  assert_contains "$output" "Project: $repo"
  assert_contains "$output" "custom-check-ok"
  [[ "$(cat "$profile_count")" -eq 1 ]]
  assert_file_not_exists "$env_marker"
  assert_file_not_exists "$shadow_marker"

  for command in check publish; do
    set +e
    if [[ "$command" == "check" ]]; then
      output="$("$AGENT_RAILS_BIN" check \
        --project "$repo" \
        --profile "$missing_profile" \
        --print-only 2>&1)"
    else
      output="$("$AGENT_RAILS_BIN" publish check \
        --project "$repo" \
        --profile "$missing_profile" \
        --no-secret-scan 2>&1)"
    fi
    status=$?
    set -e
    [[ "$status" -eq 2 ]]
    assert_contains "$output" "Profile not found: $missing_profile"
  done
}

test_verify_runs_plan_and_can_preview() {
  local repo="$TMP_ROOT/verify-run"
  local output preview
  mkdir -p "$repo/scripts"
  git -C "$repo" init -q
  printf '#!/usr/bin/env bash\nprintf "ok"\n' > "$repo/scripts/verify.sh"
  git -C "$repo" add scripts/verify.sh
  git_commit "$repo" init
  printf '\nprintf "changed"\n' >> "$repo/scripts/verify.sh"

  output="$("$AGENT_RAILS_BIN" verify --project "$repo")"
  preview="$("$AGENT_RAILS_BIN" verify --project "$repo" --print-only)"

  assert_contains "$output" "Agent Rails Verify"
  assert_contains "$output" "Running suggested commands"
  assert_contains "$output" ">>> shell entrypoints changed"
  assert_contains "$output" "Agent Rails verification complete."
  assert_contains "$preview" "Agent Rails Verify"
  assert_contains "$preview" "bash -n scripts/verify.sh"
  assert_not_contains "$preview" "Running suggested commands"
}

test_verify_preserves_child_exit_and_partial_output() {
  local repo="$TMP_ROOT/verify-child-exit"
  local profile="$TMP_ROOT/verify-child-exit.profile"
  local output status
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# verify child exit\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '\nchanged\n' >> "$repo/README.md"
  cat > "$profile" <<'PROFILE'
VERIFY_PROJECT='printf "partial-verification-output\n"; exit 19'
PROFILE

  set +e
  output="$("$AGENT_RAILS_BIN" verify \
    --project "$repo" \
    --profile "$profile" \
    --publish 2>&1)"
  status=$?
  set -e

  [[ "$status" -eq 19 ]]
  assert_contains "$output" "partial-verification-output"
  assert_contains "$output" "Repair Pack"
  assert_contains "$output" "Exit code: 19"
  assert_contains "$output" "First diagnostic:"
  assert_contains "$output" "Next action:"
  assert_not_contains "$output" "Publish readiness"
  assert_not_contains "$output" "verification complete"
}

test_verify_publish_adds_release_check() {
  local repo="$TMP_ROOT/verify-publish"
  local base_sha output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  base_sha="$(git -C "$repo" rev-parse HEAD)"
  printf '\nchanged\n' >> "$repo/README.md"

  output="$({
    "$AGENT_RAILS_BIN" verify \
      --project "$repo" \
      --publish \
      --base "$base_sha" \
      --print-only \
      --no-secret-scan
  })"

  assert_contains "$output" "Agent Rails Verify"
  assert_contains "$output" "Mode: publish"
  assert_contains "$output" "Agent publish check"
  assert_contains "$output" "Disabled by --no-secret-scan"
  assert_contains "$output" "Agent Rails publish verification complete."

  if output="$("$AGENT_RAILS_BIN" verify --project "$repo" --no-secret-scan 2>&1)"; then
    printf 'Expected --no-secret-scan without --publish to fail.\n' >&2
    return 1
  fi
  assert_contains "$output" "--no-secret-scan requires --publish"
}

test_verify_uses_python_target_context() {
  local repo="$TMP_ROOT/verify-python-target-context"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/verify-python-target-context.profile"
  local invalid_profile="$TMP_ROOT/verify-python-target-context-invalid.profile"
  local missing_profile="$TMP_ROOT/verify-python-target-context-missing.profile"
  local profile_marker="$TMP_ROOT/verify-python-target-context-profile-marker"
  local shadow_marker="$TMP_ROOT/verify-python-target-context-shadow-marker"
  local output status
  mkdir -p "$nested"
  repo="$(cd "$repo" && pwd -P)"
  nested="$repo/nested/path"
  git -C "$repo" init -q
  printf '# verify Python Target Project Context\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  {
    printf 'source "%s/profiles/default.profile"\n' "$ROOT_DIR"
    printf 'printf "loaded\\n" >> "$VERIFY_PROFILE_MARKER"\n'
    printf 'VERIFY_PYTHON=\047printf "custom-verification-ok\\n"\047\n'
  } > "$profile"

  output="$(cd "$repo" && \
    PYTHONPATH=. \
    AGENT_RAILS_SHADOW_MARKER="$shadow_marker" \
    VERIFY_PROFILE_MARKER="$profile_marker" \
      "$AGENT_RAILS_BIN" verify \
        --project "$nested" \
        --profile "$profile")"

  assert_contains "$output" "Project: $repo"
  assert_contains "$output" "custom-verification-ok"
  assert_file_not_exists "$shadow_marker"
  [[ "$(wc -l < "$profile_marker" | tr -d ' ')" -eq 1 ]]

  set +e
  output="$("$AGENT_RAILS_BIN" verify \
    --project "$repo" \
    --profile "$missing_profile" \
    --print-only 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Profile not found: $missing_profile"
  assert_not_contains "$output" "Agent Rails Verify"

  printf 'false\n' > "$invalid_profile"
  set +e
  output="$("$AGENT_RAILS_BIN" verify \
    --project "$repo" \
    --profile "$invalid_profile" \
    --print-only 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Profile could not be sourced: $invalid_profile"
  assert_not_contains "$output" "Agent Rails Verify"

  set +e
  output="$("$AGENT_RAILS_BIN" verify \
    --project "$TMP_ROOT/verify-python-target-context-missing-project" \
    --profile "$profile" \
    --print-only 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 1 ]]
  [[ -z "$output" ]]

  set +e
  output="$("$AGENT_RAILS_BIN" verify \
    --project '' \
    --profile "$profile" \
    --print-only 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 1 ]]
  [[ -z "$output" ]]
}

test_publish_check_summarizes_scope_and_redacts_secrets() {
  local repo="$TMP_ROOT/publish-check"
  local unreadable_path output status
  mkdir -p "$repo/scripts"
  git -C "$repo" init -q
  git -C "$repo" branch -M main
  printf '# temp\n' > "$repo/README.md"
  {
    printf '#!/usr/bin/env bash\n'
    printf 'LEGACY_TOKEN=unit-test-historical-secret-123456\n'
    printf 'printf "ok\\n"\n'
  } > "$repo/scripts/run.sh"
  git -C "$repo" add README.md scripts/run.sh
  git_commit "$repo" init

  git -C "$repo" switch -q -c feature
  printf 'COMMITTED_COOKIE=unit-test-committed-secret-123456\n' >> "$repo/scripts/run.sh"
  git -C "$repo" add scripts/run.sh
  git_commit "$repo" committed-secret-fixture
  printf 'DEPLOY_PASSWORD=unit-test-staged-secret-123456\n' >> "$repo/scripts/run.sh"
  git -C "$repo" add scripts/run.sh
  printf 'API_TOKEN=unit-test-unstaged-secret-123456\n' >> "$repo/scripts/run.sh"
  printf 'SERVICE_ACCESS_KEY=super-secret-value\n' > "$repo/.env.local"
  {
    printf 'AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base\n'
    printf 'SERVICE_TOKEN_ENV="${SERVICE_TOKEN_ENV:-SERVICE_ACCESS_KEY}"\n'
  } > "$repo/tokenizer.md"
  printf 'SPACED_API_TOKEN=unit-test-spaced-secret-123456\n' > "$repo/secret file.env"
  printf 'ARROW_API_TOKEN=unit-test-arrow-secret-123456\n' > "$repo/arrow -> secret.env"
  printf 'UNICODE_API_TOKEN=unit-test-unicode-secret-123456\n' > "$repo/秘密.env"
  printf 'DASH_API_TOKEN=unit-test-dash-secret-123456\n' > "$repo/-secret.env"

  output="$("$AGENT_RAILS_BIN" publish check --project "$repo")"

  assert_contains "$output" "AGENT RAILS: CHECK-ONLY (reason=publish"
  assert_contains "$output" "Agent publish check"
  assert_contains "$output" "Staged files (1)"
  assert_contains "$output" "Unstaged files (1)"
  assert_contains "$output" "Untracked files (6)"
  assert_contains "$output" "scripts/run.sh"
  assert_contains "$output" ".env.local"
  assert_contains "$output" "secret file.env"
  assert_contains "$output" "arrow -> secret.env"
  assert_contains "$output" "秘密.env"
  assert_contains "$output" "-secret.env"
  assert_contains "$output" "Potential secret matches found"
  assert_contains "$output" "COMMITTED_COOKIE=<redacted>"
  assert_contains "$output" "DEPLOY_PASSWORD=<redacted>"
  assert_contains "$output" "API_TOKEN=<redacted>"
  assert_contains "$output" "SERVICE_ACCESS_KEY=<redacted>"
  assert_contains "$output" "SPACED_API_TOKEN=<redacted>"
  assert_contains "$output" "ARROW_API_TOKEN=<redacted>"
  assert_contains "$output" "UNICODE_API_TOKEN=<redacted>"
  assert_contains "$output" "DASH_API_TOKEN=<redacted>"
  assert_not_contains "$output" "LEGACY_TOKEN=<redacted>"
  assert_not_contains "$output" "unit-test-historical-secret"
  assert_not_contains "$output" "unit-test-committed-secret"
  assert_not_contains "$output" "unit-test-staged-secret"
  assert_not_contains "$output" "unit-test-unstaged-secret"
  assert_not_contains "$output" "super-secret-value"
  assert_not_contains "$output" "unit-test-spaced-secret"
  assert_not_contains "$output" "unit-test-arrow-secret"
  assert_not_contains "$output" "unit-test-unicode-secret"
  assert_not_contains "$output" "unit-test-dash-secret"
  assert_not_contains "$output" "TIKTOKEN_ENCODING=<redacted>"
  assert_not_contains "$output" "SERVICE_TOKEN_ENV=<redacted>"
  assert_contains "$output" "Suggested verification:"

  unreadable_path="$repo/unreadable.env"
  printf 'UNREADABLE_API_TOKEN=unit-test-unreadable-secret-123456\n' > "$unreadable_path"
  chmod 000 "$unreadable_path"
  set +e
  output="$("$AGENT_RAILS_BIN" publish check --project "$repo" 2>&1)"
  status=$?
  set -e
  chmod 600 "$unreadable_path"
  [[ "$status" -eq 1 ]]
  assert_contains "$output" "Unable to inspect untracked file for sensitive output: unreadable.env"
  assert_not_contains "$output" "unit-test-unreadable-secret"
}

test_publish_check_scan_io_failure_exits_one() {
  local fake_bin="$TMP_ROOT/publish-io-bin"
  local real_git repo="$TMP_ROOT/publish-io-failure"
  local output status
  mkdir -p "$repo" "$fake_bin"
  git -C "$repo" init -q
  printf '# publish I/O failure\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  git -C "$repo" branch -M main
  real_git="$(command -v git)"
  cat > "$fake_bin/git" <<EOF
#!/usr/bin/env bash
for argument in "\$@"; do
  if [[ "\$argument" == "diff" ]]; then
    exit 74
  fi
done
exec "$real_git" "\$@"
EOF
  chmod +x "$fake_bin/git"

  set +e
  output="$(PATH="$fake_bin:$PATH" "$AGENT_RAILS_BIN" publish check --project "$repo" 2>&1)"
  status=$?
  set -e

  [[ "$status" -eq 1 ]]
  assert_contains "$output" "Unable to inspect committed publish diff for sensitive output."
}

test_publish_check_requires_deployed_baseline_when_upstream_equals_target() {
  local repo="$TMP_ROOT/publish-baseline"
  local remote="$TMP_ROOT/publish-baseline.git"
  local deployed_sha
  local output
  mkdir -p "$repo"
  git init --bare -q "$remote"
  git -C "$repo" init -q
  printf '# v1\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" v1
  git -C "$repo" branch -M main
  git -C "$repo" remote add origin "$remote"
  git -C "$repo" push -q -u origin main
  deployed_sha="$(git -C "$repo" rev-parse HEAD)"

  printf '\nv2\n' >> "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" v2
  git -C "$repo" push -q

  output="$("$AGENT_RAILS_BIN" publish check --project "$repo")"
  assert_contains "$output" "Deployment delta: UNRESOLVED"
  assert_contains "$output" "pass --base <currently-deployed-source-revision>"
  assert_contains "$output" "push/upstream baseline is not proof"

  output="$("$AGENT_RAILS_BIN" publish check --project "$repo" --base "$deployed_sha")"
  assert_not_contains "$output" "Deployment delta: UNRESOLVED"
  assert_contains "$output" "README.md"
}

test_git_commands_reject_invalid_base_ref() {
  local repo="$TMP_ROOT/invalid-base-ref"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  if output="$("$AGENT_RAILS_BIN" publish check --project "$repo" --base refs/heads/does-not-exist --no-secret-scan 2>&1)"; then
    printf 'Expected publish check to reject an invalid base ref.\n' >&2
    exit 1
  fi
  assert_contains "$output" "Base ref not found: refs/heads/does-not-exist"

  if output="$("$AGENT_RAILS_BIN" check --project "$repo" --base refs/heads/does-not-exist --print-only 2>&1)"; then
    printf 'Expected check to reject an invalid base ref.\n' >&2
    exit 1
  fi
  assert_contains "$output" "Base ref not found: refs/heads/does-not-exist"

  if output="$("$AGENT_RAILS_BIN" pack --project "$repo" --base refs/heads/does-not-exist --budget 1000 invalid-base 2>&1)"; then
    printf 'Expected pack to reject an invalid base ref.\n' >&2
    exit 1
  fi
  assert_contains "$output" "Base ref not found: refs/heads/does-not-exist"
}

test_estimate_uses_model_preset() {
  local output

  output="$("$AGENT_RAILS_BIN" estimate --model glm5.1 --tokenizer char --chars-per-token 2 abcdefghijkl)"

  assert_contains "$output" "Characters: 12"
  assert_contains "$output" "Tokenizer: char-estimate"
  assert_contains "$output" "Estimated tokens: 6"
  assert_contains "$output" "Model: glm5.1 (preset)"
  assert_contains "$output" "Context: 202000 tokens"
}

test_estimate_uses_custom_tokenizer_command() {
  local output

  output="$("$AGENT_RAILS_BIN" estimate --model qwen3.7-max --tokenizer command --tokenizer-command 'printf 42' abcdef)"

  assert_contains "$output" "Tokenizer: command"
  assert_contains "$output" "Estimated tokens: 42"
  assert_contains "$output" "Model: qwen3.7-max (preset)"

  output="$("$AGENT_RAILS_BIN" estimate \
    --tokenizer auto \
    --tokenizer-path "$TMP_ROOT/missing-huggingface-tokenizer" \
    --tokenizer-command 'printf 43' \
    abcdef)"
  assert_contains "$output" "Tokenizer: command"
  assert_contains "$output" "Estimated tokens: 43"
}

test_estimate_uses_deepseek_preset() {
  local output

  output="$("$AGENT_RAILS_BIN" estimate --model deepseek-v4-pro --tokenizer char --chars-per-token 2 abcdefghij)"

  assert_contains "$output" "Estimated tokens: 5"
  assert_contains "$output" "Model: deepseek-v4-pro (preset)"
  assert_contains "$output" "Context: 1000000 tokens"
  assert_contains "$output" "Max input: 1000000 tokens"
  assert_contains "$output" "Max output: 384000 tokens"
  assert_contains "$output" "RPM: 15000"
  assert_contains "$output" "TPM: 1200000"
}

test_estimate_preserves_profile_file_stdin_and_error_contracts() {
  local profile="$TMP_ROOT/python-estimate.profile"
  local input_file="$TMP_ROOT/python-estimate-input.txt"
  local output status
  {
    printf 'AGENT_RAILS_MODEL="qwen3.7-max"\n'
    printf 'AGENT_RAILS_TOKENIZER="char"\n'
    printf 'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="3"\n'
  } > "$profile"
  printf '你好' > "$input_file"

  output="$("$AGENT_RAILS_BIN" estimate --profile "$profile" --file "$input_file")"
  assert_contains "$output" "Source: file: $input_file"
  assert_contains "$output" "Characters: 2"
  assert_contains "$output" "Bytes: 6"
  assert_contains "$output" "Estimated tokens: 1"
  assert_contains "$output" "Model: qwen3.7-max (preset)"

  output="$(printf 'abcd' | "$AGENT_RAILS_BIN" estimate --tokenizer char --chars-per-token invalid)"
  assert_contains "$output" "Source: stdin"
  assert_contains "$output" "Chars/token estimate: 2"
  assert_contains "$output" "Estimated tokens: 2"

  set +e
  output="$("$AGENT_RAILS_BIN" estimate --tokenizer invalid abc 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Unknown tokenizer: invalid"

  set +e
  output="$("$AGENT_RAILS_BIN" estimate --file "$TMP_ROOT/missing-estimate-input" 2>&1)"
  status=$?
  set -e
  [[ "$status" -eq 2 ]]
  assert_contains "$output" "Input file not found: $TMP_ROOT/missing-estimate-input"
}

test_python_estimate_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_estimate.py"
}

test_python_target_project_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_target_project.py"
}

test_python_profile_init_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_profile_init.py"
}

test_python_online_memory_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_online_memory.py"
}

test_python_git_scope_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_git_scope.py"
}

test_python_sensitive_output_modules() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_sensitive_output.py"
}

test_python_verification_plan_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_verification_plan.py"
}

test_python_repair_pack_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_repair_pack.py"
}

test_python_check_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_check_application.py"
}

test_python_publish_check_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_publish_check_application.py"
}

test_python_verify_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_verify_application.py"
}

test_python_run_application_module() {
  PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH="$ROOT_DIR/src" \
    python3 "$ROOT_DIR/tests/test_run_application.py"
}

install_target_python_shadow_package() {
  local repo="$1"
  mkdir -p "$repo/agent_rails"
  cat > "$repo/agent_rails/__init__.py" <<'PYTHON'
import os
from pathlib import Path

Path(os.environ["AGENT_RAILS_SHADOW_MARKER"]).write_text("shadow package executed\n")
PYTHON
  printf 'raise SystemExit(73)\n' > "$repo/agent_rails/__main__.py"
  cat > "$repo/sitecustomize.py" <<'PYTHON'
import os

with open(os.environ["AGENT_RAILS_SHADOW_MARKER"], "w", encoding="utf-8") as marker:
    marker.write("sitecustomize executed\n")
PYTHON
}

test_python_cli_bootstrap_ignores_target_shadow_package_for_run() {
  local repo="$TMP_ROOT/python-bootstrap-run"
  local profile="$TMP_ROOT/python-bootstrap-run.profile"
  local adapter="$TMP_ROOT/python-bootstrap-run-adapter.sh"
  local task_pack="$TMP_ROOT/python-bootstrap-run-task-pack.md"
  local marker="$TMP_ROOT/python-bootstrap-run-shadow-marker"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# trusted bootstrap run\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  cat > "$adapter" <<'ADAPTER'
#!/usr/bin/env bash
printf -- '- title: Trusted bootstrap online card\n'
ADAPTER
  chmod +x "$adapter"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="python-bootstrap-run"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$task_pack"
    printf 'MEMORY_PROVIDER="hybrid"\n'
    printf 'AGENT_RAILS_ONLINE_MEMORY_CMD="%s"\n' "$adapter"
  } > "$profile"

  output="$(cd "$repo" && PYTHONPATH=. AGENT_RAILS_SHADOW_MARKER="$marker" \
    "$AGENT_RAILS_BIN" run --project . --profile "$profile" --token-budget 5000 \
      --tokenizer char "trusted Python bootstrap")"

  assert_contains "$output" "Agent Rails Estimate"
  assert_file_contains "$task_pack" "Trusted bootstrap online card"
  assert_file_not_exists "$marker"
}

test_python_cli_bootstrap_preserves_relative_estimate_file() {
  local repo="$TMP_ROOT/python-bootstrap-estimate"
  local marker="$TMP_ROOT/python-bootstrap-estimate-shadow-marker"
  local output
  mkdir -p "$repo"
  install_target_python_shadow_package "$repo"
  printf 'relative input\n' > "$repo/input.md"

  output="$(cd "$repo" && PYTHONPATH=. AGENT_RAILS_SHADOW_MARKER="$marker" \
    "$AGENT_RAILS_BIN" estimate --tokenizer char --chars-per-token 1 --file input.md)"

  assert_contains "$output" "Source: file: input.md"
  assert_contains "$output" "Estimated tokens: 15"
  assert_file_not_exists "$marker"
}

test_python_cli_bootstrap_ignores_target_shadow_package_for_profile_init() {
  local repo="$TMP_ROOT/python-bootstrap-profile-init"
  local marker="$TMP_ROOT/python-bootstrap-profile-init-shadow-marker"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# trusted bootstrap profile init\n' > "$repo/README.md"
  install_target_python_shadow_package "$repo"

  output="$(cd "$repo" && PYTHONPATH=. AGENT_RAILS_SHADOW_MARKER="$marker" \
    "$AGENT_RAILS_BIN" profile init --project . --name trusted-profile --print-only)"

  assert_contains "$output" 'PROJECT_NAME="trusted-profile"'
  assert_file_not_exists "$marker"
}

test_python_cli_bootstrap_ignores_target_shadow_package_for_doctor() {
  local repo="$TMP_ROOT/python-bootstrap-doctor"
  local profile="$TMP_ROOT/python-bootstrap-doctor.profile"
  local adapter="$TMP_ROOT/python-bootstrap-doctor-adapter.sh"
  local marker="$TMP_ROOT/python-bootstrap-doctor-shadow-marker"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# trusted bootstrap doctor\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  install_target_python_shadow_package "$repo"
  cat > "$adapter" <<'ADAPTER'
#!/usr/bin/env bash
printf -- '- Doctor bootstrap card\n'
ADAPTER
  chmod +x "$adapter"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="python-bootstrap-doctor"\n'
    printf 'MEMORY_PROVIDER="online"\n'
    printf 'AGENT_RAILS_ONLINE_MEMORY_CMD="%s"\n' "$adapter"
  } > "$profile"

  output="$(cd "$repo" && PYTHONPATH=. AGENT_RAILS_SHADOW_MARKER="$marker" \
    "$AGENT_RAILS_BIN" doctor --project . --profile "$profile" --online-memory-smoke)"

  assert_contains "$output" "Online memory smoke read OK."
  assert_file_not_exists "$marker"
}

test_run_print_only_does_not_write_pack() {
  local repo="$TMP_ROOT/run-print-only"
  local output_path="$TMP_ROOT/run-print-only-pack.md"
  local profile="$TMP_ROOT/run-print-only.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$output_path"
  } > "$profile"

  output="$("$AGENT_RAILS_BIN" run \
    --project "$repo" \
    --profile "$profile" \
    --token-budget 1200 \
    --tokenizer command \
    --tokenizer-command 'printf 42' \
    --print-only \
    "run loop")"

  assert_contains "$output" "AGENT RAILS: ON"
  assert_contains "$output" "Agent Rails Run"
  assert_contains "$output" "--token-budget"
  assert_contains "$output" "--tokenizer-command"
  assert_contains "$output" "Print-only mode. No files written."
  assert_file_not_exists "$output_path"
}

test_run_generates_pack_and_instructions() {
  local repo="$TMP_ROOT/run-loop"
  local output_path="$TMP_ROOT/run-loop-pack.md"
  local profile="$TMP_ROOT/run-loop.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '\nchanged\n' >> "$repo/README.md"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="run-loop"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$output_path"
  } > "$profile"
  printf 'stale pack\n' > "$output_path"
  chmod 644 "$output_path"

  output="$("$AGENT_RAILS_BIN" run --project "$repo" --profile "$profile" --model glm5.1 --pack-mode normal --tokenizer char "run loop")"

  assert_contains "$output" "AGENT RAILS: ON (mode=normal"
  assert_contains "$output" "Agent Instructions"
  assert_contains "$output" "Tell the user: AGENT RAILS: ON"
  assert_contains "$output" "Read the Task Pack"
  assert_contains "$output" "Grill Gate"
  assert_contains "$output" "Estimated tokens:"
  assert_file_contains "$output_path" "## Session Marker"
  assert_file_contains "$output_path" "AGENT RAILS: ON (mode=normal"
  assert_file_not_contains "$output_path" "AGENT RAILS: CHECK-ONLY"
  assert_file_contains "$output_path" "## Changed File Priority"
  assert_file_contains "$output_path" "### Grill Gate"
  assert_file_contains "$output_path" "### Target Scope Rules"
  assert_file_contains "$output_path" "do not reuse the current --profile"
  assert_file_contains "$output_path" "### Sensitive Output Rules"
  assert_file_contains "$output_path" "Base64 and URL encoding are transport encodings, not redaction"
  local pack_mode
  if pack_mode="$(stat -f '%Lp' "$output_path" 2>/dev/null)"; then
    :
  else
    pack_mode="$(stat -c '%a' "$output_path")"
  fi
  if [[ "$pack_mode" != "600" ]]; then
    printf 'Expected Task Pack mode 600, got %s for %s.\n' "$pack_mode" "$output_path" >&2
    exit 1
  fi
}

test_run_infers_deep_for_refactor_goal() {
  local repo="$TMP_ROOT/run-infer-refactor"
  local output_path="$TMP_ROOT/run-infer-refactor-pack.md"
  local profile="$TMP_ROOT/run-infer-refactor.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="run-infer-refactor"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$output_path"
  } > "$profile"

  output="$("$AGENT_RAILS_BIN" run --project "$repo" --profile "$profile" --model glm5.1 --tokenizer char "重构 current module")"

  assert_contains "$output" "Inferred pack mode: deep"
  assert_file_contains "$output_path" 'Pack mode: `deep`'
}

test_run_infers_lite_for_poc_goal() {
  local repo="$TMP_ROOT/run-infer-poc"
  local output_path="$TMP_ROOT/run-infer-poc-pack.md"
  local profile="$TMP_ROOT/run-infer-poc.profile"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="run-infer-poc"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$output_path"
  } > "$profile"

  output="$("$AGENT_RAILS_BIN" run --project "$repo" --profile "$profile" --model glm5.1 --tokenizer char "Trajectory Eval POC deploy prep")"

  assert_contains "$output" "Inferred pack mode: lite"
  assert_contains "$output" "In lite mode, skip full grill"
  assert_file_contains "$output_path" 'Pack mode: `lite`'
  assert_file_contains "$output_path" "Lite mode active: do not run a full grill"
  assert_file_contains "$output_path" "### Trigger Matrix"
}

test_pack_defaults_to_worktree_specific_path() {
  local repo_a="$TMP_ROOT/worktree-a/sample-project"
  local repo_b="$TMP_ROOT/worktree-b/sample-project"
  local profile="$TMP_ROOT/worktree-specific.profile"
  local home="$TMP_ROOT/home-worktree-specific"
  local output_a output_b path_a path_b
  mkdir -p "$repo_a" "$repo_b" "$home"
  git -C "$repo_a" init -q
  printf '# a\n' > "$repo_a/README.md"
  git -C "$repo_a" add README.md
  git_commit "$repo_a" init
  git -C "$repo_b" init -q
  printf '# b\n' > "$repo_b/README.md"
  git -C "$repo_b" add README.md
  git_commit "$repo_b" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="sample-project"\n'
    printf 'MEMORY_PROVIDER="local"\n'
  } > "$profile"

  output_a="$(HOME="$home" "$AGENT_RAILS_BIN" pack --project "$repo_a" --profile "$profile" --budget 1000 "worktree check")"
  output_b="$(HOME="$home" "$AGENT_RAILS_BIN" pack --project "$repo_b" --profile "$profile" --budget 1000 "worktree check")"
  path_a="$(printf '%s\n' "$output_a" | sed -n -E 's/^Wrote //p' | sed -n '1p')"
  path_b="$(printf '%s\n' "$output_b" | sed -n -E 's/^Wrote //p' | sed -n '1p')"

  assert_contains "$output_a" "AGENT RAILS: ON"
  assert_contains "$path_a" "$home/.agent-rails/agent-context/"
  assert_contains "$path_a" "sample-project-"
  assert_contains "$path_b" "sample-project-"
  if [[ "$path_a" == "$path_b" ]]; then
    printf 'Expected different worktrees to get different Task Pack paths.\n%s\n%s\n' "$path_a" "$path_b" >&2
    exit 1
  fi
}

test_standalone_tui_ab_eval() {
  if ! command -v python3 >/dev/null 2>&1; then
    printf 'python3 is required for tools/ab_eval.py.\n' >&2
    exit 1
  fi
  PYTHONPYCACHEPREFIX="$TMP_ROOT/ab-eval-pycache" python3 "$ROOT_DIR/tests/test_ab_eval.py"
}

test_agent_rails_cli_has_no_eval_command() {
  local output
  output="$("$AGENT_RAILS_BIN" --help)"
  assert_not_contains "$output" "agent-rails eval"
  if "$AGENT_RAILS_BIN" eval >/dev/null 2>&1; then
    printf 'Expected the removed eval command to fail.\n' >&2
    exit 1
  fi
}

run_workflow_tests() {
  run_test test_agent_check_includes_bin_entrypoint "agent-check includes bin/agent-rails"
  run_test test_agent_check_excludes_deleted_shell_from_command "agent-check excludes deleted shell files from commands"
  run_test test_agent_check_suggestions_only_omits_repeated_scope "agent-check suggestions-only omits repeated scope"
  run_test test_agent_check_selects_changed_test_suites "agent-check selects changed test suites"
  run_test test_agent_check_run_uses_child_shell "agent-check --run uses child shell"
  run_test test_agent_check_run_target_guard_ignores_inherited_worktree "agent-check target guard ignores inherited worktree"
  run_test test_pack_and_check_share_python_verification_plan "pack and check share Python Verification Plan"
  run_test test_check_and_publish_use_python_target_context "check and publish use Python Target Project Context"
  run_test test_verify_runs_plan_and_can_preview "verify runs or previews the verification plan"
  run_test test_verify_preserves_child_exit_and_partial_output "verify preserves child exit and partial output"
  run_test test_verify_publish_adds_release_check "verify --publish adds release checks"
  run_test test_verify_uses_python_target_context "verify uses Python Target Project Context"
  run_test test_publish_check_summarizes_scope_and_redacts_secrets "publish check summarizes scope and redacts secrets"
  run_test test_publish_check_requires_deployed_baseline_when_upstream_equals_target "publish check requires deployed baseline when upstream equals target"
  run_test test_publish_check_scan_io_failure_exits_one "publish check maps scan I/O failure to runtime exit"
  run_test test_git_commands_reject_invalid_base_ref "git commands reject invalid base ref"
  run_test test_estimate_uses_model_preset "estimate uses model preset"
  run_test test_estimate_uses_custom_tokenizer_command "estimate uses custom tokenizer command"
  run_test test_estimate_uses_deepseek_preset "estimate uses deepseek preset"
  run_test test_estimate_preserves_profile_file_stdin_and_error_contracts "estimate preserves profile, input, and error contracts"
  run_test test_python_estimate_modules "Python estimate modules"
  run_test test_python_target_project_modules "Python Paths, Profile, and Target Project modules"
  run_test test_python_profile_init_modules "Python Profile Init module"
  run_test test_python_online_memory_modules "Python provider-neutral Online Memory Interface"
  run_test test_python_git_scope_modules "Python Git Scope module"
  run_test test_python_sensitive_output_modules "Python Sensitive Output Guard"
  run_test test_python_verification_plan_module "Python Verification Plan module"
  run_test test_python_repair_pack_module "Python Repair Pack module"
  run_test test_python_check_application_module "Python Agent Check Application Service"
  run_test test_python_publish_check_application_module "Python Publish Check Application Service"
  run_test test_python_verify_application_module "Python Verify Application Service"
  run_test test_python_run_application_module "Python Run Application Service"
  run_test test_python_cli_bootstrap_ignores_target_shadow_package_for_run "Python CLI bootstrap ignores Target Project shadow package for run"
  run_test test_python_cli_bootstrap_preserves_relative_estimate_file "Python CLI bootstrap preserves relative estimate file"
  run_test test_python_cli_bootstrap_ignores_target_shadow_package_for_profile_init "Python CLI bootstrap ignores Target Project shadow package for Profile Init"
  run_test test_python_cli_bootstrap_ignores_target_shadow_package_for_doctor "Python CLI bootstrap ignores Target Project shadow package for Doctor"
  run_test test_run_print_only_does_not_write_pack "run print-only does not write pack"
  run_test test_run_generates_pack_and_instructions "run generates pack and instructions"
  run_test test_run_infers_deep_for_refactor_goal "run infers deep for refactor goal"
  run_test test_run_infers_lite_for_poc_goal "run infers lite for poc goal"
  run_test test_pack_defaults_to_worktree_specific_path "pack defaults to worktree-specific path"
  run_test test_standalone_tui_ab_eval "standalone TUI A/B eval"
  run_test test_agent_rails_cli_has_no_eval_command "agent-rails CLI has no eval command"
}
