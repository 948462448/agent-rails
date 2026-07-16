"""Managed local workspace lifecycle for Agent Rails adapter artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
from typing import Mapping, Optional, Sequence, Tuple

from agent_rails.git._runner import run_git


_GENERATED_MARKER = b"<!-- agent-rails:generated -->"
_VALID_SKILL_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_VALID_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MANAGED_SKILLS_FORMAT = "agent-rails-managed-skills-v2"


class ManagedAdapterWorkspaceError(RuntimeError):
    """Raised when a managed adapter workspace request is unsafe or invalid."""


def resolve_local_ignore_path(
    project: Path,
    *,
    is_git_repo: bool,
    environment: Mapping[str, str],
) -> Path:
    """Resolve the personal ignore file used by one Local Adapter."""

    if not is_git_repo:
        return project / ".gitignore"
    try:
        completed = run_git(
            project,
            ("rev-parse", "--git-path", "info/exclude"),
            environment=environment,
        )
    except OSError as exc:
        raise ManagedAdapterWorkspaceError(
            "Unable to resolve the Target Project local Git exclude file."
        ) from exc
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value:
        raise ManagedAdapterWorkspaceError(
            "Unable to resolve the Target Project local Git exclude file."
        )
    candidate = Path(value)
    return candidate if candidate.is_absolute() else project / candidate


@dataclass(frozen=True)
class ManagedAdapterWorkspaceConfig:
    """Stable paths and policy for one adapter workspace."""

    home: Path
    project: Path
    skills_relative_dir: Path
    guide_path: Path
    pack_command_path: Path
    lite_command_path: Path
    check_command_path: Path
    managed_skills_path: Path
    dry_run: bool = False
    force: bool = False
    protect_tracked: bool = False

    def __post_init__(self) -> None:
        home = _absolute_path(self.home)
        project = _absolute_path(self.project)
        relative_skills = Path(self.skills_relative_dir)
        if (
            relative_skills.is_absolute()
            or relative_skills == Path(".")
            or ".." in relative_skills.parts
        ):
            raise ManagedAdapterWorkspaceError(
                "Managed adapter skills path must be a non-empty project-relative path."
            )
        for name in ("dry_run", "force", "protect_tracked"):
            if not isinstance(getattr(self, name), bool):
                raise ManagedAdapterWorkspaceError(
                    f"Managed adapter workspace {name} must be boolean."
                )

        object.__setattr__(self, "home", home)
        object.__setattr__(self, "project", project)
        object.__setattr__(self, "skills_relative_dir", relative_skills)
        _require_project_path(project, project / relative_skills)
        for name in (
            "guide_path",
            "pack_command_path",
            "lite_command_path",
            "check_command_path",
            "managed_skills_path",
        ):
            value = Path(getattr(self, name))
            normalized = value if value.is_absolute() else project / value
            normalized = _absolute_path(normalized)
            _require_project_path(project, normalized)
            object.__setattr__(self, name, normalized)


@dataclass(frozen=True)
class ManagedAdapterWorkspaceState:
    """Read-only snapshot of mutable workspace discovery state."""

    managed_skills: Tuple[str, ...]
    is_git_repo: bool


class ManagedAdapterWorkspace:
    """Apply adapter artifact and skill lifecycle operations to one project."""

    def __init__(self, config: ManagedAdapterWorkspaceConfig) -> None:
        if not isinstance(config, ManagedAdapterWorkspaceConfig):
            raise ManagedAdapterWorkspaceError(
                "Invalid managed adapter workspace configuration."
            )
        self.config = config
        self._managed_skills: list[str] = []
        self._managed_skill_set: set[str] = set()
        self._managed_skill_fingerprints: dict[str, str] = {}
        self._planned_managed_skill_removals: set[str] = set()
        self._skill_removal_plan: dict[str, bool] = {}
        self._managed_inventory_tracked = False
        self._is_git_repo = self._detect_git_repository()
        self._tracked_paths: Optional[frozenset[str]] = None

    @property
    def state(self) -> ManagedAdapterWorkspaceState:
        return ManagedAdapterWorkspaceState(
            managed_skills=tuple(self._managed_skills),
            is_git_repo=self._is_git_repo,
        )

    @property
    def removal_has_survivors(self) -> bool:
        """Return whether the current removal plan must retain ownership state."""

        if self.config.dry_run:
            return bool(
                self._managed_skill_set - self._planned_managed_skill_removals
            )
        return bool(self._managed_skill_set)

    @staticmethod
    def is_valid_managed_skill_name(skill_name: str) -> bool:
        return (
            isinstance(skill_name, str)
            and bool(skill_name)
            and skill_name not in (".", "..")
            and "/" not in skill_name
            and ".." not in skill_name
            and _VALID_SKILL_NAME.fullmatch(skill_name) is not None
        )

    def _record_managed_skill(self, skill_name: str, fingerprint: str) -> bool:
        """Record one proven skill tree, preserving first-seen inventory order."""

        if (
            not self.is_valid_managed_skill_name(skill_name)
            or not isinstance(fingerprint, str)
            or _VALID_SHA256.fullmatch(fingerprint) is None
        ):
            return False
        if skill_name not in self._managed_skill_set:
            self._managed_skill_set.add(skill_name)
            self._managed_skills.append(skill_name)
        self._managed_skill_fingerprints[skill_name] = fingerprint
        return True

    def load_managed_skills(self) -> Tuple[str, ...]:
        """Load, validate, and de-duplicate the persisted managed inventory."""

        path = self._managed_project_path(self.config.managed_skills_path)
        if not path.exists():
            return ()
        if not path.is_file():
            raise ManagedAdapterWorkspaceError(
                f"Managed skill inventory is not a regular file: {path}"
            )
        self._managed_inventory_tracked = self._is_tracked_candidate(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to read valid managed skill inventory: {path}"
            ) from exc
        if (
            not isinstance(payload, dict)
            or set(payload) != {"format", "skills"}
            or payload.get("format") != _MANAGED_SKILLS_FORMAT
            or not isinstance(payload.get("skills"), list)
        ):
            raise ManagedAdapterWorkspaceError(
                f"Unsupported managed skill inventory format: {path}"
            )

        parsed = []
        seen = set()
        for entry in payload["skills"]:
            if not isinstance(entry, dict) or set(entry) != {"name", "sha256"}:
                raise ManagedAdapterWorkspaceError(
                    f"Invalid managed skill ownership entry in: {path}"
                )
            skill_name = entry["name"]
            fingerprint = entry["sha256"]
            if (
                not self.is_valid_managed_skill_name(skill_name)
                or not isinstance(fingerprint, str)
                or _VALID_SHA256.fullmatch(fingerprint) is None
                or skill_name in seen
            ):
                raise ManagedAdapterWorkspaceError(
                    f"Invalid managed skill ownership entry in: {path}"
                )
            seen.add(skill_name)
            parsed.append((skill_name, fingerprint))

        for skill_name, fingerprint in parsed:
            self._record_managed_skill(skill_name, fingerprint)
        return ()

    def is_generated_file(self, path: Path) -> bool:
        """Recognize current markers and the four pre-marker legacy signatures."""

        candidate = self._managed_project_path(path)
        if not candidate.is_file():
            return False
        try:
            content = candidate.read_bytes()
        except OSError:
            return False
        if _GENERATED_MARKER in content:
            return True

        signatures = {
            self.config.guide_path: (
                b"Agent Rails Version:",
                b"Visible session marker protocol",
            ),
            self.config.pack_command_path: (
                b"Generate and read the Agent Rails Task Pack",
                b"AGENT RAILS: ON",
            ),
            self.config.lite_command_path: (
                b"lite Agent Rails Task Pack",
                b"--pack-mode lite",
            ),
            self.config.check_command_path: (
                b"Agent Rails verification suggestions",
                b"AGENT RAILS: CHECK-ONLY",
            ),
        }
        signature = signatures.get(candidate)
        return signature is not None and all(part in content for part in signature)

    def is_tracked_file(self, path: Path) -> bool:
        candidate = self._managed_project_path(path)
        return self._is_tracked_candidate(candidate)

    def _is_tracked_candidate(self, candidate: Path) -> bool:
        relative = self._project_relative(candidate)
        if relative is None or not self._is_git_repo:
            return False
        return relative in self._load_tracked_paths()

    def write_generated_file(self, path: Path, content: str) -> Tuple[str, ...]:
        candidate = self._managed_project_path(path)
        if not isinstance(content, str):
            raise ManagedAdapterWorkspaceError(
                "Generated adapter file content must be text."
            )
        messages = []
        if self._protects_tracked(candidate) and self.is_tracked_file(candidate):
            return (f"Keeping tracked file in local mode: {candidate}",)
        if candidate.exists() and not self.config.force:
            if not self.is_generated_file(candidate):
                return (f"Keeping unmanaged existing file: {candidate}",)
            messages.append(f"Refreshing Agent Rails-generated {candidate}")
        if self.config.dry_run:
            messages.append(f"Would write {candidate}")
            return tuple(messages)

        try:
            _atomic_write_project_text(
                self.config.project,
                candidate,
                content.rstrip("\n") + "\n",
            )
        except (OSError, UnicodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to write generated adapter file: {candidate}"
            ) from exc
        messages.append(f"Wrote {candidate}")
        return tuple(messages)

    def write_managed_skills(self) -> Tuple[str, ...]:
        if not self._managed_skills:
            return ()
        path = self._managed_project_path(self.config.managed_skills_path)
        if self._protects_tracked(path) and self.is_tracked_file(path):
            return (
                f"Keeping tracked managed skill inventory in local mode: {path}",
            )
        if self.config.dry_run:
            return (f"Would write managed skill inventory: {path}",)
        try:
            content = json.dumps(
                {
                    "format": _MANAGED_SKILLS_FORMAT,
                    "skills": [
                        {
                            "name": skill_name,
                            "sha256": self._managed_skill_fingerprints[skill_name],
                        }
                        for skill_name in sorted(self._managed_skill_set)
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            _atomic_write_project_text(self.config.project, path, content + "\n")
        except (OSError, UnicodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to write managed skill inventory: {path}"
            ) from exc
        return (f"Wrote managed skill inventory: {path}",)

    def install_skills(self) -> Tuple[str, ...]:
        messages = []
        candidates = []
        installed_any = False
        self._managed_project_path(self.skills_dir)
        if self._managed_inventory_tracked and self._protects_tracked(
            self.config.managed_skills_path
        ):
            return (
                "Keeping tracked managed skill inventory in local mode: "
                f"{self.config.managed_skills_path}",
            )
        source_root = self.config.home / "skills"
        skill_directories = _list_skill_directories(self.config.home)
        for skill_name in skill_directories:
            source = source_root / skill_name
            if not self.is_valid_managed_skill_name(skill_name):
                messages.append(
                    f"Ignoring invalid managed skill entry: {skill_name}"
                )
                continue
            target = self.skills_dir / skill_name
            self._managed_project_path(target)
            if self._protects_tracked(target) and self._is_tracked_prefix(
                self._skill_relative_path(skill_name)
            ):
                messages.append(
                    f"Keeping tracked skill directory in local mode: {target}"
                )
                continue
            expected_target_fingerprint = None
            if target.exists() and not self.config.force:
                if skill_name not in self._managed_skill_fingerprints:
                    messages.append(
                        f"Keeping unmanaged existing skill directory: {target}"
                    )
                    continue
                actual = _fingerprint_project_skill(
                    self.config.project,
                    target,
                )
                expected = self._managed_skill_fingerprints[skill_name]
                if actual != expected:
                    messages.append(
                        f"Keeping modified managed skill directory: {target}"
                    )
                    continue
                expected_target_fingerprint = expected
            candidates.append(
                (
                    skill_name,
                    source,
                    target,
                    expected_target_fingerprint,
                )
            )

        if not candidates:
            messages.append("No Agent Rails skills to install.")
            return tuple(messages)

        selected = []
        for skill_name, source, target, expected_target_fingerprint in candidates:
            try:
                source_fingerprint = _preflight_skill_tree(
                    self.config.home,
                    skill_name,
                    target,
                )
            except OSError as exc:
                raise ManagedAdapterWorkspaceError(
                    f"Unable to validate Agent Rails skill: {skill_name}"
                ) from exc
            if source_fingerprint is None:
                messages.append(f"Skipping {skill_name}: missing {source / 'SKILL.md'}")
                continue
            selected.append(
                (
                    skill_name,
                    source,
                    target,
                    source_fingerprint,
                    expected_target_fingerprint,
                )
            )

        if not selected:
            messages.append("No Agent Rails skills to install.")
            return tuple(messages)

        for (
            skill_name,
            source,
            target,
            source_fingerprint,
            expected_target_fingerprint,
        ) in selected:
            if self.config.dry_run:
                self._record_managed_skill(skill_name, source_fingerprint)
                messages.append(f"Would install {source} -> {target}")
                continue
            try:
                installed_fingerprint = _copy_skill_tree_into_project(
                    self.config.project,
                    self.config.home,
                    skill_name,
                    target,
                    expected_target_fingerprint=expected_target_fingerprint,
                    force=self.config.force,
                )
            except OSError as exc:
                raise ManagedAdapterWorkspaceError(
                    f"Unable to install Agent Rails skill: {skill_name}"
                ) from exc
            if installed_fingerprint is None:
                messages.append(
                    f"Keeping modified managed skill directory: {target}"
                )
                continue
            self._record_managed_skill(skill_name, installed_fingerprint)
            installed_any = True
            messages.append(f"Installed {source} -> {target}")
        if installed_any:
            _sync_directory(self.skills_dir)
        return tuple(messages)

    def remove_generated_file(self, path: Path) -> Tuple[str, ...]:
        candidate = self._managed_project_path(path, allow_leaf_symlink=True)
        if self._protects_tracked(candidate) and self._is_tracked_candidate(candidate):
            return (f"Keeping tracked file in local mode: {candidate}",)
        try:
            candidate_mode = os.lstat(candidate).st_mode
        except FileNotFoundError:
            return ()
        if stat.S_ISLNK(candidate_mode) and not self.config.force:
            return (f"Keeping unmanaged existing file: {candidate}",)
        if not self.config.force and not self.is_generated_file(candidate):
            return (f"Keeping unmanaged existing file: {candidate}",)
        if self.config.dry_run:
            return (f"Would remove {candidate}",)
        try:
            _unlink_project_file(
                self.config.project,
                candidate,
                allow_leaf_symlink=True,
            )
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to remove generated adapter file: {candidate}"
            ) from exc
        return (f"Removed {candidate}",)

    def remove_managed_skills(self) -> Tuple[str, ...]:
        self._planned_managed_skill_removals.clear()
        self._managed_project_path(self.skills_dir)
        skill_names = self._managed_skill_names_for_removal()
        if not skill_names:
            return ()
        if not self.skills_dir.exists():
            if self.config.dry_run:
                self._planned_managed_skill_removals.update(skill_names)
            else:
                for skill_name in skill_names:
                    self._forget_managed_skill(skill_name)
            return ()
        if not self.skills_dir.is_dir():
            raise ManagedAdapterWorkspaceError(
                f"Managed skill root is not a directory: {self.skills_dir}"
            )
        if set(self._skill_removal_plan) != set(skill_names):
            self.preflight_removal()

        messages = []
        removed_any = False
        for skill_name in skill_names:
            if not self.is_valid_managed_skill_name(skill_name):
                continue
            target = self.skills_dir / skill_name
            self._managed_project_path(target, allow_leaf_symlink=True)
            if not target.exists() and not target.is_symlink():
                if self.config.dry_run:
                    self._planned_managed_skill_removals.add(skill_name)
                else:
                    self._forget_managed_skill(skill_name)
                continue
            if self._protects_tracked(target) and self._is_tracked_prefix(
                self._skill_relative_path(skill_name)
            ):
                messages.append(
                    f"Keeping tracked skill directory in local mode: {target}"
                )
                continue
            if not self._skill_removal_plan.get(skill_name, False):
                messages.append(
                    f"Keeping modified managed skill directory: {target}"
                )
                continue
            if self.config.dry_run:
                self._planned_managed_skill_removals.add(skill_name)
                messages.append(f"Would remove {target}")
                continue
            try:
                removed = _remove_project_path(
                    self.config.project,
                    target,
                    expected_fingerprint=(
                        None
                        if self.config.force
                        else self._managed_skill_fingerprints.get(skill_name)
                    ),
                    force=self.config.force,
                )
            except OSError as exc:
                raise ManagedAdapterWorkspaceError(
                    f"Unable to remove managed Agent Rails skill: {target}"
                ) from exc
            if not removed:
                messages.append(
                    f"Keeping modified managed skill directory: {target}"
                )
                continue
            self._forget_managed_skill(skill_name)
            removed_any = True
            messages.append(f"Removed {target}")
        if removed_any and self.skills_dir.is_dir():
            _sync_directory(self.skills_dir)
        return tuple(messages)

    def preflight_removal(self) -> None:
        """Build the complete removal plan before any lifecycle mutation."""

        self._skill_removal_plan.clear()
        self._managed_project_path(self.skills_dir)
        skill_names = self._managed_skill_names_for_removal()
        if not self.skills_dir.exists():
            self._skill_removal_plan.update(
                (skill_name, True) for skill_name in skill_names
            )
            return
        for skill_name in skill_names:
            if not self.is_valid_managed_skill_name(skill_name):
                continue
            target = self.skills_dir / skill_name
            self._managed_project_path(target, allow_leaf_symlink=True)
            if self._managed_inventory_tracked and self._protects_tracked(
                self.config.managed_skills_path
            ):
                self._skill_removal_plan[skill_name] = False
                continue
            if self._protects_tracked(target) and self._is_tracked_prefix(
                self._skill_relative_path(skill_name)
            ):
                self._skill_removal_plan[skill_name] = False
                continue
            if self.config.force:
                try:
                    _preflight_project_tree_removal(self.config.project, target)
                except OSError as exc:
                    raise ManagedAdapterWorkspaceError(
                        f"Unable to preflight managed skill removal: {target}"
                    ) from exc
                self._skill_removal_plan[skill_name] = True
                continue
            expected = self._managed_skill_fingerprints.get(skill_name)
            try:
                actual = _fingerprint_project_skill(self.config.project, target)
            except ManagedAdapterWorkspaceError:
                actual = None
            except OSError as exc:
                raise ManagedAdapterWorkspaceError(
                    f"Unable to preflight managed skill removal: {target}"
                ) from exc
            self._skill_removal_plan[skill_name] = (
                actual is None and not target.exists() and not target.is_symlink()
            ) or (expected is not None and actual == expected)

    def remove_managed_skills_file(self) -> Tuple[str, ...]:
        path = self._managed_project_path(self.config.managed_skills_path)
        if self._protects_tracked(path) and self.is_tracked_file(path):
            return (f"Keeping tracked file in local mode: {path}",)
        if not path.exists():
            return ()
        if self.config.dry_run:
            survivors = self._managed_skill_set - self._planned_managed_skill_removals
            if survivors:
                return (
                    f"Would retain managed skill inventory for preserved skills: {path}",
                )
            return (f"Would remove {path}",)
        if self._managed_skills:
            return self.write_managed_skills()
        try:
            _unlink_project_file(self.config.project, path)
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to remove managed skill inventory: {path}"
            ) from exc
        return (f"Removed {path}",)

    def ensure_ignore_block(
        self,
        path: Path,
        marker: str,
        end_marker: str,
        entries: Sequence[str],
        cleanup_only_entries: Sequence[str] = (),
    ) -> Tuple[str, ...]:
        candidate = self._project_input_path(path)
        safe_candidate = _canonical_ignore_path(self.config.project, candidate)
        _require_non_symlink_file(safe_candidate, "local ignore file")
        _validate_ignore_lines(marker, end_marker, *entries, *cleanup_only_entries)
        if self.config.dry_run:
            return (
                f"Would ensure local ignore entries in {candidate}",
                *(f"  {entry}" for entry in entries),
            )

        try:
            content = (
                safe_candidate.read_text(
                    encoding="utf-8", errors="surrogateescape"
                )
                if safe_candidate.is_file()
                else ""
            )
            if marker in content.splitlines():
                content = _strip_ignore_block(
                    content,
                    marker,
                    end_marker,
                    (*entries, *cleanup_only_entries),
                )
            block = "\n".join((marker, *entries, end_marker)) + "\n"
            if content:
                content += "\n"
            _atomic_write_ignore_text(safe_candidate, content + block)
        except (OSError, UnicodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Failed to update local ignore file: {candidate}"
            ) from exc
        return (f"Updated local ignore file: {candidate}",)

    def remove_ignore_block(
        self,
        path: Path,
        marker: str,
        end_marker: str,
        dry_run_prefix: str,
        success_prefix: str,
        entries: Sequence[str],
    ) -> Tuple[str, ...]:
        candidate = self._project_input_path(path)
        safe_candidate = _canonical_ignore_path(self.config.project, candidate)
        _require_non_symlink_file(safe_candidate, "local ignore file")
        _validate_ignore_lines(
            marker, end_marker, dry_run_prefix, success_prefix, *entries
        )
        if not safe_candidate.is_file():
            return ()
        try:
            content = safe_candidate.read_text(
                encoding="utf-8", errors="surrogateescape"
            )
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to read local ignore file: {candidate}"
            ) from exc
        if marker not in content.splitlines():
            return ()
        if self.config.dry_run:
            return (f"{dry_run_prefix} {candidate}",)
        stripped = _strip_ignore_block(content, marker, end_marker, entries)
        try:
            _atomic_write_ignore_text(safe_candidate, stripped)
        except (OSError, UnicodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to remove local ignore block: {candidate}"
            ) from exc
        return (f"{success_prefix} {candidate}",)

    @property
    def skills_dir(self) -> Path:
        return self.config.project / self.config.skills_relative_dir

    def _detect_git_repository(self) -> bool:
        try:
            completed = self._git("rev-parse", "--is-inside-work-tree")
        except OSError:
            return False
        return completed.returncode == 0 and completed.stdout.strip() == "true"

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return run_git(self.config.project, arguments)

    def _project_relative(self, path: Path) -> Optional[str]:
        try:
            relative = path.relative_to(self.config.project)
        except ValueError:
            return None
        if relative == Path("."):
            return None
        return relative.as_posix()

    def _managed_project_path(
        self, path: Path, *, allow_leaf_symlink: bool = False
    ) -> Path:
        candidate = self._project_input_path(path)
        _require_project_path(
            self.config.project,
            candidate,
            allow_leaf_symlink=allow_leaf_symlink,
        )
        return candidate

    def validate_managed_path(self, path: Path) -> Path:
        """Validate one tool-specific managed path against project escape."""

        return self._managed_project_path(path)

    def validate_ignore_path(self, path: Path) -> Path:
        """Preflight one tool-specific ignore file without following its leaf."""

        candidate = self._project_input_path(path)
        safe_candidate = _canonical_ignore_path(self.config.project, candidate)
        try:
            parent = _open_absolute_directory_no_symlinks(safe_candidate.parent)
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to validate local ignore parent: {candidate.parent}"
            ) from exc
        try:
            if not os.access(
                safe_candidate.parent,
                os.W_OK | os.X_OK,
            ):
                raise ManagedAdapterWorkspaceError(
                    f"Local ignore parent is not writable: {candidate.parent}"
                )
            try:
                current = os.stat(
                    safe_candidate.name,
                    dir_fd=parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                return candidate
            if stat.S_ISLNK(current.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Refusing symbolic-link local ignore file: {candidate}"
                )
            if not stat.S_ISREG(current.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Local ignore path is not a regular file: {candidate}"
                )
            descriptor = os.open(
                safe_candidate.name,
                os.O_RDONLY
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent,
            )
            os.close(descriptor)
        except ManagedAdapterWorkspaceError:
            raise
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to validate local ignore file: {candidate}"
            ) from exc
        finally:
            os.close(parent)
        return candidate

    def replace_text_file(self, path: Path, content: str) -> None:
        """Atomically replace one tool-specific text file without following links."""

        candidate = self._managed_project_path(path)
        if not isinstance(content, str):
            raise ManagedAdapterWorkspaceError(
                "Managed adapter text replacement requires text content."
            )
        try:
            _atomic_write_project_text(self.config.project, candidate, content)
        except (OSError, UnicodeError) as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to replace managed adapter text file: {candidate}"
            ) from exc

    def unlink_managed_file(self, path: Path) -> None:
        """Remove one tool-specific file through a no-follow project handle."""

        candidate = self._managed_project_path(path)
        try:
            _unlink_project_file(self.config.project, candidate)
        except FileNotFoundError:
            return
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to remove managed adapter file: {candidate}"
            ) from exc

    def _project_input_path(self, path: Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.config.project / candidate
        return _absolute_path(candidate)

    def _protects_tracked(self, path: Path) -> bool:
        return self.config.protect_tracked and not self.config.force

    def _is_tracked_prefix(self, relative: str) -> bool:
        if not self._is_git_repo:
            return False
        prefix = relative.rstrip("/") + "/"
        return any(
            path == relative or path.startswith(prefix)
            for path in self._load_tracked_paths()
        )

    def _load_tracked_paths(self) -> frozenset[str]:
        if self._tracked_paths is None:
            try:
                completed = self._git("ls-files", "-z")
            except OSError as exc:
                raise ManagedAdapterWorkspaceError(
                    "Unable to query Git tracked paths for local-mode protection."
                ) from exc
            if completed.returncode != 0:
                raise ManagedAdapterWorkspaceError(
                    "Unable to query Git tracked paths for local-mode protection."
                )
            self._tracked_paths = frozenset(completed.stdout.split("\0")) - {""}
        return self._tracked_paths

    def _skill_relative_path(self, skill_name: str) -> str:
        return (self.config.skills_relative_dir / skill_name).as_posix()

    def _forget_managed_skill(self, skill_name: str) -> None:
        self._managed_skill_set.discard(skill_name)
        self._managed_skill_fingerprints.pop(skill_name, None)
        self._managed_skills = [
            name for name in self._managed_skills if name != skill_name
        ]

    def _managed_skill_names_for_removal(self) -> Tuple[str, ...]:
        return tuple(self._managed_skills)


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _require_project_path(
    project: Path, path: Path, *, allow_leaf_symlink: bool = False
) -> None:
    try:
        relative = path.relative_to(project)
    except ValueError as exc:
        raise ManagedAdapterWorkspaceError(
            f"Managed adapter path is outside the target project: {path}"
        ) from exc
    if relative == Path("."):
        raise ManagedAdapterWorkspaceError(
            f"Managed adapter path cannot replace the target project: {path}"
        )
    current = project
    for part in relative.parts:
        current = current / part
        try:
            mode = os.lstat(current).st_mode
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ManagedAdapterWorkspaceError(
                f"Unable to validate managed adapter path: {current}"
            ) from exc
        if stat.S_ISLNK(mode):
            if allow_leaf_symlink and current == path:
                continue
            raise ManagedAdapterWorkspaceError(
                f"Managed adapter path traverses a symbolic link: {current}"
            )
        if current != path and not stat.S_ISDIR(mode):
            raise ManagedAdapterWorkspaceError(
                f"Managed adapter parent is not a directory: {current}"
            )


def _require_non_symlink_file(path: Path, label: str) -> None:
    try:
        mode = os.lstat(path).st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise ManagedAdapterWorkspaceError(f"Unable to validate {label}: {path}") from exc
    if stat.S_ISLNK(mode):
        raise ManagedAdapterWorkspaceError(
            f"Refusing symbolic-link {label}: {path}"
        )


def _directory_flags() -> int:
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if no_follow == 0:
        raise ManagedAdapterWorkspaceError(
            "This platform cannot provide no-follow adapter workspace writes."
        )
    return (
        os.O_RDONLY
        | no_follow
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _open_project_parent(
    project: Path,
    path: Path,
    *,
    create: bool,
    allow_leaf_symlink: bool = False,
) -> Tuple[int, str]:
    _require_project_path(
        project,
        path,
        allow_leaf_symlink=allow_leaf_symlink,
    )
    relative = path.relative_to(project)
    descriptor = os.open(project, _directory_flags())
    try:
        for part in relative.parts[:-1]:
            try:
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(part, mode=0o777, dir_fd=descriptor)
                child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor, relative.name
    except BaseException:
        os.close(descriptor)
        raise


def _atomic_write_project_text(project: Path, path: Path, content: str) -> None:
    payload = content.encode("utf-8", errors="strict")
    parent, name = _open_project_parent(project, path, create=True)
    try:
        _atomic_write_at(parent, name, payload)
    finally:
        os.close(parent)


def _atomic_write_ignore_text(path: Path, content: str) -> None:
    parent = _open_absolute_directory_no_symlinks(path.parent)
    try:
        _atomic_write_at(
            parent,
            path.name,
            content.encode("utf-8", errors="surrogateescape"),
        )
    finally:
        os.close(parent)


def _open_absolute_directory_no_symlinks(path: Path) -> int:
    absolute = _absolute_path(path)
    descriptor = os.open(absolute.anchor or os.sep, _directory_flags())
    try:
        for part in absolute.parts[1:]:
            child = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _canonical_ignore_path(project: Path, candidate: Path) -> Path:
    """Resolve only the shared trusted prefix, preserving inner link checks."""

    try:
        common = Path(os.path.commonpath((os.fspath(project), os.fspath(candidate))))
        relative = candidate.relative_to(common)
    except (ValueError, OSError):
        return candidate
    resolved_common = Path(os.path.realpath(common))
    return resolved_common / relative


def _atomic_write_at(
    parent: int, name: str, payload: bytes, *, requested_mode: Optional[int] = None
) -> None:
    existing_mode = None
    try:
        existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(existing.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Refusing symbolic-link managed adapter file: {name}"
            )
        if not stat.S_ISREG(existing.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Managed adapter file is not regular: {name}"
            )
        existing_mode = stat.S_IMODE(existing.st_mode)

    temporary = ""
    descriptor = -1
    for _ in range(128):
        temporary = f".{name}.agent-rails-{secrets.token_hex(8)}"
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY
                | os.O_CREAT
                | os.O_EXCL
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_CLOEXEC", 0),
                0o666 if requested_mode is None else requested_mode,
                dir_fd=parent,
            )
            break
        except FileExistsError:
            continue
    if descriptor < 0:
        raise ManagedAdapterWorkspaceError(
            f"Unable to allocate managed adapter staging file for: {name}"
        )
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while staging managed adapter file")
            view = view[written:]
        os.fsync(descriptor)
        final_mode = requested_mode if requested_mode is not None else existing_mode
        if final_mode is not None:
            os.fchmod(descriptor, final_mode)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary,
            name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        temporary = ""
        os.fsync(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary:
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass


def _write_staged_file_at(
    parent: int,
    name: str,
    source: int,
    mode: int,
) -> None:
    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        mode,
        dir_fd=parent,
    )
    keep = False
    try:
        while True:
            chunk = os.read(source, 1024 * 1024)
            if not chunk:
                break
            view = memoryview(chunk)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise OSError("short write while copying staged skill file")
                view = view[written:]
        os.fchmod(descriptor, mode)
        keep = True
    finally:
        os.close(descriptor)
        if not keep:
            try:
                os.unlink(name, dir_fd=parent)
            except FileNotFoundError:
                pass


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, _directory_flags())
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _list_skill_directories(home: Path) -> Tuple[str, ...]:
    home_descriptor = -1
    skills_descriptor = -1
    try:
        home_descriptor = os.open(home, _directory_flags())
        try:
            skills_descriptor = os.open(
                "skills", _directory_flags(), dir_fd=home_descriptor
            )
        except FileNotFoundError:
            return ()
        names = []
        for name in sorted(os.listdir(skills_descriptor)):
            item = os.stat(name, dir_fd=skills_descriptor, follow_symlinks=False)
            if stat.S_ISDIR(item.st_mode) and not stat.S_ISLNK(item.st_mode):
                names.append(name)
        return tuple(names)
    except OSError as exc:
        raise ManagedAdapterWorkspaceError(
            f"Unable to list Agent Rails skills: {home / 'skills'}"
        ) from exc
    finally:
        if skills_descriptor >= 0:
            os.close(skills_descriptor)
        if home_descriptor >= 0:
            os.close(home_descriptor)


def _open_skill_source_directory(home: Path, skill_name: str) -> int:
    home_descriptor = os.open(home, _directory_flags())
    skills_descriptor = -1
    try:
        skills_descriptor = os.open(
            "skills", _directory_flags(), dir_fd=home_descriptor
        )
        return os.open(
            skill_name, _directory_flags(), dir_fd=skills_descriptor
        )
    except BaseException:
        raise
    finally:
        if skills_descriptor >= 0:
            os.close(skills_descriptor)
        os.close(home_descriptor)


def _preflight_skill_tree(
    home: Path,
    skill_name: str,
    target: Path,
) -> Optional[str]:
    source = _open_skill_source_directory(home, skill_name)
    try:
        _validate_skill_source_fd(source, home / "skills" / skill_name)
        _validate_skill_destination_fd(source, target)
        if not _has_regular_skill_manifest(source):
            return None
        return _fingerprint_skill_fd(source, home / "skills" / skill_name)
    finally:
        os.close(source)


def _has_regular_skill_manifest(directory: int) -> bool:
    try:
        manifest = os.stat("SKILL.md", dir_fd=directory, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return stat.S_ISREG(manifest.st_mode)


def _require_regular_skill_manifest(directory: int, display: Path) -> None:
    if not _has_regular_skill_manifest(directory):
        raise ManagedAdapterWorkspaceError(
            f"Agent Rails skill is missing a regular manifest: {display / 'SKILL.md'}"
        )


def _fingerprint_project_skill(project: Path, target: Path) -> Optional[str]:
    parent, name = _open_project_parent(
        project,
        target,
        create=False,
        allow_leaf_symlink=True,
    )
    try:
        return _fingerprint_named_skill(parent, name, target)
    finally:
        os.close(parent)


def _fingerprint_named_skill(
    parent: int,
    name: str,
    display: Path,
) -> Optional[str]:
    directory = -1
    try:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(current.st_mode) or not stat.S_ISDIR(current.st_mode):
            return None
        directory = os.open(name, _directory_flags(), dir_fd=parent)
        if not _has_regular_skill_manifest(directory):
            return None
        return _fingerprint_skill_fd(directory, display)
    finally:
        if directory >= 0:
            os.close(directory)


def _fingerprint_skill_fd(directory: int, display: Path) -> str:
    digest = hashlib.sha256()
    digest.update(b"agent-rails-skill-tree-v1\0")
    _fingerprint_directory_fd(directory, digest, "", display)
    return digest.hexdigest()


def _fingerprint_directory_fd(
    directory: int,
    digest: "hashlib._Hash",
    relative: str,
    display: Path,
) -> None:
    before = os.fstat(directory)
    _fingerprint_record(
        digest,
        b"D",
        relative,
        stat.S_IMODE(before.st_mode),
        0,
    )
    names = sorted(os.listdir(directory), key=os.fsencode)
    for name in names:
        child_relative = name if not relative else f"{relative}/{name}"
        child_display = display / name
        child_stat = os.stat(name, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(child_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a symbolic link: {child_display}"
            )
        if stat.S_ISDIR(child_stat.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=directory)
            try:
                opened = os.fstat(child)
                if not stat.S_ISDIR(opened.st_mode):
                    raise ManagedAdapterWorkspaceError(
                        f"Agent Rails skill changed during fingerprint: {child_display}"
                    )
                _fingerprint_directory_fd(
                    child,
                    digest,
                    child_relative,
                    child_display,
                )
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(child_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a non-regular file: {child_display}"
            )
        descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory,
        )
        try:
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Agent Rails skill changed during fingerprint: {child_display}"
                )
            _fingerprint_record(
                digest,
                b"F",
                child_relative,
                stat.S_IMODE(opened.st_mode),
                opened.st_size,
            )
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            after = os.fstat(descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ManagedAdapterWorkspaceError(
                    f"Agent Rails skill changed during fingerprint: {child_display}"
                )
        finally:
            os.close(descriptor)

    after_names = sorted(os.listdir(directory), key=os.fsencode)
    after = os.fstat(directory)
    if names != after_names or (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_mtime_ns,
    ):
        raise ManagedAdapterWorkspaceError(
            f"Agent Rails skill changed during fingerprint: {display}"
        )


def _fingerprint_record(
    digest: "hashlib._Hash",
    kind: bytes,
    relative: str,
    mode: int,
    size: int,
) -> None:
    encoded = os.fsencode(relative)
    digest.update(kind)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)
    digest.update(mode.to_bytes(4, "big"))
    digest.update(size.to_bytes(8, "big"))


def _validate_skill_source_fd(source: int, display: Path) -> None:
    for name in sorted(os.listdir(source)):
        item = os.stat(name, dir_fd=source, follow_symlinks=False)
        child_display = display / name
        if stat.S_ISLNK(item.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a symbolic link: {child_display}"
            )
        if stat.S_ISDIR(item.st_mode):
            child = os.open(name, _directory_flags(), dir_fd=source)
            try:
                _validate_skill_source_fd(child, child_display)
            finally:
                os.close(child)
        elif not stat.S_ISREG(item.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a non-regular file: {child_display}"
            )


def _validate_skill_destination_fd(source: int, target: Path) -> None:
    try:
        target_stat = os.stat(target, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISLNK(target_stat.st_mode) or not stat.S_ISDIR(target_stat.st_mode):
        raise ManagedAdapterWorkspaceError(
            f"Managed adapter directory is unsafe: {target}"
        )
    for name in sorted(os.listdir(source)):
        destination = target / name
        try:
            destination_stat = os.stat(destination, follow_symlinks=False)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(destination_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Managed adapter destination contains a symbolic link: {destination}"
            )
        source_stat = os.stat(name, dir_fd=source, follow_symlinks=False)
        if stat.S_ISDIR(source_stat.st_mode):
            if not stat.S_ISDIR(destination_stat.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Managed adapter directory is unsafe: {destination}"
                )
            child = os.open(name, _directory_flags(), dir_fd=source)
            try:
                _validate_skill_destination_fd(child, destination)
            finally:
                os.close(child)
        elif not stat.S_ISREG(destination_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Managed adapter file is unsafe: {destination}"
            )


def _copy_skill_tree_into_project(
    project: Path,
    home: Path,
    skill_name: str,
    target: Path,
    *,
    expected_target_fingerprint: Optional[str],
    force: bool,
) -> Optional[str]:
    parent, name = _open_project_parent(project, target, create=True)
    source = -1
    stage = ""
    backup = ""
    preserve_backup = False
    installed_fingerprint = ""
    try:
        source = _open_skill_source_directory(home, skill_name)
        _require_regular_skill_manifest(source, home / "skills" / skill_name)
        source_mode = stat.S_IMODE(os.fstat(source).st_mode)
        stage = _allocate_directory_at(parent, f".{name}.agent-rails-stage")
        stage_descriptor = os.open(stage, _directory_flags(), dir_fd=parent)
        try:
            _copy_source_fd(
                source,
                stage_descriptor,
                home / "skills" / skill_name,
            )
            _require_regular_skill_manifest(
                stage_descriptor,
                home / "skills" / skill_name,
            )
            os.fchmod(stage_descriptor, source_mode)
            installed_fingerprint = _fingerprint_skill_fd(
                stage_descriptor,
                target,
            )
        finally:
            os.close(stage_descriptor)

        try:
            existing = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            if expected_target_fingerprint is not None and not force:
                return None
            os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
            stage = ""
        else:
            if stat.S_ISLNK(existing.st_mode) or not stat.S_ISDIR(existing.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Managed adapter directory is unsafe: {target}"
                )
            if expected_target_fingerprint is None and not force:
                return None
            backup = _unused_name_at(parent, f".{name}.agent-rails-old")
            os.rename(name, backup, src_dir_fd=parent, dst_dir_fd=parent)
            if not force:
                try:
                    current_fingerprint = _fingerprint_named_skill(
                        parent,
                        backup,
                        target,
                    )
                except ManagedAdapterWorkspaceError:
                    current_fingerprint = None
                except BaseException:
                    preserve_backup = True
                    os.rename(backup, name, src_dir_fd=parent, dst_dir_fd=parent)
                    backup = ""
                    preserve_backup = False
                    raise
                if current_fingerprint != expected_target_fingerprint:
                    preserve_backup = True
                    os.rename(backup, name, src_dir_fd=parent, dst_dir_fd=parent)
                    backup = ""
                    preserve_backup = False
                    return None
            try:
                os.rename(stage, name, src_dir_fd=parent, dst_dir_fd=parent)
                stage = ""
            except BaseException:
                preserve_backup = True
                os.rename(backup, name, src_dir_fd=parent, dst_dir_fd=parent)
                backup = ""
                preserve_backup = False
                raise
            try:
                _remove_tree_at(parent, backup)
            except BaseException:
                replacement = _unused_name_at(
                    parent,
                    f".{name}.agent-rails-failed",
                )
                os.rename(
                    name,
                    replacement,
                    src_dir_fd=parent,
                    dst_dir_fd=parent,
                )
                try:
                    os.rename(
                        backup,
                        name,
                        src_dir_fd=parent,
                        dst_dir_fd=parent,
                    )
                except BaseException:
                    preserve_backup = True
                    os.rename(
                        replacement,
                        name,
                        src_dir_fd=parent,
                        dst_dir_fd=parent,
                    )
                    raise
                backup = ""
                try:
                    _remove_tree_at(parent, replacement)
                except OSError:
                    pass
                raise
            backup = ""
    finally:
        if source >= 0:
            os.close(source)
        if stage:
            try:
                _remove_tree_at(parent, stage)
            except OSError:
                pass
        if backup and not preserve_backup:
            try:
                _remove_tree_at(parent, backup)
            except OSError:
                pass
        os.close(parent)
    return installed_fingerprint


def _unused_name_at(parent: int, prefix: str) -> str:
    for _ in range(128):
        name = f"{prefix}-{secrets.token_hex(8)}"
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return name
    raise ManagedAdapterWorkspaceError(
        f"Unable to allocate managed adapter directory name for: {prefix}"
    )


def _allocate_directory_at(parent: int, prefix: str) -> str:
    for _ in range(128):
        name = f"{prefix}-{secrets.token_hex(8)}"
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
            return name
        except FileExistsError:
            continue
    raise ManagedAdapterWorkspaceError(
        f"Unable to allocate managed adapter staging directory for: {prefix}"
    )


def _copy_source_fd(source: int, destination: int, display: Path) -> None:
    for name in sorted(os.listdir(source)):
        source_stat = os.stat(name, dir_fd=source, follow_symlinks=False)
        source_mode = stat.S_IMODE(source_stat.st_mode)
        child_display = display / name
        if stat.S_ISLNK(source_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a symbolic link: {child_display}"
            )
        if stat.S_ISDIR(source_stat.st_mode):
            source_child = os.open(name, _directory_flags(), dir_fd=source)
            destination_child = -1
            try:
                os.mkdir(name, mode=0o700, dir_fd=destination)
                destination_child = os.open(
                    name, _directory_flags(), dir_fd=destination
                )
                _copy_source_fd(source_child, destination_child, child_display)
                os.fchmod(destination_child, source_mode)
            finally:
                if destination_child >= 0:
                    os.close(destination_child)
                os.close(source_child)
            continue
        if not stat.S_ISREG(source_stat.st_mode):
            raise ManagedAdapterWorkspaceError(
                f"Agent Rails skill contains a non-regular file: {child_display}"
            )
        source_descriptor = os.open(
            name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_CLOEXEC", 0),
            dir_fd=source,
        )
        try:
            opened = os.fstat(source_descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ManagedAdapterWorkspaceError(
                    f"Agent Rails skill file changed during copy: {child_display}"
                )
            _write_staged_file_at(
                destination,
                name,
                source_descriptor,
                source_mode,
            )
            after = os.fstat(source_descriptor)
            if (
                opened.st_dev,
                opened.st_ino,
                opened.st_mode,
                opened.st_size,
                opened.st_mtime_ns,
            ) != (
                after.st_dev,
                after.st_ino,
                after.st_mode,
                after.st_size,
                after.st_mtime_ns,
            ):
                raise ManagedAdapterWorkspaceError(
                    f"Agent Rails skill file changed during copy: {child_display}"
                )
        finally:
            os.close(source_descriptor)


def _unlink_project_file(
    project: Path, path: Path, *, allow_leaf_symlink: bool = False
) -> None:
    parent, name = _open_project_parent(
        project,
        path,
        create=False,
        allow_leaf_symlink=allow_leaf_symlink,
    )
    try:
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(current.st_mode):
            raise IsADirectoryError(os.fspath(path))
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)


def _preflight_project_tree_removal(project: Path, path: Path) -> None:
    parent, name = _open_project_parent(
        project,
        path,
        create=False,
        allow_leaf_symlink=True,
    )
    directory = -1
    try:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if not stat.S_ISDIR(current.st_mode) or stat.S_ISLNK(current.st_mode):
            return
        directory = os.open(name, _directory_flags(), dir_fd=parent)
        _validate_removal_tree_fd(directory)
    finally:
        if directory >= 0:
            os.close(directory)
        os.close(parent)


def _validate_removal_tree_fd(directory: int) -> None:
    for child_name in os.listdir(directory):
        child = os.stat(child_name, dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISDIR(child.st_mode) or stat.S_ISLNK(child.st_mode):
            continue
        child_directory = os.open(
            child_name,
            _directory_flags(),
            dir_fd=directory,
        )
        try:
            _validate_removal_tree_fd(child_directory)
        finally:
            os.close(child_directory)


def _remove_project_path(
    project: Path,
    path: Path,
    *,
    expected_fingerprint: Optional[str],
    force: bool,
) -> bool:
    parent, name = _open_project_parent(
        project,
        path,
        create=False,
        allow_leaf_symlink=True,
    )
    try:
        try:
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return True
        if stat.S_ISDIR(current.st_mode) and not stat.S_ISLNK(current.st_mode):
            tombstone = _unused_name_at(parent, f".{name}.agent-rails-remove")
            os.rename(name, tombstone, src_dir_fd=parent, dst_dir_fd=parent)
            if not force:
                try:
                    actual_fingerprint = _fingerprint_named_skill(
                        parent,
                        tombstone,
                        path,
                    )
                except ManagedAdapterWorkspaceError:
                    actual_fingerprint = None
                except BaseException:
                    os.rename(tombstone, name, src_dir_fd=parent, dst_dir_fd=parent)
                    raise
                if (
                    expected_fingerprint is None
                    or actual_fingerprint != expected_fingerprint
                ):
                    os.rename(tombstone, name, src_dir_fd=parent, dst_dir_fd=parent)
                    return False
            _remove_tree_at(parent, tombstone)
        else:
            if not force:
                return False
            os.unlink(name, dir_fd=parent)
        return True
    finally:
        os.close(parent)


def _remove_tree_at(parent: int, name: str) -> None:
    directory = os.open(name, _directory_flags(), dir_fd=parent)
    try:
        current_mode = stat.S_IMODE(os.fstat(directory).st_mode)
        os.fchmod(directory, current_mode | stat.S_IRWXU)
        for child_name in os.listdir(directory):
            child = os.stat(child_name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(child.st_mode) and not stat.S_ISLNK(child.st_mode):
                _remove_tree_at(directory, child_name)
            else:
                os.unlink(child_name, dir_fd=directory)
    finally:
        os.close(directory)
    os.rmdir(name, dir_fd=parent)


def _validate_ignore_lines(*values: str) -> None:
    for value in values:
        if not isinstance(value, str) or "\n" in value or "\r" in value:
            raise ManagedAdapterWorkspaceError(
                "Managed local ignore values must be single text lines."
            )


def _strip_ignore_block(
    content: str,
    marker: str,
    end_marker: str,
    managed_entries: Sequence[str],
) -> str:
    managed = set(managed_entries)
    output = []
    in_managed_block = False
    for line in content.splitlines():
        if line == marker:
            in_managed_block = True
            continue
        if in_managed_block and line == end_marker:
            in_managed_block = False
            continue
        if in_managed_block and line in managed:
            continue
        if in_managed_block:
            in_managed_block = False
        output.append(line)
    return "" if not output else "\n".join(output) + "\n"
