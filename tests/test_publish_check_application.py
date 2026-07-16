#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from typing import Dict, Optional
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.verification.publish_check import (  # noqa: E402
    PublishCheckError,
    PublishCheckCliOverrides,
    PublishCheckInputError,
    PublishCheckRequest,
    prepare_publish_check,
    render_publish_check_report,
    run_publish_check,
)
from agent_rails.config.target_project import resolve_target_project  # noqa: E402
from agent_rails.verification.check_application import (  # noqa: E402
    CHECK_PROFILE_VARIABLES,
)
import agent_rails.verification.publish_check as publish_module  # noqa: E402


def run_git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def init_repo(path: Path, *, branch: str = "main") -> None:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "agent-rails-tests@example.com")
    run_git(path, "config", "user.name", "Agent Rails Tests")
    (path / "README.md").write_text("# base\n", encoding="utf-8")
    run_git(path, "add", "README.md")
    run_git(path, "commit", "-qm", "base")
    run_git(path, "branch", "-M", branch)


def write_profile(path: Path, text: str = "") -> None:
    path.write_text(text, encoding="utf-8")


def make_request(
    project: Path,
    profile: Path,
    *,
    base_ref: Optional[str] = None,
    base_ref_explicit: bool = False,
    target_ref: str = "HEAD",
    target_ref_explicit: bool = False,
    scan_secrets: bool = True,
    environment: Optional[Dict[str, str]] = None,
) -> PublishCheckRequest:
    return PublishCheckRequest(
        requested_project=project,
        kit_home=ROOT,
        explicit_profile=str(profile),
        overrides=PublishCheckCliOverrides(
            base_ref=base_ref,
            base_ref_explicit=base_ref_explicit,
            target_ref=target_ref,
            target_ref_explicit=target_ref_explicit,
            scan_secrets=scan_secrets,
        ),
        environment=dict(os.environ if environment is None else environment),
    )


