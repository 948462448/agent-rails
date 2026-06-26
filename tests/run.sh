#!/usr/bin/env bash
# Lightweight e2e tests for Agent Rails shell entrypoints.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AGENT_RAILS_BIN="$ROOT_DIR/bin/agent-rails"
EXPECTED_AGENT_RAILS_VERSION="$(awk 'NF { print $1; exit }' "$ROOT_DIR/VERSION")"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-tests.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

git_commit() {
  local repo="$1"
  local message="$2"
  git -C "$repo" -c user.name=Agent -c user.email=agent@example.com commit -q -m "$message"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "$haystack" != *"$needle"* ]]; then
    printf 'Expected output to contain: %s\n' "$needle" >&2
    printf 'Actual output:\n%s\n' "$haystack" >&2
    exit 1
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "$haystack" == *"$needle"* ]]; then
    printf 'Expected output not to contain: %s\n' "$needle" >&2
    printf 'Actual output:\n%s\n' "$haystack" >&2
    exit 1
  fi
}

assert_file_contains() {
  local path="$1"
  local needle="$2"
  if ! grep -Fq -- "$needle" "$path"; then
    printf 'Expected %s to contain: %s\n' "$path" "$needle" >&2
    printf 'Actual file:\n' >&2
    sed -n '1,160p' "$path" >&2
    exit 1
  fi
}

assert_file_not_contains() {
  local path="$1"
  local needle="$2"
  if grep -Fq -- "$needle" "$path"; then
    printf 'Expected %s not to contain: %s\n' "$path" "$needle" >&2
    printf 'Actual file:\n' >&2
    sed -n '1,160p' "$path" >&2
    exit 1
  fi
}

assert_file_not_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    printf 'Expected %s not to exist.\n' "$path" >&2
    exit 1
  fi
}

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
  assert_contains "$output" "--force"
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
  assert_contains "$output" "agent-rails pack"
  assert_contains "$output" "profiles/default.profile"

  output="$(CLAUDE_PROJECT_DIR="$plain_repo" "$ROOT_DIR/hooks/agent-rails-session-start.sh")"
  if [[ -n "$output" ]]; then
    printf 'Expected hook to stay quiet without an Agent Rails marker.\n%s\n' "$output" >&2
    exit 1
  fi
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

test_claude_commands_use_current_worktree_root() {
  local repo="$TMP_ROOT/current-worktree-root"
  local profile="$TMP_ROOT/custom.profile"
  local task_pack_path="$TMP_ROOT/custom-task-pack.md"
  mkdir -p "$repo"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="custom-name"\n'
    printf 'TASK_PACK_PATH="%s"\n' "$task_pack_path"
  } > "$profile"

  "$AGENT_RAILS_BIN" claude install --project "$repo" --profile "$profile" --mode project >/dev/null

  assert_file_contains "$repo/.claude/AGENT_RAILS.md" "git rev-parse --show-toplevel"
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" "git rev-parse --show-toplevel"
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" '--project "$project_root"'
  assert_file_not_contains "$repo/.claude/commands/agent-rails-pack.md" "$task_pack_path"
  assert_file_not_contains "$repo/.claude/commands/agent-rails-pack.md" "--project \"$repo\""
  assert_file_contains "$repo/.claude/commands/agent-rails-pack.md" "AGENT RAILS: ON"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "git rev-parse --show-toplevel"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" '--project "$project_root"'
  assert_file_not_contains "$repo/.claude/commands/agent-rails-lite.md" "$task_pack_path"
  assert_file_contains "$repo/.claude/commands/agent-rails-lite.md" "AGENT RAILS: ON"
  assert_file_contains "$repo/CLAUDE.md" "git rev-parse --show-toplevel"
  assert_file_not_contains "$repo/CLAUDE.md" "$task_pack_path"
}

