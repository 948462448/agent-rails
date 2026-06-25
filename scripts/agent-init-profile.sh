#!/usr/bin/env bash
# Generate a local Agent Rails profile for a target project.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails profile init [--project PATH] [--name NAME] [--scope user|project] [--output PATH] [--force] [--print-only]

Examples:
  agent-rails profile init --project /path/to/project
  agent-rails profile init --project /path/to/project --name my-project --print-only
  agent-rails profile init --project /path/to/project --scope project

User profiles are written under ~/.agent-rails/profiles/projects/ by default.
Project profiles are written under <project>/.agent-rails/profile with --scope project.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

project="$PWD"
profile_name=""
profile_scope="user"
output_path=""
force=0
print_only=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --name)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_name="$2"
      shift 2
      ;;
    --scope)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        user|project)
          profile_scope="$2"
          ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      output_path="$2"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --print-only)
      print_only=1
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

if [[ ! -d "$project" ]]; then
  printf 'Project directory not found: %s\n' "$project" >&2
  exit 2
fi

project_abs="$(cd "$project" && pwd)"
if [[ -z "$profile_name" ]]; then
  profile_name="$(basename "$project_abs" | tr '[:upper:] ' '[:lower:]-' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g')"
fi
if [[ -z "$profile_name" ]]; then
  printf 'Could not derive profile name from: %s\n' "$project_abs" >&2
  exit 2
fi

if [[ -z "$output_path" ]]; then
  if [[ "$profile_scope" == "project" ]]; then
    output_path="$project_abs/.agent-rails/profile"
  else
    output_path="$AGENT_RAILS_CONFIG_HOME/profiles/projects/$profile_name.profile"
  fi
fi

detect_entry_doc() {
  local doc
  for doc in AGENTS.md CLAUDE.md README.md; do
    if [[ -f "$project_abs/$doc" ]]; then
      printf '%s\n' "$doc"
      return 0
    fi
  done
  printf 'AGENTS.md\n'
}

has_make_target() {
  local target="$1"
  [[ -f "$project_abs/Makefile" ]] && grep -Eq "^${target}:" "$project_abs/Makefile"
}

package_has_script() {
  local package_json="$1"
  local script_name="$2"
  [[ -f "$package_json" ]] && grep -Eq "\"${script_name}\"[[:space:]]*:" "$package_json"
}

detect_node_command() {
  if package_has_script "$project_abs/package.json" lint; then
    printf 'npm run lint\n'
  elif package_has_script "$project_abs/package.json" test; then
    printf 'npm test\n'
  elif package_has_script "$project_abs/frontend/package.json" lint; then
    printf 'cd frontend && npm run lint\n'
  elif package_has_script "$project_abs/frontend/package.json" test; then
    printf 'cd frontend && npm test\n'
  fi
}

detect_python_command() {
  if [[ -f "$project_abs/pyproject.toml" || -f "$project_abs/pytest.ini" || -f "$project_abs/setup.py" ]]; then
    printf 'python3 -m pytest\n'
  elif [[ -d "$project_abs/tests" ]] && find "$project_abs/tests" -type f -name '*.py' -print -quit | grep -q .; then
    printf 'python3 -m pytest\n'
  fi
}

detect_java_command() {
  if [[ -f "$project_abs/mvnw" ]]; then
    printf './mvnw test\n'
  elif [[ -f "$project_abs/pom.xml" ]]; then
    printf 'mvn test\n'
  elif [[ -f "$project_abs/gradlew" ]]; then
    printf './gradlew test\n'
  elif [[ -f "$project_abs/build.gradle" || -f "$project_abs/settings.gradle" ]]; then
    printf 'gradle test\n'
  fi
}

detect_go_command() {
  if [[ -f "$project_abs/go.mod" ]]; then
    printf 'go test ./...\n'
  fi
}

detect_rust_command() {
  if [[ -f "$project_abs/Cargo.toml" ]]; then
    printf 'cargo test\n'
  fi
}

escape_double_quotes() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

entry_doc="$(detect_entry_doc)"
verify_project=""
verify_node="$(detect_node_command)"
verify_python="$(detect_python_command)"
verify_java="$(detect_java_command)"
verify_go="$(detect_go_command)"
verify_rust="$(detect_rust_command)"

if has_make_target test; then
  verify_project="make test"
elif has_make_target check; then
  verify_project="make check"
fi

tmp_profile="$(mktemp)"
trap 'rm -f "$tmp_profile"' EXIT

