#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from typing import Dict, Optional


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.adapters.codex import (
    CodexAction,
    CodexAdapterError,
    CodexAdapterInputError,
    CodexDoctorRequest,
    CodexEventStream,
    CodexInstallMode,
    CodexInstallRequest,
    CodexUninstallRequest,
    run_codex_adapter,
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


class CodexAdapterApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-codex-application-"
        )
        root = Path(self.temporary.name)
        self.kit_home = root / "kit home;literal"
        self.project = root / "project"
        self.working_directory = root / "working directory"
        self.user_home = root / "user"
        self.fake_bin = root / "fake-bin"
        self.git_only_bin = root / "git-only-bin"
        self.codex_log = root / "codex.log"
        for path in (
            self.kit_home / "bin",
            self.kit_home / "codex-marketplace",
            self.project,
            self.working_directory,
            self.user_home,
            self.fake_bin,
            self.git_only_bin,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.kit_home / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        cli = self.kit_home / "bin/agent-rails"
        cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cli.chmod(cli.stat().st_mode | stat.S_IXUSR)

        git = shutil.which("git")
        if git is None:
            self.fail("git is required by the Codex adapter tests")
        (self.git_only_bin / "git").symlink_to(git)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def environment(
        self,
        *,
        fake_codex: bool = False,
        fail_marketplace_add: bool = False,
        signal_marketplace_add: bool = False,
    ) -> Dict[str, str]:
        path = self.fake_bin if fake_codex else self.git_only_bin
        environment = {
            "HOME": str(self.user_home),
            "PATH": str(path),
            "CODEX_LOG": str(self.codex_log),
        }
        if fail_marketplace_add:
            environment["CODEX_FAIL_MARKETPLACE_ADD"] = "1"
        if signal_marketplace_add:
            environment["CODEX_SIGNAL_MARKETPLACE_ADD"] = "1"
        return environment

    def run_cli(
        self,
        *arguments: str,
        environment: Optional[Dict[str, str]] = None,
        kit_home: Optional[Path] = None,
    ) -> subprocess.CompletedProcess[str]:
        child_environment = dict(
            self.environment() if environment is None else environment
        )
        child_environment["AGENT_RAILS_HOME"] = str(
            self.kit_home if kit_home is None else kit_home
        )
        return subprocess.run(
            (
                sys.executable,
                "-E",
                str(ROOT / "scripts/agent-python-cli.py"),
                "codex-adapter",
                *arguments,
            ),
            cwd=self.working_directory,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
            check=False,
        )

    def install_request(
        self,
        *,
        requested_project: Optional[Path] = None,
        explicit_profile: Optional[str] = None,
        mode: CodexInstallMode = CodexInstallMode.LOCAL,
        fix_project: bool = False,
        dry_run: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> CodexInstallRequest:
        return CodexInstallRequest(
            requested_project=requested_project,
            kit_home=self.kit_home,
            explicit_profile=explicit_profile,
            mode=mode,
            fix_project=fix_project,
            dry_run=dry_run,
            working_directory=self.working_directory,
            environment=(
                self.environment() if environment is None else environment
            ),
        )

    def doctor_request(
        self,
        *,
        requested_project: Optional[Path] = None,
        explicit_profile: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> CodexDoctorRequest:
        return CodexDoctorRequest(
            requested_project=requested_project,
            kit_home=self.kit_home,
            explicit_profile=explicit_profile,
            working_directory=self.working_directory,
            environment=(
                self.environment() if environment is None else environment
            ),
        )

    def uninstall_request(
        self,
        *,
        dry_run: bool = False,
        environment: Optional[Dict[str, str]] = None,
    ) -> CodexUninstallRequest:
        return CodexUninstallRequest(
            kit_home=self.kit_home,
            dry_run=dry_run,
            working_directory=self.working_directory,
            environment=(
                self.environment() if environment is None else environment
            ),
        )

    def install_fake_codex(self) -> Path:
        executable = self.fake_bin / "codex"
        executable.write_text(
            """#!/bin/sh
{
  printf 'BEGIN\\n'
  printf 'cwd=%s\\n' "$PWD"
  printf 'argc=%s\\n' "$#"
  for argument in "$@"; do
    printf 'arg=%s\\n' "$argument"
  done
  printf 'END\\n'
} >> "$CODEX_LOG"

case "${1-}|${2-}|${3-}" in
  'plugin|marketplace|add')
    if [ "${CODEX_SIGNAL_MARKETPLACE_ADD:-}" = 1 ]; then
      kill -TERM "$$"
    fi
    if [ "${CODEX_FAIL_MARKETPLACE_ADD:-}" = 1 ]; then
      printf 'marketplace-add-partial\\n'
      printf 'marketplace add failed-\\033]0;codex-title\\007-‮spoof\\n' >&2
      exit 41
    fi
    printf 'marketplace-added\\n'
    ;;
  'plugin|add|agent-rails@agent-rails-local')
    printf 'plugin-added\\n'
    ;;
  'plugin|marketplace|list')
    printf 'marketplace-visible\\n'
    printf 'private marketplace diagnostic\\n' >&2
    exit 51
    ;;
  'plugin|list|')
    printf 'plugin-visible\\n'
    printf 'private plugin diagnostic\\n' >&2
    exit 52
    ;;
  'plugin|remove|agent-rails@agent-rails-local')
    printf 'plugin-removed\\n'
    ;;
  *)
    printf 'unexpected fake Codex argv\\n' >&2
    exit 90
    ;;
esac
""",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def install_oversized_codex(self, descendant_marker: Path) -> Path:
        executable = self.fake_bin / "codex"
        descendant_code = (
            "from pathlib import Path; import time; "
            "time.sleep(0.8); "
            f"Path({str(descendant_marker)!r}).write_text('survived')"
        )
        executable.write_text(
            f"""#!{sys.executable}
import subprocess
import sys
import time

if sys.argv[1:4] == ["plugin", "marketplace", "add"]:
    subprocess.Popen(
        [sys.executable, "-c", {descendant_code!r}],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sys.stdout.buffer.write(b"x" * 1_000_001)
    sys.stdout.buffer.flush()
    time.sleep(2)
else:
    print("ok")
""",
            encoding="utf-8",
        )
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        return executable

    def codex_calls(self) -> list[tuple[Path, tuple[str, ...]]]:
        if not self.codex_log.exists():
            return []
        calls: list[tuple[Path, tuple[str, ...]]] = []
        lines = self.codex_log.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            self.assertEqual(lines[index], "BEGIN")
            cwd = Path(lines[index + 1].removeprefix("cwd="))
            count = int(lines[index + 2].removeprefix("argc="))
            arguments = tuple(
                line.removeprefix("arg=")
                for line in lines[index + 3 : index + 3 + count]
            )
            self.assertEqual(lines[index + 3 + count], "END")
            calls.append((cwd, arguments))
            index += count + 4
        return calls

    def test_typed_requests_reject_invalid_fields_before_side_effects(self) -> None:
        request = self.install_request(dry_run=True)
        invalid_requests = (
            replace(request, requested_project=str(self.project)),
            replace(request, kit_home=str(self.kit_home)),
            replace(request, explicit_profile=self.kit_home / "profile"),
            replace(request, mode="local"),
            replace(request, fix_project=1),
            replace(request, dry_run=1),
            replace(request, working_directory=str(self.working_directory)),
            replace(request, environment=(("HOME", str(self.user_home)),)),
            replace(request, environment={"HOME": 1}),
            replace(self.uninstall_request(), dry_run=1),
        )

        for invalid in invalid_requests:
            with self.subTest(request=invalid):
                with self.assertRaises(CodexAdapterInputError):
                    run_codex_adapter(invalid)
        with self.assertRaises(CodexAdapterInputError):
            run_codex_adapter(
                self.install_request(fix_project=True, dry_run=True)
            )
        self.assertFalse(self.codex_log.exists())

    def test_pre_resolved_context_accepts_subdirectory_but_rejects_nested_repo(
        self,
    ) -> None:
        environment = self.environment()
        _git(self.project, "init", "-q")
        context = resolve_target_project(
            self.project,
            kit_home=self.kit_home,
            environment=environment,
            require_profile=False,
            load_profile=False,
        )
        nested = self.project / "nested/path"
        nested.mkdir(parents=True)

        accepted = run_codex_adapter(
            self.install_request(
                requested_project=nested,
                dry_run=True,
                environment=environment,
            ),
            context=context,
        )

        self.assertEqual(accepted.project_root, self.project.resolve())
        nested_repo = self.project / "nested-repository"
        nested_repo.mkdir()
        _git(nested_repo, "init", "-q")
        with self.assertRaisesRegex(
            CodexAdapterInputError,
            "does not match the requested project",
        ):
            run_codex_adapter(
                self.install_request(
                    requested_project=nested_repo,
                    dry_run=True,
                    environment=environment,
                ),
                context=context,
            )

    def test_install_dry_run_needs_no_codex_and_never_loads_profile(self) -> None:
        nested = self.project / "nested/path"
        nested.mkdir(parents=True)
        _git(self.project, "init", "-q")
        marker = self.project.parent / "profile-loaded"
        profile = self.project.parent / "do-not-load.profile"
        profile.write_text(
            f"touch {marker}\nexit 97\n",
            encoding="utf-8",
        )

        result = run_codex_adapter(
            self.install_request(
                requested_project=nested,
                explicit_profile=str(profile),
                mode=CodexInstallMode.PROJECT,
                fix_project=True,
                dry_run=True,
                environment=self.environment(fake_codex=False),
            )
        )

        self.assertEqual(result.action, CodexAction.INSTALL)
        self.assertEqual(result.mode, CodexInstallMode.PROJECT)
        self.assertEqual(result.project_root, self.project.resolve())
        self.assertEqual(result.profile_path, str(profile))
        self.assertEqual(result.stderr, "")
        self.assertFalse(marker.exists())
        self.assertFalse(self.codex_log.exists())
        self.assertIn("Agent Rails Codex Install", result.stdout)
        self.assertIn("Would run: codex plugin marketplace add", result.stdout)
        self.assertIn(
            "Would run: codex plugin add agent-rails@agent-rails-local",
            result.stdout,
        )
        self.assertIn("doctor --project", result.stdout)
        self.assertIn("--fix", result.stdout)
        self.assertIn("--mode project", result.stdout)
        self.assertIn("--profile", result.stdout)

    def test_install_uses_exact_argv_order_and_short_circuits_on_failure(self) -> None:
        self.install_fake_codex()
        environment = self.environment(fake_codex=True)

        result = run_codex_adapter(
            self.install_request(environment=environment)
        )

        self.assertEqual(result.action, CodexAction.INSTALL)
        self.assertEqual(result.stderr, "")
        self.assertLess(
            result.stdout.index("marketplace-added"),
            result.stdout.index("plugin-added"),
        )
        self.assertEqual(
            self.codex_calls(),
            [
                (
                    self.working_directory.resolve(),
                    (
                        "plugin",
                        "marketplace",
                        "add",
                        str(self.kit_home.resolve() / "codex-marketplace"),
                    ),
                ),
                (
                    self.working_directory.resolve(),
                    ("plugin", "add", "agent-rails@agent-rails-local"),
                ),
            ],
        )

        self.codex_log.unlink()
        with self.assertRaises(CodexAdapterError):
            run_codex_adapter(
                self.install_request(
                    environment=self.environment(
                        fake_codex=True,
                        fail_marketplace_add=True,
                    )
                )
            )
        self.assertEqual(
            [arguments for _, arguments in self.codex_calls()],
            [
                (
                    "plugin",
                    "marketplace",
                    "add",
                    str(self.kit_home.resolve() / "codex-marketplace"),
                )
            ],
        )

    def test_doctor_ignores_list_failures_and_suppresses_stderr(self) -> None:
        executable = self.install_fake_codex().resolve()

        result = run_codex_adapter(
            self.doctor_request(environment=self.environment(fake_codex=True))
        )

        self.assertEqual(result.action, CodexAction.DOCTOR)
        self.assertEqual(result.stderr, "")
        self.assertIn(f"[OK] Codex CLI: {executable}", result.stdout)
        self.assertIn("marketplace-visible", result.stdout)
        self.assertIn("plugin-visible", result.stdout)
        self.assertNotIn("private marketplace diagnostic", result.stdout)
        self.assertNotIn("private plugin diagnostic", result.stdout)
        self.assertTrue(
            all(event.stream is CodexEventStream.STDOUT for event in result.events)
        )
        self.assertEqual(
            [arguments for _, arguments in self.codex_calls()],
            [
                ("plugin", "marketplace", "list"),
                ("plugin", "list"),
            ],
        )

    def test_uninstall_runs_exact_remove_and_dry_run_needs_no_codex(self) -> None:
        self.install_fake_codex()

        result = run_codex_adapter(
            self.uninstall_request(
                environment=self.environment(fake_codex=True)
            )
        )

        self.assertEqual(result.action, CodexAction.UNINSTALL)
        self.assertEqual(result.stderr, "")
        self.assertIn("Agent Rails Codex Uninstall", result.stdout)
        self.assertIn("plugin-removed", result.stdout)
        self.assertEqual(
            [arguments for _, arguments in self.codex_calls()],
            [("plugin", "remove", "agent-rails@agent-rails-local")],
        )

        self.codex_log.unlink()
        dry_run = run_codex_adapter(
            self.uninstall_request(
                dry_run=True,
                environment=self.environment(fake_codex=False),
            )
        )
        self.assertIn(
            "Would run: codex plugin remove agent-rails@agent-rails-local",
            dry_run.stdout,
        )
        self.assertFalse(self.codex_log.exists())

    def test_codex_lookup_ignores_empty_and_relative_path_segments(self) -> None:
        self.install_fake_codex()
        local_codex = self.working_directory / "codex"
        relative_bin = self.working_directory / "relative-bin"
        relative_bin.mkdir()
        relative_codex = relative_bin / "codex"
        marker = self.working_directory / "project-codex-executed"
        malicious = """#!/bin/sh
printf 'executed\n' >> "$PROJECT_CODEX_MARKER"
exit 0
"""
        for executable in (local_codex, relative_codex):
            executable.write_text(malicious, encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

        original_directory = Path.cwd()
        try:
            os.chdir(self.working_directory)
            for label, path_value in (
                ("empty", f":{self.fake_bin}"),
                ("relative", f"relative-bin:{self.fake_bin}"),
            ):
                with self.subTest(path_segment=label):
                    marker.unlink(missing_ok=True)
                    self.codex_log.unlink(missing_ok=True)
                    environment = self.environment(fake_codex=True)
                    environment["PATH"] = path_value
                    environment["PROJECT_CODEX_MARKER"] = str(marker)

                    result = run_codex_adapter(
                        self.uninstall_request(environment=environment)
                    )

                    self.assertEqual(result.exit_code, 0)
                    self.assertFalse(
                        marker.exists(),
                        "Codex lookup executed a target-project PATH entry.",
                    )
                    self.assertEqual(
                        [arguments for _, arguments in self.codex_calls()],
                        [("plugin", "remove", "agent-rails@agent-rails-local")],
                    )
        finally:
            os.chdir(original_directory)

    def test_child_output_limit_fails_closed_and_kills_process_group(self) -> None:
        descendant_marker = self.working_directory / "descendant-survived"
        self.install_oversized_codex(descendant_marker)
        environment = self.environment(fake_codex=True)
        environment["CODEX_DESCENDANT_MARKER"] = str(descendant_marker)

        with self.assertRaises(CodexAdapterError) as raised:
            run_codex_adapter(self.install_request(environment=environment))

        self.assertEqual(raised.exception.exit_code, 1)
        self.assertIn("1000000 bytes", str(raised.exception))
        time.sleep(1)
        self.assertFalse(
            descendant_marker.exists(),
            "An oversized Codex child left a descendant process running.",
        )

    def test_failure_error_carries_sanitized_events_and_child_streams(self) -> None:
        self.install_fake_codex()

        with self.assertRaises(CodexAdapterError) as raised:
            run_codex_adapter(
                self.install_request(
                    environment=self.environment(
                        fake_codex=True,
                        fail_marketplace_add=True,
                    )
                )
            )

        error = raised.exception
        self.assertEqual(error.exit_code, 41)
        self.assertIn("Agent Rails Codex Install", error.stdout)
        self.assertIn("marketplace-add-partial", error.stdout)
        self.assertIn("marketplace add failed", error.stderr)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        self.assertNotIn("\x1b", error.stderr)
        self.assertNotIn("\x07", error.stderr)
        self.assertNotIn("\u202e", error.stderr)
        self.assertTrue(error.events)

    def test_cli_replays_sanitized_child_streams_on_failure(self) -> None:
        self.install_fake_codex()
        completed = self.run_cli(
            "install",
            environment=self.environment(
                fake_codex=True,
                fail_marketplace_add=True,
            ),
        )

        self.assertEqual(completed.returncode, 41)
        self.assertIn("Agent Rails Codex Install", completed.stdout)
        self.assertIn("marketplace-add-partial", completed.stdout)
        self.assertIn("marketplace add failed", completed.stderr)
        self.assertIn("\\x1b", completed.stderr)
        self.assertIn("\\x07", completed.stderr)
        self.assertIn("\\u202e", completed.stderr)
        self.assertNotIn("\x1b", completed.stderr)
        self.assertNotIn("\x07", completed.stderr)
        self.assertNotIn("\u202e", completed.stderr)

    def test_signal_exit_status_uses_shell_convention(self) -> None:
        self.install_fake_codex()

        with self.assertRaises(CodexAdapterError) as raised:
            run_codex_adapter(
                self.install_request(
                    environment=self.environment(
                        fake_codex=True,
                        signal_marketplace_add=True,
                    )
                )
            )

        self.assertEqual(raised.exception.exit_code, 128 + 15)

    def test_cli_escapes_control_characters_in_input_errors(self) -> None:
        missing = self.project.parent / (
            "missing\nproject-\x1b]0;codex-title\x07-\u202espoof"
        )

        completed = self.run_cli(
            "install",
            "--project",
            str(missing),
            "--dry-run",
        )

        self.assertEqual(completed.returncode, 2)
        rendered = completed.stderr.removesuffix("\n")
        self.assertIn("\\n", rendered)
        self.assertIn("\\x1b", rendered)
        self.assertIn("\\x07", rendered)
        self.assertIn("\\u202e", rendered)
        self.assertNotIn("\n", rendered)
        self.assertNotIn("\x1b", rendered)
        self.assertNotIn("\x07", rendered)
        self.assertNotIn("\u202e", rendered)

    def test_actual_fix_project_composes_doctor_and_writes_adapter(self) -> None:
        self.install_fake_codex()
        profile = ROOT / "profiles/default.profile"

        result = run_codex_adapter(
            replace(
                self.install_request(
                    requested_project=self.project,
                    explicit_profile=str(profile),
                    mode=CodexInstallMode.LOCAL,
                    fix_project=True,
                    environment=self.environment(fake_codex=True),
                ),
                kit_home=ROOT,
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Doctor fix completed", result.stdout)
        self.assertTrue((self.project / ".claude/AGENT_RAILS.md").is_file())
        self.assertTrue((self.project / "CLAUDE.local.md").is_file())
        self.assertEqual(
            [arguments for _, arguments in self.codex_calls()],
            [
                (
                    "plugin",
                    "marketplace",
                    "add",
                    str(ROOT / "codex-marketplace"),
                ),
                ("plugin", "add", "agent-rails@agent-rails-local"),
            ],
        )

    def test_actual_fix_project_propagates_doctor_failure(self) -> None:
        self.install_fake_codex()
        missing_profile = self.project.parent / "missing.profile"

        result = run_codex_adapter(
            replace(
                self.install_request(
                    requested_project=self.project,
                    explicit_profile=str(missing_profile),
                    fix_project=True,
                    environment=self.environment(fake_codex=True),
                ),
                kit_home=ROOT,
            )
        )

        self.assertEqual(result.exit_code, 1)
        self.assertIn(f"Profile not found: {missing_profile}", result.stdout)
        self.assertFalse((self.project / ".claude/AGENT_RAILS.md").exists())
        self.assertEqual(len(self.codex_calls()), 2)

    def test_project_marker_parent_symlink_is_ignored_and_output_is_safe(self) -> None:
        project = self.project.parent / (
            "project\nname-\x1b]0;codex-title-\u202espoof"
        )
        outside = self.project.parent / "outside-codex-plugin"
        project.mkdir()
        outside.mkdir()
        (outside / "plugin.json").write_text(
            '{"name":"outside-agent-rails"}\n', encoding="utf-8"
        )
        (project / ".codex-plugin").symlink_to(
            outside,
            target_is_directory=True,
        )

        result = run_codex_adapter(
            self.doctor_request(
                requested_project=project,
                environment={
                    "HOME": str(self.user_home),
                    "PATH": "",
                },
            )
        )

        visible = (
            str(project.resolve())
            .replace("\n", "\\n")
            .replace("\x1b", "\\x1b")
            .replace("\u202e", "\\u202e")
        )
        self.assertEqual(result.project_root, project.resolve())
        self.assertIn(f"Project: {visible}", result.stdout)
        self.assertIn("[WARN] Project has no Agent Rails marker yet", result.stdout)
        self.assertNotIn(str(project.resolve()), result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\u202e", result.stdout)

        (project / ".codex-plugin").unlink()
        marker = project / ".codex-plugin/plugin.json"
        marker.parent.mkdir()
        marker.write_text("{}\n", encoding="utf-8")
        regular = run_codex_adapter(
            self.doctor_request(
                requested_project=project,
                environment={
                    "HOME": str(self.user_home),
                    "PATH": "",
                },
            )
        )
        self.assertIn("[OK] Project has Agent Rails marker.", regular.stdout)


if __name__ == "__main__":
    unittest.main()
