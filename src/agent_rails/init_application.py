"""Render shell setup guidance without modifying a user's configuration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping, Optional

from .context.markdown import display_text


class InitShell(str, Enum):
    ZSH = "zsh"
    BASH = "bash"
    FISH = "fish"


@dataclass(frozen=True)
class InitRequest:
    requested_shell: Optional[InitShell]
    requested_project: Optional[Path]
    explicit_profile: Optional[Path]
    kit_home: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class InitResult:
    shell: InitShell
    project_path: Optional[Path]
    profile_path: Optional[Path]
    output: str
    exit_code: int = 0


class InitInputError(ValueError):
    """The init guide request cannot be represented safely."""

    def __init__(self, message: str, *, exit_code: int = 2) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def run_init(request: InitRequest) -> InitResult:
    """Render one deterministic, copy-pasteable shell setup guide."""

    _validate_request(request)
    environment = dict(request.environment)
    home_value = environment.get("HOME", "")
    if not home_value:
        raise InitInputError("HOME is required for agent-rails init.")
    user_home = Path(home_value)

    shell = request.requested_shell
    if shell is None:
        shell_name = Path(environment.get("SHELL", "zsh") or "zsh").name
        try:
            shell = InitShell(shell_name)
        except ValueError as exc:
            raise InitInputError(
                f"Unsupported shell: {display_text(shell_name)}\n"
                "Supported shells: zsh, bash, fish"
            ) from exc

    project_path = request.requested_project
    if project_path is None:
        configured_project = environment.get("AGENT_RAILS_PROJECT", "")
        project_path = Path(configured_project) if configured_project else None

    profile_path = request.explicit_profile
    if profile_path is None:
        configured_profile = environment.get("AGENT_RAILS_PROFILE", "")
        profile_path = Path(configured_profile) if configured_profile else None
    if profile_path is None and project_path is not None:
        config_home = environment.get("AGENT_RAILS_CONFIG_HOME", "")
        if not config_home:
            config_home = str(user_home / ".agent-rails")
        profile_path = (
            Path(config_home)
            / "profiles/projects"
            / f"{project_path.name}.profile"
        )

    output = _render_guide(
        shell,
        request.kit_home,
        user_home,
        project_path,
        profile_path,
    )
    return InitResult(
        shell=shell,
        project_path=project_path,
        profile_path=profile_path,
        output=output,
    )


def _validate_request(request: InitRequest) -> None:
    if not isinstance(request, InitRequest):
        raise InitInputError("Invalid init request.")
    if request.requested_shell is not None and not isinstance(
        request.requested_shell, InitShell
    ):
        raise InitInputError("Invalid init shell.")
    if request.requested_project is not None and not isinstance(
        request.requested_project, Path
    ):
        raise InitInputError("Init project must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, Path
    ):
        raise InitInputError("Init profile must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise InitInputError("Init kit home must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise InitInputError("Init environment must be a mapping.")
    for key, value in request.environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise InitInputError("Init environment keys and values must be text.")


def _render_guide(
    shell: InitShell,
    kit_home: Path,
    user_home: Path,
    project_path: Optional[Path],
    profile_path: Optional[Path],
) -> str:
    if shell is InitShell.ZSH:
        rc_file = user_home / ".zshrc"
        reload_command = "source ~/.zshrc"
    elif shell is InitShell.BASH:
        rc_file = user_home / ".bashrc"
        reload_command = "source ~/.bashrc"
    else:
        rc_file = user_home / ".config/fish/config.fish"
        reload_command = "source ~/.config/fish/config.fish"

    lines = [
        "Agent Rails Init",
        "",
        f"1. Add this block to {display_text(str(rc_file))}:",
        "",
        "# Agent Rails",
        _assignment(shell, "AGENT_RAILS_HOME", str(kit_home)),
        (
            'fish_add_path "$AGENT_RAILS_HOME/bin"'
            if shell is InitShell.FISH
            else 'export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
        ),
        'alias ar="agent-rails"',
    ]
    if project_path is not None:
        lines.append(_assignment(shell, "AGENT_RAILS_PROJECT", str(project_path)))
    if profile_path is not None:
        lines.append(_assignment(shell, "AGENT_RAILS_PROFILE", str(profile_path)))
    lines.extend(
        (
            "",
            "2. Reload your shell:",
            "",
            reload_command,
            "",
            "3. Verify:",
            "",
            "agent-rails --help",
            "agent-rails home",
        )
    )
    if project_path is not None and profile_path is not None:
        lines.append(
            'ar doctor --project "$AGENT_RAILS_PROJECT" '
            '--profile "$AGENT_RAILS_PROFILE"'
        )
    lines.extend(
        (
            "",
            "4. Connect a project:",
            "",
            "cd /path/to/project",
            "agent-rails setup --tool claude  # or codex / opencode",
            "",
            "# Restart the selected coding agent, then work normally.",
            "# Before delivery:",
            "agent-rails verify",
        )
    )
    return "\n".join(lines) + "\n"


def _assignment(shell: InitShell, name: str, value: str) -> str:
    if shell is InitShell.FISH:
        return f"set -gx {name} {_fish_literal(value)}"
    return f"export {name}={_posix_literal(value)}"


def _is_simple_double_quoted(value: str) -> bool:
    return not any(
        character in value
        for character in ('\\', '"', '$', '`', '\n', '\r', '\t')
    ) and not any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    )


def _posix_literal(value: str) -> str:
    if _is_simple_double_quoted(value):
        return f'"{value}"'
    encoded = os.fsencode(value)
    return "$'" + "".join(f"\\x{byte:02x}" for byte in encoded) + "'"


def _fish_literal(value: str) -> str:
    if _is_simple_double_quoted(value):
        return f'"{value}"'
    return "".join(f"\\x{byte:02x}" for byte in os.fsencode(value))


__all__ = (
    "InitInputError",
    "InitRequest",
    "InitResult",
    "InitShell",
    "run_init",
)
