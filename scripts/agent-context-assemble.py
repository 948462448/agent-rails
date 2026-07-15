#!/usr/bin/env python3
"""Assemble an Agent Rails Markdown pack under a hard token budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from typing import Any


CATEGORY_WEIGHTS = {
    "mandatory": 10,
    "git": 35,
    "contract": 25,
    "memory": 15,
    "verify": 15,
}

SECTION_RULES = {
    "Session Marker": ("mandatory", 100, 80),
    "Goal": ("mandatory", 110, 160),
    "Context Budget": ("mandatory", 115, 180),
    "Current Git State": ("mandatory", 100, 160),
    "Changed Files": ("git", 70, 0),
    "Changed File Priority": ("git", 85, 0),
    "Changed File Excerpts": ("git", 100, 0),
    "Working Tree Status": ("git", 90, 0),
    "Relevant Entry Docs": ("git", 75, 0),
    "Context Gaps": ("git", 65, 0),
    "Agent Rails Contract": ("contract", 105, 240),
    "Subagent Result Contract": ("contract", 75, 0),
    "Project Configuration": ("contract", 60, 0),
    "Memory Provider": ("memory", 55, 0),
    "Memory Cards": ("memory", 90, 0),
    "Verification Suggestions": ("verify", 100, 0),
    "Delivery Checklist": ("verify", 90, 80),
}


@dataclass
class Section:
    name: str
    text: str
    index: int
    category: str
    priority: int
    minimum: int
    full_tokens: int = 0
    allocated: int = 0


class TokenCounter:
    def __init__(
        self,
        mode: str,
        chars_per_token: int,
        command: str = "",
        tokenizer_path: str = "",
        tiktoken_encoding: str = "cl100k_base",
    ) -> None:
        self.requested_mode = mode
        self.chars_per_token = max(1, chars_per_token)
        self.command = command
        self.tokenizer_path = tokenizer_path
        self.tiktoken_encoding = tiktoken_encoding
        self._auto_mode = mode == "auto"
        self.cache: dict[str, int] = {}
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
        if digest in self.cache:
            self.cache_hits += 1
            return self.cache[digest], True

        self.cache_misses += 1
        try:
            if self.effective_mode == "char-estimate":
                value = math.ceil(len(text) / self.chars_per_token)
            elif self.effective_mode == "command":
                value = self._count_with_command(text)
            elif self.effective_mode.startswith("tiktoken:"):
                value = len(self._encoder.encode(text))
            elif self.effective_mode.startswith("huggingface:"):
                value = len(self._encoder.encode(text, add_special_tokens=False))
            else:
                raise RuntimeError(f"unsupported tokenizer: {self.effective_mode}")
        except Exception:
            if not self._auto_mode:
                raise
            self.effective_mode = "char-estimate"
            self._encoder = None
            value = math.ceil(len(text) / self.chars_per_token)

        self.cache[digest] = value
        return value, False

    def _count_with_command(self, text: str) -> int:
        path = ""
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
                handle.write(text)
                path = handle.name
            env = os.environ.copy()
            env["AGENT_RAILS_TOKENIZER_INPUT"] = path
            result = subprocess.run(
                self.command,
                shell=True,
                check=True,
                capture_output=True,
                text=True,
                env=env,
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


def split_sections(text: str) -> list[Section]:
    matches = list(re.finditer(r"(?m)^## ([^\n]+)\n", text))
    sections: list[Section] = []
    if not matches:
        return [Section("__preamble__", text, 0, "mandatory", 120, 40)]

    preamble = text[: matches[0].start()]
    if preamble:
        sections.append(Section("__preamble__", preamble, 0, "mandatory", 120, 40))
    for match_index, match in enumerate(matches):
        end = matches[match_index + 1].start() if match_index + 1 < len(matches) else len(text)
        name = match.group(1).strip()
        category, priority, minimum = SECTION_RULES.get(name, ("contract", 50, 0))
        sections.append(Section(name, text[match.start() : end], len(sections), category, priority, minimum))
    return sections


def annotate_budget(text: str, counter: TokenCounter, budget: int) -> str:
    text = text.replace(
        "- Mode: candidate output; the request hook applies the live hard token budget.\n",
        "- Mode: hard token budget derived from the live model and session.\n",
        1,
    )
    line = f"- Token allocator: `{counter.effective_mode}` hard cap `{budget}` tokens\n"
    match = re.search(r"(?m)^## Context Budget\n", text)
    if not match:
        return text
    insert_at = text.find("\n", match.end())
    if insert_at < 0:
        insert_at = match.end()
    else:
        insert_at += 1
    return text[:insert_at] + line + text[insert_at:]


def allocate_sections(sections: list[Section], budget: int, counter: TokenCounter, weights: dict[str, int]) -> dict[str, Any]:
    for section in sections:
        section.full_tokens, _ = counter.count(section.text)
        section.minimum = min(section.minimum, section.full_tokens)

    requested_minimum = sum(section.minimum for section in sections)
    if requested_minimum > budget:
        scale = budget / requested_minimum if requested_minimum else 0
        for section in sections:
            section.minimum = int(section.minimum * scale)

    for section in sections:
        section.allocated = section.minimum

    remaining = max(0, budget - sum(section.allocated for section in sections))
    category_sections: dict[str, list[Section]] = {key: [] for key in weights}
    for section in sections:
        category_sections.setdefault(section.category, []).append(section)
    for values in category_sections.values():
        values.sort(key=lambda item: (-item.priority, item.index))

    active_weights = {
        category: weight
        for category, weight in weights.items()
        if weight > 0 and any(item.full_tokens > item.allocated for item in category_sections.get(category, []))
    }
    initial_budgets: dict[str, int] = {key: 0 for key in weights}
    if active_weights and remaining:
        total_weight = sum(active_weights.values())
        for category, weight in active_weights.items():
            initial_budgets[category] = remaining * weight // total_weight

    def fill_category(category: str, amount: int) -> int:
        used = 0
        for section in category_sections.get(category, []):
            demand = section.full_tokens - section.allocated
            if demand <= 0:
                continue
            grant = min(demand, amount - used)
            if grant <= 0:
                break
            section.allocated += grant
            used += grant
        return used

    initial_used = 0
    for category, amount in initial_budgets.items():
        initial_used += fill_category(category, amount)

    pool = remaining - initial_used
    redistributed = 0
    while pool > 0:
        needy = [
            category
            for category, weight in weights.items()
            if weight > 0 and any(item.full_tokens > item.allocated for item in category_sections.get(category, []))
        ]
        if not needy:
            break
        before = pool
        total_weight = sum(weights[category] for category in needy)
        for category in needy:
            share = max(1, pool * weights[category] // total_weight)
            used = fill_category(category, min(pool, share))
            pool -= used
            redistributed += used
            if pool <= 0:
                break
        if pool == before:
            break

    category_tokens: dict[str, int] = {key: 0 for key in weights}
    for section in sections:
        category_tokens[section.category] = category_tokens.get(section.category, 0) + section.allocated
    return {
        "initial_category_budgets": initial_budgets,
        "category_tokens": category_tokens,
        "redistributed_tokens": redistributed,
    }


def assemble(text: str, budget: int, counter: TokenCounter, weights: dict[str, int] | None = None) -> tuple[str, dict[str, Any]]:
    if budget <= 0:
        raise ValueError("budget must be positive")
    weights = dict(weights or CATEGORY_WEIGHTS)
    annotated = annotate_budget(text, counter, budget)
    sections = split_sections(annotated)
    allocation = allocate_sections(sections, budget, counter, weights)

    rendered: list[str] = []
    truncated: list[str] = []
    for section in sorted(sections, key=lambda item: item.index):
        if section.allocated <= 0:
            continue
        value = counter.truncate(section.text, section.allocated)
        if not value:
            continue
        if value != section.text:
            truncated.append(section.name)
        rendered.append(value)
    output = "".join(rendered)
    used_tokens, _ = counter.count(output)

    if used_tokens > budget:
        output = counter.truncate(output, budget)
        used_tokens, _ = counter.count(output)

    metadata = {
        "budget_tokens": budget,
        "used_tokens": used_tokens,
        "tokenizer": counter.effective_mode,
        "cache_hits": counter.cache_hits,
        "cache_misses": counter.cache_misses,
        "truncated_sections": truncated,
        **allocation,
    }
    return output, metadata


def serve(counter: TokenCounter) -> int:
    for raw in sys.stdin:
        request: dict[str, Any] = {}
        try:
            request = json.loads(raw)
            action = request.get("action")
            if action == "count":
                tokens, cache_hit = counter.count(str(request.get("text", "")))
                response = {
                    "id": request.get("id"),
                    "tokens": tokens,
                    "cache_hit": cache_hit,
                    "tokenizer": counter.effective_mode,
                }
            elif action == "assemble":
                output, metadata = assemble(
                    str(request.get("text", "")),
                    int(request.get("budget_tokens", 0)),
                    counter,
                    request.get("weights"),
                )
                response = {"id": request.get("id"), "content": output, "metadata": metadata}
            else:
                raise ValueError(f"unknown action: {action}")
        except Exception as exc:
            response = {"id": request.get("id"), "error": str(exc)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--metadata")
    parser.add_argument("--budget-tokens", type=int)
    parser.add_argument("--tokenizer", choices=["auto", "char", "command", "tiktoken", "huggingface", "hf"], default="auto")
    parser.add_argument("--tokenizer-command", default="")
    parser.add_argument("--tokenizer-path", default="")
    parser.add_argument("--tiktoken-encoding", default="cl100k_base")
    parser.add_argument("--chars-per-token", type=int, default=2)
    parser.add_argument("--serve", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        counter = TokenCounter(
            args.tokenizer,
            args.chars_per_token,
            args.tokenizer_command,
            args.tokenizer_path,
            args.tiktoken_encoding,
        )
        if args.serve:
            return serve(counter)
        if not args.input or not args.output or not args.budget_tokens:
            raise ValueError("--input, --output, and --budget-tokens are required")
        raw = Path(args.input).read_text(encoding="utf-8", errors="replace")
        output, metadata = assemble(raw, args.budget_tokens, counter)
        Path(args.output).write_text(output, encoding="utf-8")
        if args.metadata:
            Path(args.metadata).write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return 0
    except Exception as exc:
        print(f"Agent Rails context assembler failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
