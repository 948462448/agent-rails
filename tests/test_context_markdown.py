#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))


from agent_rails.context.markdown import (  # noqa: E402
    display_text,
    has_markdown_heading,
    markdown_code,
    markdown_fence,
    valid_utf8,
)


class ContextMarkdownTests(unittest.TestCase):
    def test_display_text_escapes_section_forging_characters(self) -> None:
        self.assertEqual(
            display_text("safe\n\t\u202e\u2028\udcff"),
            r"safe\x0a\x09\u202e\u2028\udcff",
        )

    def test_markdown_code_uses_collision_free_span(self) -> None:
        self.assertEqual(
            markdown_code("path`name\n## forged"),
            r"`` path`name\x0a## forged ``",
        )

    def test_markdown_fence_exceeds_content_runs(self) -> None:
        self.assertEqual(markdown_fence("before ``` after", "`", 3), "````")
        self.assertEqual(markdown_fence("plain", "~", 3), "~~~")

    def test_markdown_fence_rejects_multi_character_delimiter(self) -> None:
        with self.assertRaisesRegex(ValueError, "one character"):
            markdown_fence("text", "``", 3)

    def test_heading_lookup_ignores_headings_inside_code_fences(self) -> None:
        text = (
            "```text\n"
            "### Grill Gate\n"
            "```\n\n"
            "### Real Heading ###\n"
        )

        self.assertFalse(has_markdown_heading(text, 3, "Grill Gate"))
        self.assertTrue(has_markdown_heading(text, 3, "Real Heading"))
        self.assertFalse(has_markdown_heading(text, 2, "Real Heading"))

    def test_heading_lookup_rejects_invalid_levels(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 6"):
            has_markdown_heading("# Heading\n", 0, "Heading")

    def test_valid_utf8_replaces_unpaired_surrogates(self) -> None:
        rendered = valid_utf8("before\udcffafter")
        rendered.encode("utf-8", errors="strict")
        self.assertEqual(rendered, "before?after")


if __name__ == "__main__":
    unittest.main()
