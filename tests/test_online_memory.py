#!/usr/bin/env python3

from __future__ import annotations

import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.memory.online import (
    MAX_ONLINE_MEMORY_OUTPUT_BYTES,
    OnlineMemoryError,
    OnlineMemoryQuery,
    query_online_memory,
)


class OnlineMemoryAdapterTest(unittest.TestCase):
    def test_adapter_runs_in_explicit_target_project_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            working_directory = Path(temp_dir) / "project"
            working_directory.mkdir()
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            output = query_online_memory(
                "pwd -P",
                OnlineMemoryQuery(
                    query_file,
                    "sample-project",
                    1,
                    working_directory=working_directory,
                ),
            )

            self.assertEqual(output, f"{working_directory.resolve()}\n")

    def test_command_receives_provider_neutral_query_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("find the relevant card\n", encoding="utf-8")
            command = r'''
printf -- '- project: %s\n' "$AGENT_RAILS_MEMORY_PROJECT"
printf -- '- limit: %s\n' "$AGENT_RAILS_MEMORY_LIMIT"
cat "$AGENT_RAILS_MEMORY_QUERY_FILE"
'''

            output = query_online_memory(
                command,
                OnlineMemoryQuery(query_file, "sample-project", 3),
                environment=dict(os.environ),
            )

            self.assertEqual(
                output,
                "- project: sample-project\n- limit: 3\nfind the relevant card\n",
            )

    def test_adapter_stderr_is_not_exposed_on_failure(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            with self.assertRaisesRegex(OnlineMemoryError, "exit code 9") as raised:
                query_online_memory(
                    "printf 'private-adapter-error' >&2; exit 9",
                    OnlineMemoryQuery(query_file, "sample-project", 1),
                )

            self.assertNotIn("private-adapter-error", str(raised.exception))

    def test_adapter_requires_utf8_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            with self.assertRaisesRegex(OnlineMemoryError, "valid UTF-8"):
                query_online_memory(
                    "printf '\\377'",
                    OnlineMemoryQuery(query_file, "sample-project", 1),
                )

    def test_adapter_normalizes_all_unicode_line_boundaries(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            output = query_online_memory(
                "printf 'first\\rsecond\\302\\205third\\342\\200\\250fourth\\r\\n'",
                OnlineMemoryQuery(query_file, "sample-project", 1),
            )

            self.assertEqual(output, "first\nsecond\nthird\nfourth\n")

    def test_adapter_timeout_kills_background_process_group(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")
            marker = Path(temp_dir) / "background-survived"
            environment = dict(os.environ)
            environment["ONLINE_MEMORY_TIMEOUT_MARKER"] = str(marker)
            child_program = """
import os
from pathlib import Path
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
time.sleep(2)
Path(os.environ["ONLINE_MEMORY_TIMEOUT_MARKER"]).touch()
"""
            command = (
                f"{shlex.quote(sys.executable)} -c {shlex.quote(child_program)} & wait"
            )

            with self.assertRaisesRegex(OnlineMemoryError, "timed out after 1 seconds"):
                query_online_memory(
                    command,
                    OnlineMemoryQuery(
                        query_file,
                        "sample-project",
                        1,
                        timeout_seconds=1,
                    ),
                    environment=environment,
                )
            time.sleep(1.3)
            self.assertFalse(marker.exists())

    def test_adapter_background_process_holding_stdout_hits_deadline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            started = time.monotonic()
            with self.assertRaisesRegex(OnlineMemoryError, "timed out after 1 seconds"):
                query_online_memory(
                    "sleep 30 & printf ok",
                    OnlineMemoryQuery(
                        query_file,
                        "sample-project",
                        1,
                        timeout_seconds=1,
                    ),
                )
            self.assertLess(time.monotonic() - started, 2.0)

    def test_adapter_success_kills_background_process_that_closed_stdout(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")
            ready = Path(temp_dir) / "background-ready"
            marker = Path(temp_dir) / "background-survived"
            environment = dict(os.environ)
            environment["ONLINE_MEMORY_READY_MARKER"] = str(ready)
            environment["ONLINE_MEMORY_SUCCESS_MARKER"] = str(marker)
            child_program = """
import os
from pathlib import Path
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(os.environ["ONLINE_MEMORY_READY_MARKER"]).touch()
time.sleep(1)
Path(os.environ["ONLINE_MEMORY_SUCCESS_MARKER"]).touch()
"""
            command = f"""
{shlex.quote(sys.executable)} -c {shlex.quote(child_program)} >/dev/null 2>&1 &
while [[ ! -f "$ONLINE_MEMORY_READY_MARKER" ]]; do sleep 0.01; done
printf ok
"""

            output = query_online_memory(
                command,
                OnlineMemoryQuery(query_file, "sample-project", 1),
                environment=environment,
            )

            self.assertEqual(output, "ok")
            self.assertTrue(ready.exists())
            time.sleep(1.1)
            self.assertFalse(marker.exists())

    def test_adapter_output_limit_is_enforced_while_reading(self) -> None:
        with tempfile.TemporaryDirectory(prefix="agent-rails-online-memory-") as temp_dir:
            query_file = Path(temp_dir) / "query.md"
            query_file.write_text("query\n", encoding="utf-8")

            exact = query_online_memory(
                _python_output_command(MAX_ONLINE_MEMORY_OUTPUT_BYTES),
                OnlineMemoryQuery(query_file, "sample-project", 1),
            )
            self.assertEqual(len(exact), MAX_ONLINE_MEMORY_OUTPUT_BYTES)

            started = time.monotonic()
            with self.assertRaisesRegex(OnlineMemoryError, "exceeds"):
                query_online_memory(
                    _python_output_command(
                        MAX_ONLINE_MEMORY_OUTPUT_BYTES + 1,
                        sleep_after_write=True,
                    ),
                    OnlineMemoryQuery(query_file, "sample-project", 1),
                )
            self.assertLess(time.monotonic() - started, 2.0)


def _python_output_command(size: int, *, sleep_after_write: bool = False) -> str:
    program = (
        f'import sys; sys.stdout.buffer.write(b"x" * {size}); '
        "sys.stdout.buffer.flush()"
    )
    if sleep_after_write:
        program += "; import time; time.sleep(30)"
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(program)}"


if __name__ == "__main__":
    unittest.main()
