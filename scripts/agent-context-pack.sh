#!/usr/bin/env bash
# Generate an Agent Rails task pack for the current checkout or a target ref.

set -euo pipefail
umask 077

usage() {
  printf 'Usage: %s [--profile PATH] [--base REF] [--target-ref REF] [--output PATH] [--model NAME] [--pack-mode lite|normal|deep|audit] [--budget CHARS] [--token-budget TOKENS] [goal text...]\n' "$0"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

profile_path_arg=""
profile_path=""
base_ref=""
target_ref="HEAD"
target_ref_explicit=0
output_path=""
context_budget_chars_arg=""
context_budget_tokens_arg=""
model_arg=""
pack_mode_arg=""
goal_parts=()

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
    --output)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      output_path="$2"
      shift 2
      ;;
    --budget|--context-budget)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      context_budget_chars_arg="$2"
      shift 2
      ;;
    --token-budget)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      context_budget_tokens_arg="$2"
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

if repo_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  is_git_repo=1
  repo_root="$(cd "$repo_root" && pwd)"
  cd "$repo_root"
else
  is_git_repo=0
  repo_root="$PWD"
fi

PROJECT_ROOT="$repo_root"
PROJECT_NAME="${PROJECT_NAME:-$(basename "$repo_root")}"
PROJECT_WORKTREE_SLUG_PRESET="${PROJECT_WORKTREE_SLUG:-}"
PROJECT_WORKTREE_SLUG="${PROJECT_WORKTREE_SLUG:-$(agent_rails_project_worktree_slug "$repo_root" "$PROJECT_NAME")}"
profile_path="$(agent_rails_resolve_profile "$repo_root" "$PROJECT_NAME" "$profile_path_arg")"

if [[ ! -f "$profile_path" ]]; then
  printf 'Profile not found: %s\n' "$profile_path" >&2
  exit 2
