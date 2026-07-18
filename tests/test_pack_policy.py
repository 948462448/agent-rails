#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.context.pack_policy import PackPolicyInput, resolve_pack_policy


class PackPolicyTest(unittest.TestCase):
    def test_model_preset_selects_token_budget_and_density_caps(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(
                model="glm-5.1",
                pack_mode="deep",
                local_memory_card_chars="5000",
                changed_file_excerpt_limit="30",
                changed_file_excerpt_chars="9000",
            )
        )

        self.assertEqual(policy.model.canonical, "glm5.1")
        self.assertEqual(policy.budget.effective_tokens, 60_000)
        self.assertEqual(policy.budget.total_chars, 120_000)
        self.assertEqual(policy.budget.source, "model preset")
        self.assertTrue(policy.budget.token_allocator_active)
        self.assertEqual(policy.density.changed_file_excerpt_limit, 8)
        self.assertEqual(policy.density.changed_file_excerpt_chars, 2200)
        self.assertEqual(policy.density.local_memory_card_chars, 1400)

    def test_explicit_char_budget_precedes_token_budget_and_splits_sections(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(
                context_budget_chars="1000",
                context_budget_tokens="900",
                git_percent="21",
                memory_percent="39",
            )
        )

        self.assertEqual(policy.budget.source, "char budget")
        self.assertEqual(policy.budget.effective_tokens, 500)
        self.assertFalse(policy.budget.token_allocator_active)
        self.assertEqual(policy.budget.git_chars, 210)
        self.assertEqual(policy.budget.changed_files_chars, 105)
        self.assertEqual(policy.budget.status_chars, 105)
        self.assertEqual(policy.budget.memory_chars, 390)

    def test_explicit_token_budget_activates_hard_allocator(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(context_budget_tokens="321", chars_per_token="3")
        )

        self.assertEqual(policy.budget.effective_tokens, 321)
        self.assertEqual(policy.budget.total_chars, 963)
        self.assertEqual(policy.budget.source, "token budget")
        self.assertTrue(policy.budget.token_allocator_active)
        self.assertEqual(policy.budget.git_chars, 0)

    def test_candidate_output_disables_all_static_budgets(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(
                model="qwen3.7-max",
                pack_mode="audit",
                candidate_output="1",
            )
        )

        self.assertTrue(policy.budget.candidate_output_active)
        self.assertFalse(policy.budget.token_allocator_active)
        self.assertIsNone(policy.budget.effective_tokens)
        self.assertEqual(policy.budget.total_chars, 0)
        self.assertEqual(policy.budget.source, "request-hook candidate output")
        self.assertEqual(policy.density.changed_file_excerpt_limit, 8)

    def test_invalid_profile_values_keep_existing_fallbacks(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(
                model="custom-model",
                pack_mode="unexpected",
                chars_per_token="0",
                git_percent="101",
                memory_percent="-1",
                changed_file_sort="random",
                changed_file_excerpt_limit="0",
                grill_max_questions="0",
            )
        )

        self.assertFalse(policy.model.known)
        self.assertEqual(policy.density.mode, "normal")
        self.assertEqual(policy.budget.chars_per_token, 2)
        self.assertEqual(policy.budget.git_percent, 20)
        self.assertEqual(policy.budget.memory_percent, 40)
        self.assertEqual(policy.density.changed_file_sort, "smart")
        self.assertEqual(policy.density.changed_file_excerpt_limit, 0)
        self.assertEqual(policy.density.grill_max_questions, 0)

    def test_shell_values_cover_the_existing_pack_contract(self) -> None:
        values = resolve_pack_policy(
            PackPolicyInput(model="deepseekv4pro", pack_mode="lite")
        ).shell_values()

        self.assertEqual(values["AGENT_RAILS_MODEL_CANONICAL"], "deepseek-v4-pro")
        self.assertEqual(values["AGENT_RAILS_MODEL_RPM"], "15000")
        self.assertEqual(values["AGENT_RAILS_CONTEXT_BUDGET_TOKENS_EFFECTIVE"], "24000")
        self.assertEqual(values["AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS"], "900")
        self.assertEqual(values["git_budget_chars"], "0")

    def test_deepseek_flash_keeps_its_name_and_uses_pro_limits(self) -> None:
        policy = resolve_pack_policy(
            PackPolicyInput(model="deepseekv4flash", pack_mode="deep")
        )

        self.assertEqual(policy.model.canonical, "deepseek-v4-flash")
        self.assertEqual(policy.model.preset.context_tokens, 1_000_000)
        self.assertEqual(policy.model.preset.max_output_tokens, 384_000)
        self.assertEqual(policy.budget.effective_tokens, 160_000)
        self.assertEqual(policy.budget.source, "model preset")

    def test_cli_shell_output_quotes_untrusted_model_names(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-pack-policy-") as temp_dir:
            marker = Path(temp_dir) / "must-not-exist"
            model = f"custom-$(touch {shlex.quote(str(marker))})"
            assignments = subprocess.run(
                [
                    sys.executable,
                    "-E",
                    str(ROOT / "scripts" / "agent-python-cli.py"),
                    "pack-policy",
                    f"--model={model}",
                    "--shell",
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            canonical = subprocess.run(
                [
                    "bash",
                    "-c",
                    'eval "$1"; printf "%s" "$AGENT_RAILS_MODEL_CANONICAL"',
                    "bash",
                    assignments,
                ],
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            self.assertEqual(canonical, model)
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
