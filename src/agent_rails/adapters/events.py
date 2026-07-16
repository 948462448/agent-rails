"""Shared terminal-safe output events for tool adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Sequence

from agent_rails.core.terminal import render_line_events, terminal_literal


class AdapterEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class AdapterEvent:
    stream: AdapterEventStream
    text: str


class AdapterOutput:
    """Result/error mixin exposing rendered stdout and stderr."""

    events: tuple[AdapterEvent, ...]

    @property
    def stdout(self) -> str:
        return render_line_events(self.events, AdapterEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return render_line_events(self.events, AdapterEventStream.STDERR)


class AdapterError(AdapterOutput, RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Iterable[AdapterEvent] = (),
    ) -> None:
        super().__init__(terminal_literal(message))
        self.exit_code = exit_code
        self.events = sanitize_events(events)


def append_event(
    events: list[AdapterEvent], stream: AdapterEventStream, text: str
) -> None:
    events.append(AdapterEvent(stream, terminal_literal(str(text))))


def append_stdout(events: list[AdapterEvent], text: str) -> None:
    append_event(events, AdapterEventStream.STDOUT, text)


def append_stdout_many(events: list[AdapterEvent], messages: Sequence[str]) -> None:
    for message in messages:
        append_stdout(events, message)


def sanitize_events(events: Iterable[AdapterEvent]) -> tuple[AdapterEvent, ...]:
    return tuple(
        AdapterEvent(event.stream, terminal_literal(str(event.text)))
        for event in events
    )
