#!/usr/bin/env bash
# Suggest or run Agent Rails verification commands based on changed paths.

set -euo pipefail

usage() {
  printf 'Usage: %s [--profile PATH] [--base REF] [--target-ref REF] [--run|--print-only|--suggestions-only]\n' "$0"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-git-scope.sh
source "$AGENT_RAILS_HOME/scripts/agent-git-scope.sh"
agent_rails_init_paths

profile_path_arg=""
profile_path=""
base_ref=""
target_ref="HEAD"
target_ref_explicit=0
run_commands=0
suggestions_only=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path_arg="$2"
      shift 2
      ;;
    --base)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      base_ref="$2"
      shift 2
      ;;
    --target-ref)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      target_ref="$2"
      target_ref_explicit=1
      shift 2
      ;;
    --run)
      run_commands=1
      shift
      ;;
    --print-only)
      run_commands=0
      shift
      ;;
    --suggestions-only)
      suggestions_only=1
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

if [[ "$suggestions_only" -eq 1 && "$run_commands" -eq 1 ]]; then
  printf '%s\n' '--suggestions-only cannot be combined with --run.' >&2
  exit 2
fi

if repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  is_git_repo=1
  repo_root="$(cd "$repo_root" && pwd)"
  cd "$repo_root"
else
  is_git_repo=0
  repo_root="$PWD"
fi

profile_path="$(agent_rails_resolve_profile "$repo_root" "$(basename "$repo_root")" "$profile_path_arg")"
if [[ ! -f "$profile_path" ]]; then
  printf 'Profile not found: %s\n' "$profile_path" >&2
  exit 2
fi

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

TARGET_REF="$target_ref"
BASE_REF="${base_ref:-${BASE_REF:-}}"

if [[ "$is_git_repo" -eq 1 ]]; then
  agent_git_scope_resolve "$TARGET_REF" "$BASE_REF" project || exit $?
  BASE_REF="$AGENT_GIT_SCOPE_BASE_REF"
  merge_base="$AGENT_GIT_SCOPE_MERGE_BASE"

  if [[ "$run_commands" -eq 1 && "$target_ref_explicit" -eq 1 ]]; then
    target_sha="$AGENT_GIT_SCOPE_TARGET_SHA"
    head_sha="$(git rev-parse HEAD)"
    if [[ "$target_sha" != "$head_sha" ]]; then
      printf 'Cannot --run checks for target ref %s while checkout is at HEAD %s. Use --print-only or check out the target first.\n' "$TARGET_REF" "${head_sha:0:12}" >&2
      exit 2
    fi
  fi

else
  if [[ "$target_ref_explicit" -eq 1 ]]; then
    printf 'Target ref requires a git repository: %s\n' "$TARGET_REF" >&2
    exit 2
  fi
  merge_base="n/a"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

changed_file_list="$tmp_dir/changed-files"
if [[ "$is_git_repo" -eq 1 ]]; then
  git_scope_snapshot_dir="$tmp_dir/git-scope"
  include_worktree=$((1 - target_ref_explicit))
  agent_git_scope_write_snapshot "$git_scope_snapshot_dir" "$include_worktree"
  cp "$git_scope_snapshot_dir/changed-paths" "$changed_file_list"
else
  : > "$changed_file_list"
fi

commands_file="$tmp_dir/commands"
: > "$commands_file"

add_command() {
  local reason="$1"
  local command="$2"
  [[ -n "$command" ]] || return 0
  if ! cut -f2- "$commands_file" | grep -Fxq "$command"; then
    printf '%s\t%s\n' "$reason" "$command" >> "$commands_file"
  fi
}

has_changed() {
  local pattern="$1"
  grep -Eq "$pattern" "$changed_file_list"
}

if has_changed '^contracts/'; then
  add_command "contracts changed" "${VERIFY_CONTRACTS:-}"
fi

if has_changed '^backend/|^Makefile$'; then
  add_command "backend changed" "${VERIFY_BACKEND:-}"
fi

if has_changed '^runtime/'; then
  add_command "runtime changed" "${VERIFY_RUNTIME:-}"
fi

if has_changed '^frontend/'; then
  add_command "frontend changed" "${VERIFY_FRONTEND:-}"
fi

if has_changed '(^package(-lock)?\.json$|^pnpm-lock\.yaml$|^yarn\.lock$|\.(js|jsx|ts|tsx)$)'; then
  add_command "node/js changed" "${VERIFY_NODE:-}"
fi

if has_changed '(^pyproject\.toml$|^requirements.*\.txt$|^setup\.py$|^pytest\.ini$|\.py$)'; then
  add_command "python changed" "${VERIFY_PYTHON:-}"
fi

