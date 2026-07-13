# Check, publish, estimate, run, path, and eval workflow tests.

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
  if grep -Fq 'eval "$command"' "$ROOT_DIR/scripts/agent-check.sh"; then
    printf 'agent-check should not run verification through eval.\n' >&2
    exit 1
  fi
}

test_publish_check_summarizes_scope_and_redacts_secrets() {
  local repo="$TMP_ROOT/publish-check"
  local output
  mkdir -p "$repo/scripts"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  printf '#!/usr/bin/env bash\nprintf "ok\\n"\n' > "$repo/scripts/run.sh"
  git -C "$repo" add README.md scripts/run.sh
  git_commit "$repo" init

  printf '\nprintf "changed\\n"\n' >> "$repo/scripts/run.sh"
  git -C "$repo" add scripts/run.sh
  printf 'OPENMEMORY_ACCESS_KEY=super-secret-value\n' > "$repo/.env.local"
  {
    printf 'AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base\n'
    printf 'OPENMEMORY_TOKEN_ENV="${OPENMEMORY_TOKEN_ENV:-OPENMEMORY_ACCESS_KEY}"\n'
  } > "$repo/tokenizer.md"

  output="$("$AGENT_RAILS_BIN" publish check --project "$repo")"

  assert_contains "$output" "AGENT RAILS: CHECK-ONLY (reason=publish"
  assert_contains "$output" "Agent publish check"
  assert_contains "$output" "Staged files (1)"
  assert_contains "$output" "Untracked files (2)"
  assert_contains "$output" "scripts/run.sh"
  assert_contains "$output" ".env.local"
  assert_contains "$output" "Potential secret matches found"
  assert_contains "$output" "OPENMEMORY_ACCESS_KEY=<redacted>"
  assert_not_contains "$output" "super-secret-value"
  assert_not_contains "$output" "TIKTOKEN_ENCODING=<redacted>"
  assert_not_contains "$output" "OPENMEMORY_TOKEN_ENV=<redacted>"
  assert_contains "$output" "Suggested verification:"
}

test_sensitive_output_module_redacts_supported_formats() {
  local input="$TMP_ROOT/sensitive-output-input.txt"
  local redacted="$TMP_ROOT/sensitive-output-redacted.txt"
  local findings="$TMP_ROOT/sensitive-output-findings.txt"
  local diff_input="$TMP_ROOT/sensitive-output-diff.txt"
  local diff_redacted="$TMP_ROOT/sensitive-output-diff-redacted.txt"

  cat > "$input" <<'SENSITIVE'
OPENMEMORY_ACCESS_KEY=unit-test-secret-shell-123456
authorization: Bearer unit-test-secret-header-123456
"api_key": "unit-test-secret-json-123456",
OPENMEMORY_TOKEN_ENV="${OPENMEMORY_TOKEN_ENV:-OPENMEMORY_ACCESS_KEY}"
AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base
-----BEGIN PRIVATE KEY-----
unit-test-private-key-material-123456
-----END PRIVATE KEY-----
SENSITIVE

  # shellcheck source=scripts/agent-sensitive-output.sh
  source "$ROOT_DIR/scripts/agent-sensitive-output.sh"
  agent_sensitive_redact_file "$input" "$redacted"
  agent_sensitive_scan_file "$input" > "$findings"

  assert_file_contains "$redacted" 'OPENMEMORY_ACCESS_KEY=<redacted>'
  assert_file_contains "$redacted" 'authorization: <redacted>'
  assert_file_contains "$redacted" '"api_key": "<redacted>",'
  assert_file_contains "$redacted" 'OPENMEMORY_TOKEN_ENV="${OPENMEMORY_TOKEN_ENV:-OPENMEMORY_ACCESS_KEY}"'
  assert_file_contains "$redacted" 'AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base'
  assert_file_contains "$redacted" '<redacted private key block>'
  assert_file_not_contains "$redacted" 'unit-test-secret'
  assert_file_not_contains "$redacted" 'unit-test-private-key-material'

  assert_file_contains "$findings" 'OPENMEMORY_ACCESS_KEY=<redacted>'
  assert_file_contains "$findings" 'authorization: <redacted>'
  assert_file_contains "$findings" '"api_key": "<redacted>",'
  assert_file_not_contains "$findings" 'TOKEN_ENV'
  assert_file_not_contains "$findings" 'TIKTOKEN_ENCODING'
  assert_file_not_contains "$findings" 'unit-test-secret'

  cat > "$diff_input" <<'SENSITIVE_DIFF'
 -----BEGIN PRIVATE KEY-----
-old-unit-test-private-key-material
+new-unit-test-private-key-material
 -----END PRIVATE KEY-----
SENSITIVE_DIFF
  agent_sensitive_redact_file "$diff_input" "$diff_redacted" diff
  assert_file_contains "$diff_redacted" '<redacted private key block>'
  assert_file_not_contains "$diff_redacted" 'private-key-material'
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

  output="$("$AGENT_RAILS_BIN" run --project "$repo" --profile "$profile" --print-only "run loop")"

  assert_contains "$output" "AGENT RAILS: ON"
  assert_contains "$output" "Agent Rails Run"
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

test_init_paths_do_not_leak_default_config_home_to_children() {
  local repo="$TMP_ROOT/init-paths-child-home"
  local profile="$TMP_ROOT/init-paths-child-home.profile"
  local parent_home="$TMP_ROOT/home-init-paths-parent"
  local child_home="$TMP_ROOT/home-init-paths-child"
  local output path
  mkdir -p "$repo" "$parent_home" "$child_home"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="init-paths-child"\n'
  } > "$profile"

  output="$(HOME="$parent_home" bash -c '
    source "$1/scripts/agent-paths.sh"
    agent_rails_init_paths
    HOME="$2" "$3" pack --project "$4" --profile "$5" --budget 1000 "child home check"
  ' bash "$ROOT_DIR" "$child_home" "$AGENT_RAILS_BIN" "$repo" "$profile")"
  path="$(printf '%s\n' "$output" | sed -n -E 's/^Wrote //p' | sed -n '1p')"

  assert_contains "$path" "$child_home/.agent-rails/agent-context/"
  assert_not_contains "$path" "$parent_home/.agent-rails/agent-context/"
}

