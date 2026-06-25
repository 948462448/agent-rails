#!/usr/bin/env bash
# Prepare a default Agent Rails execution loop for an agent session.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails run [--project PATH] [--profile PATH] [--model NAME] [--pack-mode lite|normal|deep|audit] [--budget CHARS] [--token-budget TOKENS] [--tokenizer auto|char|tiktoken|command] [--print-only] [goal text...]

Generates a Task Pack, estimates its size, and prints the next commands/instructions
for an agent session. This wrapper does not hard-control Claude/Codex internals.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

project="$PWD"
profile_path=""
model_arg=""
pack_mode_arg=""
budget_arg=""
token_budget_arg=""
tokenizer_arg=""
print_only=0
goal_parts=()

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
    --model)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      model_arg="$2"
      shift 2
      ;;
    --pack-mode)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      pack_mode_arg="$2"
      shift 2
      ;;
    --budget|--context-budget)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      budget_arg="$2"
      shift 2
      ;;
    --token-budget)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      token_budget_arg="$2"
      shift 2
      ;;
    --tokenizer)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_arg="$2"
      shift 2
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
      goal_parts+=("$1")
      shift
      ;;
  esac
done

if [[ ! -d "$project" ]]; then
  printf 'Project directory not found: %s\n' "$project" >&2
  exit 2
fi

project_abs="$(cd "$project" && pwd)"
if git_root="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
  project_abs="$(cd "$git_root" && pwd)"
fi
project_name="$(basename "$project_abs")"

resolve_profile() {
  if [[ -n "$profile_path" ]]; then
    printf '%s\n' "$profile_path"
    return 0
  fi

  agent_rails_resolve_profile "$project_abs" "$project_name" ""
}

profile_path="$(resolve_profile)"
if [[ ! -f "$profile_path" ]]; then
  printf 'Profile not found: %s\n' "$profile_path" >&2
  exit 2
fi

goal="${goal_parts[*]:-TODO: describe the concrete user goal.}"

infer_pack_mode_for_goal() {
  local text_l
  text_l="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
  case "$text_l" in
    *audit*|*审计*|*全面review*|*全面检查*|*风险扫描*)
      printf 'audit\n'
      ;;
    *refactor*|*重构*|*架构*|*architecture*|*迁移*|*migration*|*api*|*contract*|*合约*|*数据模型*|*data\ model*|*debug*|*diagnose*|*排查*|*review*|*code\ review*)
      printf 'deep\n'
      ;;
    *poc*|*prototype*|*原型*|*试水*|*快速*|*whl*|*dockerfile*|*oss*|*上传*|*deploy*|*发布*|*部署*|*codegen*)
      printf 'lite\n'
      ;;
    *)
      printf '\n'
      ;;
  esac
}

pack_mode_inferred=""
if [[ -z "$pack_mode_arg" ]]; then
  pack_mode_inferred="$(infer_pack_mode_for_goal "$goal")"
  if [[ -n "$pack_mode_inferred" ]]; then
    pack_mode_arg="$pack_mode_inferred"
  fi
fi

pack_args=(--project "$project_abs" --profile "$profile_path")
estimate_args=(--profile "$profile_path")
check_args=(--project "$project_abs" --profile "$profile_path" --print-only)
memory_args=(--project "$project_abs" --profile "$profile_path")

if [[ -n "$model_arg" ]]; then
  pack_args+=(--model "$model_arg")
  estimate_args+=(--model "$model_arg")
fi
if [[ -n "$pack_mode_arg" ]]; then
  pack_args+=(--pack-mode "$pack_mode_arg")
fi
if [[ -n "$budget_arg" ]]; then
  pack_args+=(--budget "$budget_arg")
fi
if [[ -n "$token_budget_arg" ]]; then
  pack_args+=(--token-budget "$token_budget_arg")
fi
if [[ -n "$tokenizer_arg" ]]; then
  estimate_args+=(--tokenizer "$tokenizer_arg")
fi

shell_quote() {
  local value="$1"
  printf "'%s'" "$(printf '%s' "$value" | sed "s/'/'\\\\''/g")"
}

print_command() {
  local first=1
  local arg
  for arg in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      first=0
    else
      printf ' '
    fi
    shell_quote "$arg"
  done
  printf '\n'
}

