#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from typing import Dict, Optional
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.adapters.opencode import (
    OpenCodeAction,
    OpenCodeAdapterError,
    OpenCodeAdapterInputError,
    OpenCodeConfigError,
    OpenCodeDoctorRequest,
    OpenCodeEvent,
    OpenCodeEventStream,
    OpenCodeInstallMode,
    OpenCodeInstallRequest,
    OpenCodeUninstallRequest,
    run_opencode_adapter,
)
from agent_rails.config.target_project import resolve_target_project
from agent_rails.adapters.workspace import (
    ManagedAdapterWorkspace,
    ManagedAdapterWorkspaceError,
)
from agent_rails import cli as agent_rails_cli


def _git(project: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class OpenCodeAdapterApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-opencode-application-"
        )
        root = Path(self.temporary.name)
        self.home = root / "kit"
        self.project = root / "project"
        self.user_home = root / "user"
        self.bin_dir = root / "bin"
        for path in (
            self.home / "profiles",
            self.home / "templates",
            self.home / "bin",
            self.home / "scripts",
            self.home / "skills" / "agent-context-pack",
            self.project,
            self.user_home,
            self.bin_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.home / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (self.home / "profiles" / "default.profile").write_text(
            "\n".join(
                (
                    'AGENT_RAILS_TOKENIZER="auto"',
                    'AGENT_RAILS_OPENCODE_CONTEXT_PERCENT="25"',
                    'AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS="30000"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.home / "templates" / "opencode-agent-rails-plugin.mjs").write_text(
            "\n".join(
                (
                    "// <!-- agent-rails:generated -->",
                    "const CONFIG = __AGENT_RAILS_CONFIG__",
                    'const hook = "experimental.chat.system.transform"',
                    'const messages = "client.session.messages"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.home / "skills" / "agent-context-pack" / "SKILL.md").write_text(
            "agent-context-pack\n", encoding="utf-8"
        )
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def init_git(self) -> None:
        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.name", "Agent Rails Test")
        _git(
            self.project,
            "config",
            "user.email",
            "agent-rails@example.invalid",
        )
        (self.project / "README.md").write_text("# fixture\n", encoding="utf-8")
        _git(self.project, "add", "README.md")
        _git(self.project, "commit", "-qm", "fixture")

    def install_request(
        self,
        *,
        profile: Optional[str] = None,
        mode: OpenCodeInstallMode = OpenCodeInstallMode.LOCAL,
        dry_run: bool = False,
        force: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> OpenCodeInstallRequest:
        return OpenCodeInstallRequest(
            requested_project=self.project,
            kit_home=self.home,
            explicit_profile=profile,
            mode=mode,
            dry_run=dry_run,
            force=force,
            environment=self.environment if environment is None else environment,
        )

    def uninstall_request(
        self, *, dry_run: bool = False, force: bool = False
    ) -> OpenCodeUninstallRequest:
        return OpenCodeUninstallRequest(
            requested_project=self.project,
            kit_home=self.home,
            explicit_profile=None,
            dry_run=dry_run,
            force=force,
            environment=self.environment,
        )

    def test_local_install_writes_adapter_config_inventory_and_ignore_in_order(
        self,
    ) -> None:
        self.init_git()

        result = run_opencode_adapter(self.install_request())

        self.assertEqual(result.action, OpenCodeAction.INSTALL)
        self.assertEqual(result.mode, OpenCodeInstallMode.LOCAL)
        self.assertEqual(result.project_root, self.project.resolve())
        lines = result.stdout.splitlines()
        self.assertEqual(
            lines[:5],
            [
                "Agent Rails opencode Install",
                "Version: 1.2.3",
                f"Project: {self.project.resolve()}",
                f"Profile: {self.home.resolve() / 'profiles' / 'default.profile'}",
                "Mode: local",
            ],
        )
        self.assertLess(
            result.stdout.index("Installed "),
            result.stdout.index(
                "Wrote "
                + str(self.project.resolve() / ".opencode/AGENT_RAILS.md")
            ),
        )
        self.assertLess(
            result.stdout.index("Merged Agent Rails plugin"),
            result.stdout.index("Wrote managed skill inventory"),
        )
        self.assertTrue(result.stdout.endswith("to take effect.\n"))

        guide = self.project / ".opencode" / "AGENT_RAILS.md"
        plugin = self.project / ".opencode" / "plugins" / "agent-rails.mjs"
        config = self.project / ".opencode" / "opencode.json"
        inventory = self.project / ".opencode" / ".agent-rails-managed-skills"
        self.assertIn("Visible session marker protocol", guide.read_text())
        self.assertIn(str(self.project.resolve()), plugin.read_text())
        self.assertIn(
            str(self.home.resolve() / "bin" / "agent-rails"), plugin.read_text()
        )
        self.assertEqual(
            json.loads(config.read_text()),
            {
                "$schema": "https://opencode.ai/config.json",
                "plugin": [str(plugin.resolve())],
            },
        )
        inventory_data = json.loads(inventory.read_text(encoding="utf-8"))
        self.assertEqual(inventory_data["format"], "agent-rails-managed-skills-v2")
        self.assertEqual(
            [entry["name"] for entry in inventory_data["skills"]],
            ["agent-context-pack"],
        )
        self.assertRegex(inventory_data["skills"][0]["sha256"], r"^[0-9a-f]{64}$")
        exclude = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude").stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = self.project / exclude
        self.assertIn("# Agent Rails opencode adapter", exclude.read_text())
        self.assertIn(".opencode/plugins/agent-rails.mjs", exclude.read_text())

    def test_profile_is_loaded_once_and_only_allowlisted_values_reach_plugin(
        self,
    ) -> None:
        self.init_git()
        count = self.project.parent / "profile-count"
        profile = self.project.parent / "custom.profile"
        profile.write_text(
            "\n".join(
                (
                    'count=0; [[ ! -f "{}" ]] || count="$(cat "{}")"'.format(
                        count, count
                    ),
                    'printf "%s\\n" "$((count + 1))" > "{}"'.format(count),
                    'TASK_PACK_PATH="relative-pack.md"',
                    'AGENT_RAILS_TOKENIZER="command"',
                    'AGENT_RAILS_TOKENIZER_CMD="printf 17"',
                    'AGENT_RAILS_OPENCODE_CONTEXT_PERCENT="31"',
                    'AGENT_RAILS_OPENCODE_MAX_PACK_TOKENS="not-a-number"',
                    'AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS="17000"',
                    'SHOULD_NOT_LEAK="profile-secret"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(self.environment)
        environment["AGENT_RAILS_OPENCODE_CONTEXT_PERCENT"] = "99"

        result = run_opencode_adapter(
            self.install_request(profile=str(profile), environment=environment)
        )

        plugin = (self.project / ".opencode/plugins/agent-rails.mjs").read_text()
        self.assertEqual(count.read_text(), "1\n")
        self.assertIn('"tokenizer": "command"', plugin)
        self.assertIn('"tokenizerCommand": "printf 17"', plugin)
        self.assertIn('"contextPercent": 31', plugin)
        self.assertIn('"maxPackTokens": 60000', plugin)
        self.assertIn('"hookTimeoutMs": 17000', plugin)
        self.assertNotIn("profile-secret", plugin)
        self.assertIn("Task Pack: relative-pack.md", result.stdout)

    def test_project_mode_removes_local_config_and_ignore_without_machine_paths(
        self,
    ) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        config = self.project / ".opencode/opencode.json"
        data = json.loads(config.read_text())
        data.update(
            {
                "plugin": ["file:///tmp/user.mjs", data["plugin"][0]],
                "instructions": [
                    "USER_RULES.md",
                    str(self.project.resolve() / ".opencode/AGENT_RAILS.md"),
                ],
                "theme": "system",
            }
        )
        config.write_text(json.dumps(data) + "\n", encoding="utf-8")

        result = run_opencode_adapter(
            self.install_request(mode=OpenCodeInstallMode.PROJECT)
        )

        self.assertIn("Mode: project", result.stdout)
        data = json.loads(config.read_text())
        self.assertEqual(data["plugin"], ["file:///tmp/user.mjs"])
        self.assertEqual(data["instructions"], ["USER_RULES.md"])
        self.assertEqual(data["theme"], "system")
        plugin = (self.project / ".opencode/plugins/agent-rails.mjs").read_text()
        guide = (self.project / ".opencode/AGENT_RAILS.md").read_text()
        self.assertIn('"bin": "agent-rails"', plugin)
        self.assertIn('"project": ""', plugin)
        self.assertIn('"profile": ""', plugin)
        self.assertNotIn(str(self.home.resolve()), plugin)
        self.assertNotIn(str(self.home.resolve()), guide)
        exclude = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude").stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = self.project / exclude
        self.assertNotIn("# Agent Rails opencode adapter", exclude.read_text())

    def test_project_mode_uses_portable_tokenizer_settings(self) -> None:
        self.init_git()
        profile = self.project.parent / "machine-bound.profile"
        personal_command = "/Users/me/bin/token-counter"
        personal_tokenizer = "/Users/me/models/tokenizer"
        profile.write_text(
            "\n".join(
                (
                    'AGENT_RAILS_TOKENIZER="command"',
                    f'AGENT_RAILS_TOKENIZER_CMD="{personal_command}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(self.environment)
        environment["AGENT_RAILS_TOKENIZER_PATH"] = personal_tokenizer

        run_opencode_adapter(
            self.install_request(
                profile=str(profile),
                mode=OpenCodeInstallMode.PROJECT,
                environment=environment,
            )
        )

        plugin = (self.project / ".opencode/plugins/agent-rails.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn('"tokenizer": "auto"', plugin)
        self.assertIn('"tokenizerCommand": ""', plugin)
        self.assertIn('"tokenizerPath": ""', plugin)
        self.assertNotIn(personal_command, plugin)
        self.assertNotIn(personal_tokenizer, plugin)

    def test_dry_run_reports_complete_plan_without_writing(self) -> None:
        self.init_git()
        exclude = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude").stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = self.project / exclude
        before = exclude.read_bytes()

        result = run_opencode_adapter(self.install_request(dry_run=True))

        self.assertFalse((self.project / ".opencode").exists())
        self.assertEqual(exclude.read_bytes(), before)
        self.assertIn("Would install", result.stdout)
        self.assertEqual(result.stdout.count("Would write"), 7)
        self.assertIn("Would ensure local ignore entries", result.stdout)
        self.assertIn("  .opencode/opencode.json", result.stdout)

    def test_tracked_config_is_preserved_in_local_mode_unless_forced(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        original = '{"theme":"team"}\n'
        config.write_text(original, encoding="utf-8")
        _git(self.project, "add", ".opencode/opencode.json")
        _git(self.project, "commit", "-qm", "team config")

        result = run_opencode_adapter(self.install_request())

        self.assertEqual(config.read_text(), original)
        self.assertIn("Keeping tracked opencode config in local mode", result.stdout)
        self.assertIn("OpenCode auto-discovers project plugins", result.stdout)

        forced = run_opencode_adapter(self.install_request(force=True))
        self.assertIn("Merged Agent Rails plugin", forced.stdout)
        self.assertEqual(json.loads(config.read_text())["theme"], "team")
        self.assertIn(
            str(self.project.resolve() / ".opencode/plugins/agent-rails.mjs"),
            json.loads(config.read_text())["plugin"],
        )

    def test_invalid_config_and_template_fail_without_overwriting_config(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        invalid = "{ invalid json\n"
        config.write_text(invalid, encoding="utf-8")

        with self.assertRaisesRegex(OpenCodeConfigError, "will not overwrite"):
            run_opencode_adapter(self.install_request())
        self.assertEqual(config.read_text(), invalid)
        with self.assertRaisesRegex(OpenCodeConfigError, "Failed to parse"):
            run_opencode_adapter(self.uninstall_request())
        self.assertEqual(config.read_text(), invalid)

        invalid_plugin = '{"plugin":"not-an-array","theme":"user"}\n'
        config.write_text(invalid_plugin, encoding="utf-8")
        with self.assertRaisesRegex(OpenCodeConfigError, "array of strings"):
            run_opencode_adapter(self.install_request())
        self.assertEqual(config.read_text(), invalid_plugin)

        config.unlink()
        template = self.home / "templates/opencode-agent-rails-plugin.mjs"
        template.write_text("const CONFIG = {}\n", encoding="utf-8")
        before = set(self.project.rglob("*"))
        with self.assertRaisesRegex(OpenCodeConfigError, "Expected one"):
            run_opencode_adapter(self.install_request())
        self.assertEqual(set(self.project.rglob("*")), before)

    def test_install_rejects_opencode_symlink_escape_without_external_writes(
        self,
    ) -> None:
        self.init_git()
        outside = self.project.parent / "outside"
        outside.mkdir()
        sentinel = outside / "user-owned.txt"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        (self.project / ".opencode").symlink_to(outside, target_is_directory=True)
        before_entries = tuple(sorted(path.name for path in outside.iterdir()))
        before_sentinel = sentinel.read_bytes()

        error = None
        try:
            run_opencode_adapter(self.install_request())
        except OpenCodeAdapterError as exc:
            error = exc

        self.assertEqual(
            tuple(sorted(path.name for path in outside.iterdir())),
            before_entries,
        )
        self.assertEqual(sentinel.read_bytes(), before_sentinel)
        self.assertIsNotNone(error, "symlink escape install must fail closed")

    def test_install_rejects_ignore_symlink_before_workspace_mutation(self) -> None:
        outside = self.project.parent / "outside-ignore"
        outside.mkdir()
        ignore = outside / "ignore"
        ignore.write_text("user-owned\n", encoding="utf-8")
        (self.project / ".gitignore").symlink_to(ignore)

        with self.assertRaisesRegex(OpenCodeAdapterError, "symbolic-link"):
            run_opencode_adapter(self.install_request())

        self.assertEqual(ignore.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse((self.project / ".opencode").exists())

    def test_install_rejects_git_ignore_parent_symlink_before_mutation(self) -> None:
        self.init_git()
        info = self.project / ".git/info"
        shutil.rmtree(info)
        outside = self.project.parent / "outside-git-info"
        outside.mkdir()
        exclude = outside / "exclude"
        exclude.write_text("user-owned\n", encoding="utf-8")
        info.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(OpenCodeAdapterError):
            run_opencode_adapter(self.install_request())

        self.assertEqual(exclude.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse((self.project / ".opencode").exists())

    def test_install_rejects_readonly_ignore_parent_before_mutation(self) -> None:
        self.init_git()
        info = self.project / ".git/info"
        exclude = info / "exclude"
        before = exclude.read_bytes()
        info.chmod(0o555)

        try:
            for dry_run in (False, True):
                with self.subTest(dry_run=dry_run):
                    with self.assertRaisesRegex(
                        OpenCodeAdapterError,
                        "ignore parent is not writable",
                    ):
                        run_opencode_adapter(
                            self.install_request(dry_run=dry_run)
                        )
                    self.assertFalse((self.project / ".opencode").exists())
                    self.assertEqual(exclude.read_bytes(), before)
        finally:
            info.chmod(0o755)

    def test_uninstall_rejects_readonly_ignore_parent_before_mutation(self) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        opencode = self.project / ".opencode"
        observed = (
            opencode / "opencode.json",
            opencode / ".agent-rails-state.json",
            opencode / ".agent-rails-managed-skills",
            opencode / "plugins/agent-rails.mjs",
            opencode / "skills/agent-context-pack/SKILL.md",
        )
        before = {path: path.read_bytes() for path in observed}
        info = self.project / ".git/info"
        exclude = info / "exclude"
        before_exclude = exclude.read_bytes()
        info.chmod(0o555)

        try:
            for dry_run in (False, True):
                with self.subTest(dry_run=dry_run):
                    with self.assertRaisesRegex(
                        OpenCodeAdapterError,
                        "ignore parent is not writable",
                    ):
                        run_opencode_adapter(
                            self.uninstall_request(dry_run=dry_run)
                        )
                    for path, content in before.items():
                        self.assertEqual(path.read_bytes(), content)
                    self.assertEqual(exclude.read_bytes(), before_exclude)
        finally:
            info.chmod(0o755)

    def test_uninstall_preserves_preexisting_custom_schema_only_config(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        original = (
            json.dumps(
                {"$schema": "https://example.invalid/custom-opencode.schema.json"},
                indent=2,
            )
            + "\n"
        )
        config.write_text(original, encoding="utf-8")

        run_opencode_adapter(self.install_request())
        run_opencode_adapter(self.uninstall_request())

        self.assertTrue(config.is_file())
        self.assertEqual(config.read_text(encoding="utf-8"), original)

    def test_uninstall_removes_config_created_by_install(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"

        run_opencode_adapter(self.install_request())
        self.assertTrue(config.is_file())

        run_opencode_adapter(self.uninstall_request())

        self.assertFalse(config.exists())

    def test_uninstall_preserves_schema_changed_after_install(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        run_opencode_adapter(self.install_request())
        data = json.loads(config.read_text(encoding="utf-8"))
        data["$schema"] = "https://example.invalid/user-schema.json"
        data["theme"] = "user"
        config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        run_opencode_adapter(self.uninstall_request())

        remaining = json.loads(config.read_text(encoding="utf-8"))
        self.assertEqual(
            remaining,
            {
                "$schema": "https://example.invalid/user-schema.json",
                "theme": "user",
            },
        )

    def test_install_rejects_unmanaged_plugin_before_any_mutation(self) -> None:
        self.init_git()
        plugin = self.project / ".opencode/plugins/agent-rails.mjs"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("user-owned plugin\n", encoding="utf-8")
        config = self.project / ".opencode/opencode.json"
        current_entry = str(plugin.resolve())
        config.write_text(
            json.dumps(
                {
                    "plugin": [current_entry],
                    "theme": "user",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        before_config = config.read_bytes()
        before_plugin = plugin.read_bytes()

        with self.assertRaisesRegex(
            OpenCodeConfigError, "unmanaged OpenCode plugin target"
        ):
            run_opencode_adapter(self.install_request())

        self.assertEqual(config.read_bytes(), before_config)
        self.assertEqual(plugin.read_bytes(), before_plugin)
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-state.json").exists()
        )
        self.assertFalse((self.project / ".opencode/AGENT_RAILS.md").exists())
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

    def test_install_rejects_non_file_plugin_before_any_mutation(self) -> None:
        self.init_git()
        plugin = self.project / ".opencode/plugins/agent-rails.mjs"
        plugin.mkdir(parents=True)
        sentinel = plugin / "user-owned.txt"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        config = self.project / ".opencode/opencode.json"
        current_entry = str(plugin.resolve())
        config.write_text(
            json.dumps(
                {
                    "plugin": [current_entry, "file:///tmp/user-plugin.mjs"],
                    "theme": "user",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        before_config = config.read_bytes()

        with self.assertRaisesRegex(
            OpenCodeConfigError, "plugin target is not a regular file"
        ):
            run_opencode_adapter(
                self.install_request(mode=OpenCodeInstallMode.PROJECT)
            )

        self.assertEqual(config.read_bytes(), before_config)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-state.json").exists()
        )
        self.assertFalse((self.project / ".opencode/AGENT_RAILS.md").exists())
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

    def test_force_install_replaces_unmanaged_regular_plugin(self) -> None:
        self.init_git()
        plugin = self.project / ".opencode/plugins/agent-rails.mjs"
        plugin.parent.mkdir(parents=True)
        plugin.write_text("user-owned plugin\n", encoding="utf-8")

        run_opencode_adapter(self.install_request(force=True))

        self.assertIn(
            "// <!-- agent-rails:generated -->",
            plugin.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            json.loads(
                (self.project / ".opencode/opencode.json").read_text(
                    encoding="utf-8"
                )
            )["plugin"],
            [str(plugin.resolve())],
        )

    def test_install_rejects_unverified_plugin_entry_in_ownership_state(self) -> None:
        self.init_git()
        opencode = self.project / ".opencode"
        opencode.mkdir()
        config = opencode / "opencode.json"
        config.write_text(
            json.dumps({"plugin": ["file:///tmp/user-owned-plugin.mjs"]}) + "\n",
            encoding="utf-8",
        )
        state = opencode / ".agent-rails-state.json"
        state.write_text(
            json.dumps(
                {
                    "format": "agent-rails-opencode-state-v1",
                    "configExistedBeforeInstall": True,
                    "schemaInserted": False,
                    "insertedPluginEntries": [
                        "file:///tmp/user-owned-plugin.mjs"
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = config.read_bytes()

        with self.assertRaisesRegex(OpenCodeConfigError, "unverified plugin entry"):
            run_opencode_adapter(
                self.install_request(mode=OpenCodeInstallMode.PROJECT)
            )

        self.assertEqual(config.read_bytes(), before)
        self.assertFalse((opencode / "plugins/agent-rails.mjs").exists())

    def test_local_lifecycle_never_mutates_tracked_ownership_state(self) -> None:
        self.init_git()
        opencode = self.project / ".opencode"
        opencode.mkdir()
        state = opencode / ".agent-rails-state.json"
        state.write_text(
            json.dumps(
                {
                    "format": "agent-rails-opencode-state-v1",
                    "configExistedBeforeInstall": False,
                    "schemaInserted": True,
                    "insertedPluginEntries": [
                        str(self.project / ".opencode/plugins/agent-rails.mjs")
                    ],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        _git(self.project, "add", ".opencode/.agent-rails-state.json")
        _git(self.project, "commit", "-qm", "tracked ownership state")
        before_state = state.read_bytes()

        with self.assertRaisesRegex(OpenCodeConfigError, "tracked.*ownership state"):
            run_opencode_adapter(self.install_request())
        self.assertEqual(state.read_bytes(), before_state)
        self.assertFalse((opencode / "plugins/agent-rails.mjs").exists())

        config = opencode / "opencode.json"
        config.write_text(
            json.dumps(
                {
                    "$schema": "https://opencode.ai/config.json",
                    "plugin": [
                        str(self.project / ".opencode/plugins/agent-rails.mjs")
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before_config = config.read_bytes()
        with self.assertRaisesRegex(OpenCodeConfigError, "tracked.*ownership state"):
            run_opencode_adapter(self.uninstall_request())
        self.assertEqual(state.read_bytes(), before_state)
        self.assertEqual(config.read_bytes(), before_config)

    def test_project_install_after_repo_move_removes_only_stale_managed_plugin(
        self,
    ) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        old_plugin = str(
            self.project.resolve() / ".opencode/plugins/agent-rails.mjs"
        )
        config = self.project / ".opencode/opencode.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        user_plugin = "file:///tmp/user-plugins/agent-rails.mjs"
        data["plugin"].append(user_plugin)
        config.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

        moved_project = self.project.with_name("renamed-project")
        self.project.rename(moved_project)
        self.project = moved_project

        run_opencode_adapter(
            self.install_request(mode=OpenCodeInstallMode.PROJECT)
        )

        moved_config = self.project / ".opencode/opencode.json"
        plugins = json.loads(moved_config.read_text(encoding="utf-8"))["plugin"]
        self.assertNotIn(old_plugin, plugins)
        self.assertEqual(plugins, [user_plugin])

    def test_invalid_config_install_leaves_workspace_and_ignore_unchanged(
        self,
    ) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        invalid = b"{ invalid json\n"
        config.write_bytes(invalid)
        exclude = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude")
            .stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = self.project / exclude
        before_exclude = exclude.read_bytes()
        before_entries = tuple(
            sorted(
                path.relative_to(config.parent).as_posix()
                for path in config.parent.rglob("*")
            )
        )

        with self.assertRaisesRegex(OpenCodeConfigError, "will not overwrite"):
            run_opencode_adapter(self.install_request())

        self.assertEqual(config.read_bytes(), invalid)
        self.assertEqual(exclude.read_bytes(), before_exclude)
        self.assertEqual(
            tuple(
                sorted(
                    path.relative_to(config.parent).as_posix()
                    for path in config.parent.rglob("*")
                )
            ),
            before_entries,
        )

    def test_invalid_config_dry_run_fails_without_workspace_or_ignore_changes(
        self,
    ) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        invalid = b"{ invalid json\n"
        config.write_bytes(invalid)
        exclude = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude")
            .stdout.strip()
        )
        if not exclude.is_absolute():
            exclude = self.project / exclude
        before_exclude = exclude.read_bytes()
        before_entries = tuple(
            sorted(
                path.relative_to(config.parent).as_posix()
                for path in config.parent.rglob("*")
            )
        )

        with self.assertRaisesRegex(OpenCodeConfigError, "will not overwrite"):
            run_opencode_adapter(self.install_request(dry_run=True))

        self.assertEqual(config.read_bytes(), invalid)
        self.assertEqual(exclude.read_bytes(), before_exclude)
        self.assertEqual(
            tuple(
                sorted(
                    path.relative_to(config.parent).as_posix()
                    for path in config.parent.rglob("*")
                )
            ),
            before_entries,
        )

    def test_install_preserves_instruction_for_unmanaged_guide(self) -> None:
        self.init_git()
        guide = self.project / ".opencode/AGENT_RAILS.md"
        guide.parent.mkdir()
        guide.write_text("user-owned guide\n", encoding="utf-8")
        config = self.project / ".opencode/opencode.json"
        config.write_text(
            json.dumps(
                {
                    "instructions": [
                        ".opencode/AGENT_RAILS.md",
                        "USER.md",
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        run_opencode_adapter(self.install_request())

        self.assertEqual(guide.read_text(encoding="utf-8"), "user-owned guide\n")
        self.assertEqual(
            json.loads(config.read_text(encoding="utf-8"))["instructions"],
            [".opencode/AGENT_RAILS.md", "USER.md"],
        )

    def test_inventory_directory_fails_preflight_for_real_and_dry_run(self) -> None:
        self.init_git()
        inventory = self.project / ".opencode/.agent-rails-managed-skills"
        inventory.mkdir(parents=True)

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(
                    OpenCodeAdapterError, "inventory is not a regular file"
                ):
                    run_opencode_adapter(self.install_request(dry_run=dry_run))
                self.assertTrue(inventory.is_dir())
                self.assertFalse(
                    (self.project / ".opencode/plugins/agent-rails.mjs").exists()
                )
                self.assertFalse(
                    (self.project / ".opencode/opencode.json").exists()
                )

    def test_inventory_load_failure_is_cli_exit_one_without_traceback(self) -> None:
        self.init_git()
        stdout = io.StringIO()
        stderr = io.StringIO()
        environment = dict(self.environment)
        environment["AGENT_RAILS_HOME"] = str(self.home)
        failure = ManagedAdapterWorkspaceError(
            "Unable to read managed skill inventory: simulated failure"
        )

        with mock.patch.dict(os.environ, environment, clear=True):
            with mock.patch.object(
                ManagedAdapterWorkspace,
                "load_managed_skills",
                side_effect=failure,
            ):
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    exit_code = agent_rails_cli.main(
                        [
                            "opencode-adapter",
                            "doctor",
                            "--project",
                            str(self.project),
                        ]
                    )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("Unable to read managed skill inventory", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_doctor_reports_cli_and_each_adapter_artifact_in_stable_order(self) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        executable = self.bin_dir / "opencode"
        executable.write_text(
            "#!/bin/sh\nprintf '0.42.0\\nnightly\\n'\n", encoding="utf-8"
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        environment = dict(self.environment)
        environment["PATH"] = f"{self.bin_dir}:{environment['PATH']}"

        result = run_opencode_adapter(
            OpenCodeDoctorRequest(
                requested_project=self.project,
                kit_home=self.home,
                explicit_profile=None,
                environment=environment,
            )
        )

        lines = result.stdout.splitlines()
        self.assertEqual(lines[0], "Agent Rails opencode Doctor")
        self.assertEqual(lines[1], "Version: 1.2.3")
        self.assertEqual(lines[2], f"Project: {self.project.resolve()}")
        self.assertEqual(lines[3], f"[OK] opencode CLI: {executable}")
        self.assertEqual(lines[4:6], ["Version: 0.42.0", "Version: nightly"])
        labels = [
            "[OK] opencode Agent Rails guide",
            "[OK] opencode request hook",
            "[OK] opencode config loads Agent Rails plugin",
            "[OK] opencode command",
        ]
        positions = [
            next(index for index, line in enumerate(lines) if label in line)
            for label in labels
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(sum("[OK] opencode command" in line for line in lines), 3)

    def test_invalid_inventory_fails_doctor_closed(self) -> None:
        self.init_git()
        inventory = self.project / ".opencode/.agent-rails-managed-skills"
        inventory.parent.mkdir()
        inventory.write_text("../escape\nagent-context-pack\n", encoding="utf-8")

        with self.assertRaisesRegex(
            OpenCodeAdapterError,
            "valid managed skill inventory",
        ):
            run_opencode_adapter(
                OpenCodeDoctorRequest(
                    requested_project=self.project,
                    kit_home=self.home,
                    explicit_profile=None,
                    environment=self.environment,
                )
            )

    def test_plain_inventory_fails_real_and_dry_install_without_mutation(self) -> None:
        self.init_git()
        inventory = self.project / ".opencode/.agent-rails-managed-skills"
        inventory.parent.mkdir()
        inventory.write_text("agent-context-pack\n", encoding="utf-8")
        before = inventory.read_bytes()

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(
                    OpenCodeAdapterError,
                    "valid managed skill inventory",
                ):
                    run_opencode_adapter(self.install_request(dry_run=dry_run))
                self.assertEqual(inventory.read_bytes(), before)
                self.assertEqual(
                    sorted(
                        path.relative_to(inventory.parent).as_posix()
                        for path in inventory.parent.rglob("*")
                    ),
                    [".agent-rails-managed-skills"],
                )

    def test_unreadable_skill_fails_uninstall_before_other_mutations(self) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        opencode = self.project / ".opencode"
        config = opencode / "opencode.json"
        plugin = opencode / "plugins/agent-rails.mjs"
        guide = opencode / "AGENT_RAILS.md"
        before = {
            path: path.read_bytes()
            for path in (config, plugin, guide, opencode / ".agent-rails-state.json")
        }
        secret = opencode / "skills/agent-context-pack/secret"
        secret.mkdir()
        (secret / "data.txt").write_text("managed\n", encoding="utf-8")
        secret.chmod(0)

        try:
            with self.assertRaisesRegex(
                OpenCodeAdapterError,
                "preflight managed skill removal",
            ):
                run_opencode_adapter(self.uninstall_request())
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)
        finally:
            secret.chmod(0o755)

    def test_uninstall_preflights_all_artifacts_before_config_mutation(
        self,
    ) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        opencode = self.project / ".opencode"
        check = opencode / "command/agent-rails-check.md"
        check.unlink()
        check.mkdir()
        (check / "user-owned.txt").write_text("user-owned\n", encoding="utf-8")
        observed = (
            opencode / "opencode.json",
            opencode / ".agent-rails-state.json",
            opencode / ".agent-rails-managed-skills",
            opencode / "plugins/agent-rails.mjs",
            opencode / "AGENT_RAILS.md",
            opencode / "command/agent-rails-pack.md",
            opencode / "command/agent-rails-lite.md",
            opencode / "skills/agent-context-pack/SKILL.md",
        )
        before = {path: path.read_bytes() for path in observed}

        with self.assertRaisesRegex(
            OpenCodeConfigError,
            "artifact scheduled for removal is not a regular file",
        ):
            run_opencode_adapter(self.uninstall_request(force=True))

        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue(check.is_dir())
        self.assertEqual(
            (check / "user-owned.txt").read_text(encoding="utf-8"),
            "user-owned\n",
        )

    def test_missing_skills_root_uninstalls_stale_inventory(self) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        skills = self.project / ".opencode/skills"
        shutil.rmtree(skills)

        run_opencode_adapter(self.uninstall_request())

        self.assertFalse(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

    def test_uninstall_dry_run_then_removes_only_managed_state(self) -> None:
        self.init_git()
        config = self.project / ".opencode/opencode.json"
        config.parent.mkdir()
        config.write_text(
            json.dumps(
                {
                    "plugin": ["file:///tmp/user-plugin.mjs"],
                    "instructions": [
                        "USER_RULES.md",
                        str(
                            self.project.resolve()
                            / ".opencode/AGENT_RAILS.md"
                        ),
                    ],
                    "theme": "system",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        run_opencode_adapter(self.install_request())
        user_skill = self.project / ".opencode/skills/agent-custom/SKILL.md"
        user_skill.parent.mkdir()
        user_skill.write_text("user-owned\n", encoding="utf-8")
        plugin = self.project.resolve() / ".opencode/plugins/agent-rails.mjs"
        config = self.project.resolve() / ".opencode/opencode.json"

        dry_run = run_opencode_adapter(self.uninstall_request(dry_run=True))

        self.assertIn("Agent Rails opencode Uninstall", dry_run.stdout)
        self.assertLess(
            dry_run.stdout.index("Would remove Agent Rails plugin"),
            dry_run.stdout.index(f"Would remove {plugin}"),
        )
        self.assertTrue(plugin.exists())
        self.assertTrue(config.exists())

        result = run_opencode_adapter(self.uninstall_request())

        self.assertIn(f"Updated {config}", result.stdout)
        self.assertFalse(plugin.exists())
        remaining = json.loads(config.read_text())
        self.assertEqual(remaining["plugin"], ["file:///tmp/user-plugin.mjs"])
        self.assertEqual(remaining["instructions"], ["USER_RULES.md"])
        self.assertEqual(remaining["theme"], "system")
        self.assertFalse(
            (self.project / ".opencode/skills/agent-context-pack").exists()
        )
        self.assertEqual(user_skill.read_text(), "user-owned\n")

    def test_uninstall_preserves_modified_skill_symlink_until_force(self) -> None:
        self.init_git()
        run_opencode_adapter(self.install_request())
        skill = self.project / ".opencode/skills/agent-context-pack"
        shutil.rmtree(skill)
        outside = self.project.parent / "outside-skill"
        outside.mkdir()
        sentinel = outside / "SKILL.md"
        sentinel.write_text("user-owned\n", encoding="utf-8")
        skill.symlink_to(outside, target_is_directory=True)

        result = run_opencode_adapter(self.uninstall_request())

        self.assertIn("Keeping modified managed skill", result.stdout)
        self.assertTrue(skill.is_symlink())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse((self.project / ".opencode/opencode.json").exists())
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-state.json").exists()
        )
        self.assertTrue(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

        run_opencode_adapter(self.uninstall_request(force=True))

        self.assertFalse(skill.exists())
        self.assertFalse(skill.is_symlink())
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "user-owned\n")
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

    def test_generated_legacy_guide_does_not_claim_existing_skill(self) -> None:
        self.init_git()
        guide = self.project / ".opencode/AGENT_RAILS.md"
        skill = self.project / ".opencode/skills/agent-context-pack/SKILL.md"
        skill.parent.mkdir(parents=True)
        guide.write_text(
            "Agent Rails Version: 0.5.1\nVisible session marker protocol\n",
            encoding="utf-8",
        )
        skill.write_text("legacy\n", encoding="utf-8")

        run_opencode_adapter(self.install_request())

        self.assertEqual(skill.read_text(), "legacy\n")
        self.assertFalse(
            (self.project / ".opencode/.agent-rails-managed-skills").exists()
        )

    def test_typed_request_rejects_non_enum_mode_and_non_boolean_policy(self) -> None:
        with self.assertRaises(OpenCodeAdapterInputError):
            run_opencode_adapter(
                OpenCodeInstallRequest(
                    requested_project=self.project,
                    kit_home=self.home,
                    explicit_profile=None,
                    mode="local",  # type: ignore[arg-type]
                    dry_run=False,
                    force=False,
                    environment=self.environment,
                )
            )
        with self.assertRaises(OpenCodeAdapterInputError):
            run_opencode_adapter(
                OpenCodeUninstallRequest(
                    requested_project=self.project,
                    kit_home=self.home,
                    explicit_profile=None,
                    dry_run=1,  # type: ignore[arg-type]
                    force=False,
                    environment=self.environment,
                )
            )

    def test_pre_resolved_context_accepts_subdirectory_but_rejects_nested_repo(
        self,
    ) -> None:
        self.init_git()
        context = resolve_target_project(
            self.project,
            kit_home=self.home,
            environment=self.environment,
            require_profile=True,
            load_profile=True,
        )
        nested = self.project / "nested/path"
        nested.mkdir(parents=True)

        accepted = run_opencode_adapter(
            replace(
                self.install_request(dry_run=True),
                requested_project=nested,
            ),
            context=context,
        )

        self.assertEqual(accepted.project_root, self.project.resolve())
        nested_repo = self.project / "nested-repository"
        nested_repo.mkdir()
        _git(nested_repo, "init", "-q")
        with self.assertRaisesRegex(
            OpenCodeAdapterInputError,
            "does not match the requested project",
        ):
            run_opencode_adapter(
                replace(
                    self.install_request(dry_run=True),
                    requested_project=nested_repo,
                ),
                context=context,
            )

    def test_runtime_error_carries_sanitized_partial_events_and_exit_code(
        self,
    ) -> None:
        self.init_git()
        dangerous = "failed-\x1b]0;title\x07-\x85-\u202espoof"

        def fail_install(*, events, **kwargs):
            del kwargs
            events.append(
                OpenCodeEvent(OpenCodeEventStream.STDOUT, "opencode-partial")
            )
            raise OpenCodeAdapterError(
                dangerous,
                exit_code=38,
                events=(
                    OpenCodeEvent(OpenCodeEventStream.STDERR, dangerous),
                ),
            )

        with mock.patch(
            "agent_rails.adapters.opencode._install",
            side_effect=fail_install,
        ):
            with self.assertRaises(OpenCodeAdapterError) as raised:
                run_opencode_adapter(self.install_request(dry_run=True))

        error = raised.exception
        self.assertEqual(error.exit_code, 38)
        self.assertIn("opencode-partial", error.stdout)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\x85", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        for raw in ("\x1b", "\x07", "\x85", "\u202e"):
            self.assertNotIn(raw, error.stderr)
            self.assertNotIn(raw, str(error))


if __name__ == "__main__":
    unittest.main()
