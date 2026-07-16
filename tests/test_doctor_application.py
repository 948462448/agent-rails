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
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.adapters.claude import (
    ClaudeInstallMode,
    ClaudeInstallRequest,
    run_claude_adapter,
)
from agent_rails.diagnostics.doctor import (
    DoctorInputError,
    DoctorRequest,
    run_doctor,
)
from agent_rails.config.target_project import resolve_target_project


def _git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class DoctorApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-doctor-application-"
        )
        root = Path(self.temporary.name)
        self.kit_home = root / "kit"
        self.project = root / "project"
        self.user_home = root / "user"
        self.default_profile = self.kit_home / "profiles/default.profile"
        self.user_rules = self.user_home / ".claude/CLAUDE.md"
        self.settings = self.user_home / ".claude/settings.json"

        for path in (
            self.kit_home / "profiles",
            self.kit_home / "bin",
            self.kit_home / "hooks",
            self.kit_home / "skills/agent-demo",
            self.project,
            self.user_home,
        ):
            path.mkdir(parents=True, exist_ok=True)

        (self.kit_home / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        self.default_profile.write_text(
            "\n".join(
                (
                    'AGENT_RAILS_CONFIG_HOME="${AGENT_RAILS_CONFIG_HOME:-$HOME/.agent-rails}"',
                    'MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"',
                    'AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-generic}"',
                    'AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-normal}"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        executable = self.kit_home / "bin/agent-rails"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        hook = self.kit_home / "hooks/agent-rails-session-start.sh"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        hook.chmod(0o755)
        (self.kit_home / "skills/agent-demo/SKILL.md").write_text(
            "# Agent Demo\n", encoding="utf-8"
        )

        for relative in (
            ".codex-plugin/plugin.json",
            ".claude-plugin/plugin.json",
            "codex-marketplace/plugins/agent-rails/.codex-plugin/plugin.json",
        ):
            manifest = self.kit_home / relative
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(
                json.dumps({"version": "1.2.3"}) + "\n", encoding="utf-8"
            )

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

        self.environment: Dict[str, str] = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "AGENT_RAILS_CLAUDE_USER_MD": str(self.user_rules),
            "AGENT_RAILS_CLAUDE_SETTINGS": str(self.settings),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def doctor_request(
        self,
        *,
        profile: Optional[str] = None,
        online_memory_smoke: bool = False,
        fix: bool = False,
        fix_mode: ClaudeInstallMode = ClaudeInstallMode.LOCAL,
        fix_session_hook: bool = False,
        fix_global_reminder: bool = False,
        dry_run: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> DoctorRequest:
        return DoctorRequest(
            requested_project=self.project,
            kit_home=self.kit_home,
            explicit_profile=(
                str(self.default_profile) if profile is None else profile
            ),
            online_memory_smoke=online_memory_smoke,
            fix=fix,
            fix_mode=fix_mode,
            fix_session_hook=fix_session_hook,
            fix_global_reminder=fix_global_reminder,
            dry_run=dry_run,
            environment=self.environment if environment is None else environment,
        )

    def install_claude(
        self,
        *,
        environment: Optional[Dict[str, str]] = None,
    ) -> None:
        run_claude_adapter(
            ClaudeInstallRequest(
                requested_project=self.project,
                kit_home=self.kit_home,
                explicit_profile=str(self.default_profile),
                mode=ClaudeInstallMode.LOCAL,
                dry_run=False,
                force=False,
                global_reminder=False,
                session_hook=False,
                environment=(
                    self.environment if environment is None else environment
                ),
            )
        )

    def test_clean_local_install_reports_ok(self) -> None:
        self.install_claude()

        result = run_doctor(self.doctor_request())

        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.events)
        self.assertEqual(result.stderr, "")
        self.assertIn("Agent Rails Doctor", result.stdout)
        self.assertIn("Kit version: 1.2.3", result.stdout)
        self.assertIn("Claude adapter version: 1.2.3", result.stdout)
        self.assertIn(
            "Agent Rails adapter files are ignored locally", result.stdout
        )
        self.assertIn("skill installed: agent-demo", result.stdout)
        self.assertTrue(result.stdout.endswith("Doctor status: OK\n"))

    def test_missing_profile_is_accumulated_and_finishes_full_report(self) -> None:
        missing = Path(self.temporary.name) / "missing.profile"

        result = run_doctor(self.doctor_request(profile=str(missing)))

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn(f"[FAIL] Profile not found: {missing}", result.stdout)
        self.assertIn("\nTools\n", result.stdout)
        self.assertIn("\nClaude Adapter\n", result.stdout)
        self.assertIn("\nSuggested Commands\n", result.stdout)
        self.assertIn("Doctor status: FAIL (1 failure(s)", result.stdout)

    def test_missing_explicit_profile_escapes_terminal_control_and_bidi_characters(
        self,
    ) -> None:
        missing = Path(self.temporary.name) / (
            "missing\nprofile-\x1b]0;doctor-title-\u202espoof.profile"
        )
        visible = (
            str(missing)
            .replace("\n", "\\n")
            .replace("\x1b", "\\x1b")
            .replace("\u202e", "\\u202e")
        )

        result = run_doctor(self.doctor_request(profile=str(missing)))

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn(f"[FAIL] Profile not found: {visible}", result.stdout)
        self.assertIn(visible, result.stdout)
        self.assertNotIn(str(missing), result.stdout)
        self.assertNotIn("missing\nprofile", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\u202e", result.stdout)

    def test_project_claude_parent_symlink_is_not_followed(self) -> None:
        outside = Path(self.temporary.name) / "outside-claude"
        commands = outside / "commands"
        skill = outside / "skills/agent-demo"
        commands.mkdir(parents=True)
        skill.mkdir(parents=True)
        guide_secret = "external-private-guide-version-123456"
        command_secret = "external-private-command-body-123456"
        skill_secret = "external-private-skill-body-123456"
        (outside / "AGENT_RAILS.md").write_text(
            f"Agent Rails Version: `{guide_secret}`\n",
            encoding="utf-8",
        )
        for name in (
            "agent-rails-pack.md",
            "agent-rails-lite.md",
            "agent-rails-check.md",
        ):
            (commands / name).write_text(
                f"{command_secret}\ngit rev-parse --show-toplevel\n",
                encoding="utf-8",
            )
        (skill / "SKILL.md").write_text(
            f"# External Skill\n{skill_secret}\n", encoding="utf-8"
        )
        (self.project / ".claude").symlink_to(outside, target_is_directory=True)

        result = run_doctor(self.doctor_request())

        self.assertEqual(result.exit_code, 0)
        self.assertNotIn("Claude guide installed:", result.stdout)
        self.assertNotIn("Claude pack command installed.", result.stdout)
        self.assertNotIn("Claude lite command installed.", result.stdout)
        self.assertNotIn("Claude check command installed.", result.stdout)
        self.assertNotIn("skill installed: agent-demo", result.stdout)
        self.assertIn("Missing Claude guide:", result.stdout)
        self.assertIn("Missing Claude pack command:", result.stdout)
        self.assertIn("Missing Claude lite command:", result.stdout)
        self.assertIn("Missing Claude check command:", result.stdout)
        self.assertIn("skill missing from project: agent-demo", result.stdout)
        for secret in (guide_secret, command_secret, skill_secret):
            self.assertNotIn(secret, result.stdout)
            self.assertNotIn(secret, result.stderr)

    def test_profile_and_environment_file_are_loaded_once(self) -> None:
        profile = Path(self.temporary.name) / "doctor.profile"
        env_file = Path(self.temporary.name) / "doctor.env"
        profile_count = Path(self.temporary.name) / "profile-count"
        env_count = Path(self.temporary.name) / "env-count"
        task_pack = Path(self.temporary.name) / "doctor-task-pack.md"
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    f"count_file={shlex.quote(str(profile_count))}",
                    'count=0; [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"',
                    'printf \'%s\\n\' "$((count + 1))" > "$count_file"',
                    f"AGENT_RAILS_ENV_FILE={shlex.quote(str(env_file))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        env_file.write_text(
            "\n".join(
                (
                    f"count_file={shlex.quote(str(env_count))}",
                    'count=0; [[ ! -f "$count_file" ]] || count="$(cat "$count_file")"',
                    'printf \'%s\\n\' "$((count + 1))" > "$count_file"',
                    'PROJECT_NAME="profile-env-project"',
                    'AGENT_RAILS_MODEL="qwen3.7-max"',
                    'AGENT_RAILS_PACK_MODE="audit"',
                    f"TASK_PACK_PATH={shlex.quote(str(task_pack))}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        result = run_doctor(self.doctor_request(profile=str(profile)))

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(profile_count.read_text(encoding="utf-8"), "1\n")
        self.assertEqual(env_count.read_text(encoding="utf-8"), "1\n")
        self.assertIn(f"Env file: {env_file}", result.stdout)
        self.assertIn("Pack mode: audit", result.stdout)
        self.assertIn("Model preset: qwen3.7-max", result.stdout)
        self.assertIn(f"Task Pack path: {task_pack}", result.stdout)

    def test_profile_tilde_claude_paths_use_request_home_not_ambient_home(
        self,
    ) -> None:
        profile = Path(self.temporary.name) / "request-home.profile"
        ambient_home = Path(self.temporary.name) / "ambient-home"
        request_settings = self.user_home / ".claude/doctor-settings.json"
        request_rules = self.user_home / ".claude/doctor-CLAUDE.md"
        ambient_home.mkdir()
        request_settings.parent.mkdir(parents=True, exist_ok=True)
        request_settings.write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": str(
                                            self.kit_home
                                            / "hooks/agent-rails-session-start.sh"
                                        ),
                                    }
                                ]
                            }
                        ]
                    }
                }
            )
            + "\n",
            encoding="utf-8",
        )
        request_rules.write_text(
            "<!-- agent-rails:global-reminder:start -->\n"
            "Use Agent Rails.\n"
            "<!-- agent-rails:global-reminder:end -->\n",
            encoding="utf-8",
        )
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    "AGENT_RAILS_CLAUDE_SETTINGS='~/.claude/doctor-settings.json'",
                    "AGENT_RAILS_CLAUDE_USER_MD='~/.claude/doctor-CLAUDE.md'",
                    "",
                )
            ),
            encoding="utf-8",
        )

        with patch.dict(os.environ, {"HOME": str(ambient_home)}):
            result = run_doctor(
                self.doctor_request(
                    profile=str(profile),
                    fix=True,
                    dry_run=True,
                )
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            f"Claude SessionStart hook installed: {request_settings}", result.stdout
        )
        would_run = next(
            line for line in result.stdout.splitlines() if line.startswith("Would run:")
        )
        self.assertIn("--session-hook", would_run)
        self.assertIn("--global-reminder", would_run)
        self.assertNotIn(str(ambient_home), result.stdout)

    def test_environment_file_source_failure_is_attributed_to_environment_file(
        self,
    ) -> None:
        profile = Path(self.temporary.name) / "broken-env.profile"
        env_file = Path(self.temporary.name) / "broken.env"
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    f"AGENT_RAILS_ENV_FILE={shlex.quote(str(env_file))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        env_file.write_text("false\n", encoding="utf-8")

        result = run_doctor(self.doctor_request(profile=str(profile)))

        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "")
        self.assertIn(
            f"[FAIL] Env file could not be sourced: {env_file}", result.stdout
        )
        self.assertNotIn("Profile could not be sourced:", result.stdout)
        self.assertNotIn("No Agent Rails env file configured.", result.stdout)
        self.assertIn("\nTools\n", result.stdout)

    def test_exported_environment_file_value_reaches_online_memory_adapter_without_leak(
        self,
    ) -> None:
        profile = Path(self.temporary.name) / "online-env-export.profile"
        env_file = Path(self.temporary.name) / "online-env-export.env"
        adapter = Path(self.temporary.name) / "online-env-export-adapter.sh"
        capture = Path(self.temporary.name) / "online-env-export-capture.txt"
        sentinel = "unit-test-doctor-online-export-sentinel-123456"
        adapter.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$DOCTOR_ONLINE_SENTINEL" > "$ONLINE_MEMORY_CAPTURE"
