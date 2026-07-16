"""Select and render Target Project documentation status for Task Pack."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Tuple

from agent_rails.context.markdown import markdown_code
from agent_rails.git._runner import run_git


class ProjectDocsError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProjectDocsRequest:
    project: Path
    is_git_repo: bool
    target_ref: str
    target_ref_explicit: bool
    changed_paths: Tuple[str, ...]
    entry_docs: Mapping[str, str]
    configuration_docs: Mapping[str, str]


@dataclass(frozen=True)
class DocumentStatus:
    label: str
    path: str
    exists: bool
    source: str


@dataclass(frozen=True)
class ProjectDocs:
    entries: Tuple[DocumentStatus, ...]
    configuration: Tuple[DocumentStatus, ...]


def collect_project_docs(request: ProjectDocsRequest) -> ProjectDocs:
    selected_entries = []
    _append_entry(selected_entries, request.entry_docs.get("root", ""), "root")
    prefixes = (
        ("backend/", "backend"),
        ("runtime/", "runtime"),
        ("frontend/", "frontend"),
        ("dolphin/", "dolphin"),
        ("contracts/", "contracts"),
    )
    for prefix, label in prefixes:
        if any(path.startswith(prefix) for path in request.changed_paths):
            _append_entry(selected_entries, request.entry_docs.get(label, ""), label)

    entries = tuple(
        _document_status(request, label=label, path=path)
        for path, label in selected_entries
    )
    configuration = tuple(
        _document_status(request, label=label, path=path)
        for label, path in request.configuration_docs.items()
    )
    return ProjectDocs(entries=entries, configuration=configuration)


def render_entry_sections(project_docs: ProjectDocs) -> str:
    lines = ["## Relevant Entry Docs\n\n"]
    for document in project_docs.entries:
        rendered_path = markdown_code(document.path)
        if document.exists:
            lines.append(
                f"- {rendered_path} "
                f"({document.label}, {document.source})\n"
            )
        else:
            lines.append(
                f"- MISSING {rendered_path} ({document.label})\n"
            )
    lines.append("\n## Context Gaps\n\n")
    missing = [document for document in project_docs.entries if not document.exists]
    if missing:
        lines.extend(
            f"- {markdown_code(document.path)} "
            f"not found for {document.label} context.\n"
            for document in missing
        )
    else:
        lines.append("- None detected.\n")
    lines.append("\n")
    return "".join(lines)


def render_configuration_section(project_docs: ProjectDocs) -> str:
    lines = ["## Project Configuration\n\n"]
    for document in project_docs.configuration:
        if not document.path:
            lines.append(f"- {document.label}: not configured.\n")
        elif document.exists:
            lines.append(
                f"- {document.label}: "
                f"{markdown_code(document.path)} "
                f"({document.source})\n"
            )
        else:
            lines.append(
                f"- {document.label}: missing "
                f"{markdown_code(document.path)}\n"
            )
    lines.append("\n")
    return "".join(lines)


def write_project_docs_bundle(
    output_dir: Path, project_docs: ProjectDocs
) -> None:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "entry-sections.md").write_text(
            render_entry_sections(project_docs), encoding="utf-8"
        )
        (output_dir / "configuration-section.md").write_text(
            render_configuration_section(project_docs), encoding="utf-8"
        )
    except (OSError, UnicodeError) as exc:
        raise ProjectDocsError(
            f"Unable to write Task Pack project docs: {output_dir}"
        ) from exc


def _append_entry(entries: list[Tuple[str, str]], path: str, label: str) -> None:
    if path:
        entries.append((path, label))


def _document_status(
    request: ProjectDocsRequest, *, label: str, path: str
) -> DocumentStatus:
    if not path:
        return DocumentStatus(label=label, path="", exists=False, source="")

    working_tree_exists = (request.project / path).exists()
    target_exists = False
    if request.is_git_repo and request.target_ref_explicit:
        try:
            result = run_git(
                request.project,
                ("cat-file", "-e", f"{request.target_ref}:{path}"),
            )
        except OSError as exc:
            raise ProjectDocsError("Git command is unavailable.") from exc
        target_exists = result.returncode == 0
    exists = working_tree_exists or target_exists
    source = (
        f"at {request.target_ref}"
        if target_exists and not working_tree_exists
        else "working tree"
    )
    return DocumentStatus(label=label, path=path, exists=exists, source=source)