fi

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-}"
if [[ -n "$AGENT_RAILS_ENV_FILE" && -f "$AGENT_RAILS_ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$AGENT_RAILS_ENV_FILE"
fi

PROJECT_NAME="${PROJECT_NAME:-$(basename "$repo_root")}"
if [[ -n "$PROJECT_WORKTREE_SLUG_PRESET" ]]; then
  PROJECT_WORKTREE_SLUG="$PROJECT_WORKTREE_SLUG_PRESET"
else
  PROJECT_WORKTREE_SLUG="$(agent_rails_project_worktree_slug "$repo_root" "$PROJECT_NAME")"
fi
TARGET_REF="$target_ref"
BASE_REF="${base_ref:-${BASE_REF:-}}"
TASK_PACK_PATH="${output_path:-${TASK_PACK_PATH:-$(agent_rails_default_task_pack_path "$PROJECT_WORKTREE_SLUG")}}"
MEMORY_LOCAL_DIR="${MEMORY_LOCAL_DIR:-$(agent_rails_default_memory_dir "$PROJECT_NAME")}"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"
AGENT_RAILS_MODEL="${model_arg:-${AGENT_RAILS_MODEL:-generic}}"
AGENT_RAILS_PACK_MODE="${pack_mode_arg:-${AGENT_RAILS_PACK_MODE:-normal}}"
AGENT_RAILS_GRILL_MAX_QUESTIONS="${AGENT_RAILS_GRILL_MAX_QUESTIONS:-8}"
AGENT_RAILS_CONTEXT_BUDGET_CHARS="${context_budget_chars_arg:-${AGENT_RAILS_CONTEXT_BUDGET_CHARS:-0}}"
AGENT_RAILS_CONTEXT_BUDGET_TOKENS="${context_budget_tokens_arg:-${AGENT_RAILS_CONTEXT_BUDGET_TOKENS:-}}"
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}"
AGENT_RAILS_BUDGET_GIT_PERCENT="${AGENT_RAILS_BUDGET_GIT_PERCENT:-20}"
AGENT_RAILS_BUDGET_MEMORY_PERCENT="${AGENT_RAILS_BUDGET_MEMORY_PERCENT:-40}"
AGENT_RAILS_BUDGET_VERIFY_PERCENT="${AGENT_RAILS_BUDGET_VERIFY_PERCENT:-20}"
AGENT_RAILS_BUDGET_CONTRACT_PERCENT="${AGENT_RAILS_BUDGET_CONTRACT_PERCENT:-20}"
AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="${AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS:-1600}"
AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT="${AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT:-8}"
AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS="${AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS:-4000}"
AGENT_RAILS_CHANGED_FILE_SORT="${AGENT_RAILS_CHANGED_FILE_SORT:-smart}"
OPENMEMORY_BASE_URL="${OPENMEMORY_BASE_URL:-}"
OPENMEMORY_MEMORY="${OPENMEMORY_MEMORY:-}"
OPENMEMORY_INSTANCE="${OPENMEMORY_INSTANCE:-agent_rails_memory_card}"
OPENMEMORY_TABLE="${OPENMEMORY_TABLE:-}"
OPENMEMORY_TOKEN_ENV="${OPENMEMORY_TOKEN_ENV:-OPENMEMORY_ACCESS_KEY}"
OPENMEMORY_LIMIT="${OPENMEMORY_LIMIT:-5}"
OPENMEMORY_TIMEOUT_SECONDS="${OPENMEMORY_TIMEOUT_SECONDS:-8}"
OPENMEMORY_PROJECT_FILTER="${OPENMEMORY_PROJECT_FILTER-$PROJECT_NAME}"
OPENMEMORY_CARD_ID_FILTER="${OPENMEMORY_CARD_ID_FILTER:-}"
OPENMEMORY_TAG_FILTER="${OPENMEMORY_TAG_FILTER:-}"
OPENMEMORY_USER_ID="${OPENMEMORY_USER_ID-agent-rails}"
OPENMEMORY_SESSION_ID="${OPENMEMORY_SESSION_ID:-}"
OPENMEMORY_VECTOR_FIELD="${OPENMEMORY_VECTOR_FIELD:-}"
OPENMEMORY_VECTOR_SOURCE_FIELD="${OPENMEMORY_VECTOR_SOURCE_FIELD:-body}"
OPENMEMORY_DRY_RUN_REQUEST="${OPENMEMORY_DRY_RUN_REQUEST:-0}"
OPENMEMORY_REQUEST_DUMP_PATH="${OPENMEMORY_REQUEST_DUMP_PATH:-$AGENT_RAILS_CONFIG_HOME/agent-context/openmemory-request.json}"

if [[ -z "$OPENMEMORY_TABLE" && -n "$OPENMEMORY_MEMORY" && -n "$OPENMEMORY_INSTANCE" ]]; then
  OPENMEMORY_TABLE="${OPENMEMORY_MEMORY}.${OPENMEMORY_INSTANCE}"
fi

goal="${goal_parts[*]:-TODO: describe the concrete user goal.}"

normalize_positive_int() {
  local value="$1"
  local default_value="$2"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

normalize_percent() {
  local value="$1"
  local default_value="$2"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -le 100 ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

normalize_optional_positive_int() {
  local value="$1"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]]; then
    printf '%s\n' "$value"
  fi
}

normalize_nonnegative_int() {
  local value="$1"
  local default_value="$2"
  if [[ "$value" =~ ^[0-9]+$ ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

normalize_pack_mode() {
  case "$1" in
    lite|normal|deep|audit) printf '%s\n' "$1" ;;
    *) printf 'normal\n' ;;
  esac
}

load_model_preset() {
  local model_key
  model_key="$(printf '%s' "$AGENT_RAILS_MODEL" | tr '[:upper:]' '[:lower:]' | tr ' _' '--')"

  AGENT_RAILS_MODEL_PRESET_FOUND=0
  AGENT_RAILS_MODEL_CANONICAL="$AGENT_RAILS_MODEL"
  AGENT_RAILS_MODEL_CONTEXT_TOKENS=""
  AGENT_RAILS_MODEL_MAX_INPUT_TOKENS=""
  AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS=""
  AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS=""
  AGENT_RAILS_MODEL_MAX_REASONING_TOKENS=""
  AGENT_RAILS_MODEL_RPM=""
  AGENT_RAILS_MODEL_TPM=""
  AGENT_RAILS_MODEL_LITE_TOKENS=""
  AGENT_RAILS_MODEL_NORMAL_TOKENS=""
  AGENT_RAILS_MODEL_DEEP_TOKENS=""
  AGENT_RAILS_MODEL_AUDIT_TOKENS=""

  case "$model_key" in
    qwen3.7-max|qwen-3.7-max|qwen3.7max)
      AGENT_RAILS_MODEL_PRESET_FOUND=1
      AGENT_RAILS_MODEL_CANONICAL="qwen3.7-max"
      AGENT_RAILS_MODEL_CONTEXT_TOKENS=1000000
      AGENT_RAILS_MODEL_MAX_INPUT_TOKENS=991000
      AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS=983000
      AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS=64000
      AGENT_RAILS_MODEL_MAX_REASONING_TOKENS=256000
      AGENT_RAILS_MODEL_LITE_TOKENS=24000
      AGENT_RAILS_MODEL_NORMAL_TOKENS=60000
      AGENT_RAILS_MODEL_DEEP_TOKENS=160000
      AGENT_RAILS_MODEL_AUDIT_TOKENS=320000
      ;;
    deepseek-v4-pro|deepseekv4pro|deepseek-v4pro|deepseek-v4|deepseek4-pro)
      AGENT_RAILS_MODEL_PRESET_FOUND=1
      AGENT_RAILS_MODEL_CANONICAL="deepseek-v4-pro"
      AGENT_RAILS_MODEL_CONTEXT_TOKENS=1000000
      AGENT_RAILS_MODEL_MAX_INPUT_TOKENS=1000000
      AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS=""
      AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS=384000
      AGENT_RAILS_MODEL_MAX_REASONING_TOKENS=""
      AGENT_RAILS_MODEL_RPM=15000
      AGENT_RAILS_MODEL_TPM=1200000
      AGENT_RAILS_MODEL_LITE_TOKENS=24000
      AGENT_RAILS_MODEL_NORMAL_TOKENS=60000
      AGENT_RAILS_MODEL_DEEP_TOKENS=160000
      AGENT_RAILS_MODEL_AUDIT_TOKENS=320000
      ;;
    glm5.1|glm-5.1|glm51)
      AGENT_RAILS_MODEL_PRESET_FOUND=1
      AGENT_RAILS_MODEL_CANONICAL="glm5.1"
      AGENT_RAILS_MODEL_CONTEXT_TOKENS=202000
      AGENT_RAILS_MODEL_MAX_INPUT_TOKENS=202000
      AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS=166000
      AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS=128000
      AGENT_RAILS_MODEL_MAX_REASONING_TOKENS=""
      AGENT_RAILS_MODEL_LITE_TOKENS=12000
      AGENT_RAILS_MODEL_NORMAL_TOKENS=24000
      AGENT_RAILS_MODEL_DEEP_TOKENS=60000
      AGENT_RAILS_MODEL_AUDIT_TOKENS=100000
      ;;
  esac
}

preset_budget_for_mode() {
  case "$AGENT_RAILS_PACK_MODE" in
    lite) printf '%s\n' "${AGENT_RAILS_MODEL_LITE_TOKENS:-}" ;;
    normal) printf '%s\n' "${AGENT_RAILS_MODEL_NORMAL_TOKENS:-}" ;;
    deep) printf '%s\n' "${AGENT_RAILS_MODEL_DEEP_TOKENS:-}" ;;
    audit) printf '%s\n' "${AGENT_RAILS_MODEL_AUDIT_TOKENS:-}" ;;
  esac
}

AGENT_RAILS_PACK_MODE="$(normalize_pack_mode "$AGENT_RAILS_PACK_MODE")"
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="$(normalize_positive_int "$AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE" 2)"
load_model_preset

context_budget_chars_input="$(normalize_optional_positive_int "$AGENT_RAILS_CONTEXT_BUDGET_CHARS")"
context_budget_tokens_input="$(normalize_optional_positive_int "$AGENT_RAILS_CONTEXT_BUDGET_TOKENS")"
AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE=""
AGENT_RAILS_CONTEXT_BUDGET_SOURCE="unbounded"

