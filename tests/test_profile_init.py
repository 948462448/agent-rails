#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.config.profile_init import (
    ProfileAlreadyExistsError,
    ProfileInitPlan,
    VerificationCommands,
    build_profile_init_plan,
    detect_verification_commands,
    render_profile,
    write_profile,
)


class ProfileInitPlanTest(unittest.TestCase):
    def test_nested_git_project_uses_canonical_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-profile-init-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            nested = repo / "nested" / "path"
            nested.mkdir(parents=True)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)

            plan = build_profile_init_plan(
                nested,
                kit_home=ROOT,
                scope="project",
                environment={"HOME": str(temp / "home")},
            )

            self.assertEqual(plan.project_root, repo.resolve())
            self.assertEqual(plan.profile_name, "repo")
            self.assertEqual(
                plan.output_path,
                str(repo.resolve() / ".agent-rails" / "profile"),
            )

    def test_verification_detection_uses_structured_node_scripts(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-profile-detect-") as temp_dir:
            project = Path(temp_dir)
            (project / "package.json").write_text(
                '{"dependencies":{"lint":"1"},"scripts":{"test":"node --test"}}',
                encoding="utf-8",
            )
            (project / "tests").mkdir()
            (project / "tests" / "run.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            commands = detect_verification_commands(project)

            self.assertEqual(commands.node, "npm test")
            self.assertEqual(commands.python, "")


class ProfileRenderTest(unittest.TestCase):
    def test_generated_shell_treats_explicit_name_as_literal_data(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-profile-shell-") as temp_dir:
            temp = Path(temp_dir)
            marker = temp / "must-not-exist"
            name = f'literal "quote" \\ $(touch {marker}) `touch {marker}`'
            profile = temp / "profile"
            profile.write_text(
                render_profile(
                    project_root=temp,
                    profile_name=name,
                    entry_doc="AGENTS.md",
                    commands=VerificationCommands(),
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["AGENT_RAILS_HOME"] = str(ROOT)
            completed = subprocess.run(
                [
                    "/bin/bash",
                    "-c",
                    'source "$1"; printf "%s\\0%s" "$PROJECT_NAME" "$MEMORY_LOCAL_DIR"',
                    "agent-rails-profile-test",
                    str(profile),
                ],
                env=environment,
                stdout=subprocess.PIPE,
                check=True,
            )
            project_name, memory_dir = completed.stdout.decode("utf-8").split("\0")
            self.assertEqual(project_name, name)
            self.assertTrue(memory_dir.endswith(f"/memory/{name}"))
            self.assertFalse(marker.exists())


class ProfileWriteTest(unittest.TestCase):
    def test_write_is_private_and_force_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-profile-write-") as temp_dir:
            output = Path(temp_dir) / "profiles" / "demo.profile"
            first = ProfileInitPlan(
                project_root=Path(temp_dir),
                profile_name="demo",
                scope="user",
                output_path=str(output),
                content="first\n",
            )
            second = ProfileInitPlan(
                project_root=Path(temp_dir),
                profile_name="demo",
                scope="user",
                output_path=str(output),
                content="second\n",
            )

            write_profile(first)
            self.assertEqual(output.read_text(encoding="utf-8"), "first\n")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
            with self.assertRaises(ProfileAlreadyExistsError):
                write_profile(second)
            self.assertEqual(output.read_text(encoding="utf-8"), "first\n")

            write_profile(second, force=True)
            self.assertEqual(output.read_text(encoding="utf-8"), "second\n")
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
