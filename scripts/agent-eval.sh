#!/usr/bin/env bash
# Minimal Agent Rails evaluation dataset and run logger.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  agent-rails eval init [--dir DIR] [--force]
  agent-rails eval record [--task PATH] [--project PATH] [--profile PATH] [--mode baseline|agentrails|NAME] [--dir DIR] [--model NAME] [--pack-mode lite|normal|deep|audit] [--tokenizer auto|char|tiktoken|command] [goal text...]
  agent-rails eval report [--runs DIR] [--output PATH]

Creates evaluation task templates, records JSONL run logs, and summarizes runs.
USAGE
}

subcommand="${1:-}"
if [[ -z "$subcommand" || "$subcommand" == "--help" || "$subcommand" == "-h" ]]; then
  usage
  exit 0
fi
shift || true

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"

json_escape() {
  awk '
    BEGIN { first = 1 }
    {
      if (!first) {
        printf "\\n"
      }
      first = 0
      gsub(/\\/,"\\\\")
      gsub(/"/,"\\\"")
      gsub(/\t/,"\\t")
      gsub(/\r/,"\\r")
      printf "%s", $0
    }
  '
}

json_value() {
  printf '%s' "$1" | json_escape
}

timestamp_utc() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

date_utc() {
  date -u '+%Y-%m-%d'
}

slugify() {
  printf '%s' "$1" | tr '[:upper:] ' '[:lower:]-' | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+|-+$//g'
}

yaml_scalar() {
  local key="$1"
  local path="$2"
  sed -n -E "s/^${key}:[[:space:]]*//p" "$path" | sed -n '1p' | sed -E 's/^"//; s/"$//'
}

write_event() {
  local log_path="$1"
  local event="$2"
  local fields="$3"
  printf '{"ts":"%s","event":"%s"%s}\n' "$(timestamp_utc)" "$event" "$fields" >> "$log_path"
}

init_eval() {
  local eval_dir="evals"
  local force=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        eval_dir="$2"
        shift 2
        ;;
      --force)
        force=1
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

  mkdir -p "$eval_dir/tasks" "$eval_dir/rubrics" "$eval_dir/runs"

  local task_path="$eval_dir/tasks/sample-code-review.yaml"
  local rubric_path="$eval_dir/rubrics/code-review.yaml"
  local readme_path="$eval_dir/README.md"

  if [[ ! -e "$task_path" || "$force" -eq 1 ]]; then
    cat > "$task_path" <<'YAML'
id: sample-code-review-001
type: code_review
repo: /path/to/project
base_ref: main
target_ref: HEAD
task: "Review the current changes and identify behavior risks with file/line evidence."
expected:
  must_find:
    - "replace this with a known important finding"
  must_not_do:
    - "do not modify code during review"
rubric:
  correctness: 0-5
  evidence: 0-5
  verification: 0-5
  noise: 0-5
YAML
  fi

  if [[ ! -e "$rubric_path" || "$force" -eq 1 ]]; then
    cat > "$rubric_path" <<'YAML'
name: code-review
scores:
  correctness: "Finds real issues and avoids missing high-risk defects."
  evidence: "Uses concrete file/line or command evidence."
  verification: "Uses or requests appropriate verification."
  noise: "Avoids style-only findings and duplicate comments."
manual_score_template:
  correctness: null
  evidence: null
  verification: null
  noise: null
  notes: ""
YAML
  fi

  if [[ ! -e "$readme_path" || "$force" -eq 1 ]]; then
    cat > "$readme_path" <<'MD'
# Agent Rails Evals

This directory stores local evaluation tasks, rubrics, and run logs.

Suggested flow:

```bash
agent-rails eval record --task evals/tasks/sample-code-review.yaml --mode baseline
agent-rails eval record --task evals/tasks/sample-code-review.yaml --mode agentrails
agent-rails eval report --runs evals/runs --output evals/report.md
```

Run logs are JSONL. Each line is one event.
MD
  fi

  printf 'Initialized eval directory: %s\n' "$eval_dir"
  printf 'Task template: %s\n' "$task_path"
  printf 'Rubric template: %s\n' "$rubric_path"
}

