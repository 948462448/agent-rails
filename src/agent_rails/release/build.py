"""Build deterministic, transactionally published Agent Rails release assets."""

from __future__ import annotations

from dataclasses import dataclass
import fcntl
import gzip
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import Callable, Mapping, Optional, Sequence, Tuple


ARCHIVE_NAME = "agent-rails.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"
INSTALLER_NAME = "install.sh"
PYTHON_INSTALLER_NAME = "release_install.py"
ASSET_NAMES = (
    ARCHIVE_NAME,
    CHECKSUM_NAME,
    INSTALLER_NAME,
    PYTHON_INSTALLER_NAME,
)

_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,127}\Z")
_REPO_LOCAL_GIT_VARIABLES = {
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_WORK_TREE",
}


@dataclass(frozen=True)
class ReleaseBuildCommand:
    argv: Tuple[str, ...]
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class ReleaseBuildCommandResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


ReleaseBuildRunner = Callable[[ReleaseBuildCommand], ReleaseBuildCommandResult]
AtomicReplace = Callable[[Path, Path], None]


def _default_runner(command: ReleaseBuildCommand) -> ReleaseBuildCommandResult:
    try:
        completed = subprocess.run(
            command.argv,
            cwd=str(command.working_directory),
            env=dict(command.environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        return ReleaseBuildCommandResult(127, stderr=str(exc).encode("utf-8"))
    except PermissionError as exc:
        return ReleaseBuildCommandResult(126, stderr=str(exc).encode("utf-8"))
    return ReleaseBuildCommandResult(
        completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _default_atomic_replace(source: Path, destination: Path) -> None:
    os.link(source, destination, follow_symlinks=False)
    source.unlink()


@dataclass(frozen=True)
class ReleaseBuildDependencies:
    runner: ReleaseBuildRunner = _default_runner
    atomic_replace: AtomicReplace = _default_atomic_replace
    runtime_runner: ReleaseBuildRunner = _default_runner


@dataclass(frozen=True)
class ReleaseBuildRequest:
    source_root: Path
    output_dir: Path
    include_worktree: bool
    environment: Mapping[str, str]


@dataclass(frozen=True)
class ReleaseBuildResult:
    version: str
    source_root: Path
    output_dir: Path
    archive_path: Path
    checksum_path: Path
    installer_path: Path
    python_installer_path: Path
    selected_paths: Tuple[str, ...]


class ReleaseBuildError(RuntimeError):
    """Release assets could not be built without weakening the contract."""


class ReleaseBuildRecoveryError(ReleaseBuildError):
    """Release publication failed and retained private recovery data."""

    def __init__(self, message: str, recovery_path: Path) -> None:
        super().__init__(message)
        self.recovery_path = recovery_path


class ReleaseBuildInputError(ReleaseBuildError):
    """The caller supplied an invalid release-build request."""


@dataclass(frozen=True)
class _SelectedSource:
    relative_path: str
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int


@dataclass(frozen=True)
class _BackupRecord:
    backup: Path
    destination: Path
    identity: Tuple[int, int]


@dataclass(frozen=True)
class _PublishedRecord:
    destination: Path
    identity: Tuple[int, int]


def build_release(
    request: ReleaseBuildRequest,
    *,
    dependencies: Optional[ReleaseBuildDependencies] = None,
) -> ReleaseBuildResult:
    """Build and publish the four fixed-name release assets."""

    dependencies = dependencies or ReleaseBuildDependencies()
    _validate_request(request)
    _validate_dependencies(dependencies)

    source_root = _canonical_source_root(request.source_root)
    version = _read_version(source_root / "VERSION")
    output_dir = _prepare_output_directory(request.output_dir)
    environment = _isolated_environment(request.environment)
    _verify_git_root(source_root, environment, dependencies.runner)
    selected_paths = _select_paths(
        source_root,
        request.include_worktree,
        environment,
        dependencies.runner,
    )
    source_paths = _validate_selected_paths(
        source_root,
        selected_paths,
        include_worktree=request.include_worktree,
    )
    selected_existing_paths = tuple(
        source.relative_path for source in source_paths
    )
    selected_by_path = {
        source.relative_path: source for source in source_paths
    }
    _validate_runtime_assets(source_root, selected_existing_paths)

    stage_root = Path(
        tempfile.mkdtemp(prefix=".agent-rails-release-stage-", dir=str(output_dir))
    )
    cleanup_stage = True
    try:
        _write_archive(
            stage_root / ARCHIVE_NAME,
            version,
            source_root,
            source_paths,
        )
        _write_checksum(
            stage_root / ARCHIVE_NAME,
            stage_root / CHECKSUM_NAME,
        )
        _copy_selected_asset(
            source_root,
            selected_by_path["scripts/agent-release-install.sh"],
            stage_root / INSTALLER_NAME,
        )
        _copy_selected_asset(
            source_root,
            selected_by_path["src/agent_rails/release/install.py"],
            stage_root / PYTHON_INSTALLER_NAME,
        )
        _verify_staged_assets(
            stage_root,
            version,
            environment,
            dependencies.runtime_runner,
        )
        cleanup_stage = False
        try:
            _publish_assets(stage_root, output_dir, dependencies.atomic_replace)
        except ReleaseBuildRecoveryError:
            raise
        except KeyboardInterrupt:
            # The publisher only re-raises this after a completed rollback, or
            # before mutation; incomplete rollback is a RecoveryError instead.
            cleanup_stage = True
            raise
        except ReleaseBuildError:
            cleanup_stage = True
            raise
        else:
            cleanup_stage = True
    except ReleaseBuildRecoveryError:
        cleanup_stage = False
        raise
    except ReleaseBuildError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBuildError(f"Release build failed: {_literal(exc)}") from exc
    finally:
        if cleanup_stage:
            shutil.rmtree(stage_root, ignore_errors=True)

    return ReleaseBuildResult(
        version=version,
        source_root=source_root,
        output_dir=output_dir,
        archive_path=output_dir / ARCHIVE_NAME,
        checksum_path=output_dir / CHECKSUM_NAME,
        installer_path=output_dir / INSTALLER_NAME,
        python_installer_path=output_dir / PYTHON_INSTALLER_NAME,
        selected_paths=selected_existing_paths,
    )


def _validate_request(request: ReleaseBuildRequest) -> None:
    if not isinstance(request, ReleaseBuildRequest):
        raise ReleaseBuildInputError("Release build request has an invalid type.")
    if not isinstance(request.source_root, Path):
        raise ReleaseBuildInputError("Release source root must be a Path.")
    if not isinstance(request.output_dir, Path):
        raise ReleaseBuildInputError("Release output directory must be a Path.")
    if not isinstance(request.include_worktree, bool):
        raise ReleaseBuildInputError("include_worktree must be a boolean.")
    if not isinstance(request.environment, Mapping):
        raise ReleaseBuildInputError("Release environment must be a mapping.")
    for key, value in request.environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ReleaseBuildInputError(
                "Release environment keys and values must be strings."
            )


def _validate_dependencies(dependencies: ReleaseBuildDependencies) -> None:
    if not isinstance(dependencies, ReleaseBuildDependencies):
        raise ReleaseBuildInputError("Release build dependencies are invalid.")
    if (
        not callable(dependencies.runner)
        or not callable(dependencies.runtime_runner)
        or not callable(dependencies.atomic_replace)
    ):
        raise ReleaseBuildInputError("Release build dependencies must be callable.")


def _canonical_source_root(path: Path) -> Path:
    try:
        canonical = Path(os.path.realpath(path))
    except (OSError, TypeError, ValueError) as exc:
        raise ReleaseBuildInputError(
            f"Release source root is invalid: {_literal(exc)}"
        ) from exc
    if not canonical.is_dir():
        raise ReleaseBuildInputError(
            f"Release source root is not a directory: {_literal(canonical)}"
        )
    return canonical


def _prepare_output_directory(path: Path) -> Path:
    try:
        if path.is_symlink():
            raise ReleaseBuildInputError(
                f"Release output directory must not be a symlink: {_literal(path)}"
            )
        if path.exists() and not path.is_dir():
            raise ReleaseBuildInputError(
                f"Release output path is not a directory: {_literal(path)}"
            )
        path.mkdir(parents=True, exist_ok=True)
        canonical = Path(os.path.realpath(path))
    except ReleaseBuildInputError:
        raise
    except (OSError, TypeError, ValueError) as exc:
        raise ReleaseBuildInputError(
            f"Release output directory is invalid: {_literal(exc)}"
        ) from exc
    if not canonical.is_dir():
        raise ReleaseBuildInputError("Release output directory could not be created.")
    return canonical


def _read_version(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseBuildInputError(
            f"Agent Rails VERSION could not be read: {_literal(exc)}"
        ) from exc
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ReleaseBuildInputError("Agent Rails VERSION is not UTF-8.") from exc
    nonempty = [line.strip() for line in text.splitlines() if line.strip()]
    if len(nonempty) != 1 or not _VERSION_PATTERN.fullmatch(nonempty[0]):
        shown = nonempty[0] if nonempty else ""
        raise ReleaseBuildInputError(
            f"Invalid Agent Rails VERSION: {_literal(shown)}"
        )
    return nonempty[0]


def _isolated_environment(environment: Mapping[str, str]) -> Mapping[str, str]:
    return {
        key: value
        for key, value in environment.items()
        if key not in _REPO_LOCAL_GIT_VARIABLES
    }


def _run_git(
    source_root: Path,
    environment: Mapping[str, str],
    runner: ReleaseBuildRunner,
    arguments: Sequence[str],
) -> ReleaseBuildCommandResult:
    command = ReleaseBuildCommand(
        argv=("git", *arguments),
        working_directory=source_root,
        environment=environment,
    )
    try:
        result = runner(command)
    except Exception as exc:
        raise ReleaseBuildError(
            f"Release Git command failed: {_literal(exc)}"
        ) from exc
    if not isinstance(result, ReleaseBuildCommandResult):
        raise ReleaseBuildError("Release Git runner returned an invalid result.")
    return result


def _verify_git_root(
    source_root: Path,
    environment: Mapping[str, str],
    runner: ReleaseBuildRunner,
) -> None:
    result = _run_git(
        source_root,
        environment,
        runner,
        ("rev-parse", "--show-toplevel"),
    )
    if result.exit_code != 0:
        raise ReleaseBuildError(
            "Release source is not a readable Git worktree: "
            f"{_literal_bytes(result.stderr)}"
        )
    try:
        reported = Path(os.path.realpath(os.fsdecode(result.stdout.rstrip(b"\r\n"))))
    except (OSError, TypeError, ValueError) as exc:
        raise ReleaseBuildError("Git returned an invalid worktree root.") from exc
    if reported != source_root:
        raise ReleaseBuildError(
            "Release source must be the Git worktree root: "
            f"{_literal(reported)}"
        )


def _select_paths(
    source_root: Path,
    include_worktree: bool,
    environment: Mapping[str, str],
    runner: ReleaseBuildRunner,
) -> Tuple[str, ...]:
    arguments = ["ls-files"]
    if include_worktree:
        arguments.extend(("--cached", "--others", "--exclude-standard"))
    arguments.append("-z")
    result = _run_git(source_root, environment, runner, arguments)
    if result.exit_code != 0:
        raise ReleaseBuildError(
            "Could not select Release files: "
            f"{_literal_bytes(result.stderr)}"
        )
    if result.stdout and not result.stdout.endswith(b"\0"):
        raise ReleaseBuildError("Git file selection was not NUL terminated.")
    raw_paths = result.stdout.split(b"\0")
    if raw_paths and raw_paths[-1] == b"":
        raw_paths.pop()
    paths: list[str] = []
    seen: set[str] = set()
    for raw_path in raw_paths:
        path = os.fsdecode(raw_path)
        if path in seen:
            raise ReleaseBuildError(
                f"Git selected a duplicate Release path: {_literal(path)}"
            )
        seen.add(path)
        paths.append(path)
    if not paths:
        raise ReleaseBuildError("Git selected no files for the Release archive.")
    return tuple(sorted(paths))


def _validate_selected_paths(
    source_root: Path,
    selected_paths: Sequence[str],
    *,
    include_worktree: bool,
) -> Tuple[_SelectedSource, ...]:
    validated: list[_SelectedSource] = []
    root_descriptor = _open_directory(source_root)
    try:
        for relative_text in selected_paths:
            if _has_unsafe_text(relative_text):
                raise ReleaseBuildError(
                    f"Release path contains unsafe text: {_literal(relative_text)}"
                )
            relative = PurePosixPath(relative_text)
            if (
                not relative_text
                or relative.is_absolute()
                or relative_text.startswith("/")
                or any(part in {"", ".", ".."} for part in relative.parts)
                or ".git" in relative.parts
            ):
                raise ReleaseBuildError(
                    f"Release path is invalid: {_literal(relative_text)}"
                )
            try:
                descriptor = _open_regular_beneath(root_descriptor, relative)
            except FileNotFoundError as exc:
                if include_worktree:
                    continue
                raise ReleaseBuildError(
                    f"Tracked Release path is missing: {_literal(relative_text)}"
                ) from exc
            except OSError as exc:
                raise ReleaseBuildError(
                    "Release path is unreadable or crosses a symlink: "
                    f"{_literal(relative_text)}"
                ) from exc
            try:
                metadata = os.fstat(descriptor)
            finally:
                os.close(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ReleaseBuildError(
                    "Release payload must contain only regular files: "
                    f"{_literal(relative_text)}"
                )
            validated.append(
                _SelectedSource(
                    relative_path=relative.as_posix(),
                    device=metadata.st_dev,
                    inode=metadata.st_ino,
                    mode=metadata.st_mode,
                    size=metadata.st_size,
                    modified_ns=metadata.st_mtime_ns,
                )
            )
    finally:
        os.close(root_descriptor)
    return tuple(validated)


def _validate_runtime_assets(source_root: Path, selected_paths: Sequence[str]) -> None:
    required = {
        "VERSION",
        "bin/agent-rails",
        "scripts/agent-python-cli.py",
        "scripts/agent-release-install.sh",
        "src/agent_rails/__init__.py",
        "src/agent_rails/cli.py",
        "src/agent_rails/public_cli.py",
        "src/agent_rails/release/install.py",
    }
    missing = sorted(required.difference(selected_paths))
    if missing:
        raise ReleaseBuildError(
            "Release file selection is missing required paths: " + ", ".join(missing)
        )
    selected_by_path = {
        source.relative_path: source
        for source in _validate_selected_paths(
            source_root,
            tuple(required),
            include_worktree=False,
        )
    }
    for relative in (
        "bin/agent-rails",
        "scripts/agent-release-install.sh",
        "src/agent_rails/release/install.py",
    ):
        if not selected_by_path[relative].mode & 0o111:
            raise ReleaseBuildError(
                f"Release runtime is not executable: {_literal(relative)}"
            )


def _write_archive(
    destination: Path,
    version: str,
    source_root: Path,
    source_paths: Sequence[_SelectedSource],
) -> None:
    root_name = f"agent-rails-{version}"
    with destination.open("xb") as raw_output:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=raw_output,
            mtime=0,
        ) as compressed:
            with tarfile.open(
                fileobj=compressed,
                mode="w",
                format=tarfile.PAX_FORMAT,
            ) as archive:
                directories = {""}
                for source in source_paths:
                    relative = source.relative_path
                    parent = PurePosixPath(relative).parent
                    while str(parent) not in {"", "."}:
                        directories.add(parent.as_posix())
                        parent = parent.parent
                for relative_dir in sorted(
                    directories,
                    key=lambda value: (value.count("/"), value),
                ):
                    name = root_name if not relative_dir else f"{root_name}/{relative_dir}"
                    info = _normalized_tar_info(name, mode=0o755)
                    info.type = tarfile.DIRTYPE
                    archive.addfile(info)
                root_descriptor = _open_directory(source_root)
                try:
                    for source in source_paths:
                        relative = PurePosixPath(source.relative_path)
                        descriptor = _open_regular_beneath(
                            root_descriptor,
                            relative,
                        )
                        metadata = os.fstat(descriptor)
                        if _source_identity(metadata) != _expected_identity(source):
                            os.close(descriptor)
                            raise ReleaseBuildError(
                                "Release source changed during build: "
                                f"{_literal(source.relative_path)}"
                            )
                        normalized_mode = (
                            0o755 if metadata.st_mode & 0o111 else 0o644
                        )
                        info = _normalized_tar_info(
                            f"{root_name}/{source.relative_path}",
                            mode=normalized_mode,
                        )
                        info.size = metadata.st_size
                        with os.fdopen(descriptor, "rb") as payload:
                            archive.addfile(info, payload)
                            if (
                                _source_identity(os.fstat(payload.fileno()))
                                != _expected_identity(source)
                            ):
                                raise ReleaseBuildError(
                                    "Release source changed while reading: "
                                    f"{_literal(source.relative_path)}"
                                )
                finally:
                    os.close(root_descriptor)
        raw_output.flush()
        os.fsync(raw_output.fileno())


def _normalized_tar_info(name: str, *, mode: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.mode = mode & 0o777
    return info


def _write_checksum(archive_path: Path, checksum_path: Path) -> None:
    digest = hashlib.sha256()
    with archive_path.open("rb") as archive:
        for block in iter(lambda: archive.read(1024 * 1024), b""):
            digest.update(block)
    checksum_path.write_bytes(
        f"{digest.hexdigest()}  {ARCHIVE_NAME}\n".encode("ascii")
    )


def _copy_selected_asset(
    source_root: Path,
    source: _SelectedSource,
    destination: Path,
) -> None:
    root_descriptor = _open_directory(source_root)
    try:
        descriptor = _open_regular_beneath(
            root_descriptor,
            PurePosixPath(source.relative_path),
        )
    finally:
        os.close(root_descriptor)
    try:
        if _source_identity(os.fstat(descriptor)) != _expected_identity(source):
            raise ReleaseBuildError(
                "Release installer source changed during build: "
                f"{_literal(source.relative_path)}"
            )
        with os.fdopen(descriptor, "rb") as payload:
            descriptor = -1
            with destination.open("xb") as output:
                shutil.copyfileobj(payload, output, length=1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            if (
                _source_identity(os.fstat(payload.fileno()))
                != _expected_identity(source)
            ):
                raise ReleaseBuildError(
                    "Release installer source changed while reading: "
                    f"{_literal(source.relative_path)}"
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    destination.chmod(0o755)


def _verify_staged_assets(
    stage_root: Path,
    version: str,
    environment: Mapping[str, str],
    runtime_runner: ReleaseBuildRunner,
) -> None:
    for name in ASSET_NAMES:
        path = stage_root / name
        if not path.is_file() or path.is_symlink():
            raise ReleaseBuildError(f"Release asset is invalid: {name}")
    archive_path = stage_root / ARCHIVE_NAME
    expected_root = f"agent-rails-{version}"
    expected_cli = f"{expected_root}/bin/agent-rails"
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBuildError("Built archive could not be inspected.") from exc
    roots = {PurePosixPath(member.name).parts[0] for member in members if member.name}
    if roots != {expected_root}:
        raise ReleaseBuildError("Built archive has an invalid root.")
    if not any(member.name == expected_cli and member.isfile() for member in members):
        raise ReleaseBuildError("Built archive does not contain the Agent Rails CLI.")
    expected_checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum = (stage_root / CHECKSUM_NAME).read_text(encoding="ascii")
    if checksum != f"{expected_checksum}  {ARCHIVE_NAME}\n":
        raise ReleaseBuildError("Built archive checksum is invalid.")
    smoke_destination = stage_root / "runtime-smoke"
    try:
        runtime_root = _extract_runtime_for_smoke(
            archive_path,
            smoke_destination,
            expected_root,
        )
        _run_runtime_smoke(runtime_root, environment, runtime_runner)
    finally:
        shutil.rmtree(smoke_destination, ignore_errors=True)


def _extract_runtime_for_smoke(
    archive_path: Path,
    destination_root: Path,
    expected_root: str,
) -> Path:
    """Extract only validated regular files and directories for runtime smoke."""

    destination_root.mkdir(mode=0o700)
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            seen: set[str] = set()
            root_directory = False
            for member in members:
                parts = member.name.split("/")
                if (
                    not member.name
                    or _has_unsafe_text(member.name)
                    or member.name in seen
                    or any(part in {"", ".", ".."} for part in parts)
                    or parts[0] != expected_root
                    or not (member.isdir() or member.isfile())
                ):
                    raise ReleaseBuildError(
                        "Built archive contains an unsafe runtime member."
                    )
                seen.add(member.name)
                if member.name == expected_root:
                    if not member.isdir():
                        raise ReleaseBuildError("Built archive has an invalid root.")
                    root_directory = True

            if not root_directory:
                raise ReleaseBuildError("Built archive has an invalid root.")

            for member in members:
                destination = destination_root.joinpath(*PurePosixPath(member.name).parts)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    destination.chmod(member.mode & 0o777)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                payload = archive.extractfile(member)
                if payload is None:
                    raise ReleaseBuildError(
                        "Built archive runtime member could not be read."
                    )
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(destination, flags, member.mode & 0o777)
                try:
                    with payload, os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        shutil.copyfileobj(payload, output, length=1024 * 1024)
                        os.fchmod(output.fileno(), member.mode & 0o777)
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
    except ReleaseBuildError:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise ReleaseBuildError(
            "Built archive could not be extracted for runtime smoke."
        ) from exc
    return destination_root / expected_root


def _run_runtime_smoke(
    runtime_root: Path,
    environment: Mapping[str, str],
    runner: ReleaseBuildRunner,
) -> None:
    smoke_environment = dict(environment)
    smoke_environment["AGENT_RAILS_HOME"] = str(runtime_root)
    smoke_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    command = ReleaseBuildCommand(
        argv=(
            sys.executable,
            "-I",
            str(runtime_root / "scripts" / "agent-python-cli.py"),
            "setup-application",
            "--help",
        ),
        working_directory=runtime_root,
        environment=smoke_environment,
    )
    try:
        result = runner(command)
    except Exception as exc:
        raise ReleaseBuildError("Built archive runtime smoke failed.") from exc
    if not isinstance(result, ReleaseBuildCommandResult):
        raise ReleaseBuildError("Release runtime runner returned an invalid result.")
    if result.exit_code != 0:
        raise ReleaseBuildError("Built archive runtime smoke failed.")


def _publish_assets(
    stage_root: Path,
    output_dir: Path,
    atomic_replace: AtomicReplace,
) -> None:
    directory_descriptor = _open_directory(output_dir)
    try:
        try:
            fcntl.flock(directory_descriptor, fcntl.LOCK_EX)
        except OSError as exc:
            raise ReleaseBuildError(
                f"Unable to lock Release output directory: {_literal(exc)}"
            ) from exc
        _publish_assets_locked(stage_root, output_dir, atomic_replace)
    finally:
        try:
            fcntl.flock(directory_descriptor, fcntl.LOCK_UN)
        finally:
            os.close(directory_descriptor)


def _publish_assets_locked(
    stage_root: Path,
    output_dir: Path,
    atomic_replace: AtomicReplace,
) -> None:
    backup_root = stage_root / "backups"
    backup_root.mkdir(mode=0o700)
    backups: list[_BackupRecord] = []
    published: list[_PublishedRecord] = []
    try:
        for name in ASSET_NAMES:
            destination = output_dir / name
            if destination.is_symlink() or (
                destination.exists() and not destination.is_file()
            ):
                raise ReleaseBuildError(
                    f"Refusing to replace non-file Release asset: {_literal(destination)}"
                )
            if destination.exists():
                backup = backup_root / name
                identity = _inode_identity(destination)
                if identity is None:
                    raise ReleaseBuildError(
                        "Release asset disappeared before backup: "
                        f"{_literal(destination)}"
                    )
                backups.append(
                    _BackupRecord(
                        backup=backup,
                        destination=destination,
                        identity=identity,
                    )
                )
                os.replace(destination, backup)
                if _inode_identity(backup) != identity:
                    raise ReleaseBuildError(
                        "Release asset changed while being backed up: "
                        f"{_literal(destination)}"
                    )
        for name in ASSET_NAMES:
            source = stage_root / name
            destination = output_dir / name
            identity = _inode_identity(source)
            if identity is None:
                raise ReleaseBuildError(
                    f"Staged Release asset disappeared: {_literal(source)}"
                )
            published.append(
                _PublishedRecord(destination=destination, identity=identity)
            )
            atomic_replace(source, destination)
            if _inode_identity(destination) != identity:
                raise ReleaseBuildError(
                    "Published Release asset identity changed unexpectedly: "
                    f"{_literal(destination)}"
                )
    except BaseException as exc:
        rollback_errors: list[str] = []
        for record in reversed(published):
            known, destination_identity = _rollback_inode_identity(
                record.destination,
                rollback_errors,
            )
            try:
                if known and destination_identity == record.identity:
                    record.destination.unlink()
            except BaseException as rollback_exc:
                known_after, identity_after = _rollback_inode_identity(
                    record.destination,
                    rollback_errors,
                )
                if known_after and identity_after == record.identity:
                    rollback_errors.append(_literal(rollback_exc))
        for record in reversed(backups):
            backup_known, backup_identity = _rollback_inode_identity(
                record.backup,
                rollback_errors,
            )
            destination_known, destination_identity = _rollback_inode_identity(
                record.destination,
                rollback_errors,
            )
            if not backup_known or not destination_known:
                continue
            if destination_identity == record.identity:
                continue
            if backup_identity != record.identity:
                rollback_errors.append(
                    "previous Release asset is missing from both destination "
                    f"and backup: {_literal(record.destination)}"
                )
                continue
            if destination_identity is not None:
                rollback_errors.append(
                    "refusing to overwrite a concurrent Release asset while "
                    f"restoring: {_literal(record.destination)}"
                )
                continue
            try:
                os.replace(record.backup, record.destination)
            except BaseException as rollback_exc:
                known_after, identity_after = _rollback_inode_identity(
                    record.destination,
                    rollback_errors,
                )
                if not known_after or identity_after != record.identity:
                    rollback_errors.append(_literal(rollback_exc))
                    continue
            known_after, identity_after = _rollback_inode_identity(
                record.destination,
                rollback_errors,
            )
            if not known_after or identity_after != record.identity:
                rollback_errors.append(
                    "restored Release asset identity changed unexpectedly: "
                    f"{_literal(record.destination)}"
                )
        detail = f"Release asset publish failed: {_literal(exc)}"
        if rollback_errors:
            raise ReleaseBuildRecoveryError(
                detail
                + "; rollback failed: "
                + "; ".join(rollback_errors)
                + "; recovery data retained at: "
                + _literal(backup_root),
                recovery_path=backup_root,
            ) from exc
        if not isinstance(exc, Exception):
            raise
        raise ReleaseBuildError(detail) from exc


def _inode_identity(path: Path) -> Optional[Tuple[int, int]]:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    return metadata.st_dev, metadata.st_ino


def _rollback_inode_identity(
    path: Path,
    rollback_errors: list[str],
) -> Tuple[bool, Optional[Tuple[int, int]]]:
    try:
        return True, _inode_identity(path)
    except BaseException as exc:
        rollback_errors.append(
            f"unable to inspect rollback path {_literal(path)}: {_literal(exc)}"
        )
        return False, None


def _open_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags)


def _open_regular_beneath(
    root_descriptor: int,
    relative: PurePosixPath,
) -> int:
    current = os.dup(root_descriptor)
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        for part in relative.parts[:-1]:
            next_descriptor = os.open(
                part,
                directory_flags,
                dir_fd=current,
            )
            os.close(current)
            current = next_descriptor
        file_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        return os.open(relative.parts[-1], file_flags, dir_fd=current)
    finally:
        os.close(current)


def _source_identity(metadata: os.stat_result) -> Tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_mode,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _expected_identity(source: _SelectedSource) -> Tuple[int, int, int, int, int]:
    return (
        source.device,
        source.inode,
        source.mode,
        source.size,
        source.modified_ns,
    )


def _has_unsafe_text(value: str) -> bool:
    return any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _literal(value: object) -> str:
    text = str(value)
    return "".join(
        character
        if 32 <= ord(character) < 127
        else f"\\u{ord(character):04x}"
        for character in text
    )


def _literal_bytes(value: bytes) -> str:
    return _literal(value.decode("utf-8", errors="replace").strip())
