#!/usr/bin/env bash
# Cold-start bootstrap for the Python Release Install Application.
set -euo pipefail
umask 077

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/release_install.py" ]]; then
  installer="$script_dir/release_install.py"
else
  installer="$script_dir/../src/agent_rails/release/install.py"
fi
if [[ ! -f "$installer" ]]; then
  printf 'Release installer Python entrypoint not found.\n' >&2
  exit 2
fi
exec python3 -I "$installer" "$@"