record_eval() {
  local task_path=""
  local project=""
  local profile_path=""
  local mode="agentrails"
  local eval_dir="evals"
  local model_arg=""
  local pack_mode_arg=""
  local tokenizer_arg=""
  local goal_parts=()

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --task)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        task_path="$2"
        shift 2
        ;;
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
      --mode)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        mode="$2"
        shift 2
        ;;
      --dir)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        eval_dir="$2"
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
      --tokenizer)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        tokenizer_arg="$2"
        shift 2
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

  local task_id="manual-task"
  local task_type="manual"
  local base_ref=""
  local target_ref=""
  local goal="${goal_parts[*]:-}"

  if [[ -n "$task_path" ]]; then
    [[ -f "$task_path" ]] || { printf 'Task file not found: %s\n' "$task_path" >&2; exit 2; }
    task_id="$(yaml_scalar id "$task_path")"
    task_type="$(yaml_scalar type "$task_path")"
    base_ref="$(yaml_scalar base_ref "$task_path")"
    target_ref="$(yaml_scalar target_ref "$task_path")"
    [[ -n "$project" ]] || project="$(yaml_scalar repo "$task_path")"
    [[ -n "$goal" ]] || goal="$(yaml_scalar task "$task_path")"
  fi

  [[ -n "$task_id" ]] || task_id="manual-task"
  [[ -n "$task_type" ]] || task_type="manual"
  [[ -n "$project" ]] || project="$PWD"
  [[ -n "$goal" ]] || goal="TODO: describe the eval task."
  [[ -d "$project" ]] || { printf 'Project directory not found: %s\n' "$project" >&2; exit 2; }

  local project_abs run_date run_id run_dir log_path artifact_dir mode_slug task_slug
  project_abs="$(cd "$project" && pwd)"
  run_date="$(date_utc)"
  task_slug="$(slugify "$task_id")"
  mode_slug="$(slugify "$mode")"
  run_id="${run_date}-${task_slug}-${mode_slug}-$$"
  run_dir="$eval_dir/runs/$run_date"
  artifact_dir="$run_dir/artifacts/$run_id"
  log_path="$run_dir/$task_slug.$mode_slug.jsonl"
  mkdir -p "$artifact_dir"

  local run_args=(run --project "$project_abs")
  local check_args=(check --project "$project_abs")
  if [[ -n "$profile_path" ]]; then
    run_args+=(--profile "$profile_path")
    check_args+=(--profile "$profile_path")
  fi
  check_args+=(--print-only)
  [[ -n "$model_arg" ]] && run_args+=(--model "$model_arg")
  [[ -n "$pack_mode_arg" ]] && run_args+=(--pack-mode "$pack_mode_arg")
  [[ -n "$tokenizer_arg" ]] && run_args+=(--tokenizer "$tokenizer_arg")
  run_args+=("$goal")

  local fields
  fields=",\"run_id\":\"$(json_value "$run_id")\",\"task_id\":\"$(json_value "$task_id")\",\"mode\":\"$(json_value "$mode")\",\"task_type\":\"$(json_value "$task_type")\",\"project\":\"$(json_value "$project_abs")\""
  write_event "$log_path" "run_started" "$fields"
  if [[ -n "$task_path" ]]; then
    write_event "$log_path" "task_loaded" ",\"run_id\":\"$(json_value "$run_id")\",\"task_path\":\"$(json_value "$task_path")\",\"base_ref\":\"$(json_value "$base_ref")\",\"target_ref\":\"$(json_value "$target_ref")\""
  fi

  local run_output="$artifact_dir/agent-rails-run.out"
  local run_exit=0
  write_event "$log_path" "command_started" ",\"run_id\":\"$(json_value "$run_id")\",\"name\":\"agent-rails run\",\"output_path\":\"$(json_value "$run_output")\""
  if "$AGENT_RAILS_BIN" "${run_args[@]}" > "$run_output" 2>&1; then
    run_exit=0
  else
    run_exit=$?
  fi
  write_event "$log_path" "command_finished" ",\"run_id\":\"$(json_value "$run_id")\",\"name\":\"agent-rails run\",\"exit\":$run_exit,\"output_path\":\"$(json_value "$run_output")\""

  local check_output="$artifact_dir/agent-rails-check.out"
  local check_exit=0
  write_event "$log_path" "command_started" ",\"run_id\":\"$(json_value "$run_id")\",\"name\":\"agent-rails check\",\"output_path\":\"$(json_value "$check_output")\""
  if "$AGENT_RAILS_BIN" "${check_args[@]}" > "$check_output" 2>&1; then
    check_exit=0
  else
    check_exit=$?
  fi
  write_event "$log_path" "command_finished" ",\"run_id\":\"$(json_value "$run_id")\",\"name\":\"agent-rails check\",\"exit\":$check_exit,\"output_path\":\"$(json_value "$check_output")\""

  local task_pack_path
  task_pack_path="$(sed -n -E 's/^Task Pack: //p' "$run_output" | sed -n '1p')"
  if [[ -n "$task_pack_path" ]]; then
    write_event "$log_path" "artifact" ",\"run_id\":\"$(json_value "$run_id")\",\"kind\":\"task_pack\",\"path\":\"$(json_value "$task_pack_path")\""
  fi

  local final_exit=0
  [[ "$run_exit" -eq 0 && "$check_exit" -eq 0 ]] || final_exit=1
  write_event "$log_path" "run_finished" ",\"run_id\":\"$(json_value "$run_id")\",\"exit\":$final_exit,\"log_path\":\"$(json_value "$log_path")\",\"artifact_dir\":\"$(json_value "$artifact_dir")\""

  printf 'Recorded eval run: %s\n' "$log_path"
  printf 'Artifacts: %s\n' "$artifact_dir"
  return "$final_exit"
}

