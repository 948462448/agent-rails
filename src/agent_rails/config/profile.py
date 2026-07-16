from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Mapping, Optional, Sequence


class ProfileLoadError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        stage: str = "profile",
        path: Optional[Path] = None,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.path = path


@dataclass(frozen=True)
class LoadedProfile:
    path: Path
    values: Mapping[str, str]
    exported_environment: Mapping[str, str]


def load_shell_profile(
    path: Path,
    *,
    environment: Mapping[str, str],
    variables: Sequence[str],
    env_file_variable: Optional[str] = None,
    working_directory: Optional[Path] = None,
    capture_exported_environment: bool = False,
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
capture_exported_environment="$3"
shift 3
exec 3>&1
failure_stage="profile"
failure_path="$profile_path"
trap '
  status=$?
  if [[ "$status" -ne 0 ]]; then
    printf "%s\0%s\0%s\0" "E" "$failure_stage" "$failure_path" >&3
  fi
' EXIT
source "$profile_path" >&2
if [[ -n "$env_file_variable" ]]; then
  env_file_path="${!env_file_variable:-}"
  if [[ -n "$env_file_path" && -f "$env_file_path" ]]; then
    failure_stage="environment"
    failure_path="$env_file_path"
    source "$env_file_path" >&2
  fi
fi
trap - EXIT
for variable_name in "$@"; do
  if declare -p "$variable_name" >/dev/null 2>&1; then
    printf 'V\0%s\0%s\0' "$variable_name" "${!variable_name}" >&3
  fi
done
if [[ "$capture_exported_environment" == "1" ]]; then
  printf 'X\0' >&3
  command -p env -0 >&3
  # `command -p` supplies a safe executable search path to `env`, and Bash
  # exposes that temporary PATH to the child. Append the caller's real PATH so
  # the captured environment preserves tool detection and child execution.
  if [[ "${PATH+x}" == "x" ]]; then
    printf 'PATH=%s\0' "$PATH" >&3
  fi
fi
'''
    command = [
        "/bin/bash",
        "-c",
        script,
        "agent-rails-profile",
        str(path),
        env_file_variable or "",
        "1" if capture_exported_environment else "0",
        *variables,
    ]
    completed = subprocess.run(
        command,
        env=dict(environment),
        cwd=working_directory,
        stdout=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        fields = completed.stdout.split(b"\0")
        if fields and fields[-1] == b"":
            fields.pop()
        if len(fields) == 3 and fields[0] == b"E":
            try:
                stage = fields[1].decode("ascii")
                failed_path = Path(fields[2].decode("utf-8", "surrogateescape"))
            except UnicodeError:
                stage = "profile"
                failed_path = path
            if stage == "environment":
                raise ProfileLoadError(
                    f"Env file could not be sourced: {failed_path}",
                    stage=stage,
                    path=failed_path,
                )
        raise ProfileLoadError(
            f"Profile could not be sourced: {path}",
            stage="profile",
            path=path,
        )

    fields = completed.stdout.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    values = {}
    exported_environment = {}
    index = 0
    try:
        while index < len(fields):
            kind = fields[index].decode("ascii")
            if kind == "X":
                index += 1
                while index < len(fields):
                    name, separator, value = fields[index].partition(b"=")
                    if not separator or not name:
                        raise ValueError("invalid exported environment record")
                    exported_environment[name.decode("utf-8", "surrogateescape")] = (
                        value.decode("utf-8", "surrogateescape")
                    )
                    index += 1
                break
            name = fields[index + 1].decode("utf-8")
            value = fields[index + 2].decode("utf-8")
            index += 3
            if kind == "V":
                values[name] = value
            else:
                raise ValueError("unknown profile payload record")
    except (IndexError, UnicodeError, ValueError) as exc:
        raise ProfileLoadError(
            f"Profile returned an invalid configuration payload: {path}"
        ) from exc
    return LoadedProfile(
        path=path,
        values=values,
        exported_environment=exported_environment,
    )
