#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

import agent_rails.adapters.workspace as workspace_module
from agent_rails.adapters.workspace import (
    ManagedAdapterWorkspace,
    ManagedAdapterWorkspaceConfig,
    ManagedAdapterWorkspaceError,
)


def _git(project: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _write_skill(root: Path, name: str, content: str) -> Path:
    skill = root / "skills" / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(content, encoding="utf-8")
    return skill


class ManagedAdapterWorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-adapter-workspace-"
        )
        root = Path(self.temporary.name)
        self.home = root / "kit"
        self.project = root / "project"
        self.home.mkdir()
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def config(self, **overrides: object) -> ManagedAdapterWorkspaceConfig:
        values = {
            "home": self.home,
            "project": self.project,
            "skills_relative_dir": Path(".adapter/skills"),
            "guide_path": Path(".adapter/AGENT_RAILS.md"),
            "pack_command_path": Path(".adapter/commands/agent-rails-pack.md"),
            "lite_command_path": Path(".adapter/commands/agent-rails-lite.md"),
            "check_command_path": Path(".adapter/commands/agent-rails-check.md"),
            "managed_skills_path": Path(".adapter/.agent-rails-managed-skills"),
        }
        values.update(overrides)
        return ManagedAdapterWorkspaceConfig(**values)  # type: ignore[arg-type]

    def init_git(self) -> None:
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.name", "Agent Rails Test")
        _git(self.project, "config", "user.email", "agent-rails@example.invalid")

    def commit_all(self) -> None:
        _git(self.project, "add", ".")
        _git(self.project, "commit", "-qm", "fixture")

    def test_config_rejects_escape_and_non_boolean_policy(self) -> None:
        with self.assertRaises(ManagedAdapterWorkspaceError):
            self.config(skills_relative_dir=Path("../outside"))
        with self.assertRaises(ManagedAdapterWorkspaceError):
            self.config(guide_path=self.project.parent / "outside.md")
        with self.assertRaises(ManagedAdapterWorkspaceError):
            self.config(dry_run=1)

    def test_generated_marker_and_all_legacy_signatures_are_recognized(self) -> None:
        workspace = ManagedAdapterWorkspace(self.config())
        fixtures = {
            workspace.config.guide_path: (
                "Agent Rails Version: 1.0\nVisible session marker protocol\n"
            ),
            workspace.config.pack_command_path: (
                "Generate and read the Agent Rails Task Pack\nAGENT RAILS: ON\n"
            ),
            workspace.config.lite_command_path: (
                "lite Agent Rails Task Pack\n--pack-mode lite\n"
            ),
            workspace.config.check_command_path: (
                "Agent Rails verification suggestions\n"
                "AGENT RAILS: CHECK-ONLY\n"
            ),
        }
        for path, content in fixtures.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            with self.subTest(path=path.name):
                self.assertTrue(workspace.is_generated_file(path))

        marker_path = self.project / ".adapter" / "plugin.mjs"
        marker_path.write_text(
            "// <!-- agent-rails:generated -->\n", encoding="utf-8"
        )
        self.assertTrue(workspace.is_generated_file(marker_path))
        self.assertTrue(workspace.is_generated_file(Path(".adapter/plugin.mjs")))

        unmanaged = self.project / ".adapter" / "unmanaged.md"
        unmanaged.write_text(fixtures[workspace.config.guide_path], encoding="utf-8")
        self.assertFalse(workspace.is_generated_file(unmanaged))

    def test_inventory_v2_load_validates_and_write_sorts(self) -> None:
        config = self.config()
        config.managed_skills_path.parent.mkdir(parents=True)
        config.managed_skills_path.write_text(
            json.dumps(
                {
                    "format": "agent-rails-managed-skills-v2",
                    "skills": [
                        {"name": "agent-z", "sha256": "f" * 64},
                        {"name": "agent-a", "sha256": "a" * 64},
                    ],
                }
            ),
            encoding="utf-8",
        )
        workspace = ManagedAdapterWorkspace(config)

        messages = workspace.load_managed_skills()
        self.assertEqual(workspace.state.managed_skills, ("agent-z", "agent-a"))
        self.assertEqual(messages, ())
        self.assertEqual(
            workspace.write_managed_skills(),
            (f"Wrote managed skill inventory: {config.managed_skills_path}",),
        )
        self.assertEqual(
            json.loads(config.managed_skills_path.read_text(encoding="utf-8")),
            {
                "format": "agent-rails-managed-skills-v2",
                "skills": [
                    {"name": "agent-a", "sha256": "a" * 64},
                    {"name": "agent-z", "sha256": "f" * 64},
                ],
            },
        )

    def test_plain_or_dot_inventory_is_rejected_without_claiming_targets(self) -> None:
        config = self.config()
        target = config.project / config.skills_relative_dir / "agent-user"
        target.mkdir(parents=True)
        sentinel = target / "USER.md"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        config.managed_skills_path.parent.mkdir(parents=True, exist_ok=True)

        invalid_v2 = (
            {
                "format": "agent-rails-managed-skills-v2",
                "skills": [{"name": ".", "sha256": "a" * 64}],
            },
            {
                "format": "agent-rails-managed-skills-v2",
                "skills": [
                    {"name": "agent-user", "sha256": "a" * 64},
                    {"name": "agent-user", "sha256": "a" * 64},
                ],
            },
            {
                "format": "agent-rails-managed-skills-v2",
                "skills": [{"name": "agent-user", "sha256": "not-a-digest"}],
            },
        )
        contents = ("agent-user\n", ".\n", *(json.dumps(item) for item in invalid_v2))
        for content in contents:
            with self.subTest(content=content.strip()):
                config.managed_skills_path.write_text(content, encoding="utf-8")
                workspace = ManagedAdapterWorkspace(config)
                with self.assertRaises(ManagedAdapterWorkspaceError):
                    workspace.load_managed_skills()
                self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")

    def test_tracked_and_unmanaged_generated_files_are_preserved(self) -> None:
        self.init_git()
        config = self.config(protect_tracked=True)
        config.guide_path.parent.mkdir(parents=True)
        config.guide_path.write_text("team-owned\n", encoding="utf-8")
        self.commit_all()
        unmanaged = self.project / ".adapter" / "user.md"
        unmanaged.write_text("user-owned\n", encoding="utf-8")

        other_repo = self.project.parent / "other"
        other_repo.mkdir()
        _git(other_repo, "init", "-q")
        with mock.patch.dict(
            os.environ,
            {"GIT_DIR": str(other_repo / ".git"), "GIT_WORK_TREE": str(other_repo)},
        ):
            workspace = ManagedAdapterWorkspace(config)
            self.assertTrue(workspace.state.is_git_repo)
            self.assertTrue(workspace.is_tracked_file(config.guide_path))
            self.assertTrue(workspace.is_tracked_file(Path(".adapter/AGENT_RAILS.md")))
            tracked = workspace.write_generated_file(config.guide_path, "replacement")

        self.assertIn("Keeping tracked file in local mode", tracked[0])
        self.assertEqual(config.guide_path.read_text(encoding="utf-8"), "team-owned\n")
        self.assertIn(
            "Keeping unmanaged existing file",
            workspace.write_generated_file(unmanaged, "replacement")[0],
        )
        self.assertEqual(unmanaged.read_text(encoding="utf-8"), "user-owned\n")

    def test_generated_write_refresh_remove_and_permissions(self) -> None:
        config = self.config()
        workspace = ManagedAdapterWorkspace(config)
        target = config.lite_command_path
        target.parent.mkdir(parents=True)
        target.write_text("<!-- agent-rails:generated -->\nstale\n", encoding="utf-8")
        target.chmod(0o640)

        messages = workspace.write_generated_file(
            target, "<!-- agent-rails:generated -->\nfresh\n\n"
        )
        self.assertEqual(len(messages), 2)
        self.assertIn("Refreshing Agent Rails-generated", messages[0])
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            "<!-- agent-rails:generated -->\nfresh\n",
        )
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
        self.assertEqual(
            workspace.remove_generated_file(target), (f"Removed {target}",)
        )
        self.assertFalse(target.exists())

    def test_force_overwrites_and_removes_tracked_or_unmanaged_files(self) -> None:
        self.init_git()
        config = self.config(protect_tracked=True, force=True)
        config.guide_path.parent.mkdir(parents=True)
        config.guide_path.write_text("team-owned\n", encoding="utf-8")
        self.commit_all()
        workspace = ManagedAdapterWorkspace(config)

        self.assertEqual(
            workspace.write_generated_file(config.guide_path, "forced"),
            (f"Wrote {config.guide_path}",),
        )
        self.assertEqual(config.guide_path.read_text(encoding="utf-8"), "forced\n")
        self.assertEqual(
            workspace.remove_generated_file(config.guide_path),
            (f"Removed {config.guide_path}",),
        )

    def test_skill_install_preserves_tracked_and_unmanaged_targets(self) -> None:
        self.init_git()
        _write_skill(self.home, "agent-check", "source-check\n")
        _write_skill(self.home, "agent-context", "source-context\n")
        _write_skill(self.home, "agent-new", "source-new\n")
        config = self.config(protect_tracked=True)
        tracked = config.project / config.skills_relative_dir / "agent-check"
        tracked.mkdir(parents=True)
        (tracked / "SKILL.md").write_text("team-check\n", encoding="utf-8")
        self.commit_all()
        unmanaged = config.project / config.skills_relative_dir / "agent-context"
        unmanaged.mkdir()
        (unmanaged / "SKILL.md").write_text("user-context\n", encoding="utf-8")
        workspace = ManagedAdapterWorkspace(config)

        messages = workspace.install_skills()
        self.assertTrue(any("Keeping tracked skill" in item for item in messages))
        self.assertTrue(any("Keeping unmanaged existing skill" in item for item in messages))
        self.assertTrue(any("Installed" in item and "agent-new" in item for item in messages))
        self.assertEqual((tracked / "SKILL.md").read_text(), "team-check\n")
        self.assertEqual((unmanaged / "SKILL.md").read_text(), "user-context\n")
        self.assertEqual(
            (workspace.skills_dir / "agent-new" / "SKILL.md").read_text(),
            "source-new\n",
        )
        self.assertEqual(workspace.state.managed_skills, ("agent-new",))

    def test_managed_skill_remove_keeps_tracked_and_unlisted_directories(self) -> None:
        self.init_git()
        _write_skill(self.home, "agent-check", "managed-check\n")
        _write_skill(self.home, "agent-context", "managed-context\n")
        config = self.config(protect_tracked=True)
        installer = ManagedAdapterWorkspace(config)
        installer.install_skills()
        installer.write_managed_skills()
        tracked = installer.skills_dir / "agent-check"
        removable = installer.skills_dir / "agent-context"
        unlisted = config.project / config.skills_relative_dir / "user-skill"
        unlisted.mkdir(parents=True)
        (unlisted / "SKILL.md").write_text("user-skill", encoding="utf-8")
        _git(self.project, "add", ".adapter/skills/agent-check/SKILL.md")
        _git(self.project, "commit", "-qm", "tracked skill fixture")
        workspace = ManagedAdapterWorkspace(config)
        workspace.load_managed_skills()

        messages = workspace.remove_managed_skills()
        self.assertTrue(any("Keeping tracked skill" in item for item in messages))
        self.assertTrue(tracked.exists())
        self.assertFalse(removable.exists())
        self.assertTrue(unlisted.exists())

    def test_existing_skill_is_not_claimed_without_inventory(self) -> None:
        _write_skill(self.home, "agent-check", "source\n")
        config = self.config()
        target = config.project / config.skills_relative_dir / "agent-check"
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("legacy\n", encoding="utf-8")
        workspace = ManagedAdapterWorkspace(config)

        self.assertTrue(
            any("Keeping unmanaged" in item for item in workspace.install_skills())
        )
        self.assertEqual((target / "SKILL.md").read_text(), "legacy\n")
        self.assertEqual(workspace.state.managed_skills, ())
        self.assertEqual(workspace.remove_managed_skills(), ())
        self.assertTrue(target.exists())

    def test_ignore_block_is_idempotent_and_cleans_legacy_entries(self) -> None:
        workspace = ManagedAdapterWorkspace(self.config())
        ignore = self.project / ".git" / "info" / "exclude"
        ignore.parent.mkdir(parents=True)
        ignore.write_text(
            "user-ignore\n\n"
            "# Agent Rails adapter\n"
            ".adapter/old\n"
            ".adapter/cleanup-only\n"
            "# Agent Rails adapter end\n",
            encoding="utf-8",
        )

        for _ in range(2):
            workspace.ensure_ignore_block(
                ignore,
                "# Agent Rails adapter",
                "# Agent Rails adapter end",
                (".adapter/new",),
                (".adapter/old", ".adapter/cleanup-only"),
            )
        content = ignore.read_text(encoding="utf-8")
        self.assertEqual(content.count("# Agent Rails adapter\n"), 1)
        self.assertIn("user-ignore", content)
        self.assertIn(".adapter/new", content)
        self.assertNotIn(".adapter/old", content)
        self.assertNotIn("cleanup-only", content)

        messages = workspace.remove_ignore_block(
            ignore,
            "# Agent Rails adapter",
            "# Agent Rails adapter end",
            "Would remove adapter block from",
            "Removed adapter block from",
            (".adapter/new", ".adapter/old", ".adapter/cleanup-only"),
        )
        self.assertEqual(messages, (f"Removed adapter block from {ignore}",))
        remaining = ignore.read_text(encoding="utf-8")
        self.assertIn("user-ignore", remaining)
        self.assertNotIn("# Agent Rails adapter", remaining)

    def test_dry_run_reports_without_mutating_any_workspace_state_on_disk(self) -> None:
        _write_skill(self.home, "agent-check", "source\n")
        config = self.config(dry_run=True)
        generated = config.guide_path
        generated.parent.mkdir(parents=True)
        generated.write_text(
            "<!-- agent-rails:generated -->\nold\n", encoding="utf-8"
        )
        workspace = ManagedAdapterWorkspace(config)
        ignore = self.project / ".gitignore"

        self.assertTrue(
            any(
                "Would write" in item
                for item in workspace.write_generated_file(generated, "new")
            )
        )
        self.assertTrue(any("Would install" in item for item in workspace.install_skills()))
        self.assertTrue(
            any(
                "Would write managed" in item
                for item in workspace.write_managed_skills()
            )
        )
        self.assertEqual(
            workspace.ensure_ignore_block(
                ignore, "# marker", "# end", (".adapter/",)
            ),
            (
                f"Would ensure local ignore entries in {ignore}",
                "  .adapter/",
            ),
        )
        self.assertEqual(
            generated.read_text(encoding="utf-8"),
            "<!-- agent-rails:generated -->\nold\n",
        )
        self.assertFalse((workspace.skills_dir / "agent-check").exists())
        self.assertFalse(config.managed_skills_path.exists())
        self.assertFalse(ignore.exists())

    def test_skill_copy_rejects_source_tree_symbolic_links(self) -> None:
        source = _write_skill(self.home, "agent-check", "source\n")
        outside = self.home.parent / "outside-source.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (source / "linked.txt").symlink_to(outside)
        workspace = ManagedAdapterWorkspace(self.config())

        with self.assertRaisesRegex(
            ManagedAdapterWorkspaceError, "contains a symbolic link"
        ):
            workspace.install_skills()

        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assertFalse((workspace.skills_dir / "agent-check/linked.txt").exists())

    def test_skill_copy_rejects_destination_tree_symbolic_links(self) -> None:
        source = _write_skill(self.home, "agent-check", "source\n")
        (source / "assets").mkdir()
        (source / "assets/data.txt").write_text("managed\n", encoding="utf-8")
        config = self.config(force=True)
        target = config.project / config.skills_relative_dir / "agent-check"
        target.mkdir(parents=True)
        outside = self.project.parent / "outside-target"
        outside.mkdir()
        sentinel = outside / "data.txt"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        (target / "assets").symlink_to(outside, target_is_directory=True)
        workspace = ManagedAdapterWorkspace(config)

        with self.assertRaisesRegex(
            ManagedAdapterWorkspaceError, "contains a symbolic link"
        ):
            workspace.install_skills()

        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")

    def test_skills_source_root_symbolic_link_is_rejected(self) -> None:
        outside = self.home.parent / "outside-skills"
        _write_skill(outside, "agent-check", "outside\n")
        (self.home / "skills").symlink_to(
            outside / "skills",
            target_is_directory=True,
        )
        workspace = ManagedAdapterWorkspace(self.config())

        with self.assertRaisesRegex(
            ManagedAdapterWorkspaceError,
            "Unable to list Agent Rails skills",
        ):
            workspace.install_skills()

        self.assertFalse((workspace.skills_dir / "agent-check").exists())

    def test_missing_manifest_is_skipped_without_ownership_claim(self) -> None:
        source = self.home / "skills" / "agent-bad"
        source.mkdir(parents=True)
        (source / "README.md").write_text("not a skill\n", encoding="utf-8")
        workspace = ManagedAdapterWorkspace(self.config())

        messages = workspace.install_skills()

        self.assertTrue(any("Skipping agent-bad" in item for item in messages))
        self.assertEqual(workspace.state.managed_skills, ())
        self.assertEqual(workspace.write_managed_skills(), ())
        user_target = workspace.skills_dir / "agent-bad"
        user_target.mkdir(parents=True)
        sentinel = user_target / "USER.md"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        self.assertEqual(workspace.remove_managed_skills(), ())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")

    def test_readonly_source_tree_installs_atomically_with_source_modes(self) -> None:
        source = _write_skill(self.home, "agent-check", "source\n")
        assets = source / "assets"
        assets.mkdir()
        data = assets / "data.txt"
        data.write_text("managed\n", encoding="utf-8")
        source.chmod(0o555)
        assets.chmod(0o555)
        workspace = ManagedAdapterWorkspace(self.config())

        try:
            workspace.install_skills()
            target = workspace.skills_dir / "agent-check"
            self.assertEqual((target / "assets/data.txt").read_text(), "managed\n")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o555)
            self.assertEqual(stat.S_IMODE((target / "assets").stat().st_mode), 0o555)
            workspace.install_skills()
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o555)
            workspace.remove_managed_skills()
            self.assertFalse(target.exists())
        finally:
            assets.chmod(0o755)
            source.chmod(0o755)

    def test_refresh_synchronizes_file_mode_in_both_directions(self) -> None:
        source = _write_skill(self.home, "agent-check", "source\n")
        manifest = source / "SKILL.md"
        manifest.chmod(0o755)
        workspace = ManagedAdapterWorkspace(self.config())

        workspace.install_skills()
        target = workspace.skills_dir / "agent-check/SKILL.md"
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

        manifest.chmod(0o644)
        workspace.install_skills()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o644)

        manifest.chmod(0o755)
        workspace.install_skills()
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o755)

    def test_non_force_refresh_preserves_modified_tree_and_force_replaces_it(self) -> None:
        source = _write_skill(self.home, "agent-check", "source-v1\n")
        workspace = ManagedAdapterWorkspace(self.config())
        workspace.install_skills()
        target = workspace.skills_dir / "agent-check"
        extra = target / "USER.md"
        extra.write_text("user-owned\n", encoding="utf-8")
        (source / "SKILL.md").write_text("source-v2\n", encoding="utf-8")

        messages = workspace.install_skills()

        self.assertTrue(any("Keeping modified managed skill" in item for item in messages))
        self.assertEqual((target / "SKILL.md").read_text(), "source-v1\n")
        self.assertEqual(extra.read_text(), "user-owned\n")

        forced = ManagedAdapterWorkspace(self.config(force=True))
        forced.install_skills()
        self.assertEqual((target / "SKILL.md").read_text(), "source-v2\n")
        self.assertFalse(extra.exists())

    def test_readonly_managed_tree_uninstalls_without_partial_tombstone(self) -> None:
        source = _write_skill(self.home, "agent-check", "source\n")
        assets = source / "assets"
        assets.mkdir()
        (assets / "data.txt").write_text("managed\n", encoding="utf-8")
        source.chmod(0o555)
        assets.chmod(0o555)
        workspace = ManagedAdapterWorkspace(self.config())

        try:
            workspace.install_skills()
            target = workspace.skills_dir / "agent-check"
            messages = workspace.remove_managed_skills()
            self.assertTrue(any("Removed" in item for item in messages))
            self.assertFalse(target.exists())
            self.assertEqual(
                list(workspace.skills_dir.glob(".*.agent-rails-remove-*")),
                [],
            )
        finally:
            assets.chmod(0o755)
            source.chmod(0o755)

    def test_partial_uninstall_rewrites_survivor_inventory_then_force_cleans(self) -> None:
        _write_skill(self.home, "agent-check", "managed-check\n")
        _write_skill(self.home, "agent-context", "managed-context\n")
        config = self.config()
        installer = ManagedAdapterWorkspace(config)
        installer.install_skills()
        installer.write_managed_skills()
        survivor = installer.skills_dir / "agent-context"
        (survivor / "USER.md").write_text("user-owned\n", encoding="utf-8")

        uninstall = ManagedAdapterWorkspace(config)
        uninstall.load_managed_skills()
        uninstall.preflight_removal()
        messages = uninstall.remove_managed_skills()
        uninstall.remove_managed_skills_file()

        self.assertTrue(any("Keeping modified" in item for item in messages))
        self.assertFalse((installer.skills_dir / "agent-check").exists())
        self.assertTrue(survivor.exists())
        inventory = json.loads(config.managed_skills_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [entry["name"] for entry in inventory["skills"]],
            ["agent-context"],
        )

        forced = ManagedAdapterWorkspace(self.config(force=True))
        forced.load_managed_skills()
        forced.preflight_removal()
        forced.remove_managed_skills()
        forced.remove_managed_skills_file()
        self.assertFalse(survivor.exists())
        self.assertFalse(config.managed_skills_path.exists())

    def test_removal_rechecks_fingerprint_after_atomic_detach(self) -> None:
        _write_skill(self.home, "agent-check", "managed\n")
        config = self.config()
        installer = ManagedAdapterWorkspace(config)
        installer.install_skills()
        installer.write_managed_skills()
        target = installer.skills_dir / "agent-check"

        uninstall = ManagedAdapterWorkspace(config)
        uninstall.load_managed_skills()
        uninstall.preflight_removal()
        extra = target / "USER.md"
        extra.write_text("arrived-after-preflight\n", encoding="utf-8")
        messages = uninstall.remove_managed_skills()
        uninstall.remove_managed_skills_file()

        self.assertTrue(any("Keeping modified" in item for item in messages))
        self.assertEqual(extra.read_text(), "arrived-after-preflight\n")
        self.assertTrue(config.managed_skills_path.exists())

    def test_refresh_rechecks_fingerprint_before_atomic_swap(self) -> None:
        source = _write_skill(self.home, "agent-check", "source-v1\n")
        workspace = ManagedAdapterWorkspace(self.config())
        workspace.install_skills()
        target = workspace.skills_dir / "agent-check"
        (source / "SKILL.md").write_text("source-v2\n", encoding="utf-8")
        original_open = __import__(
            "agent_rails.adapters.workspace",
            fromlist=["_open_skill_source_directory"],
        )._open_skill_source_directory
        calls = 0

        def open_with_race(home: Path, skill_name: str) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                (target / "USER.md").write_text(
                    "arrived-after-preflight\n",
                    encoding="utf-8",
                )
            return original_open(home, skill_name)

        with mock.patch(
            "agent_rails.adapters.workspace._open_skill_source_directory",
            side_effect=open_with_race,
        ):
            messages = workspace.install_skills()

        self.assertTrue(any("Keeping modified" in item for item in messages))
        self.assertEqual((target / "SKILL.md").read_text(), "source-v1\n")
        self.assertEqual(
            (target / "USER.md").read_text(),
            "arrived-after-preflight\n",
        )

    def test_refresh_restores_old_tree_when_backup_cleanup_fails(self) -> None:
        source = _write_skill(self.home, "agent-check", "source-v1\n")
        workspace = ManagedAdapterWorkspace(self.config())
        workspace.install_skills()
        target = workspace.skills_dir / "agent-check"
        (source / "SKILL.md").write_text("source-v2\n", encoding="utf-8")
        original_remove_tree = workspace_module._remove_tree_at

        def fail_backup_cleanup(parent: int, name: str) -> None:
            if ".agent-rails-old-" in name:
                raise OSError("injected backup cleanup failure")
            original_remove_tree(parent, name)

        with mock.patch(
            "agent_rails.adapters.workspace._remove_tree_at",
            side_effect=fail_backup_cleanup,
        ):
            with self.assertRaisesRegex(
                ManagedAdapterWorkspaceError,
                "Unable to install Agent Rails skill",
            ):
                workspace.install_skills()

        self.assertEqual((target / "SKILL.md").read_text(), "source-v1\n")
        self.assertEqual(
            list(workspace.skills_dir.glob(".agent-check.agent-rails-old-*")),
            [],
        )
        self.assertEqual(
            list(workspace.skills_dir.glob(".agent-check.agent-rails-stage-*")),
            [],
        )

    def test_staged_skill_file_copy_streams_chunks(self) -> None:
        source = self.home / "large-skill-file"
        payload = b"a" * (2 * 1024 * 1024 + 17)
        source.write_bytes(payload)
        destination = self.project / "destination"
        destination.mkdir()
        source_descriptor = os.open(source, os.O_RDONLY)
        destination_descriptor = os.open(destination, os.O_RDONLY)
        original_read = os.read
        original_write = os.write
        events: list[str] = []

        def recording_read(descriptor: int, size: int) -> bytes:
            if descriptor == source_descriptor:
                events.append(f"read:{size}")
            return original_read(descriptor, size)

        def recording_write(descriptor: int, data: bytes) -> int:
            events.append(f"write:{len(data)}")
            return original_write(descriptor, data)

        try:
            with mock.patch.object(
                workspace_module.os,
                "read",
                side_effect=recording_read,
            ), mock.patch.object(
                workspace_module.os,
                "write",
                side_effect=recording_write,
            ):
                workspace_module._write_staged_file_at(
                    destination_descriptor,
                    "copied",
                    source_descriptor,
                    0o640,
                )
        finally:
            os.close(destination_descriptor)
            os.close(source_descriptor)

        self.assertEqual((destination / "copied").read_bytes(), payload)
        self.assertEqual(stat.S_IMODE((destination / "copied").stat().st_mode), 0o640)
        self.assertEqual(events[:4], [
            "read:1048576",
            "write:1048576",
            "read:1048576",
            "write:1048576",
        ])

    def test_missing_skills_root_clears_stale_inventory(self) -> None:
        _write_skill(self.home, "agent-check", "managed\n")
        config = self.config()
        installer = ManagedAdapterWorkspace(config)
        installer.install_skills()
        installer.write_managed_skills()
        shutil.rmtree(installer.skills_dir)

        uninstall = ManagedAdapterWorkspace(config)
        uninstall.load_managed_skills()
        uninstall.preflight_removal()
        uninstall.remove_managed_skills()
        uninstall.remove_managed_skills_file()

        self.assertFalse(config.managed_skills_path.exists())

    def test_unreadable_managed_tree_fails_removal_preflight(self) -> None:
        source = _write_skill(self.home, "agent-check", "managed\n")
        secret = source / "secret"
        secret.mkdir()
        (secret / "data.txt").write_text("managed\n", encoding="utf-8")
        config = self.config()
        installer = ManagedAdapterWorkspace(config)
        installer.install_skills()
        installer.write_managed_skills()
        target_secret = installer.skills_dir / "agent-check/secret"
        target_secret.chmod(0)

        try:
            uninstall = ManagedAdapterWorkspace(config)
            uninstall.load_managed_skills()
            with self.assertRaisesRegex(
                ManagedAdapterWorkspaceError,
                "preflight managed skill removal",
            ):
                uninstall.preflight_removal()
            self.assertTrue(config.managed_skills_path.exists())
            self.assertTrue((target_secret.parent / "SKILL.md").exists())
        finally:
            target_secret.chmod(0o755)

    def test_local_mode_does_not_mutate_skills_behind_tracked_inventory(self) -> None:
        source = _write_skill(self.home, "agent-check", "source-v1\n")
        project_workspace = ManagedAdapterWorkspace(self.config())
        project_workspace.install_skills()
        project_workspace.write_managed_skills()
        target = project_workspace.skills_dir / "agent-check/SKILL.md"
        inventory_before = project_workspace.config.managed_skills_path.read_bytes()
        self.init_git()
        _git(self.project, "add", ".adapter/.agent-rails-managed-skills")
        _git(self.project, "commit", "-qm", "tracked inventory")
        (source / "SKILL.md").write_text("source-v2\n", encoding="utf-8")

        local_workspace = ManagedAdapterWorkspace(self.config(protect_tracked=True))
        local_workspace.load_managed_skills()
        messages = local_workspace.install_skills()

        self.assertTrue(any("Keeping tracked managed skill inventory" in item for item in messages))
        self.assertEqual(target.read_text(), "source-v1\n")
        self.assertEqual(
            local_workspace.config.managed_skills_path.read_bytes(),
            inventory_before,
        )

    def test_tracked_path_snapshot_uses_one_git_query(self) -> None:
        self.init_git()
        config = self.config()
        config.guide_path.parent.mkdir(parents=True)
        config.guide_path.write_text("tracked\n", encoding="utf-8")
        skill = config.project / config.skills_relative_dir / "agent-check/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("tracked\n", encoding="utf-8")
        self.commit_all()
        workspace = ManagedAdapterWorkspace(config)

        with mock.patch.object(workspace, "_git", wraps=workspace._git) as git:
            self.assertTrue(workspace.is_tracked_file(config.guide_path))
            self.assertTrue(workspace.is_tracked_file(config.guide_path))
            self.assertTrue(
                workspace._is_tracked_prefix(".adapter/skills/agent-check")
            )

        self.assertEqual(git.call_count, 1)
        git.assert_called_once_with("ls-files", "-z")

    def test_tracked_path_snapshot_fails_closed_when_git_query_fails(self) -> None:
        self.init_git()
        workspace = ManagedAdapterWorkspace(self.config())
        failed = subprocess.CompletedProcess(
            args=("git", "ls-files", "-z"),
            returncode=128,
            stdout="",
            stderr="fatal: injected failure",
        )

        with mock.patch.object(workspace, "_git", return_value=failed) as git:
            for _ in range(2):
                with self.assertRaisesRegex(
                    ManagedAdapterWorkspaceError,
                    "Unable to query Git tracked paths",
                ):
                    workspace.is_tracked_file(workspace.config.guide_path)

        self.assertIsNone(workspace._tracked_paths)
        self.assertEqual(git.call_count, 2)

    def test_remove_managed_inventory_honors_dry_run(self) -> None:
        config = self.config(dry_run=True)
        config.managed_skills_path.parent.mkdir(parents=True)
        config.managed_skills_path.write_text("agent-check\n", encoding="utf-8")
        workspace = ManagedAdapterWorkspace(config)
        self.assertEqual(
            workspace.remove_managed_skills_file(),
            (f"Would remove {config.managed_skills_path}",),
        )
        self.assertTrue(config.managed_skills_path.exists())


if __name__ == "__main__":
    unittest.main()