if [[ -n "$context_budget_chars_input" ]]; then
  AGENT_RAILS_CONTEXT_BUDGET_CHARS="$context_budget_chars_input"
  AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE="$((AGENT_RAILS_CONTEXT_BUDGET_CHARS / AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE))"
  AGENT_RAILS_CONTEXT_BUDGET_SOURCE="char budget"
elif [[ -n "$context_budget_tokens_input" ]]; then
  AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE="$context_budget_tokens_input"
  AGENT_RAILS_CONTEXT_BUDGET_CHARS="$((AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE * AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE))"
  AGENT_RAILS_CONTEXT_BUDGET_SOURCE="token budget"
elif [[ "$AGENT_RAILS_MODEL_PRESET_FOUND" -eq 1 ]]; then
  preset_tokens="$(preset_budget_for_mode)"
  if [[ -n "$preset_tokens" ]]; then
    AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE="$preset_tokens"
    AGENT_RAILS_CONTEXT_BUDGET_CHARS="$((AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE * AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE))"
    AGENT_RAILS_CONTEXT_BUDGET_SOURCE="model preset"
  else
    AGENT_RAILS_CONTEXT_BUDGET_CHARS=0
  fi
else
  AGENT_RAILS_CONTEXT_BUDGET_CHARS=0
fi

AGENT_RAILS_BUDGET_GIT_PERCENT="$(normalize_percent "$AGENT_RAILS_BUDGET_GIT_PERCENT" 20)"
AGENT_RAILS_BUDGET_MEMORY_PERCENT="$(normalize_percent "$AGENT_RAILS_BUDGET_MEMORY_PERCENT" 40)"
AGENT_RAILS_BUDGET_VERIFY_PERCENT="$(normalize_percent "$AGENT_RAILS_BUDGET_VERIFY_PERCENT" 20)"
AGENT_RAILS_BUDGET_CONTRACT_PERCENT="$(normalize_percent "$AGENT_RAILS_BUDGET_CONTRACT_PERCENT" 20)"
AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="$(normalize_positive_int "$AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS" 1600)"
AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT="$(normalize_nonnegative_int "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" 5)"
AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS="$(normalize_nonnegative_int "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS" 4000)"
AGENT_RAILS_GRILL_MAX_QUESTIONS="$(normalize_positive_int "$AGENT_RAILS_GRILL_MAX_QUESTIONS" 8)"
case "$AGENT_RAILS_CHANGED_FILE_SORT" in
  smart|path) ;;
  *) AGENT_RAILS_CHANGED_FILE_SORT="smart" ;;
esac

if [[ "$AGENT_RAILS_PACK_MODE" == "lite" ]]; then
  if [[ "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" -gt 4 ]]; then
    AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT=4
  fi
  if [[ "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS" -gt 1800 ]]; then
    AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS=1800
  fi
  if [[ "$AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS" -gt 900 ]]; then
    AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS=900
  fi
fi

section_budget() {
  local percent="$1"
  if [[ "$AGENT_RAILS_CONTEXT_BUDGET_CHARS" -gt 0 ]]; then
    printf '%s\n' $((AGENT_RAILS_CONTEXT_BUDGET_CHARS * percent / 100))
  else
    printf '0\n'
  fi
}

git_budget_chars="$(section_budget "$AGENT_RAILS_BUDGET_GIT_PERCENT")"
memory_budget_chars="$(section_budget "$AGENT_RAILS_BUDGET_MEMORY_PERCENT")"
verify_budget_chars="$(section_budget "$AGENT_RAILS_BUDGET_VERIFY_PERCENT")"
contract_budget_chars="$(section_budget "$AGENT_RAILS_BUDGET_CONTRACT_PERCENT")"
changed_files_budget_chars="$git_budget_chars"
status_budget_chars="$git_budget_chars"
if [[ "$git_budget_chars" -gt 0 ]]; then
  changed_files_budget_chars=$((git_budget_chars / 2))
  status_budget_chars=$((git_budget_chars - changed_files_budget_chars))
  [[ "$changed_files_budget_chars" -lt 1 ]] && changed_files_budget_chars=1
fi

print_file_excerpt() {
  local path="$1"
  local budget="$2"
  awk -v limit="$budget" '
    BEGIN {
      used = 0
      truncated = 0
    }
    {
      line = $0 "\n"
      len = length(line)
      if (limit > 0 && used + len > limit) {
        remaining = limit - used
        if (remaining > 0) {
          printf "%s", substr(line, 1, remaining)
        }
        truncated = 1
        exit
      }
      printf "%s", line
      used += len
    }
    END {
      if (truncated) {
        printf "\n...[truncated by Agent Rails budget]...\n"
      }
    }
  ' "$path"
}

is_text_file_for_excerpt() {
  local path="$1"
  [[ -f "$path" ]] || return 1
  case "$path" in
    *.bmp|*.gif|*.ico|*.jpeg|*.jpg|*.pdf|*.png|*.webp|*.avif|*.heic|*.mp3|*.mp4|*.mov|*.ttf|*.woff|*.woff2|*.zip|*.gz|*.tgz|*.bz2|*.xz|*.7z|*.jar|*.war|*.class|*.pyc)
      return 1
      ;;
  esac
  [[ ! -s "$path" ]] && return 0
  LC_ALL=C grep -Iq . "$path" 2>/dev/null
}

resolve_default_base_ref() {
  local ref
  for ref in origin/main origin/master main master; do
    if git rev-parse --verify --quiet "$ref" >/dev/null; then
      printf '%s\n' "$ref"
      return 0
    fi
  done
}

if [[ "$is_git_repo" -eq 1 ]]; then
  if [[ -z "$BASE_REF" ]]; then
    BASE_REF="$(resolve_default_base_ref)"
  fi

  if ! git rev-parse --verify --quiet "$TARGET_REF" >/dev/null; then
    printf 'Target ref not found: %s\n' "$TARGET_REF" >&2
    exit 2
  fi

  if [[ "$target_ref_explicit" -eq 1 ]]; then
    branch="$TARGET_REF"
  else
    branch="$(git branch --show-current 2>/dev/null || true)"
  fi
  head_sha="$(git rev-parse --short "$TARGET_REF")"

  if [[ -n "$BASE_REF" ]] && git rev-parse --verify --quiet "$BASE_REF" >/dev/null; then
    merge_base="$(git merge-base "$TARGET_REF" "$BASE_REF")"
  else
    merge_base="$(git rev-parse "$TARGET_REF")"
  fi
