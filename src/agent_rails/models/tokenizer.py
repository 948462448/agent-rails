from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TokenCount:
    tokens: int
    tokenizer: str


class TokenizerSelectionError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class TokenCounter:
    """Count and truncate text through one replaceable Tokenizer Interface."""

    def __init__(
        self,
        mode: str,
        chars_per_token: int,
        command: str = "",
        tokenizer_path: str = "",
        tiktoken_encoding: str = "cl100k_base",
        working_directory: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.requested_mode = mode
        self.chars_per_token = max(1, chars_per_token)
        self.command = command
        self.tokenizer_path = tokenizer_path
        self.tiktoken_encoding = tiktoken_encoding
        self.working_directory = working_directory
        self.environment = None if environment is None else dict(environment)
        self._auto_mode = mode == "auto"
        self.cache: dict[tuple[str, str], int] = {}
        self.cache_hits = 0
        self.cache_misses = 0
        self._encoder: Any = None
        self.effective_mode = self._initialize_mode()

    def _initialize_mode(self) -> str:
        mode = self.requested_mode
        if mode == "auto":
            if self.tokenizer_path:
                try:
                    import transformers  # type: ignore

                    self._encoder = transformers.AutoTokenizer.from_pretrained(
                        self.tokenizer_path,
                        trust_remote_code=True,
                    )
                    return f"huggingface:{self.tokenizer_path}"
                except Exception:
                    pass
            if self.command:
                return "command"
            try:
                import tiktoken  # type: ignore

                self._encoder = tiktoken.get_encoding(self.tiktoken_encoding)
                return f"tiktoken:{self.tiktoken_encoding}"
            except Exception:
                return "char-estimate"

        if mode == "char":
            return "char-estimate"
        if mode == "command":
            if not self.command:
                raise ValueError("tokenizer command mode requires --tokenizer-command")
            return "command"
        if mode == "tiktoken":
            try:
                import tiktoken  # type: ignore
            except Exception as exc:
                raise ValueError("tiktoken tokenizer is unavailable") from exc
            self._encoder = tiktoken.get_encoding(self.tiktoken_encoding)
            return f"tiktoken:{self.tiktoken_encoding}"
        if mode in {"huggingface", "hf"}:
            if not self.tokenizer_path:
                raise ValueError("huggingface mode requires --tokenizer-path")
            try:
                import transformers  # type: ignore
            except Exception as exc:
                raise ValueError("transformers is required for a Hugging Face tokenizer") from exc
            self._encoder = transformers.AutoTokenizer.from_pretrained(
                self.tokenizer_path,
                trust_remote_code=True,
            )
            return f"huggingface:{self.tokenizer_path}"
        raise ValueError(f"unknown tokenizer mode: {mode}")

    def count(self, text: str) -> tuple[int, bool]:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        cache_key = (self.effective_mode, digest)
        if cache_key in self.cache:
            self.cache_hits += 1
            return self.cache[cache_key], True

        self.cache_misses += 1
        try:
            value = self._count_uncached(text)
        except Exception:
            if not self._auto_mode:
                raise
            self.effective_mode = "char-estimate"
            self._encoder = None
            self.cache.clear()
            cache_key = (self.effective_mode, digest)
            value = math.ceil(len(text) / self.chars_per_token)

        self.cache[cache_key] = value
        return value, False

    def _count_uncached(self, text: str) -> int:
        if self.effective_mode == "char-estimate":
            return math.ceil(len(text) / self.chars_per_token)
        if self.effective_mode == "command":
            return self._count_with_command(text)
        if self.effective_mode.startswith("tiktoken:"):
            return len(self._encoder.encode(text))
        if self.effective_mode.startswith("huggingface:"):
            return len(self._encoder.encode(text, add_special_tokens=False))
        raise RuntimeError(f"unsupported tokenizer: {self.effective_mode}")

    def _count_with_command(self, text: str) -> int:
        path = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                handle.write(text)
                path = handle.name
            env = (
                os.environ.copy()
                if self.environment is None
                else dict(self.environment)
            )
            env["AGENT_RAILS_TOKENIZER_INPUT"] = path
            result = subprocess.run(
                self.command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
                env=env,
                cwd=self.working_directory,
            )
            raw = result.stdout.strip()
            if not raw.isdigit():
                raise ValueError("tokenizer command must print one non-negative integer")
            return int(raw)
        finally:
            if path:
                Path(path).unlink(missing_ok=True)

    def truncate(self, text: str, budget: int) -> str:
        if budget <= 0:
            return ""
        total, _ = self.count(text)
        if total <= budget:
            return text

        marker = "\n...[truncated by Agent Rails token budget]...\n"
        marker_tokens, _ = self.count(marker)
        content_budget = budget - marker_tokens
        if content_budget <= 0:
            return ""

        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            tokens, _ = self.count(text[:middle])
            if tokens <= content_budget:
                low = middle
            else:
                high = middle - 1

        prefix = text[:low]
        newline = prefix.rfind("\n")
        if newline >= max(0, int(len(prefix) * 0.65)):
            prefix = prefix[: newline + 1]
        candidate = prefix.rstrip() + marker
        while candidate:
            tokens, _ = self.count(candidate)
            if tokens <= budget:
                return candidate
            prefix = prefix[:-1]
            candidate = prefix.rstrip() + marker
        return ""


def count_tokens(
    text: str,
    mode: str,
    chars_per_token: int,
    command: str = "",
    tokenizer_path: str = "",
    tiktoken_encoding: str = "cl100k_base",
) -> TokenCount:
    try:
        counter = TokenCounter(mode, chars_per_token, command, tokenizer_path, tiktoken_encoding)
        tokens, _ = counter.count(text)
        return TokenCount(tokens=tokens, tokenizer=counter.effective_mode)
    except Exception as exc:
        if mode == "command":
            raise TokenizerSelectionError("Tokenizer command failed or did not print an integer.") from exc
        if mode == "tiktoken":
            raise TokenizerSelectionError(
                "tiktoken tokenizer unavailable. Install tiktoken or use --tokenizer char/command."
            ) from exc
        if mode in {"huggingface", "hf"}:
            raise TokenizerSelectionError(
                "Hugging Face tokenizer unavailable. Set --tokenizer-path and install transformers."
            ) from exc
        raise TokenizerSelectionError(f"Unknown tokenizer: {mode}", exit_code=2) from exc
