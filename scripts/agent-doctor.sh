#!/usr/bin/env bash
# Diagnose Agent Rails setup for a target project. This script does not write files.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails doctor [--project PATH] [--profile PATH] [--online-memory-smoke] [--fix] [--mode local|project] [--session-hook] [--global-reminder] [--dry-run]

Checks project/profile wiring, Claude adapter files, local ignore status, skills,
model presets, optional online memory readiness, and required command-line tools.

--fix refreshes the Claude adapter and bundled skills for the target project.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
agent_rails_kit_home="$AGENT_RAILS_HOME"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-model-presets.sh
source "$AGENT_RAILS_HOME/scripts/agent-model-presets.sh"
agent_rails_init_paths
AGENT_RAILS_VERSION="$(agent_rails_version)"

resolve_target_project_context() {
  [[ "$#" -eq 6 ]] || return 2
  local requested_project="$1"
  local requested_profile="$2"
  local config_home="$3"
  local project_name="$4"
  local worktree_slug_preset="$5"
  local task_pack_path="$6"
  local target_context_assignments
  local target_context_args=(
    --project "$requested_project"
    --agent-rails-home "$agent_rails_kit_home"
    --skip-profile-load
    --shell
  )
  if [[ -n "$requested_profile" ]]; then
    target_context_args+=(--profile "$requested_profile")
  fi
  target_context_assignments="$({
    AGENT_RAILS_CONFIG_HOME="$config_home" \
    PROJECT_NAME="$project_name" \
    PROJECT_WORKTREE_SLUG="$worktree_slug_preset" \
    TASK_PACK_PATH="$task_pack_path" \
    PYTHONDONTWRITEBYTECODE=1 \
      python3 -E "$agent_rails_kit_home/scripts/agent-python-cli.py" \
        target-context "${target_context_args[@]}"
  })" || return $?
  eval "$target_context_assignments"
}

project="$PWD"
profile_path=""
online_memory_smoke=0
fix=0
fix_mode="local"
fix_session_hook=0
fix_global_reminder=0
fix_dry_run=0
failures=0
warnings=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path="$2"
      shift 2
      ;;
    --online-memory-smoke)
      online_memory_smoke=1
      shift
      ;;
    --fix)
      fix=1
      shift
      ;;
    --mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        local|project) fix_mode="$2" ;;
        *) usage >&2; exit 2 ;;
      esac
      shift 2
      ;;
    --session-hook)
      fix_session_hook=1
      shift
      ;;
    --global-reminder)
      fix_global_reminder=1
      shift
      ;;
    --dry-run)
      fix_dry_run=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

ok() {
  printf '[OK] %s\n' "$1"
}

warn() {
  warnings=$((warnings + 1))
  printf '[WARN] %s\n' "$1"
}

fail() {
  failures=$((failures + 1))
  printf '[FAIL] %s\n' "$1"
}

info() {
  printf '[INFO] %s\n' "$1"
}

print_command() {
  local first=1 arg
  for arg in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      first=0
    else
      printf ' '
    fi
    printf '%q' "$arg"
  done
  printf '\n'
}

command_status() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "command available: $command_name"
  else
    warn "command missing: $command_name"
  fi
}

read_manifest_version() {
  local manifest_path="$1"
  [[ -f "$manifest_path" ]] || return 1
  sed -nE 's/^[[:space:]]*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/p' "$manifest_path" | head -n 1
}

check_manifest_version() {
  local label="$1"
  local manifest_path="$2"
  local manifest_version
  if [[ ! -f "$manifest_path" ]]; then
    warn "$label manifest missing: $manifest_path"
    return 0
  fi
  manifest_version="$(read_manifest_version "$manifest_path" || true)"
  if [[ -z "$manifest_version" ]]; then
    warn "$label manifest has no version: $manifest_path"
  elif [[ "$manifest_version" == "$AGENT_RAILS_VERSION" ]]; then
    ok "$label manifest version: $manifest_version"
  else
    warn "$label manifest version $manifest_version differs from kit version $AGENT_RAILS_VERSION."
  fi
}

