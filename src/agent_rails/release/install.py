"""Validate and transactionally install an Agent Rails release archive."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import gzip
import hashlib
import hmac
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import sys
import tarfile
import tempfile
from typing import BinaryIO, Callable, Mapping, Optional, Sequence, TextIO, Tuple
import unicodedata
import urllib.request


_ARCHIVE_NAME = "agent-rails.tar.gz"
_CHECKSUM_NAME = f"{_ARCHIVE_NAME}.sha256"
_REPOSITORY_PATTERN = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z"
)
_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,127}\Z")
_CHECKSUM_PATTERN = re.compile(
    rf"([0-9A-Fa-f]{{64}})[ \t]+\*?{re.escape(_ARCHIVE_NAME)}\Z"
)

_USAGE = """Usage: install.sh [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--dry-run]

Downloads a versioned Agent Rails release archive, verifies its SHA-256 digest,
installs it under a versioned directory, and atomically switches the `current`
and CLI symlinks. VERSION defaults to the latest published release.
"""


class ReleaseInstallEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class ReleaseInstallEvent:
    stream: ReleaseInstallEventStream
    text: str


@dataclass(frozen=True)
class ReleaseInstallRequest:
    requested_version: str
    repository: str
    install_root: Path
    bin_dir: Path
    dry_run: bool
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class ReleaseInstallResult:
    requested_version: str
    repository: str
    version: str
    install_root: Path
    bin_dir: Path
    release_dir: Path
    current_link: Path
    cli_link: Path
    dry_run: bool
    already_installed: bool
    exit_code: int
    events: Tuple[ReleaseInstallEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_events(self.events, ReleaseInstallEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, ReleaseInstallEventStream.STDERR)


class ReleaseInstallError(RuntimeError):
    """A release could not be installed without weakening safety."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Tuple[ReleaseInstallEvent, ...] = (),
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.events = events

    @property
    def stdout(self) -> str:
        return _render_events(self.events, ReleaseInstallEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, ReleaseInstallEventStream.STDERR)


