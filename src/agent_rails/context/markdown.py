"""Shared safe Markdown display primitives for Task Pack context Modules."""

from __future__ import annotations

import re
import unicodedata


_FENCE_OPENING = re.compile(r" {0,3}(?P<fence>`{3,}|~{3,})")


def valid_utf8(value: str) -> str:
    """Return valid UTF-8 text, replacing unpaired surrogate code points."""

    return value.encode("utf-8", errors="replace").decode("utf-8")


def display_text(value: str) -> str:
    """Make control, formatting, separator, and surrogate characters visible."""

    rendered: list[str] = []
    for character in value:
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Cs", "Zl", "Zp"}:
            codepoint = ord(character)
            if codepoint <= 0xFF:
                rendered.append(f"\\x{codepoint:02x}")
            else:
                rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(character)
    return "".join(rendered)


def markdown_code(value: str) -> str:
    """Render one display-safe value in a collision-free Markdown code span."""

    safe = display_text(value)
    longest = max((len(run) for run in re.findall(r"`+", safe)), default=0)
    fence = "`" * max(1, longest + 1)
    if longest:
        return f"{fence} {safe} {fence}"
    return f"{fence}{safe}{fence}"


def markdown_fence(text: str, character: str, minimum: int) -> str:
    """Choose a fence longer than any matching run in the enclosed text."""

    if len(character) != 1:
        raise ValueError("Markdown fence character must be one character.")
    longest = max(
        (len(run) for run in re.findall(re.escape(character) + r"+", text)),
        default=0,
    )
    return character * max(minimum, longest + 1)


def has_markdown_heading(text: str, level: int, title: str) -> bool:
    """Return whether an ATX heading exists outside Markdown code fences."""

    if level < 1 or level > 6:
        raise ValueError("Markdown heading level must be between 1 and 6.")
    fence_character = ""
    fence_length = 0
    heading_prefix = "#" * level
    for line in text.splitlines():
        if fence_character:
            if re.fullmatch(
                rf" {{0,3}}{re.escape(fence_character)}{{{fence_length},}}[ \t]*",
                line,
            ):
                fence_character = ""
                fence_length = 0
            continue

        opening = _FENCE_OPENING.match(line)
        if opening:
            fence = opening.group("fence")
            fence_character = fence[0]
            fence_length = len(fence)
            continue

        heading = re.fullmatch(
            rf" {{0,3}}{re.escape(heading_prefix)}[ \t]+(?P<title>.*?)[ \t]*",
            line,
        )
        if heading is None:
            continue
        candidate = re.sub(r"[ \t]+#+[ \t]*$", "", heading.group("title"))
        if candidate == title:
            return True
    return False


__all__ = (
    "display_text",
    "has_markdown_heading",
    "markdown_code",
    "markdown_fence",
    "valid_utf8",
)