test_pack_embeds_local_memory_with_budget() {
  local repo="$TMP_ROOT/pack-budget"
  local profile="$TMP_ROOT/budget.profile"
  local memory_dir="$TMP_ROOT/memory"
  local output="$TMP_ROOT/budget-task-pack.md"
  mkdir -p "$repo" "$memory_dir"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  cat > "$memory_dir/contracts-first.md" <<'CARD'
---
title: Contracts first
triggers:
  - contracts
---

## Rule

Cross-project API shape changes must start from contracts.
This extra line is intentionally long enough to exercise truncation under a small memory budget.
CARD
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="pack-budget"\n'
    printf 'MEMORY_LOCAL_DIR="%s"\n' "$memory_dir"
    printf 'MEMORY_PROVIDER="local"\n'
    printf 'AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="1000"\n'
  } > "$profile"

  "$AGENT_RAILS_BIN" pack --project "$repo" --profile "$profile" --output "$output" --budget 360 "contracts change" >/dev/null

  assert_file_contains "$output" "## Context Budget"
  assert_file_contains "$output" 'Memory cards: `40%` -> `144` chars'
  assert_file_contains "$output" '#### `'
  assert_file_contains "$output" "Cross-project API shape changes"
  assert_file_contains "$output" "truncated by Agent Rails budget"
}

test_pack_skips_unmatched_local_memory() {
  local repo="$TMP_ROOT/pack-memory-no-fallback"
  local profile="$TMP_ROOT/memory-no-fallback.profile"
  local memory_dir="$TMP_ROOT/memory-no-fallback"
  local output="$TMP_ROOT/memory-no-fallback-task-pack.md"
  mkdir -p "$repo" "$memory_dir"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  cat > "$memory_dir/pandora-boot.md" <<'CARD'
---
title: Pandora boot
triggers:
  - pandora
  - boot
---

This card should not match an unrelated refactor goal.
CARD
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="pack-memory-no-fallback"\n'
    printf 'MEMORY_LOCAL_DIR="%s"\n' "$memory_dir"
    printf 'MEMORY_PROVIDER="local"\n'
  } > "$profile"

  "$AGENT_RAILS_BIN" pack --project "$repo" --profile "$profile" --output "$output" "score formatting refactor" >/dev/null

  assert_file_contains "$output" "No local cards selected"
  assert_file_not_contains "$output" "Pandora boot"
}

test_memory_suggest_skip_records_decision_only() {
  local repo="$TMP_ROOT/memory-skip"
  local profile="$TMP_ROOT/memory-skip.profile"
  local memory_dir="$TMP_ROOT/memory-skip-cards"
  local decision_path="$TMP_ROOT/memory-skip-decision.md"
  local output
  mkdir -p "$repo" "$memory_dir"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="memory-skip"\n'
    printf 'MEMORY_LOCAL_DIR="%s"\n' "$memory_dir"
  } > "$profile"

  output="$("$AGENT_RAILS_BIN" memory suggest --project "$repo" --profile "$profile" --output "$decision_path" --decision skip --reason "one-off output")"

  assert_contains "$output" "Wrote $decision_path"
  assert_file_contains "$decision_path" 'Decision: `skip`'
  assert_file_contains "$decision_path" "one-off output"
  assert_file_not_exists "$memory_dir/untitled-memory-decision.md"
}

test_memory_suggest_write_local_card() {
  local repo="$TMP_ROOT/memory-write"
  local profile="$TMP_ROOT/memory-write.profile"
  local memory_dir="$TMP_ROOT/memory-write-cards"
  local decision_path="$TMP_ROOT/memory-write-decision.md"
  local output
  mkdir -p "$repo" "$memory_dir"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '\nchanged\n' >> "$repo/README.md"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="memory-write"\n'
    printf 'MEMORY_LOCAL_DIR="%s"\n' "$memory_dir"
  } > "$profile"

  output="$("$AGENT_RAILS_BIN" memory suggest \
    --project "$repo" \
    --profile "$profile" \
    --output "$decision_path" \
    --decision keep \
    --write-local \
    --title "Backend auth probe" \
    --trigger "auth readiness" \
    --applies-to "backend" \
    --verify "curl checkpreload.htm first" \
    --caution "SSO config is environment-specific" \
    "Use checkpreload.htm as the first readiness probe before reading business handlers.")"

  assert_contains "$output" "Wrote local memory $memory_dir/backend-auth-probe.md"
  assert_file_contains "$decision_path" 'Decision: `keep`'
  assert_file_contains "$memory_dir/backend-auth-probe.md" 'title: "Backend auth probe"'
  assert_file_contains "$memory_dir/backend-auth-probe.md" '  - "auth readiness"'
  assert_file_contains "$memory_dir/backend-auth-probe.md" "Use checkpreload.htm as the first readiness probe"
  assert_file_contains "$memory_dir/backend-auth-probe.md" "curl checkpreload.htm first"
}

