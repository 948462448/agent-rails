#!/usr/bin/env bash
# Lightweight e2e test runner for Agent Rails public and runtime contracts.

set -euo pipefail

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$TESTS_DIR/.." && pwd)"
export AGENT_RAILS_HOME="$ROOT_DIR"
AGENT_RAILS_BIN="$ROOT_DIR/bin/agent-rails"
EXPECTED_AGENT_RAILS_VERSION="$(awk 'NF { print $1; exit }' "$ROOT_DIR/VERSION")"
TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-tests.XXXXXX")"
TMP_ROOT="$(cd "$TMP_ROOT" && pwd -P)"
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
       bash tests/run.sh --related [PATH ...]
       bash tests/run.sh --list-related [PATH ...]

With no suite names, runs all 175 tests in their historical order.
Related mode maps explicit paths, or current Git changes when no paths are
given, to the smallest safe set of module suites.
USAGE
}

reset_related_suites() {
  related_core=0
  related_adapters=0
  related_workflows=0
  related_context=0
}

select_all_related_suites() {
  related_core=1
  related_adapters=1
  related_workflows=1
  related_context=1
}

select_related_path() {
  local path="$1"
  case "$path" in
    src/agent_rails/core/terminal.py)
      related_core=1
      related_adapters=1
      related_workflows=1
      related_context=1
      ;;
    src/agent_rails/adapters/*|src/agent_rails/diagnostics/*|src/agent_rails/session_start.py)
      related_adapters=1
      ;;
    src/agent_rails/evidence/*)
      related_workflows=1
      related_context=1
      ;;
    src/agent_rails/context/*|src/agent_rails/memory/*|src/agent_rails/core/private_text.py)
      related_context=1
      ;;
    src/agent_rails/models/*)
      related_context=1
      related_workflows=1
      ;;
    src/agent_rails/verification/plan.py)
      related_workflows=1
      related_context=1
      ;;
    src/agent_rails/verification/*|src/agent_rails/run_application.py|src/agent_rails/estimate.py|src/agent_rails/config/profile_init.py)
      related_workflows=1
      ;;
    src/agent_rails/git/*|src/agent_rails/security/*)
      related_workflows=1
      related_context=1
      ;;
    src/agent_rails/release/*|src/agent_rails/public_cli.py|src/agent_rails/update_application.py|src/agent_rails/setup_application.py|src/agent_rails/skills_install.py|src/agent_rails/init_application.py)
      related_core=1
      ;;
    src/agent_rails/cli.py|src/agent_rails/core/paths.py|src/agent_rails/config/profile.py|src/agent_rails/config/target_project.py|src/agent_rails/__main__.py)
      select_all_related_suites
      ;;
    src/agent_rails/*.py|src/agent_rails/*/*.py)
      select_all_related_suites
      ;;
    tests/suites/core.sh)
      related_core=1
      ;;
    tests/suites/adapters.sh)
      related_adapters=1
      ;;
    tests/suites/workflows.sh)
      related_workflows=1
      ;;
    tests/suites/context.sh)
      related_context=1
      ;;
    tests/test_adapter_content.py|tests/test_adapter_workspace.py|tests/test_claude_adapter.py|tests/test_opencode_adapter.py|tests/test_doctor_application.py|tests/test_session_start.py)
      related_adapters=1
      ;;
    tests/test_code_evidence.py)
      related_workflows=1
      related_context=1
      ;;
    tests/test_context_assembler.py|tests/test_pack_*.py|tests/test_change_evidence.py|tests/test_memory_*.py|tests/test_project_docs.py|tests/test_contract_sections.py|tests/test_context_markdown.py|tests/test_private_text.py)
      related_context=1
      ;;
    tests/test_check_application.py|tests/test_publish_check_application.py|tests/test_verify_application.py|tests/test_run_application.py|tests/test_verification_plan.py|tests/test_repair_pack.py|tests/test_git_scope.py|tests/test_sensitive_output.py|tests/test_estimate.py|tests/test_target_project.py|tests/test_profile_init.py|tests/test_ab_eval.py)
      related_workflows=1
      ;;
    tests/test_*.py)
      related_core=1
      ;;
    tests/run.sh|tests/lib/*|scripts/agent-python-cli.py|bin/agent-rails)
      select_all_related_suites
      ;;
    hooks/*|codex-marketplace/*)
      related_adapters=1
      ;;
    scripts/agent-release-install.sh|.github/workflows/release.yml)
      related_core=1
      ;;
    templates/opencode-agent-rails-plugin.mjs)
      related_adapters=1
      related_context=1
      ;;
    docs/*|README.md|README.en.md|CHANGELOG.md|CONTEXT.md)
      ;;
    *)
      select_all_related_suites
      ;;
  esac
}

select_related_suites() {
  reset_related_suites
  if [[ "$#" -gt 0 ]]; then
    local path
    for path in "$@"; do
      select_related_path "$path"
    done
    return
  fi

  local path
  while IFS= read -r path; do
    [[ -n "$path" ]] && select_related_path "$path"
  done < <(
    {
      git -C "$ROOT_DIR" diff --name-only HEAD --
      git -C "$ROOT_DIR" ls-files --others --exclude-standard
    } | LC_ALL=C sort -u
  )
}

list_related_suites() {
  [[ "$related_core" -eq 1 ]] && printf 'core\n'
  [[ "$related_adapters" -eq 1 ]] && printf 'adapters\n'
  [[ "$related_workflows" -eq 1 ]] && printf 'workflows\n'
  [[ "$related_context" -eq 1 ]] && printf 'context\n'
  return 0
}

run_related_suites() {
  local selected=0
  if [[ "$related_core" -eq 1 ]]; then
    run_core_tests
    selected=1
  fi
  if [[ "$related_adapters" -eq 1 ]]; then
    run_adapter_tests
    selected=1
  fi
  if [[ "$related_workflows" -eq 1 ]]; then
    run_workflow_tests
    selected=1
  fi
  if [[ "$related_context" -eq 1 ]]; then
    run_context_tests
    selected=1
  fi
  if [[ "$selected" -eq 0 ]]; then
    printf 'No related test suites selected.\n'
  fi
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

if [[ "$1" == "--related" || "$1" == "--list-related" ]]; then
  related_mode="$1"
  shift
  select_related_suites "$@"
  if [[ "$related_mode" == "--list-related" ]]; then
    list_related_suites
  else
    run_related_suites
  fi
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
