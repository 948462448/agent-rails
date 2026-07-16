"""Terminal-safe text and event rendering shared by public applications."""

from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Protocol
import unicodedata


_LINE_BOUNDARY = re.compile(r"\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]")


class TerminalEvent(Protocol):
    """Minimal event interface consumed by terminal renderers."""

    @property
    def stream(self) -> object: ...

    @property
    def text(self) -> str: ...


def terminal_text(value: str, *, preserve_newline: bool) -> str:
    """Render untrusted text without allowing terminal control injection."""

    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == "\n" and preserve_newline:
            escaped.append(character)
        elif character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif (
            category in {"Cc", "Cf", "Zl", "Zp"}
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def terminal_literal(value: str) -> str:
    """Render one terminal-safe literal with line breaks escaped."""

    return terminal_text(value, preserve_newline=False)


def terminal_stream_text(value: str) -> str:
    """Render terminal-safe stream text while preserving line breaks."""

    return terminal_text(value, preserve_newline=True)


def normalize_line_boundaries(value: str) -> str:
    """Normalize every Unicode line boundary to LF."""

    return _LINE_BOUNDARY.sub("\n", value)


def render_line_events(events: Iterable[TerminalEvent], stream: object) -> str:
    """Render logical line events with one trailing newline."""

    selected = [event.text for event in events if event.stream is stream]
    return "" if not selected else "\n".join(selected) + "\n"


def render_chunk_events(events: Iterable[TerminalEvent], stream: object) -> str:
    """Render already-delimited stream chunks without adding separators."""

    return "".join(event.text for event in events if event.stream is stream)
