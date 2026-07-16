"""Build a stable verification plan from changed Target Project paths."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shlex
import stat
from typing import Tuple

from agent_rails.git._runner import run_git


_CONTRACTS = re.compile(r"^contracts/")
_BACKEND = re.compile(r"^backend/|^Makefile$")
_RUNTIME = re.compile(r"^runtime/")
_FRONTEND = re.compile(r"^frontend/")
_NODE = re.compile(
    r"(^package(-lock)?\.json$|^pnpm-lock\.yaml$|^yarn\.lock$|\.(js|jsx|ts|tsx)$)"
)
_PYTHON = re.compile(
    r"(^pyproject\.toml$|^requirements.*\.txt$|^setup\.py$|^pytest\.ini$|\.py$)"
)
_JAVA = re.compile(
    r"(^pom\.xml$|^mvnw$|^build\.gradle$|^settings\.gradle$|\.java$|\.kt$)"
)
_GO = re.compile(r"(^go\.mod$|^go\.sum$|\.go$)")
_RUST = re.compile(r"(^Cargo\.toml$|^Cargo\.lock$|\.rs$)")
_DOLPHIN_PYTHON = re.compile(r"^dolphin/.*\.py$", re.DOTALL)
_DOLPHIN = re.compile(r"^dolphin/")
_SHELL_ENTRYPOINT = re.compile(
    r"^(bin/agent-rails|scripts/.*\.sh)$", re.DOTALL
)
_SHELL_TEST = re.compile(r"^tests/.*\.sh$", re.DOTALL)
_KNOWN_SUITES = ("core", "adapters", "workflows", "context")
_KNOWN_SUITE_PATHS = frozenset(
    f"tests/suites/{suite}.sh" for suite in _KNOWN_SUITES
)
_NO_COMMAND_HINT = (
    "- No automated command selected. For docs-only changes, manually review "
    "rendered Markdown and links.\n"
)


class VerificationPlanError(RuntimeError):
    """The verification plan could not be built or serialized safely."""


@dataclass(frozen=True)
class VerificationCommands:
    """Opaque commands supplied by the Target Project Profile."""

    contracts: str = ""
    backend: str = ""
    runtime: str = ""
    frontend: str = ""
    node: str = ""
    python: str = ""
    java: str = ""
    go: str = ""
    rust: str = ""
    dolphin: str = ""
    shell: str = ""
    tests: str = ""
    project: str = ""


@dataclass(frozen=True)
class VerificationPlanRequest:
    project: Path
    changed_paths: Tuple[str, ...]
    commands: VerificationCommands
    target_ref: str = "HEAD"
    target_ref_explicit: bool = False


@dataclass(frozen=True)
class VerificationStep:
    reason: str
    command: str


@dataclass(frozen=True)
class VerificationPlan:
    steps: Tuple[VerificationStep, ...]


def build_verification_plan(request: VerificationPlanRequest) -> VerificationPlan:
    """Select commands in the stable order used by the Agent Check Application."""

    paths = request.changed_paths
    steps: list[VerificationStep] = []
    seen_commands: set[str] = set()

    def add(reason: str, command: str) -> None:
        if not command or command in seen_commands:
            return
        _reject_nul(command, "verification command")
        steps.append(VerificationStep(reason=reason, command=command))
        seen_commands.add(command)

    matchers: Tuple[Tuple[re.Pattern[str], str, str], ...] = (
        (_CONTRACTS, "contracts changed", request.commands.contracts),
        (_BACKEND, "backend changed", request.commands.backend),
        (_RUNTIME, "runtime changed", request.commands.runtime),
        (_FRONTEND, "frontend changed", request.commands.frontend),
        (_NODE, "node/js changed", request.commands.node),
        (_PYTHON, "python changed", request.commands.python),
        (_JAVA, "java/jvm changed", request.commands.java),
        (_GO, "go changed", request.commands.go),
        (_RUST, "rust changed", request.commands.rust),
    )
    for pattern, reason, command in matchers:
        if _has_changed(paths, pattern):
            add(reason, command)

    if _has_changed(paths, _DOLPHIN_PYTHON):
        add("dolphin python changed", request.commands.dolphin)
    elif _has_changed(paths, _DOLPHIN):
        add("dolphin changed", request.commands.dolphin)

    shell_paths = tuple(path for path in paths if _SHELL_ENTRYPOINT.search(path))
    if shell_paths:
        existing_shell_paths = tuple(
            path for path in shell_paths if _verification_file_exists(request, path)
        )
        if existing_shell_paths:
            command = request.commands.shell or _default_shell_command(
                existing_shell_paths
            )
            add("shell entrypoints changed", command)

    if _has_changed(paths, _SHELL_TEST):
        add("shell tests changed", _test_command(request))

    if paths and not steps:
        add("project default", request.commands.project)

    return VerificationPlan(steps=tuple(steps))


def render_suggestions(plan: VerificationPlan) -> str:
    if not plan.steps:
        return _NO_COMMAND_HINT
    return "".join(
        f"- [{step.reason}] {step.command}\n" for step in plan.steps
    )


def write_verification_plan_bundle(
    output_dir: Path, plan: VerificationPlan
) -> None:
    """Write human suggestions and lossless reason/command execution records."""

    for step in plan.steps:
        _reject_nul(step.reason, "verification reason")
        _reject_nul(step.command, "verification command")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "suggestions.md").write_text(
            _valid_utf8(render_suggestions(plan)), encoding="utf-8"
        )
        fields = (
            field
            for step in plan.steps
            for field in (step.reason, step.command)
        )
        payload = b"".join(
            field.encode("utf-8", errors="surrogateescape") + b"\0"
            for field in fields
        )
        (output_dir / "steps0").write_bytes(payload)
    except (OSError, UnicodeError) as exc:
        raise VerificationPlanError(
            f"Unable to write verification plan: {output_dir}"
        ) from exc


def _has_changed(paths: Tuple[str, ...], pattern: re.Pattern[str]) -> bool:
    return any(pattern.search(path) is not None for path in paths)


def _verification_file_exists(
    request: VerificationPlanRequest, path: str
) -> bool:
    _reject_nul(path, "changed path")
    if not request.target_ref_explicit:
        candidate = request.project / path
        try:
            project_root = request.project.resolve()
            candidate.resolve().relative_to(project_root)
            return stat.S_ISREG(candidate.lstat().st_mode)
        except (OSError, RuntimeError, ValueError):
            return False
    try:
        result = run_git(
            request.project,
            (
                "--literal-pathspecs",
                "ls-tree",
                "-z",
                request.target_ref,
                "--",
                path,
            ),
        )
    except OSError as exc:
        raise VerificationPlanError("Git command is unavailable.") from exc
    if result.returncode != 0 or not result.stdout:
        return False
    mode = result.stdout.split(" ", 1)[0]
    return mode in {"100644", "100755"}


def _default_shell_command(paths: Tuple[str, ...]) -> str:
    return "bash -n " + " ".join(_shell_quote(path) for path in paths)


def _shell_quote(value: str) -> str:
    """Quote one path for the Bash-compatible runner without executing it now."""

    _reject_nul(value, "changed path")
    if any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        escaped = []
        for character in value:
            codepoint = ord(character)
            if character == "\\":
                escaped.append("\\\\")
            elif character == "'":
                escaped.append("\\'")
            elif character == "\n":
                escaped.append("\\n")
            elif character == "\r":
                escaped.append("\\r")
            elif character == "\t":
                escaped.append("\\t")
            elif codepoint < 32 or codepoint == 127:
                escaped.append(f"\\x{codepoint:02x}")
            elif 0xDC80 <= codepoint <= 0xDCFF:
                escaped.append(f"\\x{codepoint - 0xDC00:02x}")
            elif 0xD800 <= codepoint <= 0xDFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(character)
        return "$'" + "".join(escaped) + "'"
    return shlex.quote(value)


def _test_command(request: VerificationPlanRequest) -> str:
    if request.commands.tests:
        return request.commands.tests

    shell_tests = tuple(
        path for path in request.changed_paths if _SHELL_TEST.search(path)
    )
    if any(path not in _KNOWN_SUITE_PATHS for path in shell_tests):
        return "bash tests/run.sh"
    suites = tuple(
        suite
        for suite in _KNOWN_SUITES
        if f"tests/suites/{suite}.sh" in shell_tests
    )
    if suites:
        return "bash tests/run.sh " + " ".join(suites)
    return "bash tests/run.sh"


def _reject_nul(value: str, label: str) -> None:
    if "\0" in value:
        raise VerificationPlanError(f"NUL byte is not allowed in {label}.")


def _valid_utf8(value: str) -> str:
    return value.encode("utf-8", errors="backslashreplace").decode("utf-8")
