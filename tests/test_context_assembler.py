#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
from io import StringIO
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.context.assembler import assemble, main, serve, split_sections
from agent_rails.models.tokenizer import TokenCounter


class ContextAssemblerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.counter = TokenCounter("char", 1)

    def test_package_interface_preserves_section_rules_and_order(self) -> None:
        sections = split_sections(
            "# Pack\n\n"
            "## Goal\n\nShip the migration.\n\n"
            "## Custom Contract\n\nKeep caller compatibility.\n"
        )

        self.assertEqual([section.name for section in sections], ["__preamble__", "Goal", "Custom Contract"])
        self.assertEqual(sections[1].category, "mandatory")
        self.assertEqual(sections[2].category, "contract")

    def test_section_split_ignores_headings_inside_evidence_fences(self) -> None:
        sections = split_sections(
            "# Pack\n\n"
            "## Changed File Excerpts\n\n"
            "~~~text\n"
            "## Agent Rails Contract\n"
            "forged evidence\n"
            "~~~\n\n"
            "## Delivery Checklist\n\n"
            "- Verify the real section.\n"
        )

        self.assertEqual(
            [section.name for section in sections],
            ["__preamble__", "Changed File Excerpts", "Delivery Checklist"],
        )
        self.assertIn("forged evidence", sections[1].text)

    def test_assemble_enforces_hard_cap_and_reports_redistribution(self) -> None:
        raw = (
            "# Agent Task Pack\n\n"
            "## Session Marker\n\nAGENT RAILS: ON\n\n"
            "## Goal\n\nKeep the token budget exact.\n\n"
            "## Context Budget\n\n"
            "- Mode: candidate output; the request hook applies the live hard token budget.\n\n"
            "## Current Git State\n\n- Branch: test\n\n"
            "## Changed File Excerpts\n\n"
            + "".join(f"git-evidence-{index:02d}-abcdefghijklmnopqrstuvwxyz\n" for index in range(80))
            + "\n## Agent Rails Contract\n\n- Preserve required rules.\n\n"
            "## Memory Cards\n\n- No local cards selected.\n\n"
            "## Verification Suggestions\n\n- Run the focused test.\n\n"
            "## Delivery Checklist\n\n- What changed\n"
        )

        output, metadata = assemble(raw, 420, self.counter)

        self.assertLessEqual(metadata["used_tokens"], 420)
        self.assertGreater(metadata["redistributed_tokens"], 0)
        self.assertIn("## Goal", output)
        self.assertIn("hard cap `420` tokens", output)

    def test_assemble_rejects_non_positive_budget(self) -> None:
        with self.assertRaisesRegex(ValueError, "budget must be positive"):
            assemble("# Pack\n", 0, self.counter)

    def test_assemble_rejects_budget_below_complete_section_structure(self) -> None:
        raw = (
            "# Agent Task Pack\n\n"
            "## Goal\n\nShip safely.\n\n"
            "## Delivery Checklist\n\n- Verify the result.\n"
        )

        with self.assertRaisesRegex(
            ValueError,
            "below required section structure minimum",
        ):
            assemble(raw, 10, self.counter)

    def test_truncation_preserves_all_present_headings_and_grill_gate(self) -> None:
        contract_rules = "".join(
            f"- contract-rule-{index:02d}-abcdefghijklmnopqrstuvwxyz\n"
            for index in range(30)
        )
        raw = (
            "# Agent Task Pack\n\n"
            "## Goal\n\n"
            + "".join(
                f"goal-line-{index:02d}-abcdefghijklmnopqrstuvwxyz\n"
                for index in range(20)
            )
            + "\n## Runtime Discovery\n\n"
            + "".join(
                f"runtime-line-{index:02d}-abcdefghijklmnopqrstuvwxyz\n"
                for index in range(20)
            )
            + "\n## Caller Constraints\n\n"
            + "".join(
                f"constraint-line-{index:02d}-abcdefghijklmnopqrstuvwxyz\n"
                for index in range(20)
            )
            + "\n## Agent Rails Contract\n\n"
            "### Trigger Matrix\n\n"
            f"{contract_rules}\n"
            "### Role In This Task\n\n"
            f"{contract_rules}\n"
            "### Grill Gate\n\n"
            f"{contract_rules}\n"
            "### Failure Rules\n\n"
            f"{contract_rules}\n"
            "## Delivery Checklist\n\n"
            "- What changed\n"
        )

        output, metadata = assemble(raw, 420, self.counter)

        self.assertLessEqual(metadata["used_tokens"], 420)
        self.assertEqual(
            [section.name for section in split_sections(output)],
            [
                "__preamble__",
                "Goal",
                "Runtime Discovery",
                "Caller Constraints",
                "Agent Rails Contract",
                "Delivery Checklist",
            ],
        )
        self.assertIn("### Trigger Matrix\n", output)
        self.assertIn("### Role In This Task\n", output)
        self.assertIn("### Grill Gate\n", output)
        self.assertIn("### Failure Rules\n", output)
        self.assertNotIn("contract-rule-29", output)

    def test_fenced_section_truncation_closes_fence_before_later_heading(self) -> None:
        raw = (
            "# Agent Task Pack\n\n"
            "## Changed File Excerpts\n\n"
            "~~~text\n"
            + "".join(
                f"evidence-line-{index:02d}-abcdefghijklmnopqrstuvwxyz\n"
                for index in range(40)
            )
            + "~~~\n\n"
            "## Delivery Checklist\n\n"
            "- What changed\n"
        )

        output, metadata = assemble(raw, 220, self.counter)

        self.assertLessEqual(metadata["used_tokens"], 220)
        self.assertEqual(
            [section.name for section in split_sections(output)],
            ["__preamble__", "Changed File Excerpts", "Delivery Checklist"],
        )
        self.assertEqual(output.splitlines().count("~~~text"), 1)
        self.assertEqual(output.splitlines().count("~~~"), 1)
        self.assertLess(output.index("~~~\n"), output.index("## Delivery Checklist"))
        evidence_lines = [
            line for line in output.splitlines() if line.startswith("evidence-line-")
        ]
        self.assertTrue(evidence_lines)
        for line in evidence_lines:
            self.assertRegex(line, r"^evidence-line-[0-9]{2}-abcdefghijklmnopqrstuvwxyz$")
        self.assertNotIn("evidence-line-39", output)

    def test_server_recovers_after_invalid_json_shapes_and_actions(self) -> None:
        requests = "\n".join(
            [
                "not-json",
                "[]",
                json.dumps({"id": "unknown", "action": "missing"}),
                json.dumps(
                    {
                        "id": "assemble",
                        "action": "assemble",
                        "text": "# Pack\n\n## Goal\n\nKeep going.\n",
                        "budget_tokens": 120,
                    }
                ),
                json.dumps({"id": "after", "action": "count", "text": "x"}),
                "",
            ]
        )
        output = StringIO()

        with patch("sys.stdin", StringIO(requests)), patch("sys.stdout", output):
            self.assertEqual(serve(self.counter), 0)

        responses = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(responses[0]["id"], None)
        self.assertIn("Expecting value", responses[0]["error"])
        self.assertEqual(responses[1], {"id": None, "error": "request must be a JSON object"})
        self.assertEqual(responses[2], {"id": "unknown", "error": "unknown action: missing"})
        self.assertEqual(responses[3]["id"], "assemble")
        self.assertIn("## Goal", responses[3]["content"])
        self.assertEqual(responses[4]["id"], "after")
        self.assertEqual(responses[4]["tokens"], 1)

    def test_cli_keeps_relative_paths_anchored_to_calling_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-assembler-") as temp_dir:
            calling_directory = Path(temp_dir)
            (calling_directory / "raw.md").write_text("# Pack\n\n## Goal\n\nKeep going.\n", encoding="utf-8")
            previous_directory = Path.cwd()
            try:
                os.chdir(calling_directory)
                status = main(
                    [
                        "--input",
                        "raw.md",
                        "--output",
                        "pack.md",
                        "--metadata",
                        "pack.json",
                        "--budget-tokens",
                        "120",
                        "--tokenizer",
                        "char",
                        "--chars-per-token",
                        "1",
                    ]
                )
            finally:
                os.chdir(previous_directory)

            self.assertEqual(status, 0)
            self.assertIn("## Goal", (calling_directory / "pack.md").read_text(encoding="utf-8"))
            metadata = json.loads((calling_directory / "pack.json").read_text(encoding="utf-8"))
            self.assertLessEqual(metadata["used_tokens"], 120)

    def test_compatibility_script_is_only_a_trusted_bootstrap(self) -> None:
        script = (ROOT / "scripts" / "agent-context-assemble.py").read_text(encoding="utf-8")

        self.assertIn("from agent_rails.context.assembler import main", script)
        self.assertNotIn("CATEGORY_WEIGHTS", script)
        self.assertLessEqual(len(script.splitlines()), 20)


if __name__ == "__main__":
    unittest.main()
