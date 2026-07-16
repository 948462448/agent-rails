"""Public Agent Rails command tree and process-replacement dispatcher."""

from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import sys
from typing import Mapping, Optional, Sequence
import unicodedata


_USAGE = """Usage:
  agent-rails setup [--project PATH] [--profile PATH] [--tool auto|claude|codex|opencode|all] [--mode local|project] [--no-session-hook] [--dry-run]
  agent-rails run [--project PATH] [--profile PATH] [--model NAME] [--pack-mode lite|normal|deep|audit] [goal text...]
  agent-rails verify [--project PATH] [--profile PATH] [--print-only] [--publish] [--base REF] [--target-ref REF] [--no-secret-scan]

Advanced:
  agent-rails --version
  agent-rails version
  agent-rails update --tool claude|codex|opencode [--project PATH] [--profile PATH] [--mode local|project] [--session-hook] [--global-reminder] [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
  agent-rails upgrade self [--version VERSION] [--repository OWNER/REPO] [--install-root PATH] [--bin-dir PATH] [--skip-tests] [--dry-run]
  agent-rails init [--shell zsh|bash|fish] [--project PATH] [--profile PATH]
  agent-rails pack [--project PATH] [agent-context-pack args...]
  agent-rails check [--project PATH] [agent-check args...]
  agent-rails publish check [--project PATH] [--profile PATH] [--base REF] [--target-ref REF] [--no-secret-scan]
  agent-rails estimate [--profile PATH] [--model NAME] [--tokenizer auto|char|tiktoken|command|huggingface] [--tokenizer-command CMD] [--tokenizer-path PATH] [--file PATH] [text...]
  agent-rails doctor [--project PATH] [--profile PATH] [--online-memory-smoke]
  agent-rails profile init [--project PATH] [--name NAME] [--scope user|project] [--output PATH] [--force] [--print-only]
  agent-rails claude install [--project PATH] [--profile PATH] [--mode local|project] [--global-reminder] [--session-hook]
  agent-rails claude uninstall [--project PATH] [--global-reminder] [--session-hook] [--dry-run]
  agent-rails codex install [--project PATH] [--profile PATH] [--fix-project] [--mode local|project] [--dry-run]
  agent-rails codex doctor [--project PATH]
  agent-rails codex uninstall [--dry-run]
  agent-rails opencode install [--project PATH] [--profile PATH] [--mode local|project] [--dry-run] [--force]
  agent-rails opencode doctor [--project PATH]
  agent-rails opencode uninstall [--project PATH] [--dry-run] [--force]
  agent-rails memory suggest [--project PATH] [--profile PATH] [--decision keep|skip|update|merge] [--write-local] [notes...]
  agent-rails skills install --dest PATH [--dry-run] [skill-name...]
  agent-rails home

Examples:
  agent-rails setup --tool claude
  agent-rails run "review current changes"
  agent-rails verify
  agent-rails verify --publish --base <deployed-revision>

Run `agent-rails <command> --help` for advanced command details.
"""

_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")


