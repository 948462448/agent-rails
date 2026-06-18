#!/usr/bin/env bash
# Install Agent Rails skill blueprints into a local skill directory.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails skills install --dest PATH [--dry-run] [skill-name...]

Examples:
  agent-rails skills install --dest "$HOME/.codex/skills" --dry-run
  agent-rails skills install --dest "$HOME/.codex/skills" agent-context-pack agent-check

The source of truth stays under $AGENT_RAILS_HOME/skills/.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"

dest=""
dry_run=0
skills=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dest)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      dest="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      skills+=("$1")
      shift
      ;;
  esac
done

if [[ -z "$dest" ]]; then
  usage >&2
  exit 2
fi

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

source_dir="$AGENT_RAILS_HOME/skills"
if [[ ! -d "$source_dir" ]]; then
  printf 'Missing source dir: %s\n' "$source_dir" >&2
  exit 1
fi

if [[ "${#skills[@]}" -eq 0 ]]; then
  while IFS= read -r skill_dir; do
    skills+=("$(basename "$skill_dir")")
  done < <(find "$source_dir" -mindepth 1 -maxdepth 1 -type d | sort)
fi

for skill in "${skills[@]}"; do
  src="$source_dir/$skill"
  if [[ ! -f "$src/SKILL.md" ]]; then
    printf 'Skipping %s: missing %s/SKILL.md\n' "$skill" "$src" >&2
    continue
  fi

  target="$dest/$skill"
  if [[ "$dry_run" -eq 1 ]]; then
    printf 'Would install %s -> %s\n' "$src" "$target"
  else
    mkdir -p "$target"
    cp -R "$src/." "$target/"
    printf 'Installed %s -> %s\n' "$src" "$target"
  fi
done