test_pack_includes_changed_file_excerpts() {
  local repo="$TMP_ROOT/pack-excerpts"
  local output="$TMP_ROOT/pack-excerpts-task-pack.md"
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'agent rails excerpt fixture\n'
    printf 'second line\n'
  } > "$repo/src/app.txt"

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --budget 4000 "read changed file" >/dev/null

  assert_file_contains "$output" "## Changed File Excerpts"
  assert_file_contains "$output" '### `src/app.txt`'
  assert_file_contains "$output" "agent rails excerpt fixture"
}

test_pack_sorts_changed_files_by_goal() {
  local repo="$TMP_ROOT/pack-smart-sort"
  local output="$TMP_ROOT/pack-smart-sort-task-pack.md"
  local script_line
  local doc_line
  mkdir -p "$repo/scripts" "$repo/docs"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  printf '#!/usr/bin/env bash\n' > "$repo/scripts/tokenizer.sh"
  printf 'plain notes\n' > "$repo/docs/plain.txt"

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --budget 4000 "tokenizer work" >/dev/null

  assert_file_contains "$output" "## Changed File Priority"
  assert_file_contains "$output" 'scripts/tokenizer.sh` score=175'
  script_line="$(grep -n '^- `scripts/tokenizer.sh`' "$output" | head -n1 | cut -d: -f1)"
  doc_line="$(grep -n '^- `docs/plain.txt`' "$output" | head -n1 | cut -d: -f1)"
  if [[ -z "$script_line" || -z "$doc_line" || "$script_line" -ge "$doc_line" ]]; then
    printf 'Expected smart sort to place scripts/tokenizer.sh before docs/plain.txt.\n' >&2
    sed -n '/## Changed Files/,/## Changed File Excerpts/p' "$output" >&2
    exit 1
  fi
}

test_pack_falls_back_from_missing_legacy_kit_profile() {
  local repo="$TMP_ROOT/pack-legacy-profile"
  local legacy_profile="$ROOT_DIR/profiles/__missing_legacy_profile_for_test__.profile"
  local output_path="$TMP_ROOT/pack-legacy-profile-task-pack.md"
  local output
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  assert_file_not_exists "$legacy_profile"

  output="$("$AGENT_RAILS_BIN" pack --project "$repo" --profile "$legacy_profile" --output "$output_path" "legacy profile")"

  assert_contains "$output" "AGENT RAILS: ON"
  assert_contains "$output" "Wrote $output_path"
  assert_file_not_contains "$output_path" "$legacy_profile"
}

test_pack_rejects_missing_non_kit_profile() {
  local repo="$TMP_ROOT/pack-missing-non-kit-profile"
  local missing_profile="$TMP_ROOT/missing-project.profile"
  local output_path="$TMP_ROOT/pack-missing-non-kit-profile-task-pack.md"
  local output status
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  assert_file_not_exists "$missing_profile"

  set +e
  output="$("$AGENT_RAILS_BIN" pack --project "$repo" --profile "$missing_profile" --output "$output_path" "missing profile" 2>&1)"
  status=$?
  set -e

  if [[ "$status" -eq 0 ]]; then
    printf 'Expected missing non-kit profile to fail.\n%s\n' "$output" >&2
    exit 1
  fi
  assert_contains "$output" "Profile not found: $missing_profile"
  assert_file_not_exists "$output_path"
}

