#!/usr/bin/env python3

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.config.profile import load_shell_profile
from agent_rails.estimate import EstimateInput, help_requested, main, render_estimate
from agent_rails.models.presets import resolve_model
from agent_rails.models.tokenizer import TokenCount, TokenCounter, count_tokens


ESTIMATE_PROFILE_VARIABLES = {
    "AGENT_RAILS_MODEL",
    "AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE",
    "AGENT_RAILS_TOKENIZER",
    "AGENT_RAILS_TOKENIZER_CMD",
    "AGENT_RAILS_TOKENIZER_PATH",
    "AGENT_RAILS_TIKTOKEN_ENCODING",
}


class ModelPresetTest(unittest.TestCase):
    def test_aliases_and_pack_budgets_match_existing_contract(self) -> None:
        qwen = resolve_model("QWEN_3.7_MAX")
        self.assertEqual(qwen.canonical, "qwen3.7-max")
        self.assertEqual(qwen.budget_for_mode("deep"), 160_000)

        generic = resolve_model("generic")
        self.assertTrue(generic.known)
        self.assertIsNone(generic.preset)

        unknown = resolve_model("custom-model")
        self.assertFalse(unknown.known)
        self.assertEqual(unknown.canonical, "custom-model")

class TokenCounterTest(unittest.TestCase):
    def test_command_adapter_runs_in_explicit_target_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-tokenizer-test-") as temp_dir:
            working_directory = Path(temp_dir) / "project"
            working_directory.mkdir()
            command = (
                f'test "$PWD" = {shlex.quote(str(working_directory.resolve()))} '
                "&& printf 7"
            )
            counter = TokenCounter(
                "command",
                2,
                command=command,
                working_directory=working_directory,
            )

            self.assertEqual(counter.count("cwd")[0], 7)

    def test_char_and_command_adapters_share_one_interface(self) -> None:
        char_count = count_tokens("abcdef", "char", 2)
        self.assertEqual(char_count, TokenCount(tokens=3, tokenizer="char-estimate"))

        command_count = count_tokens("abcdef", "command", 2, command="printf 42")
        self.assertEqual(command_count, TokenCount(tokens=42, tokenizer="command"))

    def test_auto_failover_clears_counts_from_previous_tokenizer(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-tokenizer-test-") as temp_dir:
            counter_script = Path(temp_dir) / "counter.py"
            counter_script.write_text(
                """#!/usr/bin/env python3
import os
from pathlib import Path
import sys

text = Path(os.environ["AGENT_RAILS_TOKENIZER_INPUT"]).read_text(encoding="utf-8")
if text == "fail":
    sys.exit(1)
print(7)
""",
                encoding="utf-8",
            )
            counter_script.chmod(0o755)
            counter = TokenCounter("auto", 2, command=shlex.quote(str(counter_script)))

            self.assertEqual(counter.count("ok")[0], 7)
            self.assertEqual(counter.count("fail")[0], 2)
            self.assertEqual(counter.effective_mode, "char-estimate")
            self.assertEqual(counter.count("ok")[0], 1)


class EstimateRenderTest(unittest.TestCase):
    def test_help_marker_is_not_misread_as_an_option_value(self) -> None:
        self.assertTrue(help_requested(["--help"]))
        self.assertFalse(help_requested(["--tokenizer-command", "--help", "abc"]))

    def test_render_preserves_public_output(self) -> None:
        output = render_estimate(
            EstimateInput("arguments", "abcdefghijkl", 12, 12),
            TokenCount(tokens=6, tokenizer="char-estimate"),
            resolve_model("glm5.1"),
            2,
        )
        self.assertEqual(
            output,
            """Agent Rails Estimate

Source: arguments
Characters: 12
Bytes: 12
Tokenizer: char-estimate
Chars/token estimate: 2
Estimated tokens: 6
Model: glm5.1 (preset)
Context: 202000 tokens (0.00% used)
Max input: 202000 tokens (0.00% used)
Max input in thinking mode: 166000 tokens
Max output: 128000 tokens
""",
        )


class EstimateMainProfileTest(unittest.TestCase):
    def run_main(
        self,
        argv: list[str],
        *,
        environment: dict[str, str],
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with (
            mock.patch.dict(os.environ, environment, clear=True),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            status = main(argv)
        return status, stdout.getvalue(), stderr.getvalue()

    def environment(self, kit_home: Path) -> dict[str, str]:
        return {
            "AGENT_RAILS_HOME": str(kit_home),
            "HOME": str(kit_home / "home"),
            "PATH": "/usr/bin:/bin",
        }

    def write_profile(self, path: Path, lines: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_explicit_shell_profile_crosses_only_estimate_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-profile-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            profile = Path(temp_dir) / "estimate.profile"
            self.write_profile(
                profile,
                [
                    'AGENT_RAILS_MODEL="qwen3.7-max"',
                    'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="3"',
                    'AGENT_RAILS_TOKENIZER="command"',
                    'AGENT_RAILS_TOKENIZER_CMD="printf 17"',
                    'AGENT_RAILS_TOKENIZER_PATH="profile-tokenizer"',
                    'AGENT_RAILS_TIKTOKEN_ENCODING="profile-encoding"',
                    'export AGENT_RAILS_PACK_MODE="audit"',
                    'export ESTIMATE_PROFILE_SECRET="must-not-cross"',
                ],
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            environment = self.environment(kit_home)
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch(
                    "agent_rails.estimate.load_shell_profile",
                    wraps=load_shell_profile,
                ) as profile_loader,
                mock.patch(
                    "agent_rails.estimate.count_tokens",
                    return_value=TokenCount(tokens=17, tokenizer="command"),
                ) as token_counter,
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                status = main(["--profile", str(profile), "abcdef"])
                self.assertNotIn("AGENT_RAILS_PACK_MODE", os.environ)
                self.assertNotIn("ESTIMATE_PROFILE_SECRET", os.environ)

            self.assertEqual(status, 0)
            self.assertEqual(stderr.getvalue(), "")
            self.assertIn("Model: qwen3.7-max (preset)\n", stdout.getvalue())
            token_counter.assert_called_once_with(
                "abcdef",
                "command",
                3,
                "printf 17",
                "profile-tokenizer",
                "profile-encoding",
            )
            self.assertEqual(profile_loader.call_count, 1)
            self.assertEqual(
                set(profile_loader.call_args.kwargs["variables"]),
                ESTIMATE_PROFILE_VARIABLES,
            )
            self.assertFalse(
                profile_loader.call_args.kwargs.get(
                    "capture_exported_environment", False
                )
            )

    def test_cli_options_override_explicit_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-profile-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            profile = Path(temp_dir) / "estimate.profile"
            self.write_profile(
                profile,
                [
                    'AGENT_RAILS_MODEL="qwen3.7-max"',
                    'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="9"',
                    'AGENT_RAILS_TOKENIZER="command"',
                    'AGENT_RAILS_TOKENIZER_CMD="printf 99"',
                    'AGENT_RAILS_TOKENIZER_PATH="profile-tokenizer"',
                    'AGENT_RAILS_TIKTOKEN_ENCODING="profile-encoding"',
                ],
            )
            with mock.patch(
                "agent_rails.estimate.count_tokens",
                return_value=TokenCount(tokens=3, tokenizer="char-estimate"),
            ) as token_counter:
                status, stdout, stderr = self.run_main(
                    [
                        "--profile",
                        str(profile),
                        "--model",
                        "glm5.1",
                        "--chars-per-token",
                        "2",
                        "--tokenizer",
                        "char",
                        "--tokenizer-command",
                        "printf 44",
                        "--tokenizer-path",
                        "cli-tokenizer",
                        "abcdef",
                    ],
                    environment=self.environment(kit_home),
                )

            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Model: glm5.1 (preset)\n", stdout)
            token_counter.assert_called_once_with(
                "abcdef",
                "char",
                2,
                "printf 44",
                "cli-tokenizer",
                "profile-encoding",
            )

    def test_default_kit_profile_is_loaded_without_profile_option(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-default-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            self.write_profile(
                kit_home / "profiles" / "default.profile",
                [
                    'AGENT_RAILS_MODEL="qwen3.7-max"',
                    'AGENT_RAILS_TOKENIZER="char"',
                    'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="3"',
                ],
            )

            status, stdout, stderr = self.run_main(
                ["abcdef"], environment=self.environment(kit_home)
            )

            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Estimated tokens: 2\n", stdout)
            self.assertIn("Model: qwen3.7-max (preset)\n", stdout)

    def test_help_does_not_execute_profile(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-help-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            marker = Path(temp_dir) / "profile-executed"
            profile = Path(temp_dir) / "estimate.profile"
            self.write_profile(
                profile,
                [
                    f"touch {shlex.quote(str(marker))}",
                    "exit 19",
                ],
            )

            status, stdout, stderr = self.run_main(
                ["--profile", str(profile), "--help"],
                environment=self.environment(kit_home),
            )

            self.assertEqual(status, 0)
            self.assertTrue(stdout.startswith("Usage: agent-rails estimate "))
            self.assertEqual(stderr, "")
            self.assertFalse(marker.exists())

    def test_missing_explicit_and_default_profiles_are_silent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-missing-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            cases = (
                ["--profile", str(Path(temp_dir) / "missing.profile")],
                [],
            )
            for profile_args in cases:
                with self.subTest(profile_args=profile_args):
                    status, stdout, stderr = self.run_main(
                        [
                            *profile_args,
                            "--tokenizer",
                            "char",
                            "--chars-per-token",
                            "2",
                            "abcd",
                        ],
                        environment=self.environment(kit_home),
                    )
                    self.assertEqual(status, 0)
                    self.assertEqual(stderr, "")
                    self.assertIn("Estimated tokens: 2\n", stdout)
                    self.assertIn("Model: generic (no preset)\n", stdout)

    def test_profile_source_failure_returns_exact_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-failure-") as temp_dir:
            kit_home = Path(temp_dir) / "kit"
            profile = Path(temp_dir) / "broken.profile"
            self.write_profile(profile, ["false"])

            status, stdout, stderr = self.run_main(
                ["--profile", str(profile), "abc"],
                environment=self.environment(kit_home),
            )

            self.assertEqual(status, 2)
            self.assertEqual(stdout, "")
            self.assertEqual(
                stderr,
                f"Profile could not be sourced: {profile}\n",
            )

    def test_profile_loading_preserves_caller_cwd_for_relative_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-estimate-cwd-") as temp_dir:
            root = Path(os.path.realpath(temp_dir))
            kit_home = root / "kit"
            caller = root / "caller"
            caller.mkdir()
            (caller / "input.md").write_text("relative", encoding="utf-8")
            profile = root / "estimate.profile"
            self.write_profile(
                profile,
                [
                    f'test "$PWD" = {shlex.quote(str(caller))}',
                    'AGENT_RAILS_MODEL="qwen3.7-max"',
                    'AGENT_RAILS_TOKENIZER="char"',
                    'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="1"',
                ],
            )
            previous_cwd = Path.cwd()
            try:
                os.chdir(caller)
                status, stdout, stderr = self.run_main(
                    ["--profile", str(profile), "--file", "input.md"],
                    environment=self.environment(kit_home),
                )
            finally:
                os.chdir(previous_cwd)

            self.assertEqual(status, 0)
            self.assertEqual(stderr, "")
            self.assertIn("Source: file: input.md\n", stdout)
            self.assertIn("Estimated tokens: 8\n", stdout)
            self.assertIn("Model: qwen3.7-max (preset)\n", stdout)


if __name__ == "__main__":
    unittest.main()
