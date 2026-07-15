# Task Pack, memory, profile, and project-context tests.

test_target_project_context_module_contract() {
  local repo="$TMP_ROOT/target-project-context"
  local nested="$repo/nested/path"
  local profile="$TMP_ROOT/target-project-context.profile"
  local missing_profile="$TMP_ROOT/missing-target-project-context.profile"
  local config_home="$TMP_ROOT/target-project-context-home"
  local repo_abs expected_slug output
  mkdir -p "$nested"
  git -C "$repo" init -q
  printf '# target project\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  repo_abs="$(cd "$repo" && pwd -P)"
  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="profile-project"\n'
    printf 'AGENT_RAILS_CONFIG_HOME="%s"\n' "$config_home"
  } > "$profile"

  (
    unset PROJECT_ROOT PROJECT_NAME PROJECT_WORKTREE_SLUG TASK_PACK_PATH
    AGENT_RAILS_HOME="$ROOT_DIR"
    # shellcheck source=scripts/agent-paths.sh
    source "$ROOT_DIR/scripts/agent-paths.sh"
    # shellcheck source=scripts/agent-target-project.sh
    source "$ROOT_DIR/scripts/agent-target-project.sh"

    unset AGENT_TARGET_PROJECT_PROFILE_PATH
    if output="$(agent_target_project_load_profile 2>&1)"; then
      printf 'Expected Profile loading before Target Project resolution to fail.\n' >&2
      exit 1
    fi
    assert_contains "$output" "Resolve a Target Project before loading its Profile."

    agent_target_project_resolve "$nested" "$profile"
    [[ "$AGENT_TARGET_PROJECT_ROOT" == "$repo_abs" ]]
    [[ "$AGENT_TARGET_PROJECT_DEFAULT_NAME" == "target-project-context" ]]
    [[ "$AGENT_TARGET_PROJECT_PROFILE_PATH" == "$profile" ]]
    [[ "$AGENT_TARGET_PROJECT_IS_GIT_REPO" -eq 1 ]]
    [[ "$AGENT_TARGET_PROJECT_PROFILE_STATUS" == "unloaded" ]]

    agent_target_project_load_profile
    expected_slug="$(agent_rails_project_worktree_slug "$repo_abs" "profile-project")"
    [[ "$PROJECT_ROOT" == "$repo_abs" ]]
    [[ "$PROJECT_NAME" == "profile-project" ]]
    [[ "$PROJECT_WORKTREE_SLUG" == "$expected_slug" ]]
    [[ "$AGENT_TARGET_PROJECT_PROFILE_STATUS" == "loaded" ]]
    [[ "$AGENT_TARGET_PROJECT_TASK_PACK_PATH" == "$config_home/agent-context/$expected_slug-task-pack.md" ]]

    unset PROJECT_ROOT PROJECT_NAME PROJECT_WORKTREE_SLUG TASK_PACK_PATH
    PROJECT_WORKTREE_SLUG="explicit-worktree"
    agent_target_project_resolve "$repo" "$profile"
    agent_target_project_load_profile
    [[ "$PROJECT_WORKTREE_SLUG" == "explicit-worktree" ]]

    unset PROJECT_ROOT PROJECT_NAME PROJECT_WORKTREE_SLUG TASK_PACK_PATH
    agent_target_project_resolve "$repo" "$missing_profile"
    if agent_target_project_load_profile; then
      printf 'Expected a missing Target Project Profile to fail.\n' >&2
      exit 1
    fi
    [[ "$AGENT_TARGET_PROJECT_PROFILE_STATUS" == "missing" ]]
  )
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

test_pack_excerpts_prioritize_changed_hunks() {
  local repo="$TMP_ROOT/pack-diff-excerpts"
  local output="$TMP_ROOT/pack-diff-excerpts-task-pack.md"
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  for line_number in $(seq 1 80); do
    printf 'unchanged filler line %s keeps the real change outside a prefix excerpt\n' "$line_number"
  done > "$repo/src/app.txt"
  git -C "$repo" add src/app.txt
  git_commit "$repo" init
  printf 'new behavior at the end of the tracked file\n' >> "$repo/src/app.txt"

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --pack-mode lite \
    "inspect the changed behavior" >/dev/null

  assert_file_contains "$output" "## Changed File Excerpts"
  assert_file_contains "$output" '+new behavior at the end of the tracked file'
  assert_file_not_contains "$output" "unchanged filler line 1 keeps"
}