else
  if [[ "$target_ref_explicit" -eq 1 ]]; then
    printf 'Target ref requires a git repository: %s\n' "$TARGET_REF" >&2
    exit 2
  fi
  branch="no-git"
  head_sha="n/a"
  merge_base="n/a"
fi

tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT

changed_file_list="$tmp_dir/changed-files"
if [[ "$is_git_repo" -eq 1 ]]; then
  {
    git diff --name-only "$merge_base"..."$TARGET_REF" 2>/dev/null || true
    if [[ "$target_ref_explicit" -eq 0 ]]; then
      git diff --name-only 2>/dev/null || true
      git diff --cached --name-only 2>/dev/null || true
      git ls-files --others --exclude-standard 2>/dev/null || true
    fi
  } | awk 'NF' | sort -u > "$changed_file_list"
else
  : > "$changed_file_list"
fi

scored_changed_file_list="$tmp_dir/changed-files-scored"
sorted_changed_file_list="$tmp_dir/changed-files-sorted"
if [[ -s "$changed_file_list" && "$AGENT_RAILS_CHANGED_FILE_SORT" == "smart" ]]; then
  awk -v goal="$goal" '
    BEGIN {
      goal_l = tolower(goal)
      token_count = split(goal_l, raw_tokens, /[^[:alnum:]_.-]+/)
      for (i = 1; i <= token_count; i++) {
        token = raw_tokens[i]
        if (length(token) >= 3) {
          goal_tokens[token] = 1
        }
      }
    }
    function add_reason(text) {
      if (reason == "") {
        reason = text
      } else {
        reason = reason ", " text
      }
    }
    function basename(path, parts, count) {
      count = split(path, parts, "/")
      return parts[count]
    }
    {
      path = $0
      path_l = tolower(path)
      base = basename(path_l)
      score = 0
      reason = ""

      for (token in goal_tokens) {
        if (index(path_l, token) > 0) {
          score += 80
          add_reason("goal:" token)
        }
      }

      if (path_l ~ /(^|\/)(agents|claude|readme|context)([-_.a-z0-9]*)?\.md$/) {
        score += 70
        add_reason("entry-doc")
      }
      if (path_l ~ /^(bin|scripts)\// || path_l ~ /^profiles\// || path_l ~ /^skills\// || path_l ~ /^templates\//) {
        score += 55
        add_reason("agent-rails-control")
      }
      if (path_l ~ /(^|\/)(test|tests|spec|specs)\// || path_l ~ /(test|spec)\.(sh|py|js|ts|tsx|jsx)$/) {
        score += 45
        add_reason("tests")
      }
      if (path_l ~ /\.(sh|py|js|jsx|ts|tsx|java|kt|go|rs|mjs|cjs|rb|php|swift)$/) {
        score += 40
        add_reason("code")
      }
      if (path_l ~ /(^|\/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|pom\.xml|build\.gradle|pyproject\.toml|requirements.*\.txt|go\.mod|cargo\.toml)$/) {
        score += 35
        add_reason("build-config")
      }
      if (score == 0) {
        score = 10
        reason = "path"
      }
      printf "%06d\t%s\t%s\n", score, path, reason
    }
  ' "$changed_file_list" | sort -r -k1,1 -k2,2 > "$scored_changed_file_list"
  cut -f2 "$scored_changed_file_list" > "$sorted_changed_file_list"
else
  awk '{ printf "%06d\t%s\tpath\n", 10, $0 }' "$changed_file_list" > "$scored_changed_file_list"
  cp "$changed_file_list" "$sorted_changed_file_list"
fi

status_file="$tmp_dir/status"
if [[ "$is_git_repo" -eq 0 ]]; then
  printf 'No git repository detected; git state is unavailable.\n' > "$status_file"
elif [[ "$target_ref_explicit" -eq 0 ]]; then
  git status --porcelain=v1 -uall > "$status_file"
else
  printf 'Target ref mode: current working tree changes are not included.\n' > "$status_file"
fi

changed_files_markdown_file="$tmp_dir/changed-files.md"
if [[ -s "$sorted_changed_file_list" ]]; then
  sed 's/^/- `/' "$sorted_changed_file_list" | sed 's/$/`/' > "$changed_files_markdown_file"
else
  printf -- '- None detected.\n' > "$changed_files_markdown_file"
fi

changed_file_priority_file="$tmp_dir/changed-file-priority.md"
if [[ -s "$scored_changed_file_list" ]]; then
  awk -F '\t' '{ printf "- `%s` score=%d (%s)\n", $2, $1 + 0, $3 }' "$scored_changed_file_list" > "$changed_file_priority_file"
else
  printf -- '- None detected.\n' > "$changed_file_priority_file"
fi

changed_file_excerpts_file="$tmp_dir/changed-file-excerpts.md"
: > "$changed_file_excerpts_file"
if [[ "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" -gt 0 && "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS" -gt 0 && -s "$sorted_changed_file_list" ]]; then
  excerpt_count=0
  while IFS= read -r changed_path; do
    [[ -z "$changed_path" ]] && continue
    if ! is_text_file_for_excerpt "$changed_path"; then
      continue
    fi
    {
      printf '### `%s`\n\n' "$changed_path"
      printf '~~~text\n'
      print_file_excerpt "$changed_path" "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS"
      printf '~~~\n\n'
    } >> "$changed_file_excerpts_file"
    excerpt_count=$((excerpt_count + 1))
    [[ "$excerpt_count" -ge "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" ]] && break
  done < "$sorted_changed_file_list"
fi

if [[ ! -s "$changed_file_excerpts_file" ]]; then
  printf -- '- No changed text file excerpts selected.\n' > "$changed_file_excerpts_file"
fi

add_entry_doc() {
  local doc="$1"
  local label="$2"
  [[ -n "$doc" ]] && printf '%s|%s\n' "$doc" "$label"
}

