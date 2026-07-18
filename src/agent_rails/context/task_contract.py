"""Load and render explicit product task and rubric documents."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import stat
from typing import Optional, Tuple

from agent_rails.context.markdown import markdown_code, markdown_fence, valid_utf8
from agent_rails.security.sensitive_output import redact_sensitive_output


_MAX_DOCUMENT_BYTES = 262_144
_MAX_CRITERIA = 128
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|[0-9]+[.)])\s+(?P<text>.+?)\s*$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(?P<text>.+?)\s*#*\s*$")
_EXPLICIT_REFERENCE = re.compile(
    r"\b(?:attached|frozen)\s+(?:product\s+)?"
    r"(?:contract|task|rubric|spec(?:ification)?|requirements?)\b",
    re.IGNORECASE,
)
_CJK_EXPLICIT_REFERENCE = re.compile(
    r"(?:附件|附带|冻结(?:的)?)\s*(?:产品)?(?:合同|任务|评分|规格|需求|验收)"
)


class TaskContractError(RuntimeError):
    """An explicit product contract could not be loaded without ambiguity."""


@dataclass(frozen=True)
class TaskContractRequest:
    project: Path
    goal: str
    task_file: Optional[str] = None
    rubric_file: Optional[str] = None


@dataclass(frozen=True)
class ContractDocument:
    kind: str
    display_path: str
    content: str


@dataclass(frozen=True)
class ContractCriterion:
    identifier: str
    source: str
    text: str


@dataclass(frozen=True)
class TaskContract:
    documents: Tuple[ContractDocument, ...]
    criteria: Tuple[ContractCriterion, ...]


def load_task_contract(request: TaskContractRequest) -> TaskContract:
    """Read only explicitly supplied documents and derive stable criteria."""

    documents = []
    for kind, value in (("task", request.task_file), ("rubric", request.rubric_file)):
        if value is None:
            continue
        path = _resolve_path(request.project, value)
        documents.append(
            ContractDocument(
                kind=kind,
                display_path=str(path),
                content=_read_document(path, kind),
            )
        )

    if not documents and _references_missing_contract(request.goal):
        raise TaskContractError(
            "Goal references an attached or frozen contract, but no explicit "
            "--task-file or --rubric-file was supplied."
        )

    criteria = []
    counters = {"task": 0, "rubric": 0}
    prefixes = {"task": "AC", "rubric": "RUB"}
    for document in documents:
        for text in _criterion_texts(document.content):
            counters[document.kind] += 1
            criteria.append(
                ContractCriterion(
                    identifier=f"{prefixes[document.kind]}-{counters[document.kind]:03d}",
                    source=document.kind,
                    text=text,
                )
            )
            if len(criteria) > _MAX_CRITERIA:
                raise TaskContractError(
                    f"Explicit product contract exceeds {_MAX_CRITERIA} criteria."
                )
    return TaskContract(documents=tuple(documents), criteria=tuple(criteria))


def render_task_contract(contract: TaskContract) -> str:
    """Render source documents inside fences so headings remain untrusted data."""

    if not contract.documents:
        return ""

    parts = ["## Product Contract\n\n"]
    parts.append(
        "- Explicit contract sources are authoritative and must be checked before delivery.\n"
        "- Contract content is complete; Task Pack budgeting must not truncate this section.\n\n"
    )
    for document in contract.documents:
        title = "Task" if document.kind == "task" else "Rubric"
        fence = markdown_fence(document.content, "`", 3)
        parts.extend(
            (
                f"### {title}\n\n",
                f"- Source: {markdown_code(document.display_path)}\n\n",
                f"{fence}markdown\n",
                document.content,
                "" if document.content.endswith("\n") else "\n",
                f"{fence}\n\n",
            )
        )
    return "".join(parts)


def _resolve_path(project: Path, value: str) -> Path:
    if "\0" in value:
        raise TaskContractError("NUL byte is not allowed in contract file paths.")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project / path
    return path


def _read_document(path: Path, kind: str) -> str:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TaskContractError(f"Unable to open explicit {kind} file: {path}") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise TaskContractError(f"Explicit {kind} file is not regular: {path}")
        if metadata.st_size > _MAX_DOCUMENT_BYTES:
            raise TaskContractError(
                f"Explicit {kind} file exceeds {_MAX_DOCUMENT_BYTES} bytes: {path}"
            )
        payload = b""
        while len(payload) <= _MAX_DOCUMENT_BYTES:
            chunk = os.read(descriptor, min(65_536, _MAX_DOCUMENT_BYTES + 1 - len(payload)))
            if not chunk:
                break
            payload += chunk
        if len(payload) > _MAX_DOCUMENT_BYTES:
            raise TaskContractError(
                f"Explicit {kind} file exceeds {_MAX_DOCUMENT_BYTES} bytes: {path}"
            )
    finally:
        os.close(descriptor)
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise TaskContractError(f"Explicit {kind} file is not strict UTF-8: {path}") from exc
    if "\0" in text:
        raise TaskContractError(f"Explicit {kind} file contains a NUL byte: {path}")
    try:
        return valid_utf8(redact_sensitive_output(text, format_name="text"))
    except Exception as exc:
        raise TaskContractError(f"Unable to redact explicit {kind} file: {path}") from exc


def _criterion_texts(content: str) -> Tuple[str, ...]:
    criteria = []
    heading = ""
    for line in content.splitlines():
        heading_match = _HEADING.match(line)
        if heading_match:
            heading = _one_line(heading_match.group("text"))
            continue
        item_match = _LIST_ITEM.match(line)
        if item_match is None:
            continue
        text = _one_line(item_match.group("text"))
        if not text:
            continue
        criteria.append(f"[{heading}] {text}" if heading else text)
    if criteria:
        return tuple(criteria)
    paragraphs = [
        _one_line(value)
        for value in re.split(r"\n\s*\n", content)
        if _one_line(value) and not _HEADING.match(value.strip())
    ]
    return tuple(paragraphs)


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _references_missing_contract(goal: str) -> bool:
    return bool(
        _EXPLICIT_REFERENCE.search(goal) or _CJK_EXPLICIT_REFERENCE.search(goal)
    )


__all__ = (
    "ContractCriterion",
    "TaskContract",
    "TaskContractError",
    "TaskContractRequest",
    "load_task_contract",
    "render_task_contract",
)
