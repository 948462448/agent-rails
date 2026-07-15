#!/usr/bin/env python3
"""Assemble an Agent Rails Markdown pack under a hard token budget."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from dataclasses import dataclass
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.models.tokenizer import TokenCounter


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