test_pack_uses_model_preset_budget() {
  local repo="$TMP_ROOT/model-preset"
  local output="$TMP_ROOT/model-preset-task-pack.md"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --model glm5.1 --pack-mode deep "model preset" >/dev/null

  assert_file_contains "$output" 'Model: `glm5.1`'
  assert_file_contains "$output" 'context `202000` tokens'
  assert_file_contains "$output" 'Pack mode: `deep`'
  assert_file_contains "$output" 'Budget source: `model preset`'
  assert_file_contains "$output" 'Token budget: `60000` tokens'
  assert_file_contains "$output" 'Total: `120000` chars'
}

test_pack_uses_lite_model_preset_budget() {
  local repo="$TMP_ROOT/lite-model-preset"
  local output="$TMP_ROOT/lite-model-preset-task-pack.md"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --model glm5.1 --pack-mode lite "POC deploy prep" >/dev/null

  assert_file_contains "$output" 'Model: `glm5.1`'
  assert_file_contains "$output" 'Pack mode: `lite`'
  assert_file_contains "$output" 'Token budget: `12000` tokens'
  assert_file_contains "$output" 'Total: `24000` chars'
  assert_file_contains "$output" 'Lite mode: skip full grill'
  assert_file_contains "$output" 'Grill question budget: `8`'
  assert_file_contains "$output" 'Use check --print-only before deploy/release/upload flows'
  assert_file_contains "$output" 'Memory is the cross-session long-term truth'
}

test_pack_uses_deepseek_model_preset_budget() {
  local repo="$TMP_ROOT/deepseek-model-preset"
  local output="$TMP_ROOT/deepseek-model-preset-task-pack.md"
  mkdir -p "$repo"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --model deepseek-v4-pro --pack-mode deep "deepseek model preset" >/dev/null

  assert_file_contains "$output" 'Model: `deepseek-v4-pro`'
  assert_file_contains "$output" 'context `1000000` tokens'
  assert_file_contains "$output" 'max input `1000000` tokens'
  assert_file_contains "$output" 'max output `384000` tokens'
  assert_file_contains "$output" 'rpm `15000`'
  assert_file_contains "$output" 'tpm `1200000`'
  assert_file_contains "$output" 'Pack mode: `deep`'
  assert_file_contains "$output" 'Token budget: `160000` tokens'
  assert_file_contains "$output" 'Total: `320000` chars'
}

test_profile_init_ignores_non_python_tests_dir() {
  local repo="$TMP_ROOT/profile-init-shell-tests"
  local output
  mkdir -p "$repo/tests"
  printf '#!/usr/bin/env bash\n' > "$repo/tests/run.sh"

  output="$("$AGENT_RAILS_BIN" profile init --project "$repo" --name shell-tests --print-only)"

  if [[ "$output" == *"VERIFY_PYTHON"* ]]; then
    printf 'Did not expect VERIFY_PYTHON for shell-only tests directory.\n%s\n' "$output" >&2
    exit 1
  fi
  if [[ "$output" == *'TASK_PACK_PATH='* ]]; then
    printf 'Generated profiles should leave TASK_PACK_PATH unset for worktree-safe defaults.\n%s\n' "$output" >&2
    exit 1
  fi
}

test_profile_init_writes_user_config_by_default() {
  local repo="$TMP_ROOT/profile-init-user"
  local home="$TMP_ROOT/home-profile-init-user"
  local profile_path="$home/.agent-rails/profiles/projects/demo.profile"
  local output
  mkdir -p "$repo" "$home"

  output="$(HOME="$home" "$AGENT_RAILS_BIN" profile init --project "$repo" --name demo)"

  assert_contains "$output" "Wrote $profile_path"
  assert_file_contains "$profile_path" 'source "$AGENT_RAILS_HOME/profiles/default.profile"'
  assert_file_contains "$profile_path" 'MEMORY_LOCAL_DIR="${AGENT_RAILS_CONFIG_HOME}/memory/demo"'
}

