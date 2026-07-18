from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import tempfile
from typing import Mapping, Optional, Sequence, TextIO

from agent_rails.config.target_project import TargetProjectError, resolve_target_project
from agent_rails.core.paths import AgentRailsPaths, sanitize_slug


class ProfileInitError(RuntimeError):
    pass


class ProfileAlreadyExistsError(ProfileInitError):
    pass


@dataclass(frozen=True)
class VerificationCommands:
    project: str = ""
    node: str = ""
    python: str = ""
    java: str = ""
    go: str = ""
    rust: str = ""


@dataclass(frozen=True)
class ProfileInitPlan:
    project_root: Path
    profile_name: str
    scope: str
    output_path: str
    content: str
    enforce_project_boundary: bool = False


def build_profile_init_plan(
    project: Path,
    *,
    kit_home: Path,
    profile_name: str = "",
    scope: str = "user",
    output_path: str = "",
    environment: Optional[Mapping[str, str]] = None,
) -> ProfileInitPlan:
    if scope not in {"user", "project"}:
        raise ValueError(f"Unknown Profile scope: {scope}")

    env = dict(os.environ if environment is None else environment)
    context = resolve_target_project(
        project,
        kit_home=kit_home,
        environment=env,
        load_profile=False,
    )
    name = profile_name or sanitize_slug(context.default_name)
    _validate_profile_name(name)

    paths = AgentRailsPaths.from_environment(kit_home, env)
    if output_path:
        target_path = output_path
    elif scope == "project":
        target_path = str(context.root / ".agent-rails" / "profile")
    else:
        # Keep the configured home lexical in user-visible output. Existing
        # Profiles may intentionally use values such as "~/.agent-rails", and
        # Path would normalize repeated separators before we print the result.
        target_path = f"{paths.config_home}/profiles/projects/{name}.profile"

    commands = detect_verification_commands(context.root)
    content = render_profile(
        project_root=context.root,
        profile_name=name,
        entry_doc=detect_entry_doc(context.root),
        commands=commands,
    )
    return ProfileInitPlan(
        project_root=context.root,
        profile_name=name,
        scope=scope,
        output_path=target_path,
        content=content,
        enforce_project_boundary=scope == "project" and not output_path,
    )


def detect_entry_doc(project_root: Path) -> str:
    for name in ("AGENTS.md", "CLAUDE.md", "README.md"):
        if (project_root / name).is_file():
            return name
    return "AGENTS.md"


def detect_verification_commands(project_root: Path) -> VerificationCommands:
    project_command = ""
    if _makefile_has_target(project_root / "Makefile", "test"):
        project_command = "make test"
    elif _makefile_has_target(project_root / "Makefile", "check"):
        project_command = "make check"

    node_command = ""
    root_scripts = _package_scripts(project_root / "package.json")
    frontend_scripts = _package_scripts(project_root / "frontend" / "package.json")
    if "lint" in root_scripts:
        node_command = "npm run lint"
    elif "test" in root_scripts:
        node_command = "npm test"
    elif "lint" in frontend_scripts:
        node_command = "cd frontend && npm run lint"
    elif "test" in frontend_scripts:
        node_command = "cd frontend && npm test"

    python_command = ""
    if any(
        (project_root / name).is_file()
        for name in ("pyproject.toml", "pytest.ini", "setup.py")
    ) or _contains_python_test(project_root / "tests"):
        python_command = "python3 -m pytest"

    java_command = ""
    if (project_root / "mvnw").is_file():
        java_command = "./mvnw test"
    elif (project_root / "pom.xml").is_file():
        java_command = "mvn test"
    elif (project_root / "gradlew").is_file():
        java_command = "./gradlew test"
    elif any(
        (project_root / name).is_file()
        for name in (
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        )
    ):
        java_command = "gradle test"

    return VerificationCommands(
        project=project_command,
        node=node_command,
        python=python_command,
        java=java_command,
        go="go test ./..." if (project_root / "go.mod").is_file() else "",
        rust="cargo test" if (project_root / "Cargo.toml").is_file() else "",
    )