test_eval_init_record_report() {
  local repo="$TMP_ROOT/eval-target"
  local eval_dir="$TMP_ROOT/evals"
  local home="$TMP_ROOT/home-eval-record"
  local task_path="$eval_dir/tasks/sample-code-review.yaml"
  local report_path="$eval_dir/report.md"
  local output
  local log_path
  mkdir -p "$repo" "$home"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '\nchanged\n' >> "$repo/README.md"

  output="$("$AGENT_RAILS_BIN" eval init --dir "$eval_dir")"
  assert_contains "$output" "Initialized eval directory"
  assert_file_contains "$task_path" "id: sample-code-review-001"

  output="$(HOME="$home" "$AGENT_RAILS_BIN" eval record --task "$task_path" --project "$repo" --dir "$eval_dir" --mode agentrails --model glm5.1 --pack-mode normal --tokenizer char)"
  assert_contains "$output" "Recorded eval run"
  log_path="$(printf '%s\n' "$output" | sed -n -E 's/^Recorded eval run: //p' | sed -n '1p')"
  assert_file_contains "$log_path" '"event":"run_started"'
  assert_file_contains "$log_path" '"event":"command_finished"'
  assert_file_contains "$log_path" '"event":"run_finished"'

  output="$("$AGENT_RAILS_BIN" eval report --runs "$eval_dir/runs" --output "$report_path")"
  assert_contains "$output" "Wrote"
  assert_file_contains "$report_path" "sample-code-review-001"
  assert_file_contains "$report_path" "agentrails"
}

run_workflow_tests() {
  run_test test_agent_check_includes_bin_entrypoint "agent-check includes bin/agent-rails"
  run_test test_agent_check_suggestions_only_omits_repeated_scope "agent-check suggestions-only omits repeated scope"
  run_test test_agent_check_selects_changed_test_suites "agent-check selects changed test suites"
  run_test test_agent_check_run_uses_child_shell "agent-check --run uses child shell"
  run_test test_publish_check_summarizes_scope_and_redacts_secrets "publish check summarizes scope and redacts secrets"
  run_test test_sensitive_output_module_redacts_supported_formats "sensitive output module redacts supported formats"
  run_test test_publish_check_requires_deployed_baseline_when_upstream_equals_target "publish check requires deployed baseline when upstream equals target"
  run_test test_git_commands_reject_invalid_base_ref "git commands reject invalid base ref"
  run_test test_estimate_uses_model_preset "estimate uses model preset"
  run_test test_estimate_uses_custom_tokenizer_command "estimate uses custom tokenizer command"
  run_test test_estimate_uses_deepseek_preset "estimate uses deepseek preset"
  run_test test_run_print_only_does_not_write_pack "run print-only does not write pack"
  run_test test_run_generates_pack_and_instructions "run generates pack and instructions"
  run_test test_run_infers_deep_for_refactor_goal "run infers deep for refactor goal"
  run_test test_run_infers_lite_for_poc_goal "run infers lite for poc goal"
  run_test test_pack_defaults_to_worktree_specific_path "pack defaults to worktree-specific path"
  run_test test_init_paths_do_not_leak_default_config_home_to_children "init paths do not leak default config home to children"
  run_test test_eval_init_record_report "eval init record report"
}
