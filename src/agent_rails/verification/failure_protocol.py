"""Track repeated verification failures without retaining their raw evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Optional, Protocol, Tuple

from agent_rails.core.private_text import (
    PrivateTextArtifact,
    PrivateTextPublishError,
    publish_private_text_batch,
)
from agent_rails.core.terminal import terminal_stream_text
from agent_rails.security.sensitive_output import (
    SensitiveOutputError,
    redact_sensitive_output,
)


_FORMAT = "agent-rails-verification-failure-v1"
_MAX_STATE_BYTES = 2_048
_MAX_CONSECUTIVE = 3
_HIGH_VALUE = re.compile(
    r"(assert(?:ion)?error|error|exception|fail(?:ed|ure)?|fatal|panic|"
    r"syntaxerror|traceback)",
    re.IGNORECASE,
)
_LOCATION_NUMBERS = re.compile(r":\d+(?::\d+)?")
_WHITESPACE = re.compile(r"\s+")


class FailureLike(Protocol):
    reason: str
    exit_code: int
    stdout: str
    stderr: str


class FailureAction(str, Enum):
    REPAIR = "repair"
    CHANGE_STRATEGY = "change-strategy"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class FailureEscalation:
    fingerprint: str
    consecutive_count: int
    action: FailureAction
    history_persisted: bool


@dataclass(frozen=True)
class FailureEvidence:
    stderr: str
    stdout: str
    excerpt: Tuple[str, ...]


@dataclass(frozen=True)
class FailureHistory:
    fingerprint: str
    consecutive_count: int


@dataclass(frozen=True)
class _FailureState:
    target_fingerprint: str
    failure_fingerprint: str
    consecutive_count: int


def failure_history_path(config_home: Path, project_root: Path) -> Path:
    """Return one user-scoped state path without trusting a project slug."""

    if not config_home.is_absolute():
        raise ValueError("Agent Rails config home must be absolute.")
    identity = os.path.realpath(os.fspath(project_root)).encode(
        "utf-8", errors="surrogateescape"
    )
    digest = hashlib.sha256(identity).hexdigest()[:24]
    return config_home / "verification-history" / f"failure-{digest}.json"


def prepare_failure_evidence(failure: FailureLike) -> FailureEvidence:
    """Redact once and select the same first diagnostic for every consumer."""

    stderr = _safe_output(failure.stderr)
    stdout = _safe_output(failure.stdout)
    lines = [line for line in stderr.splitlines() if line.strip()]
    lines.extend(line for line in stdout.splitlines() if line.strip())
    if not lines:
        excerpt = ("No diagnostic output captured.",)
    else:
        diagnostic_index = len(lines) - 1
        for index, line in enumerate(lines):
            if _HIGH_VALUE.search(line):
                diagnostic_index = index
                break
        excerpt = tuple(
            lines[max(0, diagnostic_index - 1) : diagnostic_index + 3]
        )
    return FailureEvidence(stderr=stderr, stdout=stdout, excerpt=excerpt)


def observe_failure(
    state_path: Optional[Path],
    target_identity: str,
    failure: FailureLike,
) -> FailureEscalation:
    """Record one failure and return its bounded consecutive-failure action."""

    evidence = prepare_failure_evidence(failure)
    fingerprint = _failure_fingerprint(failure, evidence)
    target_fingerprint = hashlib.sha256(
        (target_identity or "non-git").encode("utf-8", errors="surrogateescape")
    ).hexdigest()
    if state_path is None:
        return _escalation(fingerprint, 1, False)

    previous, safe_to_publish = _read_state(state_path)
    if not safe_to_publish:
        return _escalation(fingerprint, 1, False)
    count = 1
    if (
        previous is not None
        and previous.target_fingerprint == target_fingerprint
        and previous.failure_fingerprint == fingerprint
    ):
        count = min(previous.consecutive_count + 1, _MAX_CONSECUTIVE)
    state = _FailureState(target_fingerprint, fingerprint, count)
    persisted = _publish_state(state_path, state)
    return _escalation(fingerprint, count, persisted)


def clear_failure_history(state_path: Optional[Path]) -> bool:
    """Clear consecutive state after a successful, executed verification."""

    if state_path is None:
        return False
    try:
        state_path.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    _, safe_to_publish = _read_state(state_path)
    if not safe_to_publish:
        return False
    return _publish_payload(
        state_path,
        json.dumps({"format": _FORMAT, "cleared": True}, sort_keys=True) + "\n",
    )


def read_failure_history(
    state_path: Optional[Path], target_identity: str
) -> Optional[FailureHistory]:
    """Return bounded prior failure metadata for one fixed verification target."""

    if state_path is None:
        return None
    previous, safe = _read_state(state_path)
    expected_target = hashlib.sha256(
        (target_identity or "non-git").encode("utf-8", errors="surrogateescape")
    ).hexdigest()
    if not safe or previous is None or previous.target_fingerprint != expected_target:
        return None
    return FailureHistory(
        fingerprint=previous.failure_fingerprint,
        consecutive_count=previous.consecutive_count,
    )


def _escalation(
    fingerprint: str, count: int, persisted: bool
) -> FailureEscalation:
    action = FailureAction.REPAIR
    if count >= 3:
        action = FailureAction.ESCALATE
    elif count == 2:
        action = FailureAction.CHANGE_STRATEGY
    return FailureEscalation(
        fingerprint=fingerprint,
        consecutive_count=count,
        action=action,
        history_persisted=persisted,
    )


def _failure_fingerprint(
    failure: FailureLike, evidence: FailureEvidence
) -> str:
    reason = _normalize(_safe_output(failure.reason))
    diagnostic = _normalize(evidence.excerpt[0] if evidence.excerpt else "")
    for line in evidence.excerpt:
        if _HIGH_VALUE.search(line):
            diagnostic = _normalize(line)
            break
    payload = json.dumps(
        {
            "reason": reason,
            "exit_code": int(failure.exit_code),
            "diagnostic": diagnostic,
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(
        " ", _LOCATION_NUMBERS.sub(":<line>", text).strip().lower()
    )


def _safe_output(text: str) -> str:
    try:
        return redact_sensitive_output(
            terminal_stream_text(text), format_name="text"
        )
    except (SensitiveOutputError, UnicodeError, OSError):
        return "[verification evidence omitted: sensitive-output guard failed]\n"


def _read_state(path: Path) -> tuple[Optional[_FailureState], bool]:
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        return None, False
    descriptor: Optional[int] = None
    try:
        descriptor = os.open(path, os.O_RDONLY | no_follow)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            return None, False
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            payload = handle.read(_MAX_STATE_BYTES + 1)
    except FileNotFoundError:
        return None, True
    except OSError:
        return None, False
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > _MAX_STATE_BYTES:
        return None, True
    try:
        value = json.loads(payload.decode("utf-8", errors="strict"))
    except (UnicodeError, json.JSONDecodeError):
        return None, True
    if not isinstance(value, dict) or value.get("format") != _FORMAT:
        return None, True
    target = value.get("target_fingerprint")
    failure = value.get("fingerprint")
    count = value.get("consecutive_count")
    if (
        not isinstance(target, str)
        or not re.fullmatch(r"[0-9a-f]{64}", target)
        or not isinstance(failure, str)
        or not re.fullmatch(r"[0-9a-f]{64}", failure)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or not 1 <= count <= _MAX_CONSECUTIVE
    ):
        return None, True
    return _FailureState(target, failure, count), True


def _publish_state(path: Path, state: _FailureState) -> bool:
    payload = json.dumps(
        {
            "format": _FORMAT,
            "target_fingerprint": state.target_fingerprint,
            "fingerprint": state.failure_fingerprint,
            "consecutive_count": state.consecutive_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    ) + "\n"
    return _publish_payload(path, payload)


def _publish_payload(path: Path, payload: str) -> bool:
    try:
        publish_private_text_batch(
            (
                PrivateTextArtifact(
                    key="verification-failure-history",
                    target=path,
                    content=payload,
                ),
            )
        )
    except (PrivateTextPublishError, OSError, UnicodeError):
        return False
    return True
