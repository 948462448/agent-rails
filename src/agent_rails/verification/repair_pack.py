"""Render bounded verification failure evidence for the next repair turn."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Optional, Sequence, Tuple

from agent_rails.core.terminal import terminal_literal, terminal_stream_text
from agent_rails.evidence.code import (
    CodeEvidenceError,
    CodeEvidenceRecord,
    CodeEvidenceRequest,
    collect_code_evidence,
)
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    redact_sensitive_output,
)


_HIGH_VALUE = re.compile(
    r"(assert(?:ion)?error|error|exception|fail(?:ed|ure)?|fatal|panic|"
    r"syntaxerror|traceback)",
    re.IGNORECASE,
)
_DEFAULT_MAX_CHARS = 4_000
_MAX_LOCATIONS = 6
_CODE_EVIDENCE_LIMIT = 4
_DIAGNOSTIC_STOP_WORDS = (
    "error errors failed failure exception traceback assertionerror expected actual"
)


@dataclass(frozen=True)
class VerificationFailure:
    reason: str
    exit_code: int
    completed_steps: int
    stdout: str
    stderr: str
    output_truncated: bool = False


@dataclass(frozen=True)
class RepairPackRequest:
    failure: VerificationFailure
    changed_paths: Tuple[str, ...]
    max_chars: int = _DEFAULT_MAX_CHARS
    project: Optional[Path] = None
    target_sha: str = ""


def render_repair_pack(request: RepairPackRequest) -> str:
    """Turn one failed verification step into safe, focused terminal evidence."""

    failure = request.failure
    stderr = _safe_output(failure.stderr)
    stdout = _safe_output(failure.stdout)
    lines, diagnostic_index = _diagnostic_lines(stderr, stdout)
    excerpt = (
        lines[max(0, diagnostic_index - 1) : diagnostic_index + 3]
        if lines
        else ["No diagnostic output captured."]
    )
    locations = _related_locations(
        (*stderr.splitlines(), *stdout.splitlines()), request.changed_paths
    )
    code_records: Optional[Tuple[CodeEvidenceRecord, ...]] = None
    code_unavailable = False
    if request.project is not None and request.target_sha:
        try:
            code_records = collect_code_evidence(
                CodeEvidenceRequest(
                    project=request.project,
                    target_sha=request.target_sha,
                    query="\n".join((failure.reason, *excerpt)),
                    ignored_text=(
                        f"{request.project.name} {_DIAGNOSTIC_STOP_WORDS}"
                    ),
                    preferred_paths=request.changed_paths,
                    limit=_CODE_EVIDENCE_LIMIT,
                )
            )
        except CodeEvidenceError:
            code_records = ()
            code_unavailable = True

    output = [
        "\nRepair Pack\n",
        f"- Failed verification: {terminal_literal(failure.reason)}\n",
        f"- Exit code: {failure.exit_code}\n",
        f"- Completed before failure: {failure.completed_steps}\n",
        "- Output capture: "
        + (
            "bounded; earlier lines omitted\n"
            if failure.output_truncated
            else "complete\n"
        ),
        "\nFirst diagnostic:\n",
    ]
    output.extend(f"  {terminal_literal(line)}\n" for line in excerpt)
    output.append("\nRelated project locations: ")
    if locations:
        output.append("\n")
        output.extend(f"- {terminal_literal(location)}\n" for location in locations)
    else:
        output.append("none confirmed\n")
    if code_records is not None:
        if code_unavailable:
            output.append("\nRelated code evidence: unavailable\n")
        elif code_records:
            output.append("\nRelated code evidence (fixed Git target):\n")
            output.extend(_render_code_record(record) for record in code_records)
        else:
            output.append("\nRelated code evidence: no tracked match\n")
    output.extend(
        (
            "\nNext action:\n",
            "- Inspect the first diagnostic and confirmed locations, make the "
            "smallest evidence-backed change, then rerun the exact failed "
            "verification command shown above.\n",
        )
    )
    return _bounded("".join(output), request.max_chars)


def _render_code_record(record: CodeEvidenceRecord) -> str:
    location = record.path if record.line <= 0 else f"{record.path}:{record.line}"
    symbol = f" symbol={terminal_literal(record.symbol)}" if record.symbol else ""
    return f"- {terminal_literal(location)}{symbol}\n"


def _safe_output(text: str) -> str:
    try:
        return redact_sensitive_output(
            terminal_stream_text(text), format_name="text"
        )
    except (SensitiveOutputError, UnicodeError, OSError):
        return "[repair evidence omitted: sensitive-output guard failed]\n"


def _diagnostic_lines(stderr: str, stdout: str) -> tuple[list[str], int]:
    lines = [line for line in stderr.splitlines() if line.strip()]
    lines.extend(line for line in stdout.splitlines() if line.strip())
    if not lines:
        return [], 0
    for index, line in enumerate(lines):
        if _HIGH_VALUE.search(line):
            return lines, index
    return lines, len(lines) - 1


def _related_locations(
    lines: Sequence[str], changed_paths: Sequence[str]
) -> Tuple[str, ...]:
    locations = []
    for path in changed_paths:
        for line in lines:
            start = line.find(path)
            if start < 0:
                continue
            suffix = line[start + len(path) :]
            match = re.match(r":([0-9]+)(?::([0-9]+))?", suffix)
            location = path
            if match:
                location += f":{match.group(1)}"
                if match.group(2):
                    location += f":{match.group(2)}"
            if location not in locations:
                locations.append(location)
            break
        if len(locations) >= _MAX_LOCATIONS:
            break
    return tuple(locations)


def _bounded(text: str, max_chars: int) -> str:
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    marker = "\n...[Repair Pack truncated]...\n"
    if max_chars <= len(marker):
        return marker[:max_chars]
    prefix = text[: max_chars - len(marker)]
    if "\n" in prefix:
        prefix = prefix.rsplit("\n", 1)[0]
    return prefix + marker