test_profile_init_can_write_project_config() {
  local repo="$TMP_ROOT/profile-init-project"
  local repo_abs
  local profile_path
  local output
  mkdir -p "$repo"
  repo_abs="$(cd "$repo" && pwd)"
  profile_path="$repo_abs/.agent-rails/profile"

  output="$("$AGENT_RAILS_BIN" profile init --project "$repo" --name project-demo --scope project)"

  assert_contains "$output" "Wrote $profile_path"
  assert_file_contains "$profile_path" 'PROJECT_NAME="project-demo"'
  assert_file_contains "$profile_path" 'MEMORY_LOCAL_DIR="${AGENT_RAILS_CONFIG_HOME}/memory/project-demo"'
}

test_run_prefers_project_agent_rails_profile() {
  local repo="$TMP_ROOT/run-project-profile"
  local home="$TMP_ROOT/home-run-project-profile"
  local repo_abs
  local output
  mkdir -p "$repo/.agent-rails" "$home"
  repo_abs="$(cd "$repo" && pwd)"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="project-local"\n'
    printf 'AGENT_RAILS_PACK_MODE="lite"\n'
  } > "$repo/.agent-rails/profile"

  output="$(HOME="$home" "$AGENT_RAILS_BIN" run --project "$repo" --print-only "project profile")"

  assert_contains "$output" "Profile: "
  assert_contains "$output" "run-project-profile/.agent-rails/profile"
  assert_contains "$output" "AGENT RAILS: ON (mode=lite"
  assert_contains "$output" "$home/.agent-rails/agent-context/project-local-"
}

test_run_uses_user_agent_rails_profile() {
  local repo="$TMP_ROOT/run-user-profile"
  local home="$TMP_ROOT/home-run-user-profile"
  local profile_path="$home/.agent-rails/profiles/projects/run-user-profile.profile"
  local output
  mkdir -p "$repo" "$(dirname "$profile_path")" "$home"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="user-local"\n'
    printf 'AGENT_RAILS_PACK_MODE="lite"\n'
  } > "$profile_path"

  output="$(HOME="$home" "$AGENT_RAILS_BIN" run --project "$repo" --print-only "user profile")"

  assert_contains "$output" "Profile: $profile_path"
  assert_contains "$output" "AGENT RAILS: ON (mode=lite"
  assert_contains "$output" "$home/.agent-rails/agent-context/user-local-"
}

test_init_prints_shell_setup
printf 'ok - init prints shell setup\n'

test_version_command_reads_version_file
printf 'ok - version command reads VERSION\n'

test_plugin_manifests_match_version_file
printf 'ok - plugin manifests match VERSION\n'

test_changelog_contains_version_file
printf 'ok - changelog contains VERSION\n'

test_update_dry_run_sequences_project_refresh
printf 'ok - update dry-run sequences project refresh\n'

test_update_falls_back_from_missing_legacy_kit_profile
printf 'ok - update falls back from missing legacy kit profile\n'

test_upgrade_self_alias_uses_update_flow
printf 'ok - upgrade self alias uses update flow\n'

test_codex_install_and_uninstall_dry_run
printf 'ok - codex install/uninstall dry-run\n'

test_agent_check_includes_bin_entrypoint
printf 'ok - agent-check includes bin/agent-rails\n'

test_agent_check_run_uses_child_shell
printf 'ok - agent-check --run uses child shell\n'

test_publish_check_summarizes_scope_and_redacts_secrets
printf 'ok - publish check summarizes scope and redacts secrets\n'

test_estimate_uses_model_preset
printf 'ok - estimate uses model preset\n'

test_estimate_uses_custom_tokenizer_command
printf 'ok - estimate uses custom tokenizer command\n'

test_estimate_uses_deepseek_preset
printf 'ok - estimate uses deepseek preset\n'

