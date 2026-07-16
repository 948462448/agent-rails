"""Publish private UTF-8 text artifacts with no-follow target semantics."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import stat
import tempfile
from typing import Optional, Tuple


class PrivateTextPublishError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        published: Tuple["PublishedPrivateText", ...] = (),
    ) -> None:
        super().__init__(message)
        self.published = published


class PrivateTextTargetExistsError(PrivateTextPublishError):
    pass


class PrivateTextNonRegularError(PrivateTextPublishError):
    pass


@dataclass(frozen=True)
class PrivateTextArtifact:
    key: str
    target: Path
    content: str
    create_only: bool = False
    staging_prefix: Optional[str] = None


@dataclass(frozen=True)
class PublishedPrivateText:
    key: str
    target: Path


@dataclass
class _StagedArtifact:
    artifact: PrivateTextArtifact
    path: Optional[Path]


def publish_private_text_batch(
    artifacts: Tuple[PrivateTextArtifact, ...],
) -> Tuple[PublishedPrivateText, ...]:
    """Stage every artifact, then publish each atomically in request order.

    The batch is not a cross-directory transaction. A raised error records the
    prefix already published, allowing callers to report partial completion.
    """

    _validate_unique_targets(artifacts)
    staged: list[_StagedArtifact] = []
    try:
        for artifact in artifacts:
            _preflight(artifact)
        for artifact in artifacts:
            staged.append(_stage(artifact))
    except PrivateTextPublishError:
        _cleanup(staged)
        raise
    except (OSError, UnicodeError) as exc:
        _cleanup(staged)
        raise PrivateTextPublishError("Unable to stage private text artifacts.") from exc

    published: list[PublishedPrivateText] = []
    try:
        for item in staged:
            staging_path = item.path
            if staging_path is None:
                raise PrivateTextPublishError("Private text staging state is invalid.")
            target = item.artifact.target
            if item.artifact.create_only:
                try:
                    os.link(staging_path, target)
                except FileExistsError as exc:
                    raise PrivateTextPublishError(
                        f"Private text target appeared during publish: {target}",
                        published=tuple(published),
                    ) from exc
                staging_path.unlink()
            else:
                os.replace(staging_path, target)
            item.path = None
            published.append(PublishedPrivateText(item.artifact.key, target))
    except PrivateTextPublishError:
        _cleanup(staged)
        raise
    except OSError as exc:
        _cleanup(staged)
        raise PrivateTextPublishError(
            "Unable to publish private text artifact.",
            published=tuple(published),
        ) from exc
    _cleanup(staged)
    return tuple(published)


def _validate_unique_targets(artifacts: Tuple[PrivateTextArtifact, ...]) -> None:
    seen: dict[str, str] = {}
    for artifact in artifacts:
        normalized = os.path.abspath(os.fspath(artifact.target))
        previous = seen.get(normalized)
        if previous is not None:
            raise PrivateTextPublishError(
                f"Private text targets overlap: {previous} and {artifact.key}"
            )
        seen[normalized] = artifact.key


def _preflight(artifact: PrivateTextArtifact) -> None:
    target = Path(artifact.target)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        mode = target.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PrivateTextPublishError(
            f"Unable to prepare private text target: {target}"
        ) from exc
    if artifact.create_only:
        raise PrivateTextTargetExistsError(f"Private text target already exists: {target}")
    if not stat.S_ISREG(mode):
        raise PrivateTextNonRegularError(
            f"Private text target is not a regular file: {target}"
        )


def _stage(artifact: PrivateTextArtifact) -> _StagedArtifact:
    target = Path(artifact.target)
    descriptor: Optional[int] = None
    staging_path: Optional[Path] = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            prefix=artifact.staging_prefix or f".{target.name}.agent-rails.",
            dir=target.parent,
        )
        staging_path = Path(raw_path)
        os.fchmod(descriptor, 0o600)
        payload = artifact.content.encode("utf-8", errors="strict")
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        return _StagedArtifact(artifact=artifact, path=staging_path)
    except (OSError, UnicodeError) as exc:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        if staging_path is not None:
            try:
                staging_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise PrivateTextPublishError(
            f"Unable to stage private text target: {target}"
        ) from exc


def _cleanup(staged: list[_StagedArtifact]) -> None:
    for item in staged:
        if item.path is None:
            continue
        try:
            item.path.unlink(missing_ok=True)
        except OSError:
            pass
