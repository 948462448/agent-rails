#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails import cli as python_cli  # noqa: E402
from agent_rails.skills_install import (  # noqa: E402
    SkillsInstallError,
    SkillsInstallInputError,
    SkillsInstallRequest,
    install_skills,
)


class SkillsInstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-skills-install-"
        )
        self.root = Path(os.path.realpath(self.temporary.name))
        self.kit_home = self.root / "kit home"
        self.source_root = self.kit_home / "skills"
        self.destination = self.root / "installed skills"
        self.source_root.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write_skill(
        self,
        name: str,
        manifest: str = "# Skill\n",
        *,
        executable_asset: bool = False,
    ) -> Path:
        source = self.source_root / name
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text(manifest, encoding="utf-8")
        asset = source / "assets" / "run.sh"
        asset.parent.mkdir()
        asset.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        if executable_asset:
            asset.chmod(asset.stat().st_mode | stat.S_IXUSR)
        return source

    def request(
        self,
        *,
        selected_skills: tuple[str, ...] = (),
        dry_run: bool = False,
        kit_home: Path | None = None,
        destination: Path | None = None,
    ) -> SkillsInstallRequest:
        return SkillsInstallRequest(
            kit_home=self.kit_home if kit_home is None else kit_home,
            destination=(
                self.destination if destination is None else destination
            ),
            selected_skills=selected_skills,
            dry_run=dry_run,
        )

    def invoke_cli(
        self,
        arguments: tuple[str, ...],
        *,
        kit_home: Path | None = None,
        include_home: bool = True,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = {"PATH": os.environ.get("PATH", "")}
        if include_home:
            environment["AGENT_RAILS_HOME"] = str(
                self.kit_home if kit_home is None else kit_home
            )
        with (
            patch.dict(os.environ, environment, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            exit_code = python_cli.main(("skills-install", *arguments))
        return exit_code, stdout.getvalue(), stderr.getvalue()

    def test_default_selection_is_sorted_and_missing_manifests_are_warnings(
        self,
    ) -> None:
        zeta = self.write_skill("zeta", "zeta\n")
        alpha = self.write_skill("alpha", "alpha\n")
        missing = self.source_root / "middle-missing"
        missing.mkdir()
        (missing / "README.md").write_text("not a skill\n", encoding="utf-8")
        (self.source_root / "not-a-directory").write_text(
            "ignored\n", encoding="utf-8"
        )

        result = install_skills(self.request(dry_run=True))

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.selected_skills, ("alpha", "zeta"))
        self.assertEqual(result.installed_skills, ())
        self.assertEqual(
            tuple(result.stdout.splitlines()),
            (
                f"Would install {alpha} -> {self.destination / 'alpha'}",
                f"Would install {zeta} -> {self.destination / 'zeta'}",
            ),
        )
        self.assertIn(
            f"Skipping middle-missing: missing {missing / 'SKILL.md'}",
            result.stderr,
        )
        self.assertFalse(self.destination.exists())

    def test_explicit_selection_preserves_order_and_limits_the_install(self) -> None:
        alpha = self.write_skill("alpha", "alpha\n")
        zeta = self.write_skill("zeta", "zeta\n")

        result = install_skills(
            self.request(
                selected_skills=("zeta", "alpha"),
                dry_run=True,
            )
        )

        self.assertEqual(result.selected_skills, ("zeta", "alpha"))
        self.assertEqual(
            tuple(result.stdout.splitlines()),
            (
                f"Would install {zeta} -> {self.destination / 'zeta'}",
                f"Would install {alpha} -> {self.destination / 'alpha'}",
            ),
        )
        self.assertFalse(self.destination.exists())

    def test_install_copies_the_complete_tree_and_preserves_executable_mode(
        self,
    ) -> None:
        source = self.write_skill(
            "agent-check",
            "version-one\n",
            executable_asset=True,
        )
        (source / "examples").mkdir()
        (source / "examples" / "sample.txt").write_bytes(b"sample\x00bytes")

        result = install_skills(
            self.request(selected_skills=("agent-check",))
        )

        target = self.destination / "agent-check"
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.selected_skills, ("agent-check",))
        self.assertEqual(result.installed_skills, ("agent-check",))
        self.assertEqual((target / "SKILL.md").read_text(), "version-one\n")
        self.assertEqual(
            (target / "examples/sample.txt").read_bytes(), b"sample\x00bytes"
        )
        self.assertTrue((target / "assets/run.sh").stat().st_mode & stat.S_IXUSR)
        self.assertEqual(
            result.stdout,
            f"Installed {source} -> {target}\n",
        )

    def test_repeated_install_atomically_refreshes_instead_of_merging_stale_files(
        self,
    ) -> None:
        source = self.write_skill("agent-check", "version-one\n")
        obsolete = source / "obsolete.txt"
        obsolete.write_text("obsolete\n", encoding="utf-8")
        request = self.request(selected_skills=("agent-check",))
        install_skills(request)

        (source / "SKILL.md").write_text("version-two\n", encoding="utf-8")
        obsolete.unlink()
        (source / "fresh.txt").write_text("fresh\n", encoding="utf-8")
        result = install_skills(request)

        target = self.destination / "agent-check"
        self.assertEqual(result.installed_skills, ("agent-check",))
        self.assertEqual((target / "SKILL.md").read_text(), "version-two\n")
        self.assertEqual((target / "fresh.txt").read_text(), "fresh\n")
        self.assertFalse((target / "obsolete.txt").exists())
        self.assertEqual(
            tuple(
                sorted(
                    path.relative_to(source).as_posix()
                    for path in source.rglob("*")
                )
            ),
            tuple(
                sorted(
                    path.relative_to(target).as_posix()
                    for path in target.rglob("*")
                )
            ),
        )

    def test_failed_publish_and_failed_rollback_preserve_recoverable_backup(
        self,
    ) -> None:
        self.write_skill("agent-check", "new-version\n")
        target = self.destination / "agent-check"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("user-version\n", encoding="utf-8")
        real_replace = os.replace

        def fail_publish_and_restore(source: object, destination: object) -> None:
            source_path = Path(source)  # type: ignore[arg-type]
            if source_path.parent.name in {"staged", "backups"}:
                raise OSError(f"injected {source_path.parent.name} failure")
            real_replace(source, destination)

        with (
            patch(
                "agent_rails.skills_install.os.replace",
                side_effect=fail_publish_and_restore,
            ),
            self.assertRaisesRegex(
                SkillsInstallError,
                "rollback failed.*Recovery data kept at",
            ),
        ):
            install_skills(self.request(selected_skills=("agent-check",)))

        transactions = tuple(
            self.destination.glob(".agent-rails-skills-*")
        )
        self.assertEqual(len(transactions), 1)
        backup = transactions[0] / "backups" / "agent-check" / "SKILL.md"
        self.assertEqual(backup.read_text(encoding="utf-8"), "user-version\n")
        self.assertFalse(target.exists())

    def test_interrupt_after_backup_move_restores_the_journaled_old_tree(
        self,
    ) -> None:
        self.write_skill("agent-check", "new-version\n")
        target = self.destination / "agent-check"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("user-version\n", encoding="utf-8")
        real_replace = os.replace

        def interrupt_after_backup(source: object, destination: object) -> None:
            source_path = Path(source)  # type: ignore[arg-type]
            destination_path = Path(destination)  # type: ignore[arg-type]
            real_replace(source, destination)
            if source_path == target and destination_path.parent.name == "backups":
                raise KeyboardInterrupt

        with (
            patch(
                "agent_rails.skills_install.os.replace",
                side_effect=interrupt_after_backup,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            install_skills(self.request(selected_skills=("agent-check",)))

        self.assertEqual(
            (target / "SKILL.md").read_text(encoding="utf-8"),
            "user-version\n",
        )
        self.assertEqual(
            tuple(self.destination.glob(".agent-rails-skills-*")),
            (),
        )

    def test_interrupt_after_publish_removes_the_journaled_new_tree(self) -> None:
        self.write_skill("agent-check", "new-version\n")
        target = self.destination / "agent-check"
        real_replace = os.replace

        def interrupt_after_publish(source: object, destination: object) -> None:
            source_path = Path(source)  # type: ignore[arg-type]
            real_replace(source, destination)
            if source_path.parent.name == "staged":
                raise KeyboardInterrupt

        with (
            patch(
                "agent_rails.skills_install.os.replace",
                side_effect=interrupt_after_publish,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            install_skills(self.request(selected_skills=("agent-check",)))

        self.assertFalse(target.exists())
        self.assertEqual(
            tuple(self.destination.glob(".agent-rails-skills-*")),
            (),
        )

    def test_interrupt_during_rollback_keeps_every_unrestored_backup(self) -> None:
        self.write_skill("alpha", "new-alpha\n")
        self.write_skill("beta", "new-beta\n")
        self.destination.mkdir()
        for name in ("alpha", "beta"):
            target = self.destination / name
            target.mkdir()
            (target / "SKILL.md").write_text(
                f"old-{name}\n", encoding="utf-8"
            )
        real_replace = os.replace
        from agent_rails import skills_install as skills_module

        real_remove_tree = skills_module._remove_tree

        def fail_beta_publish(source: object, destination: object) -> None:
            source_path = Path(source)  # type: ignore[arg-type]
            if source_path.parent.name == "staged" and source_path.name == "beta":
                raise OSError("injected beta publish failure")
            real_replace(source, destination)

        def interrupt_after_remove(path: Path) -> None:
            real_remove_tree(path)
            raise KeyboardInterrupt

        with (
            patch(
                "agent_rails.skills_install.os.replace",
                side_effect=fail_beta_publish,
            ),
            patch(
                "agent_rails.skills_install._remove_tree",
                side_effect=interrupt_after_remove,
            ),
            self.assertRaises(KeyboardInterrupt),
        ):
            install_skills(
                self.request(selected_skills=("alpha", "beta"))
            )

        transactions = tuple(
            self.destination.glob(".agent-rails-skills-*")
        )
        self.assertEqual(len(transactions), 1)
        for name in ("alpha", "beta"):
            backup = transactions[0] / "backups" / name / "SKILL.md"
            self.assertEqual(
                backup.read_text(encoding="utf-8"),
                f"old-{name}\n",
            )

    def test_source_root_and_source_tree_symlinks_fail_closed(self) -> None:
        outside = self.root / "outside source"
        self.write_skill("agent-check", "source\n")
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("outside\n", encoding="utf-8")
        (self.source_root / "agent-check" / "linked.txt").symlink_to(secret)

        with self.assertRaisesRegex(SkillsInstallError, "symbolic link"):
            install_skills(
                self.request(selected_skills=("agent-check",))
            )

        self.assertEqual(secret.read_text(), "outside\n")
        self.assertFalse(self.destination.exists())

        shutil.rmtree(self.source_root)
        external_skills = outside / "skills"
        external_skills.mkdir()
        external_skill = external_skills / "external"
        external_skill.mkdir()
        (external_skill / "SKILL.md").write_text("outside\n", encoding="utf-8")
        self.source_root.symlink_to(external_skills, target_is_directory=True)

        with self.assertRaisesRegex(SkillsInstallError, "symbolic link"):
            install_skills(self.request())

        self.assertFalse(self.destination.exists())

    def test_destination_root_and_skill_symlinks_fail_without_touching_targets(
        self,
    ) -> None:
        self.write_skill("agent-check", "managed\n")
        outside = self.root / "outside target"
        outside.mkdir()
        sentinel = outside / "sentinel.txt"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        linked_destination = self.root / "linked destination"
        linked_destination.symlink_to(outside, target_is_directory=True)

        with self.assertRaisesRegex(SkillsInstallError, "symbolic link"):
            install_skills(
                self.request(
                    selected_skills=("agent-check",),
                    destination=linked_destination,
                )
            )

        self.assertEqual(sentinel.read_text(), "user-owned\n")
        self.assertFalse((outside / "agent-check").exists())

        self.destination.mkdir()
        linked_skill = self.destination / "agent-check"
        linked_skill.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(SkillsInstallError, "symbolic link"):
            install_skills(
                self.request(selected_skills=("agent-check",))
            )

        self.assertTrue(linked_skill.is_symlink())
        self.assertEqual(sentinel.read_text(), "user-owned\n")

    def test_invalid_skill_names_are_rejected_before_destination_mutation(
        self,
    ) -> None:
        for index, name in enumerate(
            (
                "",
                ".",
                "..",
                "../escape",
                "nested/name",
                str(self.root / "absolute"),
                "agent..check",
                "line\nbreak",
            )
        ):
            with self.subTest(name=name):
                destination = self.root / f"destination-{index}"
                with self.assertRaises(SkillsInstallInputError) as raised:
                    install_skills(
                        self.request(
                            selected_skills=(name,),
                            destination=destination,
                        )
                    )
                self.assertEqual(raised.exception.exit_code, 2)
                self.assertFalse(destination.exists())
        self.assertFalse((self.kit_home / "escape").exists())

    def test_complete_preflight_prevents_partial_install_when_later_skill_is_unsafe(
        self,
    ) -> None:
        self.write_skill("alpha", "alpha\n")
        beta = self.write_skill("beta", "beta\n")
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (beta / "unsafe.txt").symlink_to(outside)

        with self.assertRaises(SkillsInstallError):
            install_skills(
                self.request(selected_skills=("alpha", "beta"))
            )

        self.assertFalse(self.destination.exists())
        self.assertEqual(outside.read_text(), "outside\n")

    def test_request_and_source_root_validation_have_typed_exit_codes(self) -> None:
        base = self.request(dry_run=True)
        invalid_requests = (
            object(),
            replace(base, kit_home=str(self.kit_home)),
            replace(base, destination=str(self.destination)),
            replace(base, selected_skills=[]),
            replace(base, selected_skills=(3,)),
            replace(base, dry_run=1),
        )
        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(SkillsInstallInputError) as raised:
                    install_skills(request)  # type: ignore[arg-type]
                self.assertEqual(raised.exception.exit_code, 2)

        missing_home = self.root / "missing kit"
        with self.assertRaises(SkillsInstallError) as raised:
            install_skills(self.request(kit_home=missing_home))
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertIn("Missing source dir", str(raised.exception))
        self.assertFalse(self.destination.exists())

    def test_cli_is_strict_and_preserves_success_warning_and_error_exit_codes(
        self,
    ) -> None:
        source = self.write_skill("agent-check", "managed\n")

        exit_code, stdout, stderr = self.invoke_cli(("--help",))
        self.assertEqual(exit_code, 0)
        self.assertIn("Usage: agent-rails skills install", stdout)
        self.assertEqual(stderr, "")

        invalid_arguments = (
            (),
            ("--dest",),
            ("--dest", ""),
            ("--unknown",),
            (
                "--dest",
                str(self.destination),
                "--dest",
                str(self.root / "other"),
            ),
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr = self.invoke_cli(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertIn("Usage: agent-rails skills install", stderr)

        exit_code, stdout, stderr = self.invoke_cli(
            ("--dest", str(self.destination), "--dry-run", "agent-check")
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            stdout,
            f"Would install {source} -> {self.destination / 'agent-check'}\n",
        )
        self.assertEqual(stderr, "")
        self.assertFalse(self.destination.exists())

        exit_code, stdout, stderr = self.invoke_cli(
            ("--dest", str(self.destination)),
            include_home=False,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("AGENT_RAILS_HOME", stderr)

        missing_home = self.root / "missing home"
        exit_code, stdout, stderr = self.invoke_cli(
            ("--dest", str(self.destination)),
            kit_home=missing_home,
        )
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("Missing source dir", stderr)


if __name__ == "__main__":
    unittest.main()
