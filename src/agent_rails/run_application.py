"""Compose Task Pack generation, estimation, and handoff as one Run facade."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Mapping, Optional, Tuple
import unicodedata

from agent_rails.config.profile import ProfileLoadError
from agent_rails.config.target_project import TargetProjectError, resolve_target_project
from agent_rails.context.pack_application import (
    PackApplicationError,
    PackApplicationRequest,
    PackApplicationResult,
    PackCliOverrides,
    generate_task_pack,
    prepare_task_pack,
)
from agent_rails.context.pack_renderer import PackRendererError, TokenizerSettings
from agent_rails.estimate import EstimateInput, render_estimate
from agent_rails.models.tokenizer import TokenCount, TokenCounter


_DEFAULT_GOAL = "TODO: describe the concrete user goal."
_PACK_MODES = frozenset({"lite", "normal", "deep", "audit"})
_TOKENIZER_MODES = frozenset(
    {"auto", "char", "command", "tiktoken", "huggingface", "hf"}
)


class RunApplicationError(RuntimeError):
    """The Run facade could not complete."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        events: Tuple["RunEvent", ...] = (),
        pack_result: Optional[PackApplicationResult] = None,
    ) -> None:
        super().__init__(_terminal_literal(message))
        self.exit_code = exit_code
        self.events = events
        self.pack_result = pack_result

    @property
    def stdout(self) -> str:
        return _render_events(self.events, RunEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, RunEventStream.STDERR)


class RunInputError(RunApplicationError):
    """The caller supplied an invalid Run request."""

    def __init__(self, message: str) -> None:
        super().__init__(message, exit_code=2)


class RunMode(str, Enum):
    EXECUTE = "execute"
    PRINT_ONLY = "print-only"


class RunEventStream(str, Enum):
    STDOUT = "stdout"
    STDERR = "stderr"


@dataclass(frozen=True)
class RunCliOverrides:
    mode: RunMode = RunMode.EXECUTE
    model: Optional[str] = None
    pack_mode: Optional[str] = None
    context_budget_chars: Optional[str] = None
    context_budget_tokens: Optional[str] = None
    tokenizer: Optional[str] = None
    tokenizer_command: Optional[str] = None
    tokenizer_path: Optional[str] = None


@dataclass(frozen=True)
class RunApplicationRequest:
    requested_project: Path
    kit_home: Path
    explicit_profile: Optional[str]
    goal: str
    overrides: RunCliOverrides
    working_directory: Path
    environment: Mapping[str, str]


@dataclass(frozen=True)
class RunEvent:
    stream: RunEventStream
    text: str


@dataclass(frozen=True)
class RunApplicationResult:
    project_root: Path
    profile_path: str
    task_pack_path: str
    effective_pack_mode: str
    inferred_pack_mode: Optional[str]
    pack_result: Optional[PackApplicationResult]
    exit_code: int
    events: Tuple[RunEvent, ...]

    @property
    def stdout(self) -> str:
        return _render_events(self.events, RunEventStream.STDOUT)

    @property
    def stderr(self) -> str:
        return _render_events(self.events, RunEventStream.STDERR)


