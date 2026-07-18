#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.verification.plan import (
    VerificationCommands,
    VerificationPlanError,
    VerificationPlanRequest,
    VerificationStep,
    build_verification_plan,
    render_suggestions,
    write_verification_plan_bundle,
)


class VerificationPlanTest(unittest.TestCase):
    def request(
        self,
        project: Path,
        changed_paths: tuple[str, ...],
        commands: VerificationCommands,
        *,
        target_ref: str = "HEAD",
        explicit: bool = False,
    ) -> VerificationPlanRequest:
        return VerificationPlanRequest(
            project=project,
            changed_paths=changed_paths,
            commands=commands,
            target_ref=target_ref,
            target_ref_explicit=explicit,
        )

    def git(self, repo: Path, *arguments: str) -> str:
        env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            env.pop(name, None)
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def make_repo(self, root: Path, name: str) -> Path:
        repo = root / name
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Agent Rails Test")
        self.git(repo, "config", "user.email", "agent-rails@example.invalid")
        return repo

    def test_matchers_have_stable_order_and_first_reason_wins_dedup(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-plan-") as temp_dir:
            project = Path(temp_dir)
            plan = build_verification_plan(
                self.request(
                    project,
                    (
                        "dolphin/job.py",
                        "Cargo.toml",
                        "service/main.go",
                        "pom.xml",
                        "frontend/view.tsx",
                        "runtime/runner.txt",
                        "backend/service.py",
                        "contracts/schema.json",
                    ),
                    VerificationCommands(
                        contracts="shared-check",
                        backend="shared-check",
                        runtime="runtime-check",
                        frontend="frontend-check",
                        node="node-check",
                        python="python-check",
                        java="java-check",
                        go="go-check",
                        rust="rust-check",
                        dolphin="dolphin-check",
                    ),
                )
            )

            self.assertEqual(
                tuple(step.reason for step in plan.steps),
                (
                    "contracts changed",
                    "runtime changed",
                    "frontend changed",
                    "node/js changed",
                    "python changed",
                    "java/jvm changed",
                    "go changed",
                    "rust changed",
                    "dolphin python changed",
                ),
            )
            self.assertEqual(plan.steps[0].command, "shared-check")

    def test_clean_task_scope_uses_non_changed_reason_and_kotlin_dsl(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-task-plan-") as temp_dir:
            project = Path(temp_dir)
            plan = build_verification_plan(
                VerificationPlanRequest(
                    project=project,
                    changed_paths=("app/build.gradle.kts",),
                    commands=VerificationCommands(java="./gradlew test"),
                    path_label="task scope",
                )
            )

            self.assertEqual(
                plan.steps,
                (VerificationStep("java/jvm task scope", "./gradlew test"),),
            )

    def test_test_override_and_fixed_suite_order(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-tests-plan-") as temp_dir:
            project = Path(temp_dir)
            paths = (
                "tests/suites/context.sh",
                "tests/suites/core.sh",
                "tests/suites/adapters.sh",
            )
            plan = build_verification_plan(
                self.request(project, paths, VerificationCommands())
            )
            self.assertEqual(
                plan.steps,
                (
                    VerificationStep(
                        "shell tests changed",
                        "bash tests/run.sh core adapters context",
                    ),
                ),
            )

            opaque = "printf 'one\\ttwo\\n'; printf '%s' \"$HOME\""
            overridden = build_verification_plan(
                self.request(
                    project,
                    paths,
                    VerificationCommands(tests=opaque),
                )
            )
            self.assertEqual(overridden.steps[0].command, opaque)

            unmapped = build_verification_plan(
                self.request(
                    project,
                    ("tests/custom-check.sh", "tests/suites/core.sh"),
                    VerificationCommands(),
                )
            )
            self.assertEqual(unmapped.steps[0].command, "bash tests/run.sh")

    def test_shell_paths_are_quoted_and_checked_in_selected_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-shell-plan-") as temp_dir:
            root = Path(temp_dir)
            target = self.make_repo(root, "target")
            sibling = self.make_repo(root, "sibling")
            (target / "scripts").mkdir()
            shell_path = "scripts/check `touch injected` $(touch also-injected)\nline.sh"
            (target / shell_path).write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
            outside_script = root / "outside-script.sh"
            outside_script.write_text("#!/usr/bin/env bash\ntrue\n", encoding="utf-8")
            (target / "scripts" / "link.sh").symlink_to(outside_script)
            self.git(target, "add", "-A")
            self.git(target, "commit", "-qm", "shell target")
            target_sha = self.git(target, "rev-parse", "HEAD").strip()
            (target / shell_path).unlink()
            (sibling / "README.md").write_text("sibling\n", encoding="utf-8")
            self.git(sibling, "add", "-A")
            self.git(sibling, "commit", "-qm", "sibling")

            with patch.dict(
                os.environ,
                {"GIT_DIR": str(sibling / ".git"), "GIT_WORK_TREE": str(sibling)},
            ):
                plan = build_verification_plan(
                    self.request(
                        target,
                        (shell_path, "scripts/link.sh"),
                        VerificationCommands(),
                        target_ref=target_sha,
                        explicit=True,
                    )
                )

            self.assertEqual(plan.steps[0].reason, "shell entrypoints changed")
            self.assertNotIn("\n", plan.steps[0].command)
            self.assertNotIn("link.sh", plan.steps[0].command)
            run = subprocess.run(
                ["bash", "-lc", plan.steps[0].command],
                cwd=target,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(run.returncode, 0)
            self.assertFalse((target / "injected").exists())
            self.assertFalse((target / "also-injected").exists())

            working_tree_plan = build_verification_plan(
                self.request(
                    target,
                    (
                        shell_path,
                        "scripts/link.sh",
                        "scripts/../../outside-script.sh",
                    ),
                    VerificationCommands(),
                )
            )
            self.assertEqual(working_tree_plan.steps, ())

    def test_shell_override_is_opaque_and_does_not_append_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-shell-override-") as temp_dir:
            project = Path(temp_dir)
            (project / "scripts").mkdir()
            (project / "scripts" / "check.sh").write_text("true\n", encoding="utf-8")
            command = "custom-check --literal='tabs\\tand $dollars'"
            plan = build_verification_plan(
                self.request(
                    project,
                    ("scripts/check.sh",),
                    VerificationCommands(shell=command),
                )
            )
            self.assertEqual(plan.steps[0].command, command)

    def test_docs_only_empty_plan_and_project_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-docs-plan-") as temp_dir:
            project = Path(temp_dir)
            docs = build_verification_plan(
                self.request(
                    project,
                    ("README.md",),
                    VerificationCommands(),
                )
            )
            empty = build_verification_plan(
                self.request(
                    project,
                    (),
                    VerificationCommands(project="project-check"),
                )
            )
            fallback = build_verification_plan(
                self.request(
                    project,
                    ("README.md",),
                    VerificationCommands(project="project-check"),
                )
            )

            expected = (
                "- No automated command selected. For docs-only changes, manually "
                "review rendered Markdown and links.\n"
            )
            self.assertEqual(render_suggestions(docs), expected)
            self.assertEqual(render_suggestions(empty), expected)
            self.assertEqual(
                fallback.steps,
                (VerificationStep("project default", "project-check"),),
            )

    def test_bundle_preserves_commands_as_nul_delimited_opaque_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-plan-bundle-") as temp_dir:
            output = Path(temp_dir) / "bundle"
            command = "printf 'left\\tright\nnext'; printf '%s' '$literal'"
            plan = build_verification_plan(
                self.request(
                    Path(temp_dir),
                    ("service.py",),
                    VerificationCommands(python=command),
                )
            )
            write_verification_plan_bundle(output, plan)

            self.assertEqual(
                (output / "steps0").read_bytes(),
                b"python changed\0" + command.encode("utf-8") + b"\0",
            )
            self.assertEqual(
                (output / "suggestions.md").read_text(encoding="utf-8"),
                f"- [python changed] {command}\n",
            )

            with self.assertRaisesRegex(VerificationPlanError, "NUL byte"):
                build_verification_plan(
                    self.request(
                        Path(temp_dir),
                        ("service.py",),
                        VerificationCommands(python="bad\0command"),
                    )
                )

            invalid_text_command = "printf invalid-\udcff-byte"
            invalid_plan = build_verification_plan(
                self.request(
                    Path(temp_dir),
                    ("service.py",),
                    VerificationCommands(python=invalid_text_command),
                )
            )
            invalid_output = Path(temp_dir) / "invalid-bundle"
            write_verification_plan_bundle(invalid_output, invalid_plan)
            suggestions = (invalid_output / "suggestions.md").read_bytes()
            suggestions.decode("utf-8")
            self.assertIn(b"\\udcff", suggestions)
            self.assertEqual(
                (invalid_output / "steps0").read_bytes(),
                b"python changed\0printf invalid-\xff-byte\0",
            )


if __name__ == "__main__":
    unittest.main()
