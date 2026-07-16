#!/usr/bin/env python3

from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from typing import Dict, Optional
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.context.pack_application import (  # noqa: E402
    OutputTarget,
    PackApplicationResult,
    PackCliOverrides,
)
from agent_rails.context.pack_renderer import (  # noqa: E402
    PackRendererError,
    TaskPackRenderResult,
    TokenizerSettings,
)
from agent_rails.context.pack_policy import (  # noqa: E402
    PackPolicyInput,
    resolve_pack_policy,
)
from agent_rails.run_application import (  # noqa: E402
    RunApplicationError,
    RunApplicationRequest,
    RunCliOverrides,
    RunEventStream,
    RunInputError,
    RunMode,
    run_agent_rails,
)
import agent_rails.run_application as run_module  # noqa: E402


def _git(project: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(project), *arguments),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()


def _quoted_command(*arguments: str) -> str:
    """Match the public Run facade's stable quote-every-argument rendering."""

    return " ".join("'" + value.replace("'", "'\\''") + "'" for value in arguments)


class RunApplicationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="agent-rails-run-application-"
        )
        root = Path(self.temporary.name)
        self.working_directory = root / "workspace"
        self.project = self.working_directory / "repo"
        self.nested_project_path = self.project / "nested" / "path"
        self.kit_home = root / "kit home"
        self.user_home = root / "user"
        self.config_home = root / "config home"
        self.profile = self.project / "run profile.sh"
        self.environment_file = self.project / "run.env"
        self.profile_count = root / "profile-count"
        self.environment_count = root / "environment-count"
        self.output = self.project / "state" / "task pack.md"

        for path in (
            self.nested_project_path,
            self.kit_home / "bin",
            self.user_home,
            self.config_home,
        ):
            path.mkdir(parents=True, exist_ok=True)
        executable = self.kit_home / "bin" / "agent-rails"
        executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

        _git(self.project, "init", "-q")
        _git(self.project, "config", "user.name", "Agent Rails Tests")
        _git(
            self.project,
            "config",
            "user.email",
            "agent-rails-tests@example.invalid",
        )
        (self.project / "README.md").write_text("# fixture\n", encoding="utf-8")
        _git(self.project, "add", "README.md")
        _git(self.project, "commit", "-qm", "fixture")
        (self.project / "README.md").write_text(
            "# fixture\n\nchanged\n", encoding="utf-8"
        )

        self.profile.write_text(
            "\n".join(
                (
                    "count=0",
                    f'[[ ! -f "{self.profile_count}" ]] || count="$(cat "{self.profile_count}")"',
                    f'printf "%s\\n" "$((count + 1))" > "{self.profile_count}"',
                    'AGENT_RAILS_ENV_FILE="run.env"',
                    'PROJECT_NAME="run-fixture"',
                    'TASK_PACK_PATH="state/task pack.md"',
                    'AGENT_RAILS_MODEL="generic"',
                    'AGENT_RAILS_PACK_MODE="normal"',
                    'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="4"',
                    'AGENT_RAILS_TOKENIZER="char"',
                    'MEMORY_PROVIDER="local"',
                    'MEMORY_LOCAL_DIR="memory"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.environment_file.write_text(
            "\n".join(
                (
                    "count=0",
                    f'[[ ! -f "{self.environment_count}" ]] || count="$(cat "{self.environment_count}")"',
                    f'printf "%s\\n" "$((count + 1))" > "{self.environment_count}"',
                    'AGENT_RAILS_MODEL="glm5.1"',
                    "",
                )
            ),
            encoding="utf-8",
        )
        self.environment: Dict[str, str] = dict(os.environ)
        self.environment.update(
            {
                "HOME": str(self.user_home),
                "AGENT_RAILS_CONFIG_HOME": str(self.config_home),
            }
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def request(
        self,
        *,
        mode: RunMode = RunMode.PRINT_ONLY,
        requested_project: Optional[Path] = None,
        explicit_profile: Optional[str] = None,
        goal: str = "run loop",
        overrides: Optional[RunCliOverrides] = None,
    ) -> RunApplicationRequest:
        return RunApplicationRequest(
            requested_project=(
                self.project if requested_project is None else requested_project
            ),
            kit_home=self.kit_home,
            explicit_profile=(
                str(self.profile) if explicit_profile is None else explicit_profile
            ),
            goal=goal,
            overrides=(
                RunCliOverrides(mode=mode) if overrides is None else overrides
            ),
            working_directory=self.working_directory,
            environment=self.environment,
        )

    def test_typed_request_rejects_invalid_mode_pack_mode_and_paths(self) -> None:
        request = self.request()

        with self.assertRaises(RunInputError) as invalid_mode:
            run_agent_rails(
                replace(
                    request,
                    overrides=replace(request.overrides, mode="print-only"),  # type: ignore[arg-type]
                )
            )
        self.assertEqual(invalid_mode.exception.exit_code, 2)

        with self.assertRaisesRegex(RunInputError, "pack mode"):
            run_agent_rails(
                replace(
                    request,
                    overrides=replace(request.overrides, pack_mode="wide"),
                )
            )

        with self.assertRaises(RunInputError) as missing_project:
            run_agent_rails(
                replace(request, requested_project=Path("missing-project"))
            )
        self.assertEqual(missing_project.exception.exit_code, 2)

        with self.assertRaises(RunInputError) as missing_profile:
            run_agent_rails(
                replace(request, explicit_profile="missing-profile.sh")
            )
        self.assertEqual(missing_profile.exception.exit_code, 2)

    def test_print_only_resolves_relative_paths_once_and_renders_exact_commands(self) -> None:
        goal = "review Bob's task pack"
        overrides = RunCliOverrides(
            mode=RunMode.PRINT_ONLY,
            model="qwen3.7-max",
            pack_mode="audit",
            context_budget_chars="48000",
            context_budget_tokens="1200",
            tokenizer="command",
            tokenizer_command="printf 42",
            tokenizer_path="tokenizers/local path",
        )

        result = run_agent_rails(
            self.request(
                requested_project=Path("repo/nested/path"),
                explicit_profile="repo/run profile.sh",
                goal=goal,
                overrides=overrides,
            )
        )

        self.assertEqual(self.profile_count.read_text(encoding="utf-8"), "1\n")
        self.assertEqual(
            self.environment_count.read_text(encoding="utf-8"), "1\n"
        )
        self.assertFalse(self.output.exists())
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.project_root, self.project.resolve())
        self.assertEqual(result.profile_path, str(self.profile.resolve()))
        self.assertEqual(result.task_pack_path, "state/task pack.md")
        self.assertEqual(result.effective_pack_mode, "audit")
        self.assertIsNone(result.inferred_pack_mode)
        self.assertIsNone(result.pack_result)
        self.assertEqual(result.stderr, "")
        self.assertTrue(result.events)
        self.assertTrue(
            all(event.stream is RunEventStream.STDOUT for event in result.events)
        )

        pack_command = _quoted_command(
            str(self.kit_home / "bin/agent-rails"),
            "pack",
            "--project",
            str(self.project.resolve()),
            "--profile",
            str(self.profile.resolve()),
            "--model",
            "qwen3.7-max",
            "--pack-mode",
            "audit",
            "--budget",
            "48000",
            "--token-budget",
            "1200",
            "--tokenizer",
            "command",
            "--tokenizer-command",
            "printf 42",
            "--tokenizer-path",
            "tokenizers/local path",
            goal,
        )
        estimate_command = _quoted_command(
            str(self.kit_home / "bin/agent-rails"),
            "estimate",
            "--profile",
            str(self.profile.resolve()),
            "--model",
            "qwen3.7-max",
            "--tokenizer",
            "command",
            "--tokenizer-command",
            "printf 42",
            "--tokenizer-path",
            "tokenizers/local path",
            "--file",
            str(self.output.resolve()),
        )
        self.assertIn(f"- Pack: {pack_command}\n", result.stdout)
        self.assertIn(f"- Estimate: {estimate_command}\n", result.stdout)
        self.assertIn("Print-only mode. No files written.\n", result.stdout)

    def test_goal_inference_and_placeholder_preserve_public_precedence(self) -> None:
        cases = (
            ("重构 current module", None, "deep", "deep"),
            ("Trajectory Eval POC deploy prep", None, "lite", "lite"),
            ("全面review current branch", None, "audit", "audit"),
            ("全面review current branch", "normal", "normal", None),
            ("", None, "normal", None),
        )
        for goal, explicit, effective, inferred in cases:
            with self.subTest(goal=goal, explicit=explicit):
                result = run_agent_rails(
                    self.request(
                        goal=goal,
                        overrides=RunCliOverrides(
                            mode=RunMode.PRINT_ONLY,
                            pack_mode=explicit,
                        ),
                    )
                )
                self.assertEqual(result.effective_pack_mode, effective)
                self.assertEqual(result.inferred_pack_mode, inferred)
                if inferred is not None:
                    self.assertIn(
                        f"Inferred pack mode: {inferred}\n", result.stdout
                    )
                else:
                    self.assertNotIn("Inferred pack mode:", result.stdout)
                if not goal:
                    self.assertIn(
                        "Goal: TODO: describe the concrete user goal.\n",
                        result.stdout,
                    )
        self.assertFalse(self.output.exists())

    def test_execute_loads_profile_and_environment_once_and_stays_in_process(self) -> None:
        result = run_agent_rails(
            self.request(
                mode=RunMode.EXECUTE,
                goal="重构 current module",
                overrides=RunCliOverrides(mode=RunMode.EXECUTE),
            )
        )

        self.assertEqual(self.profile_count.read_text(encoding="utf-8"), "1\n")
        self.assertEqual(
            self.environment_count.read_text(encoding="utf-8"), "1\n"
        )
        self.assertTrue(self.output.is_file())
        self.assertEqual(stat.S_IMODE(self.output.stat().st_mode), 0o600)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.effective_pack_mode, "deep")
        self.assertEqual(result.inferred_pack_mode, "deep")
        self.assertIsNotNone(result.pack_result)
        self.assertIn("Wrote state/task pack.md\n", result.stdout)
        self.assertIn("Agent Rails Estimate\n", result.stdout)
        self.assertIn("Tokenizer: char-estimate\n", result.stdout)
        self.assertIn("Model: glm5.1 (preset)\n", result.stdout)
        self.assertIn("Agent Instructions\n", result.stdout)
        self.assertIn(
            "Tell the user: AGENT RAILS: ON (mode=deep, pack=state/task pack.md)",
            result.stdout,
        )

    def test_execute_delegates_exactly_once_to_task_pack_application(self) -> None:
        content = "# Agent Task Pack\n\nfixture\n"
        fake_pack = PackApplicationResult(
            project_root=self.project.resolve(),
            profile_path=self.profile,
            output=OutputTarget(
                display_path="state/task pack.md",
                filesystem_path=self.output,
            ),
            pack_mode="normal",
            resolved_target_sha=None,
            changed_paths=("README.md",),
            verification_fallback_used=False,
            policy=resolve_pack_policy(
                PackPolicyInput(model="generic", pack_mode="normal")
            ),
            tokenizer=TokenizerSettings(mode="char"),
            render_result=TaskPackRenderResult(
                content=content,
                assembler_metadata=None,
            ),
        )
        overrides = RunCliOverrides(
            mode=RunMode.EXECUTE,
            model="generic",
            pack_mode="normal",
            context_budget_chars="9000",
            context_budget_tokens="7000",
            tokenizer="char",
            tokenizer_command="ignored",
            tokenizer_path="ignored-path",
        )

        with patch.object(
            run_module, "generate_task_pack", return_value=fake_pack
        ) as generate:
            result = run_agent_rails(
                self.request(goal="exact delegation", overrides=overrides)
            )

        generate.assert_called_once()
        delegated = generate.call_args.args[0]
        self.assertEqual(delegated.requested_project, self.project.resolve())
        self.assertEqual(delegated.kit_home, self.kit_home.resolve())
        self.assertEqual(delegated.explicit_profile, str(self.profile))
        self.assertEqual(delegated.goal, "exact delegation")
        self.assertEqual(
            delegated.overrides,
            PackCliOverrides(
                model="generic",
                pack_mode="normal",
                context_budget_chars="9000",
                context_budget_tokens="7000",
                tokenizer="char",
                tokenizer_command="ignored",
                tokenizer_path="ignored-path",
            ),
        )
        self.assertIs(result.pack_result, fake_pack)
        self.assertIn("Agent Rails Estimate\n", result.stdout)
        self.assertIn(f"Characters: {len(content)}\n", result.stdout)
        self.assertFalse(self.output.exists())

    def test_execute_estimate_uses_exported_profile_environment(self) -> None:
        with self.environment_file.open("a", encoding="utf-8") as handle:
            handle.write('export RUN_TOKENIZER_SENTINEL="loaded"\n')

        result = run_agent_rails(
            self.request(
                mode=RunMode.EXECUTE,
                overrides=RunCliOverrides(
                    mode=RunMode.EXECUTE,
                    tokenizer="command",
                    tokenizer_command=(
                        'test "$RUN_TOKENIZER_SENTINEL" = loaded && printf 7'
                    ),
                ),
            )
        )

        self.assertIn("Estimated tokens: 7\n", result.stdout)
        self.assertEqual(self.profile_count.read_text(encoding="utf-8"), "1\n")
        self.assertEqual(
            self.environment_count.read_text(encoding="utf-8"), "1\n"
        )

    def test_estimate_failure_preserves_completed_pack_events(self) -> None:
        content = "# Agent Task Pack\n\nfixture\n"
        fake_pack = PackApplicationResult(
            project_root=self.project.resolve(),
            profile_path=self.profile,
            output=OutputTarget("state/task pack.md", self.output),
            pack_mode="normal",
            resolved_target_sha=None,
            changed_paths=(),
            verification_fallback_used=False,
            policy=resolve_pack_policy(
                PackPolicyInput(model="generic", pack_mode="normal")
            ),
            tokenizer=TokenizerSettings(
                mode="command",
                command="opaque-secret-command --token do-not-render",
            ),
            render_result=TaskPackRenderResult(content, None),
        )

        with patch.object(
            run_module, "generate_task_pack", return_value=fake_pack
        ):
            with self.assertRaises(RunApplicationError) as raised:
                run_agent_rails(
                    self.request(
                        mode=RunMode.EXECUTE,
                        overrides=RunCliOverrides(mode=RunMode.EXECUTE),
                    )
                )

        error = raised.exception
        self.assertIs(error.pack_result, fake_pack)
        self.assertIn("Wrote state/task pack.md\n", error.stdout)
        self.assertIn("Tokenizer command failed", str(error))
        self.assertNotIn("opaque-secret-command", str(error))

    def test_relative_kit_home_is_anchored_to_working_directory(self) -> None:
        result = run_agent_rails(
            replace(
                self.request(),
                kit_home=Path("../kit home"),
            )
        )

        self.assertEqual(result.exit_code, 0)
        self.assertIn(str(self.kit_home / "bin/agent-rails"), result.stdout)

    def test_unexpected_pack_failure_stays_inside_application_boundary(self) -> None:
        with patch.object(
            run_module,
            "generate_task_pack",
            side_effect=RuntimeError("evidence collection failed"),
        ):
            with self.assertRaises(RunApplicationError) as raised:
                run_agent_rails(
                    self.request(
                        mode=RunMode.EXECUTE,
                        overrides=RunCliOverrides(mode=RunMode.EXECUTE),
                    )
                )

        self.assertEqual(raised.exception.exit_code, 1)
        self.assertIn("evidence collection failed", str(raised.exception))

    def test_runtime_pack_failure_is_not_misclassified_as_public_input(self) -> None:
        request = self.request(
            mode=RunMode.EXECUTE,
            overrides=RunCliOverrides(
                mode=RunMode.EXECUTE,
                tokenizer="char",
            ),
        )
        with patch.object(
            run_module,
            "generate_task_pack",
            side_effect=PackRendererError("atomic Task Pack publish failed"),
        ):
            with self.assertRaises(RunApplicationError) as raised:
                run_agent_rails(request)

        self.assertNotIsInstance(raised.exception, RunInputError)
        self.assertEqual(raised.exception.exit_code, 1)
        self.assertIn("atomic Task Pack publish failed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
