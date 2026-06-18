#!/usr/bin/env bash
# Generate an Agent Rails task pack for the current checkout or a target ref.

set -euo pipefail

usage() {
  printf 'Usage: %s [--profile PATH] [--base REF] [--target-ref REF] [--output PATH] [goal text...]\n' "$0"
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"

profile_path="$AGENT_RAILS_HOME/profiles/open-eval.profile"
base_ref=""
target_ref="HEAD"
target_ref_explicit=0
output_path=""
goal_parts=()

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
      goal_parts+=("$1")
      shift
      ;;
  esac
done

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

AGENT_RAILS_ENV_FILE="${AGENT_RAILS_ENV_FILE:-$HOME/.agent-rails/openmemory.env}"
if [[ -f "$AGENT_RAILS_ENV_FILE" ]]; then
  # shellcheck source=/dev/null
  source "$AGENT_RAILS_ENV_FILE"
fi

PROJECT_NAME="${PROJECT_NAME:-$(basename "$repo_root")}"
BASE_REF="${base_ref:-${BASE_REF:-origin/master}}"
TARGET_REF="$target_ref"
TASK_PACK_PATH="${output_path:-${TASK_PACK_PATH:-.scratch/agent-context/task-pack.md}}"
MEMORY_LOCAL_DIR="${MEMORY_LOCAL_DIR:-$AGENT_RAILS_HOME/memory/$PROJECT_NAME}"
MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"
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
OPENMEMORY_REQUEST_DUMP_PATH="${OPENMEMORY_REQUEST_DUMP_PATH:-.scratch/agent-context/openmemory-request.json}"

if [[ -z "$OPENMEMORY_TABLE" && -n "$OPENMEMORY_MEMORY" && -n "$OPENMEMORY_INSTANCE" ]]; then
  OPENMEMORY_TABLE="${OPENMEMORY_MEMORY}.${OPENMEMORY_INSTANCE}"
fi

goal="${goal_parts[*]:-TODO: describe the concrete user goal.}"
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

status_file="$tmp_dir/status"
if [[ "$target_ref_explicit" -eq 0 ]]; then
  git status --porcelain=v1 -uall > "$status_file"
else
  printf 'Target ref mode: current working tree changes are not included.\n' > "$status_file"
fi

select_entry_docs() {
  local out="$1"
  : > "$out"
  [[ -n "${ENTRY_DOC_ROOT:-}" ]] && printf '%s|root\n' "$ENTRY_DOC_ROOT" >> "$out"

  if grep -Eq '^backend/' "$changed_file_list"; then
    printf '%s|backend\n' "${ENTRY_DOC_BACKEND:-backend/AGENTS.md}" >> "$out"
  fi
  if grep -Eq '^runtime/' "$changed_file_list"; then
    printf '%s|runtime\n' "${ENTRY_DOC_RUNTIME:-runtime/AGENTS.md}" >> "$out"
  fi
  if grep -Eq '^frontend/' "$changed_file_list"; then
    printf '%s|frontend\n' "${ENTRY_DOC_FRONTEND:-frontend/AGENTS.md}" >> "$out"
  fi
  if grep -Eq '^dolphin/' "$changed_file_list"; then
    printf '%s|dolphin\n' "${ENTRY_DOC_DOLPHIN:-dolphin/AGENTS.md}" >> "$out"
  fi
  if grep -Eq '^contracts/' "$changed_file_list"; then
    printf '%s|contracts\n' "${ENTRY_DOC_CONTRACTS:-contracts/README.md}" >> "$out"
  fi
}

doc_exists() {
  local doc="$1"
  if [[ "$target_ref_explicit" -eq 1 ]]; then
    git cat-file -e "$TARGET_REF:$doc" 2>/dev/null || [[ -f "$doc" ]]
  else
    [[ -f "$doc" ]]
  fi
}

doc_source_note() {
  local doc="$1"
  if [[ "$target_ref_explicit" -eq 1 && ! -f "$doc" ]]; then
    printf 'at %s' "$TARGET_REF"
  else
    printf 'working tree'
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

if [[ ! -s "$selected_cards_file" && -d "$MEMORY_LOCAL_DIR" ]]; then
  find "$MEMORY_LOCAL_DIR" -maxdepth 1 -type f -name '*.md' ! -name 'README.md' | sort | sed -n '1,3p' > "$selected_cards_file"
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
    "$agent_check_script" --profile "$profile_path" --base "$BASE_REF" --target-ref "$TARGET_REF" --print-only > "$suggestions_file" || true
  else
    "$agent_check_script" --profile "$profile_path" --base "$BASE_REF" --print-only > "$suggestions_file" || true
  fi
else
  printf 'Run agent-rails check after it is available.\n' > "$suggestions_file"
fi

mkdir -p "$(dirname "$TASK_PACK_PATH")"

{
  printf '# Agent Task Pack\n\n'
  printf '> Generated by Agent Rails.\n\n'

  printf '## Goal\n\n%s\n\n' "$goal"

  printf '## Current Git State\n\n'
  printf -- '- Project: `%s`\n' "$PROJECT_NAME"
  printf -- '- Branch: `%s`\n' "${branch:-detached}"
  printf -- '- Target ref: `%s`\n' "$TARGET_REF"
  printf -- '- HEAD: `%s`\n' "$head_sha"
  printf -- '- Base ref: `%s`\n' "$BASE_REF"
  printf -- '- Merge base: `%s`\n\n' "${merge_base:0:12}"

  printf '## Changed Files\n\n'
  if [[ -s "$changed_file_list" ]]; then
    sed 's/^/- `/' "$changed_file_list" | sed 's/$/`/'
  else
    printf -- '- None detected.\n'
  fi
  printf '\n'

  printf '## Working Tree Status\n\n'
  if [[ -s "$status_file" ]]; then
    printf '```text\n'
    cat "$status_file"
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
      printf -- '- `%s` not found for %s changes.\n' "$doc" "$label"
      gaps=$((gaps + 1))
    fi
  done < "$entry_docs_file"
  [[ "$gaps" -eq 0 ]] && printf -- '- None detected.\n'
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
    cat "$selected_online_cards_file"
    printf '\n\n'
  fi
  if [[ -s "$selected_cards_file" ]]; then
    printf '### Local\n\n'
    sed 's/^/- `/' "$selected_cards_file" | sed 's/$/`/'
  else
    printf -- '- No local cards selected.\n'
  fi
  printf '\n'

  printf '## Verification Suggestions\n\n'
  printf '```text\n'
  cat "$suggestions_file"
  printf '```\n\n'

  printf '## Delivery Checklist\n\n'
  printf -- '- What changed\n'
  printf -- '- What was verified\n'
  printf -- '- What was not verified\n'
  printf -- '- Residual risks\n'
  printf -- '- Next action suggestions: fix / do not fix / later\n'
} > "$TASK_PACK_PATH"

printf 'Wrote %s\n' "$TASK_PACK_PATH"
