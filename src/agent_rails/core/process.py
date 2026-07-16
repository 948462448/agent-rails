"""Shared child-process streaming and process-group cleanup."""

from __future__ import annotations

import codecs
import os
import selectors
import signal
import subprocess
import time
from typing import BinaryIO, Callable, Optional


class ChildProcessStreamError(RuntimeError):
    """A configured child output stream is unavailable."""


def stream_process_output(
    process: subprocess.Popen[bytes],
    *,
    stdout_sink: Optional[Callable[[str], None]],
    stderr_sink: Optional[Callable[[str], None]],
    chunk_bytes: int = 65_536,
) -> int:
    """Decode and drain configured child streams concurrently."""

    configured = (
        (process.stdout, stdout_sink),
        (process.stderr, stderr_sink),
    )
    streams: list[BinaryIO] = []
    selector = selectors.DefaultSelector()
    try:
        for stream, sink in configured:
            if sink is None:
                continue
            if stream is None:
                raise ChildProcessStreamError("Child process output stream is unavailable.")
            os.set_blocking(stream.fileno(), False)
            decoder = codecs.getincrementaldecoder("utf-8")(
                errors="backslashreplace"
            )
            selector.register(stream, selectors.EVENT_READ, (sink, decoder))
            streams.append(stream)

        while selector.get_map():
            for key, _ in selector.select():
                stream = key.fileobj
                sink, decoder = key.data
                try:
                    chunk = os.read(stream.fileno(), chunk_bytes)
                except (BlockingIOError, InterruptedError):
                    continue
                if chunk:
                    sink(decoder.decode(chunk))
                    continue
                tail = decoder.decode(b"", final=True)
                if tail:
                    sink(tail)
                selector.unregister(stream)
                stream.close()
        return process.wait()
    finally:
        selector.close()
        for stream in streams:
            if not stream.closed:
                try:
                    stream.close()
                except OSError:
                    pass


def stop_process_group(process: subprocess.Popen[bytes]) -> None:
    """Terminate one isolated child process group and boundedly reap it."""

    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except OSError:
        pass
    try:
        process.wait(timeout=0.25)
    except (OSError, subprocess.TimeoutExpired):
        pass
    if _process_group_alive(process_group):
        try:
            os.killpg(process_group, signal.SIGKILL)
        except OSError:
            pass
    try:
        process.wait(timeout=1)
    except (OSError, subprocess.TimeoutExpired):
        pass
    deadline = time.monotonic() + 1
    while _process_group_alive(process_group) and time.monotonic() < deadline:
        time.sleep(0.01)


def _process_group_alive(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
