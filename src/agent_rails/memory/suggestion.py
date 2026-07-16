"""Record a curated memory decision and optionally publish one local card."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import html
import json
import os
from pathlib import Path
import re
from typing import Mapping, Optional, Tuple

from agent_rails.config.target_project import resolve_target_project
from agent_rails.context.markdown import display_text, markdown_code, markdown_fence
from agent_rails.core.paths import AgentRailsPaths, canonical_path
from agent_rails.core.private_text import (
    PrivateTextArtifact,
    PrivateTextNonRegularError,
    PrivateTextPublishError,
    PrivateTextTargetExistsError,
    PublishedPrivateText,
    publish_private_text_batch,
)
from agent_rails.git.scope import collect_worktree_snapshot
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    scan_sensitive_output,
)


MEMORY_SUGGEST_PROFILE_VARIABLES = ("MEMORY_LOCAL_DIR",)
_DEFAULT_TITLE = "Untitled memory decision"
_DEFAULT_VERIFY = (
    "Re-check the files, commands, or config named in the task before relying "
    "on this memory."
)
_DEFAULT_CAUTION = (
    "Apply only within the listed scope. Treat environment, branch, and service "
    "behavior as verify-first."
)
_NON_GIT_STATUS = "No git repository detected; git state is unavailable."


class MemoryDecision(str, Enum):
    KEEP = "keep"
    SKIP = "skip"
    UPDATE = "update"
    MERGE = "merge"


class MemoryStaleness(str, Enum):
    STABLE = "stable"
    VERIFY_FIRST = "verify-first"


class ArtifactKind(str, Enum):
    DECISION = "decision"
    LOCAL_MEMORY = "local-memory"


class MemorySuggestionInputError(RuntimeError):
    pass


class MemorySuggestionPublishError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        published: Tuple["PublishedArtifact", ...] = (),
    ) -> None:
        super().__init__(message)
        self.published = published


@dataclass(frozen=True)
class ArtifactTarget:
    display_path: str
    filesystem_path: Path


@dataclass(frozen=True)
class MemorySuggestionRequest:
    requested_project: Path
    invocation_cwd: Path
    kit_home: Path
    explicit_profile: Optional[str]
    output: Optional[str]
    decision: MemoryDecision
    write_local: bool
    force: bool
    memory_id: Optional[str]
    title: Optional[str]
    triggers: Tuple[str, ...]
    applies_to: Tuple[str, ...]
    verify: str
    caution: str
    reason: str
    staleness: MemoryStaleness
    notes: str
    environment: Mapping[str, str]


@dataclass(frozen=True)
class PublishedArtifact:
    kind: ArtifactKind
    target: ArtifactTarget


@dataclass(frozen=True)
class MemorySuggestionResult:
    requested_project_path: Path
    project_root: Path
    project_name: str
    profile_display_path: str
    decision: MemoryDecision
    memory_id: str
    title: str
    triggers: Tuple[str, ...]
    applies_to: Tuple[str, ...]
    changed_paths: Tuple[str, ...]
    working_tree_status: str
    decision_target: ArtifactTarget
    local_target: Optional[ArtifactTarget]
    published: Tuple[PublishedArtifact, ...]


def suggest_memory(request: MemorySuggestionRequest) -> MemorySuggestionResult:
    """Build and publish a decision log plus an optional local memory card."""

    _validate_request(request)
    context = resolve_target_project(
        request.requested_project,
        kit_home=request.kit_home,
        explicit_profile=request.explicit_profile,
        environment=request.environment,
        require_profile=True,
        load_profile=True,
        load_environment_file=False,
        profile_variables=MEMORY_SUGGEST_PROFILE_VARIABLES,
    )
    requested_path = canonical_path(request.requested_project)
    invocation_cwd = canonical_path(request.invocation_cwd)
    config_home = (
        context.profile_values.get("AGENT_RAILS_CONFIG_HOME")
        or request.environment.get("AGENT_RAILS_CONFIG_HOME")
        or f"{request.environment.get('HOME', str(Path.home()))}/.agent-rails"
    )
    paths = AgentRailsPaths(request.kit_home, config_home)
    memory_dir_display = (
        context.profile_values.get("MEMORY_LOCAL_DIR")
        or paths.default_memory_dir(context.project_name)
    )
    output_display = request.output or paths.default_memory_decision_path(
        context.project_name
    )
    decision_target = ArtifactTarget(
        display_path=output_display,
        filesystem_path=_anchored_path(invocation_cwd, output_display),
    )

    if context.is_git_repo:
        worktree = collect_worktree_snapshot(context.root)
        changed_paths = worktree.changed_paths
        status = worktree.status
    else:
        changed_paths = ()
        status = _NON_GIT_STATUS

    title = request.title or _DEFAULT_TITLE
    memory_id = _resolve_memory_id(request.memory_id, title, context.project_name)
    triggers = _resolve_triggers(request.triggers, title, context.project_name)
    applies_to = _resolve_applies_to(
        request.applies_to, changed_paths, context.project_name
    )
    local_display = f"{memory_dir_display.rstrip('/')}/{memory_id}.md"
    local_target = (
        ArtifactTarget(
            display_path=local_display,
            filesystem_path=_anchored_path(invocation_cwd, local_display),
        )
        if request.write_local
        else None
    )
    if (
        local_target is not None
        and _normalized_path(local_target.filesystem_path)
        == _normalized_path(decision_target.filesystem_path)
    ):
        raise MemorySuggestionInputError(
            "Decision output and local memory path must be different."
        )
    if local_target is not None and not request.force:
        try:
            local_target.filesystem_path.lstat()
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MemorySuggestionInputError(
                f"Unable to inspect local memory path: {local_target.display_path}"
            ) from exc
        else:
            raise MemorySuggestionInputError(
                f"Local memory already exists: {local_target.display_path}\n"
                "Use --force to replace it, or choose --id."
            )

    card = _render_card(
        memory_id=memory_id,
        title=title,
        triggers=triggers,
        applies_to=applies_to,
        staleness=request.staleness,
        project_name=context.project_name,
        decision=request.decision,
        notes=request.notes,
        verify=request.verify or _DEFAULT_VERIFY,
        caution=request.caution or _DEFAULT_CAUTION,
    )
    decision_log = _render_decision_log(
        request=request,
        requested_path=requested_path,
        project_name=context.project_name,
        profile_path=context.profile_path,
        memory_dir=memory_dir_display,
        local_target=local_target,
        changed_paths=changed_paths,
        status=status,
        card=card,
    )
    _reject_sensitive(card)
    _reject_sensitive(request.reason)
    _strict_utf8(decision_log)

    artifacts = [
        PrivateTextArtifact(
            key=ArtifactKind.DECISION.value,
            target=decision_target.filesystem_path,
            content=decision_log,
        )
    ]
    if local_target is not None:
        artifacts.append(
            PrivateTextArtifact(
                key=ArtifactKind.LOCAL_MEMORY.value,
                target=local_target.filesystem_path,
                content=card,
                create_only=not request.force,
            )
        )
    targets = {
        ArtifactKind.DECISION.value: decision_target,
        ArtifactKind.LOCAL_MEMORY.value: local_target,
    }
    try:
        published_private = publish_private_text_batch(tuple(artifacts))
    except (PrivateTextTargetExistsError, PrivateTextNonRegularError) as exc:
        raise MemorySuggestionInputError(str(exc)) from exc
    except PrivateTextPublishError as exc:
        published = _published_artifacts(exc.published, targets)
        raise MemorySuggestionPublishError(
            "Unable to publish memory suggestion artifacts.",
            published=published,
        ) from exc
    published = _published_artifacts(published_private, targets)
    return MemorySuggestionResult(
        requested_project_path=requested_path,
        project_root=context.root,
        project_name=context.project_name,
        profile_display_path=context.profile_path,
        decision=request.decision,
        memory_id=memory_id,
        title=title,
        triggers=triggers,
        applies_to=applies_to,
        changed_paths=changed_paths,
        working_tree_status=status,
        decision_target=decision_target,
        local_target=local_target,
        published=published,
    )


def memory_slugify(value: str) -> str:
    lowered = value.lower()
    slug = re.sub(r"[^a-z0-9._-]+", "-", lowered)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:80]


def _validate_request(request: MemorySuggestionRequest) -> None:
    if request.write_local and request.decision is MemoryDecision.SKIP:
        raise MemorySuggestionInputError(
            "Refusing --write-local with --decision skip."
        )
    if request.write_local and not request.notes:
        raise MemorySuggestionInputError(
            "Refusing --write-local without curated memory notes."
        )
    if request.memory_id is not None:
        if not request.memory_id or request.memory_id != memory_slugify(request.memory_id):
            raise MemorySuggestionInputError(
                "Memory ID must be a canonical lowercase slug of at most 80 characters."
            )
    for label, value in (
        ("title", request.title or ""),
        ("reason", request.reason),
        ("verify", request.verify),
        ("caution", request.caution),
        ("notes", request.notes),
        *[("trigger", value) for value in request.triggers],
        *[("applies-to", value) for value in request.applies_to],
    ):
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise MemorySuggestionInputError(
                f"Memory suggestion {label} is not valid UTF-8."
            ) from exc


def _resolve_memory_id(explicit: Optional[str], title: str, project_name: str) -> str:
    if explicit is not None:
        return explicit
    if title != _DEFAULT_TITLE:
        derived = memory_slugify(title)
        if derived:
            return derived
    derived = memory_slugify(f"{project_name}-memory-{_timestamp()}")
    return derived or f"memory-{_timestamp()}"


def _resolve_triggers(
    supplied: Tuple[str, ...], title: str, project_name: str
) -> Tuple[str, ...]:
    if supplied:
        return supplied
    selected = []
    if title != _DEFAULT_TITLE:
        for word in memory_slugify(title).split("-"):
            if len(word) >= 3 and word not in selected:
                selected.append(word)
            if len(selected) >= 6:
                break
    return tuple(selected or [project_name])


def _resolve_applies_to(
    supplied: Tuple[str, ...], changed_paths: Tuple[str, ...], project_name: str
) -> Tuple[str, ...]:
    if supplied:
        return supplied
    selected = []
    for path in changed_paths:
        value = path.split("/", 1)[0]
        if value and value not in selected:
            selected.append(value)
        if len(selected) >= 6:
            break
    return tuple(selected or [project_name])


def _render_card(
    *,
    memory_id: str,
    title: str,
    triggers: Tuple[str, ...],
    applies_to: Tuple[str, ...],
    staleness: MemoryStaleness,
    project_name: str,
    decision: MemoryDecision,
    notes: str,
    verify: str,
    caution: str,
) -> str:
    lines = [
        "---\n",
        f"id: {_yaml_scalar(memory_id)}\n",
        f"title: {_yaml_scalar(title)}\n",
        "triggers:\n",
        *[f"  - {_yaml_scalar(value)}\n" for value in triggers],
        "applies_to:\n",
        *[f"  - {_yaml_scalar(value)}\n" for value in applies_to],
        f"staleness: {staleness.value}\n",
        "source:\n",
        f"  - {_yaml_scalar(f'agent-rails memory suggest: project={project_name} decision={decision.value}')}\n",
        "---\n\n",
        "## Rule\n\n",
        f"{notes or 'TODO: State the reusable project fact or workflow rule in 1-3 sentences.'}\n\n",
        "## Verify\n\n",
        f"{verify}\n\n",
        "## Caution\n\n",
        f"{caution}\n",
    ]
    return _strict_utf8("".join(lines))


def _render_decision_log(
    *,
    request: MemorySuggestionRequest,
    requested_path: Path,
    project_name: str,
    profile_path: str,
    memory_dir: str,
    local_target: Optional[ArtifactTarget],
    changed_paths: Tuple[str, ...],
    status: str,
    card: str,
) -> str:
    status_fence = markdown_fence(status, "`", 3)
    card_fence = markdown_fence(card, "`", 3)
    lines = [
        "# Agent Rails Memory Decision\n\n",
        "> Model-curated decision log. Local memory is written only when "
        "`--write-local` is used. This helper never writes through an online "
        "memory Adapter.\n\n",
        "## Source Context\n\n",
        f"- Project: {markdown_code(project_name)}\n",
        f"- Project path: {markdown_code(str(requested_path))}\n",
        f"- Profile: {markdown_code(profile_path)}\n",
        f"- Local memory dir: {markdown_code(memory_dir)}\n\n",
        "## Decision\n\n",
        f"- Decision: {markdown_code(request.decision.value)}\n",
        f"- Reason: {_inline_text(request.reason or 'Not provided.')}\n",
        f"- Local write requested: {markdown_code('yes' if request.write_local else 'no')}\n",
    ]
    if local_target is not None:
        lines.append(
            f"- Local memory path: {markdown_code(local_target.display_path)}\n"
        )
    lines.extend(["\n", "## Changed Files At Suggest Time\n\n"])
    if changed_paths:
        lines.extend(f"- {markdown_code(path)}\n" for path in changed_paths)
    else:
        lines.append("- None detected.\n")
    lines.extend(["\n", "## Working Tree Status\n\n"])
    if status:
        lines.extend([f"{status_fence}text\n", status])
        if not status.endswith("\n"):
            lines.append("\n")
        lines.extend([f"{status_fence}\n\n"])
    else:
        lines.append("Clean.\n\n")
    lines.extend(
        [
            "## Candidate\n\n",
            f"{card_fence}markdown\n",
            card,
            f"{card_fence}\n\n",
            "## Curator Checklist\n\n",
            "- No secrets, cookies, tokens, AccessKeys, or full sensitive responses.\n",
            "- The lesson is reusable for future tasks, not a one-off transcript summary.\n",
            "- Existing memory/docs were checked for duplicates or conflicts.\n",
            "- `Verify` tells the next agent how to confirm the claim.\n",
            "- The online memory Adapter was not used for writing.\n",
        ]
    )
    return _strict_utf8("".join(lines))


def _reject_sensitive(card: str) -> None:
    try:
        findings = scan_sensitive_output(
            card, source_name="memory suggestion", format_name="text"
        )
    except SensitiveOutputError as exc:
        raise MemorySuggestionInputError(
            "Memory suggestion could not pass the Sensitive Output Guard."
        ) from exc
    if findings:
        raise MemorySuggestionInputError(
            "Memory suggestion contains secret-bearing content and was not written."
        )


def _published_artifacts(
    published: Tuple[PublishedPrivateText, ...],
    targets: Mapping[str, Optional[ArtifactTarget]],
) -> Tuple[PublishedArtifact, ...]:
    result = []
    for item in published:
        target = targets.get(item.key)
        if target is None:
            continue
        result.append(PublishedArtifact(ArtifactKind(item.key), target))
    return tuple(result)


def _yaml_scalar(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _inline_text(value: str) -> str:
    return html.escape(display_text(value), quote=False)


def _anchored_path(cwd: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else cwd / path


def _normalized_path(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _strict_utf8(value: str) -> str:
    return value.encode("utf-8", errors="strict").decode("utf-8")


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")