report_eval() {
  local runs_dir="evals/runs"
  local output_path=""

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --runs)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        runs_dir="$2"
        shift 2
        ;;
      --output)
        [[ $# -ge 2 ]] || { usage >&2; exit 2; }
        output_path="$2"
        shift 2
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

  local tmp_report
  tmp_report="$(mktemp)"
  {
    printf '# Agent Rails Eval Report\n\n'
    printf -- '- Runs dir: `%s`\n' "$runs_dir"
    printf -- '- Generated: `%s`\n\n' "$(timestamp_utc)"
    printf '| Log | Task | Mode | Exit | Artifacts |\n'
    printf '| --- | --- | --- | ---: | --- |\n'
    if [[ -d "$runs_dir" ]]; then
      while IFS= read -r log_path; do
        task_id="$(sed -n -E 's/.*"event":"run_started".*"task_id":"([^"]*)".*/\1/p' "$log_path" | sed -n '1p')"
        mode="$(sed -n -E 's/.*"event":"run_started".*"mode":"([^"]*)".*/\1/p' "$log_path" | sed -n '1p')"
        exit_code="$(sed -n -E 's/.*"event":"run_finished".*"exit":([0-9]+).*/\1/p' "$log_path" | tail -n1)"
        artifact_dir="$(sed -n -E 's/.*"event":"run_finished".*"artifact_dir":"([^"]*)".*/\1/p' "$log_path" | tail -n1)"
        printf '| `%s` | `%s` | `%s` | `%s` | `%s` |\n' "$log_path" "${task_id:-unknown}" "${mode:-unknown}" "${exit_code:-?}" "${artifact_dir:-}" 
      done < <(find "$runs_dir" -type f -name '*.jsonl' | sort)
    fi
  } > "$tmp_report"

  if [[ -n "$output_path" ]]; then
    mkdir -p "$(dirname "$output_path")"
    cp "$tmp_report" "$output_path"
    printf 'Wrote %s\n' "$output_path"
  else
    cat "$tmp_report"
  fi
  rm -f "$tmp_report"
}

case "$subcommand" in
  init) init_eval "$@" ;;
  record) record_eval "$@" ;;
  report) report_eval "$@" ;;
  *) usage >&2; exit 2 ;;
esac
