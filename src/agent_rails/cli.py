from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import sys
from typing import Sequence

from . import estimate
from .config import profile_init
from .config.profile import ProfileLoadError
from .config.target_project import TargetProjectError, resolve_target_project
from .memory.online import OnlineMemoryError, OnlineMemoryQuery, query_online_memory
from .models.presets import render_shell_values


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print("Agent Rails Python CLI requires a command.", file=sys.stderr)
        return 2

    command = args.pop(0)
    if command == "estimate":
        return estimate.main(args)
    if command == "model-preset":
        return _model_preset(args)
    if command == "target-context":
        return _target_context(args)
    if command == "profile-init":
        return _profile_init(args)
    if command == "online-memory":
        return _online_memory(args)

    print(f"Unknown Agent Rails Python command: {command}", file=sys.stderr)
    return 2


def _model_preset(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python model-preset", add_help=False)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("model")
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    if not options.shell:
        print("model-preset currently requires --shell.", file=sys.stderr)
        return 2
    print(render_shell_values(options.model))
    return 0


def _target_context(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python target-context", add_help=False)
    parser.add_argument("--project", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--agent-rails-home", required=True)
    parser.add_argument("--required-profile", action="store_true")
    parser.add_argument("--skip-profile-load", action="store_true")
    parser.add_argument("--load-env-file", action="store_true")
    parser.add_argument("--shell", action="store_true")
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    if not options.shell:
        print("target-context currently requires --shell.", file=sys.stderr)
        return 2
    try:
        context = resolve_target_project(
            Path(options.project),
            kit_home=Path(options.agent_rails_home),
            explicit_profile=options.profile,
            require_profile=options.required_profile,
            load_profile=not options.skip_profile_load,
            load_environment_file=options.load_env_file,
        )
    except TargetProjectError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"Profile not found: {exc}", file=sys.stderr)
        return 2
    except ProfileLoadError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    for name, value in context.shell_values().items():
        print(f"{name}={shlex.quote(value)}")
    return 0


def _online_memory(args: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="agent-rails-python online-memory", add_help=False)
    parser.add_argument("--command", required=True)
    parser.add_argument("--query-file", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--timeout-seconds", type=int, default=8)
    parser.add_argument("--output", required=True)
    try:
        options = parser.parse_args(list(args))
    except SystemExit:
        return 2
    try:
        output = query_online_memory(
            options.command,
            OnlineMemoryQuery(
                query_file=Path(options.query_file),
                project=options.project,
                limit=options.limit,
                timeout_seconds=options.timeout_seconds,
            ),
        )
    except OnlineMemoryError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    Path(options.output).write_text(output, encoding="utf-8")
    return 0


def _profile_init(args: Sequence[str]) -> int:
    if len(args) < 2 or args[0] != "--agent-rails-home":
        print("profile-init requires --agent-rails-home PATH.", file=sys.stderr)
        return 2
    return profile_init.main(args[2:], kit_home=Path(args[1]))
