#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.context.task_model import (  # noqa: E402
    TaskModelRequest,
    build_task_model,
    render_task_model,
)
from agent_rails.evidence.code import (  # noqa: E402
    CodeEvidenceRecord,
    CodeEvidenceRole,
)
from agent_rails.verification.plan import (  # noqa: E402
    VerificationPlan,
    VerificationStep,
)


class TaskModelTest(unittest.TestCase):
    def test_uses_code_evidence_and_verification_as_bounded_plan_inputs(self) -> None:
        model = build_task_model(
            TaskModelRequest(
                goal="Preserve session validation while adding a repair path.",
                changed_paths=("src/session.py", "tests/test_session.py"),
                code_evidence=(
                    CodeEvidenceRecord(
                        path="src/session.py",
                        line=42,
                        symbol="validate_session",
                        role=CodeEvidenceRole.IMPLEMENTATION,
                        score=200,
                        reasons=("goal",),
                    ),
                    CodeEvidenceRecord(
                        path="tests/test_session.py",
                        line=17,
                        symbol="SessionTest",
                        role=CodeEvidenceRole.VERIFICATION,
                        score=190,
                        reasons=("tests",),
                    ),
                ),
                verification=VerificationPlan(
                    steps=(
                        VerificationStep("python changed", "python3 -m unittest"),
                    )
                ),
            )
        )

        rendered = render_task_model(model)

        self.assertIn("## Task Model", rendered)
        self.assertIn("### Behavior Invariants", rendered)
        self.assertIn("### Change Plan", rendered)
        self.assertIn("`src/session.py:42`", rendered)
        self.assertIn("`tests/test_session.py:17`", rendered)
        self.assertIn("`python changed`", rendered)
        self.assertIn("### Acceptance Criteria", rendered)
        self.assertIn("### Do Not Change", rendered)
        self.assertIn("### Open Assumptions", rendered)
        self.assertIn("Code evidence is a candidate location", rendered)

    def test_no_plan_keeps_manual_acceptance_visible_and_goal_safe(self) -> None:
        rendered = render_task_model(
            build_task_model(
                TaskModelRequest(
                    goal="Repair heading injection\n## Forged Section",
                    changed_paths=(),
                    code_evidence=(),
                    verification=VerificationPlan(steps=()),
                )
            )
        )

        self.assertEqual(rendered.count("## Task Model"), 1)
        self.assertIn("No automated verification command was selected", rendered)
        self.assertIn("Product-specific acceptance remains unresolved", rendered)
        self.assertIn("Repair heading injection", rendered)
        self.assertNotIn("\n## Forged Section\n", rendered)


if __name__ == "__main__":
    unittest.main()
