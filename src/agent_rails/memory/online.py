from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Mapping, Optional

from agent_rails.core.terminal import normalize_line_boundaries


MAX_ONLINE_MEMORY_OUTPUT_BYTES = 1_000_000
_ADAPTER_SUPERVISOR = r'''
status_fd="$1"
/bin/bash -c '
  status_fd="$1"
  eval "exec ${status_fd}>&-"
  exec /bin/bash -c "$AGENT_RAILS_ADAPTER_COMMAND"
' agent-rails-adapter "$status_fd"
adapter_status=$?
printf '%s\n' "$adapter_status" >&"$status_fd"
eval "exec ${status_fd}>&-"
exec 1>&-
while :; do /bin/sleep 3600; done
'''


class OnlineMemoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class OnlineMemoryQuery:
    query_file: Path
    project: str
    limit: int
    timeout_seconds: int = 8
    working_directory: Optional[Path] = None


def query_online_memory(
    command: str,
    query: OnlineMemoryQuery,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Run one provider-neutral online Memory Adapter.

    The Adapter reads the UTF-8 query file named by
    ``AGENT_RAILS_MEMORY_QUERY_FILE`` and writes UTF-8 Markdown to stdout.
    Credentials remain private to the Adapter's inherited environment.
    """
    if not command:
        raise OnlineMemoryError("Online memory command is not configured.")
    if not query.query_file.is_file():
        raise OnlineMemoryError(f"Online memory query file not found: {query.query_file}")
    if query.limit < 1:
        raise OnlineMemoryError("Online memory limit must be a positive integer.")
    if query.timeout_seconds < 1:
        raise OnlineMemoryError("Online memory timeout must be a positive integer.")

    adapter_env = dict(os.environ if environment is None else environment)
    adapter_env.update(
        {
            "AGENT_RAILS_ADAPTER_COMMAND": command,
            "AGENT_RAILS_MEMORY_QUERY_FILE": str(query.query_file.resolve()),
            "AGENT_RAILS_MEMORY_PROJECT": query.project,
            "AGENT_RAILS_MEMORY_LIMIT": str(query.limit),
        }
    )
    deadline = time.monotonic() + query.timeout_seconds
    try:
        status_read, status_write = os.pipe()
    except OSError:
        raise OnlineMemoryError("Online memory command could not be started.") from None
    try:
        process = subprocess.Popen(
            [
                "/bin/bash",
                "-c",
                _ADAPTER_SUPERVISOR,
                "agent-rails-supervisor",
                str(status_write),
            ],
            env=adapter_env,
            cwd=query.working_directory,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            bufsize=0,
            close_fds=True,
            pass_fds=(status_write,),
        )
    except (OSError, ValueError):
        os.close(status_read)
        raise OnlineMemoryError("Online memory command could not be started.") from None
    finally:
        os.close(status_write)

    try:
        output, returncode = _read_bounded_output(
            process,
            status_read,
            deadline,
            query.timeout_seconds,
        )
    finally:
        _terminate_process_group(process)

    if returncode != 0:
        raise OnlineMemoryError(
            f"Online memory command failed with exit code {returncode}."
        )
    try:
        decoded = output.decode("utf-8")
    except UnicodeDecodeError:
        raise OnlineMemoryError("Online memory output is not valid UTF-8.") from None
    return normalize_line_boundaries(decoded)


def _read_bounded_output(
    process: subprocess.Popen[bytes],
    status_fd: int,
    deadline: float,
    timeout_seconds: int,
) -> tuple[bytes, int]:
    stdout = process.stdout
    if stdout is None:
        os.close(status_fd)
        raise OnlineMemoryError("Online memory command stdout is unavailable.")

    selector = selectors.DefaultSelector()
    output = bytearray()
    status = bytearray()
    stdout_open = True
    status_open = True
    try:
        os.set_blocking(stdout.fileno(), False)
        os.set_blocking(status_fd, False)
        selector.register(stdout, selectors.EVENT_READ, "stdout")
        selector.register(status_fd, selectors.EVENT_READ, "status")
        while stdout_open or status_open:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _timeout_error(timeout_seconds)
            events = selector.select(remaining)
            if not events:
                raise _timeout_error(timeout_seconds)
            for key, _ in events:
                try:
                    if key.data == "stdout":
                        chunk = os.read(
                            stdout.fileno(),
                            max(
                                1,
                                min(
                                    65_536,
                                    MAX_ONLINE_MEMORY_OUTPUT_BYTES - len(output) + 1,
                                ),
                            ),
                        )
                        if not chunk:
                            selector.unregister(stdout)
                            stdout_open = False
                            continue
                        output.extend(chunk)
                        if len(output) > MAX_ONLINE_MEMORY_OUTPUT_BYTES:
                            raise OnlineMemoryError(
                                "Online memory output exceeds "
                                f"{MAX_ONLINE_MEMORY_OUTPUT_BYTES} bytes."
                            )
                    else:
                        chunk = os.read(status_fd, 32)
                        if not chunk:
                            selector.unregister(status_fd)
                            status_open = False
                            continue
                        status.extend(chunk)
                        if len(status) > 16:
                            raise OnlineMemoryError(
                                "Online memory command status is unavailable."
                            )
                except BlockingIOError:
                    continue

        return bytes(output), _parse_adapter_status(bytes(status))
    except OSError:
        raise OnlineMemoryError("Online memory command output could not be read.") from None
    finally:
        try:
            selector.close()
        except OSError:
            pass
        try:
            stdout.close()
        except OSError:
            pass
        try:
            os.close(status_fd)
        except OSError:
            pass


def _parse_adapter_status(raw_status: bytes) -> int:
    try:
        status = raw_status.decode("ascii")
    except UnicodeDecodeError:
        raise OnlineMemoryError("Online memory command status is unavailable.") from None
    if not status.endswith("\n") or not status[:-1].isdigit():
        raise OnlineMemoryError("Online memory command status is unavailable.")
    returncode = int(status[:-1])
    if returncode > 255:
        raise OnlineMemoryError("Online memory command status is unavailable.")
    return returncode


def _timeout_error(timeout_seconds: int) -> OnlineMemoryError:
    return OnlineMemoryError(
        f"Online memory command timed out after {timeout_seconds} seconds."
    )


def _signal_process_group(process_group: int, signal_number: int) -> bool:
    try:
        os.killpg(process_group, signal_number)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    except OSError:
        return False
    return True


def _process_group_exists(process_group: int) -> bool:
    return _signal_process_group(process_group, 0)


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    if _signal_process_group(process_group, signal.SIGTERM):
        grace_deadline = time.monotonic() + 0.2
        while time.monotonic() < grace_deadline and _process_group_exists(process_group):
            time.sleep(0.01)
        _signal_process_group(process_group, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
        try:
            process.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass
    except OSError:
        pass
