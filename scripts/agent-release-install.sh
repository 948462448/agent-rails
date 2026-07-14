#!/usr/bin/env bash
# Install an Agent Rails GitHub Release without requiring a source checkout.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: install.sh [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--dry-run]

Downloads a versioned Agent Rails release archive, verifies its SHA-256 digest,
installs it under a versioned directory, and atomically switches the `current`
and CLI symlinks. VERSION defaults to the latest published release.

Environment overrides:
  AGENT_RAILS_RELEASE_REPOSITORY  GitHub OWNER/REPO
  AGENT_RAILS_RELEASE_BASE_URL    Release download base (mainly for mirrors/tests)
  AGENT_RAILS_INSTALL_ROOT        Versioned installation root
  AGENT_RAILS_BIN_DIR             Directory for the agent-rails CLI symlink
USAGE
}

repository="${AGENT_RAILS_RELEASE_REPOSITORY:-948462448/agent-rails}"
requested_version="latest"
install_root="${AGENT_RAILS_INSTALL_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/agent-rails}"
bin_dir="${AGENT_RAILS_BIN_DIR:-$HOME/.local/bin}"
dry_run=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      requested_version="${2#v}"
      shift 2
      ;;
    --repository)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      repository="$2"
      shift 2
      ;;
    --install-root)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      install_root="$2"
      shift 2
      ;;
    --bin-dir)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      bin_dir="$2"
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
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! "$repository" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  printf 'Invalid GitHub repository: %s\n' "$repository" >&2
  exit 2
fi
if [[ "$requested_version" != "latest" && ! "$requested_version" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]]; then
  printf 'Invalid release version: %s\n' "$requested_version" >&2
  exit 2
