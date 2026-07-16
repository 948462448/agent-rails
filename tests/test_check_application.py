#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import io
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from typing import Dict, Optional
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.verification.check_application import (
    CheckApplicationError,
    CheckApplicationRequest,
    CheckCliOverrides,
    CheckInputError,
    CheckMode,
    PreparedCheck,
    execute_check,
    prepare_check,
    render_check_report,
)
from agent_rails.config.target_project import resolve_target_project
import agent_rails.verification.check_application as check_module
from agent_rails.verification.plan import (
    VerificationCommands,
    VerificationPlan,
    VerificationStep,
)


def run_git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def init_repo(path: Path) -> None:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "agent-rails-tests@example.com")
    run_git(path, "config", "user.name", "Agent Rails Tests")
    (path / "README.md").write_text("# base\n", encoding="utf-8")
    run_git(path, "add", "README.md")
    run_git(path, "commit", "-qm", "base")
    run_git(path, "branch", "-M", "main")


def make_request(
    project: Path,
    profile: Path,
    *,
    mode: CheckMode = CheckMode.PREVIEW,
    base_ref: Optional[str] = None,
    target_ref: str = "HEAD",
    target_ref_explicit: bool = False,
    environment: Optional[Dict[str, str]] = None,
) -> CheckApplicationRequest:
    return CheckApplicationRequest(
        requested_project=project,
        kit_home=ROOT,
        explicit_profile=str(profile),
        overrides=CheckCliOverrides(
            base_ref=base_ref,
            target_ref=target_ref,
            target_ref_explicit=target_ref_explicit,
            mode=mode,
        ),
        environment=dict(os.environ if environment is None else environment),
    )