select_entry_docs() {
  local out="$1"
  : > "$out"
  add_entry_doc "${ENTRY_DOC_ROOT:-}" root >> "$out"
  if grep -Eq '^backend/' "$changed_file_list"; then
    add_entry_doc "${ENTRY_DOC_BACKEND:-}" backend >> "$out"
  fi
  if grep -Eq '^runtime/' "$changed_file_list"; then
    add_entry_doc "${ENTRY_DOC_RUNTIME:-}" runtime >> "$out"
  fi
  if grep -Eq '^frontend/' "$changed_file_list"; then
    add_entry_doc "${ENTRY_DOC_FRONTEND:-}" frontend >> "$out"
  fi
  if grep -Eq '^dolphin/' "$changed_file_list"; then
    add_entry_doc "${ENTRY_DOC_DOLPHIN:-}" dolphin >> "$out"
  fi
  if grep -Eq '^contracts/' "$changed_file_list"; then
    add_entry_doc "${ENTRY_DOC_CONTRACTS:-}" contracts >> "$out"
  fi
}

doc_exists() {
  local doc="$1"
  if [[ "$is_git_repo" -eq 1 && "$target_ref_explicit" -eq 1 ]]; then
    git cat-file -e "$TARGET_REF:$doc" 2>/dev/null || [[ -e "$doc" ]]
  else
    [[ -e "$doc" ]]
  fi
}

doc_source_note() {
  local doc="$1"
  if [[ "$is_git_repo" -eq 1 && "$target_ref_explicit" -eq 1 && ! -f "$doc" ]]; then
    printf 'at %s' "$TARGET_REF"
  else
    printf 'working tree'
  fi
}

print_lines_as_bullets() {
  local text="$1"
  if [[ -z "$text" ]]; then
    printf -- '- None configured.\n'
    return 0
  fi

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    printf -- '- %s\n' "$line"
  done <<< "$text"
}

print_doc_status() {
  local label="$1"
  local doc="$2"
  if [[ -z "$doc" ]]; then
    printf -- '- %s: not configured.\n' "$label"
  elif doc_exists "$doc"; then
    printf -- '- %s: `%s` (%s)\n' "$label" "$doc" "$(doc_source_note "$doc")"
  else
    printf -- '- %s: missing `%s`\n' "$label" "$doc"
  fi
}

entry_docs_file="$tmp_dir/entry-docs"
select_entry_docs "$entry_docs_file"

haystack_file="$tmp_dir/haystack"
{
  printf '%s\n' "$goal"
  sed 's#/# #g' "$changed_file_list"
} | tr '[:upper:]' '[:lower:]' > "$haystack_file"

selected_cards_file="$tmp_dir/cards"
: > "$selected_cards_file"
if [[ -d "$MEMORY_LOCAL_DIR" ]]; then
  while IFS= read -r card; do
    [[ "$(basename "$card")" == "README.md" ]] && continue
    card_text="$(tr '[:upper:]' '[:lower:]' < "$card")"
    card_name="$(basename "$card" .md | tr '[:upper:]' '[:lower:]' | tr '-' ' ')"
    if grep -Fqi "$card_name" "$haystack_file"; then
      printf '%s\n' "$card" >> "$selected_cards_file"
      continue
    fi
    while IFS= read -r token; do
      token="$(printf '%s' "$token" | sed -E 's/^[[:space:]]*-[[:space:]]*//; s/^[[:space:]]+//; s/[[:space:]]+$//')"
      [[ -z "$token" ]] && continue
      if grep -Fqi "$token" "$haystack_file"; then
        printf '%s\n' "$card" >> "$selected_cards_file"
        break
      fi
    done < <(printf '%s\n' "$card_text" | sed -n '/^triggers:/,/^[a-z_].*:/p' | grep '^[[:space:]]*- ' || true)
  done < <(find "$MEMORY_LOCAL_DIR" -maxdepth 1 -type f -name '*.md' | sort)
fi

memory_provider_uses_openmemory() {
  case "$MEMORY_PROVIDER" in
    openmemory|hybrid) return 0 ;;
    *) return 1 ;;
  esac
}

sanitize_openmemory_message() {
  sed -E \
    -e 's/[0-9]{1,3}(\.[0-9]{1,3}){3}/<ip>/g' \
    -e 's/trace_[[:alnum:]_:-]+/<trace>/g' \
    -e 's/[[:space:]]+/ /g' \
    -e 's/^ //' \
    -e 's/ $//' \
    | cut -c 1-220
}

selected_online_cards_file="$tmp_dir/online-cards"
openmemory_status_file="$tmp_dir/openmemory-status"
: > "$selected_online_cards_file"
: > "$openmemory_status_file"

