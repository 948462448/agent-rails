#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shlex
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

from agent_rails.adapters.claude import (
    ClaudeAdapterError,
    ClaudeAdapterInputError,
    ClaudeEvent,
    ClaudeEventStream,
    ClaudeInstallMode,
    ClaudeInstallRequest,
    ClaudeUninstallRequest,
    run_claude_adapter,
)
from agent_rails.config.target_project import resolve_target_project


_GENERATED_MARKER = "<!-- agent-rails:generated -->"
_RULES_MARKER = "<!-- agent-rails:start -->"
_RULES_END_MARKER = "<!-- agent-rails:end -->"
_GLOBAL_MARKER = "<!-- agent-rails:global-reminder:start -->"
_GLOBAL_END_MARKER = "<!-- agent-rails:global-reminder:end -->"
_IGNORE_MARKER = "# Agent Rails local adapter"
_IGNORE_END_MARKER = "# Agent Rails local adapter end"


def _git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _tree_snapshot(root: Path) -> tuple[tuple[str, str, int, bytes], ...]:
    """Capture project-visible state without including Git's private metadata."""

    entries = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == ".git":
            continue
        current = path.lstat()
        mode = stat.S_IMODE(current.st_mode)
        if path.is_symlink():
            entries.append(
                (relative.as_posix(), "link", mode, os.fsencode(os.readlink(path)))
            )
        elif path.is_dir():
            entries.append((relative.as_posix(), "dir", mode, b""))
        else:
            entries.append((relative.as_posix(), "file", mode, path.read_bytes()))
    return tuple(entries)


class ClaudeAdapterApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-claude-application-"
        )
        root = Path(self.temporary.name)
        self.home = root / "kit"
        self.project = root / "project"
        self.user_home = root / "user"
        self.user_rules = self.user_home / ".claude" / "CLAUDE.md"
        self.settings = self.user_home / ".claude" / "settings.json"
        for path in (
            self.home / "profiles",
            self.home / "bin",
            self.home / "hooks",
            self.home / "skills" / "agent-context-pack",
            self.project,
            self.user_home,
        ):
            path.mkdir(parents=True, exist_ok=True)

        (self.home / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (self.home / "profiles" / "default.profile").write_text(
            'PROJECT_NAME="claude-fixture"\n', encoding="utf-8"
        )
        executable = self.home / "bin" / "agent-rails"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        hook = self.home / "hooks" / "agent-rails-session-start.sh"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        (self.home / "skills" / "agent-context-pack" / "SKILL.md").write_text(
            "agent-context-pack\n", encoding="utf-8"
        )
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "AGENT_RAILS_CLAUDE_USER_MD": str(self.user_rules),
            "AGENT_RAILS_CLAUDE_SETTINGS": str(self.settings),
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

    def local_ignore_path(self) -> Path:
        value = Path(
            _git(self.project, "rev-parse", "--git-path", "info/exclude")
            .stdout.strip()
        )
        return value if value.is_absolute() else self.project / value

    def install_request(
        self,
        *,
        profile: Optional[str] = None,
        mode: ClaudeInstallMode = ClaudeInstallMode.LOCAL,
        dry_run: bool = False,
        force: bool = False,
        global_reminder: bool = False,
        session_hook: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> ClaudeInstallRequest:
        return ClaudeInstallRequest(
            requested_project=self.project,
            kit_home=self.home,
            explicit_profile=profile,
            mode=mode,
            dry_run=dry_run,
            force=force,
            global_reminder=global_reminder,
            session_hook=session_hook,
            environment=self.environment if environment is None else environment,
        )

    def uninstall_request(
        self,
        *,
        dry_run: bool = False,
        force: bool = False,
        global_reminder: bool = False,
        session_hook: bool = False,
    ) -> ClaudeUninstallRequest:
        return ClaudeUninstallRequest(
            requested_project=self.project,
            kit_home=self.home,
            explicit_profile=None,
            dry_run=dry_run,
            force=force,
            global_reminder=global_reminder,
            session_hook=session_hook,
            environment=self.environment,
        )

    def test_local_install_writes_v2_inventory_generated_rules_and_ignore(
        self,
    ) -> None:
        self.init_git()

        result = run_claude_adapter(self.install_request())

        self.assertEqual(result.mode, ClaudeInstallMode.LOCAL)
        self.assertEqual(result.project_root, self.project.resolve())
        self.assertIn("Claude adapter ready.", result.stdout)
        self.assertIn("Mode: local", result.stdout)

        inventory_path = self.project / ".claude/.agent-rails-managed-skills"
        inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
        self.assertEqual(inventory["format"], "agent-rails-managed-skills-v2")
        self.assertEqual(
            [entry["name"] for entry in inventory["skills"]],
            ["agent-context-pack"],
        )
        self.assertRegex(inventory["skills"][0]["sha256"], r"^[0-9a-f]{64}$")

        generated = (
            self.project / ".claude/AGENT_RAILS.md",
            self.project / ".claude/commands/agent-rails-pack.md",
            self.project / ".claude/commands/agent-rails-lite.md",
            self.project / ".claude/commands/agent-rails-check.md",
        )
        for path in generated:
            with self.subTest(path=path.name):
                self.assertIn(_GENERATED_MARKER, path.read_text(encoding="utf-8"))
        self.assertEqual(
            (self.project / ".claude/skills/agent-context-pack/SKILL.md").read_text(
                encoding="utf-8"
            ),
            "agent-context-pack\n",
        )

        rules = (self.project / "CLAUDE.local.md").read_text(encoding="utf-8")
        self.assertEqual(rules.count(_RULES_MARKER), 1)
        self.assertEqual(rules.count(_RULES_END_MARKER), 1)
        self.assertIn(str(self.home / "bin/agent-rails"), rules)
        self.assertIn(str(self.home / "profiles/default.profile"), rules)
        self.assertFalse((self.project / "CLAUDE.md").exists())

        ignore = self.local_ignore_path().read_text(encoding="utf-8")
        self.assertEqual(ignore.count(_IGNORE_MARKER + "\n"), 1)
        self.assertEqual(ignore.count(_IGNORE_END_MARKER + "\n"), 1)
        self.assertIn(".claude/.agent-rails-managed-skills", ignore)
        self.assertIn(".claude/skills/agent-*/", ignore)
        self.assertIn("CLAUDE.local.md", ignore)

    def test_project_install_promotes_local_adapter_to_portable_visible_files(
        self,
    ) -> None:
        self.init_git()
        run_claude_adapter(self.install_request())
        ignore_path = self.local_ignore_path()

        result = run_claude_adapter(
            self.install_request(mode=ClaudeInstallMode.PROJECT)
        )

        self.assertEqual(result.mode, ClaudeInstallMode.PROJECT)
        self.assertIn("Mode: project", result.stdout)
        self.assertFalse((self.project / "CLAUDE.local.md").exists())
        portable_paths = (
            self.project / "CLAUDE.md",
            self.project / ".claude/AGENT_RAILS.md",
            self.project / ".claude/commands/agent-rails-pack.md",
            self.project / ".claude/commands/agent-rails-lite.md",
            self.project / ".claude/commands/agent-rails-check.md",
        )
        for path in portable_paths:
            with self.subTest(path=path.name):
                content = path.read_text(encoding="utf-8")
                self.assertIn("agent-rails", content)
                self.assertNotIn(str(self.home), content)
                self.assertNotIn(str(self.project), content)
                self.assertNotIn(str(self.home / "profiles/default.profile"), content)
        self.assertNotIn(
            _IGNORE_MARKER, ignore_path.read_text(encoding="utf-8")
        )
        for relative in ("CLAUDE.md", ".claude/AGENT_RAILS.md"):
            completed = subprocess.run(
                ("git", "-C", str(self.project), "check-ignore", "-q", relative),
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0, relative)

    def test_uninstall_exact_cleanup_preserves_user_rules_and_unrelated_skills(
        self,
    ) -> None:
        self.init_git()
        team_rules = self.project / "CLAUDE.md"
        team_rules.write_text("# Team rules\n", encoding="utf-8")
        run_claude_adapter(self.install_request())
        local_rules = self.project / "CLAUDE.local.md"
        local_rules.write_text(
            local_rules.read_text(encoding="utf-8") + "\n# User local rule\n",
            encoding="utf-8",
        )
        user_skill = self.project / ".claude/skills/user-owned/SKILL.md"
        user_skill.parent.mkdir(parents=True)
        user_skill.write_text("user-owned\n", encoding="utf-8")
        user_command = self.project / ".claude/commands/user-command.md"
        user_command.write_text("user-owned\n", encoding="utf-8")

        result = run_claude_adapter(self.uninstall_request())

        self.assertIn("Claude adapter removed.", result.stdout)
        for path in (
            self.project / ".claude/AGENT_RAILS.md",
            self.project / ".claude/commands/agent-rails-pack.md",
            self.project / ".claude/commands/agent-rails-lite.md",
            self.project / ".claude/commands/agent-rails-check.md",
            self.project / ".claude/skills/agent-context-pack",
            self.project / ".claude/.agent-rails-managed-skills",
        ):
            with self.subTest(path=path):
                self.assertFalse(path.exists())
        self.assertEqual(team_rules.read_text(encoding="utf-8"), "# Team rules\n")
        self.assertEqual(user_skill.read_text(encoding="utf-8"), "user-owned\n")
        self.assertEqual(user_command.read_text(encoding="utf-8"), "user-owned\n")
        remaining_rules = local_rules.read_text(encoding="utf-8")
        self.assertIn("# User local rule", remaining_rules)
        self.assertNotIn(_RULES_MARKER, remaining_rules)
        self.assertNotIn(_RULES_END_MARKER, remaining_rules)
        self.assertNotIn(
            _IGNORE_MARKER,
            self.local_ignore_path().read_text(encoding="utf-8"),
        )

    def test_global_reminder_requires_force_to_replace_its_existing_block(
        self,
    ) -> None:
        self.init_git()
        self.user_rules.parent.mkdir(parents=True)
        self.user_rules.write_text("# User global rule\n", encoding="utf-8")
        run_claude_adapter(self.install_request(global_reminder=True))
        installed = self.user_rules.read_text(encoding="utf-8")
        self.assertEqual(installed.count(_GLOBAL_MARKER), 1)
        self.assertEqual(installed.count(_GLOBAL_END_MARKER), 1)
        stale = installed.replace("## Agent Rails", "## Stale Agent Rails", 1)
        self.user_rules.write_text(stale, encoding="utf-8")

        kept = run_claude_adapter(self.install_request(global_reminder=True))

        self.assertEqual(self.user_rules.read_text(encoding="utf-8"), stale)
        self.assertIn("already exists", kept.stdout)
        self.assertIn("--force", kept.stdout)

        replaced = run_claude_adapter(
            self.install_request(global_reminder=True, force=True)
        )

        content = self.user_rules.read_text(encoding="utf-8")
        self.assertIn("# User global rule", content)
        self.assertIn("## Agent Rails", content)
        self.assertNotIn("## Stale Agent Rails", content)
        self.assertEqual(content.count(_GLOBAL_MARKER), 1)
        self.assertIn("Replaced global Agent Rails reminder", replaced.stdout)

    def test_session_start_settings_preserve_user_hooks_and_are_idempotent(
        self,
    ) -> None:
        self.init_git()
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text(
            json.dumps(
                {
                    "theme": "dark",
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "echo user-session-hook",
                                    }
                                ],
                            }
                        ],
                        "Notification": [
                            {
                                "matcher": "all",
                                "hooks": [
                                    {"type": "command", "command": "echo notify"}
                                ],
                            }
                        ],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        for _ in range(2):
            run_claude_adapter(self.install_request(session_hook=True))

        installed = json.loads(self.settings.read_text(encoding="utf-8"))
        self.assertEqual(installed["theme"], "dark")
        rendered = json.dumps(installed)
        self.assertIn("echo user-session-hook", rendered)
        self.assertIn("echo notify", rendered)
        self.assertEqual(rendered.count("agent-rails-session-start.sh"), 1)
        self.assertIn("Loading Agent Rails...", rendered)

        run_claude_adapter(self.uninstall_request(session_hook=True))

        remaining = json.loads(self.settings.read_text(encoding="utf-8"))
        rendered = json.dumps(remaining)
        self.assertEqual(remaining["theme"], "dark")
        self.assertIn("echo user-session-hook", rendered)
        self.assertIn("echo notify", rendered)
        self.assertNotIn("agent-rails-session-start.sh", rendered)

    def test_malformed_rules_fail_preflight_without_project_mutation(self) -> None:
        self.init_git()
        rules = self.project / "CLAUDE.local.md"
        rules.write_text(
            "# User rule\n\n<!-- agent-rails:start -->\nunterminated\n",
            encoding="utf-8",
        )
        before = _tree_snapshot(self.project)

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(ClaudeAdapterError):
                    run_claude_adapter(self.install_request(dry_run=dry_run))
                self.assertEqual(_tree_snapshot(self.project), before)

    def test_malformed_settings_fail_preflight_without_project_mutation(self) -> None:
        self.init_git()
        self.settings.parent.mkdir(parents=True)
        malformed = b'{"hooks": invalid json\n'
        self.settings.write_bytes(malformed)
        before = _tree_snapshot(self.project)

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaises(ClaudeAdapterError):
                    run_claude_adapter(
                        self.install_request(session_hook=True, dry_run=dry_run)
                    )
                self.assertEqual(_tree_snapshot(self.project), before)
                self.assertEqual(self.settings.read_bytes(), malformed)

    def test_readonly_ignore_parent_fails_preflight_without_project_mutation(
        self,
    ) -> None:
        self.init_git()
        info = self.project / ".git/info"
        exclude = info / "exclude"
        before_exclude = exclude.read_bytes()
        before = _tree_snapshot(self.project)
        info.chmod(0o555)

        try:
            for dry_run in (False, True):
                with self.subTest(dry_run=dry_run):
                    with self.assertRaisesRegex(
                        ClaudeAdapterError, "ignore parent is not writable"
                    ):
                        run_claude_adapter(self.install_request(dry_run=dry_run))
                    self.assertEqual(_tree_snapshot(self.project), before)
                    self.assertEqual(exclude.read_bytes(), before_exclude)
        finally:
            info.chmod(0o755)

    def test_generated_artifact_directory_fails_preflight_without_mutation(
        self,
    ) -> None:
        self.init_git()
        conflict = self.project / ".claude/AGENT_RAILS.md"
        conflict.mkdir(parents=True)
        (conflict / "user-owned.txt").write_text(
            "user-owned\n", encoding="utf-8"
        )
        exclude = self.local_ignore_path()
        before_exclude = exclude.read_bytes()
        before = _tree_snapshot(self.project)

        for dry_run in (False, True):
            with self.subTest(dry_run=dry_run):
                with self.assertRaisesRegex(
                    ClaudeAdapterError, "not a regular file"
                ):
                    run_claude_adapter(self.install_request(dry_run=dry_run))
                self.assertEqual(_tree_snapshot(self.project), before)
                self.assertEqual(exclude.read_bytes(), before_exclude)

    def test_session_hook_quotes_special_kit_path_as_one_shell_argument(
        self,
    ) -> None:
        self.init_git()
        sentinel = Path(self.temporary.name) / "CLAUDE_SESSION_HOOK_SENTINEL"
        special_home = self.home.with_name(
            "kit-$(touch CLAUDE_SESSION_HOOK_SENTINEL)-'; printf unsafe; #"
        )
        self.home.rename(special_home)
        self.home = special_home

        run_claude_adapter(self.install_request(session_hook=True))

        settings = json.loads(self.settings.read_text(encoding="utf-8"))
        handlers = [
            handler
            for group in settings["hooks"]["SessionStart"]
            for handler in group.get("hooks", [])
            if "agent-rails-session-start.sh" in handler.get("command", "")
        ]
        self.assertEqual(len(handlers), 1)
        command = handlers[0]["command"]
        arguments = shlex.split(command)
        self.assertEqual(arguments[0], "bash")
        self.assertEqual(
            arguments[1],
            os.path.realpath(self.home / "hooks/agent-rails-session-start.sh"),
        )
        self.assertEqual(arguments[2:], [";", "exit", "0"])

        subprocess.run(
            command,
            shell=True,
            cwd=self.temporary.name,
            check=True,
            env=self.environment,
        )
        self.assertFalse(sentinel.exists())

    def test_dry_run_reports_full_plan_without_mutating_project_or_user_files(
        self,
    ) -> None:
        self.init_git()
        before = _tree_snapshot(self.project)
        exclude = self.local_ignore_path()
        before_exclude = exclude.read_bytes()

        result = run_claude_adapter(
            self.install_request(
                dry_run=True,
                global_reminder=True,
                session_hook=True,
            )
        )

        self.assertIn("Would install", result.stdout)
        self.assertIn("Would append Agent Rails block", result.stdout)
        self.assertIn("Would append global Agent Rails reminder", result.stdout)
        self.assertIn("Would install Agent Rails SessionStart hook", result.stdout)
        self.assertEqual(_tree_snapshot(self.project), before)
        self.assertEqual(exclude.read_bytes(), before_exclude)
        self.assertFalse(self.user_rules.exists())
        self.assertFalse(self.settings.exists())

    def test_typed_requests_reject_invalid_mode_and_non_boolean_policies(self) -> None:
        invalid_mode = replace(self.install_request(), mode="local")  # type: ignore[arg-type]
        invalid_force = replace(self.install_request(), force=1)  # type: ignore[arg-type]
        invalid_hook = replace(
            self.uninstall_request(), session_hook="yes"  # type: ignore[arg-type]
        )

        for request in (invalid_mode, invalid_force, invalid_hook):
            with self.subTest(request=type(request).__name__):
                with self.assertRaises(ClaudeAdapterInputError):
                    run_claude_adapter(request)
        with self.assertRaises(ClaudeAdapterInputError):
            run_claude_adapter(object())  # type: ignore[arg-type]

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

        accepted = run_claude_adapter(
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
            ClaudeAdapterInputError,
            "does not match the requested project",
        ):
            run_claude_adapter(
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
                ClaudeEvent(ClaudeEventStream.STDOUT, "claude-partial")
            )
            raise ClaudeAdapterError(
                dangerous,
                exit_code=37,
                events=(
                    ClaudeEvent(ClaudeEventStream.STDERR, dangerous),
                ),
            )

        with mock.patch(
            "agent_rails.adapters.claude._install",
            side_effect=fail_install,
        ):
            with self.assertRaises(ClaudeAdapterError) as raised:
                run_claude_adapter(self.install_request(dry_run=True))

        error = raised.exception
        self.assertEqual(error.exit_code, 37)
        self.assertIn("claude-partial", error.stdout)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\x85", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        for raw in ("\x1b", "\x07", "\x85", "\u202e"):
            self.assertNotIn(raw, error.stderr)
            self.assertNotIn(raw, str(error))


if __name__ == "__main__":
    unittest.main()
