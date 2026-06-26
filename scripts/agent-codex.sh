#!/usr/bin/env bash
# Install, inspect, or remove the local Agent Rails Codex plugin.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails codex install [--project PATH] [--profile PATH] [--fix-project] [--dry-run]
       agent-rails codex doctor [--project PATH]
       agent-rails codex uninstall [--dry-run]

Codex install registers the repo-local Agent Rails marketplace and installs
agent-rails@agent-rails-local. Project marker/adapter refresh is explicit via
--fix-project so business repositories are not changed by surprise.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths
AGENT_RAILS_VERSION="$(agent_rails_version)"

marketplace_path="$AGENT_RAILS_HOME/codex-marketplace"
plugin_selector="agent-rails@agent-rails-local"
subcommand="${1:-}"
[[ -n "$subcommand" ]] || { usage >&2; exit 2; }
shift || true

project=""
profile_path=""
fix_project=0
dry_run=0

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
    --fix-project)
      fix_project=1
      shift
      ;;
    --dry-run)
      dry_run=1
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

run_or_print() {
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would run: '
    print_command "$@"
  else
    "$@"
  fi
}

require_codex() {
  if ! command -v codex >/dev/null 2>&1; then
    printf 'Codex CLI not found. Install Codex first, then rerun this command.\n' >&2
    exit 127
  fi
}

resolve_project() {
  [[ -n "$project" ]] || return 0
  if [[ ! -d "$project" ]]; then
    printf 'Project directory not found: %s\n' "$project" >&2
    exit 2
  fi
  project_abs="$(cd "$project" && pwd)"
  if git_root_for_project="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
    project_abs="$(cd "$git_root_for_project" && pwd)"
  fi
  project_name="$(basename "$project_abs")"
  profile_path="$(agent_rails_resolve_profile "$project_abs" "$project_name" "$profile_path")"
}

project_has_marker() {
  [[ -n "${project_abs:-}" ]] || return 1
  [[ -f "$project_abs/.codex-plugin/plugin.json" ]] && return 0
  [[ -f "$project_abs/.claude/AGENT_RAILS.md" ]] && return 0
  grep -q '<!-- agent-rails:start -->' "$project_abs/CLAUDE.local.md" 2>/dev/null && return 0
  grep -q '<!-- agent-rails:start -->' "$project_abs/CLAUDE.md" 2>/dev/null && return 0
  return 1
}

print_project_status() {
  [[ -n "${project_abs:-}" ]] || return 0
  printf 'Project: %s\n' "$project_abs"
  if project_has_marker; then
    printf '[OK] Project has Agent Rails marker.\n'
  else
    printf '[WARN] Project has no Agent Rails marker yet. Run `agent-rails doctor --project "%s" --fix` or pass --fix-project.\n' "$project_abs"
  fi
}

case "$subcommand" in
  install)
    resolve_project
    [[ "$dry_run" -eq 1 ]] || require_codex
    printf 'Agent Rails Codex Install\n'
    printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
    printf 'Marketplace: %s\n' "$marketplace_path"
    printf 'Plugin: %s\n' "$plugin_selector"
    run_or_print codex plugin marketplace add "$marketplace_path"
    run_or_print codex plugin add "$plugin_selector"
    if [[ "$fix_project" -eq 1 ]]; then
      [[ -n "${project_abs:-}" ]] || { printf '%s\n' '--fix-project requires --project.' >&2; exit 2; }
      fix_args=(doctor --project "$project_abs" --fix)
      [[ -n "$profile_path" ]] && fix_args+=(--profile "$profile_path")
      run_or_print "$AGENT_RAILS_BIN" "${fix_args[@]}"
    else
      print_project_status
    fi
    printf 'Open a new Codex thread for SessionStart context to take effect.\n'
    ;;
  doctor)
    resolve_project
    printf 'Agent Rails Codex Doctor\n'
    printf 'Version: %s\n' "$AGENT_RAILS_VERSION"
    if command -v codex >/dev/null 2>&1; then
      printf '[OK] Codex CLI: %s\n' "$(command -v codex)"
      printf 'Marketplace: %s\n' "$marketplace_path"
      printf 'Plugin: %s\n' "$plugin_selector"
      codex plugin marketplace list 2>/dev/null || true
      codex plugin list 2>/dev/null || true
    else
      printf '[WARN] Codex CLI not found.\n'
    fi
    print_project_status
    ;;
  uninstall)
    [[ "$dry_run" -eq 1 ]] || require_codex
    printf 'Agent Rails Codex Uninstall\n'
    run_or_print codex plugin remove "$plugin_selector"
    ;;
  --help|-h)
    usage
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac
