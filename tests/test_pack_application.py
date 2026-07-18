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

from agent_rails.context import pack_application
from agent_rails.context.pack_application import (
    PACK_PROFILE_VARIABLES,
    PackApplicationRequest,
    PackCliOverrides,
    generate_task_pack,
)


class PackApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.com")
        self._git("config", "user.name", "Agent Rails Tests")
        (self.repo / "README.md").write_text("# test\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-qm", "initial")
        self.profile = self.root / "profile"
        self.profile.write_text(
            f'source "{ROOT}/profiles/default.profile"\n'
            'PROJECT_NAME="application-test"\n',
            encoding="utf-8",
        )

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def _request(
        self,
        *,
        output: str,
        requested_project: Path | None = None,
        target_ref: str = "HEAD",
        target_ref_explicit: bool = False,
    ) -> PackApplicationRequest:
        return PackApplicationRequest(
            requested_project=requested_project or self.repo,
            kit_home=ROOT,
            explicit_profile=str(self.profile),
            goal="application service refactor",
            overrides=PackCliOverrides(
                target_ref=target_ref,
                target_ref_explicit=target_ref_explicit,
                output=output,
                context_budget_chars="4000",
                pack_mode="lite",
            ),
            environment={
                "HOME": str(self.root / "home"),
                "PATH": os.environ.get("PATH", ""),
            },
        )

    def test_resolves_nested_project_and_relative_output_without_chdir(self) -> None:
        nested = self.repo / "nested" / "path"
        nested.mkdir(parents=True)

        result = generate_task_pack(
            self._request(output="packs/task-pack.md", requested_project=nested)
        )

        expected = self.repo.resolve() / "packs" / "task-pack.md"
        self.assertEqual(result.project_root, self.repo.resolve())
        self.assertEqual(result.output.display_path, "packs/task-pack.md")
        self.assertEqual(result.output.filesystem_path, expected)
        self.assertTrue(expected.is_file())
        self.assertEqual(expected.stat().st_mode & 0o777, 0o600)
        self.assertIn("## Task Model", expected.read_text(encoding="utf-8"))

    def test_profile_and_environment_file_are_loaded_once(self) -> None:
        profile_count = self.root / "profile-count"
        env_count = self.root / "env-count"
        env_file = self.root / "pack.env"
        self.profile.write_text(
            f'source "{ROOT}/profiles/default.profile"\n'
            f'count=0; [[ ! -f "{profile_count}" ]] || count="$(cat "{profile_count}")"\n'
            f'printf "%s\\n" "$((count + 1))" > "{profile_count}"\n'
            f'AGENT_RAILS_ENV_FILE="{env_file}"\n',
            encoding="utf-8",
        )
        env_file.write_text(
            f'count=0; [[ ! -f "{env_count}" ]] || count="$(cat "{env_count}")"\n'
            f'printf "%s\\n" "$((count + 1))" > "{env_count}"\n'
            'PROJECT_NAME="env-project"\n',
            encoding="utf-8",
        )

        output = self.root / "once.md"
        generate_task_pack(self._request(output=str(output)))

        self.assertEqual(profile_count.read_text(encoding="utf-8"), "1\n")
        self.assertEqual(env_count.read_text(encoding="utf-8"), "1\n")
        self.assertIn("Project: `env-project`", output.read_text(encoding="utf-8"))

    def test_explicit_target_is_frozen_for_downstream_consumers(self) -> None:
        (self.repo / "scripts").mkdir()
        (self.repo / "scripts" / "target.sh").write_text(
            "#!/usr/bin/env bash\n", encoding="utf-8"
        )
        self._git("add", "scripts/target.sh")
        self._git("commit", "-qm", "target")
        target_sha = self._git("rev-parse", "HEAD")
        self._git("branch", "moving-target", target_sha)
        (self.repo / "moved.md").write_text("moved\n", encoding="utf-8")
        self._git("add", "moved.md")
        self._git("commit", "-qm", "moved")
        moved_sha = self._git("rev-parse", "HEAD")
        captured: list[str] = []
        real_collect_docs = pack_application.collect_project_docs
        real_build_plan = pack_application.build_verification_plan

        def collect_after_move(request):
            self._git("update-ref", "refs/heads/moving-target", moved_sha)
            captured.append(request.target_ref)
            return real_collect_docs(request)

        def capture_plan(request):
            captured.append(request.target_ref)
            return real_build_plan(request)

        with patch.object(
            pack_application, "collect_project_docs", side_effect=collect_after_move
        ), patch.object(
            pack_application, "build_verification_plan", side_effect=capture_plan
        ):
            result = generate_task_pack(
                self._request(
                    output=str(self.root / "target.md"),
                    target_ref="moving-target",
                    target_ref_explicit=True,
                )
            )

        self.assertEqual(result.resolved_target_sha, target_sha)
        self.assertEqual(captured, [target_sha, target_sha])

    def test_verification_failure_is_nonfatal(self) -> None:
        output = self.root / "fallback.md"
        with patch.object(
            pack_application,
            "build_verification_plan",
            side_effect=RuntimeError("verification unavailable"),
        ):
            result = generate_task_pack(self._request(output=str(output)))

        self.assertTrue(result.verification_fallback_used)
        self.assertIn(
            "Run agent-rails check after it is available.",
            output.read_text(encoding="utf-8"),
        )

    def test_pack_policy_is_resolved_once_and_profile_is_allowlisted(self) -> None:
        with patch.object(
            pack_application,
            "resolve_pack_policy",
            wraps=pack_application.resolve_pack_policy,
        ) as resolve_policy:
            generate_task_pack(self._request(output=str(self.root / "policy.md")))

        resolve_policy.assert_called_once()
        self.assertNotIn("ACCESS_KEY", PACK_PROFILE_VARIABLES)
        self.assertNotIn("TOKEN", PACK_PROFILE_VARIABLES)
        self.assertNotIn("COOKIE", PACK_PROFILE_VARIABLES)


if __name__ == "__main__":
    unittest.main()
