#!/usr/bin/env bash
# Build the assets consumed by GitHub Releases and agent-release-install.sh.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/build-release.sh [--output DIR] [--include-worktree]

Builds:
  agent-rails.tar.gz
  agent-rails.tar.gz.sha256
  install.sh

The default archive contains Git-tracked files. --include-worktree also includes
untracked, non-ignored files and is intended only for local pre-commit testing.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source_root="$(git -C "$script_dir/.." rev-parse --show-toplevel)"
output_dir="$source_root/dist"
include_worktree=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      output_dir="$2"
      shift 2
      ;;
    --include-worktree)
      include_worktree=1
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

version="$(awk 'NF { print $1; exit }' "$source_root/VERSION")"
if [[ ! "$version" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]]; then
  printf 'Invalid Agent Rails VERSION: %s\n' "$version" >&2
  exit 1
fi
if [[ ! -x "$source_root/scripts/agent-release-install.sh" ]]; then
  printf 'Release installer is missing or not executable: %s\n' "$source_root/scripts/agent-release-install.sh" >&2
  exit 1
fi

mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd -P)"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-release.XXXXXX")"
trap 'rm -rf "$tmp_dir"' EXIT

package_root="$tmp_dir/agent-rails-$version"
file_list="$tmp_dir/files.list"
mkdir -p "$package_root"

if [[ "$include_worktree" -eq 1 ]]; then
  git -C "$source_root" ls-files --cached --others --exclude-standard -z > "$file_list"
else
  git -C "$source_root" ls-files -z > "$file_list"
fi

while IFS= read -r -d '' relative_path; do
  [[ -e "$source_root/$relative_path" || -L "$source_root/$relative_path" ]] || {
    printf 'Tracked release path is missing: %s\n' "$relative_path" >&2
    exit 1
  }
  mkdir -p "$package_root/$(dirname "$relative_path")"
  cp -pP "$source_root/$relative_path" "$package_root/$relative_path"
done < "$file_list"

archive_path="$output_dir/agent-rails.tar.gz"
checksum_path="$archive_path.sha256"
installer_path="$output_dir/install.sh"

tar -czf "$archive_path" -C "$tmp_dir" "agent-rails-$version"
cp -p "$source_root/scripts/agent-release-install.sh" "$installer_path"
chmod +x "$installer_path"

if command -v sha256sum >/dev/null 2>&1; then
  checksum="$(sha256sum "$archive_path" | awk '{print $1}')"
else
  checksum="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
fi
printf '%s  %s\n' "$checksum" "$(basename "$archive_path")" > "$checksum_path"

if ! tar -tzf "$archive_path" | grep -Fqx "agent-rails-$version/bin/agent-rails"; then
  printf 'Built archive does not contain the Agent Rails CLI.\n' >&2
  exit 1
fi

printf 'Built Agent Rails %s release assets in %s\n' "$version" "$output_dir"