class PublishCheckApplicationTest(unittest.TestCase):
    def test_pre_resolved_context_skips_profile_resolution_and_freezes_fresh_scope(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-context-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            nested = repo / "nested"
            profile = root / "publish.profile"
            count = root / "profile-count"
            init_repo(repo)
            nested.mkdir()
            write_profile(
                profile,
                f'''count=0
[[ ! -f {shlex.quote(str(count))} ]] || count="$(cat {shlex.quote(str(count))})"
printf "%s\\n" "$((count + 1))" > {shlex.quote(str(count))}
BASE_REF=main
VERIFY_PYTHON='printf "python-context-ok\\n"'
''',
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
                profile_variables=CHECK_PROFILE_VARIABLES,
                capture_profile_environment=True,
            )
            (repo / "fresh.py").write_text("print('fresh')\n", encoding="utf-8")
            request = make_request(
                nested,
                profile,
                scan_secrets=False,
                environment=environment,
            )

            with patch.object(
                publish_module,
                "resolve_target_project",
                side_effect=AssertionError("context must prevent Profile resolution"),
            ) as resolver:
                prepared = prepare_publish_check(request, context=context)
                result = run_publish_check(request, context=context)

            resolver.assert_not_called()
            self.assertEqual(context.root, repo.resolve())
            self.assertEqual(prepared.project_root, repo.resolve())
            self.assertIn("fresh.py", prepared.snapshot.untracked_paths)
            self.assertEqual(
                prepared.verification_plan.steps[0].command,
                'printf "python-context-ok\\n"',
            )
            self.assertEqual(result.prepared.snapshot, prepared.snapshot)
            self.assertEqual(count.read_text(encoding="utf-8"), "1\n")

    def test_pre_resolved_context_rejects_project_profile_and_kit_mismatches(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-context-mismatch-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            other_repo = root / "other-repo"
            profile = root / "publish.profile"
            other_profile = root / "other.profile"
            init_repo(repo)
            init_repo(other_repo)
            write_profile(profile, "BASE_REF=main\n")
            write_profile(other_profile, "BASE_REF=main\n")
            environment = dict(os.environ)
            context = resolve_target_project(
                repo,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
                load_profile=True,
                profile_variables=CHECK_PROFILE_VARIABLES,
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
                    with self.assertRaises(PublishCheckInputError):
                        prepare_publish_check(request, context=context)

    def test_typed_request_rejects_inconsistent_explicit_flags_and_non_git(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-input-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            request = make_request(repo, profile)

            with self.assertRaises(PublishCheckInputError):
                prepare_publish_check(
                    replace(
                        request,
                        overrides=replace(
                            request.overrides,
                            base_ref=None,
                            base_ref_explicit=True,
                        ),
                    )
                )
            with self.assertRaises(PublishCheckInputError):
                prepare_publish_check(
                    replace(
                        request,
                        overrides=replace(
                            request.overrides,
                            target_ref="",
                            target_ref_explicit=True,
                        ),
                    )
                )

            non_git = root / "not-a-repository"
            non_git.mkdir()
            with self.assertRaisesRegex(
                PublishCheckInputError, "publish check requires a git repository"
            ):
                prepare_publish_check(make_request(non_git, profile))

    def test_profile_loads_once_without_environment_file_and_builds_plan(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-profile-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            nested = repo / "nested" / "path"
            profile = root / "publish.profile"
            profile_count = root / "profile-count"
            env_file = root / "publish.env"
            env_marker = root / "env-loaded"
            init_repo(repo)
            nested.mkdir(parents=True)
            (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
            env_file.write_text(
                f"touch {shlex.quote(str(env_marker))}\n", encoding="utf-8"
            )
            write_profile(
                profile,
                f'''count=0
[[ ! -f {shlex.quote(str(profile_count))} ]] || count="$(cat {shlex.quote(str(profile_count))})"
printf "%s\\n" "$((count + 1))" > {shlex.quote(str(profile_count))}
AGENT_RAILS_ENV_FILE={shlex.quote(str(env_file))}
BASE_REF="main"
VERIFY_PYTHON='printf "python-publish-ok\\n"'
''',
            )

            prepared = prepare_publish_check(make_request(nested, profile))

            self.assertEqual(prepared.project_root, repo.resolve())
            self.assertEqual(prepared.profile_path, str(profile))
            self.assertEqual(profile_count.read_text(encoding="utf-8"), "1\n")
            self.assertFalse(env_marker.exists())
            self.assertEqual(prepared.scope.base_ref, "main")
            self.assertIn("app.py", prepared.snapshot.untracked_paths)
            self.assertEqual(len(prepared.verification_plan.steps), 1)
            self.assertEqual(prepared.verification_plan.steps[0].reason, "python changed")
            self.assertEqual(
                prepared.verification_plan.steps[0].command,
                'printf "python-publish-ok\\n"',
            )

    def test_four_layer_scope_and_secret_scan_are_complete_and_redacted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-secrets-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            scripts = repo / "scripts"
            scripts.mkdir()
            script = scripts / "run.sh"
            script.write_text(
                "#!/usr/bin/env bash\n"
                "LEGACY_TOKEN=unit-test-historical-secret-123456\n",
                encoding="utf-8",
            )
            run_git(repo, "add", "scripts/run.sh")
            run_git(repo, "commit", "-qm", "historical fixture")
            run_git(repo, "switch", "-qc", "feature")
            script.write_text(
                script.read_text(encoding="utf-8")
                + "COMMITTED_COOKIE=unit-test-committed-secret-123456\n",
                encoding="utf-8",
            )
            run_git(repo, "add", "scripts/run.sh")
            run_git(repo, "commit", "-qm", "committed fixture")
            script.write_text(
                script.read_text(encoding="utf-8")
                + "DEPLOY_PASSWORD=unit-test-staged-secret-123456\n",
                encoding="utf-8",
            )
            run_git(repo, "add", "scripts/run.sh")
            script.write_text(
                script.read_text(encoding="utf-8")
                + "API_TOKEN=unit-test-unstaged-secret-123456\n",
                encoding="utf-8",
            )
            (repo / ".env.local").write_text(
                "SERVICE_ACCESS_KEY=unit-test-untracked-secret-123456\n",
                encoding="utf-8",
            )
            write_profile(profile)

            prepared = prepare_publish_check(
                make_request(
                    repo,
                    profile,
                    base_ref="main",
                    base_ref_explicit=True,
                )
            )
            report = render_publish_check_report(prepared)
            findings = "\n".join(prepared.secret_scan.findings)

            self.assertEqual(prepared.snapshot.committed_paths, ("scripts/run.sh",))
            self.assertEqual(prepared.snapshot.staged_paths, ("scripts/run.sh",))
            self.assertEqual(prepared.snapshot.unstaged_paths, ("scripts/run.sh",))
            self.assertEqual(prepared.snapshot.untracked_paths, (".env.local",))
            self.assertTrue(prepared.secret_scan.enabled)
            for redacted in (
                "COMMITTED_COOKIE=<redacted>",
                "DEPLOY_PASSWORD=<redacted>",
                "API_TOKEN=<redacted>",
                "SERVICE_ACCESS_KEY=<redacted>",
            ):
                self.assertIn(redacted, findings)
                self.assertIn(redacted, report)
            for raw_secret in (
                "unit-test-historical-secret-123456",
                "unit-test-committed-secret-123456",
                "unit-test-staged-secret-123456",
                "unit-test-unstaged-secret-123456",
                "unit-test-untracked-secret-123456",
            ):
                self.assertNotIn(raw_secret, findings)
                self.assertNotIn(raw_secret, report)
            self.assertNotIn("LEGACY_TOKEN=<redacted>", findings)
            self.assertIn("Staged files (1)", report)
            self.assertIn("Unstaged files (1)", report)
            self.assertIn("Untracked files (1)", report)

    def test_diff_disabled_tracked_file_is_scanned_without_git_text_conversion(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-no-diff-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            (repo / ".gitattributes").write_text(
                "tracked-secret.env -diff\n", encoding="utf-8"
            )
            secret_file = repo / "tracked-secret.env"
            secret_file.write_text("SAFE_VALUE=base\n", encoding="utf-8")
            run_git(repo, "add", ".gitattributes", "tracked-secret.env")
            run_git(repo, "commit", "-qm", "binary diff fixture")
            run_git(repo, "switch", "-qc", "feature")
            raw_secret = "unit-test-no-diff-secret-123456"
            secret_file.write_text(
                f"SAFE_VALUE=base\nTRACKED_API_TOKEN={raw_secret}\n",
                encoding="utf-8",
            )
            run_git(repo, "add", "tracked-secret.env")
            run_git(repo, "commit", "-qm", "tracked secret")
            write_profile(profile)

            prepared = prepare_publish_check(
                make_request(
                    repo,
                    profile,
                    base_ref="main",
                    base_ref_explicit=True,
                )
            )
            report = render_publish_check_report(prepared)
            findings = "\n".join(prepared.secret_scan.findings)

            self.assertIn("tracked-secret.env", prepared.snapshot.committed_paths)
            self.assertIn("TRACKED_API_TOKEN=<redacted>", findings)
            self.assertIn("TRACKED_API_TOKEN=<redacted>", report)
            self.assertNotIn(raw_secret, findings)
            self.assertNotIn(raw_secret, report)

    def test_publish_check_does_not_execute_repository_clean_filter(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-filter-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            filter_script = root / "clean-filter.sh"
            marker = root / "clean-filter-ran"
            init_repo(repo)
            filter_script.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "touch \"$1\"\n"
                "cat\n",
                encoding="utf-8",
            )
            filter_script.chmod(0o755)
            filter_command = (
                f"{shlex.quote(str(filter_script))} {shlex.quote(str(marker))}"
            )
            run_git(repo, "config", "filter.publish-marker.clean", filter_command)
            run_git(repo, "config", "filter.publish-marker.smudge", "cat")
            run_git(repo, "config", "filter.publish-marker.required", "true")
            (repo / ".gitattributes").write_text(
                "filtered.txt filter=publish-marker\n", encoding="utf-8"
            )
            filtered = repo / "filtered.txt"
            filtered.write_text("base\n", encoding="utf-8")
            run_git(repo, "add", ".gitattributes", "filtered.txt")
            run_git(repo, "commit", "-qm", "filter fixture")
            marker.unlink(missing_ok=True)
            filtered.write_text("changed\n", encoding="utf-8")
            write_profile(profile)

            prepare_publish_check(make_request(repo, profile))

            self.assertFalse(
                marker.exists(),
                "publish check must not execute repository clean filters",
            )

    def test_implicit_and_explicit_deployment_baselines_remain_distinct(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-baseline-") as temp_dir:
            root = Path(temp_dir)
            profile = root / "publish.profile"
            write_profile(profile)

            equal_repo = root / "equal"
            init_repo(equal_repo)
            implicit_equal = prepare_publish_check(make_request(equal_repo, profile))
            self.assertTrue(implicit_equal.deployment_delta_unresolved)
            self.assertIn(
                "Deployment delta: UNRESOLVED",
                render_publish_check_report(implicit_equal),
            )

            explicit_equal = prepare_publish_check(
                make_request(
                    equal_repo,
                    profile,
                    base_ref="main",
                    base_ref_explicit=True,
                )
            )
            self.assertFalse(explicit_equal.deployment_delta_unresolved)
            self.assertNotIn(
                "Deployment delta: UNRESOLVED",
                render_publish_check_report(explicit_equal),
            )

            missing_repo = root / "missing"
            init_repo(missing_repo, branch="topic")
            implicit_missing = prepare_publish_check(make_request(missing_repo, profile))
            self.assertEqual(implicit_missing.scope.base_ref, "")
            self.assertTrue(implicit_missing.deployment_delta_unresolved)

    def test_missing_baseline_marks_secret_scan_incomplete_instead_of_clean(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-incomplete-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo, branch="topic")
            write_profile(profile)

            prepared = prepare_publish_check(make_request(repo, profile))
            report = render_publish_check_report(prepared)
            secret_section = report.split("\nSecret scan:\n", 1)[1].split(
                "\nSuggested verification:\n", 1
            )[0]

            self.assertEqual(prepared.scope.base_ref, "")
            self.assertTrue(prepared.deployment_delta_unresolved)
            self.assertIn("INCOMPLETE", secret_section)
            self.assertIn("baseline", secret_section.casefold())
            self.assertNotIn(
                "No likely secrets found in changed text files", secret_section
            )

    def test_no_secret_scan_does_not_open_an_unreadable_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-no-scan-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            unreadable = repo / "unreadable.env"
            unreadable.write_text(
                "API_TOKEN=unit-test-no-scan-secret-123456\n", encoding="utf-8"
            )
            unreadable.chmod(0)
            try:
                request = make_request(repo, profile, scan_secrets=False)
                prepared = prepare_publish_check(request)
                report = render_publish_check_report(prepared)
                result = run_publish_check(request)
            finally:
                unreadable.chmod(0o600)

            self.assertFalse(prepared.secret_scan.enabled)
            self.assertEqual(prepared.secret_scan.findings, ())
            self.assertIn("Disabled by --no-secret-scan", report)
            self.assertNotIn("unit-test-no-scan-secret-123456", report)
            self.assertEqual(result.exit_code, 0)
            self.assertEqual(result.prepared, prepared)
            stdout_events = [
                event for event in result.events if event.stream.value == "stdout"
            ]
            self.assertTrue(stdout_events)
            self.assertEqual("".join(event.text for event in stdout_events), report)

    def test_unreadable_untracked_file_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-unreadable-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            unreadable = repo / "unreadable.env"
            unreadable.write_text(
                "API_TOKEN=unit-test-unreadable-secret-123456\n", encoding="utf-8"
            )
            unreadable.chmod(0)
            try:
                if os.access(unreadable, os.R_OK):
                    self.skipTest("current user can read mode-000 files")
                with self.assertRaisesRegex(
                    PublishCheckError,
                    "Unable to inspect untracked file for sensitive output: unreadable.env",
                ) as raised:
                    prepare_publish_check(make_request(repo, profile))
                self.assertNotIsInstance(raised.exception, PublishCheckInputError)
                self.assertNotIn(
                    "unit-test-unreadable-secret-123456", str(raised.exception)
                )
            finally:
                unreadable.chmod(0o600)

    def test_untracked_symlink_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-symlink-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            outside = root / "outside.env"
            init_repo(repo)
            write_profile(profile)
            outside.write_text(
                "OUTSIDE_API_TOKEN=unit-test-outside-secret-123456\n",
                encoding="utf-8",
            )
            (repo / "leak.env").symlink_to(outside)

            prepared = prepare_publish_check(make_request(repo, profile))
            report = render_publish_check_report(prepared)
            findings = "\n".join(prepared.secret_scan.findings)

            self.assertIn("leak.env", prepared.snapshot.untracked_paths)
            self.assertNotIn("OUTSIDE_API_TOKEN=<redacted>", findings)
            self.assertNotIn("unit-test-outside-secret-123456", findings)
            self.assertNotIn("unit-test-outside-secret-123456", report)

    def test_untracked_symlink_target_text_is_redacted_without_following_target(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-link-text-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            link_secret = "unit-test-link-target-secret-123456"
            followed_secret = "unit-test-followed-content-secret-123456"
            target_name = f"API_TOKEN={link_secret}"
            init_repo(repo)
            write_profile(profile)
            target = repo / target_name
            target.write_text(
                f"FOLLOWED_PASSWORD={followed_secret}\n", encoding="utf-8"
            )
            (repo / ".git/info/exclude").write_text(
                f"/{target_name}\n", encoding="utf-8"
            )
            (repo / "linked.env").symlink_to(target_name)

            prepared = prepare_publish_check(make_request(repo, profile))
            report = render_publish_check_report(prepared)
            findings = "\n".join(prepared.secret_scan.findings)

            self.assertEqual(prepared.snapshot.untracked_paths, ("linked.env",))
            self.assertIn("API_TOKEN=<redacted>", findings)
            self.assertNotIn(link_secret, findings)
            self.assertNotIn(link_secret, report)
            self.assertNotIn("FOLLOWED_PASSWORD=<redacted>", findings)
            self.assertNotIn(followed_secret, findings)
            self.assertNotIn(followed_secret, report)

    def test_c1_paths_and_verification_commands_are_terminal_escaped(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-terminal-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            c1_path = "c1-\u0085-path.txt"
            command = 'printf "safe"\u009b]0;forged\nFORGED_VERIFICATION_LINE'
            init_repo(repo)
            (repo / c1_path).write_text("changed\n", encoding="utf-8")
            write_profile(
                profile,
                f"VERIFY_PROJECT={shlex.quote(command)}\n",
            )

            prepared = prepare_publish_check(make_request(repo, profile))
            report = render_publish_check_report(prepared)

            self.assertIn("c1-\\x85-path.txt", report)
            self.assertIn(
                'printf "safe"\\x9b]0;forged\\nFORGED_VERIFICATION_LINE',
                report,
            )
            self.assertNotIn("\u0085", report)
            self.assertNotIn("\u009b", report)
            self.assertNotIn("\nFORGED_VERIFICATION_LINE", report)

    def test_git_diff_io_failure_is_runtime_error_not_input_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-io-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            real_run_git = publish_module.run_git

            def fail_diff(project, arguments, *, environment=None):
                if arguments and arguments[0] == "diff":
                    raise OSError("unit-test publish diff I/O failure")
                return real_run_git(project, arguments, environment=environment)

            with patch.object(publish_module, "run_git", side_effect=fail_diff):
                with self.assertRaisesRegex(
                    PublishCheckError,
                    "Unable to inspect .* publish diff for sensitive output",
                ) as raised:
                    prepare_publish_check(make_request(repo, profile))

            self.assertNotIsInstance(raised.exception, PublishCheckInputError)
            self.assertNotIn("unit-test publish diff I/O failure", str(raised.exception))

    def test_worktree_fingerprint_drift_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-drift-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            (repo / "local.txt").write_text("changed\n", encoding="utf-8")
            calls = 0

            def drifting_fingerprint(*_args, **_kwargs):
                nonlocal calls
                calls += 1
                return "before" if calls == 1 else "after"

            with patch.object(
                publish_module,
                "fingerprint_git_worktree",
                side_effect=drifting_fingerprint,
                create=True,
            ):
                with self.assertRaises(PublishCheckError) as raised:
                    prepare_publish_check(make_request(repo, profile))

            self.assertGreaterEqual(calls, 2)
            self.assertNotIsInstance(raised.exception, PublishCheckInputError)
            self.assertRegex(
                str(raised.exception).casefold(), "changed|moved|drift"
            )

    def test_remote_credentials_are_sanitized_before_entering_result_or_report(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-publish-remote-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            profile = root / "publish.profile"
            init_repo(repo)
            write_profile(profile)
            run_git(
                repo,
                "remote",
                "add",
                "origin",
                "https://unit-test-user:unit-test-password@example.invalid/owner/repo.git?access_token=unit-test-query-secret",
            )

            prepared = prepare_publish_check(
                make_request(repo, profile, scan_secrets=False)
            )
            report = render_publish_check_report(prepared)

            self.assertEqual(
                prepared.repository.origin_url,
                "https://<redacted>@example.invalid/owner/repo.git",
            )
            self.assertIn(
                "Origin: https://<redacted>@example.invalid/owner/repo.git", report
            )
            for secret in (
                "unit-test-user",
                "unit-test-password",
                "unit-test-query-secret",
                "access_token=",
            ):
                self.assertNotIn(secret, prepared.repository.origin_url)
                self.assertNotIn(secret, report)


if __name__ == "__main__":
    unittest.main()
