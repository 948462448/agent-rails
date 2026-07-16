from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Mapping, Optional, Sequence

from agent_rails.core.paths import (
    AgentRailsPaths,
    canonical_path,
    project_worktree_slug,
)

from .profile import load_shell_profile


TARGET_PROFILE_VARIABLES = (
    "AGENT_RAILS_CONFIG_HOME",
    "AGENT_RAILS_ENV_FILE",
    "AGENT_RAILS_PACK_MODE",
    "PROJECT_NAME",
    "PROJECT_WORKTREE_SLUG",
    "TASK_PACK_PATH",
)

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


class TargetProjectError(RuntimeError):
    pass


@dataclass(frozen=True)
class TargetProjectContext:
    root: Path
    default_name: str
    profile_path: str
    profile_status: str
    is_git_repo: bool
    project_name: str
    worktree_slug_preset: str
    worktree_slug: str
    task_pack_path: str
    profile_values: Mapping[str, str]
    profile_environment: Mapping[str, str]

    def shell_values(self) -> Mapping[str, str]:
        values = dict(self.profile_values)
        values.update(
            {
                "AGENT_TARGET_PROJECT_ROOT": str(self.root),
                "AGENT_TARGET_PROJECT_DEFAULT_NAME": self.default_name,
                "AGENT_TARGET_PROJECT_PROFILE_PATH": str(self.profile_path),
                "AGENT_TARGET_PROJECT_PROFILE_STATUS": self.profile_status,
                "AGENT_TARGET_PROJECT_IS_GIT_REPO": "1" if self.is_git_repo else "0",
                "AGENT_TARGET_PROJECT_WORKTREE_SLUG_PRESET": self.worktree_slug_preset,
                "AGENT_TARGET_PROJECT_TASK_PACK_PATH": str(self.task_pack_path),
                "PROJECT_ROOT": str(self.root),
                "PROJECT_NAME": self.project_name,
                "PROJECT_WORKTREE_SLUG": self.worktree_slug,
            }
        )
        return values


def resolve_target_project(
    requested_path: Path,
    *,
    kit_home: Path,
    explicit_profile: Optional[str] = None,
    environment: Optional[Mapping[str, str]] = None,
    require_profile: bool = False,
    load_profile: bool = True,
    load_environment_file: bool = False,
    profile_variables: Sequence[str] = (),
    capture_profile_environment: bool = False,
) -> TargetProjectContext:
    env = dict(os.environ if environment is None else environment)
    if not requested_path.is_dir():
        raise TargetProjectError(f"Project directory not found: {requested_path}")

    project_root, is_git_repo = resolve_project_root_identity(requested_path, env)
    default_name = project_root.name
    paths = AgentRailsPaths.from_environment(kit_home, env)
    profile_path = paths.resolve_profile(project_root, default_name, explicit_profile)
    worktree_slug_preset = env.get("PROJECT_WORKTREE_SLUG", "")

    profile_values: Mapping[str, str] = {}
    profile_environment: Mapping[str, str] = {}
    profile_status = "unloaded"
    profile_exists = Path(profile_path).is_file()
    if require_profile and not profile_exists:
        raise FileNotFoundError(str(profile_path))

    if load_profile and profile_exists:
        profile_load_path = Path(profile_path)
        if not profile_load_path.is_absolute():
            profile_load_path = Path(os.path.abspath(profile_load_path))
        profile_env = dict(env)
        profile_env["AGENT_RAILS_CONFIG_HOME"] = (
            env.get("AGENT_RAILS_CONFIG_HOME") or paths.config_home
        )
        profile_env["AGENT_RAILS_HOME"] = str(paths.kit_home)
        profile_env["PROJECT_ROOT"] = str(project_root)
        profile_env["PROJECT_NAME"] = env.get("PROJECT_NAME") or default_name
        loaded = load_shell_profile(
            profile_load_path,
            environment=profile_env,
            variables=tuple(
                dict.fromkeys((*TARGET_PROFILE_VARIABLES, *profile_variables))
            ),
            env_file_variable="AGENT_RAILS_ENV_FILE" if load_environment_file else None,
            working_directory=project_root,
            capture_exported_environment=capture_profile_environment,
        )
        profile_values = loaded.values
        profile_environment = loaded.exported_environment
        profile_status = "loaded"
    elif not profile_exists:
        profile_status = "missing"

    project_name = profile_values.get("PROJECT_NAME") or env.get("PROJECT_NAME") or default_name
    config_home = profile_values.get("AGENT_RAILS_CONFIG_HOME", paths.config_home)
    effective_paths = AgentRailsPaths(paths.kit_home, config_home)
    worktree_slug = worktree_slug_preset or project_worktree_slug(project_root, project_name)
    task_pack_value = profile_values.get("TASK_PACK_PATH")
    if task_pack_value is None:
        task_pack_value = env.get("TASK_PACK_PATH")
    task_pack_path = (
        task_pack_value
        if task_pack_value
        else effective_paths.default_task_pack_path(worktree_slug)
    )

    return TargetProjectContext(
        root=project_root,
        default_name=default_name,
        profile_path=profile_path,
        profile_status=profile_status,
        is_git_repo=is_git_repo,
        project_name=project_name,
        worktree_slug_preset=worktree_slug_preset,
        worktree_slug=worktree_slug,
        task_pack_path=task_pack_path,
        profile_values=profile_values,
        profile_environment=profile_environment,
    )


def resolve_project_root_identity(
    requested_path: Path, environment: Mapping[str, str]
) -> tuple[Path, bool]:
    """Return the canonical project identity used by Target Project resolution.

    A path anywhere inside one Git worktree identifies that worktree's top-level
    directory.  A nested Git repository therefore keeps its own identity instead
    of being accepted as part of its parent repository.  Repository-discovery
    environment variables are removed so callers cannot redirect the identity.
    """

    requested = canonical_path(requested_path)
    git_environment = dict(environment)
    for variable in _GIT_REPOSITORY_ENVIRONMENT:
        git_environment.pop(variable, None)
    try:
        completed = subprocess.run(
            ["git", "-C", str(requested), "rev-parse", "--show-toplevel"],
            env=git_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
    except OSError:
        return requested, False
    if completed.returncode != 0:
        return requested, False
    return canonical_path(Path(completed.stdout.strip())), True
