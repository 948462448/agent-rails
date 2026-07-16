#!/usr/bin/env python3
"""Run the Agent Rails Python CLI from a trusted absolute bootstrap."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "public":
        # Keep version/home/help independent from every application module;
        # non-builtin commands replace this process with the internal helper.
        from agent_rails.public_cli import main as public_main

        raise SystemExit(public_main(sys.argv[2:]))

    from agent_rails.cli import main

    raise SystemExit(main())