test_pack_redacts_sensitive_changed_hunks() {
  local repo="$TMP_ROOT/pack-sensitive-excerpts"
  local output="$TMP_ROOT/pack-sensitive-excerpts-task-pack.md"
  mkdir -p "$repo/config"
  git -C "$repo" init -q
  printf 'OPENMEMORY_ACCESS_KEY=${OPENMEMORY_ACCESS_KEY}\n' > "$repo/config/runtime.env"
  git -C "$repo" add config/runtime.env
  git_commit "$repo" init
  {
    printf 'OPENMEMORY_ACCESS_KEY=unit-test-pack-secret-123456\n'
    printf '%s\n' '-----BEGIN PRIVATE KEY-----'
    printf 'unit-test-pack-private-key-material-123456\n'
    printf '%s\n' '-----END PRIVATE KEY-----'
  } > "$repo/config/runtime.env"

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --pack-mode lite \
    "inspect runtime credential config" >/dev/null

  assert_file_contains "$output" '+OPENMEMORY_ACCESS_KEY=<redacted>'
  assert_file_contains "$output" '+<redacted private key block>'
  assert_file_not_contains "$output" 'unit-test-pack-secret-123456'
  assert_file_not_contains "$output" 'unit-test-pack-private-key-material-123456'
}

test_pack_truncation_preserves_utf8() {
  local repo="$TMP_ROOT/pack-utf8-truncation"
  local output="$TMP_ROOT/pack-utf8-truncation-task-pack.md"
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  for line_number in $(seq 1 40); do
    printf '第%s行：中文摘录必须在完整字符和完整行边界截断，不能产生非法字节。\n' "$line_number"
  done > "$repo/src/chinese.txt"

  "$AGENT_RAILS_BIN" pack --project "$repo" --output "$output" --pack-mode lite \
    "检查中文摘录截断" >/dev/null

  iconv -f UTF-8 -t UTF-8 "$output" >/dev/null
  assert_file_contains "$output" 'truncated by Agent Rails budget'
}