def run_agent_rails(request: RunApplicationRequest) -> RunApplicationResult:
    """Resolve once, then preview or execute one complete Run handoff."""

    _validate_request(request)
    working_directory = _canonical_directory(
        request.working_directory,
        "Run working directory",
        RunApplicationError,
    )
    requested_project = _anchored_path(
        request.requested_project, working_directory
    )
    if not requested_project.is_dir():
        raise RunInputError(
            "Project directory not found: "
            f"{_terminal_literal(str(request.requested_project))}"
        )
    requested_kit_home = _anchored_path(request.kit_home, working_directory)
    kit_home = _canonical_directory(
        requested_kit_home,
        "Agent Rails home",
        RunApplicationError,
    )
    kit_home_display = Path(
        os.path.abspath(
            os.fspath(_anchored_path(request.kit_home, working_directory))
        )
    )
    environment = dict(request.environment)

    try:
        unresolved = resolve_target_project(
            requested_project,
            kit_home=kit_home,
            explicit_profile=request.explicit_profile,
            environment=environment,
            require_profile=False,
            load_profile=False,
        )
    except TargetProjectError as exc:
        raise RunInputError(_terminal_literal(str(exc))) from exc

    if request.explicit_profile is None:
        internal_profile = unresolved.profile_path
    elif unresolved.profile_path != request.explicit_profile:
        internal_profile = unresolved.profile_path
    else:
        internal_profile = _internal_profile(
            request.explicit_profile, working_directory
        )
    goal = request.goal or _DEFAULT_GOAL
    inferred_mode = (
        None
        if request.overrides.pack_mode is not None
        else _infer_pack_mode(goal)
    )
    requested_mode = request.overrides.pack_mode or inferred_mode
    pack_request = PackApplicationRequest(
        requested_project=unresolved.root,
        kit_home=kit_home,
        explicit_profile=internal_profile,
        goal=goal,
        overrides=_pack_overrides(request.overrides, requested_mode),
        environment=environment,
    )

    try:
        if request.overrides.mode is RunMode.PRINT_ONLY:
            prepared = prepare_task_pack(pack_request)
            pack_result = None
            effective_mode = prepared.policy.density.mode
            task_pack_path = prepared.output.display_path
            task_pack_filesystem_path = prepared.output.filesystem_path
            effective_profile = prepared.context.profile_path
        else:
            pack_result = generate_task_pack(pack_request)
            prepared = None
            effective_mode = pack_result.pack_mode
            task_pack_path = pack_result.output.display_path
            task_pack_filesystem_path = pack_result.output.filesystem_path
            effective_profile = str(pack_result.profile_path)
    except FileNotFoundError as exc:
        raise RunInputError(f"Profile not found: {_terminal_literal(str(exc))}") from exc
    except (TargetProjectError, ProfileLoadError, PackApplicationError) as exc:
        raise RunInputError(_terminal_literal(str(exc))) from exc
    except PackRendererError as exc:
        raise RunApplicationError(_terminal_literal(str(exc))) from exc
    except (OSError, UnicodeError) as exc:
        raise RunApplicationError(_terminal_literal(str(exc))) from exc
    except Exception as exc:
        raise RunApplicationError(_terminal_literal(str(exc))) from exc

    events: list[RunEvent] = []
    commands = _commands(
        request=request,
        kit_home=kit_home_display,
        project_root=unresolved.root,
        profile_display=effective_profile,
        goal=goal,
        requested_mode=requested_mode,
        task_pack_path=task_pack_path,
        task_pack_filesystem_path=task_pack_filesystem_path,
    )
    _render_preamble(
        events,
        project_root=unresolved.root,
        profile_display=effective_profile,
        goal=goal,
        effective_mode=effective_mode,
        inferred_mode=inferred_mode,
        task_pack_path=task_pack_path,
        commands=commands,
    )

    if request.overrides.mode is RunMode.PRINT_ONLY:
        _stdout(events, "Print-only mode. No files written.")
    else:
        assert pack_result is not None
        _stdout(events, f"AGENT RAILS: ON (mode={effective_mode}, pack={task_pack_path})")
        _stdout(events, f"Wrote {task_pack_path}")
        _stdout(events)
        try:
            estimate = _estimate_pack(
                pack_result,
                working_directory,
                task_pack_path,
                environment,
            )
        except Exception as exc:
            raise RunApplicationError(
                _tokenizer_failure_message(
                    pack_result.tokenizer,
                    request.overrides.tokenizer,
                ),
                events=tuple(events),
                pack_result=pack_result,
            ) from exc
        _stdout_text(events, estimate)
        _render_instructions(
            events,
            effective_mode=effective_mode,
            task_pack_path=task_pack_path,
            commands=commands,
        )

    return RunApplicationResult(
        project_root=unresolved.root,
        profile_path=effective_profile,
        task_pack_path=task_pack_path,
        effective_pack_mode=effective_mode,
        inferred_pack_mode=inferred_mode,
        pack_result=pack_result,
        exit_code=0,
        events=tuple(events),
    )


