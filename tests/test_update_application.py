#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple, Union
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.update_application import (  # noqa: E402
    UpdateAction,
    UpdateApplicationError,
    UpdateCommand,
    UpdateCommandResult,
    UpdateDependencies,
    UpdateEventStream,
    UpdateInputError,
    UpdateInstallMode,
    UpdateMode,
    UpdateReexecRequest,
    UpdateRequest,
    UpdateSource,
    UpdateTool,
    run_update,
)


def _git(project: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


CommandResponse = Union[
    UpdateCommandResult,
    Callable[[UpdateCommand], UpdateCommandResult],
]


class FakeRunner:
    """Record logical external commands without performing an update."""

    def __init__(self) -> None:
        self.calls: List[UpdateCommand] = []
        self.responses: Dict[UpdateAction, List[CommandResponse]] = {}

    def queue(
        self,
        action: UpdateAction,
        *responses: CommandResponse,
    ) -> None:
        self.responses.setdefault(action, []).extend(responses)

    def __call__(self, command: UpdateCommand) -> UpdateCommandResult:
        self.calls.append(command)
        queued = self.responses.get(command.action, [])
        if queued:
            response = queued.pop(0)
            return response(command) if callable(response) else response
        if command.action is UpdateAction.GIT_PROBE:
            return UpdateCommandResult(exit_code=0, stdout=f"{command.argv[2]}\n")
        if command.action is UpdateAction.GIT_UPSTREAM:
            return UpdateCommandResult(exit_code=0, stdout="origin/main\n")
        if command.action is UpdateAction.GIT_BRANCH:
            return UpdateCommandResult(exit_code=0, stdout="feature/update\n")
        return UpdateCommandResult(exit_code=0)

    def actions(self) -> Tuple[UpdateAction, ...]:
        return tuple(call.action for call in self.calls)

    def commands(self, action: UpdateAction) -> Tuple[UpdateCommand, ...]:
        return tuple(call for call in self.calls if call.action is action)


class FakeReexec:
    def __init__(self) -> None:
        self.requests: List[UpdateReexecRequest] = []

    def __call__(self, request: UpdateReexecRequest) -> None:
        self.requests.append(request)


class UpdateApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-update-application-"
        )
        self.root = Path(os.path.realpath(self.temporary.name))
        self.working_directory = self.root / "working"
        self.kit_home = self.root / "kit home"
        self.project = self.working_directory / "project"
        self.nested_project = self.project / "nested" / "path"
        self.user_home = self.root / "user home"
        self.install_root = self.root / "install root"
        self.bin_dir = self.root / "bin dir"
        self.profile = self.root / "update profile.sh"
        self.profile_marker = self.root / "profile-executed"

        for path in (
            self.kit_home / "bin",
            self.kit_home / "profiles",
            self.kit_home / "scripts",
            self.kit_home / "src/agent_rails/release",
            self.kit_home / "tests",
            self.nested_project,
            self.user_home,
            self.install_root,
            self.bin_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.cli = self.kit_home / "bin/agent-rails"
        self.cli.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IXUSR)
        self.installer = self.kit_home / "scripts/agent-release-install.sh"
        self.installer.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        self.installer.chmod(self.installer.stat().st_mode | stat.S_IXUSR)
        self.python_helper = self.kit_home / "scripts/agent-python-cli.py"
        self.python_helper.write_text(
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        self.python_installer = (
            self.kit_home / "src/agent_rails/release/install.py"
        )
        self.python_installer.write_text(
            "raise SystemExit(99)\n",
            encoding="utf-8",
        )
        source_tests = self.kit_home / "tests/run.sh"
        source_tests.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        source_tests.chmod(source_tests.stat().st_mode | stat.S_IXUSR)
        (self.kit_home / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        (self.kit_home / "profiles/default.profile").write_text(
            "PROJECT_NAME=default\n",
            encoding="utf-8",
        )

        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.name", "Agent Rails Tests")
        _git(
            self.project,
            "config",
            "user.email",
            "agent-rails@example.invalid",
        )
        (self.project / "README.md").write_text(
            "# update fixture\n", encoding="utf-8"
        )
        _git(self.project, "add", "README.md")
        _git(self.project, "commit", "-qm", "fixture")

        self.profile.write_text(
            "\n".join(
                (
                    'printf "executed\\n" > "$UPDATE_PROFILE_MARKER"',
                    "exit 97",
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "AGENT_RAILS_HOME": str(self.kit_home),
            "UPDATE_PROFILE_MARKER": str(self.profile_marker),
        }
        self.runner = FakeRunner()
        self.reexec = FakeReexec()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        mode: UpdateMode = UpdateMode.PROJECT,
        requested_project: Optional[Path] = None,
        explicit_profile: Optional[str] = None,
        tool: Optional[UpdateTool] = UpdateTool.CLAUDE,
        install_mode: UpdateInstallMode = UpdateInstallMode.LOCAL,
        session_hook: bool = False,
        global_reminder: bool = False,
        requested_version: str = "latest",
        skip_pull: bool = True,
        skip_tests: bool = True,
        skip_doctor: bool = False,
        skip_adapter: bool = False,
        dry_run: bool = False,
        original_arguments: Optional[Tuple[str, ...]] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> UpdateRequest:
        project = (
            None
            if mode is UpdateMode.SELF
            else self.nested_project
            if requested_project is None
            else requested_project
        )
        profile = (
            None
            if mode is UpdateMode.SELF
            else str(self.profile)
            if explicit_profile is None
            else explicit_profile
        )
        selected_tool = None if mode is UpdateMode.SELF else tool
        if original_arguments is None:
            original_arguments = (
                ("--self-only",)
                if mode is UpdateMode.SELF
                else ("--tool", selected_tool.value)  # type: ignore[union-attr]
            )
        return UpdateRequest(
            mode=mode,
            requested_project=project,
            kit_home=self.kit_home,
            explicit_profile=profile,
            tool=selected_tool,
            install_mode=install_mode,
            session_hook=session_hook,
            global_reminder=global_reminder,
            requested_version=requested_version,
            repository="owner/agent-rails",
            install_root=self.install_root,
            bin_dir=self.bin_dir,
            skip_pull=skip_pull,
            skip_tests=skip_tests,
            skip_doctor=skip_doctor,
            skip_adapter=skip_adapter,
            dry_run=dry_run,
            original_arguments=original_arguments,
            working_directory=self.working_directory,
            environment=(
                dict(self.environment) if environment is None else environment
            ),
        )

    def dependencies(self) -> UpdateDependencies:
        return UpdateDependencies(
            runner=self.runner,
            reexec=self.reexec,
        )

    def test_request_types_and_self_project_combinations_are_strict(self) -> None:
        invalid_requests = (
            replace(self.request(), mode="project"),
            replace(self.request(), tool="claude"),
            replace(self.request(), install_mode="local"),
            replace(self.request(), skip_pull=1),
            replace(self.request(), original_arguments=["--tool", "claude"]),
            replace(self.request(), working_directory="."),
            replace(self.request(), environment={"PATH": 3}),
            replace(self.request(), tool=None),
            replace(
                self.request(mode=UpdateMode.SELF),
                tool=UpdateTool.CLAUDE,
            ),
            replace(
                self.request(tool=UpdateTool.OPENCODE),
                session_hook=True,
            ),
            replace(
                self.request(tool=UpdateTool.CODEX),
                global_reminder=True,
            ),
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(UpdateInputError) as raised:
                    run_update(request, dependencies=self.dependencies())
                self.assertEqual(raised.exception.exit_code, 2)

    def test_self_mode_never_resolves_target_project_or_profile(self) -> None:
        missing_project = self.root / "missing project"
        missing_profile = self.root / "missing profile.sh"
        request = replace(
            self.request(mode=UpdateMode.SELF),
            requested_project=missing_project,
            explicit_profile=str(missing_profile),
        )

        result = run_update(request, dependencies=self.dependencies())

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.mode, UpdateMode.SELF)
        self.assertIsNone(result.project_root)
        self.assertIsNone(result.profile_path)
        self.assertIn("Mode: self", result.stdout)
        self.assertNotIn("Project:", result.stdout)
        self.assertNotIn("Profile:", result.stdout)
        self.assertNotIn(UpdateAction.PRE_DOCTOR, self.runner.actions())
        self.assertNotIn(UpdateAction.ADAPTER_INSTALL, self.runner.actions())
        self.assertFalse(self.profile_marker.exists())

    def test_all_project_refresh_gates_skip_target_resolution(self) -> None:
        missing = self.root / "missing target"

        result = run_update(
            self.request(
                requested_project=missing,
                explicit_profile=str(self.root / "missing.profile"),
                skip_doctor=True,
                skip_adapter=True,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.project_root)
        self.assertIsNone(result.profile_path)
        self.assertIn("Mode: project", result.stdout)
        self.assertNotIn("Project:", result.stdout)
        self.assertEqual(self.runner.actions(), (UpdateAction.GIT_PROBE,))

    def test_project_profile_is_not_executed_and_tool_argv_are_exact(self) -> None:
        cases = (
            (
                UpdateTool.CLAUDE,
                UpdateInstallMode.PROJECT,
                True,
                True,
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "doctor",
                    "--project",
                    str(self.project.resolve()),
                    "--profile",
                    str(self.profile),
                ),
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "claude",
                    "install",
                    "--project",
                    str(self.project.resolve()),
                    "--profile",
                    str(self.profile),
                    "--mode",
                    "project",
                    "--session-hook",
                    "--global-reminder",
                ),
            ),
            (
                UpdateTool.CODEX,
                UpdateInstallMode.LOCAL,
                False,
                False,
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "codex",
                    "doctor",
                    "--project",
                    str(self.project.resolve()),
                ),
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "codex",
                    "install",
                    "--project",
                    str(self.project.resolve()),
                    "--profile",
                    str(self.profile),
                    "--fix-project",
                    "--mode",
                    "local",
                ),
            ),
            (
                UpdateTool.OPENCODE,
                UpdateInstallMode.PROJECT,
                False,
                False,
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "opencode",
                    "doctor",
                    "--project",
                    str(self.project.resolve()),
                ),
                (
                    sys.executable,
                    "-E",
                    str(self.python_helper),
                    "public",
                    "opencode",
                    "install",
                    "--project",
                    str(self.project.resolve()),
                    "--profile",
                    str(self.profile),
                    "--mode",
                    "project",
                ),
            ),
        )

        for tool, mode, session_hook, reminder, doctor_argv, install_argv in cases:
            with self.subTest(tool=tool.value):
                self.runner = FakeRunner()
                stale_environment = dict(self.environment)
                stale_environment["AGENT_RAILS_HOME"] = str(
                    self.root / "stale-kit-home"
                )
                result = run_update(
                    self.request(
                        tool=tool,
                        install_mode=mode,
                        session_hook=session_hook,
                        global_reminder=reminder,
                        environment=stale_environment,
                    ),
                    dependencies=self.dependencies(),
                )

                self.assertEqual(result.exit_code, 0)
                self.assertEqual(result.project_root, self.project.resolve())
                self.assertEqual(result.profile_path, str(self.profile))
                self.assertFalse(self.profile_marker.exists())
                project_calls = tuple(
                    call
                    for call in self.runner.calls
                    if call.action
                    in {
                        UpdateAction.PRE_DOCTOR,
                        UpdateAction.ADAPTER_INSTALL,
                        UpdateAction.FINAL_DOCTOR,
                    }
                )
                self.assertEqual(
                    tuple(call.argv for call in project_calls),
                    (doctor_argv, install_argv, doctor_argv),
                )
                for call in project_calls:
                    self.assertEqual(
                        call.environment["AGENT_RAILS_HOME"],
                        str(self.kit_home.resolve()),
                    )
                    self.assertNotIn("bin/agent-rails", " ".join(call.argv))
                    self.assertNotIn(
                        "agent-release-install.sh",
                        " ".join(call.argv),
                    )

    def test_missing_legacy_kit_profile_falls_back_but_other_missing_fails(self) -> None:
        legacy_profile = self.kit_home / "profiles/missing-legacy.profile"

        result = run_update(
            self.request(explicit_profile=str(legacy_profile)),
            dependencies=self.dependencies(),
        )

        default_profile = self.kit_home / "profiles/default.profile"
        self.assertEqual(result.profile_path, str(default_profile))
        self.assertNotIn(str(legacy_profile), result.stdout)
        self.assertIn(str(default_profile), result.stdout)
        self.runner = FakeRunner()
        arbitrary_missing = self.root / "missing.profile"
        with self.assertRaises(UpdateInputError) as raised:
            run_update(
                self.request(explicit_profile=str(arbitrary_missing)),
                dependencies=self.dependencies(),
            )
        self.assertEqual(raised.exception.exit_code, 2)
        self.assertIn("Profile not found", str(raised.exception))
        self.assertNotIn(UpdateAction.PRE_DOCTOR, self.runner.actions())

    def test_git_checkout_with_upstream_pulls_and_runs_source_tests(self) -> None:
        result = run_update(
            self.request(
                mode=UpdateMode.SELF,
                skip_pull=False,
                skip_tests=False,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source, UpdateSource.GIT)
        self.assertEqual(
            self.runner.actions(),
            (
                UpdateAction.GIT_PROBE,
                UpdateAction.GIT_STATUS,
                UpdateAction.GIT_UPSTREAM,
                UpdateAction.GIT_PULL,
                UpdateAction.SOURCE_TESTS,
            ),
        )
        self.assertEqual(
            self.runner.commands(UpdateAction.GIT_PULL)[0].argv,
            (
                "git",
                "-C",
                str(self.kit_home),
                "pull",
                "--ff-only",
            ),
        )
        self.assertEqual(
            self.runner.commands(UpdateAction.GIT_PROBE)[0].argv,
            (
                "git",
                "-C",
                str(self.kit_home),
                "rev-parse",
                "--show-toplevel",
            ),
        )
        self.assertEqual(
            self.runner.commands(UpdateAction.GIT_STATUS)[0].argv,
            ("git", "-C", str(self.kit_home), "status", "--porcelain"),
        )
        self.assertEqual(
            self.runner.commands(UpdateAction.GIT_UPSTREAM)[0].argv,
            (
                "git",
                "-C",
                str(self.kit_home),
                "rev-parse",
                "--abbrev-ref",
                "--symbolic-full-name",
                "@{upstream}",
            ),
        )
        self.assertEqual(
            self.runner.commands(UpdateAction.SOURCE_TESTS)[0].argv,
            ("bash", str(self.kit_home / "tests/run.sh")),
        )
        self.assertNotIn("origin/main", result.stdout)

    def test_git_checkout_without_upstream_uses_branch_then_main_fallback(self) -> None:
        for branch_output, expected_branch in (
            ("feature/refactor\n", "feature/refactor"),
            ("\n", "main"),
        ):
            with self.subTest(branch=expected_branch):
                self.runner = FakeRunner()
                self.runner.queue(
                    UpdateAction.GIT_UPSTREAM,
                    UpdateCommandResult(exit_code=1),
                )
                self.runner.queue(
                    UpdateAction.GIT_BRANCH,
                    UpdateCommandResult(exit_code=0, stdout=branch_output),
                )

                result = run_update(
                    self.request(
                        mode=UpdateMode.SELF,
                        skip_pull=False,
                    ),
                    dependencies=self.dependencies(),
                )

                self.assertEqual(result.exit_code, 0)
                self.assertEqual(
                    self.runner.commands(UpdateAction.GIT_PULL)[0].argv,
                    (
                        "git",
                        "-C",
                        str(self.kit_home),
                        "pull",
                        "--ff-only",
                        "origin",
                        expected_branch,
                    ),
                )

    def test_nested_release_home_is_not_misclassified_as_parent_git_checkout(self) -> None:
        parent = self.root / "parent-repository"
        release_home = parent / "ignored-release-home"
        release_home.mkdir(parents=True)
        _git(parent, "init", "-q")
        _git(parent, "config", "user.name", "Agent Rails Tests")
        _git(parent, "config", "user.email", "agent-rails@example.invalid")
        (parent / ".gitignore").write_text("ignored-release-home/\n", encoding="utf-8")
        _git(parent, "add", ".gitignore")
        _git(parent, "commit", "-qm", "ignore release")

        result = run_update(
            replace(
                self.request(mode=UpdateMode.SELF),
                kit_home=release_home,
            ),
            dependencies=UpdateDependencies(reexec=self.reexec),
        )

        self.assertEqual(result.source, UpdateSource.RELEASE)
        self.assertIn("Skip release download", result.stdout)
        self.assertNotIn("Skip git pull", result.stdout)

    def test_git_probe_execution_failure_does_not_fall_back_to_release(self) -> None:
        for exit_code in (126, 127):
            with self.subTest(exit_code=exit_code):
                self.runner = FakeRunner()
                self.runner.queue(
                    UpdateAction.GIT_PROBE,
                    UpdateCommandResult(
                        exit_code=exit_code,
                        stderr="git unavailable\n",
                    ),
                )
                with self.assertRaises(UpdateApplicationError) as raised:
                    run_update(
                        self.request(mode=UpdateMode.SELF),
                        dependencies=self.dependencies(),
                    )
                self.assertEqual(raised.exception.exit_code, exit_code)
                self.assertIn("Git is required", str(raised.exception))
                self.assertIn("git unavailable", raised.exception.stderr)
                self.assertNotIn(UpdateAction.RELEASE_INSTALL, self.runner.actions())

    def test_dirty_git_checkout_stops_before_pull(self) -> None:
        self.runner.queue(
            UpdateAction.GIT_STATUS,
            UpdateCommandResult(exit_code=0, stdout=" M scripts/local.sh\n"),
        )

        with self.assertRaises(UpdateApplicationError) as raised:
            run_update(
                self.request(
                    mode=UpdateMode.SELF,
                    skip_pull=False,
                ),
                dependencies=self.dependencies(),
            )

        error = raised.exception
        self.assertEqual(error.exit_code, 1)
        self.assertIn("local changes", str(error))
        self.assertNotIn(UpdateAction.GIT_PULL, self.runner.actions())
        self.assertNotIn(UpdateAction.SOURCE_TESTS, self.runner.actions())

    def test_git_status_and_branch_failures_preserve_diagnostics_and_stop(self) -> None:
        self.runner.queue(
            UpdateAction.GIT_STATUS,
            UpdateCommandResult(exit_code=41, stderr="status failed\n"),
        )
        status_result = run_update(
            self.request(mode=UpdateMode.SELF, skip_pull=False),
            dependencies=self.dependencies(),
        )
        self.assertEqual(status_result.exit_code, 41)
        self.assertEqual(status_result.failed_action, UpdateAction.GIT_STATUS)
        self.assertIn("status failed", status_result.stderr)
        self.assertNotIn(UpdateAction.GIT_PULL, self.runner.actions())

        self.runner = FakeRunner()
        self.runner.queue(
            UpdateAction.GIT_UPSTREAM,
            UpdateCommandResult(exit_code=1),
        )
        self.runner.queue(
            UpdateAction.GIT_BRANCH,
            UpdateCommandResult(exit_code=42, stderr="branch failed\n"),
        )
        with self.assertRaises(UpdateApplicationError) as raised:
            run_update(
                self.request(mode=UpdateMode.SELF, skip_pull=False),
                dependencies=self.dependencies(),
            )
        self.assertEqual(raised.exception.exit_code, 42)
        self.assertEqual(raised.exception.failed_action, UpdateAction.GIT_BRANCH)
        self.assertIn("branch failed", raised.exception.stderr)
        self.assertNotIn(UpdateAction.GIT_PULL, self.runner.actions())

    def test_git_version_selection_is_rejected_before_update(self) -> None:
        with self.assertRaises(UpdateInputError) as raised:
            run_update(
                self.request(
                    mode=UpdateMode.SELF,
                    requested_version="2.0.0",
                    skip_pull=False,
                ),
                dependencies=self.dependencies(),
            )

        self.assertEqual(raised.exception.exit_code, 2)
        self.assertIn("GitHub Release", str(raised.exception))
        self.assertNotIn(UpdateAction.GIT_PULL, self.runner.actions())

    def test_dry_run_plans_git_and_project_steps_without_executing_them(self) -> None:
        result = run_update(
            self.request(
                skip_pull=False,
                skip_tests=False,
                dry_run=True,
                session_hook=True,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source, UpdateSource.GIT)
        self.assertEqual(
            self.runner.actions(),
            (UpdateAction.GIT_PROBE, UpdateAction.GIT_UPSTREAM),
        )
        self.assertNotIn(UpdateAction.GIT_STATUS, self.runner.actions())
        self.assertNotIn(UpdateAction.GIT_PULL, self.runner.actions())
        self.assertNotIn(UpdateAction.SOURCE_TESTS, self.runner.actions())
        self.assertNotIn(UpdateAction.PRE_DOCTOR, self.runner.actions())
        self.assertNotIn(UpdateAction.ADAPTER_INSTALL, self.runner.actions())
        self.assertIn("Would run:", result.stdout)
        self.assertIn("git", result.stdout)
        self.assertIn("pull", result.stdout)
        self.assertIn("Run pre-upgrade doctor", result.stdout)
        self.assertIn("Refresh target adapter and skills", result.stdout)
        self.assertIn("Run final doctor", result.stdout)
        self.assertFalse(self.profile_marker.exists())

    def test_skip_doctor_and_adapter_gates_are_independent(self) -> None:
        cases = (
            (
                True,
                False,
                (UpdateAction.ADAPTER_INSTALL,),
            ),
            (
                False,
                True,
                (UpdateAction.PRE_DOCTOR, UpdateAction.FINAL_DOCTOR),
            ),
        )

        for skip_doctor, skip_adapter, expected in cases:
            with self.subTest(
                skip_doctor=skip_doctor,
                skip_adapter=skip_adapter,
            ):
                self.runner = FakeRunner()
                result = run_update(
                    self.request(
                        skip_doctor=skip_doctor,
                        skip_adapter=skip_adapter,
                    ),
                    dependencies=self.dependencies(),
                )

                self.assertEqual(result.exit_code, 0)
                project_actions = tuple(
                    action
                    for action in self.runner.actions()
                    if action
                    in {
                        UpdateAction.PRE_DOCTOR,
                        UpdateAction.ADAPTER_INSTALL,
                        UpdateAction.FINAL_DOCTOR,
                    }
                )
                self.assertEqual(project_actions, expected)

    def test_release_dry_run_invokes_installer_and_never_runs_source_tests(self) -> None:
        self.runner.queue(
            UpdateAction.GIT_PROBE,
            UpdateCommandResult(exit_code=1),
        )

        result = run_update(
            self.request(
                mode=UpdateMode.SELF,
                requested_version="2.0.0",
                skip_pull=False,
                skip_tests=False,
                dry_run=True,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source, UpdateSource.RELEASE)
        self.assertEqual(
            self.runner.commands(UpdateAction.RELEASE_INSTALL)[0].argv,
            (
                sys.executable,
                "-I",
                str(self.python_installer),
                "--version",
                "2.0.0",
                "--repository",
                "owner/agent-rails",
                "--install-root",
                str(self.install_root),
                "--bin-dir",
                str(self.bin_dir),
                "--dry-run",
            ),
        )
        self.assertNotIn(UpdateAction.SOURCE_TESTS, self.runner.actions())
        self.assertIn(
            "Skip source test suite for verified Release installation.",
            result.stdout,
        )
        self.assertEqual(self.reexec.requests, [])

    def test_release_switch_reexecs_exact_original_argv_and_environment(self) -> None:
        original_arguments = (
            "--self-only",
            "--version",
            "2.0.0",
            "--repository",
            "owner/agent-rails",
        )
        self.runner.queue(
            UpdateAction.GIT_PROBE,
            UpdateCommandResult(exit_code=1),
        )

        def install_release(command: UpdateCommand) -> UpdateCommandResult:
            release_home = self.install_root / "releases/2.0.0"
            (release_home / "scripts").mkdir(parents=True)
            new_helper = release_home / "scripts/agent-python-cli.py"
            new_helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
            (release_home / "VERSION").write_text("2.0.0\n", encoding="utf-8")
            (self.install_root / "current").symlink_to(
                release_home,
                target_is_directory=True,
            )
            return UpdateCommandResult(
                exit_code=0,
                stdout="release-installed\n",
            )

        self.runner.queue(UpdateAction.RELEASE_INSTALL, install_release)

        result = run_update(
            self.request(
                mode=UpdateMode.SELF,
                requested_version="2.0.0",
                skip_pull=False,
                original_arguments=original_arguments,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(len(self.reexec.requests), 1)
        request = self.reexec.requests[0]
        new_home = self.install_root / "releases/2.0.0"
        new_helper = new_home / "scripts/agent-python-cli.py"
        self.assertEqual(
            request.argv,
            (
                sys.executable,
                "-E",
                str(new_helper),
                "public",
                "update",
                *original_arguments,
                "--skip-pull",
            ),
        )
        expected_environment = dict(self.environment)
        expected_environment.update(
            {
                "AGENT_RAILS_UPDATE_REEXEC": "1",
                "AGENT_RAILS_HOME": str(new_home),
            }
        )
        self.assertEqual(dict(request.environment), expected_environment)
        self.assertEqual(
            self.runner.actions(),
            (UpdateAction.GIT_PROBE, UpdateAction.RELEASE_INSTALL),
        )
        self.assertIn("release-installed", result.stdout)
        self.assertNotIn("Agent Rails update complete.", result.stdout)

    def test_release_reexec_guard_prevents_a_second_process_switch(self) -> None:
        self.runner.queue(
            UpdateAction.GIT_PROBE,
            UpdateCommandResult(exit_code=1),
        )
        new_home = self.install_root / "current"
        release_home = self.install_root / "releases/2.0.0"
        (release_home / "scripts").mkdir(parents=True)
        new_helper = release_home / "scripts/agent-python-cli.py"
        new_helper.write_text("raise SystemExit(0)\n", encoding="utf-8")
        (release_home / "VERSION").write_text("2.0.0\n", encoding="utf-8")
        new_home.symlink_to(release_home, target_is_directory=True)
        environment = dict(self.environment)
        environment["AGENT_RAILS_UPDATE_REEXEC"] = "1"

        result = run_update(
            self.request(
                mode=UpdateMode.SELF,
                requested_version="2.0.0",
                skip_pull=False,
                environment=environment,
            ),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.source, UpdateSource.RELEASE)
        self.assertEqual(self.reexec.requests, [])
        self.assertIn("Agent Rails update complete.", result.stdout)

    def test_child_nonzero_exit_preserves_partial_output_and_stops(self) -> None:
        dangerous = "doctor-failed-\x1b]0;title\x07-\x85-\u202espoof"
        self.runner.queue(
            UpdateAction.PRE_DOCTOR,
            UpdateCommandResult(
                exit_code=37,
                stdout="partial-doctor-stdout\n",
                stderr=dangerous,
            ),
        )

        result = run_update(
            self.request(),
            dependencies=self.dependencies(),
        )

        self.assertEqual(result.exit_code, 37)
        self.assertEqual(result.failed_action, UpdateAction.PRE_DOCTOR)
        self.assertIn("partial-doctor-stdout", result.stdout)
        self.assertIn("\\x1b", result.stderr)
        self.assertIn("\\x07", result.stderr)
        self.assertIn("\\x85", result.stderr)
        self.assertIn("\\u202e", result.stderr)
        for raw in ("\x1b", "\x07", "\x85", "\u202e"):
            self.assertNotIn(raw, result.stderr)
        self.assertNotIn(UpdateAction.ADAPTER_INSTALL, self.runner.actions())
        self.assertNotIn(UpdateAction.FINAL_DOCTOR, self.runner.actions())
        self.assertNotIn("Agent Rails update complete.", result.stdout)
        self.assertTrue(
            all(
                isinstance(event.stream, UpdateEventStream)
                for event in result.events
            )
        )


if __name__ == "__main__":
    unittest.main()