test_pack_sorts_changed_files_by_goal() {
  local repo="$TMP_ROOT/pack-smart-sort"
  local output="$TMP_ROOT/pack-smart-sort-task-pack.md"
  local content_repo="$TMP_ROOT/pack-smart-content-sort"
  local content_output="$TMP_ROOT/pack-smart-content-sort-task-pack.md"
  local script_line
  local doc_line
  local alpha_line
  local zeta_line
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

  mkdir -p "$content_repo/scripts"
  git -C "$content_repo" init -q
  printf '#!/usr/bin/env bash\nprintf "base alpha\\n"\n' > "$content_repo/scripts/alpha.sh"
  printf '#!/usr/bin/env bash\nprintf "base zeta\\n"\n' > "$content_repo/scripts/zeta.sh"
  git -C "$content_repo" add scripts
  git_commit "$content_repo" init
  printf 'printf "LATENCY REGRESSION guard\\n"\n' >> "$content_repo/scripts/alpha.sh"
  printf 'printf "generic update\\n"\n' >> "$content_repo/scripts/zeta.sh"

  "$AGENT_RAILS_BIN" pack --project "$content_repo" --output "$content_output" --pack-mode lite \
    "latency regression" >/dev/null

  assert_file_contains "$content_output" 'scripts/alpha.sh` score=185'
  assert_file_contains "$content_output" 'change:latency, change:regression'
  alpha_line="$(grep -n '^- `scripts/alpha.sh`' "$content_output" | head -n1 | cut -d: -f1)"
  zeta_line="$(grep -n '^- `scripts/zeta.sh`' "$content_output" | head -n1 | cut -d: -f1)"
  if [[ -z "$alpha_line" || -z "$zeta_line" || "$alpha_line" -ge "$zeta_line" ]]; then
    printf 'Expected changed-content matches to prioritize scripts/alpha.sh.\n' >&2
    sed -n '/## Changed File Priority/,/## Changed File Excerpts/p' "$content_output" >&2
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

test_pack_fails_closed_when_output_cannot_be_replaced() {
  local repo="$TMP_ROOT/pack-output-failure"
  local output_dir="$TMP_ROOT/pack-output-destination"
  local output command_succeeded=0 mode_before mode_after
  mkdir -p "$repo" "$output_dir"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  mode_before="$(stat -f '%Lp' "$output_dir" 2>/dev/null || stat -c '%a' "$output_dir")"

  if output="$("$AGENT_RAILS_BIN" pack --project "$repo" --output "$output_dir" "output failure" 2>&1)"; then
    command_succeeded=1
  fi
  mode_after="$(stat -f '%Lp' "$output_dir" 2>/dev/null || stat -c '%a' "$output_dir")"

  # Older implementations chmodded the destination directory before reporting
  # success. Restore cleanup access before asserting the failure contract.
  chmod 700 "$output_dir"
  if [[ "$command_succeeded" -eq 1 ]]; then
    printf 'Expected Task Pack generation to fail for a directory output path.\n%s\n' "$output" >&2
    exit 1
  fi
  assert_not_contains "$output" "Wrote $output_dir"
  if [[ "$mode_after" != "$mode_before" ]]; then
    printf 'Expected failed Task Pack generation to preserve destination mode %s; got %s.\n' "$mode_before" "$mode_after" >&2
    exit 1
  fi
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

test_context_assembler_enforces_token_budget_and_redistributes_unused_shares() {
  local raw="$TMP_ROOT/context-assembler-raw.md"
  local output="$TMP_ROOT/context-assembler-output.md"
  local metadata="$TMP_ROOT/context-assembler-metadata.json"
  local used redistributed
  {
    printf '# Agent Task Pack\n\n'
    printf '## Session Marker\n\nAGENT RAILS: ON\n\n'
    printf '## Goal\n\nKeep the token budget exact.\n\n'
    printf '## Current Git State\n\n- Branch: test\n\n'
    printf '## Changed File Excerpts\n\n'
    for line_number in $(seq 1 80); do
      printf 'git-evidence-%02d-abcdefghijklmnopqrstuvwxyz\n' "$line_number"
    done
    printf '\n## Agent Rails Contract\n\n- Preserve required rules.\n\n'
    printf '## Memory Cards\n\n- No local cards selected.\n\n'
    printf '## Verification Suggestions\n\n- Run the focused test.\n\n'
    printf '## Delivery Checklist\n\n- What changed\n'
  } > "$raw"

  python3 "$ROOT_DIR/scripts/agent-context-assemble.py" \
    --input "$raw" \
    --output "$output" \
    --metadata "$metadata" \
    --budget-tokens 420 \
    --tokenizer char \
    --chars-per-token 1

  used="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["used_tokens"])' "$metadata")"
  redistributed="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["redistributed_tokens"])' "$metadata")"
  [[ "$used" -le 420 ]]
  [[ "$redistributed" -gt 0 ]]
  assert_file_contains "$output" "## Goal"
  assert_file_contains "$output" "Keep the token budget exact."
  assert_file_contains "$output" "## Agent Rails Contract"
  assert_file_contains "$output" "git-evidence-"
}

test_context_assembler_server_caches_token_counts() {
  local counter="$TMP_ROOT/tokenizer-counter.sh"
  local count_file="$TMP_ROOT/tokenizer-counter.count"
  local result="$TMP_ROOT/tokenizer-server-result.json"
  cat > "$counter" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail
count_file="${TOKENIZER_COUNT_FILE:?}"
current=0
[[ ! -f "$count_file" ]] || current="$(cat "$count_file")"
printf '%s\n' "$((current + 1))" > "$count_file"
wc -c < "$AGENT_RAILS_TOKENIZER_INPUT" | tr -d '[:space:]'
SCRIPT
  chmod +x "$counter"

  TOKENIZER_COUNT_FILE="$count_file" python3 - "$ROOT_DIR/scripts/agent-context-assemble.py" "$counter" "$result" <<'PY'
import json
import subprocess
import sys

assembler, counter, result = sys.argv[1:]
proc = subprocess.Popen(
    [sys.executable, assembler, "--serve", "--tokenizer", "command", "--tokenizer-command", counter],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    text=True,
)
responses = []
for request_id in ("one", "two"):
    proc.stdin.write(json.dumps({"id": request_id, "action": "count", "text": "same-content"}) + "\n")
    proc.stdin.flush()
    responses.append(json.loads(proc.stdout.readline()))
proc.stdin.close()
proc.terminate()
proc.wait(timeout=5)
with open(result, "w", encoding="utf-8") as handle:
    json.dump(responses, handle)
PY

  [[ "$(cat "$count_file")" -eq 1 ]]
  assert_file_contains "$result" '"cache_hit": true'
}

test_pack_token_budget_uses_token_assembler() {
  local repo="$TMP_ROOT/pack-token-assembler"
  local output="$TMP_ROOT/pack-token-assembler-task-pack.md"
  local chars
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  for line_number in $(seq 1 120); do
    printf 'token-aware-evidence-%03d-abcdefghijklmnopqrstuvwxyz\n' "$line_number"
  done > "$repo/src/large.txt"

  AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE=1 "$AGENT_RAILS_BIN" pack \
    --project "$repo" \
    --output "$output" \
    --token-budget 1400 \
    --tokenizer char \
    "token aware pack" >/dev/null

  chars="$(LC_ALL=en_US.UTF-8 wc -m < "$output" | tr -d '[:space:]')"
  [[ "$chars" -le 1400 ]]
  assert_file_contains "$output" "## Goal"
  assert_file_contains "$output" "token aware pack"
  assert_file_contains "$output" "Token allocator:"
}

test_pack_candidate_output_defers_hard_budget_to_request_hook() {
  local repo="$TMP_ROOT/pack-candidate-output"
  local output="$TMP_ROOT/pack-candidate-output-task-pack.md"
  local chars
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init
  for line_number in $(seq 1 40); do
    printf 'candidate-evidence-%03d-abcdefghijklmnopqrstuvwxyz\n' "$line_number"
  done > "$repo/src/large.txt"

  AGENT_RAILS_CANDIDATE_OUTPUT=1 \
    AGENT_RAILS_CONTEXT_BUDGET_TOKENS=200 \
    AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE=1 \
    "$AGENT_RAILS_BIN" pack \
      --project "$repo" \
      --output "$output" \
      --tokenizer char \
      "request hook candidates" >/dev/null

  chars="$(LC_ALL=en_US.UTF-8 wc -m < "$output" | tr -d '[:space:]')"
  [[ "$chars" -gt 200 ]]
  assert_file_contains "$output" "Mode: candidate output"
  assert_file_not_contains "$output" "Token allocator:"
  assert_file_contains "$output" "candidate-evidence-"
}

test_pack_modes_bound_size_without_dropping_capabilities() {
  local repo="$TMP_ROOT/pack-mode-density"
  local profile="$TMP_ROOT/pack-mode-density.profile"
  local mode output size
  local lite_size normal_size deep_size audit_size
  mkdir -p "$repo/src"
  git -C "$repo" init -q
  printf '# temp\n' > "$repo/README.md"
  git -C "$repo" add README.md
  git_commit "$repo" init

  for file_number in 1 2 3 4 5 6 7 8; do
    for line_number in $(seq 1 80); do
      printf 'file-%s line-%s token-budget fixture keeps changed source excerpts useful for orientation\n' \
        "$file_number" "$line_number"
    done > "$repo/src/file-$file_number.txt"
  done

  {
    printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n'
    printf 'PROJECT_NAME="pack-mode-density"\n'
    printf 'AGENT_RAILS_WORKFLOW_RULES="CUSTOM PROFILE RULE: inspect the runtime seam before editing."\n'
  } > "$profile"

  for mode in lite normal deep audit; do
    output="$TMP_ROOT/pack-mode-density-$mode.md"
    "$AGENT_RAILS_BIN" pack --project "$repo" --profile "$profile" --output "$output" --pack-mode "$mode" \
      "reduce Task Pack tokens without losing capability" >/dev/null

    for heading in \
      "## Goal" \
      "## Current Git State" \
      "## Changed File Priority" \
      "## Changed File Excerpts" \
      "## Relevant Entry Docs" \
      "## Agent Rails Contract" \
      "### Grill Gate" \
      "## Subagent Result Contract" \
      "## Memory Cards" \
      "## Verification Suggestions" \
      "## Delivery Checklist"; do
      assert_file_contains "$output" "$heading"
    done
    assert_file_contains "$output" "CUSTOM PROFILE RULE: inspect the runtime seam before editing."
    size="$(wc -c < "$output" | tr -d ' ')"
    case "$mode" in
      lite) lite_size="$size" ;;
      normal) normal_size="$size" ;;
      deep) deep_size="$size" ;;
      audit) audit_size="$size" ;;
    esac
  done

  if [[ "$lite_size" -gt 16000 || "$normal_size" -gt 32000 || "$deep_size" -gt 32000 ]]; then
    printf 'Expected compact packs; got lite=%s normal=%s deep=%s chars.\n' \
      "$lite_size" "$normal_size" "$deep_size" >&2
    exit 1
  fi
  if [[ "$lite_size" -ge "$normal_size" || "$normal_size" -ge "$deep_size" || "$deep_size" -ge "$audit_size" ]]; then
    printf 'Expected increasing pack density; got lite=%s normal=%s deep=%s audit=%s chars.\n' \
      "$lite_size" "$normal_size" "$deep_size" "$audit_size" >&2
    exit 1
  fi
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