read_adapter_version() {
  local path
  for path in "$guide_path" "$claude_local_md_path" "$claude_project_md_path"; do
    [[ -f "$path" ]] || continue
    sed -nE 's/^Agent Rails Version:[[:space:]]*`?([^`[:space:]]+)`?.*/\1/p' "$path" | head -n 1
  done | awk 'NF { print; exit }'
}

provider_uses_online_memory() {
  case "${MEMORY_PROVIDER:-local}" in
    online|hybrid) return 0 ;;
    *) return 1 ;;
  esac
}

run_online_memory_smoke() {
  if ! provider_uses_online_memory; then
    warn "Online memory smoke requested but MEMORY_PROVIDER is not online/hybrid."
    return 0
  fi

  if [[ -z "${AGENT_RAILS_ONLINE_MEMORY_CMD:-}" ]]; then
    return 0
  fi

  local tmp_dir query_file output_file timeout_seconds
  tmp_dir="$(mktemp -d)"
  query_file="$tmp_dir/query.md"
  output_file="$tmp_dir/output.md"
  printf 'Agent Rails Doctor online memory smoke.\n' > "$query_file"
  timeout_seconds="${AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS:-8}"
  if ! [[ "$timeout_seconds" =~ ^[0-9]+$ && "$timeout_seconds" -gt 0 ]]; then
    timeout_seconds=8
  fi

  if PYTHONDONTWRITEBYTECODE=1 \
    python3 -E "$AGENT_RAILS_HOME/scripts/agent-python-cli.py" online-memory \
      --command "$AGENT_RAILS_ONLINE_MEMORY_CMD" \
      --query-file "$query_file" \
      --project "$PROJECT_NAME" \
      --limit 1 \
      --timeout-seconds "$timeout_seconds" \
      --output "$output_file" >/dev/null 2>&1; then
    ok "Online memory smoke read OK."
  else
    warn "Online memory smoke failed; adapter diagnostics were suppressed."
  fi
  rm -rf "$tmp_dir"
}

printf 'Agent Rails Doctor\n\n'

if [[ -d "$AGENT_RAILS_HOME" ]]; then
  ok "Agent Rails home: $AGENT_RAILS_HOME"
else
  fail "Agent Rails home not found: $AGENT_RAILS_HOME"
fi

if [[ -x "$AGENT_RAILS_BIN" ]]; then
  ok "Agent Rails CLI: $AGENT_RAILS_BIN"
else
  fail "Agent Rails CLI is not executable: $AGENT_RAILS_BIN"
fi
ok "Kit version: $AGENT_RAILS_VERSION"

if [[ ! -d "$project" ]]; then
  fail "project directory not found: $project"
  printf '\nDoctor status: FAIL (%s failure(s), %s warning(s))\n' "$failures" "$warnings"
  exit 1
fi

resolve_target_project_context \
  "$project" \
  "$profile_path" \
  "$AGENT_RAILS_CONFIG_HOME" \
  "${PROJECT_NAME:-}" \
  "${PROJECT_WORKTREE_SLUG:-}" \
  "${TASK_PACK_PATH:-}" || exit $?
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"
is_git_repo="$AGENT_TARGET_PROJECT_IS_GIT_REPO"
PROJECT_WORKTREE_SLUG_PRESET="$AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET"
ok "Project: $project_abs"

if [[ "$is_git_repo" -eq 1 ]]; then
  ok "Git repository: $project_abs"
else
  warn "No git repository detected; diff-based pack/check output will be limited."
fi

if [[ -f "$profile_path" ]]; then
  ok "Profile: $profile_path"
else
  fail "Profile not found: $profile_path"
fi

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  if ! source "$profile_path"; then
    fail "Profile could not be sourced: $profile_path"
  fi
fi

AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-}"
if [[ -n "$AGENT_RAILS_ENV_FILE" ]]; then
  if [[ -f "$AGENT_RAILS_ENV_FILE" ]]; then
    ok "Env file: $AGENT_RAILS_ENV_FILE"
    # shellcheck source=/dev/null
    if ! source "$AGENT_RAILS_ENV_FILE"; then
      fail "Env file could not be sourced: $AGENT_RAILS_ENV_FILE"
    fi
  else
    warn "Env file configured but missing: $AGENT_RAILS_ENV_FILE"
  fi
else
  info "No Agent Rails env file configured."
fi

PROJECT_NAME="${PROJECT_NAME:-$AGENT_TARGET_PROJECT_DEFAULT_NAME}"
resolve_target_project_context \
  "$project_abs" \
  "$profile_path" \
  "$AGENT_RAILS_CONFIG_HOME" \
  "$PROJECT_NAME" \
  "$PROJECT_WORKTREE_SLUG_PRESET" \
  "${TASK_PACK_PATH:-}" || exit $?
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"
is_git_repo="$AGENT_TARGET_PROJECT_IS_GIT_REPO"
TASK_PACK_PATH="$AGENT_TARGET_PROJECT_TASK_PACK_PATH"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"
AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-generic}"
AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-normal}"

case "$AGENT_RAILS_PACK_MODE" in
  lite|normal|deep|audit)
    ok "Pack mode: $AGENT_RAILS_PACK_MODE"
    ;;
  *)
    warn "Unknown pack mode: $AGENT_RAILS_PACK_MODE (expected lite, normal, deep, or audit)"
    ;;
esac

if agent_model_preset_known "$AGENT_RAILS_MODEL"; then
  ok "Model preset: $AGENT_RAILS_MODEL"
else
  warn "Unknown model preset: $AGENT_RAILS_MODEL"
fi

info "Task Pack path: $TASK_PACK_PATH"

printf '\nTools\n'
command_status git
command_status awk
command_status sed

printf '\nPlugin Manifests\n'
check_manifest_version "Codex plugin" "$AGENT_RAILS_HOME/.codex-plugin/plugin.json"
check_manifest_version "Claude plugin" "$AGENT_RAILS_HOME/.claude-plugin/plugin.json"
check_manifest_version "Codex marketplace plugin" "$AGENT_RAILS_HOME/codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json"

printf '\nMemory\n'
ok "Memory provider: $MEMORY_PROVIDER"
if provider_uses_online_memory; then
  AGENT_RAILS_ONLINE_MEMORY_CMD="${AGENT_RAILS_ONLINE_MEMORY_CMD:-}"
  if [[ -n "$AGENT_RAILS_ONLINE_MEMORY_CMD" ]]; then
    ok "Online memory command configured."
  else
    warn "AGENT_RAILS_ONLINE_MEMORY_CMD is not configured."
  fi
fi
if [[ "$online_memory_smoke" -eq 1 ]]; then
  run_online_memory_smoke
elif provider_uses_online_memory; then
  info "Online memory smoke not requested; pass --online-memory-smoke to test the read path."
fi

printf '\nClaude Adapter\n'
claude_dir="$project_abs/.claude"
skills_dir="$claude_dir/skills"
guide_path="$claude_dir/AGENT_RAILS.md"
pack_command_path="$claude_dir/commands/agent-rails-pack.md"
lite_command_path="$claude_dir/commands/agent-rails-lite.md"
check_command_path="$claude_dir/commands/agent-rails-check.md"
claude_project_md_path="$project_abs/CLAUDE.md"
claude_local_md_path="$project_abs/CLAUDE.local.md"
claude_settings_path="${AGENT_RAILS_CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
claude_user_md_path="${AGENT_RAILS_CLAUDE_USER_MD:-$HOME/.claude/CLAUDE.md}"
session_hook_path="$AGENT_RAILS_HOME/hooks/agent-rails-session-start.sh"
adapter_version="$(read_adapter_version)"
existing_session_hook=0
existing_global_reminder=0

