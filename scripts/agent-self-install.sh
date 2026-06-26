#!/usr/bin/env bash
# Install or update Agent Rails from a tarball, URL, or local directory.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails self install|update [--install-dir PATH] [--bin-dir PATH] [--source URL|PATH] [--ref REF] [--dry-run] [--force]

Installs or updates a user-mode Agent Rails kit without requiring a manual git clone.

Defaults:
  install dir: ~/.agent-rails/kit for install, current AGENT_RAILS_HOME for update
  bin dir:     ~/.agent-rails/bin
  source:      https://github.com/948462448/agent-rails/archive/refs/heads/main.tar.gz
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

action="${1:-}"
case "$action" in
  install|update)
    shift
    ;;
  ""|--help|-h)
    usage
    exit 0
    ;;
  *)
    usage >&2
    exit 2
    ;;
esac

install_dir=""
bin_dir="${AGENT_RAILS_BIN_DIR:-$AGENT_RAILS_CONFIG_HOME/bin}"
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

if [[ -z "$install_dir" ]]; then
  if [[ "$action" == "install" ]]; then
    install_dir="$AGENT_RAILS_CONFIG_HOME/kit"
  else
    install_dir="$AGENT_RAILS_HOME"
  fi
fi

if [[ -z "$source_arg" ]]; then
  source_arg="https://github.com/948462448/agent-rails/archive/refs/heads/$source_ref.tar.gz"
fi

absolute_path() {
  local path="$1"
  local parent
  case "$path" in
    /*)
      printf '%s\n' "$path"
      ;;
    *)
      parent="$(dirname "$path")"
      printf '%s/%s\n' "$(cd "$parent" && pwd)" "$(basename "$path")"
      ;;
  esac
}

safe_install_dir() {
  local path="$1"
  if [[ -z "$path" || "$path" == "/" || "$path" == "$HOME" ]]; then
    printf 'Refusing unsafe install dir: %s\n' "$path" >&2
    exit 2
  fi
}

print_command() {
  local first=1 arg
  for arg in "$@"; do
    if [[ "$first" -eq 1 ]]; then
      first=0
    else
      printf ' '
    fi
    printf '%q' "$arg"
  done
  printf '\n'
}

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

copy_source_to() {
  local source_dir="$1"
  local dest_dir="$2"
  mkdir -p "$dest_dir"
  tar -C "$source_dir" --exclude './.git' --exclude '.git' -cf - . | tar -C "$dest_dir" -xf -
}

prepare_source_dir() {
  local source="$1"
  local scratch="$2"
  local archive_path

  case "$source" in
    http://*|https://*)
      need_command curl
      archive_path="$scratch/agent-rails.tar.gz"
      if [[ "$dry_run" -eq 1 ]]; then
        printf '%s\n' "$source"
        return 0
      fi
      curl -fsSL "$source" -o "$archive_path"
      extract_archive "$archive_path" "$scratch/extract"
      ;;
    *)
      if [[ -d "$source" ]]; then
        absolute_path "$source"
      elif [[ -f "$source" ]]; then
        archive_path="$(absolute_path "$source")"
        extract_archive "$archive_path" "$scratch/extract"
      else
        printf 'Install source not found: %s\n' "$source" >&2
        exit 2
      fi
      ;;
  esac
}

validate_source_dir() {
  local source_dir="$1"
  [[ -f "$source_dir/bin/agent-rails" ]] || { printf 'Source is missing bin/agent-rails: %s\n' "$source_dir" >&2; exit 2; }
  [[ -f "$source_dir/scripts/agent-paths.sh" ]] || { printf 'Source is missing scripts/agent-paths.sh: %s\n' "$source_dir" >&2; exit 2; }
  [[ -f "$source_dir/VERSION" ]] || { printf 'Source is missing VERSION: %s\n' "$source_dir" >&2; exit 2; }
}

install_from_source_dir() {
  local source_dir="$1"
  local install_abs="$2"
  local bin_abs="$3"
  local staging_dir
  local backup_dir

  staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-install.XXXXXX")"
  backup_dir="$install_abs.backup.$$"
  copy_source_to "$source_dir" "$staging_dir/kit"

  if [[ -e "$install_abs" ]]; then
    if [[ "$force" -ne 1 && "$action" == "install" ]]; then
      printf 'Install dir already exists: %s (pass --force to replace)\n' "$install_abs" >&2
      rm -rf "$staging_dir"
      exit 1
    fi
    mv "$install_abs" "$backup_dir"
  fi
  mkdir -p "$(dirname "$install_abs")"
  mv "$staging_dir/kit" "$install_abs"
  rm -rf "$staging_dir"
  rm -rf "$backup_dir"

  mkdir -p "$bin_abs"
  ln -sfn "$install_abs/bin/agent-rails" "$bin_abs/agent-rails"
}

install_abs="$(absolute_path "$install_dir")"
bin_abs="$(absolute_path "$bin_dir")"
safe_install_dir "$install_abs"

printf 'Agent Rails Self %s\n' "$(printf '%s' "$action" | tr '[:lower:]' '[:upper:]')"
printf 'Install dir: %s\n' "$install_abs"
printf 'Bin dir: %s\n' "$bin_abs"
printf 'Source: %s\n' "$source_arg"

if [[ "$action" == "update" && ! -d "$install_abs" ]]; then
  printf 'Install dir not found for update: %s\n' "$install_abs" >&2
  printf 'Run `agent-rails self install --install-dir "%s"` first.\n' "$install_abs" >&2
  exit 2
fi

if [[ "$dry_run" -eq 1 ]]; then
  printf '\nWould fetch/extract source and replace %s\n' "$install_abs"
  printf 'Would link CLI: '
  print_command ln -sfn "$install_abs/bin/agent-rails" "$bin_abs/agent-rails"
  exit 0
fi

need_command tar
scratch_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-source.XXXXXX")"
trap 'rm -rf "$scratch_dir"' EXIT
source_dir="$(prepare_source_dir "$source_arg" "$scratch_dir")"
validate_source_dir "$source_dir"
install_from_source_dir "$source_dir" "$install_abs" "$bin_abs"

if [[ "$action" == "update" ]]; then
  result_verb="Updated"
else
  result_verb="Installed"
fi
printf '\n%s Agent Rails %s to %s\n' "$result_verb" "$("$install_abs/bin/agent-rails" --version | awk '{print $2}')" "$install_abs"
printf 'CLI: %s/agent-rails\n' "$bin_abs"
printf 'Add this to PATH if needed:\n'
printf '  export PATH="%s:$PATH"\n' "$bin_abs"