def render_profile(
    *,
    project_root: Path,
    profile_name: str,
    entry_doc: str,
    commands: VerificationCommands,
) -> str:
    escaped_name = _escape_shell_double_quoted(profile_name)
    escaped_entry_doc = _escape_shell_double_quoted(entry_doc)
    lines = [
        f"# Agent Rails profile for {_escape_shell_comment(profile_name)}.",
        f"# Generated from `{_escape_shell_comment(str(project_root))}`.",
        "",
        "# shellcheck source=/dev/null",
        'source "$AGENT_RAILS_HOME/profiles/default.profile"',
        "",
        f'PROJECT_NAME="{escaped_name}"',
        "# Leave TASK_PACK_PATH unset for the default worktree-isolated path:",
        "# ${AGENT_RAILS_CONFIG_HOME}/agent-context/${PROJECT_WORKTREE_SLUG}-task-pack.md",
        f'MEMORY_LOCAL_DIR="${{AGENT_RAILS_CONFIG_HOME}}/memory/{escaped_name}"',
        "# Online memory is an optional read-only external command Adapter.",
        "# The Adapter owns credentials/protocol and writes UTF-8 Markdown to stdout.",
        'MEMORY_PROVIDER="${MEMORY_PROVIDER:-local}"',
        'AGENT_RAILS_ONLINE_MEMORY_CMD="${AGENT_RAILS_ONLINE_MEMORY_CMD:-}"',
        'AGENT_RAILS_ONLINE_MEMORY_LIMIT="${AGENT_RAILS_ONLINE_MEMORY_LIMIT:-5}"',
        'AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS="${AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS:-8}"',
        "",
        "# Model preset and context budget. Use qwen3.7-max, glm5.1, deepseek-v4-pro, or deepseek-v4-flash when applicable.",
        'AGENT_RAILS_MODEL="${AGENT_RAILS_MODEL:-generic}"',
        'AGENT_RAILS_PACK_MODE="${AGENT_RAILS_PACK_MODE:-normal}"',
        'AGENT_RAILS_CONTEXT_BUDGET_TOKENS="${AGENT_RAILS_CONTEXT_BUDGET_TOKENS:-}"',
        'AGENT_RAILS_CONTEXT_BUDGET_CHARS="${AGENT_RAILS_CONTEXT_BUDGET_CHARS:-0}"',
        'AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}"',
        'AGENT_RAILS_TOKENIZER="${AGENT_RAILS_TOKENIZER:-auto}"',
        'AGENT_RAILS_TOKENIZER_CMD="${AGENT_RAILS_TOKENIZER_CMD:-}"',
        'AGENT_RAILS_TIKTOKEN_ENCODING="${AGENT_RAILS_TIKTOKEN_ENCODING:-cl100k_base}"',
        'AGENT_RAILS_BUDGET_GIT_PERCENT="${AGENT_RAILS_BUDGET_GIT_PERCENT:-20}"',
        'AGENT_RAILS_BUDGET_MEMORY_PERCENT="${AGENT_RAILS_BUDGET_MEMORY_PERCENT:-40}"',
        'AGENT_RAILS_BUDGET_VERIFY_PERCENT="${AGENT_RAILS_BUDGET_VERIFY_PERCENT:-20}"',
        'AGENT_RAILS_BUDGET_CONTRACT_PERCENT="${AGENT_RAILS_BUDGET_CONTRACT_PERCENT:-20}"',
        'AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS="${AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS:-1600}"',
        "",
        'AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT="${AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT:-5}"',
        'AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS="${AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS:-4000}"',
        "",
        'AGENT_RAILS_CHANGED_FILE_SORT="${AGENT_RAILS_CHANGED_FILE_SORT:-smart}"',
        "",
        f'ENTRY_DOC_ROOT="{escaped_entry_doc}"',
        'DOMAIN_DOC_ROOT="CONTEXT.md"',
        'DOMAIN_DOC_MAP="CONTEXT-MAP.md"',
        'ADR_DIR="docs/adr"',
        'AGENT_DOC_DIR="docs/agents"',
        'ISSUE_TRACKER_DOC="docs/agents/issue-tracker.md"',
        'TRIAGE_LABELS_DOC="docs/agents/triage-labels.md"',
        "",
        "# Verification commands. Keep these lightweight and agent-runnable.",
    ]
    for variable, value in (
        ("VERIFY_PROJECT", commands.project),
        ("VERIFY_NODE", commands.node),
        ("VERIFY_PYTHON", commands.python),
        ("VERIFY_JAVA", commands.java),
        ("VERIFY_GO", commands.go),
        ("VERIFY_RUST", commands.rust),
    ):
        if value:
            lines.append(f'{variable}="{_escape_shell_double_quoted(value)}"')
    return "\n".join(lines) + "\n"


