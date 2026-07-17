#!/usr/bin/env python3

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.verification.failure_protocol import (  # noqa: E402
    FailureAction,
    clear_failure_history,
    failure_history_path,
    observe_failure,
)
from agent_rails.verification.repair_pack import VerificationFailure  # noqa: E402


class FailureProtocolTest(unittest.TestCase):
    def failure(
        self,
        *,
        reason: str = "Python tests",
        exit_code: int = 1,
        diagnostic: str = "tests/test_login.py:37: AssertionError: expected session\n",
    ) -> VerificationFailure:
        return VerificationFailure(
            reason=reason,
            exit_code=exit_code,
            completed_steps=0,
            stdout="",
            stderr=diagnostic,
        )

    def test_repeated_failure_changes_strategy_then_escalates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-protocol-") as temp:
            state = Path(temp) / "history.json"

            first = observe_failure(state, "target-a", self.failure())
            second = observe_failure(
                state,
                "target-a",
                self.failure(
                    diagnostic=(
                        "tests/test_login.py:91: AssertionError: expected   session\n"
                    )
                ),
            )
            third = observe_failure(state, "target-a", self.failure())
            fourth = observe_failure(state, "target-a", self.failure())

            self.assertEqual(first.action, FailureAction.REPAIR)
            self.assertEqual(first.consecutive_count, 1)
            self.assertEqual(second.action, FailureAction.CHANGE_STRATEGY)
            self.assertEqual(second.consecutive_count, 2)
            self.assertEqual(third.action, FailureAction.ESCALATE)
            self.assertEqual(third.consecutive_count, 3)
            self.assertEqual(fourth.action, FailureAction.ESCALATE)
            self.assertEqual(fourth.consecutive_count, 3)
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertTrue(first.history_persisted)

    def test_new_failure_or_target_resets_consecutive_count(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-reset-") as temp:
            state = Path(temp) / "history.json"
            observe_failure(state, "target-a", self.failure())
            changed = observe_failure(
                state,
                "target-a",
                self.failure(diagnostic="TypeError: missing request argument\n"),
            )
            new_target = observe_failure(
                state,
                "target-b",
                self.failure(diagnostic="TypeError: missing request argument\n"),
            )

            self.assertEqual(changed.consecutive_count, 1)
            self.assertEqual(new_target.consecutive_count, 1)

    def test_success_clear_makes_the_next_failure_first_again(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-clear-") as temp:
            state = Path(temp) / "history.json"
            observe_failure(state, "target-a", self.failure())
            observe_failure(state, "target-a", self.failure())

            self.assertTrue(clear_failure_history(state))
            result = observe_failure(state, "target-a", self.failure())

            self.assertEqual(result.consecutive_count, 1)
            self.assertEqual(result.action, FailureAction.REPAIR)

    def test_private_state_contains_only_bounded_fingerprints(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-private-") as temp:
            state = Path(temp) / "history.json"
            secret = "unit-test-api-token-private-value"
            result = observe_failure(
                state,
                "target-a",
                self.failure(
                    diagnostic=(
                        f"API_TOKEN={secret}\n"
                        "tests/test_login.py:37: AssertionError: expected session\n"
                    )
                ),
            )

            payload = state.read_text(encoding="utf-8")
            parsed = json.loads(payload)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o600)
            self.assertLessEqual(len(payload), 512)
            self.assertNotIn(secret, payload)
            self.assertNotIn("test_login.py", payload)
            self.assertEqual(parsed["fingerprint"], result.fingerprint)

    def test_unsafe_state_degrades_without_following_symlink(self) -> None:
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks unavailable")
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-link-") as temp:
            root = Path(temp)
            victim = root / "victim.json"
            victim.write_text("keep-me", encoding="utf-8")
            state = root / "history.json"
            state.symlink_to(victim)

            result = observe_failure(state, "target-a", self.failure())

            self.assertFalse(result.history_persisted)
            self.assertEqual(result.consecutive_count, 1)
            self.assertEqual(victim.read_text(encoding="utf-8"), "keep-me")

    def test_history_path_is_user_scoped_and_ignores_untrusted_slug(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-failure-path-") as temp:
            config_home = Path(temp) / "config"
            project = Path(temp) / "target project"
            path = failure_history_path(config_home, project)

            self.assertEqual(path.parent, config_home / "verification-history")
            self.assertTrue(path.name.startswith("failure-"))
            self.assertTrue(path.name.endswith(".json"))
            self.assertNotIn("target project", path.name)


if __name__ == "__main__":
    unittest.main()