profile_task_pack_path() {
  PROJECT_ROOT="$project_abs"
  PROJECT_NAME="${PROJECT_NAME:-$project_name}"
  PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
  PROJECT_WORKTREE_SLUG="${PROJECT_WORKTREE_SLUG:-$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")}"
  # shellcheck source=/dev/null
  source "$profile_path"
  AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-}"
  if [[ -n "$AGENT_RAILS_ENV_FILE" && -f "$AGENT_RAILS_ENV_FILE" ]]; then
    # shellcheck source=/dev/null
    source "$AGENT_RAILS_ENV_FILE"
  fi
  PROJECT_NAME="${PROJECT_NAME:-$project_name}"
  if [[ -n "$PROJECT_WORKTREE_SLUG_PRESET" ]]; then
    PROJECT_WORKTREE_SLUG="$PROJECT_WORKTREE_SLUG_PRESET"
  else
    PROJECT_WORKTREE_SLUG="$(agent_rails_project_worktree_slug "$project_abs" "$PROJECT_NAME")"
  fi
  PROFILE_TASK_PACK_PATH="${TASK_PACK_PATH:-$(agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG")}"
}

PROFILE_TASK_PACK_PATH=""
profile_task_pack_path
task_pack_path="$PROFILE_TASK_PACK_PATH"
effective_pack_mode="${pack_mode_arg:-${AGENT_RAILS_PACK_MODE:-normal}}"
pack_command=("$AGENT_RAILS_BIN" pack "${pack_args[@]}" "$goal")
estimate_command=("$AGENT_RAILS_BIN" estimate "${estimate_args[@]}" --file "$task_pack_path")
check_command=("$AGENT_RAILS_BIN" check "${check_args[@]}")
memory_skip_command=("$AGENT_RAILS_BIN" memory suggest "${memory_args[@]}" --decision skip --reason "<why no durable memory>")
memory_write_command=("$AGENT_RAILS_BIN" memory suggest "${memory_args[@]}" --decision keep --write-local --title "<short title>" --trigger "<trigger>" --applies-to "<scope>" --verify "<check>" --caution "<scope limits>" "<brief reusable lesson>")

printf 'AGENT RAILS: ON (mode=%s, pack=%s)\n\n' "$effective_pack_mode" "$task_pack_path"
printf 'Agent Rails Run\n\n'
printf 'Project: %s\n' "$project_abs"
printf 'Profile: %s\n' "$profile_path"
printf 'Goal: %s\n' "$goal"
if [[ -n "$pack_mode_inferred" ]]; then
  printf 'Inferred pack mode: %s\n' "$pack_mode_inferred"
fi
printf 'Task Pack: %s\n\n' "$task_pack_path"

printf 'Commands\n'
printf -- '- Pack: '
print_command "${pack_command[@]}"
printf -- '- Estimate: '
print_command "${estimate_command[@]}"
printf -- '- Check: '
print_command "${check_command[@]}"
printf -- '- Memory curator skip log: '
print_command "${memory_skip_command[@]}"
printf -- '- Memory curator local write: '
print_command "${memory_write_command[@]}"
printf '\n'

if [[ "$print_only" -eq 1 ]]; then
  printf 'Print-only mode. No files written.\n'
  exit 0
fi

"${pack_command[@]}"
printf '\n'
"${estimate_command[@]}"

printf '\nAgent Instructions\n'
printf '0. Tell the user: AGENT RAILS: ON (mode=%s, pack=%s)\n' "$effective_pack_mode" "$task_pack_path"
printf '1. Read the Task Pack: %s\n' "$task_pack_path"
printf '2. Follow Trigger Matrix, Session Marker, Context Budget, Changed File Priority, Memory Cards, Grill Gate, Verification Suggestions, and Subagent Result Contract.\n'
printf '   In lite mode, skip full grill and keep only blocker questions plus deferred decisions.\n'
printf '3. Before final delivery, run:\n'
printf '   '
print_command "${check_command[@]}"
printf '4. After delivery, use agent-memory-curator. If no durable lesson, log skip:\n'
printf '   '
print_command "${memory_skip_command[@]}"
printf '   If valuable, write one local card:\n'
printf '   '
print_command "${memory_write_command[@]}"