[[ -f "$guide_path" ]] && ok "Claude guide installed: $guide_path" || warn "Missing Claude guide: $guide_path"
[[ -f "$pack_command_path" ]] && ok "Claude pack command installed." || warn "Missing Claude pack command: $pack_command_path"
[[ -f "$lite_command_path" ]] && ok "Claude lite command installed." || warn "Missing Claude lite command: $lite_command_path"
[[ -f "$check_command_path" ]] && ok "Claude check command installed." || warn "Missing Claude check command: $check_command_path"
if [[ -f "$claude_local_md_path" ]] && grep -q '<!-- agent-rails:start -->' "$claude_local_md_path"; then
  ok "CLAUDE.local.md contains Agent Rails block."
elif [[ -f "$claude_project_md_path" ]] && grep -q '<!-- agent-rails:start -->' "$claude_project_md_path"; then
  ok "CLAUDE.md contains Agent Rails block."
else
  warn "CLAUDE.local.md/CLAUDE.md Agent Rails block is missing."
fi
if [[ -n "$adapter_version" ]]; then
  if [[ "$adapter_version" == "$AGENT_RAILS_VERSION" ]]; then
    ok "Claude adapter version: $adapter_version"
  else
    warn "Claude adapter version $adapter_version differs from kit version $AGENT_RAILS_VERSION; run doctor --fix."
  fi
elif [[ -f "$guide_path" || -f "$claude_local_md_path" || -f "$claude_project_md_path" ]]; then
  warn "Claude adapter version missing; run doctor --fix."
fi

if [[ -f "$guide_path" ]] && ! grep -Fq "$profile_path" "$guide_path"; then
  warn "Claude guide does not reference current profile path."
fi
if [[ -f "$pack_command_path" ]] && grep -Fq "$project_abs" "$pack_command_path"; then
  warn "Claude pack command hardcodes this install path; upgrade adapter to make worktrees safe."
fi
if [[ -f "$pack_command_path" ]] && ! grep -Fq 'git rev-parse --show-toplevel' "$pack_command_path"; then
  warn "Claude pack command does not resolve the current git worktree root."
fi

if [[ -f "$claude_settings_path" ]] && grep -Fq "agent-rails-session-start.sh" "$claude_settings_path"; then
  existing_session_hook=1
  ok "Claude SessionStart hook installed: $claude_settings_path"
  if ! grep -Fq "$session_hook_path" "$claude_settings_path"; then
    warn "Claude SessionStart hook points to a different Agent Rails path; reinstall with --session-hook if this kit moved."
  fi
else
  info "Claude SessionStart hook not installed; pass --session-hook to inject Agent Rails as startup context."
fi
if [[ -f "$claude_user_md_path" ]] && grep -Fq '<!-- agent-rails:global-reminder:start -->' "$claude_user_md_path"; then
  existing_global_reminder=1
fi

printf '\nSkills\n'
source_skills_dir="$AGENT_RAILS_HOME/skills"
if [[ -d "$source_skills_dir" ]]; then
  missing_skills=0
  while IFS= read -r skill_dir; do
    skill_name="$(basename "$skill_dir")"
    if [[ -f "$skills_dir/$skill_name/SKILL.md" ]]; then
      ok "skill installed: $skill_name"
    else
      missing_skills=$((missing_skills + 1))
      warn "skill missing from project: $skill_name"
    fi
  done < <(find "$source_skills_dir" -mindepth 1 -maxdepth 1 -type d | sort)
  if [[ "$missing_skills" -gt 0 ]]; then
    info "Install/update skills: $AGENT_RAILS_BIN skills install --dest \"$skills_dir\""
  fi
else
  fail "Agent Rails source skills dir missing: $source_skills_dir"
fi

