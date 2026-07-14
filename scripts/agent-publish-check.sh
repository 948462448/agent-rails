#!/usr/bin/env bash
# Summarize publish readiness without changing the target repository.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails publish check [--profile PATH] [--base REF] [--target-ref REF] [--no-secret-scan]

Summarizes local commit/push scope, scans changed files for likely secrets with
redacted output, and embeds the normal Agent Rails verification suggestions.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-target-project.sh
source "$AGENT_RAILS_HOME/scripts/agent-target-project.sh"
# shellcheck source=scripts/agent-git-scope.sh
source "$AGENT_RAILS_HOME/scripts/agent-git-scope.sh"
# shellcheck source=scripts/agent-sensitive-output.sh
source "$AGENT_RAILS_HOME/scripts/agent-sensitive-output.sh"
agent_rails_init_paths

profile_path_arg=""
base_ref=""
base_ref_explicit=0
target_ref="HEAD"
target_ref_explicit=0
scan_secrets=1

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
      base_ref_explicit=1
      shift 2
      ;;
    --target-ref)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      target_ref="$2"
      target_ref_explicit=1
      shift 2
      ;;
    --no-secret-scan)
      scan_secrets=0
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

if ! repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  printf 'publish check requires a git repository.\n' >&2
  exit 2
fi
repo_root="$(cd "$repo_root" && pwd)"
cd "$repo_root"

agent_target_project_resolve "$repo_root" "$profile_path_arg" || exit $?
agent_target_project_load_profile required || exit 2
repo_root="$AGENT_TARGET_PROJECT_ROOT"
project_name="$AGENT_TARGET_PROJECT_DEFAULT_NAME"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"

TARGET_REF="$target_ref"
BASE_REF="${base_ref:-${BASE_REF:-}}"
agent_git_scope_resolve "$TARGET_REF" "$BASE_REF" publish || exit $?
BASE_REF="$AGENT_GIT_SCOPE_BASE_REF"
merge_base="$AGENT_GIT_SCOPE_MERGE_BASE"

deployment_delta_unresolved=0
if [[ "$base_ref_explicit" -eq 0 ]]; then
  if [[ -z "$BASE_REF" ]]; then
    deployment_delta_unresolved=1
  elif [[ "$AGENT_GIT_SCOPE_BASE_SHA" == "$AGENT_GIT_SCOPE_TARGET_SHA" ]]; then
    deployment_delta_unresolved=1
  fi
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

git_scope_snapshot_dir="$tmp_dir/git-scope"
agent_git_scope_write_snapshot "$git_scope_snapshot_dir" 1
status_file="$git_scope_snapshot_dir/status"
committed_paths_file="$git_scope_snapshot_dir/committed-paths"
changed_paths_file="$git_scope_snapshot_dir/changed-paths"
secret_findings_file="$tmp_dir/secret-findings"
untracked_paths_file="$tmp_dir/untracked-paths"
secret_scan_diff_file="$tmp_dir/secret-scan.diff"
check_output_file="$tmp_dir/agent-check"

count_lines() {
  local path="$1"
  if [[ -s "$path" ]]; then
    wc -l < "$path" | tr -d ' '
  else
    printf '0'
  fi
}

status_count() {
  local kind="$1"
  awk -v kind="$kind" '
    kind == "staged" && substr($0, 1, 1) != " " && substr($0, 1, 1) != "?" { count++ }
    kind == "unstaged" && substr($0, 2, 1) != " " && substr($0, 1, 2) != "??" { count++ }
    kind == "untracked" && substr($0, 1, 2) == "??" { count++ }
    END { print count + 0 }
  ' "$status_file"
}

print_status_group() {
  local title="$1"
  local kind="$2"
  local count
  count="$(status_count "$kind")"
  printf '%s (%s):\n' "$title" "$count"
  if [[ "$count" -eq 0 ]]; then
    printf -- '- None\n'
    return 0
  fi
  awk -v kind="$kind" '
    function path_from_status(line) {
      path = substr(line, 4)
      sub(/^.* -> /, "", path)
      return path
    }
    kind == "staged" && substr($0, 1, 1) != " " && substr($0, 1, 1) != "?" { print "- " path_from_status($0) }
    kind == "unstaged" && substr($0, 2, 1) != " " && substr($0, 1, 2) != "??" { print "- " path_from_status($0) }
    kind == "untracked" && substr($0, 1, 2) == "??" { print "- " path_from_status($0) }
  ' "$status_file"
}

print_top_paths() {
  if [[ ! -s "$changed_paths_file" ]]; then
    printf -- '- None\n'
    return 0
  fi
  awk '
    {
      top = $0
      sub(/\/.*/, "", top)
      if (top == "") {
        top = "."
      }
      count[top]++
    }
    END {
      for (top in count) {
        print count[top] "\t" top
      }
    }
  ' "$changed_paths_file" | sort -rn | head -n 8 | awk -F '\t' '{ printf "- %s (%s files)\n", $2, $1 }'
}

scan_git_diff_for_secrets() {
  git diff --no-ext-diff --no-color --no-prefix --unified=0 "$@" > "$secret_scan_diff_file"
  [[ -s "$secret_scan_diff_file" ]] || return 0
  agent_sensitive_scan_file "$secret_scan_diff_file" diff >> "$secret_findings_file"
}

