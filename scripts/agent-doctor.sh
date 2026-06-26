#!/usr/bin/env bash
# Diagnose Agent Rails setup for a target project. This script does not write files.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails doctor [--project PATH] [--profile PATH] [--openmemory-smoke] [--fix] [--mode local|project] [--session-hook] [--global-reminder] [--dry-run]

Checks project/profile wiring, Claude adapter files, local ignore status, skills,
model presets, OpenMemory readiness, optional OpenMemory read smoke, and required command-line tools.

--fix refreshes the Claude adapter and bundled skills for the target project.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths
AGENT_RAILS_VERSION="$(agent_rails_version)"

project="$PWD"
profile_path=""
openmemory_smoke=0
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
    --openmemory-smoke)
      openmemory_smoke=1
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

resolve_profile() {
  local project_name="$1"
  agent_rails_resolve_profile "$project_abs" "$project_name" "$profile_path"
}

known_model_preset() {
  local model_key
  model_key="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr ' _' '--')"
  case "$model_key" in
    generic|qwen3.7-max|qwen-3.7-max|qwen3.7max|glm5.1|glm-5.1|glm51|deepseek-v4-pro|deepseekv4pro|deepseek-v4pro|deepseek-v4|deepseek4-pro)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

provider_uses_openmemory() {
  case "${MEMORY_PROVIDER:-local}" in
    openmemory|hybrid) return 0 ;;
    *) return 1 ;;
  esac
}

sanitize_openmemory_message() {
  sed -E \
    -e 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<ip>/g' \
    -e 's/[[:space:]]+/ /g' \
    -e 's/^ //' \
    -e 's/ $//' \
    | cut -c 1-220
}

run_openmemory_smoke() {
  if ! provider_uses_openmemory; then
    warn "OpenMemory smoke requested but MEMORY_PROVIDER is not openmemory/hybrid."
    return 0
  fi

  if [[ -z "$OPENMEMORY_BASE_URL" || -z "$OPENMEMORY_MEMORY" || -z "$OPENMEMORY_TABLE" ]]; then
    warn "OpenMemory smoke skipped because base URL, memory, or table is missing."
    return 0
  fi
  if [[ -z "${!OPENMEMORY_TOKEN_ENV-}" ]]; then
    warn "OpenMemory smoke skipped because token env is missing: $OPENMEMORY_TOKEN_ENV"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    warn "OpenMemory smoke skipped because curl and jq are required."
    return 0
  fi

  local tmp_dir request_file response_file error_file base_url token http_code api_code count
  tmp_dir="$(mktemp -d)"
  request_file="$tmp_dir/openmemory-smoke-request.json"
  response_file="$tmp_dir/openmemory-smoke-response.json"
  error_file="$tmp_dir/openmemory-smoke.err"
  trap 'rm -rf "$tmp_dir"; trap - RETURN' RETURN

  jq -n \
    --arg memory "$OPENMEMORY_MEMORY" \
    --arg table "$OPENMEMORY_TABLE" \
    --arg project_filter "${OPENMEMORY_PROJECT_FILTER:-}" \
    --arg user_id "${OPENMEMORY_USER_ID:-agent-rails}" \
    --arg session_id "${OPENMEMORY_SESSION_ID:-}" \
    '({
      memory: $memory,
      table: $table,
      limit: 1,
      field_selector: {
        attributes: {
          mode: "include",
          include: ["card_id", "project", "title", "updated_at"]
        }
      }
    }
    + (if $user_id == "" then {} else {user_id: $user_id} end)
    + (if $session_id == "" then {} else {session_id: $session_id} end)
    + (if $project_filter == "" then {} else {filters: {project: $project_filter}} end))' > "$request_file"

  if [[ "${OPENMEMORY_DRY_RUN_REQUEST:-0}" == "1" ]]; then
    OPENMEMORY_REQUEST_DUMP_PATH="${OPENMEMORY_REQUEST_DUMP_PATH:-$AGENT_RAILS_CONFIG_HOME/agent-context/openmemory-doctor-smoke.json}"
    mkdir -p "$(dirname "$OPENMEMORY_REQUEST_DUMP_PATH")"
    cp "$request_file" "$OPENMEMORY_REQUEST_DUMP_PATH"
    ok "OpenMemory smoke dry-run request written: $OPENMEMORY_REQUEST_DUMP_PATH"
    return 0
  fi

  base_url="${OPENMEMORY_BASE_URL%/}"
  token="${!OPENMEMORY_TOKEN_ENV-}"
  http_code="$(curl -sS -o "$response_file" -w '%{http_code}' \
    --max-time "${OPENMEMORY_TIMEOUT_SECONDS:-8}" \
    -X POST "$base_url/agent-memory/v1/memories/collection/list" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data @"$request_file" 2>"$error_file" || true)"

  if [[ ! "$http_code" =~ ^2 ]]; then
    warn "OpenMemory smoke failed: HTTP ${http_code:-unknown}. $(sanitize_openmemory_message < "$error_file")"
    return 0
  fi

  api_code="$(jq -r '.code // empty' "$response_file" 2>/dev/null || true)"
  if [[ "$api_code" != "OK" ]]; then
    warn "OpenMemory smoke failed: code=${api_code:-unknown} message=$(jq -r '.message // ""' "$response_file" 2>/dev/null | sanitize_openmemory_message)"
    return 0
  fi

  count="$(jq -r '.data.memories // [] | length' "$response_file" 2>/dev/null || printf '0')"
  ok "OpenMemory smoke read OK: $count record(s) visible from $OPENMEMORY_TABLE"
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