class ReleaseInstallInputError(ReleaseInstallError):
    """The caller supplied an invalid release-install request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class _ReleaseInstallSignal(BaseException):
    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class ReleaseInstallLimits:
    """Hard resource budgets for untrusted release assets."""

    archive_download_bytes: int = 128 * 1024 * 1024
    checksum_download_bytes: int = 4 * 1024
    archive_stream_bytes: int = 384 * 1024 * 1024
    archive_members: int = 20_000
    archive_single_file_bytes: int = 64 * 1024 * 1024
    archive_total_file_bytes: int = 256 * 1024 * 1024


_DEFAULT_LIMITS = ReleaseInstallLimits()


ReleaseDownloader = Callable[[str, Path], None]
AtomicReplace = Callable[[Path, Path], None]


def _default_download(url: str, destination: Path) -> None:
    limit = (
        _DEFAULT_LIMITS.archive_download_bytes
        if destination.name == _ARCHIVE_NAME
        else _DEFAULT_LIMITS.checksum_download_bytes
    )
    _download_url_bounded(url, destination, limit)


def _download_url_bounded(url: str, destination: Path, limit: int) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "agent-rails-release-installer"},
    )
    with (
        urllib.request.urlopen(request, timeout=60) as response,
        destination.open("xb") as output,
    ):
        copied = 0
        while True:
            block = response.read(min(1024 * 1024, limit - copied + 1))
            if not block:
                break
            copied += len(block)
            if copied > limit:
                raise ReleaseInstallError(
                    "Release asset exceeds its download size limit."
                )
            output.write(block)
        output.flush()
        os.fsync(output.fileno())


def _default_atomic_replace(source: Path, destination: Path) -> None:
    os.replace(source, destination)


@dataclass(frozen=True)
class ReleaseInstallDependencies:
    download: ReleaseDownloader = _default_download
    atomic_replace: AtomicReplace = _default_atomic_replace
    limits: ReleaseInstallLimits = _DEFAULT_LIMITS


@dataclass(frozen=True)
class _ArchiveLayout:
    version: str
    root_name: str
    members: Tuple[tarfile.TarInfo, ...]


@dataclass(frozen=True)
class _PathSnapshot:
    kind: str
    data: bytes = b""
    mode: int = 0
    link_target: str = ""


def install_release(
    request: ReleaseInstallRequest,
    *,
    dependencies: Optional[ReleaseInstallDependencies] = None,
) -> ReleaseInstallResult:
    """Install one release while serializing all mutations for its install root."""

    dependencies = dependencies or ReleaseInstallDependencies()
    _validate_request(request)
    _validate_dependencies(dependencies)
    working_directory = _canonical_working_directory(request.working_directory)
    normalized_request = ReleaseInstallRequest(
        requested_version=request.requested_version,
        repository=request.repository,
        install_root=_canonical_install_path(
            request.install_root,
            working_directory,
            "Install root",
        ),
        bin_dir=_canonical_install_path(
            request.bin_dir,
            working_directory,
            "CLI bin directory",
        ),
        dry_run=request.dry_run,
        working_directory=working_directory,
        environment=dict(request.environment),
    )
    if normalized_request.dry_run:
        return _install_release_impl(
            normalized_request,
            dependencies=dependencies,
        )
    with _exclusive_install_locks(
        normalized_request.install_root,
        normalized_request.bin_dir,
    ):
        return _install_release_impl(
            normalized_request,
            dependencies=dependencies,
        )


def _install_release_impl(
    request: ReleaseInstallRequest,
    *,
    dependencies: Optional[ReleaseInstallDependencies] = None,
) -> ReleaseInstallResult:
    """Prepare and commit one immutable release under the install-root lock."""

    dependencies = dependencies or ReleaseInstallDependencies()
    _validate_request(request)
    _validate_dependencies(dependencies)
    working_directory = _canonical_working_directory(request.working_directory)
    install_root = _canonical_install_path(
        request.install_root,
        working_directory,
        "Install root",
    )
    bin_dir = _canonical_install_path(
        request.bin_dir,
        working_directory,
        "CLI bin directory",
    )
    environment = dict(request.environment)
    requested_version = request.requested_version
    repository = request.repository
    base_url = environment.get(
        "AGENT_RAILS_RELEASE_BASE_URL",
        f"https://github.com/{repository}",
    ).rstrip("/")
    if not base_url or _has_unsafe_terminal_text(base_url):
        raise ReleaseInstallInputError(
            f"Invalid release base URL: {_terminal_literal(base_url)}"
        )
    asset_base = (
        f"{base_url}/releases/latest/download"
        if requested_version == "latest"
        else f"{base_url}/releases/download/v{requested_version}"
    )
    archive_url = f"{asset_base}/{_ARCHIVE_NAME}"
    checksum_url = f"{asset_base}/{_CHECKSUM_NAME}"
    current_link = install_root / "current"
    cli_link = bin_dir / "agent-rails"
    preview_version = requested_version
    preview_release = install_root / "releases" / preview_version

    if request.dry_run:
        events: list[ReleaseInstallEvent] = []
        _out(events, "Agent Rails Release Install")
        _out(events, f"Repository: {_terminal_literal(repository)}")
        _out(events, f"Version: {_terminal_literal(requested_version)}")
        _out(events, f"Would download: {_terminal_literal(archive_url)}")
        _out(events, f"Would verify: {_terminal_literal(checksum_url)}")
        _out(events, f"Would install under: {_terminal_literal(str(install_root / 'releases'))}")
        _out(events, f"Would link: {_terminal_literal(str(cli_link))}")
        return ReleaseInstallResult(
            requested_version=requested_version,
            repository=repository,
            version=preview_version,
            install_root=install_root,
            bin_dir=bin_dir,
            release_dir=preview_release,
            current_link=current_link,
            cli_link=cli_link,
            dry_run=True,
            already_installed=False,
            exit_code=0,
            events=tuple(events),
        )

    events = []
    _out(events, "Agent Rails Release Install")
    _out(events, f"Repository: {_terminal_literal(repository)}")
    _out(events, f"Version: {_terminal_literal(requested_version)}")

    try:
        current_snapshot = _managed_link_snapshot(
            current_link,
            expected_target=None,
            current=True,
        )
        cli_snapshot = _managed_link_snapshot(
            cli_link,
            expected_target=str(current_link / "bin/agent-rails"),
            current=False,
        )
        repository_metadata = install_root / "release-repository"
        bin_metadata = install_root / "release-bin-dir"
        repository_snapshot = _metadata_snapshot(repository_metadata)
        bin_metadata_snapshot = _metadata_snapshot(bin_metadata)
    except ReleaseInstallError as exc:
        raise _with_events(exc, events) from exc

    package_version: str
    release_dir: Path
    already_installed = False
    stage: Optional[Path] = None

    if requested_version != "latest":
        release_dir = install_root / "releases" / requested_version
        try:
            already_installed = _existing_release_is_valid(
                release_dir,
                requested_version,
            )
        except ReleaseInstallError as exc:
            raise _with_events(exc, events) from exc
        package_version = requested_version
    else:
        release_dir = preview_release

    if not already_installed:
        try:
            with tempfile.TemporaryDirectory(
                prefix="agent-rails-release-download-"
            ) as temporary:
                temporary_root = Path(temporary)
                archive_path = temporary_root / _ARCHIVE_NAME
                checksum_path = temporary_root / _CHECKSUM_NAME
                _out(events, "Download Agent Rails release")
                try:
                    _download_release_asset(
                        dependencies,
                        archive_url,
                        archive_path,
                        dependencies.limits.archive_download_bytes,
                    )
                    _download_release_asset(
                        dependencies,
                        checksum_url,
                        checksum_path,
                        dependencies.limits.checksum_download_bytes,
                    )
                except ReleaseInstallError:
                    raise
                except Exception as exc:
                    raise ReleaseInstallError(
                        "Unable to download Agent Rails release."
                    ) from exc
                _verify_checksum(archive_path, checksum_path)
                tar_path = temporary_root / "agent-rails.tar"
                _materialize_tar_stream(
                    archive_path,
                    tar_path,
                    dependencies.limits.archive_stream_bytes,
                )
                layout = _inspect_archive(
                    tar_path,
                    requested_version,
                    dependencies.limits,
                )
                package_version = layout.version
                release_dir = install_root / "releases" / package_version
                already_installed = _existing_release_is_valid(
                    release_dir,
                    package_version,
                )
                if not already_installed:
                    releases_dir = install_root / "releases"
                    releases_dir.mkdir(parents=True, exist_ok=True)
                    stage = _extract_archive(
                        tar_path,
                        layout,
                        releases_dir,
                        dependencies.limits,
                    )
        except ReleaseInstallError as exc:
            if stage is not None:
                _remove_path(stage)
            raise _with_events(exc, events) from exc
        except (OSError, UnicodeError, tarfile.TarError) as exc:
            if stage is not None:
                _remove_path(stage)
            raise ReleaseInstallError(
                "Unable to prepare Agent Rails release.",
                events=tuple(events),
            ) from exc
        except BaseException:
            if stage is not None:
                _remove_path_quietly(stage)
            raise

    try:
        _commit_install(
            dependencies=dependencies,
            stage=stage,
            release_dir=release_dir,
            install_root=install_root,
            bin_dir=bin_dir,
            current_link=current_link,
            cli_link=cli_link,
            repository=repository,
            package_version=package_version,
            repository_snapshot=repository_snapshot,
            bin_metadata_snapshot=bin_metadata_snapshot,
            current_snapshot=current_snapshot,
            cli_snapshot=cli_snapshot,
        )
    except ReleaseInstallError as exc:
        raise _with_events(exc, events) from exc
    except BaseException:
        if stage is not None:
            _remove_path_quietly(stage)
        raise

    if already_installed:
        _out(events, f"Agent Rails {_terminal_literal(package_version)} is already installed.")
    else:
        _out(events, f"Installed Agent Rails {_terminal_literal(package_version)}")
    _out(events, f"Home: {_terminal_literal(str(current_link))}")
    _out(events, f"Command: {_terminal_literal(str(cli_link))}")
    path_entries = environment.get("PATH", "").split(os.pathsep)
    if str(bin_dir) not in path_entries:
        _out(events, f"Add {_terminal_literal(str(bin_dir))} to PATH to run agent-rails directly.")

    return ReleaseInstallResult(
        requested_version=requested_version,
        repository=repository,
        version=package_version,
        install_root=install_root,
        bin_dir=bin_dir,
        release_dir=release_dir,
        current_link=current_link,
        cli_link=cli_link,
        dry_run=False,
        already_installed=already_installed,
        exit_code=0,
        events=tuple(events),
    )


def _commit_install(
    *,
    dependencies: ReleaseInstallDependencies,
    stage: Optional[Path],
    release_dir: Path,
    install_root: Path,
    bin_dir: Path,
    current_link: Path,
    cli_link: Path,
    repository: str,
    package_version: str,
    repository_snapshot: _PathSnapshot,
    bin_metadata_snapshot: _PathSnapshot,
    current_snapshot: _PathSnapshot,
    cli_snapshot: _PathSnapshot,
) -> None:
    repository_metadata = install_root / "release-repository"
    bin_metadata = install_root / "release-bin-dir"
    staged: list[Path] = []
    attempted: list[tuple[Path, _PathSnapshot]] = []
    release_published = False
    release_identity: Optional[tuple[int, int]] = None
    release_was_new = stage is not None
    try:
        install_root.mkdir(parents=True, exist_ok=True)
        bin_dir.mkdir(parents=True, exist_ok=True)
        repository_stage = _stage_private_file(
            install_root,
            ".release-repository.",
            f"{repository}\n".encode("utf-8"),
        )
        staged.append(repository_stage)
        bin_metadata_stage = _stage_private_file(
            install_root,
            ".release-bin-dir.",
            f"{bin_dir}\n".encode("utf-8"),
        )
        staged.append(bin_metadata_stage)
        current_stage = _stage_symlink(
            install_root,
            ".current.",
            f"releases/{package_version}",
        )
        staged.append(current_stage)
        cli_stage = _stage_symlink(
            bin_dir,
            ".agent-rails.",
            str(current_link / "bin/agent-rails"),
        )
        staged.append(cli_stage)

        _assert_snapshot(current_link, current_snapshot)
        _assert_snapshot(cli_link, cli_snapshot)
        _assert_snapshot(repository_metadata, repository_snapshot)
        _assert_snapshot(bin_metadata, bin_metadata_snapshot)

        if stage is not None:
            if _path_exists(release_dir):
                raise ReleaseInstallError(
                    "Release destination changed during installation."
                )
            stage_identity = _path_identity(stage)
            release_identity = stage_identity
            try:
                dependencies.atomic_replace(stage, release_dir)
                release_published = True
            except BaseException:
                release_published = (
                    stage_identity is not None
                    and _path_identity(release_dir) == stage_identity
                )
                raise
            stage = None

        _replace_owned_destination(
            dependencies.atomic_replace,
            repository_stage,
            repository_metadata,
            repository_snapshot,
            attempted,
        )
        staged.remove(repository_stage)
        _replace_owned_destination(
            dependencies.atomic_replace,
            bin_metadata_stage,
            bin_metadata,
            bin_metadata_snapshot,
            attempted,
        )
        staged.remove(bin_metadata_stage)
        _replace_owned_destination(
            dependencies.atomic_replace,
            current_stage,
            current_link,
            current_snapshot,
            attempted,
        )
        staged.remove(current_stage)
        _replace_owned_destination(
            dependencies.atomic_replace,
            cli_stage,
            cli_link,
            cli_snapshot,
            attempted,
        )
        staged.remove(cli_stage)
    except BaseException as exc:
        rollback_failed = False
        pending_interrupt: Optional[BaseException] = (
            exc if not isinstance(exc, Exception) else None
        )
        for destination, snapshot in reversed(attempted):
            try:
                _restore_snapshot(
                    destination,
                    snapshot,
                    dependencies.atomic_replace,
                )
            except BaseException as rollback_exc:
                rollback_failed = True
                if (
                    pending_interrupt is None
                    and not isinstance(rollback_exc, Exception)
                ):
                    pending_interrupt = rollback_exc
        if release_was_new and release_published:
            try:
                current_identity = _path_identity(release_dir)
                if current_identity == release_identity:
                    _remove_path(release_dir)
                elif current_identity is not None:
                    rollback_failed = True
            except BaseException as rollback_exc:
                rollback_failed = True
                if (
                    pending_interrupt is None
                    and not isinstance(rollback_exc, Exception)
                ):
                    pending_interrupt = rollback_exc
        if stage is not None:
            try:
                _remove_path(stage)
            except BaseException as rollback_exc:
                rollback_failed = True
                if (
                    pending_interrupt is None
                    and not isinstance(rollback_exc, Exception)
                ):
                    pending_interrupt = rollback_exc
        for path in staged:
            try:
                _remove_path(path)
            except BaseException as rollback_exc:
                rollback_failed = True
                if (
                    pending_interrupt is None
                    and not isinstance(rollback_exc, Exception)
                ):
                    pending_interrupt = rollback_exc
        message = (
            "Unable to commit Agent Rails release; rollback was incomplete."
            if rollback_failed
            else "Unable to commit Agent Rails release."
        )
        if pending_interrupt is not None:
            raise pending_interrupt
        raise ReleaseInstallError(message) from exc
    finally:
        for path in staged:
            _remove_path_quietly(path)
        if stage is not None:
            _remove_path_quietly(stage)


def _replace_owned_destination(
    atomic_replace: AtomicReplace,
    source: Path,
    destination: Path,
    snapshot: _PathSnapshot,
    attempted: list[tuple[Path, _PathSnapshot]],
) -> None:
    source_identity = _path_identity(source)
    record = (destination, snapshot)
    try:
        atomic_replace(source, destination)
        attempted.append(record)
    except BaseException:
        if (
            record not in attempted
            and source_identity is not None
            and _path_identity(destination) == source_identity
        ):
            attempted.append(record)
        raise


def _acquire_file_lock(path: Path) -> int:
    descriptor = -1
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReleaseInstallError(
                "Agent Rails release install lock is not a regular file."
            )
        os.fchmod(descriptor, 0o600)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        return descriptor
    except ReleaseInstallError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ReleaseInstallError(
            "Unable to acquire the Agent Rails release install lock."
        ) from exc
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        raise


@contextmanager
def _exclusive_install_locks(install_root: Path, bin_dir: Path):
    """Serialize both the immutable release root and shared CLI publication."""

    lock_paths = sorted(
        {
            install_root / ".install.lock",
            bin_dir / ".agent-rails.install.lock",
        },
        key=os.fspath,
    )
    descriptors: list[int] = []
    try:
        for path in lock_paths:
            descriptors.append(_acquire_file_lock(path))
        yield
    finally:
        for descriptor in reversed(descriptors):
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(descriptor)


@contextmanager
def _install_signal_handlers():
    handled = tuple(
        candidate
        for candidate in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)
        if candidate is not None
    )
    previous: dict[int, object] = {}

    def interrupt(signum: int, _frame: object) -> None:
        raise _ReleaseInstallSignal(signum)

    try:
        for signum in handled:
            previous_handler = signal.getsignal(signum)
            signal.signal(signum, interrupt)
            previous[signum] = previous_handler
    except (OSError, ValueError):
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass
        yield
        return
    try:
        yield
    finally:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                pass


def _validate_request(request: ReleaseInstallRequest) -> None:
    if not isinstance(request, ReleaseInstallRequest):
        raise ReleaseInstallInputError("Invalid Release Install request.")
    if not isinstance(request.requested_version, str):
        raise ReleaseInstallInputError("Release version must be text.")
    if not isinstance(request.repository, str):
        raise ReleaseInstallInputError("GitHub repository must be text.")
    if not isinstance(request.install_root, Path):
        raise ReleaseInstallInputError("Install root must be a Path.")
    if not isinstance(request.bin_dir, Path):
        raise ReleaseInstallInputError("CLI bin directory must be a Path.")
    if type(request.dry_run) is not bool:
        raise ReleaseInstallInputError("dry_run must be boolean.")
    if not isinstance(request.working_directory, Path):
        raise ReleaseInstallInputError("Working directory must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise ReleaseInstallInputError("Release environment must be a mapping.")
    if any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise ReleaseInstallInputError(
            "Release environment keys and values must be text."
        )
    if (
        not _REPOSITORY_PATTERN.fullmatch(request.repository)
        or any(part in {".", ".."} for part in request.repository.split("/"))
    ):
        raise ReleaseInstallInputError(
            f"Invalid GitHub repository: {_terminal_literal(request.repository)}"
        )
    if (
        request.requested_version != "latest"
        and not _VERSION_PATTERN.fullmatch(request.requested_version)
    ):
        raise ReleaseInstallInputError(
            f"Invalid release version: {_terminal_literal(request.requested_version)}"
        )
    for label, path in (
        ("Install root", request.install_root),
        ("CLI bin directory", request.bin_dir),
        ("Working directory", request.working_directory),
    ):
        text = os.fspath(path)
        if _has_unsafe_terminal_text(text):
            raise ReleaseInstallInputError(
                f"{label} is invalid: {_terminal_literal(text)}"
            )


def _validate_dependencies(dependencies: ReleaseInstallDependencies) -> None:
    if not isinstance(dependencies, ReleaseInstallDependencies):
        raise ReleaseInstallInputError("Invalid Release Install dependencies.")
    if not callable(dependencies.download) or not callable(dependencies.atomic_replace):
        raise ReleaseInstallInputError("Release Install dependencies are invalid.")
    if not isinstance(dependencies.limits, ReleaseInstallLimits):
        raise ReleaseInstallInputError("Release Install limits are invalid.")
    values = tuple(vars(dependencies.limits).values())
    if any(type(value) is not int or value <= 0 for value in values):
        raise ReleaseInstallInputError("Release Install limits must be positive integers.")


def _canonical_working_directory(path: Path) -> Path:
    try:
        canonical = Path(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError) as exc:
        raise ReleaseInstallInputError("Working directory is invalid.") from exc
    if not canonical.is_dir():
        raise ReleaseInstallInputError(
            f"Working directory not found: {_terminal_literal(str(canonical))}"
        )
    return canonical


def _canonical_install_path(path: Path, working_directory: Path, label: str) -> Path:
    anchored = path if path.is_absolute() else working_directory / path
    try:
        canonical = Path(os.path.realpath(os.fspath(anchored)))
    except (OSError, ValueError) as exc:
        raise ReleaseInstallInputError(f"{label} is invalid.") from exc
    if canonical == Path(canonical.anchor):
        raise ReleaseInstallInputError(
            f"{label} must not be the filesystem root: {_terminal_literal(str(canonical))}"
        )
    return canonical


def _managed_link_snapshot(
    path: Path,
    *,
    expected_target: Optional[str],
    current: bool,
) -> _PathSnapshot:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _PathSnapshot("absent")
    except OSError as exc:
        raise ReleaseInstallError("Unable to inspect managed release links.") from exc
    if not stat.S_ISLNK(info.st_mode):
        raise ReleaseInstallError(
            f"Refusing to replace non-symlink path: {_terminal_literal(str(path))}"
        )
    try:
        target = os.readlink(path)
    except OSError as exc:
        raise ReleaseInstallError("Unable to inspect managed release link.") from exc
    if current:
        fields = target.split("/")
        owned = (
            len(fields) == 2
            and fields[0] == "releases"
            and bool(_VERSION_PATTERN.fullmatch(fields[1]))
        )
    else:
        owned = target == expected_target
    if not owned:
        raise ReleaseInstallError(
            f"Refusing to replace an unmanaged symlink: {_terminal_literal(str(path))}"
        )
    return _PathSnapshot("symlink", link_target=target)


def _metadata_snapshot(path: Path) -> _PathSnapshot:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return _PathSnapshot("absent")
    except OSError as exc:
        raise ReleaseInstallError("Unable to inspect release metadata.") from exc
    if not stat.S_ISREG(info.st_mode):
        raise ReleaseInstallError("Release metadata path is not a regular file.")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ReleaseInstallError("Unable to read release metadata.") from exc
    return _PathSnapshot(
        "file",
        data=data,
        mode=stat.S_IMODE(info.st_mode),
    )


def _existing_release_is_valid(path: Path, version: str) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise ReleaseInstallError("Unable to inspect existing release directory.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ReleaseInstallError(
            f"Existing release directory is invalid: {_terminal_literal(str(path))}"
        )
    version_path = path / "VERSION"
    cli_path = path / "bin/agent-rails"
    try:
        version_info = version_path.lstat()
        cli_info = cli_path.lstat()
        installed_version = _read_version_file(version_path)
    except (FileNotFoundError, OSError, UnicodeError, ReleaseInstallError) as exc:
        raise ReleaseInstallError(
            f"Existing release directory is invalid: {_terminal_literal(str(path))}"
        ) from exc
    if (
        not stat.S_ISREG(version_info.st_mode)
        or stat.S_ISLNK(version_info.st_mode)
        or not stat.S_ISREG(cli_info.st_mode)
        or stat.S_ISLNK(cli_info.st_mode)
        or not cli_info.st_mode & 0o111
        or installed_version != version
    ):
        raise ReleaseInstallError(
            f"Existing release directory is invalid: {_terminal_literal(str(path))}"
        )
    return True


def _verify_checksum(archive_path: Path, checksum_path: Path) -> None:
    try:
        payload = checksum_path.read_bytes().decode("utf-8", errors="strict")
    except (OSError, UnicodeError) as exc:
        raise ReleaseInstallError("Invalid checksum file for Agent Rails release.") from exc
    lines = [line for line in payload.splitlines() if line]
    if len(lines) != 1:
        raise ReleaseInstallError("Invalid checksum file for Agent Rails release.")
    match = _CHECKSUM_PATTERN.fullmatch(lines[0])
    if match is None:
        raise ReleaseInstallError("Invalid checksum file for Agent Rails release.")
    try:
        digest = hashlib.sha256()
        with archive_path.open("rb") as archive:
            for block in iter(lambda: archive.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise ReleaseInstallError("Unable to verify Agent Rails release.") from exc
    if not hmac.compare_digest(digest.hexdigest().lower(), match.group(1).lower()):
        raise ReleaseInstallError("Checksum mismatch for Agent Rails release.")


def _assert_download_size(path: Path, limit: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ReleaseInstallError("Downloaded release asset is missing.") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ReleaseInstallError("Downloaded release asset is not a regular file.")
    if info.st_size > limit:
        raise ReleaseInstallError(
            "Release asset exceeds its download size limit."
        )


def _download_release_asset(
    dependencies: ReleaseInstallDependencies,
    url: str,
    destination: Path,
    limit: int,
) -> None:
    if dependencies.download is _default_download:
        _download_url_bounded(url, destination, limit)
    else:
        dependencies.download(url, destination)
    _assert_download_size(destination, limit)


def _materialize_tar_stream(
    archive_path: Path,
    tar_path: Path,
    limit: int,
) -> None:
    copied = 0
    try:
        with gzip.open(archive_path, "rb") as source, tar_path.open("xb") as output:
            while True:
                block = source.read(min(1024 * 1024, limit - copied + 1))
                if not block:
                    break
                copied += len(block)
                if copied > limit:
                    raise ReleaseInstallError(
                        "Release archive exceeds its tar stream resource limit."
                    )
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
    except ReleaseInstallError:
        raise
    except (OSError, EOFError) as exc:
        raise ReleaseInstallError(
            "Unable to decompress Agent Rails release archive."
        ) from exc


def _inspect_archive(
    path: Path,
    requested_version: str,
    limits: ReleaseInstallLimits,
) -> _ArchiveLayout:
    try:
        with tarfile.open(
            path,
            mode="r:",
            encoding="utf-8",
            errors="surrogateescape",
        ) as archive:
            members = _bounded_archive_members(archive, limits)
            if not members:
                raise ReleaseInstallError("Release archive is empty.")
            seen: set[str] = set()
            roots: set[str] = set()
            normalized_members: list[tarfile.TarInfo] = []
            version_member: Optional[tarfile.TarInfo] = None
            cli_member: Optional[tarfile.TarInfo] = None
            root_directory_present = False
            for member in members:
                name, parts = _validated_member_name(member.name)
                if name in seen:
                    raise ReleaseInstallError("Release archive contains duplicate entries.")
                seen.add(name)
                root = parts[0]
                roots.add(root)
                if any(part.casefold() == ".git" for part in parts):
                    raise ReleaseInstallError("Release archive contains forbidden metadata.")
                if not (member.isdir() or member.isreg()):
                    raise ReleaseInstallError("Release archive contains an unsafe entry type.")
                if len(parts) == 1:
                    if not member.isdir():
                        raise ReleaseInstallError("Release archive root is invalid.")
                    root_directory_present = True
                if len(parts) == 2 and parts[1] == "VERSION":
                    version_member = member
                if len(parts) == 3 and parts[1:] == ("bin", "agent-rails"):
                    cli_member = member
                normalized_members.append(member)
            if len(roots) != 1 or not root_directory_present:
                raise ReleaseInstallError(
                    "Release archive must contain one top-level directory."
                )
            root_name = next(iter(roots))
            if not root_name.startswith("agent-rails-"):
                raise ReleaseInstallError("Release archive root is invalid.")
            root_version = root_name[len("agent-rails-") :]
            if not _VERSION_PATTERN.fullmatch(root_version):
                raise ReleaseInstallError("Release archive root is invalid.")
            if version_member is None or not version_member.isreg():
                raise ReleaseInstallError("Release archive VERSION is missing.")
            if version_member.size > 1024:
                raise ReleaseInstallError("Release archive VERSION is invalid.")
            version_stream = archive.extractfile(version_member)
            if version_stream is None:
                raise ReleaseInstallError("Release archive VERSION is invalid.")
            try:
                version_payload = version_stream.read().decode(
                    "utf-8", errors="strict"
                )
            except (OSError, UnicodeError) as exc:
                raise ReleaseInstallError("Release archive VERSION is invalid.") from exc
            version = _parse_version_text(version_payload)
            if root_version != version:
                raise ReleaseInstallError("Release archive root version does not match VERSION.")
            if requested_version != "latest" and requested_version != version:
                raise ReleaseInstallError("Release archive version does not match the request.")
            if (
                cli_member is None
                or not cli_member.isreg()
                or not cli_member.mode & 0o111
            ):
                raise ReleaseInstallError("Release archive CLI is missing or not executable.")
            return _ArchiveLayout(
                version=version,
                root_name=root_name,
                members=tuple(normalized_members),
            )
    except ReleaseInstallError:
        raise
    except (OSError, UnicodeError, tarfile.TarError) as exc:
        raise ReleaseInstallError("Unable to read Agent Rails release archive.") from exc


def _bounded_archive_members(
    archive: tarfile.TarFile,
    limits: ReleaseInstallLimits,
) -> tuple[tarfile.TarInfo, ...]:
    members: list[tarfile.TarInfo] = []
    total_file_bytes = 0
    for member in archive:
        if len(members) >= limits.archive_members:
            raise ReleaseInstallError(
                "Release archive exceeds its member resource limit."
            )
        members.append(member)
        if member.isreg():
            if member.size < 0 or member.size > limits.archive_single_file_bytes:
                raise ReleaseInstallError(
                    "Release archive exceeds its single-file resource limit."
                )
            total_file_bytes += member.size
            if total_file_bytes > limits.archive_total_file_bytes:
                raise ReleaseInstallError(
                    "Release archive exceeds its expanded-size resource limit."
                )
    return tuple(members)


def _validated_member_name(name: str) -> tuple[str, tuple[str, ...]]:
    try:
        name.encode("utf-8", errors="strict")
    except UnicodeError as exc:
        raise ReleaseInstallError("Release archive contains an invalid entry name.") from exc
    if _has_unsafe_terminal_text(name) or name.startswith("/"):
        raise ReleaseInstallError("Release archive contains an unsafe entry name.")
    normalized = name[:-1] if name.endswith("/") else name
    parts = tuple(normalized.split("/"))
    if not normalized or any(part in {"", ".", ".."} for part in parts):
        raise ReleaseInstallError("Release archive contains an unsafe entry name.")
    return normalized, parts


def _extract_archive(
    archive_path: Path,
    layout: _ArchiveLayout,
    releases_dir: Path,
    limits: ReleaseInstallLimits,
) -> Path:
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".agent-rails-{layout.version}.",
            dir=releases_dir,
        )
    )
    try:
        with tarfile.open(
            archive_path,
            mode="r:",
            encoding="utf-8",
            errors="surrogateescape",
        ) as archive:
            members = _bounded_archive_members(archive, limits)
            if tuple(_member_signature(member) for member in members) != tuple(
                _member_signature(member) for member in layout.members
            ):
                raise ReleaseInstallError("Release archive changed during extraction.")
            directories: list[tuple[Path, int]] = []
            root_mode = 0o755
            extracted_file_bytes = 0
            for member in members:
                _, parts = _validated_member_name(member.name)
                relative = parts[1:]
                mode = member.mode & 0o777
                if not relative:
                    root_mode = mode
                    continue
                destination = stage.joinpath(*relative)
                _ensure_beneath(stage, destination)
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                    directories.append((destination, mode))
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                source = archive.extractfile(member)
                if source is None:
                    raise ReleaseInstallError("Release archive file is unreadable.")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(destination, flags, mode or 0o600)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        descriptor = -1
                        copied = _copy_member_with_budget(
                            source,
                            output,
                            expected_size=member.size,
                            single_file_limit=limits.archive_single_file_bytes,
                            remaining_total=(
                                limits.archive_total_file_bytes
                                - extracted_file_bytes
                            ),
                        )
                        extracted_file_bytes += copied
                        output.flush()
                        os.fsync(output.fileno())
                finally:
                    if descriptor >= 0:
                        os.close(descriptor)
                os.chmod(destination, mode)
            for directory, mode in reversed(directories):
                os.chmod(directory, mode)
            os.chmod(stage, root_mode)
        if not _existing_release_is_valid(stage, layout.version):
            raise ReleaseInstallError("Extracted release is invalid.")
        return stage
    except BaseException:
        _remove_path_quietly(stage)
        raise


def _member_signature(member: tarfile.TarInfo) -> tuple[str, bytes, int, int]:
    return (member.name, member.type, member.size, member.mode)


def _copy_member_with_budget(
    source: BinaryIO,
    output: BinaryIO,
    *,
    expected_size: int,
    single_file_limit: int,
    remaining_total: int,
) -> int:
    allowed = min(expected_size, single_file_limit, remaining_total)
    if expected_size < 0 or allowed < expected_size:
        raise ReleaseInstallError(
            "Release archive exceeds its extraction resource limit."
        )
    copied = 0
    while True:
        block = source.read(min(1024 * 1024, allowed - copied + 1))
        if not block:
            break
        if not isinstance(block, bytes) or copied + len(block) > allowed:
            raise ReleaseInstallError(
                "Release archive exceeds its extraction resource limit."
            )
        written = output.write(block)
        if written != len(block):
            raise ReleaseInstallError("Release archive file is unreadable.")
        copied += len(block)
    if copied != expected_size:
        raise ReleaseInstallError("Release archive changed during extraction.")
    return copied


def _ensure_beneath(root: Path, destination: Path) -> None:
    try:
        destination.relative_to(root)
    except ValueError as exc:
        raise ReleaseInstallError("Release archive path escapes staging.") from exc


def _parse_version_text(payload: str) -> str:
    lines = payload.splitlines()
    if len(lines) != 1 or lines[0] != lines[0].strip():
        raise ReleaseInstallError("Release archive VERSION is invalid.")
    version = lines[0]
    if not _VERSION_PATTERN.fullmatch(version):
        raise ReleaseInstallError("Release archive VERSION is invalid.")
    return version


def _read_version_file(path: Path) -> str:
    return _parse_version_text(path.read_text(encoding="utf-8", errors="strict"))


def _stage_private_file(
    directory: Path,
    prefix: str,
    payload: bytes,
) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, dir=directory)
    path = Path(raw_path)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        return path
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        _remove_path_quietly(path)
        raise


def _stage_symlink(directory: Path, prefix: str, target: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=prefix, dir=directory)
    os.close(descriptor)
    path = Path(raw_path)
    path.unlink()
    try:
        path.symlink_to(target)
        return path
    except BaseException:
        _remove_path_quietly(path)
        raise


def _assert_snapshot(path: Path, snapshot: _PathSnapshot) -> None:
    current = (
        _managed_link_snapshot(
            path,
            expected_target=snapshot.link_target,
            current=(path.name == "current"),
        )
        if snapshot.kind in {"absent", "symlink"} and path.name in {"current", "agent-rails"}
        else _metadata_snapshot(path)
    )
    if current != snapshot:
        raise ReleaseInstallError("Release destination changed during installation.")


def _restore_snapshot(
    path: Path,
    snapshot: _PathSnapshot,
    atomic_replace: AtomicReplace,
) -> None:
    if snapshot.kind == "absent":
        _remove_path(path)
        return
    if snapshot.kind == "symlink":
        stage = _stage_symlink(path.parent, f".{path.name}.rollback.", snapshot.link_target)
    elif snapshot.kind == "file":
        stage = _stage_private_file(
            path.parent,
            f".{path.name}.rollback.",
            snapshot.data,
        )
        os.chmod(stage, snapshot.mode)
    else:
        raise ReleaseInstallError("Release rollback state is invalid.")
    try:
        atomic_replace(stage, path)
    finally:
        _remove_path_quietly(stage)


def _remove_path(path: Path) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _remove_path_quietly(path: Path) -> None:
    try:
        _remove_path(path)
    except OSError:
        pass


def _path_exists(path: Path) -> bool:
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def _path_identity(path: Path) -> Optional[tuple[int, int]]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    return (info.st_dev, info.st_ino)


def _with_events(
    error: ReleaseInstallError,
    events: list[ReleaseInstallEvent],
) -> ReleaseInstallError:
    return ReleaseInstallError(
        str(error),
        exit_code=error.exit_code,
        events=tuple(events) + tuple(error.events),
    )


def _out(events: list[ReleaseInstallEvent], text: str) -> None:
    events.append(
        ReleaseInstallEvent(ReleaseInstallEventStream.STDOUT, f"{text}\n")
    )


def _render_events(
    events: Tuple[ReleaseInstallEvent, ...],
    stream: ReleaseInstallEventStream,
) -> str:
    return "".join(event.text for event in events if event.stream is stream)


def _has_unsafe_terminal_text(value: str) -> bool:
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if (
            category in {"Cc", "Cf", "Zl", "Zp"}
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            return True
    return False


def _terminal_literal(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if (
            category in {"Cc", "Cf", "Zl", "Zp"}
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            if character == "\n":
                escaped.append("\\n")
            elif character == "\r":
                escaped.append("\\r")
            elif character == "\t":
                escaped.append("\\t")
            elif codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
    working_directory: Optional[Path] = None,
    dependencies: Optional[ReleaseInstallDependencies] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    previous_umask = os.umask(0o077)
    try:
        return _main(
            argv,
            environment=environment,
            working_directory=working_directory,
            dependencies=dependencies,
            stdout=stdout,
            stderr=stderr,
        )
    finally:
        os.umask(previous_umask)


def _main(
    argv: Optional[Sequence[str]] = None,
    *,
    environment: Optional[Mapping[str, str]] = None,
    working_directory: Optional[Path] = None,
    dependencies: Optional[ReleaseInstallDependencies] = None,
    stdout: Optional[TextIO] = None,
    stderr: Optional[TextIO] = None,
) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    selected_environment = dict(os.environ if environment is None else environment)
    selected_working_directory = Path.cwd() if working_directory is None else working_directory
    selected_stdout = sys.stdout if stdout is None else stdout
    selected_stderr = sys.stderr if stderr is None else stderr
    values: dict[str, str] = {}
    dry_run = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument in {"--help", "-h"}:
            selected_stdout.write(_USAGE)
            return 0
        if argument == "--dry-run":
            dry_run = True
            index += 1
            continue
        destination = {
            "--version": "version",
            "--repository": "repository",
            "--install-root": "install_root",
            "--bin-dir": "bin_dir",
        }.get(argument)
        if destination is None or index + 1 >= len(arguments):
            selected_stderr.write(_USAGE)
            return 2
        values[destination] = arguments[index + 1]
        index += 2
    home = selected_environment.get("HOME", "")
    if not home:
        selected_stderr.write("HOME is required for release installation.\n")
        return 2
    xdg_data_home = selected_environment.get("XDG_DATA_HOME", "")
    default_install = (
        Path(xdg_data_home) / "agent-rails"
        if xdg_data_home
        else Path(home) / ".local/share/agent-rails"
    )
    requested_version = values.get("version", "latest")
    if requested_version.startswith("v"):
        requested_version = requested_version[1:]
    request = ReleaseInstallRequest(
        requested_version=requested_version,
        repository=values.get(
            "repository",
            selected_environment.get(
                "AGENT_RAILS_RELEASE_REPOSITORY",
                "948462448/agent-rails",
            ),
        ),
        install_root=Path(
            values.get(
                "install_root",
                selected_environment.get(
                    "AGENT_RAILS_INSTALL_ROOT",
                    str(default_install),
                ),
            )
        ),
        bin_dir=Path(
            values.get(
                "bin_dir",
                selected_environment.get(
                    "AGENT_RAILS_BIN_DIR",
                    str(Path(home) / ".local/bin"),
                ),
            )
        ),
        dry_run=dry_run,
        working_directory=selected_working_directory,
        environment=selected_environment,
    )
    try:
        with _install_signal_handlers():
            result = install_release(request, dependencies=dependencies)
    except _ReleaseInstallSignal as exc:
        selected_stderr.write(
            f"Release installation interrupted by signal {exc.signum}.\n"
        )
        return 128 + exc.signum
    except ReleaseInstallError as exc:
        selected_stdout.write(exc.stdout)
        selected_stderr.write(exc.stderr)
        selected_stderr.write(f"{exc}\n")
        return exc.exit_code
    except (OSError, UnicodeError, ValueError):
        selected_stderr.write("Unable to install Agent Rails release.\n")
        return 1
    selected_stdout.write(result.stdout)
    selected_stderr.write(result.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