def write_profile(plan: ProfileInitPlan, *, force: bool = False) -> None:
    if plan.enforce_project_boundary:
        _write_project_scoped_profile(plan, force=force)
        return

    output = Path(plan.output_path)
    if output.exists() and not force:
        raise ProfileAlreadyExistsError(str(output))
    if output.is_dir():
        raise ProfileInitError(f"Profile output is a directory: {output}")

    parent = output.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.",
            dir=str(parent),
        )
    except OSError as exc:
        raise ProfileInitError(f"Could not prepare Profile output: {output}") from exc

    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(plan.content)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(str(temporary), str(output))
        else:
            try:
                os.link(str(temporary), str(output))
            except FileExistsError:
                raise ProfileAlreadyExistsError(str(output)) from None
            temporary.unlink()
    except ProfileAlreadyExistsError:
        raise
    except OSError as exc:
        raise ProfileInitError(f"Could not write Profile: {output}") from exc
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _write_project_scoped_profile(
    plan: ProfileInitPlan,
    *,
    force: bool,
) -> None:
    expected = plan.project_root / ".agent-rails" / "profile"
    if plan.output_path != str(expected):
        raise ProfileInitError("Project Profile output escaped the Target Project boundary.")

    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        project_fd = os.open(str(plan.project_root), directory_flags)
    except OSError as exc:
        raise ProfileInitError(
            f"Could not open Target Project directory: {plan.project_root}"
        ) from exc

    try:
        try:
            os.mkdir(".agent-rails", mode=0o700, dir_fd=project_fd)
        except FileExistsError:
            pass
        try:
            profile_dir_fd = os.open(
                ".agent-rails",
                directory_flags,
                dir_fd=project_fd,
            )
        except OSError as exc:
            raise ProfileInitError(
                "Project Profile directory must be a real directory inside "
                f"the Target Project: {expected.parent}"
            ) from exc
        try:
            _write_profile_at(
                profile_dir_fd,
                "profile",
                plan.content,
                force=force,
            )
        finally:
            os.close(profile_dir_fd)
    except ProfileInitError:
        raise
    except OSError as exc:
        raise ProfileInitError(f"Could not write Profile: {expected}") from exc
    finally:
        os.close(project_fd)


def _write_profile_at(
    directory_fd: int,
    filename: str,
    content: str,
    *,
    force: bool,
) -> None:
    try:
        existing = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        existing = None
    if existing is not None:
        if stat.S_ISDIR(existing.st_mode):
            raise ProfileInitError(f"Profile output is a directory: {filename}")
        if not force:
            raise ProfileAlreadyExistsError(filename)

    temporary_name = ""
    descriptor = -1
    open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    open_flags |= getattr(os, "O_NOFOLLOW", 0)
    for _ in range(32):
        candidate = f".{filename}.{secrets.token_hex(8)}"
        try:
            descriptor = os.open(
                candidate,
                open_flags,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        temporary_name = candidate
        break
    if descriptor < 0:
        raise ProfileInitError("Could not allocate a temporary Profile file.")

    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if force:
            os.replace(
                temporary_name,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        else:
            try:
                os.link(
                    temporary_name,
                    filename,
                    src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                raise ProfileAlreadyExistsError(filename) from None
            os.unlink(temporary_name, dir_fd=directory_fd)
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if temporary_name:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def main(
    args: Sequence[str],
    *,
    kit_home: Path,
    environment: Optional[Mapping[str, str]] = None,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = _argument_parser()
    try:
        options = parser.parse_args(list(args))
    except SystemExit as exc:
        return int(exc.code)

    try:
        plan = build_profile_init_plan(
            Path(options.project),
            kit_home=kit_home,
            profile_name=options.name,
            scope=options.scope,
            output_path=options.output,
            environment=environment,
        )
    except (TargetProjectError, ValueError) as exc:
        print(str(exc), file=stderr)
        return 2

    if options.print_only:
        stdout.write(plan.content)
        return 0

    try:
        write_profile(plan, force=options.force)
    except ProfileAlreadyExistsError:
        print(f"Profile already exists: {plan.output_path}", file=stderr)
        print("Use --force to overwrite.", file=stderr)
        return 1
    except ProfileInitError as exc:
        print(str(exc), file=stderr)
        return 1

    print(f"Wrote {plan.output_path}", file=stdout)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-rails profile init",
        description="Generate a local Agent Rails Profile for a Target Project.",
        epilog=(
            "User Profiles default to ~/.agent-rails/profiles/projects/. "
            "Use --scope project for <project>/.agent-rails/profile."
        ),
    )
    parser.add_argument("--project", default=os.getcwd())
    parser.add_argument("--name", default="")
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--output", default="")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print-only", action="store_true")
    return parser


def _validate_profile_name(name: str) -> None:
    if not name:
        raise ValueError("Could not derive Profile name from Target Project.")
    if name in {".", ".."} or "/" in name or any(ord(char) < 32 for char in name):
        raise ValueError("Profile name must not contain path separators or control characters.")


def _makefile_has_target(path: Path, target: str) -> bool:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(rf"^{re.escape(target)}:", content, flags=re.MULTILINE) is not None


def _package_scripts(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    scripts = payload.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _contains_python_test(tests_dir: Path) -> bool:
    if not tests_dir.is_dir():
        return False
    try:
        return next(tests_dir.rglob("*.py"), None) is not None
    except OSError:
        return False


def _escape_shell_double_quoted(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )


def _escape_shell_comment(value: str) -> str:
    return "".join(
        char if 32 <= ord(char) != 127 else f"\\x{ord(char):02x}"
        for char in value
    )
