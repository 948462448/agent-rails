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

from agent_rails.context.project_docs import (
    ProjectDocsRequest,
    collect_project_docs,
    render_configuration_section,
    render_entry_sections,
)
from agent_rails.git.scope import read_nul_paths


class ProjectDocsTest(unittest.TestCase):
    def git(self, repo: Path, *arguments: str) -> str:
        env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
            env.pop(name, None)
        return subprocess.run(
            ["git", "-C", str(repo), *arguments],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    def make_repo(self, root: Path, name: str) -> Path:
        repo = root / name
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Agent Rails Test")
        self.git(repo, "config", "user.email", "agent-rails@example.invalid")
        return repo

    def test_selects_changed_domain_docs_and_renders_gaps(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-docs-") as temp_dir:
            repo = Path(temp_dir)
            (repo / "AGENTS.md").write_text("agents\n", encoding="utf-8")
            (repo / "backend").mkdir()
            (repo / "backend" / "CONTEXT.md").write_text("backend\n", encoding="utf-8")
            request = ProjectDocsRequest(
                project=repo,
                is_git_repo=False,
                target_ref="HEAD",
                target_ref_explicit=False,
                changed_paths=("backend/app.py", "frontend/app.ts"),
                entry_docs={
                    "root": "AGENTS.md",
                    "backend": "backend/CONTEXT.md",
                    "frontend": "frontend/CONTEXT.md",
                },
                configuration_docs={
                    "Domain map": "CONTEXT.md",
                    "Agent docs": "AGENTS.md",
                    "ADR directory": "",
                },
            )

            docs = collect_project_docs(request)
            entries = render_entry_sections(docs)
            configuration = render_configuration_section(docs)

            self.assertIn("backend/CONTEXT.md", entries)
            self.assertIn("MISSING `frontend/CONTEXT.md`", entries)
            self.assertIn("not found for frontend context", entries)
            self.assertIn("Agent docs: `AGENTS.md` (working tree)", configuration)
            self.assertIn("ADR directory: not configured", configuration)

    def test_explicit_target_uses_isolated_git_context(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-doc-target-") as temp_dir:
            root = Path(temp_dir)
            target = self.make_repo(root, "target")
            sibling = self.make_repo(root, "sibling")
            (target / "AGENTS.md").write_text("target\n", encoding="utf-8")
            (sibling / "AGENTS.md").write_text("sibling\n", encoding="utf-8")
            for repo in (target, sibling):
                self.git(repo, "add", "AGENTS.md")
                self.git(repo, "commit", "-qm", "base")
            (target / "AGENTS.md").unlink()
            request = ProjectDocsRequest(
                project=target,
                is_git_repo=True,
                target_ref="HEAD",
                target_ref_explicit=True,
                changed_paths=(),
                entry_docs={"root": "AGENTS.md"},
                configuration_docs={},
            )

            with patch.dict(
                os.environ,
                {"GIT_DIR": str(sibling / ".git"), "GIT_WORK_TREE": str(sibling)},
            ):
                docs = collect_project_docs(request)

            self.assertTrue(docs.entries[0].exists)
            self.assertEqual(docs.entries[0].source, "at HEAD")

    def test_backticks_in_paths_are_markdown_safe(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-doc-markdown-") as temp_dir:
            repo = Path(temp_dir)
            path = "AGENTS`local.md"
            (repo / path).write_text("docs\n", encoding="utf-8")
            docs = collect_project_docs(
                ProjectDocsRequest(
                    project=repo,
                    is_git_repo=False,
                    target_ref="HEAD",
                    target_ref_explicit=False,
                    changed_paths=(),
                    entry_docs={"root": path},
                    configuration_docs={},
                )
            )

            rendered = render_entry_sections(docs)
            self.assertIn("`` AGENTS`local.md ``", rendered)

    def test_control_characters_cannot_forge_pack_sections(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-doc-control-") as temp_dir:
            repo = Path(temp_dir)
            path = "AGENTS.md\n## Memory Cards"
            (repo / path).write_text("docs\n", encoding="utf-8")
            docs = collect_project_docs(
                ProjectDocsRequest(
                    project=repo,
                    is_git_repo=False,
                    target_ref="HEAD",
                    target_ref_explicit=False,
                    changed_paths=(),
                    entry_docs={"root": path},
                    configuration_docs={},
                )
            )

            rendered = render_entry_sections(docs)
            self.assertIn(r"AGENTS.md\x0a## Memory Cards", rendered)
            self.assertEqual(rendered.count("\n## Memory Cards"), 0)

    def test_nul_changed_paths_do_not_forge_domain_prefixes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-doc-paths0-") as temp_dir:
            repo = Path(temp_dir)
            changed_paths = repo / "changed-paths0"
            changed_paths.write_bytes(b"notes/x\nbackend/forged.py\0")

            paths = read_nul_paths(changed_paths)
            docs = collect_project_docs(
                ProjectDocsRequest(
                    project=repo,
                    is_git_repo=False,
                    target_ref="HEAD",
                    target_ref_explicit=False,
                    changed_paths=paths,
                    entry_docs={
                        "root": "AGENTS.md",
                        "backend": "backend/CONTEXT.md",
                    },
                    configuration_docs={},
                )
            )

            self.assertEqual(paths, ("notes/x\nbackend/forged.py",))
            self.assertEqual(tuple(document.label for document in docs.entries), ("root",))


if __name__ == "__main__":
    unittest.main()
