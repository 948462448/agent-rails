#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.memory.suggestion import (
    MEMORY_SUGGEST_PROFILE_VARIABLES,
    MemoryDecision,
    MemoryStaleness,
    MemorySuggestionInputError,
    MemorySuggestionRequest,
    memory_slugify,
    suggest_memory,
)


class MemorySuggestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / "repo"
        self.caller = self.root / "caller"
        self.repo.mkdir()
        self.caller.mkdir()
        self._git("init", "-q")
        self._git("config", "user.email", "tests@example.com")
        self._git("config", "user.name", "Agent Rails Tests")
        (self.repo / "README.md").write_text("# memory\n", encoding="utf-8")
        self._git("add", "README.md")
        self._git("commit", "-qm", "initial")
        self.profile = self.root / "profile"
        self.profile.write_text(
            f'source "{ROOT}/profiles/default.profile"\n'
            'PROJECT_NAME="memory-test"\n'
            'MEMORY_LOCAL_DIR="memory-cards"\n',
            encoding="utf-8",
        )

    def _git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def _request(self, **changes) -> MemorySuggestionRequest:
        request = MemorySuggestionRequest(
            requested_project=self.repo,
            invocation_cwd=self.caller,
            kit_home=ROOT,
            explicit_profile=str(self.profile),
            output="decisions/result.md",
            decision=MemoryDecision.KEEP,
            write_local=False,
            force=False,
            memory_id=None,
            title=None,
            triggers=(),
            applies_to=(),
            verify="",
            caution="",
            reason="",
            staleness=MemoryStaleness.VERIFY_FIRST,
            notes="",
            environment={
                "HOME": str(self.root / "home"),
                "PATH": os.environ.get("PATH", ""),
            },
        )
        return replace(request, **changes)

    def test_skip_writes_private_decision_at_invocation_relative_path(self) -> None:
        nested = self.repo / "nested" / "path"
        nested.mkdir(parents=True)

        result = suggest_memory(
            self._request(
                requested_project=nested,
                decision=MemoryDecision.SKIP,
                reason="one-off output",
            )
        )

        output = self.caller.resolve() / "decisions" / "result.md"
        self.assertEqual(result.requested_project_path, nested.resolve())
        self.assertEqual(result.project_root, self.repo.resolve())
        self.assertEqual(result.decision_target.filesystem_path, output)
        self.assertEqual(output.stat().st_mode & 0o777, 0o600)
        content = output.read_text(encoding="utf-8")
        self.assertIn("Decision: `skip`", content)
        self.assertIn(f"Project path: `{nested.resolve()}`", content)
        self.assertIn("one-off output", content)
        self.assertFalse((self.caller / "memory-cards").exists())

    def test_local_card_reuses_candidate_and_derives_scope_from_worktree(self) -> None:
        source = self.repo / "backend" / "auth file.py"
        source.parent.mkdir()
        source.write_text("print('changed')\n", encoding="utf-8")

        result = suggest_memory(
            self._request(
                write_local=True,
                title="Backend auth probe",
                notes="Use the readiness probe before reading handlers.",
            )
        )

        local = self.caller.resolve() / "memory-cards" / "backend-auth-probe.md"
        decision = result.decision_target.filesystem_path.read_text(encoding="utf-8")
        card = local.read_text(encoding="utf-8")
        self.assertEqual(result.memory_id, "backend-auth-probe")
        self.assertEqual(result.triggers, ("backend", "auth", "probe"))
        self.assertEqual(result.applies_to, ("backend",))
        self.assertIn(card, decision)
        self.assertEqual(local.stat().st_mode & 0o777, 0o600)

    def test_profile_executes_once_and_environment_file_is_not_loaded(self) -> None:
        profile_count = self.root / "profile-count"
        env_marker = self.root / "env-marker"
        config = self.repo / "config"
        config.mkdir()
        (config / "helper.profile").write_text(
            'PROJECT_NAME="helper-name"\n', encoding="utf-8"
        )
        (config / "memory.env").write_text(
            f': > "{env_marker}"\nPROJECT_NAME="must-not-win"\n',
            encoding="utf-8",
        )
        self.profile.write_text(
            f'source "{ROOT}/profiles/default.profile"\n'
            f'count=0; [[ ! -f "{profile_count}" ]] || count="$(cat "{profile_count}")"\n'
            f'printf "%s\\n" "$((count + 1))" > "{profile_count}"\n'
            'source "config/helper.profile"\n'
            'AGENT_RAILS_ENV_FILE="config/memory.env"\n'
            'PROJECT_NAME="profile-name"\n'
            'MEMORY_LOCAL_DIR="memory-cards"\n',
            encoding="utf-8",
        )

        result = suggest_memory(self._request())

        self.assertEqual(profile_count.read_text(encoding="utf-8"), "1\n")
        # The helper source proves Profile cwd. The env-file Adapter itself
        # stays disabled for Memory Suggest, so its name cannot override.
        self.assertFalse(env_marker.exists())
        self.assertEqual(result.project_name, "profile-name")

    def test_rejects_noncanonical_explicit_ids(self) -> None:
        for memory_id in ("", "../escape", "Uppercase", "has space", "a" * 81):
            with self.subTest(memory_id=memory_id):
                with self.assertRaisesRegex(MemorySuggestionInputError, "canonical"):
                    suggest_memory(self._request(memory_id=memory_id))
        self.assertEqual(memory_slugify("Backend  Auth / Probe"), "backend-auth-probe")

    def test_write_local_constraints_fail_before_any_write(self) -> None:
        output = self.caller / "decisions" / "result.md"
        with self.assertRaisesRegex(MemorySuggestionInputError, "decision skip"):
            suggest_memory(
                self._request(
                    decision=MemoryDecision.SKIP,
                    write_local=True,
                    notes="reusable",
                )
            )
        with self.assertRaisesRegex(MemorySuggestionInputError, "without curated"):
            suggest_memory(self._request(write_local=True, title="Missing notes"))
        self.assertFalse(output.exists())

    def test_existing_local_card_preserves_old_decision_without_force(self) -> None:
        decision = self.caller / "decisions" / "result.md"
        local = self.caller / "memory-cards" / "existing.md"
        decision.parent.mkdir()
        local.parent.mkdir()
        decision.write_text("old decision\n", encoding="utf-8")
        local.write_text("old card\n", encoding="utf-8")

        with self.assertRaisesRegex(MemorySuggestionInputError, "already exists"):
            suggest_memory(
                self._request(
                    memory_id="existing",
                    title="Existing",
                    write_local=True,
                    notes="new card",
                )
            )

        self.assertEqual(decision.read_text(encoding="utf-8"), "old decision\n")
        self.assertEqual(local.read_text(encoding="utf-8"), "old card\n")

    def test_rejects_overlapping_targets_and_secret_bearing_content(self) -> None:
        local_path = "memory-cards/overlap.md"
        with self.assertRaisesRegex(MemorySuggestionInputError, "must be different"):
            suggest_memory(
                self._request(
                    output=local_path,
                    memory_id="overlap",
                    title="Overlap",
                    write_local=True,
                    notes="safe reusable rule",
                )
            )
        with self.assertRaisesRegex(MemorySuggestionInputError, "secret-bearing"):
            suggest_memory(
                self._request(
                    memory_id="secret",
                    title="Secret",
                    write_local=True,
                    notes="API_TOKEN=unit-test-secret-value",
                )
            )
        with self.assertRaisesRegex(MemorySuggestionInputError, "secret-bearing"):
            suggest_memory(
                self._request(
                    reason="AUTH_TOKEN=unit-test-secret-value",
                )
            )
        self.assertFalse((self.caller / local_path).exists())

    def test_yaml_and_candidate_fences_treat_metadata_as_data(self) -> None:
        result = suggest_memory(
            self._request(
                memory_id="fence-safe",
                title='Quoted "title"\nnext',
                triggers=("```",),
                write_local=True,
                notes="A reusable rule with ``` inside.",
            )
        )

        local = result.local_target.filesystem_path
        card = local.read_text(encoding="utf-8")
        decision = result.decision_target.filesystem_path.read_text(encoding="utf-8")
        self.assertIn('title: "Quoted \\"title\\"\\nnext"', card)
        self.assertIn('  - "```"', card)
        self.assertIn("````markdown\n", decision)
        self.assertIn(card, decision)

    def test_profile_allowlist_excludes_online_memory_and_credentials(self) -> None:
        self.assertEqual(MEMORY_SUGGEST_PROFILE_VARIABLES, ("MEMORY_LOCAL_DIR",))


if __name__ == "__main__":
    unittest.main()
