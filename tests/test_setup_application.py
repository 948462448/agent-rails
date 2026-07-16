#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.adapters.claude import (
    ClaudeAction,
    ClaudeAdapterError,
    ClaudeAdapterResult,
    ClaudeEvent,
    ClaudeEventStream,
    ClaudeInstallMode,
    ClaudeInstallRequest,
)
from agent_rails.adapters.codex import (
    CodexAction,
    CodexAdapterError,
    CodexAdapterResult,
    CodexDoctorRequest,
    CodexEvent,
    CodexEventStream,
    CodexInstallMode,
    CodexInstallRequest,
)
from agent_rails.adapters.opencode import (
    OpenCodeAction,
    OpenCodeAdapterError,
    OpenCodeAdapterResult,
    OpenCodeDoctorRequest,
    OpenCodeEvent,
    OpenCodeEventStream,
    OpenCodeInstallMode,
    OpenCodeInstallRequest,
)
from agent_rails.config.target_project import TargetProjectContext
from agent_rails.diagnostics.doctor import (
    DoctorEvent,
    DoctorEventStream,
    DoctorRequest,
    DoctorResult,
)
from agent_rails import setup_application as setup_module
from agent_rails.setup_application import (
    SetupAction,
    SetupApplicationError,
    SetupEventStream,
    SetupInputError,
    SetupInstallMode,
    SetupRequest,
    SetupStep,
    SetupTool,
    run_setup,
)


