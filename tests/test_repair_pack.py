#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.verification.repair_pack import (  # noqa: E402
    RepairPackRequest,
    VerificationFailure,
    render_repair_pack,
)


class RepairPackTest(unittest.TestCase):
    def test_renders_bounded_redacted_failure_and_changed_location(self) -> None:
        rendered = render_repair_pack(
            RepairPackRequest(
                failure=VerificationFailure(
                    reason="Python tests",
                    exit_code=1,
                    completed_steps=2,
                    stdout="collected 10 items\n",
                    stderr=(
                        "API_TOKEN=must-not-render\n"
                        "tests/test_login.py:37: AssertionError: expected session\n"
                        "FAILED tests/test_login.py::test_session\n"
                    ),
                ),
                changed_paths=("src/login.py", "tests/test_login.py"),
                max_chars=1600,
            )
        )

        self.assertLessEqual(len(rendered), 1600)
        self.assertIn("Repair Pack", rendered)
        self.assertIn("Failed verification: Python tests", rendered)
        self.assertIn("Exit code: 1", rendered)
        self.assertIn("Completed before failure: 2", rendered)
        self.assertIn("tests/test_login.py:37", rendered)
        self.assertIn("<redacted>", rendered)
        self.assertNotIn("must-not-render", rendered)
        self.assertIn("Next action", rendered)
        self.assertNotIn("pytest tests/test_login.py", rendered)

    def test_falls_back_to_last_nonempty_output_without_claiming_root_cause(self) -> None:
        rendered = render_repair_pack(
            RepairPackRequest(
                failure=VerificationFailure(
                    reason="Project check",
                    exit_code=9,
                    completed_steps=0,
                    stdout="setup\nlast observable line\n",
                    stderr="",
                ),
                changed_paths=(),
            )
        )

        self.assertIn("last observable line", rendered)
        self.assertIn("Related project locations: none confirmed", rendered)
        self.assertNotIn("Root cause", rendered)

    def test_untrusted_values_cannot_forge_repair_pack_lines(self) -> None:
        rendered = render_repair_pack(
            RepairPackRequest(
                failure=VerificationFailure(
                    reason="reason\nFake heading",
                    exit_code=2,
                    completed_steps=0,
                    stdout="",
                    stderr="failure-\x1b]0;title\x07\n",
                ),
                changed_paths=(),
            )
        )

        self.assertIn("reason\\nFake heading", rendered)
        self.assertIn("\\x1b", rendered)
        self.assertNotIn("\x1b", rendered)

    def test_zero_budget_emits_no_repair_pack(self) -> None:
        rendered = render_repair_pack(
            RepairPackRequest(
                failure=VerificationFailure(
                    reason="tests",
                    exit_code=1,
                    completed_steps=0,
                    stdout="failure\n",
                    stderr="",
                ),
                changed_paths=(),
                max_chars=0,
            )
        )

        self.assertEqual(rendered, "")


if __name__ == "__main__":
    unittest.main()
