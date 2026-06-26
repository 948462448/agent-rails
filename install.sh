#!/usr/bin/env bash
# Bootstrap Agent Rails user-mode install without requiring a manual git clone.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh [--install-dir PATH] [--bin-dir PATH] [--source URL|PATH] [--ref REF] [--dry-run] [--force]

Environment overrides:
  AGENT_RAILS_INSTALL_DIR
  AGENT_RAILS_BIN_DIR
  AGENT_RAILS_INSTALL_SOURCE
  AGENT_RAILS_INSTALL_REF
USAGE
}

install_dir="${AGENT_RAILS_INSTALL_DIR:-$HOME/.agent-rails/kit}"
bin_dir="${AGENT_RAILS_BIN_DIR:-$HOME/.agent-rails/bin}"
source_arg="${AGENT_RAILS_INSTALL_SOURCE:-}"
source_ref="${AGENT_RAILS_INSTALL_REF:-main}"
dry_run=0
force=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --install-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      install_dir="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      bin_dir="$2"
      shift 2
      ;;
    --source)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      source_arg="$2"
      shift 2
      ;;
    --ref)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      source_ref="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
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

if [[ -z "$source_arg" ]]; then
  source_arg="https://github.com/948462448/agent-rails/archive/refs/heads/$source_ref.tar.gz"
fi

need_command() {
  local name="$1"
  if ! command -v "$name" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$name" >&2
    exit 2
  fi
}

extract_archive() {
  local archive_path="$1"
  local extract_dir="$2"
  local root

  mkdir -p "$extract_dir"
  tar -xzf "$archive_path" -C "$extract_dir"
  if [[ -f "$extract_dir/bin/agent-rails" ]]; then
    printf '%s\n' "$extract_dir"
    return 0
  fi
  root="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | sort | sed -n '1p')"
  if [[ -z "$root" ]]; then
    root="$extract_dir"
  fi
  printf '%s\n' "$root"
}

scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-bootstrap.XXXXXX")"
trap 'rm -rf "$scratch_dir"' EXIT

printf 'Agent Rails Bootstrap Install\n'
printf 'Install dir: %s\n' "$install_dir"
printf 'Bin dir: %s\n' "$bin_dir"
printf 'Source: %s\n' "$source_arg"

if [[ "$dry_run" -eq 1 ]]; then
  printf '\nWould download/extract source and run agent-rails self install.\n'
  exit 0
fi

need_command tar
case "$source_arg" in
  http://*|https://*)
    need_command curl
    archive_path="$scratch_dir/agent-rails.tar.gz"
    curl -fsSL "$source_arg" -o "$archive_path"
    source_dir="$(extract_archive "$archive_path" "$scratch_dir/extract")"
    ;;
  *)
    if [[ -d "$source_arg" ]]; then
      source_dir="$source_arg"
      archive_path="$source_arg"
    elif [[ -f "$source_arg" ]]; then
      archive_path="$source_arg"
      source_dir="$(extract_archive "$archive_path" "$scratch_dir/extract")"
    else
      printf 'Install source not found: %s\n' "$source_arg" >&2
      exit 2
    fi
    ;;
esac

[[ -x "$source_dir/bin/agent-rails" ]] || { printf 'Source is missing executable bin/agent-rails: %s\n' "$source_dir" >&2; exit 2; }

args=(self install --install-dir "$install_dir" --bin-dir "$bin_dir" --source "$archive_path")
[[ "$force" -eq 1 ]] && args+=(--force)
"$source_dir/bin/agent-rails" "${args[@]}"