run_context_tests() {
  run_test test_target_project_context_module_contract "shared Target Project Context module contract"
  run_test test_claude_commands_use_current_worktree_root "claude commands use current worktree root"
  run_test test_pack_embeds_local_memory_with_budget "pack embeds local memory with budget"
  run_test test_pack_skips_unmatched_local_memory "pack skips unmatched local memory"
  run_test test_memory_suggest_skip_records_decision_only "memory suggest skip records decision only"
  run_test test_memory_suggest_write_local_card "memory suggest writes local card"
  run_test test_pack_includes_changed_file_excerpts "pack includes changed file excerpts"
  run_test test_pack_excerpts_prioritize_changed_hunks "pack excerpts prioritize changed hunks"
  run_test test_pack_redacts_sensitive_changed_hunks "pack redacts sensitive changed hunks"
  run_test test_pack_truncation_preserves_utf8 "pack truncation preserves UTF-8"
  run_test test_pack_sorts_changed_files_by_goal "pack sorts changed files by goal"
  run_test test_pack_falls_back_from_missing_legacy_kit_profile "pack falls back from missing legacy kit profile"
  run_test test_pack_rejects_missing_non_kit_profile "pack rejects missing non-kit profile"
  run_test test_pack_fails_closed_when_output_cannot_be_replaced "pack fails closed when output cannot be replaced"
  run_test test_pack_uses_model_preset_budget "pack uses model preset budget"
  run_test test_pack_uses_lite_model_preset_budget "pack uses lite model preset budget"
  run_test test_pack_uses_deepseek_model_preset_budget "pack uses deepseek model preset budget"
  run_test test_context_assembler_enforces_token_budget_and_redistributes_unused_shares "context assembler enforces token budget and redistributes unused shares"
  run_test test_context_assembler_server_caches_token_counts "context assembler server caches token counts"
  run_test test_pack_token_budget_uses_token_assembler "pack token budget uses token assembler"
  run_test test_pack_candidate_output_defers_hard_budget_to_request_hook "pack candidate output defers hard budget"
  run_test test_pack_modes_bound_size_without_dropping_capabilities "pack modes bound size without dropping capabilities"
  run_test test_profile_init_ignores_non_python_tests_dir "profile init ignores shell-only tests dir"
  run_test test_profile_init_writes_user_config_by_default "profile init writes user config by default"
  run_test test_profile_init_can_write_project_config "profile init can write project config"
  run_test test_run_prefers_project_agent_rails_profile "run prefers project .agent-rails profile"
  run_test test_run_uses_user_agent_rails_profile "run uses user .agent-rails profile"
}
