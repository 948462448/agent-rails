#!/usr/bin/env python3
"""Run the Agent Rails Python CLI from a trusted absolute bootstrap."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT / "src"))

from agent_rails.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