printf '%s\n' 'benign online memory card'
""",
            encoding="utf-8",
        )
        adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)
        env_file.write_text(
            f"export DOCTOR_ONLINE_SENTINEL={shlex.quote(sentinel)}\n",
            encoding="utf-8",
        )
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    f"AGENT_RAILS_ENV_FILE={shlex.quote(str(env_file))}",
                    'MEMORY_PROVIDER="online"',
                    f"AGENT_RAILS_ONLINE_MEMORY_CMD={shlex.quote(str(adapter))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(self.environment)
        environment["ONLINE_MEMORY_CAPTURE"] = str(capture)

        result = run_doctor(
            self.doctor_request(
                profile=str(profile),
                online_memory_smoke=True,
                environment=environment,
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(capture.read_text(encoding="utf-8"), f"{sentinel}\n")
        self.assertIn("Online memory smoke read OK.", result.stdout)
        self.assertNotIn(sentinel, result.stdout)
        self.assertNotIn(sentinel, result.stderr)

    def test_online_memory_smoke_reports_success_without_exposing_cards(self) -> None:
        profile = Path(self.temporary.name) / "online-success.profile"
        adapter = Path(self.temporary.name) / "online-success-adapter.sh"
        capture = Path(self.temporary.name) / "online-success-capture.txt"
        adapter.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
{
  printf 'project=%s\n' "$AGENT_RAILS_MEMORY_PROJECT"
  printf 'limit=%s\n' "$AGENT_RAILS_MEMORY_LIMIT"
  printf 'query='
  tr '\n' ' ' < "$AGENT_RAILS_MEMORY_QUERY_FILE"
  printf '\n'
} > "$ONLINE_MEMORY_CAPTURE"
printf '%s\n' 'private online card body'
""",
            encoding="utf-8",
        )
        adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    'PROJECT_NAME="doctor-online-memory"',
                    'MEMORY_PROVIDER="hybrid"',
                    f"AGENT_RAILS_ONLINE_MEMORY_CMD={shlex.quote(str(adapter))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(self.environment)
        environment["ONLINE_MEMORY_CAPTURE"] = str(capture)

        result = run_doctor(
            self.doctor_request(
                profile=str(profile),
                online_memory_smoke=True,
                environment=environment,
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Memory provider: hybrid", result.stdout)
        self.assertIn("Online memory command configured.", result.stdout)
        self.assertIn("Online memory smoke read OK.", result.stdout)
        self.assertNotIn("private online card body", result.stdout)
        self.assertEqual(result.stderr, "")
        captured = capture.read_text(encoding="utf-8")
        self.assertIn("project=doctor-online-memory", captured)
        self.assertIn("limit=1", captured)
        self.assertIn(
            "query=Agent Rails Doctor online memory smoke. ", captured
        )

    def test_online_memory_failure_suppresses_adapter_diagnostics(self) -> None:
        profile = Path(self.temporary.name) / "online-failure.profile"
        adapter = Path(self.temporary.name) / "online-failure-adapter.sh"
        secret = "unit-test-private-online-memory-error-123456"
        adapter.write_text(
            """#!/usr/bin/env bash
set -euo pipefail
printf 'private adapter error: %s\n' "$DOCTOR_ONLINE_MEMORY_SECRET" >&2
exit 9
""",
            encoding="utf-8",
        )
        adapter.chmod(adapter.stat().st_mode | stat.S_IXUSR)
        profile.write_text(
            "\n".join(
                (
                    f"source {shlex.quote(str(self.default_profile))}",
                    'MEMORY_PROVIDER="online"',
                    f"AGENT_RAILS_ONLINE_MEMORY_CMD={shlex.quote(str(adapter))}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        environment = dict(self.environment)
        environment["DOCTOR_ONLINE_MEMORY_SECRET"] = secret

        result = run_doctor(
            self.doctor_request(
                profile=str(profile),
                online_memory_smoke=True,
                environment=environment,
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(
            "Online memory smoke failed; adapter diagnostics were suppressed.",
            result.stdout,
        )
        self.assertNotIn("private adapter error", result.stdout)
        self.assertNotIn(secret, result.stdout)
        self.assertNotIn(secret, result.stderr)

    def test_fix_refreshes_stale_claude_adapter_through_python_interface(self) -> None:
        stale_environment = dict(self.environment)
        stale_environment["AGENT_RAILS_VERSION_OVERRIDE"] = "0.1.0"
        self.install_claude(environment=stale_environment)

        before = run_doctor(self.doctor_request())
        self.assertIn(
            "Claude adapter version 0.1.0 differs from kit version 1.2.3",
            before.stdout,
        )

        fixed = run_doctor(self.doctor_request(fix=True))

        self.assertEqual(fixed.exit_code, 0)
        self.assertIn("\nFixes\n", fixed.stdout)
        self.assertIn("Doctor fix completed", fixed.stdout)
        guide = (self.project / ".claude/AGENT_RAILS.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("Agent Rails Version: 1.2.3", guide)
        self.assertNotIn("Agent Rails Version: 0.1.0", guide)

        after = run_doctor(self.doctor_request())
        self.assertEqual(after.exit_code, 0)
        self.assertIn("Claude adapter version: 1.2.3", after.stdout)
        self.assertTrue(after.stdout.endswith("Doctor status: OK\n"))

    def test_typed_request_validation_fails_before_diagnostics(self) -> None:
        request = self.doctor_request()
        invalid_requests = (
            replace(request, requested_project=str(self.project)),
            replace(request, kit_home=str(self.kit_home)),
            replace(request, explicit_profile=self.default_profile),
            replace(request, online_memory_smoke=1),
            replace(request, fix="yes"),
            replace(request, fix_mode="local"),
            replace(request, fix_session_hook=1),
            replace(request, fix_global_reminder=1),
            replace(request, dry_run=1),
            replace(request, environment=(("HOME", str(self.user_home)),)),
            replace(request, environment={"HOME": 1}),
        )

        for invalid in invalid_requests:
            with self.subTest(request=invalid):
                with self.assertRaises(DoctorInputError):
                    run_doctor(invalid)

    def test_pre_resolved_context_accepts_subdirectory_but_rejects_nested_repo(
        self,
    ) -> None:
        context = resolve_target_project(
            self.project,
            kit_home=self.kit_home,
            explicit_profile=str(self.default_profile),
            environment=self.environment,
            require_profile=False,
            load_profile=True,
        )
        nested = self.project / "nested/path"
        nested.mkdir(parents=True)

        accepted = run_doctor(
            replace(self.doctor_request(), requested_project=nested),
            context=context,
        )

        self.assertEqual(accepted.project_root, self.project.resolve())
        nested_repo = self.project / "nested-repository"
        nested_repo.mkdir()
        _git(nested_repo, "init", "-q")
        with self.assertRaisesRegex(
            DoctorInputError,
            "does not match the requested project",
        ):
            run_doctor(
                replace(
                    self.doctor_request(), requested_project=nested_repo
                ),
                context=context,
            )


if __name__ == "__main__":
    unittest.main()
