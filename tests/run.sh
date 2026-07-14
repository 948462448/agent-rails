#!/usr/bin/env bash
# Lightweight e2e test runner for Agent Rails shell entrypoints.

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$TESTS_DIR/.." && pwd)"
AGENT_RAILS_BIN="$ROOT_DIR/bin/agent-rails"
EXPECTED_AGENT_RAILS_VERSION="$(awk 'NF { print $1; exit }' "$ROOT_DIR/VERSION")"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-tests.XXXXXX")"
trap 'rm -rf "$TMP_ROOT"' EXIT

# shellcheck source=tests/lib/test-helpers.sh
source "$TESTS_DIR/lib/test-helpers.sh"
# shellcheck source=tests/suites/core.sh
source "$TESTS_DIR/suites/core.sh"
# shellcheck source=tests/suites/adapters.sh
source "$TESTS_DIR/suites/adapters.sh"
# shellcheck source=tests/suites/workflows.sh
source "$TESTS_DIR/suites/workflows.sh"
# shellcheck source=tests/suites/context.sh
source "$TESTS_DIR/suites/context.sh"

usage() {
  cat <<'USAGE'
Usage: bash tests/run.sh [core|adapters|workflows|context ...]

With no suite names, runs all 81 tests in their historical order.
USAGE
}

run_all_tests() {
  run_core_tests
  run_adapter_foundation_tests
  run_workflow_tests
  run_adapter_claude_tests
  run_context_tests
}

if [[ "$#" -eq 0 ]]; then
  run_all_tests
  exit 0
fi

for suite_name in "$@"; do
  case "$suite_name" in
    core)
      run_core_tests
      ;;
    adapters)
      run_adapter_tests
      ;;
    workflows)
      run_workflow_tests
      ;;
    context)
      run_context_tests
      ;;
    --help|-h)
      usage
      ;;
    *)
      printf 'Unknown test suite: %s\n' "$suite_name" >&2
      usage >&2
      exit 2
      ;;
  esac
done