def _validate_request(request: RunApplicationRequest) -> None:
    if not isinstance(request, RunApplicationRequest):
        raise RunInputError("Invalid Run application request.")
    if not isinstance(request.requested_project, Path):
        raise RunInputError("Run requested project must be a Path.")
    if not isinstance(request.kit_home, Path):
        raise RunInputError("Run kit home must be a Path.")
    if request.explicit_profile is not None and not isinstance(
        request.explicit_profile, str
    ):
        raise RunInputError("Run explicit Profile must be text.")
    if not isinstance(request.goal, str):
        raise RunInputError("Run goal must be text.")
    if not isinstance(request.overrides, RunCliOverrides):
        raise RunInputError("Invalid Run CLI overrides.")
    if not isinstance(request.overrides.mode, RunMode):
        raise RunInputError("Invalid Run mode.")
    if (
        request.overrides.pack_mode is not None
        and request.overrides.pack_mode not in _PACK_MODES
    ):
        raise RunInputError("Invalid Run pack mode.")
    if (
        request.overrides.tokenizer is not None
        and request.overrides.tokenizer not in _TOKENIZER_MODES
    ):
        raise RunInputError("Invalid Run tokenizer.")
    for name in (
        "model",
        "pack_mode",
        "context_budget_chars",
        "context_budget_tokens",
        "tokenizer",
        "tokenizer_command",
        "tokenizer_path",
    ):
        value = getattr(request.overrides, name)
        if value is not None and not isinstance(value, str):
            raise RunInputError(f"Run {name} override must be text.")
    if not isinstance(request.working_directory, Path):
        raise RunInputError("Run working directory must be a Path.")
    if not isinstance(request.environment, Mapping):
        raise RunInputError("Run environment must be a mapping.")
    if not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in request.environment.items()
    ):
        raise RunInputError("Run environment keys and values must be text.")


def _canonical_directory(
    path: Path, label: str, error_type: type[RunApplicationError]
) -> Path:
    canonical = Path(os.path.realpath(os.fspath(path)))
    if not canonical.is_dir():
        raise error_type(f"{label} not found: {_terminal_literal(str(path))}")
    return canonical


def _anchored_path(path: Path, working_directory: Path) -> Path:
    return path if path.is_absolute() else working_directory / path


def _internal_profile(
    explicit_profile: Optional[str], working_directory: Path
) -> Optional[str]:
    if explicit_profile is None:
        return None
    path = Path(explicit_profile)
    if not path.is_absolute():
        path = working_directory / path
    return os.path.abspath(os.fspath(path))


def _pack_overrides(
    overrides: RunCliOverrides, pack_mode: Optional[str]
) -> PackCliOverrides:
    return PackCliOverrides(
        model=overrides.model,
        pack_mode=pack_mode,
        context_budget_chars=overrides.context_budget_chars,
        context_budget_tokens=overrides.context_budget_tokens,
        tokenizer=overrides.tokenizer,
        tokenizer_command=overrides.tokenizer_command,
        tokenizer_path=overrides.tokenizer_path,
    )


def _infer_pack_mode(goal: str) -> Optional[str]:
    lowered = goal.casefold()
    if any(
        token in lowered
        for token in ("audit", "审计", "全面review", "全面检查", "风险扫描")
    ):
        return "audit"
    if any(
        token in lowered
        for token in (
            "refactor",
            "重构",
            "架构",
            "architecture",
            "迁移",
            "migration",
            "api",
            "contract",
            "合约",
            "数据模型",
            "data model",
            "debug",
            "diagnose",
            "排查",
            "review",
            "code review",
        )
    ):
        return "deep"
    if any(
        token in lowered
        for token in (
            "poc",
            "prototype",
            "原型",
            "试水",
            "快速",
            "whl",
            "dockerfile",
            "oss",
            "上传",
            "deploy",
            "发布",
            "部署",
            "codegen",
        )
    ):
        return "lite"
    return None