fi
if [[ "$install_root" != /* ]]; then
  install_root="$PWD/$install_root"
fi
if [[ "$bin_dir" != /* ]]; then
  bin_dir="$PWD/$bin_dir"
fi
if [[ "$install_root" == *$'\n'* || "$bin_dir" == *$'\n'* ]]; then
  printf 'Install paths must not contain newlines.\n' >&2
  exit 2
fi
[[ "$install_root" == "/" ]] || install_root="${install_root%/}"
[[ "$bin_dir" == "/" ]] || bin_dir="${bin_dir%/}"

release_base_url="${AGENT_RAILS_RELEASE_BASE_URL:-https://github.com/$repository}"
release_base_url="${release_base_url%/}"
if [[ "$requested_version" == "latest" ]]; then
  asset_base_url="$release_base_url/releases/latest/download"
else
  asset_base_url="$release_base_url/releases/download/v$requested_version"
fi

archive_name="agent-rails.tar.gz"
checksum_name="$archive_name.sha256"
archive_url="$asset_base_url/$archive_name"
checksum_url="$asset_base_url/$checksum_name"
current_link="$install_root/current"
cli_link="$bin_dir/agent-rails"

if [[ "$dry_run" -eq 1 ]]; then
  printf 'Agent Rails Release Install\n'
  printf 'Repository: %s\n' "$repository"
  printf 'Version: %s\n' "$requested_version"
  printf 'Would download: %s\n' "$archive_url"
  printf 'Would verify: %s\n' "$checksum_url"
  printf 'Would install under: %s/releases\n' "$install_root"
  printf 'Would link: %s\n' "$cli_link"
  exit 0
fi

for command_name in curl tar awk mktemp; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    printf 'Required command not found: %s\n' "$command_name" >&2
    exit 1
  fi
done
if ! command -v sha256sum >/dev/null 2>&1 && ! command -v shasum >/dev/null 2>&1; then
  printf 'Required SHA-256 tool not found: sha256sum or shasum\n' >&2
  exit 1
fi

tmp_dir=""
current_tmp=""
cli_tmp=""
release_stage=""
repository_metadata_tmp=""
bin_dir_metadata_tmp=""
cleanup() {
  [[ -z "$current_tmp" ]] || rm -f "$current_tmp"
  [[ -z "$cli_tmp" ]] || rm -f "$cli_tmp"
  [[ -z "$release_stage" ]] || rm -rf "$release_stage"
  [[ -z "$repository_metadata_tmp" ]] || rm -f "$repository_metadata_tmp"
  [[ -z "$bin_dir_metadata_tmp" ]] || rm -f "$bin_dir_metadata_tmp"
  [[ -z "$tmp_dir" ]] || rm -rf "$tmp_dir"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

replace_symlink() {
  local source_link="$1"
  local destination_link="$2"
  if mv -Tf "$source_link" "$destination_link" 2>/dev/null; then
    return 0
  fi
  if mv -fh "$source_link" "$destination_link" 2>/dev/null; then
    return 0
  fi
  printf 'Unable to atomically replace symlink: %s\n' "$destination_link" >&2
  return 1
}

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/agent-rails-install.XXXXXX")"
archive_path="$tmp_dir/$archive_name"
checksum_path="$tmp_dir/$checksum_name"
listing_path="$tmp_dir/archive.list"

curl_args=(-fsSL)
if [[ "$release_base_url" == https://* ]]; then
  curl_args+=(--proto '=https' --tlsv1.2)
fi

printf 'Download Agent Rails release\n'
curl "${curl_args[@]}" "$archive_url" -o "$archive_path"
curl "${curl_args[@]}" "$checksum_url" -o "$checksum_path"

expected_checksum="$(awk -v name="$archive_name" '$2 == name || $2 == "*" name { print $1; exit }' "$checksum_path")"
if [[ ! "$expected_checksum" =~ ^[0-9A-Fa-f]{64}$ ]]; then
  printf 'Invalid checksum file for %s\n' "$archive_name" >&2
  exit 1
fi
if command -v sha256sum >/dev/null 2>&1; then
  actual_checksum="$(sha256sum "$archive_path" | awk '{print $1}')"
else
  actual_checksum="$(shasum -a 256 "$archive_path" | awk '{print $1}')"
fi
if [[ "$actual_checksum" != "$expected_checksum" ]]; then
  printf 'Checksum mismatch for %s\n' "$archive_name" >&2
  exit 1
fi

tar -tzf "$archive_path" > "$listing_path"
top_dir=""
while IFS= read -r entry; do
  entry="${entry%/}"
  [[ -n "$entry" ]] || continue
  case "$entry" in
    /*|../*|*/../*|*/..)
      printf 'Unsafe release archive path: %s\n' "$entry" >&2
      exit 1
      ;;
  esac
  entry_top="${entry%%/*}"
  if [[ -z "$top_dir" ]]; then
    top_dir="$entry_top"
  elif [[ "$entry_top" != "$top_dir" ]]; then
    printf 'Release archive must contain one top-level directory.\n' >&2
    exit 1
  fi
done < "$listing_path"
if [[ ! "$top_dir" =~ ^agent-rails-[0-9A-Za-z][0-9A-Za-z.+-]*$ ]]; then
  printf 'Unexpected release archive root: %s\n' "${top_dir:-<empty>}" >&2
  exit 1
fi

tar -xzf "$archive_path" -C "$tmp_dir"
package_dir="$tmp_dir/$top_dir"
if [[ ! -x "$package_dir/bin/agent-rails" || ! -f "$package_dir/VERSION" ]]; then
  printf 'Release archive is missing the Agent Rails CLI or VERSION.\n' >&2
  exit 1
fi
package_version="$(awk 'NF { print $1; exit }' "$package_dir/VERSION")"
if [[ ! "$package_version" =~ ^[0-9A-Za-z][0-9A-Za-z.+-]*$ ]]; then
  printf 'Invalid VERSION in release archive: %s\n' "$package_version" >&2
  exit 1
fi
if [[ "$requested_version" != "latest" && "$package_version" != "$requested_version" ]]; then
  printf 'Release version mismatch: requested %s, archive contains %s\n' "$requested_version" "$package_version" >&2
  exit 1
fi

release_dir="$install_root/releases/$package_version"
repository_metadata="$install_root/release-repository"
bin_dir_metadata="$install_root/release-bin-dir"
if [[ (-e "$current_link" || -L "$current_link") && ! -L "$current_link" ]]; then
  printf 'Refusing to replace non-symlink current path: %s\n' "$current_link" >&2
  exit 1
fi
if [[ (-e "$cli_link" || -L "$cli_link") && ! -L "$cli_link" ]]; then
  printf 'Refusing to replace non-symlink CLI path: %s\n' "$cli_link" >&2
  exit 1
fi

mkdir -p "$install_root/releases" "$bin_dir"
already_installed=0
if [[ -d "$release_dir" ]]; then
  installed_version="$(awk 'NF { print $1; exit }' "$release_dir/VERSION" 2>/dev/null || true)"
  if [[ "$installed_version" != "$package_version" || ! -x "$release_dir/bin/agent-rails" ]]; then
    printf 'Existing release directory is invalid: %s\n' "$release_dir" >&2
    exit 1
  fi
  already_installed=1
else
  release_stage="$install_root/releases/.agent-rails-$package_version.$$"
  mv "$package_dir" "$release_stage"
  mv "$release_stage" "$release_dir"
  release_stage=""
fi

repository_metadata_tmp="$install_root/.release-repository.$$"
bin_dir_metadata_tmp="$install_root/.release-bin-dir.$$"
printf '%s\n' "$repository" > "$repository_metadata_tmp"
printf '%s\n' "$bin_dir" > "$bin_dir_metadata_tmp"
chmod 600 "$repository_metadata_tmp" "$bin_dir_metadata_tmp"
mv -f "$repository_metadata_tmp" "$repository_metadata"
mv -f "$bin_dir_metadata_tmp" "$bin_dir_metadata"

current_tmp="$install_root/.current.$$"
ln -s "releases/$package_version" "$current_tmp"
replace_symlink "$current_tmp" "$current_link"
current_tmp=""

cli_tmp="$bin_dir/.agent-rails.$$"
ln -s "$current_link/bin/agent-rails" "$cli_tmp"
replace_symlink "$cli_tmp" "$cli_link"
cli_tmp=""

if [[ "$already_installed" -eq 1 ]]; then
  printf 'Agent Rails %s is already installed.\n' "$package_version"
else
  printf 'Installed Agent Rails %s\n' "$package_version"
fi
printf 'Home: %s\n' "$current_link"
printf 'Command: %s\n' "$cli_link"
case ":$PATH:" in
  *":$bin_dir:"*) ;;
  *) printf 'Add %s to PATH to run agent-rails directly.\n' "$bin_dir" ;;
esac