printf '\nGit Visibility\n'
if [[ "$is_git_repo" -eq 1 ]]; then
  tracked_agent_files="$({
    git -C "$project_abs" ls-files \
      CLAUDE.local.md \
      .claude/AGENT_RAILS.md \
      .claude/commands/agent-rails-pack.md \
      .claude/commands/agent-rails-lite.md \
      .claude/commands/agent-rails-check.md \
      2>/dev/null || true
    if [[ -d "$source_skills_dir" ]]; then
      while IFS= read -r skill_dir; do
        git -C "$project_abs" ls-files ".claude/skills/$(basename "$skill_dir")" 2>/dev/null || true
      done < <(find "$source_skills_dir" -mindepth 1 -maxdepth 1 -type d | sort)
    fi
  } | awk 'NF' | sort -u)"
  if [[ -f "$claude_project_md_path" ]] && grep -q '<!-- agent-rails:start -->' "$claude_project_md_path"; then
    tracked_project_claude="$(git -C "$project_abs" ls-files CLAUDE.md 2>/dev/null || true)"
    if [[ -n "$tracked_project_claude" ]]; then
      tracked_agent_files="${tracked_agent_files}${tracked_agent_files:+$'\n'}${tracked_project_claude}"
    fi
  fi
  if [[ -n "$tracked_agent_files" ]]; then
    ok "Agent Rails adapter files are tracked; project mode is plausible."
  elif [[ -e "$claude_dir" || -e "$claude_local_md_path" ]]; then
    if git -C "$project_abs" check-ignore -q .claude/AGENT_RAILS.md 2>/dev/null \
      && git -C "$project_abs" check-ignore -q CLAUDE.local.md 2>/dev/null; then
      ok "Agent Rails adapter files are ignored locally; local mode is plausible."
    else
      warn "Agent Rails adapter files exist but are neither tracked nor ignored."
    fi
  else
    info "No Claude adapter files found yet."
  fi
else
  info "Skipping git visibility checks outside git."
fi

printf '\nSuggested Commands\n'
printf -- '- Generate pack: %s pack --project "%s" --profile "%s" "<goal>"\n' "$AGENT_RAILS_BIN" "$project_abs" "$profile_path"
printf -- '- Check verification plan: %s check --project "%s" --profile "%s" --print-only\n' "$AGENT_RAILS_BIN" "$project_abs" "$profile_path"
printf -- '- Install Claude adapter: %s claude install --project "%s" --profile "%s" --mode local\n' "$AGENT_RAILS_BIN" "$project_abs" "$profile_path"
printf -- '- Install Claude adapter with startup hook: %s claude install --project "%s" --profile "%s" --mode local --session-hook\n' "$AGENT_RAILS_BIN" "$project_abs" "$profile_path"
printf -- '- Fix local Agent Rails adapter: %s doctor --project "%s" --profile "%s" --fix\n' "$AGENT_RAILS_BIN" "$project_abs" "$profile_path"
printf -- '- Preview Claude adapter removal: %s claude uninstall --project "%s" --dry-run\n' "$AGENT_RAILS_BIN" "$project_abs"

if [[ "$fix" -eq 1 ]]; then
  printf '\nFixes\n'
  if [[ "$failures" -gt 0 ]]; then
    warn "Skipping --fix because doctor has failures. Resolve failures first, then rerun doctor --fix."
  else
    fix_args=(--force --project "$project_abs" --profile "$profile_path" --mode "$fix_mode")
    if [[ "$fix_session_hook" -eq 1 || "$existing_session_hook" -eq 1 ]]; then
      fix_args+=(--session-hook)
    fi
    if [[ "$fix_global_reminder" -eq 1 || "$existing_global_reminder" -eq 1 ]]; then
      fix_args+=(--global-reminder)
    fi
    if [[ "$fix_dry_run" -eq 1 ]]; then
      fix_args+=(--dry-run)
      printf 'Would run: '
      print_command "$AGENT_RAILS_HOME/scripts/agent-install-claude.sh" "${fix_args[@]}"
    else
      "$AGENT_RAILS_HOME/scripts/agent-install-claude.sh" "${fix_args[@]}"
      printf 'Doctor fix completed. Re-run doctor to verify a clean state.\n'
    fi
  fi
fi

printf '\n'
if [[ "$failures" -gt 0 ]]; then
  printf 'Doctor status: FAIL (%s failure(s), %s warning(s))\n' "$failures" "$warnings"
  exit 1
elif [[ "$warnings" -gt 0 ]]; then
  printf 'Doctor status: OK with warnings (%s warning(s))\n' "$warnings"
else
  printf 'Doctor status: OK\n'
fi
