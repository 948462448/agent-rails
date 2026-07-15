#!/usr/bin/env bash
# Prepare a default Agent Rails execution loop for an agent session.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails run [--project PATH] [--profile PATH] [--model NAME] [--pack-mode lite|normal|deep|audit] [--budget CHARS] [--token-budget TOKENS] [--tokenizer auto|char|tiktoken|command|huggingface] [--tokenizer-command CMD] [--tokenizer-path PATH] [--print-only] [goal text...]

Generates a Task Pack, estimates its size, and prints the next commands/instructions
for an agent session. This wrapper does not hard-control Claude/Codex internals.
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
model_arg=""
pack_mode_arg=""
budget_arg=""
token_budget_arg=""
tokenizer_arg=""
tokenizer_command_arg=""
tokenizer_path_arg=""
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
    --tokenizer-command)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_command_arg="$2"
      shift 2
      ;;
    --tokenizer-path)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_path_arg="$2"
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

agent_target_project_resolve "$project" "$profile_path" || exit $?
agent_target_project_load_profile required || exit 2
project_abs="$AGENT_TARGET_PROJECT_ROOT"
profile_path="$AGENT_TARGET_PROJECT_PROFILE_PATH"

AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-}"
if [[ -n "$AGENT_RAILS_ENV_FILE" && -f "$AGENT_RAILS_ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$AGENT_RAILS_ENV_FILE"
fi
agent_target_project_finalize
task_pack_path="$AGENT_TARGET_PROJECT_TASK_PACK_PATH"

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
  pack_args+=(--tokenizer "$tokenizer_arg")
  estimate_args+=(--tokenizer "$tokenizer_arg")
fi
if [[ -n "$tokenizer_command_arg" ]]; then
  pack_args+=(--tokenizer-command "$tokenizer_command_arg")
  estimate_args+=(--tokenizer-command "$tokenizer_command_arg")
fi
if [[ -n "$tokenizer_path_arg" ]]; then
  pack_args+=(--tokenizer-path "$tokenizer_path_arg")
  estimate_args+=(--tokenizer-path "$tokenizer_path_arg")
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
