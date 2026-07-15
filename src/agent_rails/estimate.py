from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from dataclasses import dataclass
from typing import Sequence

from .models.presets import ResolvedModel, resolve_model
from .models.tokenizer import TokenCount, TokenizerSelectionError, count_tokens


USAGE = """Usage: agent-rails estimate [--profile PATH] [--model NAME] [--tokenizer auto|char|tiktoken|command|huggingface] [--tokenizer-command CMD] [--tokenizer-path PATH] [--chars-per-token N] [--file PATH] [text...]

Examples:
  agent-rails estimate --model qwen3.7-max --file ~/.agent-rails/agent-context/project-task-pack.md
  agent-rails estimate --tokenizer tiktoken --file ~/.agent-rails/agent-context/project-task-pack.md
  agent-rails estimate --tokenizer-command 'my-token-counter "$AGENT_RAILS_TOKENIZER_INPUT"' --file pack.md

Use --tokenizer command for exact Qwen/GLM tokenizers when a local tokenizer command is available.
Without a tokenizer dependency, auto falls back to a character estimate.
"""


class EstimateArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        del message
        print(USAGE, end="", file=sys.stderr)
        raise SystemExit(2)


@dataclass(frozen=True)
class EstimateInput:
    source: str
    text: str
    characters: int
    bytes_count: int


def build_parser() -> argparse.ArgumentParser:
    parser = EstimateArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--profile")
    parser.add_argument("--model")
    parser.add_argument("--tokenizer")
    parser.add_argument("--tokenizer-command")
    parser.add_argument("--tokenizer-path")
    parser.add_argument("--chars-per-token")
    parser.add_argument("--file")
    parser.add_argument("text", nargs="*")
    return parser


def help_requested(args: Sequence[str]) -> bool:
    options_with_values = {
        "--profile",
        "--model",
        "--tokenizer",
        "--tokenizer-command",
        "--tokenizer-path",
        "--chars-per-token",
        "--file",
    }
    index = 0
    while index < len(args):
        value = args[index]
        if value in options_with_values:
            index += 2
            continue
        if value in {"--help", "-h"}:
            return True
        index += 1
    return False


def normalize_positive_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def read_input(input_file: str | None, text_parts: Sequence[str]) -> EstimateInput:
    if input_file:
        path = Path(input_file)
        if not path.is_file():
            raise FileNotFoundError(input_file)
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        return EstimateInput(f"file: {input_file}", text, len(text), len(raw))

    if text_parts:
        text = " ".join(text_parts)
        raw = text.encode("utf-8")
        return EstimateInput("arguments", text, len(text), len(raw))

    raw = sys.stdin.buffer.read()
    text = raw.decode("utf-8", errors="replace")
    return EstimateInput("stdin", text, len(text), len(raw))


def format_percent(tokens: int, limit: int) -> str:
    value = tokens * 10_000 // limit
    return f"{value // 100}.{value % 100:02d}%"


def render_estimate(
    input_value: EstimateInput,
    token_count: TokenCount,
    model: ResolvedModel,
    chars_per_token: int,
) -> str:
    lines = [
        "Agent Rails Estimate",
        "",
        f"Source: {input_value.source}",
        f"Characters: {input_value.characters}",
        f"Bytes: {input_value.bytes_count}",
        f"Tokenizer: {token_count.tokenizer}",
    ]
    if token_count.tokenizer == "char-estimate":
        lines.append(f"Chars/token estimate: {chars_per_token}")
    lines.append(f"Estimated tokens: {token_count.tokens}")

    preset = model.preset
    if preset is None:
        lines.append(f"Model: {model.canonical} (no preset)")
        return "\n".join(lines) + "\n"

    lines.extend(
        [
            f"Model: {preset.canonical} (preset)",
            f"Context: {preset.context_tokens} tokens ({format_percent(token_count.tokens, preset.context_tokens)} used)",
            f"Max input: {preset.max_input_tokens} tokens ({format_percent(token_count.tokens, preset.max_input_tokens)} used)",
        ]
    )
    if preset.max_input_thinking_tokens is not None:
        lines.append(f"Max input in thinking mode: {preset.max_input_thinking_tokens} tokens")
    lines.append(f"Max output: {preset.max_output_tokens} tokens")
    if preset.max_reasoning_tokens is not None:
        lines.append(f"Max reasoning: {preset.max_reasoning_tokens} tokens")
    if preset.rpm is not None:
        lines.append(f"RPM: {preset.rpm}")
    if preset.tpm is not None:
        lines.append(f"TPM: {preset.tpm}")
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if help_requested(args_list):
        print(USAGE, end="")
        return 0
    args = build_parser().parse_args(args_list)

    model_name = args.model or os.environ.get("AGENT_RAILS_MODEL") or "generic"
    chars_per_token = normalize_positive_int(
        args.chars_per_token or os.environ.get("AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE", "2"),
        2,
    )
    tokenizer = args.tokenizer or os.environ.get("AGENT_RAILS_TOKENIZER") or "auto"
    tokenizer_command = args.tokenizer_command or os.environ.get("AGENT_RAILS_TOKENIZER_CMD", "")
    tokenizer_path = args.tokenizer_path or os.environ.get("AGENT_RAILS_TOKENIZER_PATH", "")
    tiktoken_encoding = os.environ.get("AGENT_RAILS_TIKTOKEN_ENCODING", "cl100k_base")

    try:
        input_value = read_input(args.file, args.text)
    except FileNotFoundError:
        print(f"Input file not found: {args.file}", file=sys.stderr)
        return 2

    try:
        token_count = count_tokens(
            input_value.text,
            tokenizer,
            chars_per_token,
            tokenizer_command,
            tokenizer_path,
            tiktoken_encoding,
        )
    except TokenizerSelectionError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code

    sys.stdout.write(render_estimate(input_value, token_count, resolve_model(model_name), chars_per_token))
    return 0
