#!/usr/bin/env bash
# Suggest or run Agent Rails verification commands based on changed paths.

set -euo pipefail

usage() {
  printf 'Usage: %s [--profile PATH] [--base REF] [--target-ref REF] [--run|--print-only]\n' "$0"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"

profile_path="$AGENT_RAILS_HOME/profiles/open-eval.profile"
base_ref=""
target_ref="HEAD"
target_ref_explicit=0
run_commands=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path="$2"
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

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

BASE_REF="${base_ref:-${BASE_REF:-origin/master}}"
TARGET_REF="$target_ref"

if ! git rev-parse --verify --quiet "$TARGET_REF" >/dev/null; then
  printf 'Target ref not found: %s\n' "$TARGET_REF" >&2
  exit 2
fi

if [[ "$run_commands" -eq 1 && "$target_ref_explicit" -eq 1 ]]; then
  target_sha="$(git rev-parse "$TARGET_REF")"
  head_sha="$(git rev-parse HEAD)"
  if [[ "$target_sha" != "$head_sha" ]]; then
    printf 'Cannot --run checks for target ref %s while checkout is at HEAD %s. Use --print-only or check out the target first.\n' "$TARGET_REF" "${head_sha:0:12}" >&2
    exit 2
  fi
fi

if git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
  merge_base="$(git merge-base "$TARGET_REF" "$BASE_REF")"
else
  merge_base="$(git rev-parse "$TARGET_REF")"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

changed_file_list="$tmp_dir/changed-files"
{
  git diff --name-only "$merge_base"..."$TARGET_REF" 2>/dev/null || true
  if [[ "$target_ref_explicit" -eq 0 ]]; then
    git diff --name-only 2>/dev/null || true
    git diff --cached --name-only 2>/dev/null || true
    git ls-files --others --exclude-standard 2>/dev/null || true
  fi
} | awk 'NF' | sort -u > "$changed_file_list"

commands_file="$tmp_dir/commands"
: > "$commands_file"

add_command() {
  local reason="$1"
  local command="$2"
  if ! cut -f2- "$commands_file" | grep -Fxq "$command"; then
    printf '%s\t%s\n' "$reason" "$command" >> "$commands_file"
  fi
}

has_changed() {
  local pattern="$1"
  grep -Eq "$pattern" "$changed_file_list"
}

if has_changed '^contracts/'; then
  add_command "contracts changed" "${VERIFY_CONTRACTS:-make codegen-check}"
fi

if has_changed '^backend/|^Makefile$'; then
  add_command "backend changed" "${VERIFY_BACKEND:-bash scripts/ci/backend.sh}"
fi

if has_changed '^runtime/'; then
  add_command "runtime changed" "${VERIFY_RUNTIME:-bash scripts/ci/runtime.sh}"
fi

if has_changed '^frontend/'; then
  add_command "frontend changed" "${VERIFY_FRONTEND:-cd frontend && npm run lint}"
fi

if has_changed '^dolphin/.*\.py$'; then
  dolphin_py_files="$(grep -E '^dolphin/.*\.py$' "$changed_file_list" | tr '\n' ' ')"
  add_command "dolphin python changed" "${VERIFY_DOLPHIN:-python3 -m py_compile ${dolphin_py_files}}"
elif has_changed '^dolphin/'; then
  add_command "dolphin changed" "${VERIFY_DOLPHIN:-python3 -m py_compile dolphin/tpp_eval_node/rawscript/tpp_eval_dolphin_main.py dolphin/tpp_eval_node/ray_entry/tpp_eval_main.py}"
fi

if has_changed '^scripts/.*\.sh$'; then
  shell_files="$(grep -E '^scripts/.*\.sh$' "$changed_file_list" | tr '\n' ' ')"
  add_command "shell scripts changed" "${VERIFY_SHELL:-bash -n ${shell_files}}"
fi

printf 'Agent check\n'
printf 'Base ref: %s\n' "$BASE_REF"
printf 'Target ref: %s\n' "$TARGET_REF"
printf 'Merge base: %s\n' "${merge_base:0:12}"
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
if [[ -s "$commands_file" ]]; then
  while IFS=$'\t' read -r reason command; do
    printf -- '- [%s] %s\n' "$reason" "$command"
  done < "$commands_file"
else
  printf -- '- No automated command selected. For docs-only changes, manually review rendered Markdown and links.\n'
fi

printf '\nNext action suggestions:\n'
printf -- '- Fix: run the suggested command for any touched executable component before merge.\n'
printf -- '- Do not fix: skip heavy component CI only when the diff is docs-only or explicitly out of scope.\n'
printf -- '- Later: add missing AGENTS.md or provider config when context gaps repeat.\n'

if [[ "$run_commands" -eq 1 && -s "$commands_file" ]]; then
  printf '\nRunning suggested commands...\n'
  while IFS=$'\t' read -r reason command; do
    printf '\n>>> %s\n%s\n' "$reason" "$command"
    eval "$command"
  done < "$commands_file"
fi
