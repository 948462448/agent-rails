#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.config.target_project import resolve_target_project
from agent_rails.core.paths import AgentRailsPaths, project_worktree_slug


class PathsTest(unittest.TestCase):
    def test_config_home_is_resolved_from_each_call_environment(self) -> None:
        first = AgentRailsPaths.from_environment(ROOT, {"HOME": "/tmp/rails-home-one"})
        second = AgentRailsPaths.from_environment(ROOT, {"HOME": "/tmp/rails-home-two"})
        literal = AgentRailsPaths.from_environment(
            ROOT,
            {"HOME": "/tmp/rails-home-three", "AGENT_RAILS_CONFIG_HOME": "~/.rails"},
        )

        self.assertEqual(first.config_home, "/tmp/rails-home-one/.agent-rails")
        self.assertEqual(second.config_home, "/tmp/rails-home-two/.agent-rails")
        self.assertEqual(literal.config_home, "~/.rails")

    def test_profile_precedence_and_legacy_kit_fallback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-paths-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            config_home = temp / "config"
            project.mkdir()
            paths = AgentRailsPaths.from_environment(
                ROOT,
                {"HOME": str(temp / "home"), "AGENT_RAILS_CONFIG_HOME": str(config_home)},
            )

            self.assertEqual(
                paths.resolve_profile(project, "project"), str(paths.default_profile_path)
            )
            legacy_user = config_home / "profiles" / "project.profile"
            legacy_user.parent.mkdir(parents=True)
            legacy_user.write_text("PROJECT_NAME=legacy\n", encoding="utf-8")
            self.assertEqual(paths.resolve_profile(project, "project"), str(legacy_user))

            user_project = config_home / "profiles" / "projects" / "project.profile"
            user_project.parent.mkdir(parents=True)
            user_project.write_text("PROJECT_NAME=user\n", encoding="utf-8")
            self.assertEqual(paths.resolve_profile(project, "project"), str(user_project))

            project_profile_sh = project / ".agent-rails" / "profile.sh"
            project_profile_sh.parent.mkdir()
            project_profile_sh.write_text("PROJECT_NAME=project-sh\n", encoding="utf-8")
            self.assertEqual(paths.resolve_profile(project, "project"), str(project_profile_sh))

            project_profile = project_profile_sh.with_name("profile")
            project_profile.write_text("PROJECT_NAME=project\n", encoding="utf-8")
            self.assertEqual(paths.resolve_profile(project, "project"), str(project_profile))

            explicit = temp / "explicit.profile"
            self.assertEqual(
                paths.resolve_profile(project, "project", str(explicit)), str(explicit)
            )
            missing_legacy_kit = ROOT / "profiles" / "missing-old-project.profile"
            self.assertEqual(
                paths.resolve_profile(project, "project", str(missing_legacy_kit)),
                str(paths.default_profile_path),
            )

    def test_worktree_slug_matches_compatibility_shell(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-slug-") as temp_dir:
            root = Path(temp_dir).resolve()
            shell = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; agent_rails_project_worktree_slug "$2" "$3"',
                    "bash",
                    str(ROOT / "scripts" / "agent-paths.sh"),
                    str(root),
                    "Mixed Project",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(project_worktree_slug(root, "Mixed Project"), shell)


class TargetProjectContextTest(unittest.TestCase):
    def test_git_root_ignores_inherited_repository_environment(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-git-env-") as temp_dir:
            temp = Path(temp_dir)
            other_repo = temp / "other-repo"
            target_repo = temp / "target-repo"
            nested = target_repo / "nested" / "path"
            other_repo.mkdir()
            nested.mkdir(parents=True)
            subprocess.run(["git", "-C", str(other_repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(target_repo), "init", "-q"], check=True)
            environment = dict(os.environ)
            environment.update(
                {
                    "GIT_DIR": str(other_repo / ".git"),
                    "GIT_WORK_TREE": str(other_repo),
                    "GIT_COMMON_DIR": str(other_repo / ".git"),
                }
            )

            context = resolve_target_project(
                nested,
                kit_home=ROOT,
                environment=environment,
                load_profile=False,
            )

            self.assertEqual(context.root, target_repo.resolve())
            self.assertTrue(context.is_git_repo)

    def test_shell_bridge_quotes_profile_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-context-quote-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            project.mkdir()
            marker = temp / "must-not-exist"
            project_name = f"literal-$(touch {marker})"
            profile = temp / "target.profile"
            profile.write_text(f"PROJECT_NAME='{project_name}'\n", encoding="utf-8")
            shell_script = r'''
assignments="$(
  PYTHONDONTWRITEBYTECODE=1 \
    python3 -E "$1/scripts/agent-python-cli.py" target-context \
      --project "$2" --profile "$3" --agent-rails-home "$1" \
      --required-profile --shell
)"
eval "$assignments"
printf '%s\n' "$PROJECT_NAME"
'''
            output = subprocess.run(
                ["bash", "-c", shell_script, "bash", str(ROOT), str(project), str(profile)],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(output, project_name)
            self.assertFalse(marker.exists())

    def test_missing_required_profile_is_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-missing-profile-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            project.mkdir()
            with self.assertRaises(FileNotFoundError):
                resolve_target_project(
                    project,
                    kit_home=ROOT,
                    explicit_profile=str(temp / "missing.profile"),
                    environment=dict(os.environ),
                    require_profile=True,
                )

    def test_context_can_finalize_without_executing_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-context-finalize-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            project.mkdir()
            profile = temp / "target.profile"
            profile.write_text("exit 42\n", encoding="utf-8")
            explicit_pack = temp / "explicit-pack.md"
            environment = dict(os.environ)
            environment.update(
                {
                    "AGENT_RAILS_CONFIG_HOME": str(temp / "config"),
                    "PROJECT_NAME": "finalized-project",
                    "PROJECT_WORKTREE_SLUG": "initial-worktree",
                    "TASK_PACK_PATH": str(explicit_pack),
                }
            )

            context = resolve_target_project(
                project,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
                load_profile=False,
            )

            self.assertEqual(context.profile_status, "unloaded")
            self.assertEqual(context.project_name, "finalized-project")
            self.assertEqual(context.worktree_slug, "initial-worktree")
            self.assertEqual(context.task_pack_path, str(explicit_pack))

    def test_nested_git_profile_and_env_file_finalize_in_order(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-target-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            nested = repo / "nested" / "path"
            nested.mkdir(parents=True)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            config_home = temp / "config-from-profile"
            env_pack = temp / "pack-from-env.md"
            env_file = temp / "agent.env"
            env_file.write_text(
                f'PROJECT_NAME="env-project"\nTASK_PACK_PATH="{env_pack}"\n',
                encoding="utf-8",
            )
            profile = temp / "target.profile"
            profile.write_text(
                '\n'.join(
                    [
                        'source "$AGENT_RAILS_HOME/profiles/default.profile"',
                        'PROJECT_NAME="profile-project"',
                        f'AGENT_RAILS_CONFIG_HOME="{config_home}"',
                        f'AGENT_RAILS_ENV_FILE="{env_file}"',
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            environment = dict(os.environ)
            environment["HOME"] = str(temp / "home")
            environment.pop("AGENT_RAILS_CONFIG_HOME", None)
            environment.pop("PROJECT_NAME", None)
            environment.pop("PROJECT_WORKTREE_SLUG", None)
            environment.pop("TASK_PACK_PATH", None)

            context = resolve_target_project(
                nested,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
                load_environment_file=True,
            )

            self.assertEqual(context.root, repo.resolve())
            self.assertTrue(context.is_git_repo)
            self.assertEqual(context.default_name, "repo")
            self.assertEqual(context.project_name, "env-project")
            self.assertEqual(context.profile_status, "loaded")
            self.assertEqual(context.task_pack_path, str(env_pack))
            self.assertEqual(
                context.worktree_slug,
                project_worktree_slug(repo.resolve(), "env-project"),
            )

    def test_initial_worktree_slug_wins_over_profile_value(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-preset-slug-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            project.mkdir()
            profile = temp / "target.profile"
            profile.write_text('PROJECT_WORKTREE_SLUG="from-profile"\n', encoding="utf-8")
            environment = dict(os.environ)
            environment["PROJECT_WORKTREE_SLUG"] = "from-caller"

            context = resolve_target_project(
                project,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
            )

            self.assertEqual(context.worktree_slug_preset, "from-caller")
            self.assertEqual(context.worktree_slug, "from-caller")

    def test_empty_inherited_config_home_is_initialized_before_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-empty-config-") as temp_dir:
            temp = Path(temp_dir)
            project = temp / "project"
            project.mkdir()
            profile = temp / "target.profile"
            profile.write_text('PROJECT_NAME="custom"\n', encoding="utf-8")
            environment = dict(os.environ)
            environment["HOME"] = str(temp / "home")
            environment["AGENT_RAILS_CONFIG_HOME"] = ""
            environment["PROJECT_NAME"] = ""

            context = resolve_target_project(
                project,
                kit_home=ROOT,
                explicit_profile=str(profile),
                environment=environment,
                require_profile=True,
            )

            self.assertTrue(
                context.task_pack_path.startswith(
                    f"{environment['HOME']}/.agent-rails/agent-context/custom-"
                )
            )

    def test_project_profiles_do_not_leak_between_sibling_repositories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-siblings-") as temp_dir:
            temp = Path(temp_dir)
            environment = dict(os.environ)
            environment["HOME"] = str(temp / "home")
            contexts = []
            for parent, project_name in (("one", "first"), ("two", "second")):
                project = temp / parent / "same-name"
                profile = project / ".agent-rails" / "profile"
                profile.parent.mkdir(parents=True)
                profile.write_text(f'PROJECT_NAME="{project_name}"\n', encoding="utf-8")
                contexts.append(
                    resolve_target_project(
                        project,
                        kit_home=ROOT,
                        environment=environment,
                        require_profile=True,
                    )
                )

            self.assertEqual([context.project_name for context in contexts], ["first", "second"])
            self.assertNotEqual(contexts[0].profile_path, contexts[1].profile_path)
            self.assertNotEqual(contexts[0].worktree_slug, contexts[1].worktree_slug)

    def test_real_git_worktrees_receive_distinct_slugs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-worktrees-") as temp_dir:
            temp = Path(temp_dir)
            repo = temp / "repo"
            sibling = temp / "repo-worktree"
            repo.mkdir()
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "Agent Rails Tests"], check=True)
            (repo / "README.md").write_text("test\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "init"], check=True)
            subprocess.run(
                ["git", "-C", str(repo), "worktree", "add", "-qb", "test-worktree", str(sibling)],
                check=True,
            )
            environment = dict(os.environ)
            environment["HOME"] = str(temp / "home")

            first = resolve_target_project(repo, kit_home=ROOT, environment=environment)
            second = resolve_target_project(sibling, kit_home=ROOT, environment=environment)

            self.assertNotEqual(first.root, second.root)
            self.assertNotEqual(first.worktree_slug, second.worktree_slug)


if __name__ == "__main__":
    unittest.main()