def _git(project: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class SetupApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-setup-application-"
        )
        self.root = Path(self.temporary.name)
        self.kit_home = self.root / "kit"
        self.project = self.root / "project"
        self.working_directory = self.root / "working"
        self.user_home = self.root / "user"
        self.profile = self.kit_home / "profiles/default.profile"
        self.profile_marker = self.root / "profile-loads.log"

        for path in (
            self.kit_home / "bin",
            self.kit_home / "profiles",
            self.project,
            self.working_directory,
            self.user_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        (self.kit_home / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        cli = self.kit_home / "bin/agent-rails"
        cli.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        cli.chmod(0o755)
        self.profile.write_text(
            "\n".join(
                (
                    'printf "loaded\\n" >> "$SETUP_PROFILE_MARKER"',
                    'AGENT_RAILS_CLAUDE_SETTINGS="$HOME/.claude/settings.json"',
                    'AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS="30000"',
                    'MEMORY_PROVIDER="local"',
                    'export SETUP_CHILD_ENV="from-profile"',
                    "",
                )
            ),
            encoding="utf-8",
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

        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "SETUP_PROFILE_MARKER": str(self.profile_marker),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        requested_project: Path | None = None,
        explicit_profile: str | None = None,
        tool: SetupTool = SetupTool.CLAUDE,
        mode: SetupInstallMode = SetupInstallMode.LOCAL,
        session_hook: bool = True,
        dry_run: bool = True,
        working_directory: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> SetupRequest:
        return SetupRequest(
            requested_project=(
                self.project if requested_project is None else requested_project
            ),
            kit_home=self.kit_home,
            explicit_profile=(
                str(self.profile)
                if explicit_profile is None
                else explicit_profile
            ),
            tool=tool,
            mode=mode,
            session_hook=session_hook,
            dry_run=dry_run,
            working_directory=(
                self.working_directory
                if working_directory is None
                else working_directory
            ),
            environment=(
                dict(self.environment) if environment is None else environment
            ),
        )

    @staticmethod
    def claude_result(
        context: TargetProjectContext, text: str = "claude-install"
    ) -> ClaudeAdapterResult:
        return ClaudeAdapterResult(
            action=ClaudeAction.INSTALL,
            project_root=context.root,
            profile_path=context.profile_path,
            task_pack_path=context.task_pack_path,
            mode=ClaudeInstallMode.LOCAL,
            events=(ClaudeEvent(ClaudeEventStream.STDOUT, text),),
        )

    @staticmethod
    def doctor_result(
        context: TargetProjectContext,
        text: str = "claude-doctor",
        *,
        failures: int = 0,
    ) -> DoctorResult:
        return DoctorResult(
            project_root=context.root,
            profile_path=context.profile_path,
            failures=failures,
            warnings=0,
            events=(DoctorEvent(DoctorEventStream.STDOUT, text),),
        )

    @staticmethod
    def codex_result(
        request: CodexInstallRequest | CodexDoctorRequest,
        context: TargetProjectContext,
        text: str,
        *,
        exit_code: int = 0,
    ) -> CodexAdapterResult:
        action = (
            CodexAction.INSTALL
            if isinstance(request, CodexInstallRequest)
            else CodexAction.DOCTOR
        )
        return CodexAdapterResult(
            action=action,
            project_root=context.root,
            profile_path=context.profile_path,
            mode=(
                request.mode
                if isinstance(request, CodexInstallRequest)
                else CodexInstallMode.LOCAL
            ),
            exit_code=exit_code,
            events=(CodexEvent(CodexEventStream.STDOUT, text),),
        )

    @staticmethod
    def opencode_result(
        request: OpenCodeInstallRequest | OpenCodeDoctorRequest,
        context: TargetProjectContext,
        text: str,
    ) -> OpenCodeAdapterResult:
        action = (
            OpenCodeAction.INSTALL
            if isinstance(request, OpenCodeInstallRequest)
            else OpenCodeAction.DOCTOR
        )
        return OpenCodeAdapterResult(
            action=action,
            project_root=context.root,
            profile_path=context.profile_path,
            task_pack_path=context.task_pack_path,
            mode=(
                request.mode
                if isinstance(request, OpenCodeInstallRequest)
                else OpenCodeInstallMode.LOCAL
            ),
            events=(OpenCodeEvent(OpenCodeEventStream.STDOUT, text),),
        )

    def test_request_is_strictly_typed(self) -> None:
        invalid_requests = (
            replace(self.request(), tool="claude"),
            replace(self.request(), mode="local"),
            replace(self.request(), session_hook=1),
            replace(self.request(), dry_run=0),
            replace(self.request(), working_directory="."),
            replace(self.request(), environment={"PATH": 3}),
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(SetupInputError) as raised:
                    run_setup(request)
                self.assertEqual(raised.exception.exit_code, 2)

    def test_missing_project_is_visible_input_error_and_terminal_safe(self) -> None:
        missing = self.root / "missing\nproject-\x1b]0;setup\x07-\x85-\u202espoof"

        with self.assertRaises(SetupInputError) as raised:
            run_setup(self.request(requested_project=missing))

        error = raised.exception
        message = str(error)
        self.assertEqual(error.exit_code, 2)
        self.assertIn("Project directory not found:", message)
        self.assertIn("missing\\nproject", message)
        self.assertIn("\\x1b", message)
        self.assertIn("\\x07", message)
        self.assertIn("\\x85", message)
        self.assertIn("\\u202e", message)
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\x07", message)
        self.assertNotIn("\x85", message)
        self.assertNotIn("\u202e", message)

    def test_auto_detection_ignores_empty_and_relative_path_segments(self) -> None:
        relative_bin = self.working_directory / "relative-bin"
        absolute_bin = self.root / "absolute-bin"
        relative_bin.mkdir()
        absolute_bin.mkdir()
        for executable in (
            relative_bin / "claude",
            absolute_bin / "opencode",
        ):
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
        environment = dict(self.environment)
        environment["PATH"] = f"relative-bin::{absolute_bin}"
        opencode_calls: list[OpenCodeInstallRequest] = []

        def run_opencode(request, *, context):
            self.assertIsInstance(request, OpenCodeInstallRequest)
            self.assertTrue(request.dry_run)
            opencode_calls.append(request)
            return self.opencode_result(request, context, "opencode-install")

        with (
            patch.object(setup_module, "run_opencode_adapter", run_opencode),
            patch.object(
                setup_module,
                "run_claude_adapter",
                side_effect=AssertionError("relative PATH entry was trusted"),
            ),
            patch.object(
                setup_module,
                "run_codex_adapter",
                side_effect=AssertionError("unexpected Codex selection"),
            ),
        ):
            result = run_setup(
                self.request(
                    tool=SetupTool.AUTO,
                    dry_run=True,
                    environment=environment,
                )
            )

        self.assertEqual(result.selected_tools, (SetupTool.OPENCODE,))
        self.assertEqual(len(opencode_calls), 1)
        self.assertIn("Detected tool: opencode", result.stdout)
        self.assertIn("Would run:", result.stdout)
        self.assertIn("opencode doctor", result.stdout)

    def test_auto_detection_rejects_zero_or_multiple_tools(self) -> None:
        empty_environment = dict(self.environment)
        empty_environment["PATH"] = "relative-bin::"
        with self.assertRaises(SetupInputError) as no_tool:
            run_setup(
                self.request(tool=SetupTool.AUTO, environment=empty_environment)
            )
        self.assertEqual(no_tool.exception.exit_code, 2)
        self.assertIn("No supported coding-agent CLI detected", str(no_tool.exception))

        absolute_bin = self.root / "multiple-bin"
        absolute_bin.mkdir()
        for name in ("claude", "codex"):
            executable = absolute_bin / name
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
        multiple_environment = dict(self.environment)
        multiple_environment["PATH"] = str(absolute_bin)
        with self.assertRaises(SetupInputError) as multiple:
            run_setup(
                self.request(tool=SetupTool.AUTO, environment=multiple_environment)
            )
        self.assertEqual(multiple.exception.exit_code, 2)
        self.assertIn(
            "Multiple supported tools detected: claude, codex",
            str(multiple.exception),
        )
        self.assertIn("--tool all", str(multiple.exception))

    def test_all_resolves_profile_once_and_composes_services_in_order(self) -> None:
        calls: list[tuple[SetupTool, SetupAction]] = []
        contexts: list[TargetProjectContext] = []
        child_environments: list[dict[str, str]] = []
        real_resolver = setup_module.resolve_target_project

        def run_claude(request, *, context):
            self.assertIsInstance(request, ClaudeInstallRequest)
            self.assertEqual(request.mode, ClaudeInstallMode.PROJECT)
            self.assertFalse(request.dry_run)
            self.assertFalse(request.force)
            self.assertFalse(request.global_reminder)
            self.assertFalse(request.session_hook)
            calls.append((SetupTool.CLAUDE, SetupAction.INSTALL))
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return self.claude_result(context)

        def run_doctor_service(request, *, context):
            self.assertIsInstance(request, DoctorRequest)
            self.assertFalse(request.fix)
            self.assertFalse(request.online_memory_smoke)
            calls.append((SetupTool.CLAUDE, SetupAction.DOCTOR))
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return self.doctor_result(context)

        def run_codex(request, *, context):
            self.assertIsInstance(request, (CodexInstallRequest, CodexDoctorRequest))
            if isinstance(request, CodexInstallRequest):
                self.assertEqual(request.mode, CodexInstallMode.PROJECT)
                self.assertTrue(request.fix_project)
                action = SetupAction.INSTALL
                text = "codex-install"
            else:
                action = SetupAction.DOCTOR
                text = "codex-doctor"
            calls.append((SetupTool.CODEX, action))
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return self.codex_result(request, context, text)

        def run_opencode(request, *, context):
            self.assertIsInstance(
                request, (OpenCodeInstallRequest, OpenCodeDoctorRequest)
            )
            if isinstance(request, OpenCodeInstallRequest):
                self.assertEqual(request.mode, OpenCodeInstallMode.PROJECT)
                action = SetupAction.INSTALL
                text = "opencode-install"
            else:
                action = SetupAction.DOCTOR
                text = "opencode-doctor"
            calls.append((SetupTool.OPENCODE, action))
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return self.opencode_result(request, context, text)

        with (
            patch.object(
                setup_module,
                "resolve_target_project",
                wraps=real_resolver,
            ) as resolver,
            patch.object(setup_module, "run_claude_adapter", run_claude),
            patch.object(setup_module, "run_doctor", run_doctor_service),
            patch.object(setup_module, "run_codex_adapter", run_codex),
            patch.object(setup_module, "run_opencode_adapter", run_opencode),
        ):
            result = run_setup(
                self.request(
                    tool=SetupTool.ALL,
                    mode=SetupInstallMode.PROJECT,
                    session_hook=False,
                    dry_run=False,
                )
            )

        expected_actions = [
            (SetupTool.CLAUDE, SetupAction.INSTALL),
            (SetupTool.CLAUDE, SetupAction.DOCTOR),
            (SetupTool.CODEX, SetupAction.INSTALL),
            (SetupTool.CODEX, SetupAction.DOCTOR),
            (SetupTool.OPENCODE, SetupAction.INSTALL),
            (SetupTool.OPENCODE, SetupAction.DOCTOR),
        ]
        self.assertEqual(calls, expected_actions)
        self.assertEqual(
            result.selected_tools,
            (SetupTool.CLAUDE, SetupTool.CODEX, SetupTool.OPENCODE),
        )
        self.assertEqual(
            result.steps,
            tuple(SetupStep(tool, action, 0) for tool, action in expected_actions),
        )
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.failed_tool)
        self.assertIsNone(result.failed_action)
        self.assertEqual(resolver.call_count, 1)
        resolver_kwargs = resolver.call_args.kwargs
        self.assertTrue(resolver_kwargs["require_profile"])
        self.assertTrue(resolver_kwargs["load_profile"])
        profile_variables = set(resolver_kwargs["profile_variables"])
        self.assertIn("AGENT_RAILS_CLAUDE_SETTINGS", profile_variables)
        self.assertIn("AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS", profile_variables)
        self.assertIn("MEMORY_PROVIDER", profile_variables)
        self.assertEqual(self.profile_marker.read_text(encoding="utf-8"), "loaded\n")
        self.assertEqual(len({id(context) for context in contexts}), 1)
        self.assertEqual(
            [environment["SETUP_CHILD_ENV"] for environment in child_environments],
            ["from-profile"] * len(expected_actions),
        )

        positions = [
            result.stdout.index(marker)
            for marker in (
                "claude-install",
                "claude-doctor",
                "codex-install",
                "codex-doctor",
                "opencode-install",
                "opencode-doctor",
            )
        ]
        self.assertEqual(positions, sorted(positions))
        self.assertTrue(result.stdout.endswith("Agent Rails setup complete.\n"))

    def test_dry_run_executes_install_preview_but_only_plans_doctor(self) -> None:
        doctor = Mock(side_effect=AssertionError("dry-run executed Doctor"))

        def run_claude(request, *, context):
            self.assertTrue(request.dry_run)
            return self.claude_result(context, "claude-dry-run")

        with (
            patch.object(setup_module, "run_claude_adapter", run_claude),
            patch.object(setup_module, "run_doctor", doctor),
        ):
            result = run_setup(self.request(tool=SetupTool.CLAUDE, dry_run=True))

        doctor.assert_not_called()
        self.assertEqual(result.exit_code, 0)
        self.assertIn("claude-dry-run", result.stdout)
        self.assertIn("Would run:", result.stdout)
        self.assertIn("doctor --project", result.stdout)
        self.assertIn(str(self.project), result.stdout)
        self.assertIn(str(self.profile), result.stdout)

    def test_facade_calls_python_services_without_shell_subprocesses(self) -> None:
        context = TargetProjectContext(
            root=self.project,
            default_name=self.project.name,
            profile_path=str(self.profile),
            profile_status="loaded",
            is_git_repo=True,
            project_name=self.project.name,
            worktree_slug_preset="",
            worktree_slug=self.project.name,
            task_pack_path=str(self.root / "task-pack.md"),
            profile_values={},
            profile_environment={},
        )

        def run_claude(request, *, context):
            return self.claude_result(context, "direct-python-service")

        with (
            patch.object(
                setup_module,
                "resolve_target_project",
                return_value=context,
            ),
            patch.object(setup_module, "run_claude_adapter", run_claude),
            patch(
                "subprocess.run",
                side_effect=AssertionError("Setup spawned a shell subprocess"),
            ),
        ):
            result = run_setup(
                self.request(tool=SetupTool.CLAUDE, dry_run=True)
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIn("direct-python-service", result.stdout)

    def test_nonzero_doctor_result_stops_all_and_returns_partial_result(self) -> None:
        later = Mock(side_effect=AssertionError("continued after failed Doctor"))

        def run_claude(request, *, context):
            return self.claude_result(context, "claude-before-failure")

        def run_doctor_service(request, *, context):
            return self.doctor_result(
                context,
                "doctor-reported-failure",
                failures=1,
            )

        with (
            patch.object(setup_module, "run_claude_adapter", run_claude),
            patch.object(setup_module, "run_doctor", run_doctor_service),
            patch.object(setup_module, "run_codex_adapter", later),
            patch.object(setup_module, "run_opencode_adapter", later),
        ):
            result = run_setup(
                self.request(tool=SetupTool.ALL, dry_run=False)
            )

        later.assert_not_called()
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.failed_tool, SetupTool.CLAUDE)
        self.assertEqual(result.failed_action, SetupAction.DOCTOR)
        self.assertEqual(
            result.steps,
            (
                SetupStep(SetupTool.CLAUDE, SetupAction.INSTALL, 0),
                SetupStep(SetupTool.CLAUDE, SetupAction.DOCTOR, 1),
            ),
        )
        self.assertIn("claude-before-failure", result.stdout)
        self.assertIn("doctor-reported-failure", result.stdout)
        self.assertNotIn("Agent Rails setup complete.", result.stdout)

    def test_runtime_error_preserves_prior_and_child_output_and_stops(self) -> None:
        calls: list[str] = []
        dangerous = "codex failed-\x1b]0;title\x07-\x85-\u202espoof"

        def run_claude(request, *, context):
            calls.append("claude-install")
            return self.claude_result(context, "claude-completed")

        def run_doctor_service(request, *, context):
            calls.append("claude-doctor")
            return self.doctor_result(context, "doctor-completed")

        def run_codex(request, *, context):
            calls.append("codex-install")
            raise CodexAdapterError(
                dangerous,
                exit_code=41,
                events=(
                    CodexEvent(CodexEventStream.STDOUT, "codex-partial-stdout"),
                    CodexEvent(CodexEventStream.STDERR, dangerous),
                ),
            )

        later = Mock(side_effect=AssertionError("continued after Codex failure"))
        with (
            patch.object(setup_module, "run_claude_adapter", run_claude),
            patch.object(setup_module, "run_doctor", run_doctor_service),
            patch.object(setup_module, "run_codex_adapter", run_codex),
            patch.object(setup_module, "run_opencode_adapter", later),
        ):
            with self.assertRaises(SetupApplicationError) as raised:
                run_setup(self.request(tool=SetupTool.ALL, dry_run=False))

        error = raised.exception
        later.assert_not_called()
        self.assertEqual(
            calls, ["claude-install", "claude-doctor", "codex-install"]
        )
        self.assertEqual(error.exit_code, 41)
        self.assertEqual(error.failed_tool, SetupTool.CODEX)
        self.assertEqual(error.failed_action, SetupAction.INSTALL)
        self.assertEqual(
            error.steps,
            (
                SetupStep(SetupTool.CLAUDE, SetupAction.INSTALL, 0),
                SetupStep(SetupTool.CLAUDE, SetupAction.DOCTOR, 0),
            ),
        )
        self.assertIn("claude-completed", error.stdout)
        self.assertIn("doctor-completed", error.stdout)
        self.assertIn("codex-partial-stdout", error.stdout)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\x85", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        self.assertNotIn("\x1b", error.stderr)
        self.assertNotIn("\x07", error.stderr)
        self.assertNotIn("\x85", error.stderr)
        self.assertNotIn("\u202e", error.stderr)
        self.assertNotIn("\x1b", str(error))
        self.assertNotIn("\x85", str(error))
        self.assertNotIn("\u202e", str(error))

    def test_claude_and_opencode_errors_preserve_sanitized_partial_events(
        self,
    ) -> None:
        dangerous = "failed-\x1b]0;title\x07-\x85-\u202espoof"
        cases = (
            (
                SetupTool.CLAUDE,
                "run_claude_adapter",
                ClaudeAdapterError(
                    dangerous,
                    exit_code=37,
                    events=(
                        ClaudeEvent(
                            ClaudeEventStream.STDOUT,
                            "claude-partial",
                        ),
                        ClaudeEvent(ClaudeEventStream.STDERR, dangerous),
                    ),
                ),
                "claude-partial",
                37,
            ),
            (
                SetupTool.OPENCODE,
                "run_opencode_adapter",
                OpenCodeAdapterError(
                    dangerous,
                    exit_code=38,
                    events=(
                        OpenCodeEvent(
                            OpenCodeEventStream.STDOUT,
                            "opencode-partial",
                        ),
                        OpenCodeEvent(OpenCodeEventStream.STDERR, dangerous),
                    ),
                ),
                "opencode-partial",
                38,
            ),
        )

        for tool, service_name, child_error, partial, exit_code in cases:
            with self.subTest(tool=tool.value):
                with patch.object(
                    setup_module,
                    service_name,
                    side_effect=child_error,
                ):
                    with self.assertRaises(SetupApplicationError) as raised:
                        run_setup(
                            self.request(
                                tool=tool,
                                dry_run=False,
                            )
                        )

                error = raised.exception
                self.assertEqual(error.exit_code, exit_code)
                self.assertEqual(error.failed_tool, tool)
                self.assertEqual(error.failed_action, SetupAction.INSTALL)
                self.assertEqual(error.steps, ())
                self.assertIn(partial, error.stdout)
                self.assertIn("\\x1b", error.stderr)
                self.assertIn("\\x07", error.stderr)
                self.assertIn("\\x85", error.stderr)
                self.assertIn("\\u202e", error.stderr)
                for raw in ("\x1b", "\x07", "\x85", "\u202e"):
                    self.assertNotIn(raw, error.stderr)
                    self.assertNotIn(raw, str(error))

    def test_relative_paths_are_anchored_and_rendered_output_is_terminal_safe(
        self,
    ) -> None:
        project = self.working_directory / "project\nname-\x85-\u202e"
        profile = self.working_directory / "profile\x1b]0;title\x07.profile"
        project.mkdir()
        profile.write_text("PROJECT_NAME=fixture\n", encoding="utf-8")
        context = TargetProjectContext(
            root=project,
            default_name=project.name,
            profile_path=str(profile),
            profile_status="loaded",
            is_git_repo=False,
            project_name="fixture",
            worktree_slug_preset="",
            worktree_slug="fixture",
            task_pack_path=str(self.root / "task-pack.md"),
            profile_values={},
            profile_environment={},
        )

        def resolve(requested_project, **kwargs):
            self.assertEqual(requested_project, project)
            self.assertEqual(kwargs["explicit_profile"], str(profile))
            return context

        def run_claude(request, *, context):
            return self.claude_result(
                context,
                "child-\x1b]0;title\x07-\x85-\u202espoof",
            )

        with (
            patch.object(setup_module, "resolve_target_project", resolve),
            patch.object(setup_module, "run_claude_adapter", run_claude),
        ):
            result = run_setup(
                self.request(
                    requested_project=Path(project.name),
                    explicit_profile=profile.name,
                    tool=SetupTool.CLAUDE,
                    dry_run=True,
                    working_directory=self.working_directory,
                )
            )

        self.assertEqual(result.project_root, project)
        self.assertEqual(result.profile_path, str(profile))
        self.assertIn("project\\nname", result.stdout)
        self.assertIn("\\x1b", result.stdout)
        self.assertIn("\\x07", result.stdout)
        self.assertIn("\\x85", result.stdout)
        self.assertIn("\\u202e", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)
        self.assertNotIn("\x85", result.stdout)
        self.assertNotIn("\u202e", result.stdout)
        self.assertTrue(
            all(isinstance(event.stream, SetupEventStream) for event in result.events)
        )


if __name__ == "__main__":
    unittest.main()
