#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import os
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.estimate import EstimateInput, help_requested, render_estimate
from agent_rails.models.presets import resolve_model
from agent_rails.models.tokenizer import TokenCount, TokenCounter, count_tokens


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

    def test_compatibility_shell_reads_the_python_preset_source(self) -> None:
        shell_module = ROOT / "scripts" / "agent-model-presets.sh"
        self.assertNotIn("case \"$model_key\"", shell_module.read_text(encoding="utf-8"))
        shell_script = r'''
source "$1"
agent_model_preset_load "$2"
printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s\n' \
  "$AGENT_RAILS_MODEL_CANONICAL" \
  "$AGENT_RAILS_MODEL_KNOWN" \
  "$AGENT_RAILS_MODEL_PRESET_FOUND" \
  "$AGENT_RAILS_MODEL_CONTEXT_TOKENS" \
  "$AGENT_RAILS_MODEL_MAX_INPUT_TOKENS" \
  "$AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS" \
  "$AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS" \
  "$AGENT_RAILS_MODEL_MAX_REASONING_TOKENS" \
  "$AGENT_RAILS_MODEL_RPM" \
  "$AGENT_RAILS_MODEL_TPM" \
  "$AGENT_RAILS_MODEL_LITE_TOKENS" \
  "$AGENT_RAILS_MODEL_NORMAL_TOKENS" \
  "$AGENT_RAILS_MODEL_DEEP_TOKENS:$AGENT_RAILS_MODEL_AUDIT_TOKENS"
'''
        for model_name in ("qwen-3.7-max", "deepseekv4pro", "glm51", "generic", "custom-model"):
            shell_values = subprocess.run(
                ["bash", "-c", shell_script, "bash", str(shell_module), model_name],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            resolved = resolve_model(model_name)
            preset = resolved.preset
            python_values = "|".join(
                [
                    resolved.canonical,
                    "1" if resolved.known else "0",
                    "1" if preset is not None else "0",
                    "" if preset is None else str(preset.context_tokens),
                    "" if preset is None else str(preset.max_input_tokens),
                    "" if preset is None or preset.max_input_thinking_tokens is None else str(preset.max_input_thinking_tokens),
                    "" if preset is None else str(preset.max_output_tokens),
                    "" if preset is None or preset.max_reasoning_tokens is None else str(preset.max_reasoning_tokens),
                    "" if preset is None or preset.rpm is None else str(preset.rpm),
                    "" if preset is None or preset.tpm is None else str(preset.tpm),
                    "" if preset is None else str(preset.pack_budgets["lite"]),
                    "" if preset is None else str(preset.pack_budgets["normal"]),
                    ":" if preset is None else f'{preset.pack_budgets["deep"]}:{preset.pack_budgets["audit"]}',
                ]
            )
            self.assertEqual(shell_values, python_values, model_name)

    def test_compatibility_shell_quotes_unknown_model_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-model-quote-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist"
            shell_script = r'''
source "$1"
agent_model_preset_load "$2"
printf '%s\n' "$AGENT_RAILS_MODEL_CANONICAL"
'''
            model_name = f"custom-$(touch {shlex.quote(str(marker))})"
            output = subprocess.run(
                [
                    "bash",
                    "-c",
                    shell_script,
                    "bash",
                    str(ROOT / "scripts" / "agent-model-presets.sh"),
                    model_name,
                ],
                env={**os.environ, "AGENT_RAILS_HOME": str(ROOT)},
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            self.assertEqual(output, model_name)
            self.assertFalse(marker.exists())


class TokenCounterTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
