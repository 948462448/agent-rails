#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import dataclass, replace
import base64
import fcntl
import hashlib
import io
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.release.install import (  # noqa: E402
    ReleaseInstallDependencies,
    ReleaseInstallError,
    ReleaseInstallEventStream,
    ReleaseInstallInputError,
    ReleaseInstallLimits,
    ReleaseInstallRequest,
    install_release,
    main,
    _copy_member_with_budget,
    _default_download,
)
from agent_rails.release import install as release_install_module  # noqa: E402


ARCHIVE_NAME = "agent-rails.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"


@dataclass(frozen=True)
class ArchiveEntry:
    name: str
    data: bytes = b""
    kind: str = "file"
    mode: int = 0o644
    linkname: str = ""


def _tar_info(entry: ArchiveEntry) -> tarfile.TarInfo:
    info = tarfile.TarInfo(entry.name)
    info.mode = entry.mode
    info.mtime = 0
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    if entry.kind == "directory":
        info.type = tarfile.DIRTYPE
        info.size = 0
    elif entry.kind == "symlink":
        info.type = tarfile.SYMTYPE
        info.linkname = entry.linkname
        info.size = 0
    elif entry.kind == "hardlink":
        info.type = tarfile.LNKTYPE
        info.linkname = entry.linkname
        info.size = 0
    elif entry.kind == "fifo":
        info.type = tarfile.FIFOTYPE
        info.size = 0
    elif entry.kind == "character":
        info.type = tarfile.CHRTYPE
        info.devmajor = 1
        info.devminor = 3
        info.size = 0
    else:
        info.type = tarfile.REGTYPE
        info.size = len(entry.data)
    return info


def build_archive(
    version: str = "1.2.3",
    *,
    root_name: Optional[str] = None,
    version_payload: Optional[bytes] = None,
    include_cli: bool = True,
    cli_mode: int = 0o755,
    include_git: bool = False,
    extra_entries: Sequence[ArchiveEntry] = (),
    duplicate_version: bool = False,
    encoding: str = "utf-8",
    archive_format: int = tarfile.USTAR_FORMAT,
) -> bytes:
    root = root_name or f"agent-rails-{version}"
    entries = [
        ArchiveEntry(f"{root}/", kind="directory", mode=0o755),
        ArchiveEntry(f"{root}/bin/", kind="directory", mode=0o755),
        ArchiveEntry(
            f"{root}/VERSION",
            version_payload if version_payload is not None else f"{version}\n".encode(),
        ),
    ]
    if include_cli:
        entries.append(
            ArchiveEntry(
                f"{root}/bin/agent-rails",
                b"#!/bin/sh\nexit 0\n",
                mode=cli_mode,
            )
        )
    entries.append(
        ArchiveEntry(
            f"{root}/README.md",
            b"# release fixture\n",
        )
    )
    if include_git:
        entries.extend(
            (
                ArchiveEntry(f"{root}/.git/", kind="directory", mode=0o755),
                ArchiveEntry(f"{root}/.git/config", b"[core]\n"),
            )
        )
    if duplicate_version:
        entries.append(
            ArchiveEntry(f"{root}/VERSION", f"{version}\n".encode())
        )
    entries.extend(extra_entries)

    output = io.BytesIO()
    with tarfile.open(
        fileobj=output,
        mode="w:gz",
        format=archive_format,
        encoding=encoding,
        errors="strict",
    ) as archive:
        for entry in entries:
            info = _tar_info(entry)
            source = (
                io.BytesIO(entry.data)
                if info.isreg()
                else None
            )
            archive.addfile(info, source)
    return output.getvalue()


def checksum_payload(archive: bytes) -> bytes:
    digest = hashlib.sha256(archive).hexdigest()
    return f"{digest}  {ARCHIVE_NAME}\n".encode("ascii")


class FakeDownloader:
    def __init__(
        self,
        archive: bytes,
        checksum: Optional[bytes] = None,
    ) -> None:
        self.archive = archive
        self.checksum = checksum_payload(archive) if checksum is None else checksum
        self.calls: List[Tuple[str, Path]] = []
        self.failure: Optional[BaseException] = None

    def __call__(self, url: str, destination: Path) -> None:
        self.calls.append((url, destination))
        if self.failure is not None:
            raise self.failure
        payload = self.archive if url.endswith(f"/{ARCHIVE_NAME}") else self.checksum
        destination.write_bytes(payload)


class RecordingAtomicReplace:
    def __init__(self) -> None:
        self.calls: List[Tuple[Path, Path]] = []
        self.fail_once_at: Optional[Path] = None
        self.failed = False
        self.observer: Optional[Callable[[Path, Path], None]] = None

    def __call__(self, source: Path, destination: Path) -> None:
        source = Path(source)
        destination = Path(destination)
        self.calls.append((source, destination))
        if self.observer is not None:
            self.observer(source, destination)
        if (
            self.fail_once_at == destination
            and not self.failed
        ):
            self.failed = True
            raise OSError("injected atomic replace failure")
        os.replace(source, destination)


class ReleaseInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-release-install-"
        )
        self.root = Path(os.path.realpath(self.temporary.name))
        self.working_directory = self.root / "working"
        self.install_root = self.root / "install root"
        self.bin_dir = self.root / "bin dir"
        self.user_home = self.root / "user home"
        for path in (self.working_directory, self.user_home):
            path.mkdir(parents=True)
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": "/usr/bin:/bin",
            "AGENT_RAILS_RELEASE_BASE_URL": "https://mirror.example/releases-base/",
        }
        self.archive = build_archive()
        self.downloader = FakeDownloader(self.archive)
        self.atomic_replace = RecordingAtomicReplace()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        requested_version: str = "1.2.3",
        repository: str = "owner/agent-rails",
        install_root: Optional[Path] = None,
        bin_dir: Optional[Path] = None,
        dry_run: bool = False,
        environment: Optional[Mapping[str, str]] = None,
        working_directory: Optional[Path] = None,
    ) -> ReleaseInstallRequest:
        return ReleaseInstallRequest(
            requested_version=requested_version,
            repository=repository,
            install_root=self.install_root if install_root is None else install_root,
            bin_dir=self.bin_dir if bin_dir is None else bin_dir,
            dry_run=dry_run,
            working_directory=(
                self.working_directory
                if working_directory is None
                else working_directory
            ),
            environment=(
                dict(self.environment)
                if environment is None
                else dict(environment)
            ),
        )

    def dependencies(
        self,
        *,
        downloader: Optional[FakeDownloader] = None,
        atomic_replace: Optional[RecordingAtomicReplace] = None,
        limits: Optional[ReleaseInstallLimits] = None,
    ) -> ReleaseInstallDependencies:
        return ReleaseInstallDependencies(
            download=self.downloader if downloader is None else downloader,
            atomic_replace=(
                self.atomic_replace
                if atomic_replace is None
                else atomic_replace
            ),
            limits=ReleaseInstallLimits() if limits is None else limits,
        )

    def test_request_types_repository_version_and_paths_are_strict(self) -> None:
        invalid = (
            replace(self.request(), requested_version=1),
            replace(self.request(), repository=None),
            replace(self.request(), install_root="install"),
            replace(self.request(), bin_dir="bin"),
            replace(self.request(), dry_run=1),
            replace(self.request(), working_directory="."),
            replace(self.request(), environment={"HOME": 3}),
            self.request(repository="owner"),
            self.request(repository="https://github.com/owner/repo"),
            self.request(repository="owner/../repo"),
            self.request(requested_version="../1.2.3"),
            self.request(requested_version="-1.2.3"),
            self.request(install_root=Path("/")),
            self.request(bin_dir=Path("/")),
            self.request(install_root=Path("bad\ninstall")),
            self.request(bin_dir=Path("bad\x00bin")),
        )

        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(ReleaseInstallInputError) as raised:
                    install_release(request, dependencies=self.dependencies())
                self.assertEqual(raised.exception.exit_code, 2)
        self.assertEqual(self.downloader.calls, [])

    def test_invalid_input_messages_cannot_forge_terminal_lines(self) -> None:
        cases = (
            self.request(repository="owner/repo\x1b]0;title\x07"),
            self.request(requested_version="1.2.3\nspoof"),
            self.request(install_root=Path("install\x85\u202espoof\nnext")),
        )

        for request in cases:
            with self.subTest(request=request):
                with self.assertRaises(ReleaseInstallInputError) as raised:
                    install_release(request, dependencies=self.dependencies())
                message = str(raised.exception)
                for raw in ("\x1b", "\x07", "\x85", "\u202e", "\n"):
                    self.assertNotIn(raw, message)
                self.assertTrue(
                    any(
                        escaped in message
                        for escaped in ("\\x1b", "\\x07", "\\x85", "\\u202e", "\\n")
                    )
                )

    def test_relative_paths_and_versioned_urls_are_canonical_in_dry_run(self) -> None:
        result = install_release(
            self.request(
                install_root=Path("state/install"),
                bin_dir=Path("commands"),
                dry_run=True,
            ),
            dependencies=self.dependencies(),
        )

        expected_install = self.working_directory / "state/install"
        expected_bin = self.working_directory / "commands"
        self.assertEqual(result.install_root, expected_install)
        self.assertEqual(result.bin_dir, expected_bin)
        self.assertEqual(result.version, "1.2.3")
        self.assertTrue(result.dry_run)
        self.assertEqual(self.downloader.calls, [])
        self.assertFalse(expected_install.exists())
        self.assertFalse(expected_bin.exists())
        self.assertIn("Agent Rails Release Install", result.stdout)
        self.assertIn("Version: 1.2.3", result.stdout)
        self.assertIn(
            "https://mirror.example/releases-base/releases/download/v1.2.3/agent-rails.tar.gz",
            result.stdout,
        )
        self.assertIn(str(expected_install / "releases"), result.stdout)
        self.assertIn(str(expected_bin / "agent-rails"), result.stdout)
        self.assertEqual(result.stderr, "")
        self.assertTrue(
            all(
                isinstance(event.stream, ReleaseInstallEventStream)
                for event in result.events
            )
        )

    def test_latest_uses_latest_asset_urls_and_archive_version(self) -> None:
        result = install_release(
            self.request(requested_version="latest"),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(
            tuple(url for url, _ in self.downloader.calls),
            (
                "https://mirror.example/releases-base/releases/latest/download/agent-rails.tar.gz",
                "https://mirror.example/releases-base/releases/latest/download/agent-rails.tar.gz.sha256",
            ),
        )

    def test_success_downloads_verifies_publishes_and_writes_metadata(self) -> None:
        release_dir = self.install_root / "releases/1.2.3"

        def observe_complete_stage(source: Path, destination: Path) -> None:
            if destination != release_dir:
                return
            self.assertEqual(source.parent, release_dir.parent)
            self.assertEqual(
                (source / "VERSION").read_text(encoding="utf-8"),
                "1.2.3\n",
            )
            self.assertTrue(os.access(source / "bin/agent-rails", os.X_OK))

        self.atomic_replace.observer = observe_complete_stage

        result = install_release(
            self.request(),
            dependencies=self.dependencies(),
        )

        current = self.install_root / "current"
        cli = self.bin_dir / "agent-rails"
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.version, "1.2.3")
        self.assertEqual(result.release_dir, release_dir)
        self.assertFalse(result.already_installed)
        self.assertEqual(len(self.downloader.calls), 2)
        self.assertTrue((release_dir / "bin/agent-rails").is_file())
        self.assertTrue(os.access(release_dir / "bin/agent-rails", os.X_OK))
        self.assertEqual(os.readlink(current), "releases/1.2.3")
        self.assertEqual(
            os.readlink(cli),
            str(current / "bin/agent-rails"),
        )
        repository_metadata = self.install_root / "release-repository"
        bin_metadata = self.install_root / "release-bin-dir"
        self.assertEqual(
            repository_metadata.read_text(encoding="utf-8"),
            "owner/agent-rails\n",
        )
        self.assertEqual(
            bin_metadata.read_text(encoding="utf-8"),
            f"{self.bin_dir}\n",
        )
        self.assertEqual(stat.S_IMODE(repository_metadata.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(bin_metadata.stat().st_mode), 0o600)
        self.assertIn("Installed Agent Rails 1.2.3", result.stdout)
        self.assertIn(f"Home: {current}", result.stdout)
        self.assertIn(f"Command: {cli}", result.stdout)
        self.assertIn(f"Add {self.bin_dir} to PATH", result.stdout)
        self.assertEqual(
            tuple(
                destination
                for _, destination in self.atomic_replace.calls
                if destination == release_dir
            ),
            (release_dir,),
        )
        leftovers = tuple(
            path
            for path in self.install_root.rglob("*")
            if path.name.startswith(".agent-rails-")
            or path.name.startswith(".current.")
            or path.name.startswith(".release-")
        )
        self.assertEqual(leftovers, ())

    def test_checksum_syntax_digest_and_utf8_fail_before_install_mutation(self) -> None:
        cases = (
            (b"not-a-checksum\n", "invalid"),
            (f"{'0' * 64}  {ARCHIVE_NAME}\n".encode(), "mismatch"),
            (b"\xff\xfe\n", "non-utf8"),
            (f"{'a' * 64}  other.tar.gz\n".encode(), "wrong-name"),
        )

        for checksum, label in cases:
            with self.subTest(case=label):
                install_root = self.root / f"checksum-{label}/install"
                bin_dir = self.root / f"checksum-{label}/bin"
                downloader = FakeDownloader(self.archive, checksum)
                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(downloader=downloader),
                    )
                self.assertEqual(raised.exception.exit_code, 1)
                self.assertFalse((install_root / "current").exists())
                self.assertFalse((bin_dir / "agent-rails").exists())
                self.assertFalse((install_root / "releases/1.2.3").exists())
                message = str(raised.exception)
                self.assertNotIn("\udcff", message)
                self.assertNotIn("\ufffd", message)

    def test_download_failure_is_generic_terminal_safe_and_non_mutating(self) -> None:
        dangerous = "network-\x1b]0;title\x07-\x85-\u202e-token=secret"
        self.downloader.failure = OSError(dangerous)

        with self.assertRaises(ReleaseInstallError) as raised:
            install_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        error = raised.exception
        self.assertEqual(error.exit_code, 1)
        self.assertNotIn("secret", str(error))
        self.assertNotIn("\x1b", str(error))
        self.assertNotIn("\u202e", str(error))
        self.assertFalse((self.install_root / "current").exists())

    def test_downloaded_archive_and_checksum_are_bounded_before_parsing(self) -> None:
        cases = (
            (
                "archive",
                FakeDownloader(self.archive),
                replace(
                    ReleaseInstallLimits(),
                    archive_download_bytes=len(self.archive) - 1,
                ),
            ),
            (
                "checksum",
                FakeDownloader(self.archive, b"x" * 65),
                replace(
                    ReleaseInstallLimits(),
                    checksum_download_bytes=64,
                ),
            ),
        )

        for label, downloader, limits in cases:
            with self.subTest(asset=label):
                install_root = self.root / f"bounded-{label}/install"
                bin_dir = self.root / f"bounded-{label}/bin"
                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(
                            downloader=downloader,
                            limits=limits,
                        ),
                    )
                self.assertIn("size limit", str(raised.exception).lower())
                self.assertFalse((install_root / "current").exists())
                self.assertFalse((bin_dir / "agent-rails").exists())

    def test_default_downloader_stops_before_writing_past_checksum_limit(self) -> None:
        payload = b"x" * (ReleaseInstallLimits().checksum_download_bytes + 1)
        url = "data:application/octet-stream;base64," + base64.b64encode(
            payload
        ).decode("ascii")
        destination = self.root / CHECKSUM_NAME

        with self.assertRaises(ReleaseInstallError) as raised:
            _default_download(url, destination)

        self.assertIn("download size limit", str(raised.exception).lower())
        self.assertLessEqual(
            destination.stat().st_size,
            ReleaseInstallLimits().checksum_download_bytes,
        )
        self.assertFalse((self.bin_dir / "agent-rails").exists())

    def test_archive_rejects_traversal_multiple_roots_links_specials_and_git(self) -> None:
        root = "agent-rails-1.2.3"
        cases = (
            (
                "absolute",
                build_archive(extra_entries=(ArchiveEntry("/absolute-escape", b"x"),)),
            ),
            (
                "dotdot",
                build_archive(
                    extra_entries=(ArchiveEntry(f"{root}/../../escape", b"x"),)
                ),
            ),
            (
                "inner-dotdot",
                build_archive(
                    extra_entries=(ArchiveEntry(f"{root}/sub/../escape", b"x"),)
                ),
            ),
            (
                "multiple-roots",
                build_archive(
                    extra_entries=(ArchiveEntry("other-root/file", b"x"),)
                ),
            ),
            (
                "symlink",
                build_archive(
                    extra_entries=(
                        ArchiveEntry(
                            f"{root}/link",
                            kind="symlink",
                            linkname="../../escape",
                        ),
                    )
                ),
            ),
            (
                "hardlink",
                build_archive(
                    extra_entries=(
                        ArchiveEntry(
                            f"{root}/hard",
                            kind="hardlink",
                            linkname=f"{root}/VERSION",
                        ),
                    )
                ),
            ),
            (
                "fifo",
                build_archive(
                    extra_entries=(ArchiveEntry(f"{root}/pipe", kind="fifo"),)
                ),
            ),
            (
                "device",
                build_archive(
                    extra_entries=(
                        ArchiveEntry(f"{root}/device", kind="character"),
                    )
                ),
            ),
            ("duplicate", build_archive(duplicate_version=True)),
            ("git-metadata", build_archive(include_git=True)),
            (
                "case-folded-git-metadata",
                build_archive(
                    extra_entries=(ArchiveEntry(f"{root}/.GIT/config", b"[core]\n"),)
                ),
            ),
            (
                "terminal-control",
                build_archive(
                    extra_entries=(ArchiveEntry(f"{root}/bad-\x1b-name", b"x"),)
                ),
            ),
        )

        for label, archive in cases:
            with self.subTest(case=label):
                install_root = self.root / f"archive-{label}/install"
                bin_dir = self.root / f"archive-{label}/bin"
                downloader = FakeDownloader(archive)
                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(downloader=downloader),
                    )
                self.assertEqual(raised.exception.exit_code, 1)
                self.assertFalse((install_root / "current").exists())
                self.assertFalse((bin_dir / "agent-rails").exists())
                self.assertFalse((self.root / "escape").exists())
                self.assertNotIn("\x1b", str(raised.exception))

    def test_archive_member_single_file_and_total_expansion_are_bounded(self) -> None:
        cases = (
            (
                "members",
                replace(ReleaseInstallLimits(), archive_members=4),
            ),
            (
                "single-file",
                replace(
                    ReleaseInstallLimits(),
                    archive_single_file_bytes=16,
                ),
            ),
            (
                "total-size",
                replace(
                    ReleaseInstallLimits(),
                    archive_total_file_bytes=32,
                ),
            ),
        )

        for label, limits in cases:
            with self.subTest(limit=label):
                install_root = self.root / f"archive-budget-{label}/install"
                bin_dir = self.root / f"archive-budget-{label}/bin"
                downloader = FakeDownloader(self.archive)
                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(
                            downloader=downloader,
                            limits=limits,
                        ),
                    )
                self.assertIn("resource limit", str(raised.exception).lower())
                self.assertFalse((install_root / "current").exists())
                self.assertFalse((bin_dir / "agent-rails").exists())

    def test_compressed_tar_stream_is_bounded_before_longname_metadata_parsing(self) -> None:
        root = "agent-rails-1.2.3"
        archive = build_archive(
            extra_entries=(
                ArchiveEntry(f"{root}/{'a' * (256 * 1024)}", b"x"),
            ),
            archive_format=tarfile.GNU_FORMAT,
        )
        downloader = FakeDownloader(archive)
        limits = replace(
            ReleaseInstallLimits(),
            archive_stream_bytes=64 * 1024,
        )

        with self.assertRaises(ReleaseInstallError) as raised:
            install_release(
                self.request(),
                dependencies=self.dependencies(
                    downloader=downloader,
                    limits=limits,
                ),
            )

        self.assertIn("tar stream resource limit", str(raised.exception).lower())
        self.assertFalse((self.install_root / "current").exists())

    def test_member_copy_rechecks_actual_bytes_against_extraction_budget(self) -> None:
        output = io.BytesIO()

        with self.assertRaises(ReleaseInstallError) as raised:
            _copy_member_with_budget(
                io.BytesIO(b"abcde"),
                output,
                expected_size=4,
                single_file_limit=4,
                remaining_total=4,
            )

        self.assertIn("extraction resource limit", str(raised.exception).lower())
        self.assertLessEqual(len(output.getvalue()), 4)

    def test_keyboard_interrupt_during_extraction_cleans_staging(self) -> None:
        with mock.patch(
            "agent_rails.release.install._copy_member_with_budget",
            side_effect=KeyboardInterrupt,
        ):
            with self.assertRaises(KeyboardInterrupt):
                install_release(
                    self.request(),
                    dependencies=self.dependencies(),
                )

        releases_dir = self.install_root / "releases"
        leftovers = (
            tuple(releases_dir.iterdir())
            if releases_dir.is_dir()
            else ()
        )
        self.assertEqual(leftovers, ())
        self.assertFalse((self.install_root / "current").exists())
        self.assertFalse((self.bin_dir / "agent-rails").exists())

    def test_commit_setup_failure_cleans_prepared_release_stage(self) -> None:
        def invalidate_bin_directory(url: str, destination: Path) -> None:
            self.downloader(url, destination)
            if url.endswith(f"/{CHECKSUM_NAME}"):
                (self.bin_dir / ".agent-rails.install.lock").unlink()
                self.bin_dir.rmdir()
                self.bin_dir.write_text("became-a-file\n", encoding="utf-8")

        with self.assertRaises(ReleaseInstallError):
            install_release(
                self.request(),
                dependencies=ReleaseInstallDependencies(
                    download=invalidate_bin_directory,
                    atomic_replace=self.atomic_replace,
                ),
            )

        releases_dir = self.install_root / "releases"
        leftovers = (
            tuple(releases_dir.iterdir())
            if releases_dir.is_dir()
            else ()
        )
        self.assertEqual(leftovers, ())
        self.assertEqual(self.bin_dir.read_text(encoding="utf-8"), "became-a-file\n")

    def test_keyboard_interrupt_during_commit_rolls_back_published_state(self) -> None:
        def interrupt_cli_publish(source: Path, destination: Path) -> None:
            os.replace(source, destination)
            if destination == self.bin_dir / "agent-rails":
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            install_release(
                self.request(),
                dependencies=ReleaseInstallDependencies(
                    download=self.downloader,
                    atomic_replace=interrupt_cli_publish,
                ),
            )

        self.assertFalse((self.install_root / "releases/1.2.3").exists())
        self.assertFalse(os.path.lexists(self.install_root / "release-repository"))
        self.assertFalse(os.path.lexists(self.install_root / "release-bin-dir"))
        self.assertFalse(os.path.lexists(self.install_root / "current"))
        self.assertFalse(os.path.lexists(self.bin_dir / "agent-rails"))
        leftovers = tuple(
            path
            for parent in (self.install_root, self.bin_dir)
            if parent.is_dir()
            for path in parent.iterdir()
            if path.name.startswith(".")
            and path.name not in {".install.lock", ".agent-rails.install.lock"}
        )
        self.assertEqual(leftovers, ())

    def test_standalone_sigterm_rolls_back_and_exits_with_signal_status(self) -> None:
        archive_path = self.root / ARCHIVE_NAME
        checksum_path = self.root / CHECKSUM_NAME
        archive_path.write_bytes(self.archive)
        checksum_path.write_bytes(checksum_payload(self.archive))
        worker = self.root / "signal-installer.py"
        worker.write_text(
            """import os
from pathlib import Path
import runpy
import signal

api = runpy.run_path(os.environ["INSTALLER_SOURCE"])
bin_dir = Path(os.environ["BIN_DIR"])

def download(url, destination):
    source = os.environ["ARCHIVE"] if url.endswith(".tar.gz") else os.environ["CHECKSUM"]
    destination.write_bytes(Path(source).read_bytes())

def replace(source, destination):
    os.replace(source, destination)
    if Path(destination) == bin_dir / "agent-rails":
        os.kill(os.getpid(), signal.SIGTERM)

dependencies = api["ReleaseInstallDependencies"](
    download=download,
    atomic_replace=replace,
)
raise SystemExit(api["main"](
    (
        "--version", "1.2.3",
        "--install-root", os.environ["INSTALL_ROOT"],
        "--bin-dir", os.environ["BIN_DIR"],
    ),
    environment={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"},
    working_directory=Path.cwd(),
    dependencies=dependencies,
))
""",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(
            {
                "INSTALLER_SOURCE": str(ROOT / "src/agent_rails/release/install.py"),
                "INSTALL_ROOT": str(self.install_root),
                "BIN_DIR": str(self.bin_dir),
                "ARCHIVE": str(archive_path),
                "CHECKSUM": str(checksum_path),
                "HOME": str(self.user_home),
            }
        )

        completed = subprocess.run(
            (sys.executable, "-I", str(worker)),
            cwd=self.working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 128 + signal.SIGTERM)
        self.assertFalse((self.install_root / "releases/1.2.3").exists())
        self.assertFalse(os.path.lexists(self.install_root / "current"))
        self.assertFalse(os.path.lexists(self.bin_dir / "agent-rails"))

    def test_archive_rejects_non_utf8_member_names_without_terminal_leak(self) -> None:
        root = "agent-rails-1.2.3"
        archive = build_archive(
            extra_entries=(ArchiveEntry(f"{root}/bad-\xff", b"x"),),
            encoding="latin-1",
        )
        downloader = FakeDownloader(archive)

        with self.assertRaises(ReleaseInstallError) as raised:
            install_release(
                self.request(),
                dependencies=self.dependencies(downloader=downloader),
            )

        message = str(raised.exception)
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertNotIn("\udcff", message)
        self.assertNotIn("ÿ", message)
        self.assertFalse((self.install_root / "current").exists())

    def test_archive_root_version_cli_and_version_file_layout_are_strict(self) -> None:
        cases = (
            ("bad-root", build_archive(root_name="package-1.2.3")),
            (
                "root-version-mismatch",
                build_archive(version="1.2.3", root_name="agent-rails-9.9.9"),
            ),
            ("requested-mismatch", build_archive(version="2.0.0")),
            (
                "invalid-version",
                build_archive(version_payload=b"../bad\n"),
            ),
            (
                "non-utf8-version",
                build_archive(version_payload=b"1.2.3-\xff\n"),
            ),
            ("missing-cli", build_archive(include_cli=False)),
            ("non-executable-cli", build_archive(cli_mode=0o644)),
        )

        for label, archive in cases:
            with self.subTest(case=label):
                install_root = self.root / f"layout-{label}/install"
                bin_dir = self.root / f"layout-{label}/bin"
                downloader = FakeDownloader(archive)
                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(downloader=downloader),
                    )
                self.assertEqual(raised.exception.exit_code, 1)
                self.assertFalse((install_root / "current").exists())
                self.assertFalse((bin_dir / "agent-rails").exists())
                message = str(raised.exception)
                self.assertNotIn("\udcff", message)
                self.assertNotIn("\ufffd", message)

    def test_existing_valid_release_is_reused_without_overwrite(self) -> None:
        first = install_release(
            self.request(),
            dependencies=self.dependencies(),
        )
        sentinel = first.release_dir / "user-sentinel"
        sentinel.write_text("preserve\n", encoding="utf-8")
        second_downloader = FakeDownloader(self.archive)
        second_replace = RecordingAtomicReplace()

        second = install_release(
            self.request(repository="another/repository"),
            dependencies=self.dependencies(
                downloader=second_downloader,
                atomic_replace=second_replace,
            ),
        )

        self.assertTrue(second.already_installed)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertIn("Agent Rails 1.2.3 is already installed.", second.stdout)
        self.assertNotIn(
            second.release_dir,
            tuple(destination for _, destination in second_replace.calls),
        )
        self.assertEqual(
            (self.install_root / "release-repository").read_text(encoding="utf-8"),
            "another/repository\n",
        )

    def test_existing_invalid_or_symlinked_release_directory_is_rejected(self) -> None:
        releases = self.install_root / "releases"
        releases.mkdir(parents=True)
        invalid = releases / "1.2.3"
        invalid.mkdir()
        (invalid / "VERSION").write_text("other\n", encoding="utf-8")

        with self.assertRaises(ReleaseInstallError):
            install_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        self.assertFalse((self.install_root / "current").exists())
        self.assertFalse((self.bin_dir / "agent-rails").exists())
        for child in tuple(invalid.iterdir()):
            child.unlink()
        invalid.rmdir()
        outside = self.root / "outside-release"
        (outside / "bin").mkdir(parents=True)
        (outside / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        cli = outside / "bin/agent-rails"
        cli.write_text("#!/bin/sh\n", encoding="utf-8")
        cli.chmod(0o755)
        invalid.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(ReleaseInstallError):
            install_release(
                self.request(),
                dependencies=self.dependencies(),
            )
        self.assertTrue(invalid.is_symlink())
        self.assertFalse((self.install_root / "current").exists())

    def test_current_and_cli_paths_require_managed_symlink_ownership(self) -> None:
        current = self.install_root / "current"
        cli = self.bin_dir / "agent-rails"
        cases = ("current-file", "current-link", "cli-file", "cli-link")

        for label in cases:
            with self.subTest(case=label):
                install_root = self.root / f"ownership-{label}/install"
                bin_dir = self.root / f"ownership-{label}/bin"
                current = install_root / "current"
                cli = bin_dir / "agent-rails"
                install_root.mkdir(parents=True)
                bin_dir.mkdir(parents=True)
                target = current if label.startswith("current") else cli
                if label.endswith("file"):
                    target.write_text("user-owned\n", encoding="utf-8")
                else:
                    target.symlink_to(self.root / "unrelated-user-target")
                downloader = FakeDownloader(self.archive)

                with self.assertRaises(ReleaseInstallError) as raised:
                    install_release(
                        self.request(
                            install_root=install_root,
                            bin_dir=bin_dir,
                        ),
                        dependencies=self.dependencies(downloader=downloader),
                    )

                self.assertEqual(raised.exception.exit_code, 1)
                self.assertEqual(downloader.calls, [])
                if target.is_symlink():
                    self.assertEqual(
                        os.readlink(target),
                        str(self.root / "unrelated-user-target"),
                    )
                else:
                    self.assertEqual(
                        target.read_text(encoding="utf-8"),
                        "user-owned\n",
                    )

    def test_commit_failure_rolls_back_release_links_and_metadata(self) -> None:
        old_release = self.install_root / "releases/1.0.0"
        (old_release / "bin").mkdir(parents=True)
        (old_release / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        old_cli = old_release / "bin/agent-rails"
        old_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        old_cli.chmod(0o755)
        self.bin_dir.mkdir(parents=True)
        current = self.install_root / "current"
        current.symlink_to("releases/1.0.0")
        cli = self.bin_dir / "agent-rails"
        cli.symlink_to(current / "bin/agent-rails")
        repository_metadata = self.install_root / "release-repository"
        bin_metadata = self.install_root / "release-bin-dir"
        repository_metadata.write_text("old/repository\n", encoding="utf-8")
        bin_metadata.write_text("/old/bin\n", encoding="utf-8")
        repository_metadata.chmod(0o600)
        bin_metadata.chmod(0o600)
        self.atomic_replace.fail_once_at = cli

        with self.assertRaises(ReleaseInstallError) as raised:
            install_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        self.assertEqual(raised.exception.exit_code, 1)
        self.assertEqual(os.readlink(current), "releases/1.0.0")
        self.assertEqual(os.readlink(cli), str(current / "bin/agent-rails"))
        self.assertEqual(
            repository_metadata.read_text(encoding="utf-8"),
            "old/repository\n",
        )
        self.assertEqual(
            bin_metadata.read_text(encoding="utf-8"),
            "/old/bin\n",
        )
        self.assertTrue(old_release.is_dir())
        self.assertFalse((self.install_root / "releases/1.2.3").exists())
        leftovers = tuple(
            path
            for path in self.install_root.rglob("*")
            if path.name.startswith(".agent-rails-")
            or path.name.startswith(".current.")
            or path.name.startswith(".release-")
        )
        self.assertEqual(leftovers, ())
        self.assertNotIn("Installed Agent Rails", raised.exception.stdout)

    def test_failed_release_publish_never_deletes_a_different_destination(self) -> None:
        release_dir = self.install_root / "releases/1.2.3"
        replacing = RecordingAtomicReplace()
        replacing.fail_once_at = release_dir

        def create_concurrent_destination(source: Path, destination: Path) -> None:
            del source
            if destination == release_dir:
                destination.mkdir()
                (destination / "winner-sentinel").write_text(
                    "preserve\n",
                    encoding="utf-8",
                )

        replacing.observer = create_concurrent_destination
        with self.assertRaises(ReleaseInstallError):
            install_release(
                self.request(),
                dependencies=self.dependencies(atomic_replace=replacing),
            )

        self.assertEqual(
            (release_dir / "winner-sentinel").read_text(encoding="utf-8"),
            "preserve\n",
        )
        self.assertFalse(os.path.lexists(self.install_root / "current"))
        self.assertFalse(os.path.lexists(self.bin_dir / "agent-rails"))

    def test_failed_link_publish_never_deletes_a_different_destination(self) -> None:
        current = self.install_root / "current"
        replacing = RecordingAtomicReplace()
        replacing.fail_once_at = current

        def create_concurrent_link(source: Path, destination: Path) -> None:
            del source
            if destination == current:
                destination.symlink_to("releases/9.9.9")

        replacing.observer = create_concurrent_link
        with self.assertRaises(ReleaseInstallError):
            install_release(
                self.request(),
                dependencies=self.dependencies(atomic_replace=replacing),
            )

        self.assertTrue(current.is_symlink())
        self.assertEqual(os.readlink(current), "releases/9.9.9")
        self.assertFalse(os.path.lexists(self.bin_dir / "agent-rails"))

    def test_install_root_lock_serializes_standalone_installers(self) -> None:
        asset_root = self.root / "release-assets"
        asset_dir = asset_root / "releases/download/v1.2.3"
        asset_dir.mkdir(parents=True)
        (asset_dir / ARCHIVE_NAME).write_bytes(self.archive)
        (asset_dir / CHECKSUM_NAME).write_bytes(checksum_payload(self.archive))
        self.install_root.mkdir(parents=True)
        lock_path = self.install_root / ".install.lock"
        lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        environment = dict(os.environ)
        environment.update(self.environment)
        environment["AGENT_RAILS_RELEASE_BASE_URL"] = asset_root.as_uri()
        command = (
            sys.executable,
            "-I",
            str(ROOT / "src/agent_rails/release/install.py"),
            "--version",
            "1.2.3",
            "--install-root",
            str(self.install_root),
            "--bin-dir",
            str(self.bin_dir),
        )
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        blocked_without_mutation = False
        try:
            time.sleep(0.4)
            blocked_without_mutation = (
                process.poll() is None
                and not (self.install_root / "current").exists()
                and not (self.bin_dir / "agent-rails").exists()
            )
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        stdout, stderr = process.communicate(timeout=10)

        self.assertTrue(blocked_without_mutation)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertIn("Installed Agent Rails 1.2.3", stdout)
        self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)
        self.assertTrue((self.install_root / "current").is_symlink())
        self.assertTrue((self.bin_dir / "agent-rails").is_symlink())

    def test_shared_bin_lock_serializes_different_install_roots(self) -> None:
        asset_root = self.root / "shared-bin-assets"
        asset_dir = asset_root / "releases/download/v1.2.3"
        asset_dir.mkdir(parents=True)
        (asset_dir / ARCHIVE_NAME).write_bytes(self.archive)
        (asset_dir / CHECKSUM_NAME).write_bytes(checksum_payload(self.archive))
        self.bin_dir.mkdir(parents=True)
        lock_path = self.bin_dir / ".agent-rails.install.lock"
        lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        other_install_root = self.root / "other-install-root"
        environment = dict(os.environ)
        environment.update(self.environment)
        environment["AGENT_RAILS_RELEASE_BASE_URL"] = asset_root.as_uri()
        process = subprocess.Popen(
            (
                sys.executable,
                "-I",
                str(ROOT / "src/agent_rails/release/install.py"),
                "--version",
                "1.2.3",
                "--install-root",
                str(other_install_root),
                "--bin-dir",
                str(self.bin_dir),
            ),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        blocked_without_mutation = False
        try:
            time.sleep(0.4)
            blocked_without_mutation = (
                process.poll() is None
                and not os.path.lexists(self.bin_dir / "agent-rails")
                and not os.path.lexists(other_install_root / "current")
            )
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        _, stderr = process.communicate(timeout=10)

        self.assertTrue(blocked_without_mutation)
        self.assertEqual(process.returncode, 0, stderr)
        self.assertTrue((self.bin_dir / "agent-rails").is_symlink())

    def test_serialized_failed_installer_preserves_concurrent_winner_state(self) -> None:
        self.install_root.mkdir(parents=True)
        self.bin_dir.mkdir(parents=True)
        lock_path = self.install_root / ".install.lock"
        lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
        archive_path = self.root / ARCHIVE_NAME
        checksum_path = self.root / CHECKSUM_NAME
        archive_path.write_bytes(self.archive)
        checksum_path.write_bytes(checksum_payload(self.archive))
        worker = self.root / "failing-installer.py"
        worker.write_text(
            """import os
from pathlib import Path
import runpy
import sys

api = runpy.run_path(os.environ["INSTALLER_SOURCE"])
install_root = Path(os.environ["INSTALL_ROOT"])
bin_dir = Path(os.environ["BIN_DIR"])

def download(url, destination):
    source = os.environ["ARCHIVE"] if url.endswith(".tar.gz") else os.environ["CHECKSUM"]
    destination.write_bytes(Path(source).read_bytes())

def replace(source, destination):
    if Path(destination) == bin_dir / "agent-rails":
        raise OSError("injected contender failure")
    os.replace(source, destination)

request = api["ReleaseInstallRequest"](
    requested_version="1.2.3",
    repository="owner/agent-rails",
    install_root=install_root,
    bin_dir=bin_dir,
    dry_run=False,
    working_directory=Path.cwd(),
    environment={"HOME": os.environ["HOME"], "PATH": "/usr/bin:/bin"},
)
dependencies = api["ReleaseInstallDependencies"](
    download=download,
    atomic_replace=replace,
)
try:
    api["install_release"](request, dependencies=dependencies)
except api["ReleaseInstallError"]:
    raise SystemExit(23)
raise SystemExit(99)
""",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(
            {
                "INSTALLER_SOURCE": str(ROOT / "src/agent_rails/release/install.py"),
                "INSTALL_ROOT": str(self.install_root),
                "BIN_DIR": str(self.bin_dir),
                "ARCHIVE": str(archive_path),
                "CHECKSUM": str(checksum_path),
                "HOME": str(self.user_home),
            }
        )
        process = subprocess.Popen(
            (sys.executable, "-I", str(worker)),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        blocked = False
        try:
            time.sleep(0.4)
            blocked = process.poll() is None
            winner = self.install_root / "releases/1.0.0"
            (winner / "bin").mkdir(parents=True)
            (winner / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            winner_cli = winner / "bin/agent-rails"
            winner_cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            winner_cli.chmod(0o755)
            (self.install_root / "current").symlink_to("releases/1.0.0")
            (self.bin_dir / "agent-rails").symlink_to(
                self.install_root / "current/bin/agent-rails"
            )
            (self.install_root / "release-repository").write_text(
                "winner/repository\n",
                encoding="utf-8",
            )
            (self.install_root / "release-bin-dir").write_text(
                f"{self.bin_dir}\n",
                encoding="utf-8",
            )
        finally:
            fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            os.close(lock_descriptor)
        _, stderr = process.communicate(timeout=10)

        self.assertTrue(blocked)
        self.assertEqual(process.returncode, 23, stderr)
        self.assertEqual(
            os.readlink(self.install_root / "current"),
            "releases/1.0.0",
        )
        self.assertEqual(
            (self.install_root / "release-repository").read_text(encoding="utf-8"),
            "winner/repository\n",
        )
        self.assertEqual(
            (self.install_root / "release-bin-dir").read_text(encoding="utf-8"),
            f"{self.bin_dir}\n",
        )
        self.assertEqual(
            os.readlink(self.bin_dir / "agent-rails"),
            str(self.install_root / "current/bin/agent-rails"),
        )
        self.assertTrue((self.install_root / "releases/1.0.0").is_dir())
        self.assertFalse((self.install_root / "releases/1.2.3").exists())

    def test_standalone_main_uses_environment_defaults_and_cli_overrides(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = dict(self.environment)
        environment.update(
            {
                "AGENT_RAILS_RELEASE_REPOSITORY": "environment/repository",
                "AGENT_RAILS_INSTALL_ROOT": str(self.root / "environment-install"),
                "AGENT_RAILS_BIN_DIR": str(self.root / "environment-bin"),
            }
        )

        exit_code = main(
            (
                "--version",
                "v2.0.0",
                "--repository",
                "argument/repository",
                "--install-root",
                "relative-install",
                "--bin-dir",
                "relative-bin",
                "--dry-run",
            ),
            environment=environment,
            working_directory=self.working_directory,
            dependencies=self.dependencies(),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 0)
        self.assertEqual(stderr.getvalue(), "")
        self.assertIn("Repository: argument/repository", stdout.getvalue())
        self.assertIn("Version: 2.0.0", stdout.getvalue())
        self.assertIn(
            str(self.working_directory / "relative-install/releases"),
            stdout.getvalue(),
        )
        self.assertIn(
            str(self.working_directory / "relative-bin/agent-rails"),
            stdout.getvalue(),
        )
        self.assertEqual(self.downloader.calls, [])

    def test_standalone_main_returns_two_for_bad_arguments(self) -> None:
        stdout = io.StringIO()
        stderr = io.StringIO()

        exit_code = main(
            ("--repository", "invalid"),
            environment=self.environment,
            working_directory=self.working_directory,
            dependencies=self.dependencies(),
            stdout=stdout,
            stderr=stderr,
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Invalid GitHub repository", stderr.getvalue())
        self.assertEqual(self.downloader.calls, [])

    def test_standalone_main_owns_and_restores_private_umask(self) -> None:
        observed: List[int] = []

        def current_umask() -> int:
            value = os.umask(0)
            os.umask(value)
            return value

        def observe_install(*args: object, **kwargs: object) -> object:
            observed.append(current_umask())
            raise ReleaseInstallError("injected stop")

        before = current_umask()
        with mock.patch.object(
            release_install_module,
            "install_release",
            side_effect=observe_install,
        ):
            exit_code = main(
                ("--dry-run",),
                environment=self.environment,
                working_directory=self.working_directory,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )

        self.assertEqual(exit_code, 1)
        self.assertEqual(observed, [0o077])
        self.assertEqual(current_umask(), before)

    def test_cold_start_shell_is_only_a_python_bootstrap(self) -> None:
        shell = ROOT / "scripts/agent-release-install.sh"
        source = shell.read_text(encoding="utf-8")
        line_count = len(source.splitlines())

        self.assertLessEqual(line_count, 40)
        self.assertIn("python3 -I", source)
        self.assertNotIn("python3 -E", source)
        self.assertTrue(
            "agent_rails.release.install" in source
            or "release-install" in source
            or "release_install.py" in source
        )
        for forbidden in (
            "sha256sum",
            "shasum",
            "tar -",
            "replace_symlink",
            "release_stage",
            "current_tmp",
            "cli_tmp",
            "ln -s",
            "mv -T",
            "mv -h",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_cold_start_shell_isolates_sibling_and_environment_imports(self) -> None:
        standalone = self.root / "standalone-installer"
        standalone.mkdir()
        shell = standalone / "install.sh"
        installer = standalone / "release_install.py"
        shutil.copy2(ROOT / "scripts/agent-release-install.sh", shell)
        shutil.copy2(ROOT / "src/agent_rails/release/install.py", installer)
        marker = self.root / "shadow-imported"
        shadow = standalone / "hashlib.py"
        shadow.write_text(
            "import os\n"
            "open(os.environ['SHADOW_MARKER'], 'w').write('loaded')\n"
            "raise RuntimeError('shadow import executed')\n",
            encoding="utf-8",
        )
        environment = dict(os.environ)
        environment.update(self.environment)
        environment.update(
            {
                "PYTHONPATH": str(standalone),
                "PYTHONUSERBASE": str(self.root / "hostile-user-base"),
                "SHADOW_MARKER": str(marker),
            }
        )

        completed = subprocess.run(
            ("bash", str(shell), "--dry-run"),
            cwd=self.working_directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Agent Rails Release Install", completed.stdout)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
