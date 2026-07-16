#!/usr/bin/env python3

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import os
from pathlib import Path
import runpy
import sys
import tempfile
from typing import Mapping, Sequence
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails import public_cli  # noqa: E402
from agent_rails.public_cli import main  # noqa: E402


class _ExecveCalled(RuntimeError):
    """Stop a mocked process replacement without weakening its contract."""


class PublicCliTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="agent-rails-public-cli-")
        self.root = Path(self.temporary.name)
        self.kit_home = self.root / "kit home"
        self.working_directory = self.root / "workspace" / "caller"
        self.project = self.root / "workspace" / "target project"
        self.scripts = self.kit_home / "scripts"
        for path in (self.scripts, self.working_directory, self.project):
            path.mkdir(parents=True, exist_ok=True)
        (self.scripts / "agent-python-cli.py").write_text(
            "# trusted test helper\n", encoding="utf-8"
        )
        self.version_file = self.kit_home / "VERSION"
        self.version_file.write_text("\n2.4.6\nignored\n", encoding="utf-8")
        self.environment = {
            "AGENT_RAILS_HOME": str(self.kit_home),
            "AGENT_RAILS_VERSION": "0.1.0-stale",
            "HOME": str(self.root / "user home"),
            "PATH": "/usr/bin:/bin",
            "PUBLIC_CLI_SENTINEL": "preserve-me",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def invoke(
        self,
        arguments: Sequence[str],
        *,
        environment: Mapping[str, str] | None = None,
        working_directory: Path | None = None,
        exec_side_effect: BaseException | type[BaseException] = _ExecveCalled,
    ) -> tuple[
        int | None,
        str,
        str,
        Mock,
        Mock,
    ]:
        stdout = StringIO()
        stderr = StringIO()
        execve = Mock(side_effect=exec_side_effect)
        chdir = Mock()
        selected_environment = dict(
            self.environment if environment is None else environment
        )
        selected_working_directory = (
            self.working_directory
            if working_directory is None
            else working_directory
        )
        exit_code: int | None = None

        with (
            patch.dict(os.environ, selected_environment, clear=True),
            patch.object(
                public_cli.os,
                "getcwd",
                return_value=str(selected_working_directory),
            ),
            patch.object(public_cli.os, "chdir", chdir),
            patch.object(public_cli.os, "execve", execve),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            try:
                exit_code = main(list(arguments))
            except _ExecveCalled:
                pass

        return exit_code, stdout.getvalue(), stderr.getvalue(), execve, chdir

    def assert_exec(
        self,
        arguments: Sequence[str],
        internal_command: str,
        child_arguments: Sequence[str],
        *,
        isolation_flag: str = "-E",
        expected_project: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> tuple[str, str, dict[str, str]]:
        exit_code, stdout, stderr, execve, chdir = self.invoke(
            arguments,
            environment=environment,
        )
        self.assertIsNone(exit_code, "a successful dispatch must replace the process")
        execve.assert_called_once()
        executable, argv, child_environment = execve.call_args.args
        self.assertEqual(executable, sys.executable)
        self.assertEqual(
            argv,
            [
                sys.executable,
                isolation_flag,
                str(self.scripts / "agent-python-cli.py"),
                internal_command,
                *child_arguments,
            ],
        )
        self.assertIsInstance(child_environment, dict)
        if expected_project is None:
            chdir.assert_not_called()
        else:
            chdir.assert_called_once_with(str(expected_project))
        return stdout, stderr, child_environment

    def test_usage_help_version_and_home_are_handled_without_child_processes(self) -> None:
        for arguments in ((), ("--help",), ("-h",)):
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr, execve, chdir = self.invoke(arguments)
                self.assertEqual(exit_code, 0)
                self.assertTrue(stdout.startswith("Usage:\n"), stdout)
                self.assertIn("agent-rails setup", stdout)
                self.assertIn("agent-rails run", stdout)
                self.assertIn("agent-rails verify", stdout)
                self.assertIn("agent-rails home", stdout)
                self.assertEqual(stderr, "")
                execve.assert_not_called()
                chdir.assert_not_called()

        for arguments in (("--version",), ("version",)):
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr, execve, chdir = self.invoke(arguments)
                self.assertEqual(exit_code, 0)
                self.assertEqual(stdout, "agent-rails 2.4.6\n")
                self.assertEqual(stderr, "")
                execve.assert_not_called()
                chdir.assert_not_called()

        exit_code, stdout, stderr, execve, chdir = self.invoke(("home",))
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, f"{self.kit_home}\n")
        self.assertEqual(stderr, "")
        execve.assert_not_called()
        chdir.assert_not_called()

    def test_version_resolution_uses_override_then_current_home_then_dev_fallback(self) -> None:
        override_environment = dict(self.environment)
        override_environment["AGENT_RAILS_VERSION_OVERRIDE"] = "7.8.9-override"
        exit_code, stdout, stderr, execve, _ = self.invoke(
            ("version",),
            environment=override_environment,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "agent-rails 7.8.9-override\n")
        self.assertEqual(stderr, "")
        execve.assert_not_called()

        self.version_file.unlink()
        fallback_environment = dict(self.environment)
        fallback_environment.pop("AGENT_RAILS_VERSION_OVERRIDE", None)
        exit_code, stdout, stderr, execve, _ = self.invoke(
            ("version",),
            environment=fallback_environment,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "agent-rails 0.0.0-dev\n")
        self.assertEqual(stderr, "")
        execve.assert_not_called()

        self.version_file.write_text("\n \t\n", encoding="utf-8")
        exit_code, stdout, stderr, execve, _ = self.invoke(
            ("version",),
            environment=fallback_environment,
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "agent-rails 0.0.0-dev\n")
        self.assertEqual(stderr, "")
        execve.assert_not_called()

    def test_missing_bootstrap_home_and_unreadable_version_fail_without_dispatch(self) -> None:
        missing_home = dict(self.environment)
        missing_home.pop("AGENT_RAILS_HOME")
        exit_code, stdout, stderr, execve, chdir = self.invoke(
            ("home",),
            environment=missing_home,
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("AGENT_RAILS_HOME", stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

        self.version_file.write_bytes(b"\xff\xfe\x00")
        exit_code, stdout, stderr, execve, chdir = self.invoke(("version",))
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("VERSION", stderr)
        self.assertNotIn("\\xff", stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

    def test_version_and_home_terminal_data_cannot_forge_output(self) -> None:
        invalid_override = dict(self.environment)
        invalid_override["AGENT_RAILS_VERSION_OVERRIDE"] = "9.9\x1b]0;forged\x07"
        exit_code, stdout, stderr, execve, chdir = self.invoke(
            ("version",), environment=invalid_override
        )
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Agent Rails version is invalid.\n")
        self.assertNotIn("\x1b", stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

        self.version_file.write_text("2.4\u202e6\n", encoding="utf-8")
        exit_code, stdout, stderr, execve, chdir = self.invoke(("version",))
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "Agent Rails version is invalid.\n")
        execve.assert_not_called()
        chdir.assert_not_called()

        controlled_home = dict(self.environment)
        controlled_home["AGENT_RAILS_HOME"] = "/tmp/kit\nforged\x1b"
        exit_code, stdout, stderr, execve, chdir = self.invoke(
            ("home",), environment=controlled_home
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(stdout, "/tmp/kit\\nforged\\x1b\n")
        self.assertEqual(stderr, "")
        execve.assert_not_called()
        chdir.assert_not_called()

    def test_entrypoint_exec_failures_preserve_shell_exit_semantics(self) -> None:
        for failure, expected, message in (
            (FileNotFoundError(), 127, "was not found"),
            (PermissionError(), 126, "not executable"),
        ):
            with self.subTest(expected=expected):
                exit_code, stdout, stderr, execve, chdir = self.invoke(
                    ("setup",), exec_side_effect=failure
                )
                self.assertEqual(exit_code, expected)
                self.assertEqual(stdout, "")
                self.assertIn(message, stderr)
                execve.assert_called_once()
                chdir.assert_not_called()

    def test_missing_or_non_regular_python_helper_fails_before_exec(self) -> None:
        helper = self.scripts / "agent-python-cli.py"
        helper.unlink()
        exit_code, stdout, stderr, execve, chdir = self.invoke(("setup",))
        self.assertEqual(exit_code, 127)
        self.assertEqual(stdout, "")
        self.assertIn("helper was not found", stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

        helper.mkdir()
        exit_code, stdout, stderr, execve, chdir = self.invoke(("setup",))
        self.assertEqual(exit_code, 126)
        self.assertEqual(stdout, "")
        self.assertIn("helper", stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

    def test_public_bootstrap_builtins_do_not_import_application_cli(self) -> None:
        stdout = StringIO()
        stderr = StringIO()
        helper = ROOT / "scripts" / "agent-python-cli.py"
        environment = {
            **self.environment,
            "AGENT_RAILS_HOME": str(self.kit_home),
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch.dict(sys.modules, {"agent_rails.cli": None}),
            patch.object(sys, "argv", [str(helper), "public", "--version"]),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
            self.assertRaises(SystemExit) as raised,
        ):
            runpy.run_path(str(helper), run_name="__main__")

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(stdout.getvalue(), "agent-rails 2.4.6\n")
        self.assertEqual(stderr.getvalue(), "")

    def test_direct_commands_route_to_exact_python_argv(self) -> None:
        cases = (
            (
                ("setup", "--tool", "claude", "--dry-run"),
                "setup-application",
                ("--tool", "claude", "--dry-run"),
                "-E",
            ),
            (
                ("run", "--model", "glm5.1", "review; do not split"),
                "run-application",
                ("--model", "glm5.1", "review; do not split"),
                "-E",
            ),
            (
                ("verify", "--publish", "--base", "deployed"),
                "verify-application",
                ("--publish", "--base", "deployed"),
                "-E",
            ),
            (
                ("update", "--tool", "codex", "--dry-run"),
                "update-application",
                ("--tool", "codex", "--dry-run"),
                "-E",
            ),
            (
                ("upgrade", "self", "--version", "3.0.0"),
                "update-application",
                ("--self-only", "--version", "3.0.0"),
                "-E",
            ),
            (
                ("init", "--shell", "fish"),
                "init-application",
                ("--shell", "fish"),
                "-I",
            ),
            (
                ("estimate", "--model", "generic", "literal $(payload)"),
                "estimate",
                ("--model", "generic", "literal $(payload)"),
                "-E",
            ),
            (
                ("doctor", "--online-memory-smoke"),
                "doctor-application",
                ("--online-memory-smoke",),
                "-E",
            ),
            (
                ("codex", "install", "--mode", "local"),
                "codex-adapter",
                ("install", "--mode", "local"),
                "-E",
            ),
            (
                ("opencode", "doctor", "--project", "opaque-project"),
                "opencode-adapter",
                ("doctor", "--project", "opaque-project"),
                "-E",
            ),
        )

        for arguments, internal_command, child_arguments, isolation_flag in cases:
            with self.subTest(arguments=arguments):
                stdout, stderr, child_environment = self.assert_exec(
                    arguments,
                    internal_command,
                    child_arguments,
                    isolation_flag=isolation_flag,
                )
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
                self.assertEqual(
                    child_environment,
                    {
                        **self.environment,
                        "AGENT_RAILS_VERSION": "2.4.6",
                        "PYTHONDONTWRITEBYTECODE": "1",
                    },
                )

    def test_nested_commands_validate_and_route_exactly(self) -> None:
        valid_cases = (
            (
                ("profile", "init", "--scope", "project"),
                "profile-init",
                ("--agent-rails-home", str(self.kit_home), "--scope", "project"),
                "-E",
            ),
            (
                ("claude", "install", "--session-hook"),
                "claude-adapter",
                ("install", "--session-hook"),
                "-E",
            ),
            (
                ("claude", "uninstall", "--dry-run"),
                "claude-adapter",
                ("uninstall", "--dry-run"),
                "-E",
            ),
            (
                ("memory", "suggest", "--decision", "skip"),
                "memory-suggest",
                ("--decision", "skip"),
                "-E",
            ),
            (
                ("skills", "install", "--dest", "/tmp/skills"),
                "skills-install",
                ("--dest", "/tmp/skills"),
                "-I",
            ),
        )

        for arguments, internal_command, child_arguments, isolation_flag in valid_cases:
            with self.subTest(arguments=arguments):
                stdout, stderr, _ = self.assert_exec(
                    arguments,
                    internal_command,
                    child_arguments,
                    isolation_flag=isolation_flag,
                )
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")

        stdout, stderr, _ = self.assert_exec(
            ("claude", "upgrade", "--session-hook"),
            "claude-adapter",
            ("install", "--force", "--session-hook"),
        )
        self.assertEqual(stdout, "")
        self.assertIn("Deprecated", stderr)
        self.assertIn("agent-rails doctor --fix", stderr)

        invalid_cases = (
            ("upgrade", "else"),
            ("publish", "else"),
            ("profile",),
            ("profile", "doctor"),
            ("claude",),
            ("claude", "doctor"),
            ("memory",),
            ("memory", "search"),
            ("skills",),
            ("skills", "list"),
        )
        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr, execve, chdir = self.invoke(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.startswith("Usage:\n"), stderr)
                execve.assert_not_called()
                chdir.assert_not_called()

    def test_nested_help_routes_to_the_existing_command_help(self) -> None:
        for arguments, internal_command in (
            (("upgrade",), "update-application"),
            (("upgrade", "--help"), "update-application"),
            (("upgrade", "-h"), "update-application"),
            (("publish",), "publish-check"),
            (("publish", "--help"), "publish-check"),
            (("publish", "-h"), "publish-check"),
        ):
            with self.subTest(arguments=arguments):
                stdout, stderr, _ = self.assert_exec(
                    arguments,
                    internal_command,
                    ("--help",),
                )
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")

    def test_project_commands_remove_project_and_anchor_relative_path_to_entry_cwd(self) -> None:
        relative_project = Path("..") / "target project"
        expected_project = self.project.resolve()
        cases = (
            (
                (
                    "pack",
                    "--model",
                    "generic",
                    "--project",
                    str(relative_project),
                    "goal with spaces",
                ),
                "task-pack",
                ("--model", "generic", "goal with spaces"),
            ),
            (
                (
                    "check",
                    "--project",
                    str(relative_project),
                    "--print-only",
                ),
                "agent-check",
                ("--print-only",),
            ),
            (
                (
                    "publish",
                    "check",
                    "--base",
                    "deployed",
                    "--project",
                    str(relative_project),
                    "--no-secret-scan",
                ),
                "publish-check",
                ("--base", "deployed", "--no-secret-scan"),
            ),
        )

        for arguments, internal_command, child_arguments in cases:
            with self.subTest(arguments=arguments):
                stdout, stderr, _ = self.assert_exec(
                    arguments,
                    internal_command,
                    child_arguments,
                    expected_project=expected_project,
                )
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")

    def test_project_commands_default_to_the_entry_cwd(self) -> None:
        for arguments, internal_command, child_arguments in (
            (("pack", "goal"), "task-pack", ("goal",)),
            (("check",), "agent-check", ()),
            (
                ("publish", "check", "--base", "main"),
                "publish-check",
                ("--base", "main"),
            ),
        ):
            with self.subTest(arguments=arguments):
                self.assert_exec(
                    arguments,
                    internal_command,
                    child_arguments,
                    expected_project=self.working_directory.resolve(),
                )

    def test_project_commands_reject_duplicate_or_missing_project_values(self) -> None:
        invalid_cases = (
            ("pack", "--project"),
            ("check", "--project"),
            ("publish", "check", "--project"),
            (
                "pack",
                "--project",
                str(self.project),
                "--project",
                str(self.project),
            ),
            (
                "check",
                "--project",
                str(self.project),
                "--project",
                str(self.working_directory),
            ),
            (
                "publish",
                "check",
                "--project",
                str(self.project),
                "--project",
                str(self.working_directory),
            ),
        )

        for arguments in invalid_cases:
            with self.subTest(arguments=arguments):
                exit_code, stdout, stderr, execve, chdir = self.invoke(arguments)
                self.assertEqual(exit_code, 2)
                self.assertEqual(stdout, "")
                self.assertTrue(stderr.startswith("Usage:\n"), stderr)
                execve.assert_not_called()
                chdir.assert_not_called()

    def test_exec_environment_uses_current_release_version_not_stale_parent_value(self) -> None:
        environment = dict(self.environment)
        environment.update(
            {
                "AGENT_RAILS_VERSION": "old-release",
                "UNRELATED_VALUE": "unchanged",
            }
        )
        _, _, child_environment = self.assert_exec(
            ("setup", "--dry-run"),
            "setup-application",
            ("--dry-run",),
            environment=environment,
        )
        self.assertEqual(child_environment["AGENT_RAILS_VERSION"], "2.4.6")
        self.assertEqual(child_environment["AGENT_RAILS_HOME"], str(self.kit_home))
        self.assertEqual(child_environment["UNRELATED_VALUE"], "unchanged")
        self.assertEqual(child_environment["PYTHONDONTWRITEBYTECODE"], "1")
        self.assertEqual(
            child_environment,
            {
                **environment,
                "AGENT_RAILS_VERSION": "2.4.6",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        override_environment = dict(environment)
        override_environment["AGENT_RAILS_VERSION_OVERRIDE"] = "9.9.9-test"
        _, _, child_environment = self.assert_exec(
            ("doctor",),
            "doctor-application",
            (),
            environment=override_environment,
        )
        self.assertEqual(child_environment["AGENT_RAILS_VERSION"], "9.9.9-test")

    def test_unknown_command_reports_usage_without_dispatch(self) -> None:
        exit_code, stdout, stderr, execve, chdir = self.invoke(("unknown", "arg"))
        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr.startswith("Usage:\n"), stderr)
        execve.assert_not_called()
        chdir.assert_not_called()

    def test_dispatcher_does_not_import_a_shell_or_shell_parser(self) -> None:
        source_path = ROOT / "src" / "agent_rails" / "public_cli.py"
        syntax = ast.parse(source_path.read_text(encoding="utf-8"))
        imported_roots: set[str] = set()
        forbidden_calls: list[str] = []
        for node in ast.walk(syntax):
            if isinstance(node, ast.Import):
                imported_roots.update(
                    alias.name.partition(".")[0] for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "os"
                and node.func.attr in {"popen", "system"}
            ):
                forbidden_calls.append(f"os.{node.func.attr}")

        self.assertTrue(
            {"subprocess", "shlex"}.isdisjoint(imported_roots),
            f"public dispatcher imported a shell execution/parser module: {imported_roots}",
        )
        self.assertEqual(forbidden_calls, [])


if __name__ == "__main__":
    unittest.main()