fetch_openmemory_cards() {
  if ! memory_provider_uses_openmemory; then
    printf 'OpenMemory disabled; using local memory provider.\n' > "$openmemory_status_file"
    return 0
  fi

  if [[ -z "$OPENMEMORY_BASE_URL" || -z "$OPENMEMORY_MEMORY" || -z "$OPENMEMORY_TABLE" ]]; then
    printf 'OpenMemory skipped: set OPENMEMORY_BASE_URL, OPENMEMORY_MEMORY, and OPENMEMORY_TABLE or OPENMEMORY_INSTANCE.\n' > "$openmemory_status_file"
    return 0
  fi

  local token
  token="${!OPENMEMORY_TOKEN_ENV-}"
  if [[ -z "$token" ]]; then
    printf 'OpenMemory skipped: token env %s is not set.\n' "$OPENMEMORY_TOKEN_ENV" > "$openmemory_status_file"
    return 0
  fi

  if ! command -v curl >/dev/null 2>&1 || ! command -v jq >/dev/null 2>&1; then
    printf 'OpenMemory skipped: curl and jq are required for online memory retrieval.\n' > "$openmemory_status_file"
    return 0
  fi

  local limit="$OPENMEMORY_LIMIT"
  if ! [[ "$limit" =~ ^[0-9]+$ ]]; then
    limit=5
  fi

  local base_url="${OPENMEMORY_BASE_URL%/}"
  local query_file="$tmp_dir/openmemory-query"
  {
    printf '%s\n' "$goal"
    printf '\nChanged files:\n'
    cat "$changed_file_list"
  } > "$query_file"

  local request_file="$tmp_dir/openmemory-request.json"
  jq -n \
    --arg memory "$OPENMEMORY_MEMORY" \
    --arg table "$OPENMEMORY_TABLE" \
    --arg project_filter "$OPENMEMORY_PROJECT_FILTER" \
    --arg card_id_filter "$OPENMEMORY_CARD_ID_FILTER" \
    --arg tag_filter "$OPENMEMORY_TAG_FILTER" \
    --arg user_id "$OPENMEMORY_USER_ID" \
    --arg session_id "$OPENMEMORY_SESSION_ID" \
    --arg vector_field "$OPENMEMORY_VECTOR_FIELD" \
    --arg vector_source_field "$OPENMEMORY_VECTOR_SOURCE_FIELD" \
    --arg query "$(cat "$query_file")" \
    --argjson limit "$limit" \
    '({}
      + (if $project_filter == "" then {} else {project: $project_filter} end)
      + (if $card_id_filter == "" then {} else {card_id: $card_id_filter} end)
      + (if $tag_filter == "" then {} else {tags: $tag_filter} end)
    ) as $filters
    | {
      memory: $memory,
      table: $table,
      limit: $limit,
      field_selector: {
        attributes: {
          mode: "include",
          include: [
            "card_id",
            "project",
            "title",
            "triggers",
            "applies_to",
            "staleness",
            "source",
            "body",
            "verify",
            "tags",
            "updated_at"
          ]
        }
      }
    }
    + (if $user_id == "" then {} else {user_id: $user_id} end)
    + (if $session_id == "" then {} else {session_id: $session_id} end)
    + (if $filters == {} then {} else {filters: $filters} end)
    + (if $vector_field == "" then {} else {
        embedding_query: {
          field_name: $vector_field,
          source_fields: [{name: $vector_source_field, value: $query}]
        }
      } end)' > "$request_file"

  if [[ "$OPENMEMORY_DRY_RUN_REQUEST" == "1" ]]; then
    mkdir -p "$(dirname "$OPENMEMORY_REQUEST_DUMP_PATH")"
    cp "$request_file" "$OPENMEMORY_REQUEST_DUMP_PATH"
    printf 'OpenMemory dry-run request written to `%s`.\n' "$OPENMEMORY_REQUEST_DUMP_PATH" > "$openmemory_status_file"
    return 0
  fi

  local response_file="$tmp_dir/openmemory-response.json"
  local curl_error_file="$tmp_dir/openmemory-curl.err"
  local http_code
  http_code="$(curl -sS -o "$response_file" -w '%{http_code}' \
    --max-time "$OPENMEMORY_TIMEOUT_SECONDS" \
    -X POST "$base_url/agent-memory/v1/memories/collection/list" \
    -H "Authorization: Bearer $token" \
    -H "Content-Type: application/json; charset=utf-8" \
    --data @"$request_file" 2>"$curl_error_file" || true)"

  if [[ ! "$http_code" =~ ^2 ]]; then
    printf 'OpenMemory query failed: HTTP %s. %s\n' "${http_code:-unknown}" "$(tr '\n' ' ' < "$curl_error_file" | sed 's/[[:space:]]*$//')" > "$openmemory_status_file"
    return 0
  fi

  local api_code
  api_code="$(jq -r '.code // empty' "$response_file" 2>/dev/null || true)"
  if [[ "$api_code" != "OK" ]]; then
    printf 'OpenMemory query failed: code=%s message=%s\n' \
      "${api_code:-unknown}" \
      "$(jq -r '.message // ""' "$response_file" 2>/dev/null | sanitize_openmemory_message)" > "$openmemory_status_file"
    return 0
  fi

  local count
  count="$(jq -r '.data.memories // [] | length' "$response_file")"
  {
    printf 'OpenMemory query OK: %s record(s) from `%s`.\n' "$count" "$OPENMEMORY_TABLE"
    printf 'OpenMemory scope: user_id=`%s`, session_id=`%s`.\n' "${OPENMEMORY_USER_ID:-<empty>}" "${OPENMEMORY_SESSION_ID:-<empty>}"
    printf 'OpenMemory filters: project=`%s`, card_id=`%s`, tags=`%s`, vector_field=`%s`.\n' \
      "${OPENMEMORY_PROJECT_FILTER:-<empty>}" \
      "${OPENMEMORY_CARD_ID_FILTER:-<empty>}" \
      "${OPENMEMORY_TAG_FILTER:-<empty>}" \
      "${OPENMEMORY_VECTOR_FIELD:-<empty>}"
  } > "$openmemory_status_file"

  jq -r '
    def text($v):
      ($v // "" | if type == "string" then . else tostring end | gsub("\n"; " ") | .[0:700]);

    .data.memories[]? as $raw
    | ($raw.memory // $raw.data // $raw) as $m
    | ($m.attributes // $m.data // $m) as $a
    | "- OpenMemory `" + text($a.card_id // $a.id // $m.id) + "`"
      + (if $m.score == null then "" else " score=" + ($m.score | tostring) end)
      + "\n  - title: " + text($a.title)
      + "\n  - staleness: " + text($a.staleness)
      + "\n  - triggers: " + text($a.triggers)
      + "\n  - applies_to: " + text($a.applies_to)
      + "\n  - body: " + text($a.body)
      + "\n  - verify: " + text($a.verify)
      + "\n  - source: " + text($a.source)
  ' "$response_file" > "$selected_online_cards_file"
}

fetch_openmemory_cards

suggestions_file="$tmp_dir/verification"
agent_check_script="$AGENT_RAILS_HOME/scripts/agent-check.sh"
if [[ -x "$agent_check_script" ]]; then
  if [[ "$target_ref_explicit" -eq 1 ]]; then
    AGENT_RAILS_SUPPRESS_MARKER=1 "$agent_check_script" --profile "$profile_path" --base "$BASE_REF" --target-ref "$TARGET_REF" --print-only > "$suggestions_file" || true
  else
    AGENT_RAILS_SUPPRESS_MARKER=1 "$agent_check_script" --profile "$profile_path" --base "$BASE_REF" --print-only > "$suggestions_file" || true
  fi
else
  printf 'Run agent-rails check after it is available.\n' > "$suggestions_file"
fi

local_card_count="$(awk 'END { print NR + 0 }' "$selected_cards_file")"
online_memory_budget="$memory_budget_chars"
local_memory_budget="$memory_budget_chars"
local_card_budget="$AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS"
if [[ "$memory_budget_chars" -gt 0 ]]; then
  if [[ -s "$selected_online_cards_file" && "$local_card_count" -gt 0 ]]; then
    online_memory_budget=$((memory_budget_chars / 2))
    local_memory_budget=$((memory_budget_chars - online_memory_budget))
  elif [[ -s "$selected_online_cards_file" ]]; then
    online_memory_budget="$memory_budget_chars"
    local_memory_budget=0
  else
    online_memory_budget=0
    local_memory_budget="$memory_budget_chars"
  fi

  if [[ "$local_card_count" -gt 0 ]]; then
    local_card_budget=$((local_memory_budget / local_card_count))
    [[ "$local_card_budget" -lt 1 ]] && local_card_budget=1
  fi
fi

mkdir -p "$(dirname "$TASK_PACK_PATH")"
if [[ -f "$TASK_PACK_PATH" ]]; then
  chmod 600 "$TASK_PACK_PATH"
fi

{
  printf '# Agent Task Pack\n\n'
  printf '> Generated by Agent Rails.\n\n'

  printf '## Session Marker\n\n'
  printf 'AGENT RAILS: ON (mode=%s, pack=%s)\n\n' "$AGENT_RAILS_PACK_MODE" "$TASK_PACK_PATH"
  printf -- '- User-visible opening line: `AGENT RAILS: ON (mode=%s, pack=%s)`.\n' "$AGENT_RAILS_PACK_MODE" "$TASK_PACK_PATH"
  printf -- '- If this pack is intentionally skipped later, say `AGENT RAILS: SKIPPED (reason=<reason>)` instead of staying silent.\n\n'

  printf '## Goal\n\n%s\n\n' "$goal"

  printf '## Context Budget\n\n'
  printf -- '- Model: `%s`' "$AGENT_RAILS_MODEL_CANONICAL"
  if [[ "$AGENT_RAILS_MODEL_PRESET_FOUND" -eq 1 ]]; then
    printf ' (context `%s` tokens, max input `%s` tokens' \
      "$AGENT_RAILS_MODEL_CONTEXT_TOKENS" \
      "$AGENT_RAILS_MODEL_MAX_INPUT_TOKENS"
    if [[ -n "$AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS" ]]; then
      printf ', thinking input `%s` tokens' "$AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS"
    fi
    printf ', max output `%s` tokens' "$AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS"
    if [[ -n "$AGENT_RAILS_MODEL_MAX_REASONING_TOKENS" ]]; then
      printf ', max reasoning `%s` tokens' "$AGENT_RAILS_MODEL_MAX_REASONING_TOKENS"
    fi
    if [[ -n "$AGENT_RAILS_MODEL_RPM" ]]; then
      printf ', rpm `%s`' "$AGENT_RAILS_MODEL_RPM"
    fi
    if [[ -n "$AGENT_RAILS_MODEL_TPM" ]]; then
      printf ', tpm `%s`' "$AGENT_RAILS_MODEL_TPM"
    fi
    printf ')'
  else
    printf ' (no preset)'
  fi
  printf '\n'
  printf -- '- Pack mode: `%s`\n' "$AGENT_RAILS_PACK_MODE"
  if [[ "$AGENT_RAILS_PACK_MODE" == "lite" ]]; then
    printf -- '- Lite mode: skip full grill, keep context/checklist/memory/verification focused, and ask only blocker questions.\n'
  fi
  printf -- '- Grill question budget: `%s`\n' "$AGENT_RAILS_GRILL_MAX_QUESTIONS"
  printf -- '- Chars/token estimate: `%s`\n' "$AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE"
  if [[ "$AGENT_RAILS_CONTEXT_BUDGET_CHARS" -gt 0 ]]; then
    printf -- '- Mode: bounded by approximate character budget.\n'
    printf -- '- Budget source: `%s`\n' "$AGENT_RAILS_CONTEXT_BUDGET_SOURCE"
    if [[ -n "$AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE" ]]; then
      printf -- '- Token budget: `%s` tokens\n' "$AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE"
    fi
    printf -- '- Total: `%s` chars\n' "$AGENT_RAILS_CONTEXT_BUDGET_CHARS"
    printf -- '- Git state: `%s%%` -> `%s` chars\n' "$AGENT_RAILS_BUDGET_GIT_PERCENT" "$git_budget_chars"
    printf -- '  - Changed file sort: `%s`\n' "$AGENT_RAILS_CHANGED_FILE_SORT"
    printf -- '  - Changed files: `%s` chars\n' "$changed_files_budget_chars"
    printf -- '  - Working tree status: `%s` chars\n' "$status_budget_chars"
    printf -- '  - Changed file excerpts: `%s` file(s), `%s` chars each\n' "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS"
    printf -- '- Memory cards: `%s%%` -> `%s` chars\n' "$AGENT_RAILS_BUDGET_MEMORY_PERCENT" "$memory_budget_chars"
    printf -- '- Verification suggestions: `%s%%` -> `%s` chars\n' "$AGENT_RAILS_BUDGET_VERIFY_PERCENT" "$verify_budget_chars"
    printf -- '- Contract/checklist: `%s%%` -> `%s` chars\n' "$AGENT_RAILS_BUDGET_CONTRACT_PERCENT" "$contract_budget_chars"
  else
    printf -- '- Mode: unbounded; set `--model NAME`, `--budget CHARS`, `--token-budget TOKENS`, or `AGENT_RAILS_CONTEXT_BUDGET_CHARS` to enable section budgets.\n'
    printf -- '- Local memory card excerpt default: `%s` chars per card.\n' "$AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS"
    printf -- '- Changed file sort: `%s`.\n' "$AGENT_RAILS_CHANGED_FILE_SORT"
    printf -- '- Changed file excerpts: `%s` file(s), `%s` chars each.\n' "$AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT" "$AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS"
  fi
  printf '\n'

  printf '## Current Git State\n\n'
  printf -- '- Project: `%s`\n' "$PROJECT_NAME"
  printf -- '- Branch: `%s`\n' "${branch:-detached}"
  printf -- '- Target ref: `%s`\n' "$TARGET_REF"
  printf -- '- HEAD: `%s`\n' "$head_sha"
  printf -- '- Base ref: `%s`\n' "${BASE_REF:-none}"
  printf -- '- Merge base: `%s`\n\n' "${merge_base:0:12}"

  printf '## Changed Files\n\n'
  print_file_excerpt "$changed_files_markdown_file" "$changed_files_budget_chars"
  printf '\n'

  printf '## Changed File Priority\n\n'
  print_file_excerpt "$changed_file_priority_file" "$changed_files_budget_chars"
  printf '\n'

  printf '## Changed File Excerpts\n\n'
  cat "$changed_file_excerpts_file"
  printf '\n'

  printf '## Working Tree Status\n\n'
  if [[ -s "$status_file" ]]; then
    printf '```text\n'
    print_file_excerpt "$status_file" "$status_budget_chars"
    printf '```\n'
  else
    printf 'Clean.\n'
  fi
  printf '\n'

  printf '## Relevant Entry Docs\n\n'
  while IFS='|' read -r doc label; do
    [[ -z "$doc" ]] && continue
    if doc_exists "$doc"; then
      printf -- '- `%s` (%s, %s)\n' "$doc" "$label" "$(doc_source_note "$doc")"
    else
      printf -- '- MISSING `%s` (%s)\n' "$doc" "$label"
    fi
  done < "$entry_docs_file"
  printf '\n'

  printf '## Context Gaps\n\n'
  gaps=0
  while IFS='|' read -r doc label; do
    [[ -z "$doc" ]] && continue
    if ! doc_exists "$doc"; then
      printf -- '- `%s` not found for %s context.\n' "$doc" "$label"
      gaps=$((gaps + 1))
    fi
  done < "$entry_docs_file"
  [[ "$gaps" -eq 0 ]] && printf -- '- None detected.\n'
  printf '\n'

  printf '## Agent Rails Contract\n\n'
  printf '### Trigger Matrix\n\n'
  print_lines_as_bullets "${AGENT_RAILS_TRIGGER_RULES:-}"
  printf '\n'
  printf '### Role In This Task\n\n'
  print_lines_as_bullets "${AGENT_RAILS_ROLE_RULES:-}"
  printf '\n'
  printf '### Workflow Rules\n\n'
  print_lines_as_bullets "${AGENT_RAILS_WORKFLOW_RULES:-}"
  printf '\n### Target Scope Rules\n\n'
  print_lines_as_bullets "${AGENT_RAILS_TARGET_SCOPE_RULES:-}"
  printf '\n### Sensitive Output Rules\n\n'
  print_lines_as_bullets "${AGENT_RAILS_SENSITIVE_OUTPUT_RULES:-}"
  printf '\n### Grill Gate\n\n'
  if [[ "$AGENT_RAILS_PACK_MODE" == "lite" ]]; then
    printf -- '- Lite mode active: do not run a full grill; preserve scope, memory, verification, and checklist value.\n'
  fi
  print_lines_as_bullets "${AGENT_RAILS_GRILL_RULES:-}"
  printf '\n### Memory Sync Rules\n\n'
  print_lines_as_bullets "${AGENT_RAILS_MEMORY_SYNC_RULES:-}"
  printf '\n### Quality Gates\n\n'
  print_lines_as_bullets "${AGENT_RAILS_QUALITY_GATES:-}"
  printf '\n### Failure Rules\n\n'
  print_lines_as_bullets "${AGENT_RAILS_FAILURE_RULES:-}"
  printf '\n'

  printf '## Subagent Result Contract\n\n'
  printf 'When delegating work to a subagent, require the final subagent response to include:\n\n'
  print_lines_as_bullets "${AGENT_RAILS_SUBAGENT_RESULT_CONTRACT:-}"
  printf '\n'

  printf '## Project Configuration\n\n'
  print_doc_status "Domain map" "${DOMAIN_DOC_MAP:-}"
  print_doc_status "Domain docs" "${DOMAIN_DOC_ROOT:-}"
  print_doc_status "ADR directory" "${ADR_DIR:-}"
  print_doc_status "Agent docs" "${AGENT_DOC_DIR:-}"
  print_doc_status "Issue tracker" "${ISSUE_TRACKER_DOC:-}"
  print_doc_status "Triage labels" "${TRIAGE_LABELS_DOC:-}"
  printf '\n'

  printf '## Memory Provider\n\n'
  printf -- '- Mode: `%s`\n' "$MEMORY_PROVIDER"
  if [[ -s "$openmemory_status_file" ]]; then
    sed 's/^/- /' "$openmemory_status_file"
  fi
  printf '\n'

  printf '## Memory Cards\n\n'
  if [[ -s "$selected_online_cards_file" ]]; then
    printf '### Online\n\n'
    print_file_excerpt "$selected_online_cards_file" "$online_memory_budget"
    printf '\n\n'
  fi
  if [[ -s "$selected_cards_file" ]]; then
    printf '### Local\n\n'
    while IFS= read -r card; do
      [[ -z "$card" ]] && continue
      printf '#### `%s`\n\n' "$card"
      printf '~~~markdown\n'
      print_file_excerpt "$card" "$local_card_budget"
      printf '~~~\n\n'
    done < "$selected_cards_file"
  else
    printf -- '- No local cards selected.\n'
  fi
  printf '\n'

  printf '## Verification Suggestions\n\n'
  printf '```text\n'
  print_file_excerpt "$suggestions_file" "$verify_budget_chars"
  printf '```\n\n'

  printf '## Delivery Checklist\n\n'
  printf -- '- What changed\n'
  printf -- '- What was verified\n'
  printf -- '- What was not verified\n'
  printf -- '- Residual risks\n'
  printf -- '- Next action suggestions: fix / do not fix / later\n'
} > "$TASK_PACK_PATH"
chmod 600 "$TASK_PACK_PATH"

printf 'AGENT RAILS: ON (mode=%s, pack=%s)\n' "$AGENT_RAILS_PACK_MODE" "$TASK_PACK_PATH"
printf 'Wrote %s\n' "$TASK_PACK_PATH"
