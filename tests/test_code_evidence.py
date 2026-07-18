#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.evidence.code import (  # noqa: E402
    CodeEvidenceError,
    CodeEvidenceRequest,
    CodeEvidenceRole,
    collect_code_evidence,
    select_code_tokens,
)
from agent_rails.evidence import code as code_module  # noqa: E402


class TrackingStream:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.offset = 0
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk = self.payload[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class CodeEvidenceTest(unittest.TestCase):
    def git(self, repo: Path, *arguments: str) -> str:
        environment = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            environment.pop(name, None)
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def test_selects_source_and_test_from_fixed_target(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-code-evidence-") as temp_dir:
            repo = Path(temp_dir)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.name", "Agent Rails Test")
            self.git(repo, "config", "user.email", "agent-rails@example.invalid")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            (repo / "src/session_validator.py").write_text(
                "class SessionValidator:\n"
                "    def validate_cookie(self, cookie: str) -> bool:\n"
                "        return bool(cookie)\n",
                encoding="utf-8",
            )
            (repo / "tests/test_session_validator.py").write_text(
                "from src.session_validator import SessionValidator\n\n"
                "def test_validate_cookie() -> None:\n"
                "    assert SessionValidator().validate_cookie('session')\n",
                encoding="utf-8",
            )
            self.git(repo, "add", "src", "tests")
            self.git(repo, "commit", "-qm", "base")
            target_sha = self.git(repo, "rev-parse", "HEAD")

            (repo / "src/session_validator.py").write_text(
                "API_TOKEN='workspace-only-secret'\n",
                encoding="utf-8",
            )
            (repo / "untracked_session_validator.py").write_text(
                "class UntrackedSessionValidator:\n    pass\n",
                encoding="utf-8",
            )

            records = collect_code_evidence(
                CodeEvidenceRequest(
                    project=repo,
                    target_sha=target_sha,
                    query="SessionValidator validate_cookie",
                    preferred_paths=("tests/test_session_validator.py",),
                    limit=4,
                )
            )

            paths = tuple(record.path for record in records)
            self.assertIn("src/session_validator.py", paths)
            self.assertIn("tests/test_session_validator.py", paths)
            self.assertNotIn("untracked_session_validator.py", paths)
            self.assertEqual(records[0].role, CodeEvidenceRole.IMPLEMENTATION)
            self.assertEqual(records[1].role, CodeEvidenceRole.VERIFICATION)
            source = next(
                record for record in records if record.path == "src/session_validator.py"
            )
            self.assertEqual(source.symbol, "SessionValidator")
            self.assertEqual(source.line, 1)

    def test_small_limit_preserves_implementation_and_verification_pair(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-code-pair-") as temp_dir:
            repo = Path(temp_dir)
            self.git(repo, "init", "-q")
            self.git(repo, "config", "user.name", "Agent Rails Test")
            self.git(repo, "config", "user.email", "agent-rails@example.invalid")
            (repo / "src").mkdir()
            (repo / "tests").mkdir()
            for name in ("session_validator.py", "session_validator_helper.py"):
                (repo / "src" / name).write_text(
                    "class SessionValidator:\n    pass\n",
                    encoding="utf-8",
                )
            (repo / "tests/test_session_validator.py").write_text(
                "def test_session_validator() -> None:\n    assert True\n",
                encoding="utf-8",
            )
            self.git(repo, "add", "src", "tests")
            self.git(repo, "commit", "-qm", "base")

            records = collect_code_evidence(
                CodeEvidenceRequest(
                    project=repo,
                    target_sha=self.git(repo, "rev-parse", "HEAD"),
                    query="session validator",
                    limit=2,
                )
            )

            self.assertEqual(
                tuple(record.role for record in records),
                (
                    CodeEvidenceRole.IMPLEMENTATION,
                    CodeEvidenceRole.VERIFICATION,
                ),
            )

            excluded = collect_code_evidence(
                CodeEvidenceRequest(
                    project=repo,
                    target_sha=self.git(repo, "rev-parse", "HEAD"),
                    query="session validator",
                    preferred_paths=("src/session_validator.py",),
                    excluded_paths=("src/session_validator.py",),
                    limit=2,
                )
            )

            self.assertEqual(
                tuple(record.path for record in excluded),
                (
                    "src/session_validator_helper.py",
                    "tests/test_session_validator.py",
                ),
            )

    def test_token_selection_supports_cjk_and_ignored_project_name(self) -> None:
        tokens = select_code_tokens(
            "修复登录校验并减少无关代码 agent-rails",
            "agent-rails",
        )

        self.assertNotIn("agent-rails", tokens)
        self.assertIn("登录", tokens)
        self.assertIn("校验", tokens)
        self.assertLessEqual(len(tokens), 6)

    def test_common_english_words_do_not_outrank_task_terms(self) -> None:
        tokens = select_code_tokens(
            "Update all the files using the contract acceptance verifier"
        )

        self.assertEqual(tokens, ("contract", "acceptance", "verifier"))

    def test_git_search_stops_reading_after_bounded_path_count(self) -> None:
        payload = b"".join(
            f"target:src/session_{index:04d}.py\0".encode("utf-8")
            for index in range(2_000)
        )
        stream = TrackingStream(payload)
        process = Mock(stdout=stream)
        process.wait.return_value = 0
        request = CodeEvidenceRequest(
            project=Path("/unused"),
            target_sha="target",
            query="session",
        )

        with (
            patch.object(code_module.subprocess, "Popen", return_value=process),
            patch.object(code_module, "stop_process_group") as stop,
        ):
            matches = code_module._content_paths(request, ("session",))

        self.assertEqual(len(matches), 512)
        self.assertLess(stream.offset, len(payload))
        self.assertTrue(stream.closed)
        stop.assert_called_once_with(process)

    def test_git_search_rejects_oversized_unframed_output(self) -> None:
        stream = TrackingStream(
            b"x" * (code_module._SEARCH_OUTPUT_MAX_BYTES + 1)
        )
        process = Mock(stdout=stream)
        request = CodeEvidenceRequest(
            project=Path("/unused"),
            target_sha="target",
            query="session",
        )

        with (
            patch.object(code_module.subprocess, "Popen", return_value=process),
            patch.object(code_module, "stop_process_group") as stop,
            self.assertRaisesRegex(CodeEvidenceError, "exceeds safe limit"),
        ):
            code_module._content_paths(request, ("session",))

        self.assertTrue(stream.closed)
        stop.assert_called_once_with(process)

    def test_invalid_target_is_reported_as_module_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-code-missing-") as temp_dir:
            with self.assertRaises(CodeEvidenceError):
                collect_code_evidence(
                    CodeEvidenceRequest(
                        project=Path(temp_dir),
                        target_sha="missing-target",
                        query="SessionValidator",
                    )
                )


if __name__ == "__main__":
    unittest.main()
