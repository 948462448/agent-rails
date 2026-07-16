"""Safely install complete Agent Rails skill trees into a local directory."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile
from typing import Optional, Sequence, Tuple


_SKILL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class SkillsInstallEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class SkillsInstallEvent:
    stream: SkillsInstallEventStream
    text: str


@dataclass(frozen=True)
class SkillsInstallRequest:
    kit_home: Path
    destination: Path
    selected_skills: Tuple[str, ...]
    dry_run: bool


@dataclass(frozen=True)
class SkillsInstallResult:
    selected_skills: Tuple[str, ...]
    installed_skills: Tuple[str, ...]
    events: Tuple[SkillsInstallEvent, ...]
    exit_code: int = 0

    @property
    def stdout(self) -> str:
        return "".join(
            event.text
            for event in self.events
            if event.stream is SkillsInstallEventStream.STDOUT
        )

    @property
    def stderr(self) -> str:
        return "".join(
            event.text
            for event in self.events
            if event.stream is SkillsInstallEventStream.STDERR
        )


class SkillsInstallError(RuntimeError):
    def __init__(self, message: str, *, exit_code: int = 1) -> None:
        super().__init__(_literal(message))
        self.exit_code = exit_code


class SkillsInstallInputError(SkillsInstallError):
    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


def install_skills(request: SkillsInstallRequest) -> SkillsInstallResult:
    """Preflight, stage, and transactionally refresh selected skill trees."""

    _validate_request(request)
    source_root = request.kit_home / "skills"
    _validate_source_root(source_root)
    _validate_destination_root(request.destination)

    events: list[SkillsInstallEvent] = []
    requested = request.selected_skills
    if requested:
        candidates = requested
    else:
        try:
            candidates = tuple(
                sorted(
                    entry.name
                    for entry in source_root.iterdir()
                    if entry.is_dir() and not entry.is_symlink()
                )
            )
        except OSError as exc:
            raise SkillsInstallError(
                f"Unable to list source dir: {source_root}"
            ) from exc

    selected: list[str] = []
    sources: list[Tuple[str, Path]] = []
    for name in candidates:
        _validate_skill_name(name)
        source = source_root / name
        manifest = source / "SKILL.md"
        if not _is_nofollow_regular(manifest):
            if source.exists() and not source.is_symlink():
                _event(
                    events,
                    SkillsInstallEventStream.STDERR,
                    f"Skipping {name}: missing {manifest}\n",
                )
            else:
                _event(
                    events,
                    SkillsInstallEventStream.STDERR,
                    f"Skipping {name}: missing {manifest}\n",
                )
            continue
        _validate_source_tree(source)
        target = request.destination / name
        _validate_target(target)
        selected.append(name)
        sources.append((name, source))

    if request.dry_run:
        for name, source in sources:
            _event(
                events,
                SkillsInstallEventStream.STDOUT,
                f"Would install {source} -> {request.destination / name}\n",
            )
        return SkillsInstallResult(
            selected_skills=tuple(selected),
            installed_skills=(),
            events=tuple(events),
        )

    if not sources:
        return SkillsInstallResult(
            selected_skills=(),
            installed_skills=(),
            events=tuple(events),
        )

    try:
        request.destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SkillsInstallError(
            f"Unable to create skill destination: {request.destination}"
        ) from exc
    _validate_destination_root(request.destination)

    transaction = Path(
        tempfile.mkdtemp(
            prefix=".agent-rails-skills-",
            dir=str(request.destination),
        )
    )
    staged_root = transaction / "staged"
    backup_root = transaction / "backups"
    staged_root.mkdir()
    backup_root.mkdir()
    installed: list[str] = []
    backups: list[Tuple[Path, Path]] = []
    published: list[Tuple[Path, int, int]] = []
    cleanup_transaction = True
    try:
        for name, source in sources:
            stage = staged_root / name
            shutil.copytree(source, stage, symlinks=True, copy_function=shutil.copy2)
            _validate_source_tree(stage)

        _validate_destination_root(request.destination)
        for name, _ in sources:
            _validate_target(request.destination / name)

        for name, _ in sources:
            target = request.destination / name
            if target.exists():
                backup = backup_root / name
                # Journal the rollback intent before the atomic move.  If a
                # signal arrives immediately after os.replace() commits, the
                # recovery loop can still find and restore the only old copy.
                backups.append((backup, target))
                os.replace(target, backup)
        for name, source in sources:
            target = request.destination / name
            stage = staged_root / name
            stage_metadata = stage.lstat()
            # Journal the exact staged inode before publication.  This closes
            # the signal window after rename without granting ownership over
            # a concurrently-created, unrelated target.
            published.append(
                (target, stage_metadata.st_dev, stage_metadata.st_ino)
            )
            os.replace(stage, target)
            installed.append(name)
            _event(
                events,
                SkillsInstallEventStream.STDOUT,
                f"Installed {source} -> {target}\n",
            )
    except BaseException as exc:
        # Rollback can itself be interrupted.  Keep the private transaction by
        # default and enable cleanup only after every recovery step succeeds.
        cleanup_transaction = False
        rollback_errors: list[str] = []
        for target, expected_device, expected_inode in reversed(published):
            try:
                try:
                    metadata = target.lstat()
                except FileNotFoundError:
                    continue
                if (
                    metadata.st_dev != expected_device
                    or metadata.st_ino != expected_inode
                ):
                    rollback_errors.append(
                        f"Published target ownership changed: {_literal(target)}"
                    )
                    continue
                _remove_tree(target)
            except Exception as rollback_exc:
                rollback_errors.append(_literal(rollback_exc))
        for backup, target in reversed(backups):
            if not backup.exists():
                # The failure happened before the intended backup move.
                continue
            try:
                os.replace(backup, target)
            except Exception as rollback_exc:
                rollback_errors.append(_literal(rollback_exc))
        if rollback_errors:
            # The transaction can contain the only remaining copy of a user's
            # previous skill tree.  A failed rollback is a recovery state, not
            # disposable staging data, so leave the private transaction in
            # place and surface its exact location.
            cleanup_transaction = False
            raise SkillsInstallError(
                "Unable to install skill trees; rollback failed: "
                + "; ".join(rollback_errors)
                + f". Recovery data kept at: {_literal(transaction)}"
            ) from exc
        cleanup_transaction = True
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, SkillsInstallError):
            raise
        raise SkillsInstallError(
            f"Unable to install skill trees: {_literal(exc)}"
        ) from exc
    finally:
        if cleanup_transaction:
            shutil.rmtree(transaction, ignore_errors=True)

    return SkillsInstallResult(
        selected_skills=tuple(selected),
        installed_skills=tuple(installed),
        events=tuple(events),
    )


def _validate_request(request: SkillsInstallRequest) -> None:
    if not isinstance(request, SkillsInstallRequest):
        raise SkillsInstallInputError("Invalid skills install request.")
    if not isinstance(request.kit_home, Path):
        raise SkillsInstallInputError("Skills kit home must be a Path.")
    if not isinstance(request.destination, Path):
        raise SkillsInstallInputError("Skills destination must be a Path.")
    if not isinstance(request.selected_skills, tuple):
        raise SkillsInstallInputError("Selected skills must be a tuple.")
    if not isinstance(request.dry_run, bool):
        raise SkillsInstallInputError("Skills dry_run must be a boolean.")
    seen: set[str] = set()
    for name in request.selected_skills:
        if not isinstance(name, str):
            raise SkillsInstallInputError("Skill names must be text.")
        _validate_skill_name(name)
        if name in seen:
            raise SkillsInstallInputError(f"Duplicate skill name: {name}")
        seen.add(name)


def _validate_skill_name(name: str) -> None:
    if (
        not _SKILL_NAME.fullmatch(name)
        or ".." in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise SkillsInstallInputError(f"Invalid skill name: {_literal(name)}")


def _validate_source_root(source_root: Path) -> None:
    if source_root.is_symlink():
        raise SkillsInstallError(
            f"Skills source dir must not be a symbolic link: {source_root}"
        )
    if not source_root.is_dir():
        raise SkillsInstallError(f"Missing source dir: {source_root}")


def _validate_destination_root(destination: Path) -> None:
    if destination.is_symlink():
        raise SkillsInstallError(
            f"Skills destination must not be a symbolic link: {destination}"
        )
    if destination.exists() and not destination.is_dir():
        raise SkillsInstallError(
            f"Skills destination is not a directory: {destination}"
        )


def _validate_target(target: Path) -> None:
    if target.is_symlink():
        raise SkillsInstallError(
            f"Skill target must not be a symbolic link: {target}"
        )
    if target.exists() and not target.is_dir():
        raise SkillsInstallError(f"Skill target is not a directory: {target}")


def _validate_source_tree(root: Path) -> None:
    if root.is_symlink() or not root.is_dir():
        raise SkillsInstallError(
            f"Skill source must be a non-symbolic-link directory: {root}"
        )
    stack = [root]
    while stack:
        directory = stack.pop()
        try:
            entries = tuple(os.scandir(directory))
        except OSError as exc:
            raise SkillsInstallError(f"Unable to read skill source: {directory}") from exc
        for entry in entries:
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise SkillsInstallError(
                    f"Unable to inspect skill source: {entry.path}"
                ) from exc
            if stat.S_ISLNK(metadata.st_mode):
                raise SkillsInstallError(
                    f"Skill source contains a symbolic link: {entry.path}"
                )
            if stat.S_ISDIR(metadata.st_mode):
                stack.append(Path(entry.path))
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise SkillsInstallError(
                    f"Skill source contains an unsupported node: {entry.path}"
                )


def _is_nofollow_regular(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(metadata.st_mode)


def _remove_tree(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def _event(
    events: list[SkillsInstallEvent],
    stream: SkillsInstallEventStream,
    text: str,
) -> None:
    events.append(SkillsInstallEvent(stream=stream, text=text))


def _literal(value: object) -> str:
    return "".join(
        character
        if 32 <= ord(character) < 127
        else f"\\u{ord(character):04x}"
        for character in str(value)
    )


__all__ = (
    "SkillsInstallError",
    "SkillsInstallEvent",
    "SkillsInstallEventStream",
    "SkillsInstallInputError",
    "SkillsInstallRequest",
    "SkillsInstallResult",
    "install_skills",
)
