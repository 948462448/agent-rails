#!/usr/bin/env bash
# Run the normal Verification Plan and optionally add publish readiness checks.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails verify [--project PATH] [--profile PATH] [--print-only] [--publish] [--base REF] [--target-ref REF] [--no-secret-scan]

By default, verify executes the Verification Plan selected by `agent-rails check`.
Use --print-only to preview it. With --publish, the same command also runs the
read-only publish scope and secret scan after the normal plan succeeds.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
# shellcheck source=scripts/agent-target-project.sh
source "$AGENT_RAILS_HOME/scripts/agent-target-project.sh"
agent_rails_init_paths

project="$PWD"
profile_path=""
print_only=0
publish=0
base_ref=""
target_ref=""
no_secret_scan=0

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
    --print-only)
      print_only=1
      shift
      ;;
    --publish)
      publish=1
      shift
      ;;
    --base)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      base_ref="$2"
      shift 2
      ;;
    --target-ref)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      target_ref="$2"
      shift 2
      ;;
    --no-secret-scan)
      no_secret_scan=1
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

if [[ "$no_secret_scan" -eq 1 && "$publish" -ne 1 ]]; then
  printf '%s\n' '--no-secret-scan requires --publish.' >&2
  exit 2
fi

agent_target_project_resolve "$project" "$profile_path" || exit $?
agent_target_project_load_profile required || exit 2
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"

check_args=(check --project "$project_abs" --profile "$profile_path")
[[ -n "$base_ref" ]] && check_args+=(--base "$base_ref")
[[ -n "$target_ref" ]] && check_args+=(--target-ref "$target_ref")
if [[ "$print_only" -eq 1 ]]; then
  check_args+=(--print-only)
else
  check_args+=(--run)
fi

printf 'Agent Rails Verify\n'
printf 'Project: %s\n' "$project_abs"
if [[ "$publish" -eq 1 ]]; then
  printf 'Mode: publish\n\n'
else
  printf 'Mode: delivery\n\n'
fi

"$AGENT_RAILS_BIN" "${check_args[@]}"

if [[ "$publish" -eq 1 ]]; then
  publish_args=(publish check --project "$project_abs" --profile "$profile_path")
  [[ -n "$base_ref" ]] && publish_args+=(--base "$base_ref")
  [[ -n "$target_ref" ]] && publish_args+=(--target-ref "$target_ref")
  [[ "$no_secret_scan" -eq 1 ]] && publish_args+=(--no-secret-scan)
  printf '\nPublish readiness\n'
  "$AGENT_RAILS_BIN" "${publish_args[@]}"
  printf '\nAgent Rails publish verification complete.\n'
else
  printf '\nAgent Rails verification complete.\n'
fi