class CheckApplicationTest(unittest.TestCase):
    def test_pre_resolved_context_skips_profile_resolution_and_shares_environment(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-context-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            nested = repo / "nested"
            profile = root / "check.profile"
            count = root / "profile-count"
            init_repo(repo)
            nested.mkdir()
            (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
            profile.write_text(
                f'''count=0
[[ ! -f "{count}" ]] || count="$(cat "{count}")"
printf "%s\\n" "$((count + 1))" > "{count}"
export CHECK_RUNTIME_TOKEN=profile-value
BASE_REF=main
VERIFY_PYTHON='test "$CHECK_RUNTIME_TOKEN" = profile-value'
''',
                encoding="utf-8",
            )
            environment = dict(os.environ)
            context = resolve_target_project(
                nested,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
                load_profile=True,
                load_environment_file=False,
                profile_variables=check_module.CHECK_PROFILE_VARIABLES,
                capture_profile_environment=True,
            )
            request = make_request(
                nested,
                profile,
                mode=CheckMode.RUN,
                environment=environment,
            )

            with patch.object(
                check_module,
                "resolve_target_project",
                side_effect=AssertionError("context must prevent Profile resolution"),
            ) as resolver:
                prepared = prepare_check(request, context=context)

            result = execute_check(
                prepared, stdout=io.StringIO(), stderr=io.StringIO()
            )

            resolver.assert_not_called()
            self.assertEqual(context.root, repo.resolve())
            self.assertEqual(prepared.project_root, repo.resolve())
            self.assertEqual(count.read_text(encoding="utf-8"), "1\n")
            self.assertEqual(
                prepared.environment["CHECK_RUNTIME_TOKEN"], "profile-value"
            )
            self.assertEqual(result.exit_code, 0)

            fallback = prepare_check(
                replace(
                    request,
                    overrides=replace(request.overrides, mode=CheckMode.PREVIEW),
                ),
                context=replace(context, profile_environment={}),
            )
            self.assertEqual(fallback.environment, environment)

    def test_pre_resolved_context_rejects_project_profile_and_kit_mismatches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-context-mismatch-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            other_repo = root / "other-repo"
            profile = root / "check.profile"
            other_profile = root / "other.profile"
            init_repo(repo)
            init_repo(other_repo)
            profile.write_text("BASE_REF=main\n", encoding="utf-8")
            other_profile.write_text("BASE_REF=main\n", encoding="utf-8")
            environment = dict(os.environ)
            context = resolve_target_project(
                repo,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
                load_profile=True,
                profile_variables=check_module.CHECK_PROFILE_VARIABLES,
                capture_profile_environment=True,
            )

            mismatches = (
                make_request(other_repo, profile, environment=environment),
                make_request(repo, other_profile, environment=environment),
                replace(
                    make_request(repo, profile, environment=environment),
                    kit_home=root / "other-kit",
                ),
            )
            for request in mismatches:
                with self.subTest(request=request):
                    with self.assertRaises(CheckInputError):
                        prepare_check(request, context=context)

    def test_profile_loads_once_without_environment_file_and_implicit_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-profile-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            count = root / "profile-count"
            env_file = root / "check.env"
            env_marker = root / "env-loaded"
            init_repo(repo)
            (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
            profile.write_text(
                f'''count=0
[[ ! -f "{count}" ]] || count="$(cat "{count}")"
printf "%s\\n" "$((count + 1))" > "{count}"
AGENT_RAILS_ENV_FILE="{env_file}"
BASE_REF="main"
VERIFY_PYTHON='printf "python-ok\\n"'
''',
                encoding="utf-8",
            )
            env_file.write_text(f'touch "{env_marker}"\n', encoding="utf-8")

            prepared = prepare_check(make_request(repo, profile))

            self.assertEqual(count.read_text(encoding="utf-8"), "1\n")
            self.assertFalse(env_marker.exists())
            self.assertEqual(prepared.resolved_base_ref, "main")
            self.assertEqual(prepared.changed_paths, ("app.py",))
            self.assertEqual(
                prepared.plan.steps,
                (VerificationStep("python changed", 'printf "python-ok\\n"'),),
            )

    def test_explicit_target_uses_frozen_tree_and_excludes_worktree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-target-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            base_sha = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "switch", "-qc", "target")
            (repo / "target.py").write_text("print('target')\n", encoding="utf-8")
            run_git(repo, "add", "target.py")
            run_git(repo, "commit", "-qm", "target")
            target_sha = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "switch", "-q", "main")
            (repo / "worktree.py").write_text("print('worktree')\n", encoding="utf-8")
            profile.write_text("VERIFY_PYTHON='python -m compileall .'\n", encoding="utf-8")

            prepared = prepare_check(
                make_request(
                    repo,
                    profile,
                    base_ref=base_sha,
                    target_ref=target_sha,
                    target_ref_explicit=True,
                )
            )

            self.assertEqual(prepared.target_sha, target_sha)
            self.assertEqual(prepared.changed_paths, ("target.py",))
            self.assertNotIn("worktree.py", prepared.changed_paths)
            with self.assertRaises(CheckInputError):
                prepare_check(
                    make_request(
                        repo,
                        profile,
                        mode=CheckMode.RUN,
                        base_ref=base_sha,
                        target_ref=target_sha,
                        target_ref_explicit=True,
                    )
                )

    def test_explicit_target_run_rejects_dirty_checkout_at_same_head(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-dirty-target-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            base_sha = run_git(repo, "rev-parse", "HEAD")
            (repo / "app.py").write_text("print('target')\n", encoding="utf-8")
            run_git(repo, "add", "app.py")
            run_git(repo, "commit", "-qm", "target")
            target_sha = run_git(repo, "rev-parse", "HEAD")
            (repo / "app.py").write_text("print('dirty')\n", encoding="utf-8")
            profile.write_text("VERIFY_PYTHON='python app.py'\n", encoding="utf-8")

            with self.assertRaisesRegex(CheckInputError, "checkout has"):
                prepare_check(
                    make_request(
                        repo,
                        profile,
                        mode=CheckMode.RUN,
                        base_ref=base_sha,
                        target_ref=target_sha,
                        target_ref_explicit=True,
                    )
                )

    def test_explicit_target_run_rechecks_cleanliness_after_fingerprinting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-clean-race-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            target_sha = run_git(repo, "rev-parse", "HEAD")
            profile.write_text("VERIFY_PROJECT='true'\n", encoding="utf-8")

            from agent_rails.verification import check_application as application

            original_fingerprint = application.fingerprint_git_worktree

            def fingerprint_then_dirty(*args, **kwargs):
                fingerprint = original_fingerprint(*args, **kwargs)
                (repo / "raced.py").write_text("print('raced')\n", encoding="utf-8")
                return fingerprint

            with patch.object(
                application,
                "fingerprint_git_worktree",
                side_effect=fingerprint_then_dirty,
            ):
                with self.assertRaisesRegex(CheckInputError, "checkout has"):
                    prepare_check(
                        make_request(
                            repo,
                            profile,
                            mode=CheckMode.RUN,
                            base_ref=target_sha,
                            target_ref=target_sha,
                            target_ref_explicit=True,
                        )
                    )

    def test_run_rejects_index_entries_hidden_from_worktree_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-hidden-index-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "hidden.py").write_text("print('base')\n", encoding="utf-8")
            run_git(repo, "add", "hidden.py")
            run_git(repo, "commit", "-qm", "hidden fixture")
            target_sha = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "update-index", "--assume-unchanged", "hidden.py")
            (repo / "hidden.py").write_text("print('dirty')\n", encoding="utf-8")
            profile.write_text("VERIFY_PYTHON='python hidden.py'\n", encoding="utf-8")

            with self.assertRaisesRegex(CheckInputError, "assume-unchanged"):
                prepare_check(
                    make_request(
                        repo,
                        profile,
                        mode=CheckMode.RUN,
                        base_ref=target_sha,
                        target_ref=target_sha,
                        target_ref_explicit=True,
                    )
                )

    def test_run_allows_missing_skip_worktree_paths_used_by_sparse_checkout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-sparse-index-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "outside.txt").write_text("outside\n", encoding="utf-8")
            run_git(repo, "add", "outside.txt")
            run_git(repo, "commit", "-qm", "sparse fixture")
            run_git(repo, "update-index", "--skip-worktree", "outside.txt")
            (repo / "outside.txt").unlink()
            (repo / "visible.py").write_text("print('visible')\n", encoding="utf-8")
            profile.write_text("VERIFY_PYTHON='true'\n", encoding="utf-8")

            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )
            result = execute_check(
                prepared, stdout=io.StringIO(), stderr=io.StringIO()
            )

            self.assertEqual(result.exit_code, 0)

    def test_non_git_report_and_explicit_target_rejection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-non-git-") as temp_dir:
            root = Path(temp_dir)
            project = root / "project"
            project.mkdir()
            profile = root / "check.profile"
            profile.write_text("VERIFY_PROJECT='true'\n", encoding="utf-8")

            prepared = prepare_check(make_request(project, profile))
            report = render_check_report(prepared)

            self.assertIn("Mode: no git repository detected", report)
            self.assertIn("- None detected.", report)
            self.assertIn("No automated command selected", report)
            with self.assertRaisesRegex(CheckInputError, "requires a git repository"):
                prepare_check(
                    make_request(
                        project,
                        profile,
                        target_ref="main",
                        target_ref_explicit=True,
                    )
                )

    def test_report_modes_marker_and_terminal_data_are_stable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-report-") as temp_dir:
            root = Path(temp_dir)
            prepared = self._prepared(root)

            report = render_check_report(
                replace(prepared, changed_paths=("line\nbreak.py",))
            )
            self.assertTrue(report.startswith("AGENT RAILS: CHECK-ONLY"))
            self.assertIn("- line\\nbreak.py\n", report)
            self.assertIn("Next action suggestions:", report)
            suppressed = render_check_report(replace(prepared, suppress_marker=True))
            self.assertTrue(suppressed.startswith("Agent check\n"))
            suggestions = render_check_report(
                replace(prepared, mode=CheckMode.SUGGESTIONS_ONLY)
            )
            self.assertEqual(suggestions, "- [first] printf first\n- [second] false\n")

    def test_runner_uses_opaque_argv_canonical_cwd_and_isolated_git_env(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-runner-") as temp_dir:
            root = Path(temp_dir)
            runner = root / "custom-shell"
            runner.write_text(
                "#!/bin/sh\n"
                "if [ \"$2\" = false ]; then exit 7; fi\n"
                "exec /bin/sh \"$@\"\n",
                encoding="utf-8",
            )
            runner.chmod(0o755)
            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_DIR": "/wrong/.git",
                    "GIT_WORK_TREE": "/wrong",
                    "AGENT_RAILS_RUN_SHELL": str(runner),
                }
            )
            prepared = self._prepared(root, environment=environment)
            stdout = io.StringIO()
            stderr = io.StringIO()
            calls = []
            real_popen = subprocess.Popen

            def observing_popen(arguments, **kwargs):
                calls.append((arguments, kwargs))
                return real_popen(arguments, **kwargs)

            with patch(
                "agent_rails.verification.check_application.subprocess.Popen",
                side_effect=observing_popen,
            ):
                result = execute_check(prepared, stdout=stdout, stderr=stderr)

            self.assertEqual(result.exit_code, 7)
            self.assertEqual(result.completed_steps, 1)
            self.assertEqual(calls[0][0], [str(runner), "-lc", "printf first"])
            self.assertEqual(calls[1][0], [str(runner), "-lc", "false"])
            self.assertEqual(calls[0][1]["cwd"], root)
            self.assertEqual(calls[0][1]["env"]["PWD"], str(root))
            self.assertNotIn("GIT_DIR", calls[0][1]["env"])
            self.assertNotIn("GIT_WORK_TREE", calls[0][1]["env"])
            self.assertIn(">>> first\nprintf first", stdout.getvalue())
            self.assertEqual(stderr.getvalue(), "")

    def test_custom_writer_streams_real_child_stdout_after_heading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-stdout-") as temp_dir:
            root = Path(temp_dir)
            command = "printf 'stdout-payload\\n'"
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("stdout", command),)
                ),
            )
            stdout = io.StringIO()

            result = execute_check(
                prepared, stdout=stdout, stderr=io.StringIO()
            )

            self.assertEqual(result.exit_code, 0)
            heading = f">>> stdout\n{command}\n"
            self.assertIn(heading, stdout.getvalue())
            self.assertTrue(stdout.getvalue().endswith("stdout-payload\n"))
            self.assertLess(
                stdout.getvalue().index(heading),
                stdout.getvalue().index("stdout-payload\n"),
            )

    def test_custom_writer_streams_real_child_stderr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-stderr-") as temp_dir:
            root = Path(temp_dir)
            command = "printf 'stderr-payload\\n' >&2"
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("stderr", command),)
                ),
            )
            stderr = io.StringIO()

            result = execute_check(
                prepared, stdout=io.StringIO(), stderr=stderr
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(stderr.getvalue(), "stderr-payload\n")

    def test_nonzero_real_child_preserves_partial_stdout_and_stderr(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-partial-") as temp_dir:
            root = Path(temp_dir)
            command = (
                "printf partial-output; "
                "printf partial-error >&2; "
                "exit 9"
            )
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("partial", command),)
                ),
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            result = execute_check(
                prepared, stdout=stdout, stderr=stderr
            )

            self.assertEqual(result.exit_code, 9)
            self.assertEqual(result.completed_steps, 0)
            self.assertTrue(stdout.getvalue().endswith("partial-output"))
            self.assertEqual(stderr.getvalue(), "partial-error")

    def test_custom_writer_preserves_real_child_signal_exit_semantics(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-signal-") as temp_dir:
            root = Path(temp_dir)
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("signal", "kill -TERM $$"),)
                ),
            )

            result = execute_check(
                prepared, stdout=io.StringIO(), stderr=io.StringIO()
            )

            self.assertEqual(result.exit_code, 143)
            self.assertEqual(result.completed_steps, 0)

    def test_custom_writer_escapes_child_control_and_non_utf8_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-bytes-") as temp_dir:
            root = Path(temp_dir)
            command = "printf 'safe\\033[31m\\377\\n'"
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("bytes", command),)
                ),
            )
            stdout = io.StringIO()

            result = execute_check(
                prepared, stdout=stdout, stderr=io.StringIO()
            )

            self.assertEqual(result.exit_code, 0)
            self.assertTrue(
                stdout.getvalue().endswith("safe\\x1b[31m\\xff\n")
            )
            self.assertNotIn("\x1b", stdout.getvalue())

    def test_custom_writers_stream_large_dual_output_without_loss(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-large-") as temp_dir:
            root = Path(temp_dir)
            payload_size = 200_000
            source = (
                "import os; "
                f"os.write(1, b'O' * {payload_size}); "
                f"os.write(2, b'E' * {payload_size})"
            )
            command = (
                f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"
            )
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("large", command),)
                ),
            )
            stdout = io.StringIO()
            stderr = io.StringIO()

            result = execute_check(
                prepared, stdout=stdout, stderr=stderr
            )

            self.assertEqual(result.exit_code, 0)
            child_stdout = stdout.getvalue().rsplit(f"{command}\n", 1)[1]
            self.assertEqual(child_stdout, "O" * payload_size)
            self.assertEqual(stderr.getvalue(), "E" * payload_size)

    def test_custom_writer_failure_stops_execution_fail_closed(self) -> None:
        class FailingWriter(io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.remaining_writes = 2

            def write(self, text: str) -> int:
                if self.remaining_writes == 0:
                    raise OSError("writer unavailable")
                self.remaining_writes -= 1
                return super().write(text)

        with tempfile.TemporaryDirectory(prefix="agent-rails-check-writer-") as temp_dir:
            root = Path(temp_dir)
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(
                        VerificationStep(
                            "writer", "printf child-output; sleep 5"
                        ),
                    )
                ),
            )

            with self.assertRaisesRegex(CheckApplicationError, "output"):
                execute_check(
                    prepared,
                    stdout=FailingWriter(),
                    stderr=io.StringIO(),
                )

    def test_writer_failure_cleans_background_group_after_shell_exits(self) -> None:
        class FailingWriter(io.StringIO):
            def __init__(self) -> None:
                super().__init__()
                self.remaining_writes = 2

            def write(self, text: str) -> int:
                if self.remaining_writes == 0:
                    raise OSError("writer unavailable")
                self.remaining_writes -= 1
                return super().write(text)

        with tempfile.TemporaryDirectory(prefix="agent-rails-check-group-") as temp_dir:
            root = Path(temp_dir)
            command = (
                "(trap '' TERM; sleep 0.2; printf child-output; sleep 30) & "
                "exit 0"
            )
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("background", command),)
                ),
            )
            processes: list[subprocess.Popen[bytes]] = []
            real_popen = subprocess.Popen

            def observing_popen(arguments, **kwargs):
                process = real_popen(arguments, **kwargs)
                processes.append(process)
                return process

            try:
                with (
                    patch.object(check_module.subprocess, "Popen", side_effect=observing_popen),
                    self.assertRaisesRegex(CheckApplicationError, "output"),
                ):
                    execute_check(
                        prepared,
                        stdout=FailingWriter(),
                        stderr=io.StringIO(),
                    )
                self.assertEqual(len(processes), 1)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    try:
                        os.killpg(processes[0].pid, 0)
                    except ProcessLookupError:
                        break
                    time.sleep(0.01)
                else:
                    self.fail("verification child process group survived cleanup")
            finally:
                if processes:
                    try:
                        os.killpg(processes[0].pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_child_stream_process_error_stops_execution_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-process-") as temp_dir:
            root = Path(temp_dir)
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("process", "sleep 5"),)
                ),
            )

            with (
                patch.object(
                    check_module,
                    "_stream_child_output",
                    side_effect=OSError("stream unavailable"),
                ),
                self.assertRaisesRegex(CheckApplicationError, "stream"),
            ):
                execute_check(
                    prepared,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )

    def test_omitted_writers_preserve_inherited_subprocess_runner(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-inherit-") as temp_dir:
            root = Path(temp_dir)
            prepared = replace(
                self._prepared(root),
                plan=VerificationPlan(
                    steps=(VerificationStep("inherit", "true"),)
                ),
            )
            with (
                patch.object(check_module.sys, "stdout", io.StringIO()),
                patch.object(check_module.sys, "stderr", io.StringIO()),
                patch.object(
                    check_module.subprocess,
                    "run",
                    return_value=subprocess.CompletedProcess([], 0),
                ) as run_process,
            ):
                result = execute_check(prepared)

            self.assertEqual(result.exit_code, 0)
            run_process.assert_called_once()

    def test_runner_failure_exit_mapping_and_empty_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-exit-") as temp_dir:
            root = Path(temp_dir)
            prepared = self._prepared(root)
            for failure, expected in ((FileNotFoundError(), 127), (PermissionError(), 126)):
                with self.subTest(expected=expected):
                    with patch(
                        "agent_rails.verification.check_application.subprocess.Popen",
                        side_effect=failure,
                    ):
                        result = execute_check(
                            prepared, stdout=io.StringIO(), stderr=io.StringIO()
                        )
                    self.assertEqual(result.exit_code, expected)
            with patch(
                "agent_rails.verification.check_application.subprocess.Popen"
            ) as popen_process:
                result = execute_check(
                    replace(prepared, plan=VerificationPlan(steps=())),
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            self.assertEqual(result.exit_code, 0)
            popen_process.assert_not_called()

    def test_empty_run_plan_still_rejects_post_planning_scope_drift(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-empty-drift-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            profile.write_text("# no verification commands\n", encoding="utf-8")
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )
            self.assertEqual(prepared.plan.steps, ())
            (repo / "late.py").write_text("print('late')\n", encoding="utf-8")

            with self.assertRaisesRegex(CheckInputError, "moved after planning"):
                execute_check(
                    prepared, stdout=io.StringIO(), stderr=io.StringIO()
                )

    def test_implicit_run_rejects_scope_drift_without_reloading_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-drift-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            profile.write_text("VERIFY_PYTHON='true'\n", encoding="utf-8")
            (repo / "first.py").write_text("print('first')\n", encoding="utf-8")
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )
            (repo / "second.py").write_text("print('second')\n", encoding="utf-8")

            with self.assertRaisesRegex(CheckInputError, "moved after planning"):
                execute_check(
                    prepared, stdout=io.StringIO(), stderr=io.StringIO()
                )

    def test_run_rejects_worktree_content_created_by_a_successful_step(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-command-drift-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "first.py").write_text("print('first')\n", encoding="utf-8")
            profile.write_text(
                "VERIFY_PYTHON='printf generated > generated.js'\n",
                encoding="utf-8",
            )
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )

            with self.assertRaisesRegex(CheckInputError, "working tree content moved"):
                execute_check(
                    prepared, stdout=io.StringIO(), stderr=io.StringIO()
                )

    def test_run_rejects_head_moved_by_the_last_successful_step(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-command-head-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "first.py").write_text("print('first')\n", encoding="utf-8")
            profile.write_text(
                "VERIFY_PYTHON='git commit --allow-empty -qm moved'\n",
                encoding="utf-8",
            )
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )

            with self.assertRaisesRegex(CheckInputError, "checkout is at HEAD"):
                execute_check(
                    prepared, stdout=io.StringIO(), stderr=io.StringIO()
                )

    def test_run_fingerprints_once_initially_and_after_each_step(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-fingerprint-count-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "first.py").write_text("print('first')\n", encoding="utf-8")
            (repo / "entry.js").write_text("export default true;\n", encoding="utf-8")
            profile.write_text(
                "VERIFY_PYTHON='true'\nVERIFY_NODE=':'\n",
                encoding="utf-8",
            )
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )
            self.assertEqual(len(prepared.plan.steps), 2)

            with patch(
                "agent_rails.verification.check_application.fingerprint_git_worktree",
                return_value=prepared.worktree_fingerprint,
            ) as fingerprint:
                result = execute_check(
                    prepared, stdout=io.StringIO(), stderr=io.StringIO()
                )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(fingerprint.call_count, 3)

    def test_profile_exported_environment_reaches_verification_commands(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-check-profile-env-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "check.profile"
            init_repo(repo)
            (repo / "first.py").write_text("print('first')\n", encoding="utf-8")
            profile.write_text(
                "export CHECK_RUNTIME_TOKEN=profile-value\n"
                "profile_check() { test \"$CHECK_RUNTIME_TOKEN\" = profile-value; }\n"
                "export -f profile_check\n"
                "VERIFY_PYTHON=profile_check\n",
                encoding="utf-8",
            )
            prepared = prepare_check(
                make_request(repo, profile, mode=CheckMode.RUN)
            )

            result = execute_check(
                prepared, stdout=io.StringIO(), stderr=io.StringIO()
            )

            self.assertEqual(result.exit_code, 0)
            self.assertEqual(prepared.environment["CHECK_RUNTIME_TOKEN"], "profile-value")
            self.assertIn("BASH_FUNC_profile_check%%", prepared.environment)

    def _prepared(
        self,
        root: Path,
        *,
        environment: Optional[Dict[str, str]] = None,
    ) -> PreparedCheck:
        plan = VerificationPlan(
            steps=(
                VerificationStep("first", "printf first"),
                VerificationStep("second", "false"),
            )
        )
        return PreparedCheck(
            project_root=root,
            profile_path="/profiles/test.profile",
            is_git_repo=False,
            requested_target_ref="HEAD",
            target_ref_explicit=False,
            target_sha=None,
            head_sha=None,
            resolved_base_ref="main",
            merge_base="abc1234567890",
            changed_paths=("app.py",),
            commands=VerificationCommands(python="true"),
            plan=plan,
            scope=None,
            mode=CheckMode.RUN,
            suppress_marker=False,
            runner_shell=(environment or {}).get("AGENT_RAILS_RUN_SHELL", "bash"),
            environment=dict(os.environ if environment is None else environment),
            worktree_fingerprint=None,
        )


if __name__ == "__main__":
    unittest.main()
