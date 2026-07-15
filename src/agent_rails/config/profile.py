from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Mapping, Optional, Sequence


class ProfileLoadError(RuntimeError):
    pass


@dataclass(frozen=True)
class LoadedProfile:
    path: Path
    values: Mapping[str, str]


def load_shell_profile(
    path: Path,
    *,
    environment: Mapping[str, str],
    variables: Sequence[str],
    env_file_variable: Optional[str] = None,
) -> LoadedProfile:
    """Load a compatibility Shell Profile and return only explicit safe fields.

    Profiles remain executable Shell during migration. The subprocess isolates
    their process-local mutations, while the allowlist prevents unrelated
    environment values or credentials from crossing the Python seam.
    """
    if not path.is_file():
        raise FileNotFoundError(str(path))

    script = r'''
set -euo pipefail
profile_path="$1"
env_file_variable="$2"
shift 2
exec 3>&1
source "$profile_path" >&2
if [[ -n "$env_file_variable" ]]; then
  env_file_path="${!env_file_variable:-}"
  if [[ -n "$env_file_path" && -f "$env_file_path" ]]; then
    source "$env_file_path" >&2
  fi
fi
for variable_name in "$@"; do
  if declare -p "$variable_name" >/dev/null 2>&1; then
    printf '%s\0%s\0' "$variable_name" "${!variable_name}" >&3
  fi
done
'''
    command = [
        "/bin/bash",
        "-c",
        script,
        "agent-rails-profile",
        str(path),
        env_file_variable or "",
        *variables,
    ]
    completed = subprocess.run(
        command,
        env=dict(environment),
        stdout=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        raise ProfileLoadError(f"Profile could not be sourced: {path}")

    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ProfileLoadError(f"Profile returned an invalid configuration payload: {path}")

    values = {
        fields[index].decode("utf-8"): fields[index + 1].decode("utf-8")
        for index in range(0, len(fields), 2)
    }
    return LoadedProfile(path=path, values=values)
