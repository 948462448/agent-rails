#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.memory.candidate import (  # noqa: E402
    MemoryCandidateError,
    MemoryCandidateRequest,
    memory_candidate_path,
    publish_memory_candidate,
)
from agent_rails.verification.plan import VerificationPlan, VerificationStep  # noqa: E402


class MemoryCandidateTest(unittest.TestCase):
    def request(self, config_home: Path) -> MemoryCandidateRequest:
        return MemoryCandidateRequest(
            config_home=config_home,
            project_root=Path("/tmp/project with space"),
            project_name="project",
            target_sha="a" * 40,
            failure_fingerprint="b" * 64,
            failure_count=2,
            changed_paths=("src/session.py", "tests/test_session.py"),
            verification=VerificationPlan(
                steps=(
                    VerificationStep("python changed", "secret command omitted"),
                )
            ),
            completed_steps=1,
        )

    def test_publishes_private_candidate_without_command_or_memory_card(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-memory-candidate-") as temp:
            config_home = Path(temp) / "config"
            result = publish_memory_candidate(self.request(config_home))
            content = result.path.read_text(encoding="utf-8")

            self.assertEqual(result.path, memory_candidate_path(config_home, Path("/tmp/project with space")))
            self.assertEqual(stat.S_IMODE(result.path.stat().st_mode), 0o600)
            self.assertIn("# Agent Rails Memory Candidate", content)
            self.assertIn("`python changed`", content)
            self.assertNotIn("secret command omitted", content)
            self.assertIn("No local memory card was written", content)
            self.assertIn("Root cause is not inferred", content)

    def test_rejects_secret_bearing_or_invalid_candidate_inputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-memory-candidate-") as temp:
            request = self.request(Path(temp) / "config")
            with self.assertRaisesRegex(MemoryCandidateError, "target SHA"):
                publish_memory_candidate(
                    MemoryCandidateRequest(
                        **{**request.__dict__, "target_sha": "not-a-sha"}
                    )
                )
            with self.assertRaisesRegex(MemoryCandidateError, "sensitive-output"):
                publish_memory_candidate(
                    MemoryCandidateRequest(
                        **{
                            **request.__dict__,
                            "changed_paths": ("API_TOKEN=private-value",),
                        }
                    )
                )


if __name__ == "__main__":
    unittest.main()
