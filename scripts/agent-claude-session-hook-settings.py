#!/usr/bin/env python3
"""Install or remove the personal Agent Rails Claude SessionStart hook."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


HOOK_BASENAME = "agent-rails-session-start.sh"


def load_settings(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = path.read_text(encoding="utf-8-sig")
    if not raw.strip():
        return {}
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError(f"Claude settings must be a JSON object: {path}")
    return data


def write_settings(path: Path, settings: dict[str, Any], dry_run: bool) -> None:
    if dry_run:
        print(f"Would write {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {path}")


def command_for(hook_path: Path) -> str:
    return f'bash "{hook_path}"; exit 0'


def is_agent_rails_hook(handler: Any) -> bool:
    return isinstance(handler, dict) and HOOK_BASENAME in str(handler.get("command", ""))


def remove_existing_session_hook(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    groups = hooks.get("SessionStart")
    if not isinstance(groups, list):
        return False

    changed = False
    next_groups: list[Any] = []
    for group in groups:
        if not isinstance(group, dict):
            next_groups.append(group)
            continue
        handlers = group.get("hooks")
        if not isinstance(handlers, list):
            next_groups.append(group)
            continue
        next_handlers = [handler for handler in handlers if not is_agent_rails_hook(handler)]
        if len(next_handlers) != len(handlers):
            changed = True
        if next_handlers:
            next_group = dict(group)
            next_group["hooks"] = next_handlers
            next_groups.append(next_group)

    if changed:
        if next_groups:
            hooks["SessionStart"] = next_groups
        else:
            hooks.pop("SessionStart", None)
        if not hooks:
            settings.pop("hooks", None)
    return changed


def install(settings: dict[str, Any], hook_path: Path) -> bool:
    remove_existing_session_hook(settings)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude settings key 'hooks' must be a JSON object")
    groups = hooks.setdefault("SessionStart", [])
    if not isinstance(groups, list):
        raise ValueError("Claude settings hooks.SessionStart must be a JSON array")
    groups.append(
        {
            "matcher": "startup|resume|clear|compact",
            "hooks": [
                {
                    "type": "command",
                    "command": command_for(hook_path),
                    "timeout": 5,
                    "statusMessage": "Loading Agent Rails...",
                }
            ],
        }
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["install", "uninstall"])
    parser.add_argument("--settings", required=True)
    parser.add_argument("--hook", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    settings_path = Path(args.settings).expanduser()
    hook_path = Path(args.hook).expanduser().resolve()
    settings = load_settings(settings_path)

    if args.action == "install":
        install(settings, hook_path)
        if args.dry_run:
            print(f"Would install Agent Rails SessionStart hook: {hook_path}")
        else:
            print(f"Installed Agent Rails SessionStart hook: {hook_path}")
        write_settings(settings_path, settings, args.dry_run)
        return 0

    changed = remove_existing_session_hook(settings)
    if args.dry_run:
        if changed:
            print(f"Would remove Agent Rails SessionStart hook from {settings_path}")
        else:
            print(f"Agent Rails SessionStart hook not present in {settings_path}")
    else:
        if changed:
            print(f"Removed Agent Rails SessionStart hook from {settings_path}")
        else:
            print(f"Agent Rails SessionStart hook not present in {settings_path}")
    write_settings(settings_path, settings, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
