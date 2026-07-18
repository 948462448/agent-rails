"""Assemble an Agent Rails Markdown pack under a hard token budget."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys
from typing import Any, Sequence

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
    "Product Contract": ("mandatory", 120, 0),
    "Current Git State": ("mandatory", 100, 160),
    "Changed Files": ("git", 70, 0),
    "Changed File Priority": ("git", 85, 0),
    "Changed File Excerpts": ("git", 100, 0),
    "Task Code Evidence": ("git", 95, 0),
    "Working Tree Status": ("git", 90, 0),
    "Relevant Entry Docs": ("git", 75, 0),
    "Context Gaps": ("git", 65, 0),
    "Task Model": ("contract", 100, 160),
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
    structure_text: str = ""
    structure_tokens: int = 0


@dataclass(frozen=True)
class _SectionHeading:
    name: str
    offset: int


@dataclass(frozen=True)
class _FenceBlock:
    start: int
    end: int
    opening: str
    content: tuple[str, ...]
    closing: str
    closed: bool
    fence_character: str
    fence_length: int


@dataclass(frozen=True)
class _MarkdownLayout:
    lines: tuple[str, ...]
    required_lines: frozenset[int]
    required_blocks: frozenset[int]
    blocks: dict[int, _FenceBlock]


_ATX_HEADING = re.compile(r" {0,3}#{1,6}(?:[ \t]+.*)?[ \t]*")
_FENCE_OPENING = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})")
_TRUNCATION_MARKER = "\n...[truncated by Agent Rails token budget]...\n"


def split_sections(text: str) -> list[Section]:
    headings = _section_headings(text)
    sections: list[Section] = []
    if not headings:
        return [Section("__preamble__", text, 0, "mandatory", 120, 40)]

    preamble = text[: headings[0].offset]
    if preamble:
        sections.append(Section("__preamble__", preamble, 0, "mandatory", 120, 40))
    for heading_index, heading in enumerate(headings):
        end = headings[heading_index + 1].offset if heading_index + 1 < len(headings) else len(text)
        name = heading.name
        category, priority, minimum = SECTION_RULES.get(name, ("contract", 50, 0))
        sections.append(Section(name, text[heading.offset : end], len(sections), category, priority, minimum))
    return sections


def _section_headings(text: str) -> list[_SectionHeading]:
    headings: list[_SectionHeading] = []
    fence_character = ""
    fence_length = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if fence_character:
            closing = re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*(?:\r?\n)?",
                line,
            )
            if closing:
                fence_character = ""
                fence_length = 0
        else:
            opening = re.match(r" {0,3}(?P<fence>`{3,}|~{3,})", line)
            if opening:
                fence = opening.group("fence")
                fence_character = fence[0]
                fence_length = len(fence)
            else:
                heading = re.fullmatch(r"## ([^\r\n]+)\r?\n?", line)
                if heading:
                    headings.append(
                        _SectionHeading(name=heading.group(1).strip(), offset=offset)
                    )
        offset += len(line)
    return headings


def _markdown_layout(text: str) -> _MarkdownLayout:
    lines = tuple(text.splitlines(keepends=True))
    required_lines: set[int] = set()
    required_blocks: set[int] = set()
    blocks: dict[int, _FenceBlock] = {}
    index = 0
    while index < len(lines):
        opening_match = _FENCE_OPENING.match(lines[index])
        if opening_match:
            fence = opening_match.group("fence")
            closing_index = index + 1
            while closing_index < len(lines):
                if re.fullmatch(
                    rf" {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\r?\n)?",
                    lines[closing_index],
                ):
                    break
                closing_index += 1
            closed = closing_index < len(lines)
            end = closing_index if closed else len(lines) - 1
            content_end = end if closed else len(lines)
            blocks[index] = _FenceBlock(
                start=index,
                end=end,
                opening=lines[index],
                content=lines[index + 1 : content_end],
                closing=lines[end] if closed else "",
                closed=closed,
                fence_character=fence[0],
                fence_length=len(fence),
            )
            index = end + 1
            continue

        line_without_ending = lines[index].rstrip("\r\n")
        if _ATX_HEADING.fullmatch(line_without_ending):
            required_lines.add(index)
            next_index = index + 1
            if (
                next_index < len(lines)
                and lines[next_index].strip(" \t\r\n") == ""
            ):
                required_lines.add(next_index)
        elif line_without_ending.startswith("- Token allocator: `"):
            required_lines.add(index)
        index += 1

    if not required_lines and lines:
        first_nonempty = next(
            (
                line_index
                for line_index, line in enumerate(lines)
                if line.strip(" \t\r\n")
            ),
            0,
        )
        if first_nonempty in blocks:
            required_blocks.add(first_nonempty)
        else:
            required_lines.add(first_nonempty)

    return _MarkdownLayout(
        lines=lines,
        required_lines=frozenset(required_lines),
        required_blocks=frozenset(required_blocks),
        blocks=blocks,
    )


def _balanced_fence(block: _FenceBlock, content_lines: int) -> str:
    parts = [block.opening]
    if parts[-1] and not parts[-1].endswith(("\n", "\r")):
        parts.append("\n")
    selected_content = block.content[:content_lines]
    parts.extend(selected_content)
    if selected_content and not selected_content[-1].endswith(("\n", "\r")):
        parts.append("\n")
    if block.closed:
        parts.append(block.closing)
    else:
        parts.append(block.fence_character * block.fence_length + "\n")
    return "".join(parts)


def _render_layout(
    layout: _MarkdownLayout,
    selected_lines: set[int] | frozenset[int],
    block_values: dict[int, str],
    *,
    include_marker: bool,
) -> str:
    rendered: list[str] = []
    index = 0
    while index < len(layout.lines):
        block = layout.blocks.get(index)
        if block is not None:
            value = block_values.get(index)
            if value is not None:
                rendered.append(value)
            index = block.end + 1
            continue
        if index in selected_lines:
            rendered.append(layout.lines[index])
        index += 1
    if include_marker:
        rendered.append(_TRUNCATION_MARKER)
    return "".join(rendered)


def _section_structure(text: str) -> str:
    layout = _markdown_layout(text)
    blocks = {
        start: _balanced_fence(layout.blocks[start], 0)
        for start in layout.required_blocks
    }
    return _render_layout(
        layout,
        layout.required_lines,
        blocks,
        include_marker=False,
    )


def _fits(text: str, budget: int, counter: TokenCounter) -> bool:
    tokens, _ = counter.count(text)
    return tokens <= budget


def _largest_fitting_prefix(length: int, fits_prefix: Any) -> int:
    if not fits_prefix(0):
        return -1
    low = 0
    high = length
    while low < high:
        middle = (low + high + 1) // 2
        if fits_prefix(middle):
            low = middle
        else:
            high = middle - 1
    return low


def _truncate_section(text: str, budget: int, counter: TokenCounter) -> str:
    full_tokens, _ = counter.count(text)
    if full_tokens <= budget:
        return text

    layout = _markdown_layout(text)
    selected_lines = set(layout.required_lines)
    block_values = {
        start: _balanced_fence(layout.blocks[start], 0)
        for start in layout.required_blocks
    }
    structure = _render_layout(
        layout,
        selected_lines,
        block_values,
        include_marker=False,
    )
    if not _fits(structure, budget, counter):
        raise ValueError("section allocation is below its required structure")

    include_marker = False
    index = 0
    while index < len(layout.lines):
        block = layout.blocks.get(index)
        if block is not None:
            def fence_prefix_fits(content_count: int) -> bool:
                candidate_blocks = dict(block_values)
                candidate_blocks[index] = _balanced_fence(block, content_count)
                candidate = _render_layout(
                    layout,
                    selected_lines,
                    candidate_blocks,
                    include_marker=include_marker,
                )
                return _fits(candidate, budget, counter)

            content_count = _largest_fitting_prefix(
                len(block.content), fence_prefix_fits
            )
            if content_count >= 0:
                block_values[index] = _balanced_fence(block, content_count)
            index = block.end + 1
            continue

        if index in selected_lines:
            index += 1
            continue

        region_end = index
        while (
            region_end < len(layout.lines)
            and region_end not in selected_lines
            and region_end not in layout.blocks
        ):
            region_end += 1

        def line_prefix_fits(line_count: int) -> bool:
            candidate_lines = set(selected_lines)
            candidate_lines.update(range(index, index + line_count))
            candidate = _render_layout(
                layout,
                candidate_lines,
                block_values,
                include_marker=include_marker,
            )
            return _fits(candidate, budget, counter)

        line_count = _largest_fitting_prefix(region_end - index, line_prefix_fits)
        if line_count > 0:
            selected_lines.update(range(index, index + line_count))
        index = region_end

    without_marker = _render_layout(
        layout,
        selected_lines,
        block_values,
        include_marker=False,
    )
    include_marker = _fits(
        without_marker + _TRUNCATION_MARKER,
        budget,
        counter,
    )
    output = (
        without_marker + _TRUNCATION_MARKER if include_marker else without_marker
    )
    if not _fits(output, budget, counter):
        raise ValueError("section could not be truncated under its token allocation")
    return output


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


def allocate_sections(
    sections: list[Section],
    budget: int,
    counter: TokenCounter,
    weights: dict[str, int],
) -> dict[str, Any]:
    desired_minimums: dict[int, int] = {}
    for section in sections:
        section.full_tokens, _ = counter.count(section.text)
        if section.name == "Product Contract":
            section.structure_text = section.text
            section.structure_tokens = section.full_tokens
            section.minimum = section.full_tokens
            desired_minimums[section.index] = section.full_tokens
            continue
        configured_minimum = min(section.minimum, section.full_tokens)
        section.structure_text = _section_structure(section.text)
        section.structure_tokens, _ = counter.count(section.structure_text)
        section.minimum = section.structure_tokens
        desired_minimums[section.index] = max(
            configured_minimum, section.structure_tokens
        )

    requested_minimum = sum(section.minimum for section in sections)
    if requested_minimum > budget:
        raise ValueError(
            "hard token budget "
            f"{budget} is below required section structure minimum "
            f"{requested_minimum}"
        )

    for section in sections:
        section.allocated = section.minimum

    remaining = max(0, budget - sum(section.allocated for section in sections))
    minimum_demands = {
        section.index: desired_minimums[section.index] - section.allocated
        for section in sections
    }
    total_minimum_demand = sum(minimum_demands.values())
    if total_minimum_demand <= remaining:
        for section in sections:
            section.allocated += minimum_demands[section.index]
        remaining -= total_minimum_demand

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
        "required_structure_tokens": requested_minimum,
        "initial_category_budgets": initial_budgets,
        "category_tokens": category_tokens,
        "redistributed_tokens": redistributed,
    }


def assemble(
    text: str,
    budget: int,
    counter: TokenCounter,
    weights: dict[str, int] | None = None,
) -> tuple[str, dict[str, Any]]:
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
        value = _truncate_section(section.text, section.allocated, counter)
        if not value:
            continue
        if value != section.text:
            truncated.append(section.name)
        rendered.append(value)
    output = "".join(rendered)
    used_tokens, _ = counter.count(output)

    if used_tokens > budget:
        raise ValueError(
            "structure-preserving assembly exceeded hard token budget: "
            f"used {used_tokens}, budget {budget}"
        )

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
        request_id: Any = None
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("request must be a JSON object")
            request: dict[str, Any] = payload
            request_id = request.get("id")
            action = request.get("action")
            if action == "count":
                tokens, cache_hit = counter.count(str(request.get("text", "")))
                response = {
                    "id": request_id,
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
                response = {"id": request_id, "content": output, "metadata": metadata}
            else:
                raise ValueError(f"unknown action: {action}")
        except Exception as exc:
            response = {"id": request_id, "error": str(exc)}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
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