@dataclass(frozen=True)
class _RunCommands:
    pack: Tuple[str, ...]
    estimate: Tuple[str, ...]
    check: Tuple[str, ...]
    memory_skip: Tuple[str, ...]
    memory_write: Tuple[str, ...]


def _commands(
    *,
    request: RunApplicationRequest,
    kit_home: Path,
    project_root: Path,
    profile_display: str,
    goal: str,
    requested_mode: Optional[str],
    task_pack_path: str,
    task_pack_filesystem_path: Path,
) -> _RunCommands:
    executable = str(kit_home / "bin/agent-rails")
    pack = [
        executable,
        "pack",
        "--project",
        str(project_root),
        "--profile",
        profile_display,
    ]
    estimate = [executable, "estimate", "--profile", profile_display]
    check = (
        executable,
        "check",
        "--project",
        str(project_root),
        "--profile",
        profile_display,
        "--print-only",
    )
    memory = (
        executable,
        "memory",
        "suggest",
        "--project",
        str(project_root),
        "--profile",
        profile_display,
    )
    values = request.overrides
    if values.model is not None:
        pack.extend(("--model", values.model))
        estimate.extend(("--model", values.model))
    if requested_mode is not None:
        pack.extend(("--pack-mode", requested_mode))
    if values.context_budget_chars is not None:
        pack.extend(("--budget", values.context_budget_chars))
    if values.context_budget_tokens is not None:
        pack.extend(("--token-budget", values.context_budget_tokens))
    if values.tokenizer is not None:
        pack.extend(("--tokenizer", values.tokenizer))
        estimate.extend(("--tokenizer", values.tokenizer))
    if values.tokenizer_command is not None:
        pack.extend(("--tokenizer-command", values.tokenizer_command))
        estimate.extend(("--tokenizer-command", values.tokenizer_command))
    if values.tokenizer_path is not None:
        pack.extend(("--tokenizer-path", values.tokenizer_path))
        estimate.extend(("--tokenizer-path", values.tokenizer_path))
    pack.append(goal)
    estimate.extend(("--file", str(task_pack_filesystem_path)))
    return _RunCommands(
        pack=tuple(pack),
        estimate=tuple(estimate),
        check=check,
        memory_skip=(
            *memory,
            "--decision",
            "skip",
            "--reason",
            "<why no durable memory>",
        ),
        memory_write=(
            *memory,
            "--decision",
            "keep",
            "--write-local",
            "--title",
            "<short title>",
            "--trigger",
            "<trigger>",
            "--applies-to",
            "<scope>",
            "--verify",
            "<check>",
            "--caution",
            "<scope limits>",
            "<brief reusable lesson>",
        ),
    )


def _render_preamble(
    events: list[RunEvent],
    *,
    project_root: Path,
    profile_display: str,
    goal: str,
    effective_mode: str,
    inferred_mode: Optional[str],
    task_pack_path: str,
    commands: _RunCommands,
) -> None:
    _stdout(events, f"AGENT RAILS: ON (mode={effective_mode}, pack={task_pack_path})")
    _stdout(events)
    _stdout(events, "Agent Rails Run")
    _stdout(events)
    _stdout(events, f"Project: {project_root}")
    _stdout(events, f"Profile: {profile_display}")
    _stdout(events, f"Goal: {goal}")
    if inferred_mode is not None:
        _stdout(events, f"Inferred pack mode: {inferred_mode}")
    _stdout(events, f"Task Pack: {task_pack_path}")
    _stdout(events)
    _stdout(events, "Commands")
    _stdout(events, f"- Pack: {_quote_command(commands.pack)}")
    _stdout(events, f"- Estimate: {_quote_command(commands.estimate)}")
    _stdout(events, f"- Check: {_quote_command(commands.check)}")
    _stdout(
        events,
        f"- Memory curator skip log: {_quote_command(commands.memory_skip)}",
    )
    _stdout(
        events,
        f"- Memory curator local write: {_quote_command(commands.memory_write)}",
    )
    _stdout(events)


