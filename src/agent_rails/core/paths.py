from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
from typing import Mapping, Optional


@dataclass(frozen=True)
class AgentRailsPaths:
    kit_home: Path
    config_home: str

    @classmethod
    def from_environment(
        cls,
        kit_home: Path,
        environment: Optional[Mapping[str, str]] = None,
    ) -> "AgentRailsPaths":
        env = os.environ if environment is None else environment
        home = env.get("HOME", str(Path.home()))
        config_home = env.get("AGENT_RAILS_CONFIG_HOME") or f"{home}/.agent-rails"
        return cls(
            kit_home=canonical_path(kit_home),
            config_home=config_home,
        )

    @property
    def user_profile_dir(self) -> Path:
        return Path(self.config_home) / "profiles" / "projects"

    @property
    def default_profile_path(self) -> Path:
        return self.kit_home / "profiles" / "default.profile"

    def default_task_pack_path(self, worktree_slug: str) -> str:
        return f"{self.config_home}/agent-context/{worktree_slug}-task-pack.md"

    def default_memory_dir(self, project_name: str) -> str:
        return f"{self.config_home}/memory/{project_name}"

    def default_memory_decision_path(self, project_name: str) -> str:
        return f"{self.config_home}/agent-context/{project_name}-memory-decision.md"

    def resolve_profile(
        self,
        project_root: Path,
        project_name: str,
        explicit_profile: Optional[str] = None,
    ) -> str:
        if explicit_profile:
            explicit_value = explicit_profile
            explicit = Path(explicit_value)
            profiles_dir = self.kit_home / "profiles"
            if (
                explicit.parent == profiles_dir
                and explicit != self.default_profile_path
                and not explicit.is_file()
                and self.default_profile_path.is_file()
            ):
                return str(self.default_profile_path)
            return explicit_value

        candidates = (
            f"{project_root}/.agent-rails/profile",
            f"{project_root}/.agent-rails/profile.sh",
            f"{self.config_home}/profiles/projects/{project_name}.profile",
            f"{self.config_home}/profiles/{project_name}.profile",
        )
        for candidate in candidates:
            if Path(candidate).is_file():
                return candidate
        return str(self.default_profile_path)


def canonical_path(path: Path) -> Path:
    return Path(os.path.realpath(str(path)))


def sanitize_slug(value: str) -> str:
    lowered = value.lower().replace(" ", "-")
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    return slug.strip("-")


def posix_cksum(value: str) -> str:
    try:
        completed = subprocess.run(
            ["cksum"],
            input=value.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("POSIX cksum is required to derive a worktree slug.") from exc
    fields = completed.stdout.decode("ascii", errors="strict").split()
    if not fields or not fields[0].isdigit():
        raise RuntimeError("POSIX cksum returned an invalid checksum.")
    return fields[0]


def project_worktree_slug(project_root: Path, project_name: Optional[str] = None) -> str:
    root = canonical_path(project_root)
    slug = sanitize_slug(project_name or root.name) or "project"
    return f"{slug}-{posix_cksum(str(root))}"
