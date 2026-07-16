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

from agent_rails.git.scope import (  # noqa: E402
    GitScopeError,
    collect_git_scope_snapshot,
    collect_worktree_snapshot,
    fingerprint_git_worktree,
    hidden_worktree_index_paths,
    resolve_git_head,
    resolve_git_scope,
    read_nul_paths,
    write_git_scope_snapshot,
)
import agent_rails.git.scope as scope_module  # noqa: E402


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def init_repo(path: Path, branch: str = "main") -> None:
    path.mkdir(parents=True)
    run_git(path, "init", "-q")
    run_git(path, "config", "user.email", "agent-rails-tests@example.com")
    run_git(path, "config", "user.name", "Agent Rails Tests")
    (path / "README.md").write_text("# base\n", encoding="utf-8")
    run_git(path, "add", "README.md")
    run_git(path, "commit", "-qm", "base")
    run_git(path, "branch", "-M", branch)


class GitScopeTest(unittest.TestCase):
    def test_collect_worktree_snapshot_is_empty_for_clean_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-clean-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)

            snapshot = collect_worktree_snapshot(repo)

            self.assertEqual(snapshot.status, "")
            self.assertEqual(snapshot.staged_paths, ())
            self.assertEqual(snapshot.unstaged_paths, ())
            self.assertEqual(snapshot.untracked_paths, ())
            self.assertEqual(snapshot.changed_paths, ())

    def test_collect_worktree_snapshot_collects_dirty_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-dirty-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            (repo / "README.md").write_text("# dirty\n", encoding="utf-8")
            (repo / "staged.py").write_text("print('staged')\n", encoding="utf-8")
            run_git(repo, "add", "staged.py")
            (repo / "notes.txt").write_text("untracked\n", encoding="utf-8")

            snapshot = collect_worktree_snapshot(repo)

            self.assertEqual(snapshot.staged_paths, ("staged.py",))
            self.assertEqual(snapshot.unstaged_paths, ("README.md",))
            self.assertEqual(snapshot.untracked_paths, ("notes.txt",))
            self.assertEqual(
                snapshot.changed_paths, ("README.md", "notes.txt", "staged.py")
            )
            self.assertIn(" M README.md", snapshot.status)
            self.assertIn("A  staged.py", snapshot.status)
            self.assertIn("?? notes.txt", snapshot.status)

    def test_worktree_fingerprint_detects_content_changes_with_same_status(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-fingerprint-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            (repo / "README.md").write_text("# dirty one\n", encoding="utf-8")
            (repo / "notes.txt").write_text("untracked one\n", encoding="utf-8")
            initial = fingerprint_git_worktree(repo)

            (repo / "README.md").write_text("# dirty two\n", encoding="utf-8")
            tracked_changed = fingerprint_git_worktree(repo)
            (repo / "notes.txt").write_text("untracked two\n", encoding="utf-8")
            untracked_changed = fingerprint_git_worktree(repo)

            self.assertNotEqual(initial, tracked_changed)
            self.assertNotEqual(tracked_changed, untracked_changed)

    def test_worktree_fingerprint_never_executes_repository_textconv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-textconv-") as temp_dir:
            root = Path(temp_dir)
            repo = root / "repo"
            marker = root / "textconv-executed"
            driver = root / "textconv.sh"
            init_repo(repo)
            driver.write_text(
                f'#!/bin/sh\n: > "{marker}"\ncat "$1"\n',
                encoding="utf-8",
            )
            driver.chmod(0o755)
            (repo / ".gitattributes").write_text("*.txt diff=pwn\n", encoding="utf-8")
            (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
            run_git(repo, "add", ".gitattributes", "tracked.txt")
            run_git(repo, "commit", "-qm", "textconv fixture")
            run_git(repo, "config", "diff.pwn.textconv", str(driver))
            (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")

            fingerprint_git_worktree(repo)

            self.assertFalse(marker.exists())

    def test_sampled_fingerprint_detects_middle_change_with_restored_mtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-large-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            artifact = repo / "large.bin"
            with artifact.open("wb") as handle:
                handle.write(b"start")
                handle.seek(9 * 1024 * 1024)
                handle.write(b"end")
            initial_metadata = artifact.stat()
            initial = fingerprint_git_worktree(repo)

            with artifact.open("r+b") as handle:
                handle.seek(4 * 1024 * 1024)
                handle.write(b"changed")
            os.utime(
                artifact,
                ns=(initial_metadata.st_atime_ns, initial_metadata.st_mtime_ns),
            )
            changed = fingerprint_git_worktree(repo)

            self.assertNotEqual(initial, changed)

    def test_hidden_index_paths_reports_assume_unchanged_and_skip_worktree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-hidden-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            (repo / "assumed.txt").write_text("assumed\n", encoding="utf-8")
            (repo / "skipped.txt").write_text("skipped\n", encoding="utf-8")
            run_git(repo, "add", "assumed.txt", "skipped.txt")
            run_git(repo, "commit", "-qm", "hidden flags fixture")
            run_git(repo, "update-index", "--assume-unchanged", "assumed.txt")
            run_git(repo, "update-index", "--skip-worktree", "skipped.txt")

            self.assertEqual(
                hidden_worktree_index_paths(repo),
                ("assumed.txt", "skipped.txt"),
            )

            (repo / "skipped.txt").unlink()
            self.assertEqual(
                hidden_worktree_index_paths(repo),
                ("assumed.txt",),
            )

    def test_collect_worktree_snapshot_preserves_space_rename_and_unicode_paths(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-paths-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            (repo / "old name.txt").write_text("rename\n", encoding="utf-8")
            run_git(repo, "add", "old name.txt")
            run_git(repo, "commit", "-qm", "rename fixture")
            run_git(repo, "mv", "old name.txt", "new 名称.txt")
            untracked_paths = ("notes space.txt", "ユニコード.md")
            for path in untracked_paths:
                (repo / path).write_text("untracked\n", encoding="utf-8")

            snapshot = collect_worktree_snapshot(repo)

            self.assertEqual(snapshot.staged_paths, ("new 名称.txt",))
            self.assertEqual(snapshot.unstaged_paths, ())
            self.assertEqual(snapshot.untracked_paths, tuple(sorted(untracked_paths)))
            self.assertEqual(
                snapshot.changed_paths,
                tuple(sorted(("new 名称.txt", *untracked_paths))),
            )
            self.assertNotIn("old name.txt", snapshot.changed_paths)

    def test_collect_worktree_snapshot_ignores_inherited_git_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-env-") as temp_dir:
            temp = Path(temp_dir)
            target = temp / "target"
            other = temp / "other"
            init_repo(target)
            init_repo(other)
            (target / "README.md").write_text("# target dirty\n", encoding="utf-8")
            (other / "other.txt").write_text("other dirty\n", encoding="utf-8")
            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_DIR": str(other / ".git"),
                    "GIT_WORK_TREE": str(other),
                    "GIT_COMMON_DIR": str(other / ".git"),
                    "GIT_INDEX_FILE": str(other / ".git" / "index"),
                }
            )

            snapshot = collect_worktree_snapshot(target, environment=environment)

            self.assertEqual(snapshot.unstaged_paths, ("README.md",))
            self.assertNotIn("other.txt", snapshot.changed_paths)

    def test_collect_worktree_snapshot_supports_unborn_repository(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktree-unborn-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            repo.mkdir()
            run_git(repo, "init", "-q")
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            run_git(repo, "add", "staged.txt")
            (repo / "untracked.txt").write_text("untracked\n", encoding="utf-8")

            snapshot = collect_worktree_snapshot(repo)

            self.assertEqual(snapshot.staged_paths, ("staged.txt",))
            self.assertEqual(snapshot.unstaged_paths, ())
            self.assertEqual(snapshot.untracked_paths, ("untracked.txt",))
            self.assertEqual(
                snapshot.changed_paths, ("staged.txt", "untracked.txt")
            )
            self.assertIn("A  staged.txt", snapshot.status)
            self.assertIn("?? untracked.txt", snapshot.status)

    def test_movable_target_is_frozen_after_initial_resolution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-frozen-ref-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            init_repo(repo)
            base_sha = run_git(repo, "rev-parse", "HEAD")
            (repo / "old.txt").write_text("old target\n", encoding="utf-8")
            run_git(repo, "add", "old.txt")
            run_git(repo, "commit", "-qm", "old target")
            old_target_sha = run_git(repo, "rev-parse", "HEAD")
            run_git(repo, "branch", "moving-target", old_target_sha)
            (repo / "new.txt").write_text("new target\n", encoding="utf-8")
            run_git(repo, "add", "new.txt")
            run_git(repo, "commit", "-qm", "new target")
            new_target_sha = run_git(repo, "rev-parse", "HEAD")
            moved = False
            real_git_call = scope_module._git

            def move_after_resolution(project, arguments, *, environment=None):
                nonlocal moved
                result = real_git_call(project, arguments, environment=environment)
                if (
                    not moved
                    and tuple(arguments)
                    == ("rev-parse", "--verify", "moving-target^{commit}")
                ):
                    run_git(repo, "update-ref", "refs/heads/moving-target", new_target_sha)
                    moved = True
                return result

            with patch.object(scope_module, "_git", side_effect=move_after_resolution):
                scope = resolve_git_scope(
                    repo,
                    target_ref="moving-target",
                    base_ref=base_sha,
                    base_policy="project",
                )
                snapshot = write_git_scope_snapshot(
                    repo, scope, temp / "snapshot", include_worktree=False
                )

            self.assertTrue(moved)
            self.assertEqual(scope.target_sha, old_target_sha)
            self.assertEqual(
                scope.target_short_sha,
                run_git(repo, "rev-parse", "--short", old_target_sha),
            )
            self.assertEqual(snapshot.committed_paths, ("old.txt",))
            self.assertNotIn("new.txt", snapshot.changed_paths)

    def test_project_policy_resolves_main_and_captures_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-scope-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            init_repo(repo)
            run_git(repo, "switch", "-qc", "feature")
            (repo / "feature.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            run_git(repo, "add", "feature.sh")
            run_git(repo, "commit", "-qm", "feature")
            (repo / "README.md").write_text("# changed\n", encoding="utf-8")
            (repo / "notes.txt").write_text("untracked\n", encoding="utf-8")

            scope = resolve_git_scope(repo, target_ref="HEAD", base_policy="project")
            self.assertEqual(scope.base_ref, "main")
            self.assertEqual(scope.target_sha, run_git(repo, "rev-parse", "HEAD"))
            self.assertEqual(scope.merge_base, run_git(repo, "merge-base", "HEAD", "main"))

            memory_snapshot = collect_git_scope_snapshot(
                repo, scope, include_worktree=True
            )
            snapshot = write_git_scope_snapshot(
                repo, scope, temp / "snapshot", include_worktree=True
            )
            self.assertEqual(snapshot, memory_snapshot)
            self.assertEqual(snapshot.committed_paths, ("feature.sh",))
            self.assertEqual(snapshot.staged_paths, ())
            self.assertEqual(snapshot.unstaged_paths, ("README.md",))
            self.assertEqual(snapshot.untracked_paths, ("notes.txt",))
            self.assertEqual(snapshot.worktree_paths, ("README.md", "notes.txt"))
            self.assertEqual(
                snapshot.changed_paths, ("README.md", "feature.sh", "notes.txt")
            )
            self.assertIn(" M README.md", snapshot.status)
            self.assertIn("?? notes.txt", snapshot.status)

            target_snapshot = write_git_scope_snapshot(
                repo, scope, temp / "target-snapshot", include_worktree=False
            )
            self.assertEqual(target_snapshot.status, "")
            self.assertEqual(target_snapshot.staged_paths, ())
            self.assertEqual(target_snapshot.unstaged_paths, ())
            self.assertEqual(target_snapshot.untracked_paths, ())
            self.assertEqual(target_snapshot.worktree_paths, ())
            self.assertEqual(target_snapshot.changed_paths, ("feature.sh",))

    def test_publish_policy_prefers_the_upstream_ref(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-upstream-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            remote = temp / "remote.git"
            init_repo(repo)
            subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
            run_git(repo, "remote", "add", "origin", str(remote))
            run_git(repo, "push", "-qu", "origin", "main")

            scope = resolve_git_scope(repo, target_ref="HEAD", base_policy="publish")
            self.assertEqual(scope.base_ref, "@{upstream}")
            self.assertEqual(scope.base_sha, scope.target_sha)

    def test_missing_default_base_uses_the_target_as_merge_base(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-no-base-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo, branch="topic")

            scope = resolve_git_scope(repo, target_ref="HEAD", base_policy="project")

            self.assertEqual(scope.base_ref, "")
            self.assertEqual(scope.base_sha, "")
            self.assertEqual(scope.merge_base, scope.target_sha)

    def test_snapshot_preserves_staged_deleted_and_renamed_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-status-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            init_repo(repo)
            (repo / "old-name.txt").write_text("rename\n", encoding="utf-8")
            (repo / "deleted.txt").write_text("delete\n", encoding="utf-8")
            run_git(repo, "add", "old-name.txt", "deleted.txt")
            run_git(repo, "commit", "-qm", "status fixtures")
            run_git(repo, "switch", "-qc", "feature")

            run_git(repo, "mv", "old-name.txt", "new-name.txt")
            (repo / "deleted.txt").unlink()
            (repo / "staged.txt").write_text("staged\n", encoding="utf-8")
            run_git(repo, "add", "staged.txt")

            scope = resolve_git_scope(repo, target_ref="HEAD", base_policy="project")
            snapshot = write_git_scope_snapshot(
                repo, scope, temp / "snapshot", include_worktree=True
            )

            self.assertEqual(
                snapshot.worktree_paths,
                ("deleted.txt", "new-name.txt", "staged.txt"),
            )
            self.assertEqual(snapshot.staged_paths, ("new-name.txt", "staged.txt"))
            self.assertEqual(snapshot.unstaged_paths, ("deleted.txt",))
            self.assertNotIn("old-name.txt", snapshot.changed_paths)

    def test_snapshot_preserves_special_untracked_paths_without_quoting(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-paths-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            init_repo(repo)
            special_paths = (
                "arrow -> secret.env",
                "secret file.env",
                "敏感.env",
            )
            for relative_path in special_paths:
                (repo / relative_path).write_text(
                    "API_TOKEN=unit-test-special-path-secret\n", encoding="utf-8"
                )

            scope = resolve_git_scope(repo, target_ref="HEAD", base_policy="project")
            snapshot_dir = temp / "snapshot"
            snapshot = write_git_scope_snapshot(
                repo, scope, snapshot_dir, include_worktree=True
            )

            self.assertEqual(snapshot.untracked_paths, special_paths)
            self.assertEqual(snapshot.worktree_paths, special_paths)
            self.assertEqual(
                (snapshot_dir / "untracked-paths0").read_bytes(),
                b"\0".join(path.encode("utf-8") for path in special_paths) + b"\0",
            )
            self.assertEqual(
                (snapshot_dir / "changed-paths0").read_bytes(),
                b"\0".join(path.encode("utf-8") for path in special_paths) + b"\0",
            )
            self.assertEqual(
                read_nul_paths(snapshot_dir / "changed-paths0"), special_paths
            )
            self.assertNotIn('"secret file.env"', snapshot.worktree_paths)

    def test_snapshot_fails_closed_for_control_paths(self) -> None:
        unsafe_paths = (
            "line\nbreak.env",
            "tab\tbreak.env",
            "escape-\x1b]0;spoof\x07.env",
            "bidi-\u202espoof.env",
            "separator-\u2028spoof.env",
        )
        for unsafe_path in unsafe_paths:
            with self.subTest(path=repr(unsafe_path)):
                with tempfile.TemporaryDirectory(
                    prefix="agent-rails-git-control-"
                ) as temp_dir:
                    temp = Path(temp_dir)
                    repo = temp / "repo"
                    init_repo(repo)
                    (repo / unsafe_path).write_text(
                        "API_TOKEN=unit-test-control-path-secret\n", encoding="utf-8"
                    )
                    scope = resolve_git_scope(
                        repo, target_ref="HEAD", base_policy="project"
                    )

                    with self.assertRaisesRegex(
                        GitScopeError,
                        "Git paths containing control characters are unsupported",
                    ):
                        write_git_scope_snapshot(
                            repo, scope, temp / "snapshot", include_worktree=True
                        )

    def test_unrelated_history_has_no_merge_base(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-merge-base-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            run_git(repo, "switch", "--orphan", "unrelated")
            (repo / "README.md").unlink(missing_ok=True)
            (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
            run_git(repo, "add", "-A")
            run_git(repo, "commit", "-qm", "unrelated")

            with self.assertRaisesRegex(
                GitScopeError,
                "Merge base not found between unrelated and main",
            ):
                resolve_git_scope(
                    repo,
                    target_ref="unrelated",
                    base_ref="main",
                    base_policy="project",
                )

    def test_invalid_refs_and_policy_preserve_public_errors(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-errors-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)

            with self.assertRaisesRegex(GitScopeError, "Target ref not found: missing-target"):
                resolve_git_scope(repo, target_ref="missing-target", base_policy="project")
            with self.assertRaisesRegex(GitScopeError, "Base ref not found: missing-base"):
                resolve_git_scope(
                    repo,
                    target_ref="HEAD",
                    base_ref="missing-base",
                    base_policy="project",
                )
            with self.assertRaisesRegex(GitScopeError, "Unknown Git scope base policy: other"):
                resolve_git_scope(repo, target_ref="HEAD", base_policy="other")

    def test_explicit_project_ignores_inherited_repository_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-env-") as temp_dir:
            temp = Path(temp_dir)
            target = temp / "target"
            other = temp / "other"
            init_repo(target)
            init_repo(other)
            (other / "other.txt").write_text("other\n", encoding="utf-8")
            run_git(other, "add", "other.txt")
            run_git(other, "commit", "-qm", "other")
            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_DIR": str(other / ".git"),
                    "GIT_WORK_TREE": str(other),
                    "GIT_COMMON_DIR": str(other / ".git"),
                    "GIT_INDEX_FILE": str(other / ".git" / "index"),
                }
            )

            scope = resolve_git_scope(
                target,
                target_ref="HEAD",
                base_policy="project",
                environment=environment,
            )

            self.assertEqual(scope.target_sha, run_git(target, "rev-parse", "HEAD"))
            self.assertEqual(scope.head_sha, run_git(target, "rev-parse", "HEAD"))
            self.assertEqual(
                resolve_git_head(target, environment=environment),
                run_git(target, "rev-parse", "HEAD"),
            )
            self.assertNotEqual(scope.target_sha, run_git(other, "rev-parse", "HEAD"))

    def test_shell_values_are_safe_to_eval(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-shell-") as temp_dir:
            repo = Path(temp_dir) / "repo"
            init_repo(repo)
            marker = Path(temp_dir) / "must-not-exist"
            ref_name = f"topic;touch${{IFS}}{marker}"
            run_git(repo, "branch", ref_name)
            completed = subprocess.run(
                [
                    "python3",
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "git-scope",
                    "--project",
                    str(repo),
                    "--target-ref",
                    ref_name,
                    "--base",
                    "main",
                    "--policy",
                    "project",
                    "--shell",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            shell = subprocess.run(
                [
                    "bash",
                    "-c",
                    'eval "$1"; printf "%s\\n" "$AGENT_GIT_SCOPE_TARGET_REF"',
                    "bash",
                    completed.stdout,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(shell.stdout.strip(), ref_name)
            self.assertFalse(marker.exists())

    def test_cli_normalizes_snapshot_io_failures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-io-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            init_repo(repo)
            blocker = temp / "not-a-directory"
            blocker.write_text("block snapshot directory\n", encoding="utf-8")

            completed = subprocess.run(
                [
                    "python3",
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "git-scope",
                    "--project",
                    str(repo),
                    "--target-ref",
                    "HEAD",
                    "--policy",
                    "project",
                    "--snapshot-dir",
                    str(blocker / "snapshot"),
                    "--include-worktree",
                    "--shell",
                ],
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 2)
            self.assertIn("Unable to write Git scope snapshot:", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
