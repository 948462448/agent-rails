#!/usr/bin/env bash
# Record a model-curated memory decision and optionally write a local memory card.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails memory suggest [--project PATH] [--profile PATH] [--output PATH]
                                [--decision keep|skip|update|merge]
                                [--write-local] [--force]
                                [--id ID] [--title TITLE]
                                [--trigger TEXT] [--applies-to TEXT]
                                [--verify TEXT] [--caution TEXT]
                                [--reason TEXT]
                                [--staleness stable|verify-first]
                                [notes...]

Examples:
  agent-rails memory suggest --project /path/to/project --decision skip --reason "one-off output"
  agent-rails memory suggest --project /path/to/project --write-local --title "Backend runs on Pandora Boot" "Pandora Boot may serve stale BOOT-INF jars after backend edits."

The model decides whether the lesson is valuable. This helper records that
decision. It writes local memory only with --write-local; it never writes
OpenMemory.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

profile_path_arg=""
profile_path=""
project="$PWD"
output_path=""
title=""
memory_id=""
decision="keep"
reason=""
staleness="verify-first"
write_local=0
force=0
verify=""
caution=""
note_parts=()
triggers=()
applies_to=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project="$2"
      shift 2
      ;;
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path_arg="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      output_path="$2"
      shift 2
      ;;
    --title)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      title="$2"
      shift 2
      ;;
    --id)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      memory_id="$2"
      shift 2
      ;;
    --decision)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        keep|skip|update|merge)
          decision="$2"
          ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --reason)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      reason="$2"
      shift 2
      ;;
    --trigger)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      triggers+=("$2")
      shift 2
      ;;
    --applies-to)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      applies_to+=("$2")
      shift 2
      ;;
    --verify)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      verify="$2"
      shift 2
      ;;
    --caution)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      caution="$2"
      shift 2
      ;;
    --write-local)
      write_local=1
      shift
      ;;
    --force)
      force=1
      shift
      ;;
    --staleness)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      case "$2" in
        stable|verify-first)
          staleness="$2"
          ;;
        *)
          usage >&2
          exit 2
          ;;
      esac
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      note_parts+=("$1")
      shift
      ;;
  esac
done

if [[ ! -d "$project" ]]; then
  printf 'Project directory not found: %s\n' "$project" >&2
  exit 2
fi

project_abs="$(cd "$project" && pwd)"
if repo_root="$(git -C "$project_abs" rev-parse --show-toplevel 2>/dev/null)"; then
  is_git_repo=1
else
  is_git_repo=0
  repo_root="$project_abs"
fi

profile_path="$(agent_rails_resolve_profile "$repo_root" "$(basename "$repo_root")" "$profile_path_arg")"
if [[ ! -f "$profile_path" ]]; then
  printf 'Profile not found: %s\n' "$profile_path" >&2
  exit 2
fi

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

PROJECT_NAME="${PROJECT_NAME:-$(basename "$repo_root")}"
MEMORY_LOCAL_DIR="${MEMORY_LOCAL_DIR:-$(agent_rails_default_memory_dir "$PROJECT_NAME")}"
notes="${note_parts[*]-}"
[[ -n "$title" ]] || title="Untitled memory decision"
if [[ -z "$output_path" ]]; then
  output_path="$(agent_rails_default_memory_decision_path "$PROJECT_NAME")"
fi

changed_files=""
status_text="No git repository detected; git state is unavailable."
if [[ "$is_git_repo" -eq 1 ]]; then
  changed_files="$(git -C "$repo_root" status --porcelain=v1 -uall | awk '{print $NF}' | sort -u)"
  status_text="$(git -C "$repo_root" status --porcelain=v1 -uall)"
fi

mkdir -p "$(dirname "$output_path")"

slugify() {
  local raw="$1"
  printf '%s' "$raw" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/^-+//; s/-+$//; s/-+/-/g' \
    | cut -c1-80
}

yaml_escape() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