if has_changed '(^pom\.xml$|^mvnw$|^build\.gradle$|^settings\.gradle$|\.java$|\.kt$)'; then
  add_command "java/jvm changed" "${VERIFY_JAVA:-}"
fi

if has_changed '(^go\.mod$|^go\.sum$|\.go$)'; then
  add_command "go changed" "${VERIFY_GO:-}"
fi

if has_changed '(^Cargo\.toml$|^Cargo\.lock$|\.rs$)'; then
  add_command "rust changed" "${VERIFY_RUST:-}"
fi

if has_changed '^dolphin/.*\.py$'; then
  add_command "dolphin python changed" "${VERIFY_DOLPHIN:-}"
elif has_changed '^dolphin/'; then
  add_command "dolphin changed" "${VERIFY_DOLPHIN:-}"
fi

if has_changed '^(bin/agent-rails|scripts/.*\.sh)$'; then
  shell_files="$(grep -E '^(bin/agent-rails|scripts/.*\.sh)$' "$changed_file_list" | tr '\n' ' ')"
  add_command "shell entrypoints changed" "${VERIFY_SHELL:-bash -n ${shell_files}}"
fi

if has_changed '^tests/.*\.sh$'; then
  test_command="${VERIFY_TESTS:-}"
  if [[ -z "$test_command" ]]; then
    unmapped_test_file="$(
      awk '
        /^tests\/.*\.sh$/ && $0 !~ /^tests\/suites\/(core|adapters|workflows|context)\.sh$/ {
          print
          exit
        }
      ' "$changed_file_list"
    )"
    if [[ -n "$unmapped_test_file" ]]; then
      test_command="bash tests/run.sh"
    else
      changed_test_suites=()
      changed_test_suite_count=0
      for test_suite in core adapters workflows context; do
        if grep -Fxq "tests/suites/$test_suite.sh" "$changed_file_list"; then
          changed_test_suites+=("$test_suite")
          changed_test_suite_count=$((changed_test_suite_count + 1))
        fi
      done
      if [[ "$changed_test_suite_count" -gt 0 ]]; then
        test_command="bash tests/run.sh ${changed_test_suites[*]}"
      else
        test_command="bash tests/run.sh"
      fi
    fi
  fi
  add_command "shell tests changed" "$test_command"
fi

if [[ -s "$changed_file_list" && ! -s "$commands_file" ]]; then
  add_command "project default" "${VERIFY_PROJECT:-}"
fi

print_suggested_verification() {
  if [[ -s "$commands_file" ]]; then
    while IFS=$'\t' read -r reason command; do
      printf -- '- [%s] %s\n' "$reason" "$command"
    done < "$commands_file"
  else
    printf -- '- No automated command selected. For docs-only changes, manually review rendered Markdown and links.\n'
  fi
}

if [[ "$suggestions_only" -eq 1 ]]; then
  print_suggested_verification
  exit 0
fi

if [[ "${AGENT_RAILS_SUPPRESS_MARKER:-0}" != "1" ]]; then
  printf 'AGENT RAILS: CHECK-ONLY (reason=verification, project=%s)\n\n' "$(basename "$repo_root")"
fi
printf 'Agent check\n'
printf 'Base ref: %s\n' "${BASE_REF:-none}"
printf 'Target ref: %s\n' "$TARGET_REF"
printf 'Merge base: %s\n' "${merge_base:0:12}"
if [[ "$is_git_repo" -eq 0 ]]; then
  printf 'Mode: no git repository detected; diff-based checks are unavailable.\n'
fi
if [[ "$target_ref_explicit" -eq 1 ]]; then
  printf 'Mode: target ref only; current working tree changes are not included.\n'
fi
printf '\nChanged files:\n'
if [[ -s "$changed_file_list" ]]; then
  sed 's/^/- /' "$changed_file_list"
else
  printf -- '- None detected.\n'
fi

printf '\nSuggested verification:\n'
print_suggested_verification

printf '\nNext action suggestions:\n'
printf -- '- Fix: run the suggested command for any touched executable component before merge.\n'
printf -- '- Do not fix: skip heavy component CI only when the diff is docs-only or explicitly out of scope.\n'
printf -- '- Later: add missing AGENTS.md or provider config when context gaps repeat.\n'

if [[ "$run_commands" -eq 1 && -s "$commands_file" ]]; then
  printf '\nRunning suggested commands...\n'
  runner_shell="${AGENT_RAILS_RUN_SHELL:-bash}"
  if ! command -v "$runner_shell" >/dev/null 2>&1; then
    printf 'Runner shell not found: %s\n' "$runner_shell" >&2
    exit 127
  fi
  while IFS=$'\t' read -r reason command; do
    printf '\n>>> %s\n%s\n' "$reason" "$command"
    "$runner_shell" -lc "$command"
  done < "$commands_file"
fi