class _PublicCliError(RuntimeError):
    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def main(argv: Optional[Sequence[str]] = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        home = _bootstrap_home(os.environ)
        version = _resolve_version(home, os.environ)
    except _PublicCliError as exc:
        print(str(exc), file=sys.stderr)
        return exc.exit_code

    environment = dict(os.environ)
    environment["AGENT_RAILS_HOME"] = str(home)
    environment["AGENT_RAILS_VERSION"] = version
    if not arguments or arguments[0] in {"--help", "-h"}:
        print(_USAGE, end="")
        return 0
    command = arguments.pop(0)
    if command in {"--version", "version"}:
        print(f"agent-rails {version}")
        return 0
    if command == "home":
        print(_terminal_literal(str(home)))
        return 0

    try:
        return _dispatch(command, arguments, home, environment)
    except _PublicCliError as exc:
        if str(exc):
            print(str(exc), file=sys.stderr)
        return exc.exit_code
    except OSError:
        print("Unable to enter the requested project or execute Agent Rails.", file=sys.stderr)
        return 1


def _dispatch(
    command: str,
    arguments: list[str],
    home: Path,
    environment: Mapping[str, str],
) -> int:
    direct = {
        "setup": ("setup-application", "-E"),
        "update": ("update-application", "-E"),
        "init": ("init-application", "-I"),
        "run": ("run-application", "-E"),
        "verify": ("verify-application", "-E"),
        # Estimate may load optional tokenizers from the user's normal Python
        # environment, so retain -E rather than isolated mode's implicit -s.
        "estimate": ("estimate", "-E"),
        "doctor": ("doctor-application", "-E"),
        "codex": ("codex-adapter", "-E"),
        "opencode": ("opencode-adapter", "-E"),
    }
    route = direct.get(command)
    if route is not None:
        internal_command, isolation_flag = route
        return _exec_python(
            home,
            internal_command,
            arguments,
            environment,
            isolation_flag=isolation_flag,
        )
    if command in {"pack", "check"}:
        return _run_in_project(
            home,
            "task-pack" if command == "pack" else "agent-check",
            arguments,
            environment,
        )
    if command == "upgrade":
        return _upgrade(home, arguments, environment)
    if command == "publish":
        return _publish(home, arguments, environment)
    if command == "profile":
        if not arguments or arguments[0] != "init":
            raise _usage_error()
        return _exec_python(
            home,
            "profile-init",
            ["--agent-rails-home", str(home), *arguments[1:]],
            environment,
        )
    if command == "claude":
        return _claude(home, arguments, environment)
    if command == "memory":
        return _required_subcommand(
            home, arguments, "suggest", "memory-suggest", environment
        )
    if command == "skills":
        return _required_subcommand(
            home,
            arguments,
            "install",
            "skills-install",
            environment,
            isolation_flag="-I",
        )
    raise _usage_error()


def _upgrade(
    home: Path, arguments: list[str], environment: Mapping[str, str]
) -> int:
    if not arguments or arguments[0] in {"--help", "-h"}:
        return _exec_python(home, "update-application", ["--help"], environment)
    if arguments[0] != "self":
        raise _usage_error()
    return _exec_python(
        home,
        "update-application",
        ["--self-only", *arguments[1:]],
        environment,
    )


def _publish(
    home: Path, arguments: list[str], environment: Mapping[str, str]
) -> int:
    if not arguments or arguments[0] in {"--help", "-h"}:
        return _exec_python(home, "publish-check", ["--help"], environment)
    if arguments[0] != "check":
        raise _usage_error()
    return _run_in_project(
        home,
        "publish-check",
        arguments[1:],
        environment,
    )


def _required_subcommand(
    home: Path,
    arguments: list[str],
    required: str,
    internal_command: str,
    environment: Mapping[str, str],
    *,
    isolation_flag: str = "-E",
) -> int:
    if not arguments or arguments[0] != required:
        raise _usage_error()
    return _exec_python(
        home,
        internal_command,
        arguments[1:],
        environment,
        isolation_flag=isolation_flag,
    )


def _claude(
    home: Path, arguments: list[str], environment: Mapping[str, str]
) -> int:
    if not arguments:
        raise _usage_error()
    subcommand = arguments[0]
    remaining = arguments[1:]
    if subcommand == "install":
        return _exec_python(
            home, "claude-adapter", ["install", *remaining], environment
        )
    if subcommand == "uninstall":
        return _exec_python(
            home, "claude-adapter", ["uninstall", *remaining], environment
        )
    if subcommand == "upgrade":
        print(
            "Deprecated: use `agent-rails doctor --fix` for repair, "
            "`agent-rails update --tool claude` for project maintenance, or "
            "`agent-rails upgrade self` for kit-only updates.",
            file=sys.stderr,
        )
        return _exec_python(
            home,
            "claude-adapter",
            ["install", "--force", *remaining],
            environment,
        )
    raise _usage_error()


def _run_in_project(
    home: Path,
    internal_command: str,
    arguments: list[str],
    environment: Mapping[str, str],
) -> int:
    entry_directory = Path(os.path.realpath(os.getcwd()))
    selected: Optional[str] = None
    forwarded: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument != "--project":
            forwarded.append(argument)
            index += 1
            continue
        if selected is not None or index + 1 >= len(arguments):
            raise _usage_error()
        selected = arguments[index + 1]
        index += 2
    if selected == "":
        raise _usage_error()
    project = entry_directory if selected is None else Path(selected)
    if not project.is_absolute():
        project = entry_directory / project
    project = Path(os.path.realpath(os.fspath(project)))
    os.chdir(str(project))
    return _exec_python(home, internal_command, forwarded, environment)


def _exec_python(
    home: Path,
    internal_command: str,
    arguments: Sequence[str],
    environment: Mapping[str, str],
    *,
    isolation_flag: str = "-E",
) -> int:
    helper = home / "scripts" / "agent-python-cli.py"
    _validate_python_helper(helper)
    executable = sys.executable
    child_environment = dict(environment)
    child_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    previous_umask = os.umask(0o077)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
        os.execve(
            executable,
            [
                executable,
                isolation_flag,
                str(helper),
                internal_command,
                *arguments,
            ],
            child_environment,
        )
    except FileNotFoundError as exc:
        raise _PublicCliError(
            "Agent Rails Python runtime entrypoint was not found.", 127
        ) from exc
    except PermissionError as exc:
        raise _PublicCliError(
            "Agent Rails Python runtime entrypoint is not executable.", 126
        ) from exc
    finally:
        os.umask(previous_umask)
    return 0


def _validate_python_helper(helper: Path) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        descriptor = os.open(helper, flags)
    except FileNotFoundError as exc:
        raise _PublicCliError(
            "Agent Rails Python command helper was not found.", 127
        ) from exc
    except OSError as exc:
        raise _PublicCliError(
            "Agent Rails Python command helper is not readable.", 126
        ) from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _PublicCliError(
                "Agent Rails Python command helper is not a regular file.", 126
            )
    finally:
        os.close(descriptor)


def _bootstrap_home(environment: Mapping[str, str]) -> Path:
    value = environment.get("AGENT_RAILS_HOME", "")
    if not value:
        raise _PublicCliError("AGENT_RAILS_HOME is required.", 2)
    return Path(value)


def _resolve_version(home: Path, environment: Mapping[str, str]) -> str:
    override = environment.get("AGENT_RAILS_VERSION_OVERRIDE", "")
    if override:
        return _validated_version(override, exit_code=2)
    path = home / "VERSION"
    if not path.is_file():
        return "0.0.0-dev"
    try:
        lines = path.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise _PublicCliError("Unable to read Agent Rails VERSION.", 1) from exc
    for line in lines:
        fields = line.split()
        if fields:
            return _validated_version(fields[0], exit_code=1)
    return "0.0.0-dev"


def _validated_version(value: str, *, exit_code: int) -> str:
    if not _VERSION_PATTERN.fullmatch(value):
        raise _PublicCliError("Agent Rails version is invalid.", exit_code)
    return value


def _terminal_literal(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if (
            category in {"Cc", "Cf", "Zl", "Zp"}
            or 0xD800 <= codepoint <= 0xDFFF
        ):
            if character == "\n":
                escaped.append("\\n")
            elif character == "\r":
                escaped.append("\\r")
            elif character == "\t":
                escaped.append("\\t")
            elif codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _usage_error() -> _PublicCliError:
    return _PublicCliError(_USAGE.rstrip("\n"), 2)