append_unique() {
  local array_name="$1"
  local value="$2"
  local existing
  [[ -n "$value" ]] || return 0
  case "$array_name" in
    triggers)
      for existing in "${triggers[@]-}"; do
        [[ "$existing" == "$value" ]] && return 0
      done
      triggers+=("$value")
      ;;
    applies_to)
      for existing in "${applies_to[@]-}"; do
        [[ "$existing" == "$value" ]] && return 0
      done
      applies_to+=("$value")
      ;;
    *)
      printf 'Unknown array for append_unique: %s\n' "$array_name" >&2
      exit 2
      ;;
  esac
}

if [[ -z "$memory_id" ]]; then
  if [[ "$title" != "Untitled memory decision" ]]; then
    memory_id="$(slugify "$title")"
  else
    memory_id="$(slugify "$PROJECT_NAME-memory-$(date +%Y%m%d%H%M%S)")"
  fi
fi
[[ -n "$memory_id" ]] || memory_id="memory-$(date +%Y%m%d%H%M%S)"

if [[ "${#triggers[@]}" -eq 0 && "$title" != "Untitled memory decision" ]]; then
  IFS='-' read -r -a title_words <<< "$(slugify "$title")"
  for word in "${title_words[@]}"; do
    [[ "${#word}" -ge 3 ]] && append_unique triggers "$word"
    [[ "${#triggers[@]}" -ge 6 ]] && break
  done
fi
if [[ "${#triggers[@]}" -eq 0 ]]; then
  triggers+=("$PROJECT_NAME")
fi