def _estimate_pack(
    pack_result: PackApplicationResult,
    working_directory: Path,
    display_path: str,
    environment: Mapping[str, str],
) -> str:
    content = pack_result.render_result.content
    policy = pack_result.policy
    settings = pack_result.tokenizer
    model = policy.model
    chars_per_token = policy.budget.chars_per_token
    counter = TokenCounter(
        settings.mode,
        chars_per_token,
        settings.command,
        settings.path,
        settings.tiktoken_encoding,
        working_directory,
        settings.environment or environment,
    )
    tokens, _ = counter.count(content)
    raw = content.encode("utf-8")
    input_value = EstimateInput(
        source=f"file: {display_path}",
        text=content,
        characters=len(content),
        bytes_count=len(raw),
    )
    return render_estimate(
        input_value,
        TokenCount(tokens=tokens, tokenizer=counter.effective_mode),
        model,
        chars_per_token,
    )


def _tokenizer_failure_message(
    settings: Optional[TokenizerSettings], requested: Optional[str]
) -> str:
    mode = settings.mode if settings is not None else requested or "auto"
    if mode == "command":
        return "Tokenizer command failed or did not print an integer."
    if mode == "tiktoken":
        return "tiktoken tokenizer unavailable. Install tiktoken or use --tokenizer char/command."
    if mode in {"huggingface", "hf"}:
        return "Hugging Face tokenizer unavailable. Set --tokenizer-path and install transformers."
    return f"Unable to estimate Task Pack tokens with tokenizer: {mode}"


def _render_instructions(
    events: list[RunEvent],
    *,
    effective_mode: str,
    task_pack_path: str,
    commands: _RunCommands,
) -> None:
    _stdout(events)
    _stdout(events, "Agent Instructions")
    _stdout(
        events,
        f"0. Tell the user: AGENT RAILS: ON (mode={effective_mode}, pack={task_pack_path})",
    )
    _stdout(events, f"1. Read the Task Pack: {task_pack_path}")
    _stdout(
        events,
        "2. Follow Trigger Matrix, Session Marker, Context Budget, Changed File "
        "Priority, Memory Cards, Grill Gate, Verification Suggestions, and "
        "Subagent Result Contract.",
    )
    _stdout(
        events,
        "   In lite mode, skip full grill and keep only blocker questions plus "
        "deferred decisions.",
    )
    _stdout(events, "3. Before final delivery, run:")
    _stdout(events, f"   {_quote_command(commands.check)}")
    _stdout(
        events,
        "4. After delivery, use agent-memory-curator. If no durable lesson, log skip:",
    )
    _stdout(events, f"   {_quote_command(commands.memory_skip)}")
    _stdout(events, "   If valuable, write one local card:")
    _stdout(events, f"   {_quote_command(commands.memory_write)}")


def _quote_command(arguments: Tuple[str, ...]) -> str:
    return " ".join("'" + value.replace("'", "'\\''") + "'" for value in arguments)


def _stdout(events: list[RunEvent], text: str = "") -> None:
    events.append(RunEvent(RunEventStream.STDOUT, _terminal_literal(text)))


def _stdout_text(events: list[RunEvent], text: str) -> None:
    for line in text.splitlines():
        _stdout(events, line)


def _terminal_literal(value: str) -> str:
    escaped: list[str] = []
    for character in value:
        codepoint = ord(character)
        category = unicodedata.category(character)
        if character == "\n":
            escaped.append("\\n")
        elif character == "\r":
            escaped.append("\\r")
        elif character == "\t":
            escaped.append("\\t")
        elif category in {"Cc", "Cf", "Zl", "Zp"} or 0xD800 <= codepoint <= 0xDFFF:
            if codepoint <= 0xFF:
                escaped.append(f"\\x{codepoint:02x}")
            elif codepoint <= 0xFFFF:
                escaped.append(f"\\u{codepoint:04x}")
            else:
                escaped.append(f"\\U{codepoint:08x}")
        else:
            escaped.append(character)
    return "".join(escaped)


def _render_events(events: Tuple[RunEvent, ...], stream: RunEventStream) -> str:
    selected = [event.text for event in events if event.stream is stream]
    return "" if not selected else "\n".join(selected) + "\n"
