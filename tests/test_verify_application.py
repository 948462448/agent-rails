#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.config.target_project import TargetProjectContext  # noqa: E402
from agent_rails.verification.check_application import (  # noqa: E402
    CHECK_PROFILE_VARIABLES,
    CheckApplicationError,
    CheckApplicationRequest,
    CheckCliOverrides,
    CheckExecutionResult,
    CheckMode,
    PreparedCheck,
)
from agent_rails.memory.candidate import MemoryCandidateResult  # noqa: E402
from agent_rails.verification.failure_protocol import FailureHistory  # noqa: E402
from agent_rails.verification.plan import (  # noqa: E402
    VerificationCommands,
    VerificationPlan,
    VerificationStep,
)
from agent_rails.verification.repair_pack import VerificationFailure  # noqa: E402
from agent_rails.verification.publish_check import (  # noqa: E402
    PublishCheckCliOverrides,
    PublishCheckError,
    PublishCheckEvent,
    PublishCheckEventStream,
    PublishCheckRequest,
    PublishCheckResult,
)
from agent_rails.verification import verify_application as verify_module  # noqa: E402
from agent_rails.verification.verify_application import (  # noqa: E402
    VerifyApplicationError,
    VerifyEventStream,
    VerifyInputError,
    VerifyMode,
    VerifyRequest,
    run_verify,
)


class VerifyApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-verify-application-"
        )
        self.root = Path(self.temporary.name)
        self.working_directory = self.root / "working"
        self.kit_home = self.root / "kit"
        self.project = self.working_directory / "project"
        self.profile = self.root / "verify.profile"
        self.profile_marker = self.root / "profile-loads.log"
        self.user_home = self.root / "user"
        for path in (
            self.working_directory,
            self.kit_home / "bin",
            self.project,
            self.user_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        executable = self.kit_home / "bin/agent-rails"
        executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        executable.chmod(0o755)
        self.profile.write_text(
            "\n".join(
                (
                    'printf "loaded\\n" >> "$VERIFY_PROFILE_MARKER"',
                    'BASE_REF="profile-base"',
                    'VERIFY_PROJECT="printf profile-check\\n"',
                    'export VERIFY_CHILD_ENV="from-profile"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.environment = {
            "HOME": str(self.user_home),
            "PATH": os.environ.get("PATH", ""),
            "VERIFY_PROFILE_MARKER": str(self.profile_marker),
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        requested_project: Path | None = None,
        kit_home: Path | None = None,
        explicit_profile: str | None = None,
        mode: VerifyMode = VerifyMode.DELIVERY,
        print_only: bool = False,
        base_ref: str | None = None,
        target_ref: str | None = None,
        no_secret_scan: bool = False,
        working_directory: Path | None = None,
        environment: dict[str, str] | None = None,
    ) -> VerifyRequest:
        return VerifyRequest(
            requested_project=(
                self.project if requested_project is None else requested_project
            ),
            kit_home=self.kit_home if kit_home is None else kit_home,
            explicit_profile=(
                str(self.profile)
                if explicit_profile is None
                else explicit_profile
            ),
            mode=mode,
            print_only=print_only,
            base_ref=base_ref,
            target_ref=target_ref,
            no_secret_scan=no_secret_scan,
            working_directory=(
                self.working_directory
                if working_directory is None
                else working_directory
            ),
            environment=(
                dict(self.environment) if environment is None else environment
            ),
        )

    def context(
        self,
        *,
        project: Path | None = None,
        profile: Path | None = None,
        profile_environment: dict[str, str] | None = None,
    ) -> TargetProjectContext:
        project = self.project if project is None else project
        profile = self.profile if profile is None else profile
        return TargetProjectContext(
            root=project,
            default_name=project.name,
            profile_path=str(profile),
            profile_status="loaded",
            is_git_repo=False,
            project_name=project.name,
            worktree_slug_preset="",
            worktree_slug=project.name,
            task_pack_path=str(project / ".agent-rails/task-pack.md"),
            profile_values={},
            profile_environment=(
                dict(self.environment)
                if profile_environment is None
                else profile_environment
            ),
        )

    @staticmethod
    def publish_result(text: str = "publish-report\n") -> PublishCheckResult:
        return PublishCheckResult(
            prepared=Mock(name="prepared-publish-check"),
            events=(
                PublishCheckEvent(PublishCheckEventStream.STDOUT, text),
            ),
        )

    def test_request_is_strictly_typed_and_secret_scan_requires_publish(self) -> None:
        invalid_requests = (
            replace(self.request(), requested_project="project"),
            replace(self.request(), kit_home="kit"),
            replace(self.request(), explicit_profile=Path("profile")),
            replace(self.request(), mode="delivery"),
            replace(self.request(), print_only=1),
            replace(self.request(), base_ref=3),
            replace(self.request(), target_ref=3),
            replace(self.request(), no_secret_scan=0),
            replace(self.request(), working_directory="."),
            replace(self.request(), environment={"PATH": 3}),
            replace(self.request(), no_secret_scan=True),
        )

        for request in invalid_requests:
            with self.subTest(request=request):
                with self.assertRaises(VerifyInputError) as raised:
                    run_verify(request)
                self.assertEqual(raised.exception.exit_code, 2)

    def test_missing_project_preserves_historical_silent_exit_one(self) -> None:
        missing = Path("missing\nproject-\x1b]0;verify\x07-\x85-\u202espoof")

        with self.assertRaises(VerifyApplicationError) as raised:
            run_verify(self.request(requested_project=missing))

        error = raised.exception
        self.assertEqual(error.exit_code, 1)
        self.assertEqual(str(error), "")
        self.assertEqual(error.stdout, "")
        self.assertEqual(error.stderr, "")
        self.assertEqual(error.events, ())

    def test_delivery_run_forwards_exact_check_scope_and_completes(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")
        requests: list[CheckApplicationRequest] = []

        def prepare(request: CheckApplicationRequest, *, context):
            requests.append(request)
            self.assertIs(context, expected_context)
            return prepared

        def execute(value, *, stdout, stderr):
            self.assertIs(value, prepared)
            stdout.write("check-runtime-stdout\n")
            stderr.write("check-runtime-stderr\n")
            return CheckExecutionResult(exit_code=0, completed_steps=2)

        expected_context = context
        publish = Mock(side_effect=AssertionError("delivery invoked Publish"))
        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", prepare),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-report\n",
            ),
            patch.object(verify_module, "execute_check", execute),
            patch.object(verify_module, "run_publish_check", publish),
        ):
            result = run_verify(
                self.request(
                    mode=VerifyMode.DELIVERY,
                    print_only=False,
                    base_ref="release-base",
                    target_ref="frozen-target",
                )
            )

        publish.assert_not_called()
        self.assertEqual(len(requests), 1)
        check_request = requests[0]
        self.assertEqual(check_request.requested_project, self.project)
        self.assertEqual(check_request.kit_home, self.kit_home)
        self.assertEqual(check_request.explicit_profile, str(self.profile))
        self.assertEqual(
            check_request.overrides,
            CheckCliOverrides(
                base_ref="release-base",
                target_ref="frozen-target",
                target_ref_explicit=True,
                mode=CheckMode.RUN,
            ),
        )
        self.assertEqual(result.mode, VerifyMode.DELIVERY)
        self.assertFalse(result.print_only)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.check_execution.completed_steps, 2)
        self.assertIsNone(result.publish_result)
        self.assertIn("Agent Rails Verify\n", result.stdout)
        self.assertIn("Mode: delivery\n", result.stdout)
        self.assertIn("check-report\n", result.stdout)
        self.assertIn("check-runtime-stdout\n", result.stdout)
        self.assertEqual(result.stderr, "check-runtime-stderr\n")
        self.assertTrue(result.stdout.endswith("Agent Rails verification complete.\n"))

    def test_publish_preview_forwards_exact_scope_and_secret_policy(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")
        check_requests: list[CheckApplicationRequest] = []
        publish_requests: list[PublishCheckRequest] = []
        execution_calls: list[object] = []
        expected_publish = self.publish_result("publish-service-report\n")

        def prepare(request: CheckApplicationRequest, *, context):
            self.assertIs(context, expected_context)
            check_requests.append(request)
            return prepared

        def execute(value, *, stdout, stderr):
            execution_calls.append(value)
            return CheckExecutionResult(exit_code=0, completed_steps=0)

        def publish(request: PublishCheckRequest, *, context):
            self.assertIs(context, expected_context)
            publish_requests.append(request)
            return expected_publish

        expected_context = context
        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", prepare),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-preview\n",
            ),
            patch.object(verify_module, "execute_check", execute),
            patch.object(verify_module, "run_publish_check", publish),
        ):
            result = run_verify(
                self.request(
                    mode=VerifyMode.PUBLISH,
                    print_only=True,
                    base_ref="deployed-sha",
                    target_ref="candidate-sha",
                    no_secret_scan=True,
                )
            )

        self.assertEqual(execution_calls, [prepared])
        self.assertEqual(
            check_requests[0].overrides,
            CheckCliOverrides(
                base_ref="deployed-sha",
                target_ref="candidate-sha",
                target_ref_explicit=True,
                mode=CheckMode.PREVIEW,
            ),
        )
        self.assertEqual(len(publish_requests), 1)
        publish_request = publish_requests[0]
        self.assertEqual(publish_request.requested_project, self.project)
        self.assertEqual(publish_request.kit_home, self.kit_home)
        self.assertEqual(publish_request.explicit_profile, str(self.profile))
        self.assertEqual(
            publish_request.overrides,
            PublishCheckCliOverrides(
                base_ref="deployed-sha",
                base_ref_explicit=True,
                target_ref="candidate-sha",
                target_ref_explicit=True,
                scan_secrets=False,
            ),
        )
        self.assertEqual(result.mode, VerifyMode.PUBLISH)
        self.assertTrue(result.print_only)
        self.assertEqual(result.exit_code, 0)
        self.assertIs(result.publish_result, expected_publish)
        self.assertLess(
            result.stdout.index("check-preview"),
            result.stdout.index("Publish readiness"),
        )
        self.assertLess(
            result.stdout.index("Publish readiness"),
            result.stdout.index("publish-service-report"),
        )
        self.assertTrue(
            result.stdout.endswith("Agent Rails publish verification complete.\n")
        )

    def test_profile_is_sourced_once_and_one_context_is_injected_everywhere(self) -> None:
        contexts: list[TargetProjectContext] = []
        child_environments: list[dict[str, str]] = []
        real_resolver = verify_module.resolve_target_project
        prepared = Mock(name="prepared-check")

        def prepare(request: CheckApplicationRequest, *, context):
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return prepared

        def publish(request: PublishCheckRequest, *, context):
            contexts.append(context)
            child_environments.append(dict(request.environment))
            return self.publish_result()

        with (
            patch.object(
                verify_module,
                "resolve_target_project",
                wraps=real_resolver,
            ) as resolver,
            patch.object(verify_module, "prepare_check", prepare),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-report\n",
            ),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(0, 0),
            ),
            patch.object(verify_module, "run_publish_check", publish),
        ):
            result = run_verify(self.request(mode=VerifyMode.PUBLISH))

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(resolver.call_count, 1)
        resolver_kwargs = resolver.call_args.kwargs
        self.assertTrue(resolver_kwargs["require_profile"])
        self.assertTrue(resolver_kwargs["load_profile"])
        self.assertFalse(resolver_kwargs["load_environment_file"])
        self.assertTrue(resolver_kwargs["capture_profile_environment"])
        self.assertEqual(
            set(resolver_kwargs["profile_variables"]),
            set(CHECK_PROFILE_VARIABLES),
        )
        self.assertEqual(
            self.profile_marker.read_text(encoding="utf-8"),
            "loaded\n",
        )
        self.assertEqual(len(contexts), 2)
        self.assertEqual(len({id(context) for context in contexts}), 1)
        self.assertEqual(
            [environment["VERIFY_CHILD_ENV"] for environment in child_environments],
            ["from-profile", "from-profile"],
        )

    def test_nonzero_check_short_circuits_publish_and_completion(self) -> None:
        context = self.context()
        prepared = Mock(
            name="prepared-check",
            changed_paths=("runtime/module.py",),
            project_root=self.project,
            scope=None,
            target_sha=None,
        )
        publish = Mock(side_effect=AssertionError("failed Check invoked Publish"))

        def execute(value, *, stdout, stderr):
            stdout.write("check-failed-after-output\n")
            return CheckExecutionResult(
                exit_code=19,
                completed_steps=1,
                failure=VerificationFailure(
                    reason="runtime tests",
                    exit_code=19,
                    completed_steps=1,
                    stdout="",
                    stderr="runtime/module.py:12: AssertionError\n",
                ),
            )

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-report\n",
            ),
            patch.object(verify_module, "execute_check", execute),
            patch.object(verify_module, "run_publish_check", publish),
        ):
            result = run_verify(self.request(mode=VerifyMode.PUBLISH))

        publish.assert_not_called()
        self.assertEqual(result.exit_code, 19)
        self.assertEqual(result.check_execution.exit_code, 19)
        self.assertIsNone(result.publish_result)
        self.assertIn("check-failed-after-output", result.stdout)
        self.assertIn("Repair Pack", result.stdout)
        self.assertIn("runtime/module.py:12", result.stdout)
        self.assertNotIn("python3 tests/runtime.py", result.stdout)
        self.assertNotIn("Publish readiness", result.stdout)
        self.assertNotIn("verification complete", result.stdout)

    def test_repeated_failures_escalate_and_success_clears_history(self) -> None:
        context = self.context()
        prepared = Mock(
            name="prepared-check",
            changed_paths=("runtime/module.py",),
            project_root=self.project,
            scope=None,
            target_sha="target-a",
        )
        calls = 0

        def execute(value, *, stdout, stderr):
            nonlocal calls
            calls += 1
            if calls == 4:
                return CheckExecutionResult(exit_code=0, completed_steps=1)
            return CheckExecutionResult(
                exit_code=19,
                completed_steps=0,
                failure=VerificationFailure(
                    reason="runtime tests",
                    exit_code=19,
                    completed_steps=0,
                    stdout="",
                    stderr="runtime/module.py:12: AssertionError\n",
                ),
            )

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(verify_module, "render_check_report", return_value=""),
            patch.object(verify_module, "execute_check", execute),
        ):
            first = run_verify(self.request())
            second = run_verify(self.request())
            third = run_verify(self.request())
            success = run_verify(self.request())
            after_success = run_verify(self.request())

        self.assertIn("Consecutive occurrences: 1", first.stdout)
        self.assertIn("Consecutive occurrences: 2", second.stdout)
        self.assertIn("change strategy", second.stdout)
        self.assertIn("Consecutive occurrences: 3", third.stdout)
        self.assertIn("stop blind retries", third.stdout)
        self.assertEqual(success.exit_code, 0)
        self.assertIn("Consecutive occurrences: 1", after_success.stdout)

    def test_verified_repair_publishes_candidate_without_memory_write(self) -> None:
        context = self.context()
        prepared = PreparedCheck(
            project_root=self.project,
            profile_path=str(self.profile),
            is_git_repo=True,
            requested_target_ref="HEAD",
            target_ref_explicit=False,
            target_sha="a" * 40,
            head_sha="a" * 40,
            resolved_base_ref="base",
            merge_base="b" * 40,
            changed_paths=("src/session.py",),
            commands=VerificationCommands(),
            plan=VerificationPlan(
                steps=(VerificationStep("python changed", "private command"),)
            ),
            scope=None,
            mode=CheckMode.RUN,
            suppress_marker=False,
            runner_shell="/bin/sh",
            environment={},
            worktree_fingerprint=None,
        )
        candidate = MemoryCandidateResult(
            path=self.root / "candidate.md",
            target_sha="a" * 40,
            failure_fingerprint="b" * 64,
        )

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(verify_module, "render_check_report", return_value=""),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(exit_code=0, completed_steps=1),
            ),
            patch.object(
                verify_module,
                "read_failure_history",
                return_value=FailureHistory("b" * 64, 2),
            ),
            patch.object(
                verify_module,
                "publish_memory_candidate",
                return_value=candidate,
            ) as publish,
        ):
            result = run_verify(self.request())

        publish.assert_called_once()
        self.assertEqual(result.memory_candidate, candidate)
        self.assertIn("Memory Candidate:", result.stdout)
        self.assertIn("no local memory card was written", result.stdout)

    def test_check_runtime_error_preserves_prior_output_exit_and_sanitizes(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")
        dangerous = "runner-\x1b]0;title\x07-\x85-\u202espoof"
        failure = CheckApplicationError(dangerous)
        failure.exit_code = 47
        publish = Mock(side_effect=AssertionError("failed Check invoked Publish"))

        def execute(value, *, stdout, stderr):
            stdout.write("partial-check-stdout\n")
            stderr.write(dangerous)
            raise failure

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-report-before-runtime\n",
            ),
            patch.object(verify_module, "execute_check", execute),
            patch.object(verify_module, "run_publish_check", publish),
        ):
            with self.assertRaises(VerifyApplicationError) as raised:
                run_verify(self.request(mode=VerifyMode.PUBLISH))

        error = raised.exception
        publish.assert_not_called()
        self.assertEqual(error.exit_code, 47)
        self.assertIn("check-report-before-runtime", error.stdout)
        self.assertIn("partial-check-stdout", error.stdout)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\x85", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        self.assertNotIn("\x1b", error.stderr)
        self.assertNotIn("\x85", error.stderr)
        self.assertNotIn("\u202e", error.stderr)
        self.assertNotIn("verification complete", error.stdout)
        self.assertNotIn("\x1b", str(error))

    def test_publish_runtime_error_preserves_check_and_child_events(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")
        dangerous = "publish-\x1b]0;title\x07-\x85-\u202espoof"
        failure = PublishCheckError(dangerous)
        failure.exit_code = 53
        failure.events = (
            PublishCheckEvent(
                PublishCheckEventStream.STDOUT,
                "partial-publish-stdout\n",
            ),
            PublishCheckEvent(PublishCheckEventStream.STDERR, dangerous),
        )

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-before-publish\n",
            ),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(0, 1),
            ),
            patch.object(verify_module, "run_publish_check", side_effect=failure),
        ):
            with self.assertRaises(VerifyApplicationError) as raised:
                run_verify(self.request(mode=VerifyMode.PUBLISH))

        error = raised.exception
        self.assertEqual(error.exit_code, 53)
        self.assertIn("check-before-publish", error.stdout)
        self.assertIn("Publish readiness", error.stdout)
        self.assertIn("partial-publish-stdout", error.stdout)
        self.assertIn("\\x1b", error.stderr)
        self.assertIn("\\x07", error.stderr)
        self.assertIn("\\x85", error.stderr)
        self.assertIn("\\u202e", error.stderr)
        self.assertNotIn("\x1b", error.stderr)
        self.assertNotIn("\x85", error.stderr)
        self.assertNotIn("\u202e", error.stderr)
        self.assertNotIn("verification complete", error.stdout)

    def test_nonzero_publish_result_preserves_report_and_skips_completion(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")
        failed_publish = replace(self.publish_result("publish-failed\n"), exit_code=23)

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="check-before-publish\n",
            ),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(0, 1),
            ),
            patch.object(
                verify_module,
                "run_publish_check",
                return_value=failed_publish,
            ),
        ):
            result = run_verify(self.request(mode=VerifyMode.PUBLISH))

        self.assertEqual(result.exit_code, 23)
        self.assertIs(result.publish_result, failed_publish)
        self.assertIn("publish-failed", result.stdout)
        self.assertNotIn("verification complete", result.stdout)

    def test_publish_rejects_head_drift_after_successful_check(self) -> None:
        def git(*arguments: str) -> str:
            environment = dict(os.environ)
            for name in (
                "GIT_DIR",
                "GIT_WORK_TREE",
                "GIT_INDEX_FILE",
                "GIT_COMMON_DIR",
            ):
                environment.pop(name, None)
            return subprocess.run(
                ("git", "-C", str(self.project), *arguments),
                env=environment,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ).stdout

        git("init", "-q")
        git("config", "user.name", "Agent Rails Test")
        git("config", "user.email", "agent-rails@example.invalid")
        (self.project / "README.md").write_text("checked\n", encoding="utf-8")
        git("add", "README.md")
        git("commit", "-qm", "checked target")
        base_sha = git("rev-parse", "HEAD").strip()

        def move_head(prepared, *, stdout, stderr):
            del prepared, stdout, stderr
            (self.project / "after-check.txt").write_text(
                "moved\n", encoding="utf-8"
            )
            git("add", "after-check.txt")
            git("commit", "-qm", "move after check")
            return CheckExecutionResult(0, 0)

        with patch.object(verify_module, "execute_check", move_head):
            with self.assertRaises(VerifyInputError) as raised:
                run_verify(
                    self.request(
                        mode=VerifyMode.PUBLISH,
                        base_ref=base_sha,
                    )
                )

        error = raised.exception
        self.assertEqual(error.exit_code, 2)
        self.assertIn("checked target or worktree moved", str(error))
        self.assertIn("Publish readiness", error.stdout)
        self.assertIn("Agent publish check", error.stdout)
        self.assertNotIn("verification complete", error.stdout)

    def test_relative_paths_are_anchored_and_public_output_is_terminal_safe(
        self,
    ) -> None:
        project = self.working_directory / "repo\nname-\x85-\u202e"
        profile = self.working_directory / "profile-\x1b]0;title\x07.sh"
        project.mkdir()
        profile.write_text("VERIFY_PROJECT=true\n", encoding="utf-8")
        context = self.context(project=project, profile=profile)
        dangerous = "report-\x1b]0;title\x07-\x85-\u202espoof\n"

        def resolve(requested_project, **kwargs):
            self.assertEqual(requested_project, project)
            self.assertEqual(kwargs["kit_home"], self.kit_home)
            self.assertEqual(kwargs["explicit_profile"], str(profile))
            return context

        with (
            patch.object(verify_module, "resolve_target_project", resolve),
            patch.object(
                verify_module,
                "prepare_check",
                return_value=Mock(name="prepared-check"),
            ),
            patch.object(
                verify_module,
                "render_check_report",
                return_value=dangerous,
            ),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(0, 0),
            ),
        ):
            result = run_verify(
                self.request(
                    requested_project=Path(project.name),
                    kit_home=Path("../kit"),
                    explicit_profile=profile.name,
                    working_directory=self.working_directory,
                    print_only=True,
                )
            )

        self.assertEqual(result.project_root, project)
        self.assertEqual(result.profile_path, str(profile))
        self.assertIn("repo\\nname", result.stdout)
        self.assertIn("\\x1b", result.stdout)
        self.assertIn("\\x07", result.stdout)
        self.assertIn("\\x85", result.stdout)
        self.assertIn("\\u202e", result.stdout)
        self.assertNotIn("\x1b", result.stdout)
        self.assertNotIn("\x07", result.stdout)
        self.assertNotIn("\x85", result.stdout)
        self.assertNotIn("\u202e", result.stdout)
        self.assertTrue(
            all(event.stream is VerifyEventStream.STDOUT for event in result.events)
        )

    def test_facade_uses_python_services_without_shell_or_cli_subprocesses(self) -> None:
        context = self.context()
        prepared = Mock(name="prepared-check")

        with (
            patch.object(verify_module, "resolve_target_project", return_value=context),
            patch.object(verify_module, "prepare_check", return_value=prepared),
            patch.object(
                verify_module,
                "render_check_report",
                return_value="direct-check-service\n",
            ),
            patch.object(
                verify_module,
                "execute_check",
                return_value=CheckExecutionResult(0, 0),
            ),
            patch(
                "subprocess.run",
                side_effect=AssertionError("Verify spawned a Shell/CLI subprocess"),
            ),
        ):
            result = run_verify(self.request(print_only=True))

        self.assertEqual(result.exit_code, 0)
        self.assertIn("direct-check-service", result.stdout)


if __name__ == "__main__":
    unittest.main()