if [[ "${#applies_to[@]}" -eq 0 && -n "$changed_files" ]]; then
  while IFS= read -r file; do
    [[ -n "$file" ]] || continue
    case "$file" in
      */*) append_unique applies_to "${file%%/*}" ;;
      *) append_unique applies_to "$file" ;;
    esac
    [[ "${#applies_to[@]}" -ge 6 ]] && break
  done <<< "$changed_files"
fi
if [[ "${#applies_to[@]}" -eq 0 ]]; then
  applies_to+=("$PROJECT_NAME")
fi

write_markdown_list() {
  local array_name="$1"
  local item
  case "$array_name" in
    triggers)
      for item in "${triggers[@]-}"; do
        printf '  - "%s"\n' "$(yaml_escape "$item")"
      done
      ;;
    applies_to)
      for item in "${applies_to[@]-}"; do
        printf '  - "%s"\n' "$(yaml_escape "$item")"
      done
      ;;
    *)
      printf 'Unknown array for write_markdown_list: %s\n' "$array_name" >&2
      exit 2
      ;;
  esac
}

if [[ "$write_local" -eq 1 ]]; then
  if [[ "$decision" == "skip" ]]; then
    printf 'Refusing --write-local with --decision skip.\n' >&2
    exit 2
  fi
  if [[ -z "$notes" ]]; then
    printf 'Refusing --write-local without curated memory notes.\n' >&2
    exit 2
  fi
fi

local_memory_path="$MEMORY_LOCAL_DIR/$memory_id.md"
if [[ "$write_local" -eq 1 && -e "$local_memory_path" && "$force" -ne 1 ]]; then
  printf 'Local memory already exists: %s\nUse --force to replace it, or choose --id.\n' "$local_memory_path" >&2
  exit 2
fi

{
  printf '# Agent Rails Memory Decision\n\n'
  printf '> Model-curated decision log. Local memory is written only when `--write-local` is used. OpenMemory is never written by this helper.\n\n'

  printf '## Source Context\n\n'
  printf -- '- Project: `%s`\n' "$PROJECT_NAME"
  printf -- '- Project path: `%s`\n' "$project_abs"
  printf -- '- Profile: `%s`\n' "$profile_path"
  printf -- '- Local memory dir: `%s`\n\n' "$MEMORY_LOCAL_DIR"

  printf '## Decision\n\n'
  printf -- '- Decision: `%s`\n' "$decision"
  printf -- '- Reason: %s\n' "${reason:-Not provided.}"
  printf -- '- Local write requested: `%s`\n' "$([[ "$write_local" -eq 1 ]] && printf yes || printf no)"
  if [[ "$write_local" -eq 1 ]]; then
    printf -- '- Local memory path: `%s`\n' "$local_memory_path"
  fi
  printf '\n'

  printf '## Changed Files At Suggest Time\n\n'
  if [[ -n "$changed_files" ]]; then
    while IFS= read -r file; do
      [[ -n "$file" ]] && printf -- '- `%s`\n' "$file"
    done <<< "$changed_files"
  else
    printf -- '- None detected.\n'
  fi
  printf '\n'

  printf '## Working Tree Status\n\n'
  if [[ -n "$status_text" ]]; then
    printf '```text\n%s\n```\n\n' "$status_text"
  else
    printf 'Clean.\n\n'
  fi

  printf '## Candidate\n\n'
  printf '```markdown\n'
  printf -- '---\n'
  printf 'id: "%s"\n' "$(yaml_escape "$memory_id")"
  printf 'title: "%s"\n' "$(yaml_escape "$title")"
  printf -- 'triggers:\n'
  write_markdown_list triggers
  printf -- 'applies_to:\n'
  write_markdown_list applies_to
  printf 'staleness: %s\n' "$staleness"
  printf -- 'source:\n'
  printf '  - "agent-rails memory suggest: project=%s decision=%s"\n' "$(yaml_escape "$PROJECT_NAME")" "$decision"
  printf -- '---\n\n'
  printf '## Rule\n\n'
  if [[ -n "$notes" ]]; then
    printf '%s\n\n' "$notes"
  else
    printf 'TODO: State the reusable project fact or workflow rule in 1-3 sentences.\n\n'
  fi
  printf '## Verify\n\n'
  if [[ -n "$verify" ]]; then
    printf '%s\n\n' "$verify"
  else
    printf 'Re-check the files, commands, or config named in the task before relying on this memory.\n\n'
  fi
  printf '## Caution\n\n'
  if [[ -n "$caution" ]]; then
    printf '%s\n' "$caution"
  else
    printf 'Apply only within the listed scope. Treat environment, branch, and service behavior as verify-first.\n'
  fi
  printf '```\n\n'

  printf '## Curator Checklist\n\n'
  printf -- '- No secrets, cookies, tokens, AccessKeys, or full sensitive responses.\n'
  printf -- '- The lesson is reusable for future tasks, not a one-off transcript summary.\n'
  printf -- '- Existing memory/docs were checked for duplicates or conflicts.\n'
  printf -- '- `Verify` tells the next agent how to confirm the claim.\n'
  printf -- '- OpenMemory was not written by this helper.\n'
} > "$output_path"

printf 'Wrote %s\n' "$output_path"

if [[ "$write_local" -eq 1 ]]; then
  mkdir -p "$MEMORY_LOCAL_DIR"
  {
    printf -- '---\n'
    printf 'id: "%s"\n' "$(yaml_escape "$memory_id")"
    printf 'title: "%s"\n' "$(yaml_escape "$title")"
    printf -- 'triggers:\n'
    write_markdown_list triggers
    printf -- 'applies_to:\n'
    write_markdown_list applies_to
    printf 'staleness: %s\n' "$staleness"
    printf -- 'source:\n'
    printf '  - "agent-rails memory suggest: project=%s decision=%s"\n' "$(yaml_escape "$PROJECT_NAME")" "$decision"
    printf -- '---\n\n'
    printf '## Rule\n\n'
    printf '%s\n\n' "$notes"
    printf '## Verify\n\n'
    if [[ -n "$verify" ]]; then
      printf '%s\n\n' "$verify"
    else
      printf 'Re-check the files, commands, or config named in the task before relying on this memory.\n\n'
    fi
    printf '## Caution\n\n'
    if [[ -n "$caution" ]]; then
      printf '%s\n' "$caution"
    else
      printf 'Apply only within the listed scope. Treat environment, branch, and service behavior as verify-first.\n'
    fi
  } > "$local_memory_path"
  printf 'Wrote local memory %s\n' "$local_memory_path"
fi
