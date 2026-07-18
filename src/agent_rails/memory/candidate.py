"""Publish bounded, private Memory Candidates after verified repair success."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from typing import Tuple

from agent_rails.context.markdown import markdown_code
from agent_rails.core.private_text import (
    PrivateTextArtifact,
    PrivateTextPublishError,
    publish_private_text_batch,
)
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    scan_sensitive_output,
)
from agent_rails.verification.plan import VerificationPlan


_SHA = re.compile(r"[0-9a-f]{40,64}")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")
_MAX_CHANGED_PATHS = 24


class MemoryCandidateError(RuntimeError):
    pass


@dataclass(frozen=True)
class MemoryCandidateRequest:
    config_home: Path
    project_root: Path
    project_name: str
    target_sha: str
    failure_fingerprint: str
    failure_count: int
    changed_paths: Tuple[str, ...]
    verification: VerificationPlan
    completed_steps: int


@dataclass(frozen=True)
class MemoryCandidateResult:
    path: Path
    target_sha: str
    failure_fingerprint: str


def memory_candidate_path(config_home: Path, project_root: Path) -> Path:
    if not config_home.is_absolute():
        raise MemoryCandidateError("Memory Candidate config home must be absolute.")
    identity = str(project_root.resolve()).encode("utf-8", errors="surrogateescape")
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return config_home / "memory-candidates" / f"candidate-{digest}.md"


def publish_memory_candidate(request: MemoryCandidateRequest) -> MemoryCandidateResult:
    _validate_request(request)
    path = memory_candidate_path(request.config_home, request.project_root)
    content = _render_candidate(request)
    try:
        findings = scan_sensitive_output(
            content, source_name="memory candidate", format_name="text"
        )
    except SensitiveOutputError as exc:
        raise MemoryCandidateError(
            "Memory Candidate sensitive-output guard failed."
        ) from exc
    if findings:
        raise MemoryCandidateError(
            "Memory Candidate contains sensitive-output evidence."
        )
    try:
        publish_private_text_batch(
            (PrivateTextArtifact(key="memory-candidate", target=path, content=content),)
        )
    except (PrivateTextPublishError, OSError, UnicodeError) as exc:
        raise MemoryCandidateError("Unable to publish Memory Candidate.") from exc
    return MemoryCandidateResult(
        path=path,
        target_sha=request.target_sha,
        failure_fingerprint=request.failure_fingerprint,
    )


def _validate_request(request: MemoryCandidateRequest) -> None:
    if not _SHA.fullmatch(request.target_sha):
        raise MemoryCandidateError("Memory Candidate target SHA is invalid.")
    if not _FINGERPRINT.fullmatch(request.failure_fingerprint):
        raise MemoryCandidateError("Memory Candidate failure fingerprint is invalid.")
    if request.failure_count < 1 or request.completed_steps < 1:
        raise MemoryCandidateError("Memory Candidate requires executed verification.")
    for path in request.changed_paths:
        if not path or "\0" in path:
            raise MemoryCandidateError("Memory Candidate changed path is invalid.")


def _render_candidate(request: MemoryCandidateRequest) -> str:
    paths = request.changed_paths[:_MAX_CHANGED_PATHS]
    verification = request.verification.steps[: request.completed_steps]
    lines = [
        "# Agent Rails Memory Candidate\n\n",
        "> Draft evidence after a failed verification later passed. No local memory card was written.\n\n",
        "## Proven Verification\n\n",
        f"- Fixed Git target: {markdown_code(request.target_sha)}\n",
        f"- Prior failure fingerprint: {markdown_code(request.failure_fingerprint)}\n",
        f"- Consecutive failure count before success: {request.failure_count}\n",
        f"- Completed verification steps: {request.completed_steps}\n",
    ]
    if verification:
        lines.extend(
            f"- Passed verification category: {markdown_code(step.reason)}\n"
            for step in verification
        )
    lines.extend(["\n## Candidate Scope\n\n"])
    if paths:
        lines.extend(f"- {markdown_code(path)}\n" for path in paths)
    else:
        lines.append("- No changed path was recorded.\n")
    lines.extend(
        (
            "\n## Curator Checklist\n\n",
            "- Root cause is not inferred; add one only with evidence.\n",
            "- Derive a reusable rule from the verified change, not from raw logs.\n",
            "- Check existing memory for duplicates and conflicts.\n",
            "- Re-verify the target files before promoting this candidate to a local memory card.\n",
            "- Verification commands and raw failure output are intentionally omitted.\n",
        )
    )
    return "".join(lines).encode("utf-8", errors="strict").decode("utf-8")