project_abs="$(cd "$project" && pwd)"
if git_root_for_project="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
  project_abs="$(cd "$git_root_for_project" && pwd)"
fi
project_name="$(basename "$project_abs")"
PROJECT_ROOT="$project_abs"
PROJECT_NAME="${PROJECT_NAME:-$project_name}"
PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
PROJECT_WORKTREE_SLUG="${PROJECT_WORKTREE_SLUG:-$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")}"
ok "Project: $project_abs"

is_git_repo=0
if command -v git >/dev/null 2>&1 && git -C "$project_abs" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  is_git_repo=1
  git_root="$(git -C "$project_abs" rev-parse --show-toplevel)"
  git_root="$(cd "$git_root" && pwd)"
  ok "Git repository: $git_root"
else
  warn "No git repository detected; diff-based pack/check output will be limited."
fi

profile_path="$(resolve_profile "$project_name")"
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

PROJECT_NAME="${PROJECT_NAME:-$project_name}"
if [[ -n "$PROJECT_WORKTREE_SLUG_PRESET" ]]; then
  PROJECT_WORKTREE_SLUG="$PROJECT_WORKTREE_SLUG_PRESET"
else
  PROJECT_WORKTREE_SLUG="$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")"
fi
TASK_PACK_PATH="${TASK_PACK_PATH:-$(agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG")}"
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

if known_model_preset "$AGENT_RAILS_MODEL"; then
  ok "Model preset: $AGENT_RAILS_MODEL"
else
  warn "Unknown model preset: $AGENT_RAILS_MODEL"
fi

info "Task Pack path: $TASK_PACK_PATH"

printf '\nTools\n'
command_status git
command_status awk
command_status sed
if provider_uses_openmemory; then
  command_status curl
  command_status jq
fi

printf '\nPlugin Manifests\n'
check_manifest_version "Codex plugin" "$AGENT_RAILS_HOME/.codex-plugin/plugin.json"
check_manifest_version "Claude plugin" "$AGENT_RAILS_HOME/.claude-plugin/plugin.json"
check_manifest_version "Codex marketplace plugin" "$AGENT_RAILS_HOME/codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json"

printf '\nOpenMemory\n'
if provider_uses_openmemory; then
  ok "Memory provider: $MEMORY_PROVIDER"
  OPENMEMORY_BASE_URL="${OPENMEMORY_BASE_URL:-}"
  OPENMEMORY_MEMORY="${OPENMEMORY_MEMORY:-}"
  OPENMEMORY_INSTANCE="${OPENMEMORY_INSTANCE:-agent_rails_memory_card}"
  OPENMEMORY_TABLE="${OPENMEMORY_TABLE:-}"
  OPENMEMORY_TOKEN_ENV="${OPENMEMORY_TOKEN_ENV:-OPENMEMORY_ACCESS_KEY}"
  OPENMEMORY_DRY_RUN_REQUEST="${OPENMEMORY_DRY_RUN_REQUEST:-0}"
    OPENMEMORY_REQUEST_DUMP_PATH="${OPENMEMORY_REQUEST_DUMP_PATH:-$AGENT_RAILS_CONFIG_HOME/agent-context/openmemory-doctor-smoke.json}"
  if [[ -z "$OPENMEMORY_TABLE" && -n "$OPENMEMORY_MEMORY" && -n "$OPENMEMORY_INSTANCE" ]]; then
    OPENMEMORY_TABLE="${OPENMEMORY_MEMORY}.${OPENMEMORY_INSTANCE}"
  fi
  [[ -n "$OPENMEMORY_BASE_URL" ]] && ok "OpenMemory base URL configured." || warn "OPENMEMORY_BASE_URL is not set."
  [[ -n "$OPENMEMORY_MEMORY" ]] && ok "OpenMemory memory configured: $OPENMEMORY_MEMORY" || warn "OPENMEMORY_MEMORY is not set."
  [[ -n "$OPENMEMORY_TABLE" ]] && ok "OpenMemory table configured: $OPENMEMORY_TABLE" || warn "OPENMEMORY_TABLE/OPENMEMORY_INSTANCE is not set."
  if [[ -n "${!OPENMEMORY_TOKEN_ENV-}" ]]; then
    ok "OpenMemory token env is set: $OPENMEMORY_TOKEN_ENV"
  else
    warn "OpenMemory token env is missing: $OPENMEMORY_TOKEN_ENV"
  fi
else
  ok "Memory provider: $MEMORY_PROVIDER"
fi
if [[ "$openmemory_smoke" -eq 1 ]]; then
  run_openmemory_smoke
else
  info "OpenMemory smoke not requested; pass --openmemory-smoke to test the read path."
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
