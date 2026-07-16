"""Run Git commands without inheriting another repository's local context."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
from typing import Dict, Mapping, Optional, Sequence


_GIT_REPOSITORY_ENVIRONMENT = (
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
)


def isolated_git_environment(
    environment: Optional[Mapping[str, str]] = None,
) -> Dict[str, str]:
    """Copy an environment without another repository's local Git context."""

    isolated = dict(os.environ if environment is None else environment)
    for variable in _GIT_REPOSITORY_ENVIRONMENT:
        isolated.pop(variable, None)
    return isolated


def run_git(
    project: Path,
    arguments: Sequence[str],
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> subprocess.CompletedProcess[str]:
    env = isolated_git_environment(environment)
    return subprocess.run(
        ["git", "-C", str(project), *arguments],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
