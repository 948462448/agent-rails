#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.core.private_text import (
    PrivateTextArtifact,
    PrivateTextNonRegularError,
    PrivateTextPublishError,
    PrivateTextTargetExistsError,
    publish_private_text_batch,
)


class PrivateTextPublisherTest(unittest.TestCase):
    def test_replace_and_create_only_publish_private_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-private-text-") as temp_dir:
            root = Path(temp_dir)
            decision = root / "decision.md"
            local = root / "memory" / "card.md"
            decision.write_text("old\n", encoding="utf-8")

            published = publish_private_text_batch(
                (
                    PrivateTextArtifact("decision", decision, "new decision\n"),
                    PrivateTextArtifact(
                        "local", local, "local card\n", create_only=True
                    ),
                )
            )

            self.assertEqual([item.key for item in published], ["decision", "local"])
            self.assertEqual(decision.read_text(encoding="utf-8"), "new decision\n")
            self.assertEqual(local.read_text(encoding="utf-8"), "local card\n")
            self.assertEqual(stat.S_IMODE(decision.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(local.stat().st_mode), 0o600)

    def test_predictable_create_conflict_preserves_other_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-private-text-") as temp_dir:
            root = Path(temp_dir)
            decision = root / "decision.md"
            local = root / "card.md"
            decision.write_text("old decision\n", encoding="utf-8")
            local.write_text("old card\n", encoding="utf-8")

            with self.assertRaises(PrivateTextTargetExistsError):
                publish_private_text_batch(
                    (
                        PrivateTextArtifact("decision", decision, "new decision\n"),
                        PrivateTextArtifact(
                            "local", local, "new card\n", create_only=True
                        ),
                    )
                )

            self.assertEqual(decision.read_text(encoding="utf-8"), "old decision\n")
            self.assertEqual(local.read_text(encoding="utf-8"), "old card\n")

    def test_duplicate_and_non_regular_targets_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-private-text-") as temp_dir:
            root = Path(temp_dir)
            target = root / "same.md"
            with self.assertRaisesRegex(PrivateTextPublishError, "overlap"):
                publish_private_text_batch(
                    (
                        PrivateTextArtifact("first", target, "first\n"),
                        PrivateTextArtifact("second", target, "second\n"),
                    )
                )
            real = root / "real.md"
            real.write_text("keep\n", encoding="utf-8")
            target.symlink_to(real)
            with self.assertRaises(PrivateTextNonRegularError):
                publish_private_text_batch(
                    (PrivateTextArtifact("link", target, "replace\n"),)
                )
            self.assertEqual(real.read_text(encoding="utf-8"), "keep\n")

    def test_stage_failure_preserves_every_old_target_and_cleans_staging(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-private-text-") as temp_dir:
            root = Path(temp_dir)
            first = root / "first.md"
            second = root / "second.md"
            first.write_text("old first\n", encoding="utf-8")
            second.write_text("old second\n", encoding="utf-8")
            real_mkstemp = tempfile.mkstemp
            calls = 0

            def fail_second_stage(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("stage failed")
                return real_mkstemp(*args, **kwargs)

            with patch(
                "agent_rails.core.private_text.tempfile.mkstemp",
                side_effect=fail_second_stage,
            ):
                with self.assertRaises(PrivateTextPublishError):
                    publish_private_text_batch(
                        (
                            PrivateTextArtifact("first", first, "new first\n"),
                            PrivateTextArtifact("second", second, "new second\n"),
                        )
                    )

            self.assertEqual(first.read_text(encoding="utf-8"), "old first\n")
            self.assertEqual(second.read_text(encoding="utf-8"), "old second\n")
            self.assertEqual(tuple(root.glob(".*.agent-rails.*")), ())

    def test_commit_failure_reports_published_prefix(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-private-text-") as temp_dir:
            root = Path(temp_dir)
            first = root / "first.md"
            second = root / "second.md"
            real_replace = os.replace
            calls = 0

            def fail_second_replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("replace failed")
                return real_replace(source, target)

            with patch(
                "agent_rails.core.private_text.os.replace",
                side_effect=fail_second_replace,
            ):
                with self.assertRaises(PrivateTextPublishError) as raised:
                    publish_private_text_batch(
                        (
                            PrivateTextArtifact("first", first, "first\n"),
                            PrivateTextArtifact("second", second, "second\n"),
                        )
                    )

            self.assertEqual([item.key for item in raised.exception.published], ["first"])
            self.assertEqual(first.read_text(encoding="utf-8"), "first\n")
            self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