{
  printf '# Agent Rails profile for %s.\n' "$profile_name"
  printf '# Generated from `%s`.\n\n' "$project_abs"
  printf '# shellcheck source=/dev/null\n'
  printf 'source "$AGENT_RAILS_HOME/profiles/default.profile"\n\n'
  printf 'PROJECT_NAME="%s"\n' "$(escape_double_quotes "$profile_name")"
  printf '# Leave TASK_PACK_PATH unset for the default worktree-isolated path:\n'
  printf '# ${AGENT_RAILS_CONFIG_HOME}/agent-context/${PROJECT_WORKTREE_SLUG}-task-pack.md\n'
  printf 'MEMORY_LOCAL_DIR="${AGENT_RAILS_CONFIG_HOME}/memory/%s"\n' "$(escape_double_quotes "$profile_name")"
  printf 'MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"\n\n'
  printf '# Model preset and context budget. Use qwen3.7-max, glm5.1, or deepseek-v4-pro when applicable.\n'
  printf 'AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-generic}"\n'
  printf 'AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-normal}"\n'
  printf 'AGENT_RAILS_CONTEXT_BUDGET_TOKENS="${AGENT_RAILS_CONTEXT_BUDGET_TOKENS:-}"\n'
  printf 'AGENT_RAILS_CONTEXT_BUDGET_CHARS="${AGENT_RAILS_CONTEXT_BUDGET_CHARS:-0}"\n'
  printf 'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}"\n'
  printf 'AGENT_RAILS_TOKENIZER="${AGENT_RAILS_TOKENIZER:-auto}"\n'
  printf 'AGENT_RAILS_TOKENIZER_CMD="${AGENT_RAILS_TOKENIZER_CMD:-}"\n'
  printf 'AGENT_RAILS_TIKTOKEN_ENCODING="${AGENT_RAILS_TIKTOKEN_ENCODING:-cl100k_base}"\n'
  printf 'AGENT_RAILS_BUDGET_GIT_PERCENT="${AGENT_RAILS_BUDGET_GIT_PERCENT:-20}"\n'
  printf 'AGENT_RAILS_BUDGET_MEMORY_PERCENT="${AGENT_RAILS_BUDGET_MEMORY_PERCENT:-40}"\n'
  printf 'AGENT_RAILS_BUDGET_VERIFY_PERCENT="${AGENT_RAILS_BUDGET_VERIFY_PERCENT:-20}"\n'
  printf 'AGENT_RAILS_BUDGET_CONTRACT_PERCENT="${AGENT_RAILS_BUDGET_CONTRACT_PERCENT:-20}"\n'
  printf 'AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="${AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS:-1600}"\n\n'
  printf 'AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT="${AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT:-5}"\n'
  printf 'AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS="${AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS:-4000}"\n\n'
  printf 'AGENT_RAILS_CHANGED_FILE_SORT="${AGENT_RAILS_CHANGED_FILE_SORT:-smart}"\n\n'
  printf 'ENTRY_DOC_ROOT="%s"\n' "$(escape_double_quotes "$entry_doc")"
  printf 'DOMAIN_DOC_ROOT="CONTEXT.md"\n'
  printf 'DOMAIN_DOC_MAP="CONTEXT-MAP.md"\n'
  printf 'ADR_DIR="docs/adr"\n'
  printf 'AGENT_DOC_DIR="docs/agents"\n'
  printf 'ISSUE_TRACKER_DOC="docs/agents/issue-tracker.md"\n'
  printf 'TRIAGE_LABELS_DOC="docs/agents/triage-labels.md"\n\n'
  printf '# Verification commands. Keep these lightweight and agent-runnable.\n'
  [[ -n "$verify_project" ]] && printf 'VERIFY_PROJECT="%s"\n' "$(escape_double_quotes "$verify_project")"
  [[ -n "$verify_node" ]] && printf 'VERIFY_NODE="%s"\n' "$(escape_double_quotes "$verify_node")"
  [[ -n "$verify_python" ]] && printf 'VERIFY_PYTHON="%s"\n' "$(escape_double_quotes "$verify_python")"
  [[ -n "$verify_java" ]] && printf 'VERIFY_JAVA="%s"\n' "$(escape_double_quotes "$verify_java")"
  [[ -n "$verify_go" ]] && printf 'VERIFY_GO="%s"\n' "$(escape_double_quotes "$verify_go")"
  [[ -n "$verify_rust" ]] && printf 'VERIFY_RUST="%s"\n' "$(escape_double_quotes "$verify_rust")"
} > "$tmp_profile"

if [[ "$print_only" -eq 1 ]]; then
  cat "$tmp_profile"
  exit 0
fi

if [[ -e "$output_path" && "$force" -ne 1 ]]; then
  printf 'Profile already exists: %s\nUse --force to overwrite.\n' "$output_path" >&2
  exit 1
fi

mkdir -p "$(dirname "$output_path")"
cp "$tmp_profile" "$output_path"
printf 'Wrote %s\n' "$output_path"