test_run_print_only_does_not_write_pack
printf 'ok - run print-only does not write pack\n'

test_run_generates_pack_and_instructions
printf 'ok - run generates pack and instructions\n'

test_run_infers_deep_for_refactor_goal
printf 'ok - run infers deep for refactor goal\n'

test_run_infers_lite_for_poc_goal
printf 'ok - run infers lite for poc goal\n'

test_pack_defaults_to_worktree_specific_path
printf 'ok - pack defaults to worktree-specific path\n'

test_eval_init_record_report
printf 'ok - eval init record report\n'

test_claude_force_replaces_existing_block
printf 'ok - claude install --force replaces existing block\n'

test_claude_install_refresh_and_uninstall
printf 'ok - claude install refresh and uninstall lifecycle\n'

test_claude_upgrade_alias_is_deprecated
printf 'ok - claude upgrade alias is deprecated\n'

test_claude_local_does_not_touch_tracked_claude_md
printf 'ok - claude local leaves tracked CLAUDE.md alone\n'

test_claude_local_can_write_global_reminder
printf 'ok - claude local can write global reminder\n'

test_claude_local_can_install_session_hook
printf 'ok - claude local can install session hook\n'

test_session_start_hook_respects_project_marker
printf 'ok - session start hook respects project marker\n'

test_session_start_hook_resolves_missing_legacy_kit_profile
printf 'ok - session start hook resolves missing legacy kit profile\n'

test_session_start_hook_outputs_codex_json
printf 'ok - session start hook outputs Codex JSON\n'

test_claude_local_allows_tracked_project_claude_files
printf 'ok - claude local allows tracked project Claude files\n'

test_claude_local_refreshes_legacy_ignore_block
printf 'ok - claude local refreshes legacy ignore block\n'

test_doctor_reports_missing_adapter_as_warning
printf 'ok - doctor reports missing adapter as warning\n'

test_doctor_ok_after_local_install
printf 'ok - doctor ok after local install\n'

test_doctor_fix_refreshes_stale_adapter_version
printf 'ok - doctor --fix refreshes stale adapter version\n'

test_doctor_openmemory_smoke_dry_run
printf 'ok - doctor openmemory smoke dry-run\n'

test_claude_commands_use_current_worktree_root
printf 'ok - claude commands use current worktree root\n'

test_pack_embeds_local_memory_with_budget
printf 'ok - pack embeds local memory with budget\n'

test_pack_skips_unmatched_local_memory
printf 'ok - pack skips unmatched local memory\n'

test_memory_suggest_skip_records_decision_only
printf 'ok - memory suggest skip records decision only\n'

test_memory_suggest_write_local_card
printf 'ok - memory suggest writes local card\n'

test_pack_includes_changed_file_excerpts
printf 'ok - pack includes changed file excerpts\n'

test_pack_sorts_changed_files_by_goal
printf 'ok - pack sorts changed files by goal\n'

test_pack_falls_back_from_missing_legacy_kit_profile
printf 'ok - pack falls back from missing legacy kit profile\n'

test_pack_rejects_missing_non_kit_profile
printf 'ok - pack rejects missing non-kit profile\n'

test_pack_uses_model_preset_budget
printf 'ok - pack uses model preset budget\n'

test_pack_uses_lite_model_preset_budget
printf 'ok - pack uses lite model preset budget\n'

test_pack_uses_deepseek_model_preset_budget
printf 'ok - pack uses deepseek model preset budget\n'

test_profile_init_ignores_non_python_tests_dir
printf 'ok - profile init ignores shell-only tests dir\n'

test_profile_init_writes_user_config_by_default
printf 'ok - profile init writes user config by default\n'

test_profile_init_can_write_project_config
printf 'ok - profile init can write project config\n'

test_run_prefers_project_agent_rails_profile
printf 'ok - run prefers project .agent-rails profile\n'

test_run_uses_user_agent_rails_profile
printf 'ok - run uses user .agent-rails profile\n'
