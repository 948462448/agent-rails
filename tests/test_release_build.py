#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
from typing import List, Mapping, Optional, Sequence, Tuple
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.release.build import (  # noqa: E402
    ReleaseBuildCommandResult,
    ReleaseBuildDependencies,
    ReleaseBuildError,
    ReleaseBuildInputError,
    ReleaseBuildRecoveryError,
    ReleaseBuildRequest,
    build_release,
)
from agent_rails.release import build as release_build_module  # noqa: E402


VERSION = "1.2.3"
ARCHIVE_NAME = "agent-rails.tar.gz"
CHECKSUM_NAME = f"{ARCHIVE_NAME}.sha256"
ASSET_NAMES = (
    ARCHIVE_NAME,
    CHECKSUM_NAME,
    "install.sh",
    "release_install.py",
)
TRACKED_PATHS = (
    "src/agent_rails/__init__.py",
    "src/agent_rails/cli.py",
    "src/agent_rails/public_cli.py",
    "src/agent_rails/release/install.py",
    "src/agent_rails/setup_application.py",
    "README.md",
    "VERSION",
    "docs/guide.md",
    "scripts/agent-release-install.sh",
    "scripts/agent-python-cli.py",
    "bin/agent-rails",
)


def _nul_paths(paths: Sequence[str]) -> bytes:
    return b"".join(os.fsencode(path) + b"\0" for path in paths)


class FakeRunner:
    """Return Git porcelain output without invoking a local Git process."""

    def __init__(self, source_root: Path, selected_paths: Sequence[str]) -> None:
        self.source_root = source_root
        self.selected_paths = tuple(selected_paths)
        self.calls: List[object] = []
        self.list_files_exit_code = 0
        self.list_files_stderr = b""

    def __call__(self, command: object) -> ReleaseBuildCommandResult:
        self.calls.append(command)
        argv = tuple(getattr(command, "argv"))
        if "rev-parse" in argv:
            return ReleaseBuildCommandResult(
                exit_code=0,
                stdout=os.fsencode(self.source_root) + b"\n",
            )
        if "ls-files" in argv:
            return ReleaseBuildCommandResult(
                exit_code=self.list_files_exit_code,
                stdout=(
                    _nul_paths(self.selected_paths)
                    if self.list_files_exit_code == 0
                    else b""
                ),
                stderr=self.list_files_stderr,
            )
        raise AssertionError(f"Unexpected release-build command: {argv!r}")

    def list_files_commands(self) -> Tuple[object, ...]:
        return tuple(
            call
            for call in self.calls
            if "ls-files" in tuple(getattr(call, "argv"))
        )


class RecordingAtomicReplace:
    def __init__(self, *, fail_at: Optional[int] = None) -> None:
        self.calls: List[Tuple[Path, Path]] = []
        self.fail_at = fail_at

    def __call__(self, source: Path, destination: Path) -> None:
        source = Path(source)
        destination = Path(destination)
        self.calls.append((source, destination))
        if self.fail_at == len(self.calls):
            raise OSError("injected release publish failure")
        os.replace(source, destination)


class ReleaseBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-release-build-"
        )
        self.root = Path(os.path.realpath(self.temporary.name))
        self.source_root = self.root / "source"
        self.output_dir = self.root / "dist"
        self.source_root.mkdir()
        self._write_fixture()
        self.runner = FakeRunner(self.source_root, TRACKED_PATHS)
        self.atomic_replace = RecordingAtomicReplace()
        self.environment: Mapping[str, str] = {
            "HOME": str(self.root / "home"),
            "PATH": "/usr/bin:/bin",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_fixture(self) -> None:
        files = {
            "VERSION": f"{VERSION}\n",
            "README.md": "# Agent Rails release fixture\n",
            "docs/guide.md": "tracked documentation\n",
            "bin/agent-rails": "#!/bin/sh\nexit 0\n",
            "scripts/agent-release-install.sh": "#!/bin/sh\nexit 0\n",
            "scripts/agent-python-cli.py": (
                "from pathlib import Path\n"
                "import sys\n"
                "ROOT = Path(__file__).resolve().parents[1]\n"
                "sys.path.insert(0, str(ROOT / 'src'))\n"
                "from agent_rails.cli import main\n"
                "raise SystemExit(main())\n"
            ),
            "src/agent_rails/__init__.py": "# package\n",
            "src/agent_rails/cli.py": (
                "from agent_rails import setup_application\n"
                "def main():\n"
                "    return 0\n"
            ),
            "src/agent_rails/public_cli.py": "# public CLI\n",
            "src/agent_rails/setup_application.py": "VALUE = 1\n",
            "src/agent_rails/release/install.py": (
                "#!/usr/bin/env python3\n"
                "raise SystemExit(0)\n"
            ),
            "scratch.txt": "untracked but eligible\n",
            "ignored.log": "ignored\n",
            ".git/config": "[core]\n",
        }
        for relative_path, content in files.items():
            path = self.source_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        for relative_path in (
            "bin/agent-rails",
            "scripts/agent-release-install.sh",
            "src/agent_rails/release/install.py",
        ):
            path = self.source_root / relative_path
            path.chmod(path.stat().st_mode | stat.S_IXUSR)

    def request(
        self,
        *,
        output_dir: Optional[Path] = None,
        include_worktree: bool = False,
    ) -> ReleaseBuildRequest:
        return ReleaseBuildRequest(
            source_root=self.source_root,
            output_dir=self.output_dir if output_dir is None else output_dir,
            include_worktree=include_worktree,
            environment=dict(self.environment),
        )

    def dependencies(
        self,
        *,
        runner: Optional[FakeRunner] = None,
        atomic_replace: Optional[RecordingAtomicReplace] = None,
    ) -> ReleaseBuildDependencies:
        return ReleaseBuildDependencies(
            runner=self.runner if runner is None else runner,
            atomic_replace=(
                self.atomic_replace
                if atomic_replace is None
                else atomic_replace
            ),
        )

    def assert_no_release_assets(self, output_dir: Path) -> None:
        for name in ASSET_NAMES:
            path = output_dir / name
            self.assertFalse(
                path.exists() or path.is_symlink(),
                f"Unexpected partial release asset: {path}",
            )

    def archive_file_paths(self, archive_path: Path) -> Tuple[str, ...]:
        root_name = f"agent-rails-{VERSION}/"
        with tarfile.open(archive_path, mode="r:gz") as archive:
            return tuple(
                sorted(
                    member.name[len(root_name) :]
                    for member in archive.getmembers()
                    if member.isfile() and member.name.startswith(root_name)
                )
            )

    def test_build_is_reproducible_and_checksum_names_the_archive(self) -> None:
        first_output = self.root / "dist-one"
        second_output = self.root / "dist-two"

        first = build_release(
            self.request(output_dir=first_output),
            dependencies=self.dependencies(),
        )
        for relative_path in TRACKED_PATHS:
            path = self.source_root / relative_path
            os.utime(path, (1_900_000_000, 1_900_000_000))
        (self.source_root / "README.md").chmod(0o600)
        (self.source_root / "bin/agent-rails").chmod(0o700)
        second_atomic = RecordingAtomicReplace()
        second = build_release(
            self.request(output_dir=second_output),
            dependencies=self.dependencies(atomic_replace=second_atomic),
        )

        self.assertEqual(first.version, VERSION)
        self.assertEqual(second.version, VERSION)
        for name in ASSET_NAMES:
            self.assertEqual(
                (first_output / name).read_bytes(),
                (second_output / name).read_bytes(),
                f"Release asset is not deterministic: {name}",
            )
        archive = (first_output / ARCHIVE_NAME).read_bytes()
        expected_checksum = (
            f"{hashlib.sha256(archive).hexdigest()}  {ARCHIVE_NAME}\n"
        ).encode("ascii")
        self.assertEqual(
            (first_output / CHECKSUM_NAME).read_bytes(),
            expected_checksum,
        )
        self.assertTrue(self.atomic_replace.calls)
        self.assertTrue(second_atomic.calls)

    def test_git_selected_files_are_the_only_archive_payload(self) -> None:
        result = build_release(
            self.request(),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.output_dir, self.output_dir.resolve())
        self.assertEqual(
            self.archive_file_paths(self.output_dir / ARCHIVE_NAME),
            tuple(sorted(TRACKED_PATHS)),
        )
        commands = self.runner.list_files_commands()
        self.assertEqual(len(commands), 1)
        argv = tuple(getattr(commands[0], "argv"))
        self.assertEqual(argv[0], "git")
        self.assertIn("ls-files", argv)
        self.assertIn("-z", argv)
        self.assertNotIn("--others", argv)
        self.assertNotIn("--exclude-standard", argv)
        working_directory = Path(getattr(commands[0], "working_directory"))
        self.assertEqual(working_directory, self.source_root.resolve())

    def test_standalone_installer_assets_are_exact_and_executable(self) -> None:
        build_release(
            self.request(),
            dependencies=self.dependencies(),
        )

        installer = self.output_dir / "install.sh"
        python_installer = self.output_dir / "release_install.py"
        self.assertEqual(
            installer.read_bytes(),
            (self.source_root / "scripts/agent-release-install.sh").read_bytes(),
        )
        self.assertEqual(
            python_installer.read_bytes(),
            (
                self.source_root / "src/agent_rails/release/install.py"
            ).read_bytes(),
        )
        self.assertTrue(installer.stat().st_mode & stat.S_IXUSR)
        self.assertTrue(python_installer.stat().st_mode & stat.S_IXUSR)

    def test_installer_copy_reuses_nofollow_selected_snapshot(self) -> None:
        outside_scripts = self.root / "outside-scripts"
        outside_scripts.mkdir()
        (outside_scripts / "agent-release-install.sh").write_text(
            "#!/bin/sh\nprintf 'outside payload\\n'\n",
            encoding="utf-8",
        )
        original_write_archive = release_build_module._write_archive

        def write_then_swap(*args: object, **kwargs: object) -> None:
            original_write_archive(*args, **kwargs)
            scripts = self.source_root / "scripts"
            scripts.rename(self.source_root / "scripts-original")
            scripts.symlink_to(outside_scripts, target_is_directory=True)

        with mock.patch.object(
            release_build_module,
            "_write_archive",
            side_effect=write_then_swap,
        ):
            with self.assertRaises(ReleaseBuildError):
                build_release(
                    self.request(),
                    dependencies=self.dependencies(),
                )

        self.assert_no_release_assets(self.output_dir)

    def test_include_worktree_selects_untracked_non_ignored_paths(self) -> None:
        selected = (*TRACKED_PATHS, "scratch.txt")
        runner = FakeRunner(self.source_root, selected)

        build_release(
            self.request(include_worktree=True),
            dependencies=self.dependencies(runner=runner),
        )

        self.assertEqual(
            self.archive_file_paths(self.output_dir / ARCHIVE_NAME),
            tuple(sorted(selected)),
        )
        commands = runner.list_files_commands()
        self.assertEqual(len(commands), 1)
        argv = tuple(getattr(commands[0], "argv"))
        for flag in ("--cached", "--others", "--exclude-standard", "-z"):
            self.assertIn(flag, argv)
        self.assertNotIn("ignored.log", self.archive_file_paths(
            self.output_dir / ARCHIVE_NAME
        ))

    def test_invalid_version_is_rejected_before_selection_or_output(self) -> None:
        (self.source_root / "VERSION").write_text(
            "1.2/../../escape\n", encoding="utf-8"
        )

        with self.assertRaises(ReleaseBuildInputError):
            build_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        self.assertEqual(self.runner.calls, [])
        self.assert_no_release_assets(self.output_dir)

    def test_request_paths_and_existing_output_file_are_rejected(self) -> None:
        with self.subTest("source root must be a Path"):
            invalid_request = replace(
                self.request(),
                source_root=str(self.source_root),  # type: ignore[arg-type]
            )
            with self.assertRaises(ReleaseBuildInputError):
                build_release(
                    invalid_request,
                    dependencies=self.dependencies(),
                )

        with self.subTest("output directory must be a directory"):
            output_file = self.root / "dist-file"
            output_file.write_text("preserve me\n", encoding="utf-8")
            with self.assertRaises(ReleaseBuildInputError):
                build_release(
                    self.request(output_dir=output_file),
                    dependencies=self.dependencies(),
                )
            self.assertEqual(
                output_file.read_text(encoding="utf-8"),
                "preserve me\n",
            )

    def test_python_cli_rejects_empty_output_path(self) -> None:
        environment = dict(self.environment)
        environment["AGENT_RAILS_HOME"] = str(ROOT)
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                str(ROOT / "scripts/agent-python-cli.py"),
                "release-build",
                "--output",
                "",
            ],
            cwd=str(self.root),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

        self.assertEqual(completed.returncode, 2)
        self.assert_no_release_assets(self.root)

    def test_selected_path_cannot_escape_source_root(self) -> None:
        outside = self.root / "outside-secret.txt"
        outside.write_text("do not package\n", encoding="utf-8")
        runner = FakeRunner(
            self.source_root,
            (*TRACKED_PATHS, "../outside-secret.txt"),
        )

        with self.assertRaises(ReleaseBuildError):
            build_release(
                self.request(),
                dependencies=self.dependencies(runner=runner),
            )

        self.assert_no_release_assets(self.output_dir)

    def test_selected_path_cannot_escape_through_parent_symlink(self) -> None:
        outside_dir = self.root / "outside-docs"
        outside_dir.mkdir()
        (outside_dir / "guide.md").write_text(
            "outside secret\n",
            encoding="utf-8",
        )
        (self.source_root / "docs/guide.md").unlink()
        (self.source_root / "docs").rmdir()
        (self.source_root / "docs").symlink_to(
            outside_dir,
            target_is_directory=True,
        )

        with self.assertRaises(ReleaseBuildError):
            build_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        self.assert_no_release_assets(self.output_dir)

    def test_missing_tracked_file_leaves_no_partial_assets(self) -> None:
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        runner = FakeRunner(
            self.source_root,
            (*TRACKED_PATHS, "missing-tracked.txt"),
        )

        with self.assertRaises(ReleaseBuildError):
            build_release(
                self.request(),
                dependencies=self.dependencies(runner=runner),
            )

        self.assert_no_release_assets(self.output_dir)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_missing_public_runtime_asset_fails_for_worktree_build(self) -> None:
        helper = self.source_root / "scripts/agent-python-cli.py"
        helper.unlink()

        with self.assertRaisesRegex(
            ReleaseBuildError,
            "missing required paths.*agent-python-cli.py",
        ):
            build_release(
                self.request(include_worktree=True),
                dependencies=self.dependencies(),
            )

        self.assert_no_release_assets(self.output_dir)

    def test_missing_transitive_runtime_module_fails_worktree_build(self) -> None:
        (self.source_root / "src/agent_rails/setup_application.py").unlink()

        with self.assertRaisesRegex(
            ReleaseBuildError,
            "runtime smoke failed",
        ):
            build_release(
                self.request(include_worktree=True),
                dependencies=self.dependencies(),
            )

        self.assert_no_release_assets(self.output_dir)

    def test_runtime_smoke_extraction_rejects_traversal_member(self) -> None:
        archive_path = self.root / "malicious.tar.gz"
        expected_root = f"agent-rails-{VERSION}"
        with tarfile.open(archive_path, mode="w:gz") as archive:
            root = tarfile.TarInfo(expected_root)
            root.type = tarfile.DIRTYPE
            archive.addfile(root)
            traversal = tarfile.TarInfo(f"{expected_root}/../../escape.txt")
            traversal.size = 1
            archive.addfile(traversal, fileobj=io.BytesIO(b"x"))

        with self.assertRaisesRegex(ReleaseBuildError, "unsafe runtime member"):
            release_build_module._extract_runtime_for_smoke(
                archive_path,
                self.root / "runtime-smoke",
                expected_root,
            )

        self.assertFalse((self.root / "escape.txt").exists())

    def test_failed_git_selection_leaves_no_partial_assets(self) -> None:
        self.runner.list_files_exit_code = 23
        self.runner.list_files_stderr = b"fatal: injected selection failure\n"

        with self.assertRaises(ReleaseBuildError):
            build_release(
                self.request(),
                dependencies=self.dependencies(),
            )

        self.assert_no_release_assets(self.output_dir)
        self.assertEqual(len(self.runner.list_files_commands()), 1)

    def test_atomic_publish_is_injectable_and_failure_is_clean(self) -> None:
        self.output_dir.mkdir()
        sentinel = self.output_dir / "keep.txt"
        sentinel.write_text("keep\n", encoding="utf-8")
        failing_replace = RecordingAtomicReplace(fail_at=1)

        with self.assertRaises(ReleaseBuildError):
            build_release(
                self.request(),
                dependencies=self.dependencies(atomic_replace=failing_replace),
            )

        self.assertEqual(len(failing_replace.calls), 1)
        self.assert_no_release_assets(self.output_dir)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")

    def test_interrupt_rolls_back_all_existing_release_assets(self) -> None:
        self.output_dir.mkdir()
        old_assets = {
            name: f"old-{name}\n".encode("utf-8")
            for name in ASSET_NAMES
        }
        for name, content in old_assets.items():
            (self.output_dir / name).write_bytes(content)

        class InterruptingReplace:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self, source: Path, destination: Path) -> None:
                self.calls += 1
                if self.calls == 2:
                    raise KeyboardInterrupt()
                os.replace(source, destination)

        interrupting = InterruptingReplace()
        dependencies = ReleaseBuildDependencies(
            runner=self.runner,
            atomic_replace=interrupting,
        )

        with self.assertRaises(KeyboardInterrupt):
            build_release(self.request(), dependencies=dependencies)

        self.assertEqual(interrupting.calls, 2)
        for name, content in old_assets.items():
            self.assertEqual((self.output_dir / name).read_bytes(), content)

    def test_interrupt_after_publish_mutation_rolls_back_new_asset(self) -> None:
        class ReplaceThenInterrupt:
            def __init__(self) -> None:
                self.calls = 0

            def __call__(self, source: Path, destination: Path) -> None:
                self.calls += 1
                os.replace(source, destination)
                if self.calls == 1:
                    raise KeyboardInterrupt()

        replace_then_interrupt = ReplaceThenInterrupt()
        dependencies = ReleaseBuildDependencies(
            runner=self.runner,
            atomic_replace=replace_then_interrupt,
        )

        with self.assertRaises(KeyboardInterrupt):
            build_release(self.request(), dependencies=dependencies)

        self.assertEqual(replace_then_interrupt.calls, 1)
        self.assert_no_release_assets(self.output_dir)
        self.assertEqual(
            tuple(self.output_dir.glob(".agent-rails-release-stage-*")),
            (),
        )

    def test_rollback_failure_retains_previous_asset_recovery_data(self) -> None:
        self.output_dir.mkdir()
        old_assets = {
            name: f"old-{name}\n".encode("utf-8")
            for name in ASSET_NAMES
        }
        for name, content in old_assets.items():
            (self.output_dir / name).write_bytes(content)
        failing_replace = RecordingAtomicReplace(fail_at=1)
        real_replace = os.replace

        def fail_archive_restore(source: object, destination: object) -> None:
            source_path = Path(source)  # type: ignore[arg-type]
            destination_path = Path(destination)  # type: ignore[arg-type]
            if (
                source_path.parent.name == "backups"
                and source_path.name == ARCHIVE_NAME
            ):
                raise OSError("injected rollback restore failure")
            real_replace(source_path, destination_path)

        with mock.patch.object(
            release_build_module.os,
            "replace",
            side_effect=fail_archive_restore,
        ):
            with self.assertRaises(ReleaseBuildRecoveryError) as raised:
                build_release(
                    self.request(),
                    dependencies=self.dependencies(atomic_replace=failing_replace),
                )

        recovery_path = raised.exception.recovery_path
        self.assertTrue(recovery_path.is_dir())
        self.assertEqual(
            (recovery_path / ARCHIVE_NAME).read_bytes(),
            old_assets[ARCHIVE_NAME],
        )
        self.assertFalse((self.output_dir / ARCHIVE_NAME).exists())
        for name in ASSET_NAMES[1:]:
            self.assertEqual((self.output_dir / name).read_bytes(), old_assets[name])

    def test_rollback_probe_interrupt_retains_unrestored_backup(self) -> None:
        self.output_dir.mkdir()
        old_assets = {
            name: f"old-{name}\n".encode("utf-8")
            for name in ASSET_NAMES
        }
        for name, content in old_assets.items():
            (self.output_dir / name).write_bytes(content)
        rollback_started = False
        interrupted = False
        real_identity = release_build_module._inode_identity

        def fail_publish(source: Path, destination: Path) -> None:
            nonlocal rollback_started
            rollback_started = True
            raise OSError("injected publish failure")

        def interrupt_rollback_probe(path: Path) -> object:
            nonlocal interrupted
            if rollback_started and path.parent.name == "backups" and not interrupted:
                interrupted = True
                raise KeyboardInterrupt()
            return real_identity(path)

        dependencies = ReleaseBuildDependencies(
            runner=self.runner,
            atomic_replace=fail_publish,
        )
        with mock.patch.object(
            release_build_module,
            "_inode_identity",
            side_effect=interrupt_rollback_probe,
        ):
            with self.assertRaises(ReleaseBuildRecoveryError) as raised:
                build_release(self.request(), dependencies=dependencies)

        self.assertTrue(interrupted)
        recovery_path = raised.exception.recovery_path
        self.assertTrue(recovery_path.is_dir())
        retained = tuple(path.name for path in recovery_path.iterdir())
        self.assertTrue(retained)
        for name in retained:
            self.assertEqual((recovery_path / name).read_bytes(), old_assets[name])

    def test_default_publish_refuses_concurrent_destination(self) -> None:
        self.output_dir.mkdir()
        old_assets = {
            name: f"old-{name}\n".encode("utf-8")
            for name in ASSET_NAMES
        }
        for name, content in old_assets.items():
            (self.output_dir / name).write_bytes(content)
        real_link = os.link
        concurrent = b"concurrent-owner\n"

        def create_destination_before_link(
            source: object,
            destination: object,
            *,
            follow_symlinks: bool = True,
        ) -> None:
            destination_path = Path(destination)  # type: ignore[arg-type]
            if destination_path.name == ARCHIVE_NAME:
                destination_path.write_bytes(concurrent)
            real_link(
                source,
                destination,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            release_build_module.os,
            "link",
            side_effect=create_destination_before_link,
        ):
            with self.assertRaises(ReleaseBuildRecoveryError) as raised:
                build_release(
                    self.request(),
                    dependencies=ReleaseBuildDependencies(runner=self.runner),
                )

        self.assertEqual(
            (self.output_dir / ARCHIVE_NAME).read_bytes(),
            concurrent,
        )
        self.assertEqual(
            (raised.exception.recovery_path / ARCHIVE_NAME).read_bytes(),
            old_assets[ARCHIVE_NAME],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