scan_full_paths_for_secrets() {
  local paths_file="$1"
  local rel_path
  while IFS= read -r rel_path; do
    [[ -n "$rel_path" && -f "$rel_path" ]] || continue
    if ! LC_ALL=C grep -Iq . "$rel_path" 2>/dev/null; then
      continue
    fi
    agent_sensitive_scan_file "$rel_path" >> "$secret_findings_file"
  done < "$paths_file"
}

scan_changed_files_for_secrets() {
  : > "$secret_findings_file"
  awk '
    function path_from_status(line) {
      path = substr(line, 4)
      sub(/^.* -> /, "", path)
      return path
    }
    NF && substr($0, 1, 2) == "??" { print path_from_status($0) }
  ' "$status_file" | sort -u > "$untracked_paths_file"

  if [[ -n "$BASE_REF" ]]; then
    scan_git_diff_for_secrets "$merge_base...$TARGET_REF"
  fi
  scan_git_diff_for_secrets --cached
  scan_git_diff_for_secrets
  scan_full_paths_for_secrets "$untracked_paths_file"
  LC_ALL=C sort -u -o "$secret_findings_file" "$secret_findings_file"
}

branch="$(git branch --show-current 2>/dev/null || true)"
[[ -n "$branch" ]] || branch="(detached)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null || true)"
remote_url="$(git remote get-url origin 2>/dev/null || true)"
ahead="n/a"
behind="n/a"
if [[ -n "$upstream" ]]; then
  read -r behind ahead < <(git rev-list --left-right --count "$upstream"...HEAD 2>/dev/null || printf 'n/a n/a\n')
fi

check_args=(--profile "$profile_path" --suggestions-only)
if [[ -n "$BASE_REF" ]]; then
  check_args+=(--base "$BASE_REF")
fi
if [[ "$target_ref_explicit" -eq 1 ]]; then
  check_args+=(--target-ref "$TARGET_REF")
fi
AGENT_RAILS_SUPPRESS_MARKER=1 "$AGENT_RAILS_HOME/scripts/agent-check.sh" "${check_args[@]}" > "$check_output_file" 2>&1 || true

if [[ "$scan_secrets" -eq 1 ]]; then
  scan_changed_files_for_secrets
fi

printf 'AGENT RAILS: CHECK-ONLY (reason=publish, project=%s)\n\n' "$project_name"
printf 'Agent publish check\n'
printf 'Project: %s\n' "$repo_root"
printf 'Profile: %s\n' "$profile_path"
printf 'Branch: %s\n' "$branch"
if [[ -n "$upstream" ]]; then
  printf 'Upstream: %s (ahead %s, behind %s)\n' "$upstream" "$ahead" "$behind"
else
  printf 'Upstream: none\n'
fi
printf 'Origin: %s\n' "${remote_url:-none}"
printf 'Base ref: %s\n' "${BASE_REF:-none}"
printf 'Target ref: %s\n' "$TARGET_REF"
printf 'Merge base: %s\n' "${merge_base:0:12}"
if [[ "$deployment_delta_unresolved" -eq 1 ]]; then
  printf 'Deployment delta: UNRESOLVED (implicit base is missing or already equals target)\n'
  printf 'Deployment baseline action: pass --base <currently-deployed-source-revision> before claiming release readiness.\n'
fi
if [[ "$target_ref_explicit" -eq 1 ]]; then
  printf 'Mode: target ref only for committed diff; working tree status is still shown.\n'
fi

printf '\nCommitted change scope:\n'
if [[ "$deployment_delta_unresolved" -eq 1 ]]; then
  printf -- '- Deployment delta unresolved; the push/upstream baseline is not proof of the currently deployed revision.\n'
elif [[ -s "$committed_paths_file" ]]; then
  sed 's/^/- /' "$committed_paths_file"
else
  printf -- '- None against base.\n'
fi

printf '\nWorking tree scope:\n'
print_status_group "Staged files" staged
print_status_group "Unstaged files" unstaged
print_status_group "Untracked files" untracked

printf '\nSuggested commit scope:\n'
if [[ ! -s "$changed_paths_file" ]]; then
  printf -- '- No local or branch changes detected.\n'
else
  printf -- '- Changed files in publish scope: %s\n' "$(count_lines "$changed_paths_file")"
  printf -- '- Top paths:\n'
  print_top_paths
fi

printf '\nSecret scan:\n'
if [[ "$scan_secrets" -eq 0 ]]; then
  printf -- '- Disabled by --no-secret-scan.\n'
elif [[ -s "$secret_findings_file" ]]; then
  printf -- '- Potential secret matches found. Review before commit/push:\n'
  sed 's/^/  - /' "$secret_findings_file"
else
  printf -- '- No likely secrets found in changed text files.\n'
fi

printf '\nSuggested verification:\n'
cat "$check_output_file"

printf '\nPublish next steps:\n'
if [[ "$deployment_delta_unresolved" -eq 1 ]]; then
  printf -- '- Resolve the deployed source baseline with --base before treating this check as release readiness evidence.\n'
fi
printf -- '- Review the changed file scope and secret scan warnings.\n'
printf -- '- Stage only intentional files, commit with a scope that matches this summary, run required checks, then push.\n'
