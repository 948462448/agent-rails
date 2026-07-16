from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess
from typing import Any, Mapping, Optional, Sequence, Tuple
import unicodedata

from agent_rails.core.paths import same_file_metadata

from ._runner import run_git


_FINGERPRINT_FULL_FILE_LIMIT = 8 * 1024 * 1024
_FINGERPRINT_TOTAL_FULL_LIMIT = 64 * 1024 * 1024
_FINGERPRINT_SAMPLE_SIZE = 1024 * 1024


class GitScopeError(RuntimeError):
    pass


@dataclass(frozen=True)
class GitScope:
    target_ref: str
    target_sha: str
    target_short_sha: str
    head_sha: str
    base_ref: str
    base_sha: str
    merge_base: str

    def shell_values(self) -> Mapping[str, str]:
        return {
            "AGENT_GIT_SCOPE_TARGET_REF": self.target_ref,
            "AGENT_GIT_SCOPE_TARGET_SHA": self.target_sha,
            "AGENT_GIT_SCOPE_TARGET_SHORT_SHA": self.target_short_sha,
            "AGENT_GIT_SCOPE_HEAD_SHA": self.head_sha,
            "AGENT_GIT_SCOPE_BASE_REF": self.base_ref,
            "AGENT_GIT_SCOPE_BASE_SHA": self.base_sha,
            "AGENT_GIT_SCOPE_MERGE_BASE": self.merge_base,
        }


@dataclass(frozen=True)
class GitScopeSnapshot:
    status: str
    staged_paths: Tuple[str, ...]
    unstaged_paths: Tuple[str, ...]
    untracked_paths: Tuple[str, ...]
    committed_paths: Tuple[str, ...]
    worktree_paths: Tuple[str, ...]
    changed_paths: Tuple[str, ...]


@dataclass(frozen=True)
class GitWorktreeSnapshot:
    status: str
    staged_paths: Tuple[str, ...]
    unstaged_paths: Tuple[str, ...]
    untracked_paths: Tuple[str, ...]
    changed_paths: Tuple[str, ...]


def resolve_git_scope(
    project: Path,
    *,
    target_ref: str,
    base_ref: str = "",
    base_policy: str,
    environment: Optional[Mapping[str, str]] = None,
) -> GitScope:
    if base_policy not in {"project", "publish"}:
        raise GitScopeError(f"Unknown Git scope base policy: {base_policy}")

    target = _git(
        project,
        ("rev-parse", "--verify", f"{target_ref}^{{commit}}"),
        environment=environment,
    )
    if target.returncode != 0:
        raise GitScopeError(f"Target ref not found: {target_ref}")
    target_sha = target.stdout.strip()

    resolved_base_ref = base_ref or default_base_ref(
        project, base_policy, environment=environment
    )
    base_sha = ""
    if resolved_base_ref:
        base = _git(
            project,
            ("rev-parse", "--verify", f"{resolved_base_ref}^{{commit}}"),
            environment=environment,
        )
        if base.returncode != 0:
            raise GitScopeError(f"Base ref not found: {resolved_base_ref}")
        base_sha = base.stdout.strip()
        merge = _git(
            project,
            ("merge-base", target_sha, base_sha),
            environment=environment,
        )
        if merge.returncode != 0:
            raise GitScopeError(
                f"Merge base not found between {target_ref} and {resolved_base_ref}."
            )
        merge_base = merge.stdout.strip()
    else:
        merge_base = target_sha

    short = _git(
        project,
        ("rev-parse", "--short", target_sha),
        environment=environment,
    )
    if short.returncode != 0:
        raise GitScopeError(f"Target ref not found: {target_ref}")

    head_sha = resolve_git_head(project, environment=environment)

    return GitScope(
        target_ref=target_ref,
        target_sha=target_sha,
        target_short_sha=short.stdout.strip(),
        head_sha=head_sha,
        base_ref=resolved_base_ref,
        base_sha=base_sha,
        merge_base=merge_base,
    )


def resolve_git_head(
    project: Path,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Resolve the checked-out commit without inheriting another repo's Git context."""
    head = _git(
        project,
        ("rev-parse", "--verify", "HEAD^{commit}"),
        environment=environment,
    )
    if head.returncode != 0:
        raise GitScopeError("Project HEAD commit not found.")
    return head.stdout.strip()


def default_base_ref(
    project: Path,
    policy: str,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    if policy == "project":
        candidates = ("origin/main", "origin/master", "main", "master")
    elif policy == "publish":
        candidates = ("@{upstream}", "origin/main", "origin/master", "main", "master")
    else:
        raise GitScopeError(f"Unknown Git scope base policy: {policy}")

    for candidate in candidates:
        result = _git(
            project,
            ("rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}"),
            environment=environment,
        )
        if result.returncode == 0:
            return candidate
    return ""


def collect_worktree_snapshot(
    project: Path,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> GitWorktreeSnapshot:
    """Collect the current index and worktree state without requiring ``HEAD``."""

    status_z_result = _git(
        project,
        ("status", "--porcelain=v1", "-z", "-uall"),
        environment=environment,
    )
    _require_git_success(status_z_result, "Unable to read Git working tree status.")
    staged_paths, unstaged_paths, untracked_paths = _parse_status_porcelain_z(
        status_z_result.stdout
    )
    changed_paths = tuple(
        sorted(set(staged_paths) | set(unstaged_paths) | set(untracked_paths))
    )

    status_result = _git(
        project,
        ("status", "--porcelain=v1", "-uall"),
        environment=environment,
    )
    _require_git_success(status_result, "Unable to read Git working tree status.")
    return GitWorktreeSnapshot(
        status=status_result.stdout,
        staged_paths=staged_paths,
        unstaged_paths=unstaged_paths,
        untracked_paths=untracked_paths,
        changed_paths=changed_paths,
    )


def fingerprint_git_worktree(
    project: Path,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> str:
    """Fingerprint index, tracked worktree content, and visible untracked files."""

    worktree = collect_worktree_snapshot(project, environment=environment)
    digest = hashlib.sha256()
    _update_fingerprint(
        digest,
        b"status",
        worktree.status.encode("utf-8", "surrogateescape"),
    )
    staged = _git(
        project,
        (
            "diff",
            "--cached",
            "--raw",
            "--no-abbrev",
            "--no-renames",
            "--no-ext-diff",
            "--no-textconv",
            "HEAD",
            "--",
        ),
        environment=environment,
    )
    _require_git_success(staged, "Unable to fingerprint Git index.")
    _update_fingerprint(
        digest,
        b"staged-index",
        staged.stdout.encode("utf-8", "surrogateescape"),
    )

    content_read_budget = [_FINGERPRINT_TOTAL_FULL_LIMIT]
    untracked = set(worktree.untracked_paths)
    for relative_path in sorted(
        set(worktree.unstaged_paths) | untracked
    ):
        _fingerprint_worktree_path(
            digest,
            project,
            relative_path,
            require_present=relative_path in untracked,
            content_read_budget=content_read_budget,
        )
    return digest.hexdigest()


def hidden_worktree_index_paths(
    project: Path,
    *,
    environment: Optional[Mapping[str, str]] = None,
) -> Tuple[str, ...]:
    """Return unsafe paths hidden from status by index worktree flags.

    Missing skip-worktree paths are the normal sparse-checkout representation
    and cannot affect commands in the current checkout, so they remain valid.
    """

    result = _git(
        project,
        ("ls-files", "-v", "-z"),
        environment=environment,
    )
    _require_git_success(result, "Unable to inspect Git index worktree flags.")
    hidden = []
    for record in _nonempty_nul_fields(result.stdout):
        if len(record) < 3 or record[1] != " ":
            raise GitScopeError("Git returned an invalid index flag payload.")
        tag = record[0]
        relative_path = record[2:]
        if tag.islower():
            hidden.append(relative_path)
        elif tag == "S":
            candidate = project / relative_path
            if candidate.exists() or candidate.is_symlink():
                hidden.append(relative_path)
    return tuple(sorted(set(hidden)))


def _fingerprint_worktree_path(
    digest: Any,
    project: Path,
    relative_path: str,
    *,
    require_present: bool,
    content_read_budget: list[int],
) -> None:
    path = project / relative_path
    encoded_path = relative_path.encode("utf-8", "surrogateescape")
    _update_fingerprint(digest, b"worktree-path", encoded_path)
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        if require_present:
            raise GitScopeError(
                f"Unable to fingerprint worktree path: {relative_path}"
            ) from exc
        _update_fingerprint(digest, b"worktree-missing", b"1")
        return
    except OSError as exc:
        raise GitScopeError(
            f"Unable to fingerprint worktree path: {relative_path}"
        ) from exc

    metadata_value = ":".join(
        str(value)
        for value in (
            stat.S_IFMT(metadata.st_mode),
            stat.S_IMODE(metadata.st_mode),
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_dev,
            metadata.st_ino,
        )
    ).encode("ascii")
    _update_fingerprint(digest, b"worktree-metadata", metadata_value)
    try:
        if stat.S_ISLNK(metadata.st_mode):
            target = os.readlink(path).encode("utf-8", "surrogateescape")
            _update_fingerprint(digest, b"worktree-symlink", target)
        elif stat.S_ISREG(metadata.st_mode):
            _fingerprint_regular_file(
                digest,
                path,
                metadata,
                content_read_budget=content_read_budget,
            )
    except OSError as exc:
        raise GitScopeError(
            f"Unable to fingerprint worktree path: {relative_path}"
        ) from exc


def _fingerprint_regular_file(
    digest: Any,
    path: Path,
    metadata: os.stat_result,
    *,
    content_read_budget: list[int],
) -> None:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not same_file_metadata(metadata, opened):
            raise GitScopeError(f"Worktree path moved while fingerprinting: {path}")
        file_digest = hashlib.sha256()
        use_full_content = (
            opened.st_size <= _FINGERPRINT_FULL_FILE_LIMIT
            and opened.st_size <= content_read_budget[0]
        )
        if use_full_content:
            content_read_budget[0] -= opened.st_size
            mode = b"full"
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise GitScopeError(
                        f"Worktree path changed while fingerprinting: {path}"
                    )
                file_digest.update(chunk)
                remaining -= len(chunk)
        else:
            sample_budget = min(
                content_read_budget[0], 2 * _FINGERPRINT_SAMPLE_SIZE
            )
            content_read_budget[0] -= sample_budget
            if sample_budget:
                mode = b"sampled"
                first_size = (sample_budget + 1) // 2
                last_size = sample_budget // 2
                first = os.read(descriptor, first_size)
                file_digest.update(len(first).to_bytes(8, "big"))
                file_digest.update(first)
                tail_offset = max(0, opened.st_size - last_size)
                os.lseek(descriptor, tail_offset, os.SEEK_SET)
                last = os.read(descriptor, last_size)
                file_digest.update(len(last).to_bytes(8, "big"))
                file_digest.update(last)
            else:
                mode = b"metadata-only"
        closed = os.fstat(descriptor)
        if not same_file_metadata(opened, closed):
            raise GitScopeError(f"Worktree path moved while fingerprinting: {path}")
    finally:
        os.close(descriptor)
    _update_fingerprint(digest, b"worktree-content-mode", mode)
    _update_fingerprint(digest, b"worktree-content-sha256", file_digest.digest())


def _update_fingerprint(digest: Any, label: bytes, value: bytes) -> None:
    digest.update(len(label).to_bytes(4, "big"))
    digest.update(label)
    digest.update(len(value).to_bytes(8, "big"))
    digest.update(value)


def write_git_scope_snapshot(
    project: Path,
    scope: GitScope,
    output_dir: Path,
    *,
    include_worktree: bool,
    environment: Optional[Mapping[str, str]] = None,
) -> GitScopeSnapshot:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise GitScopeError(f"Unable to write Git scope snapshot: {output_dir}") from exc

    snapshot = collect_git_scope_snapshot(
        project,
        scope,
        include_worktree=include_worktree,
        environment=environment,
    )
    try:
        _write_lines(output_dir / "status", snapshot.status.splitlines())
        _write_lines(output_dir / "staged-paths", snapshot.staged_paths)
        _write_lines(output_dir / "unstaged-paths", snapshot.unstaged_paths)
        _write_lines(output_dir / "untracked-paths", snapshot.untracked_paths)
        _write_nul_paths(output_dir / "untracked-paths0", snapshot.untracked_paths)
        _write_lines(output_dir / "committed-paths", snapshot.committed_paths)
        _write_lines(output_dir / "worktree-paths", snapshot.worktree_paths)
        _write_lines(output_dir / "changed-paths", snapshot.changed_paths)
        _write_nul_paths(output_dir / "changed-paths0", snapshot.changed_paths)
    except OSError as exc:
        raise GitScopeError(f"Unable to write Git scope snapshot: {output_dir}") from exc

    return snapshot


def collect_git_scope_snapshot(
    project: Path,
    scope: GitScope,
    *,
    include_worktree: bool,
    environment: Optional[Mapping[str, str]] = None,
) -> GitScopeSnapshot:
    """Collect committed and optional worktree paths without a temp bundle."""

    worktree = (
        collect_worktree_snapshot(project, environment=environment)
        if include_worktree
        else GitWorktreeSnapshot(
            status="",
            staged_paths=(),
            unstaged_paths=(),
            untracked_paths=(),
            changed_paths=(),
        )
    )
    status = worktree.status
    staged_paths = worktree.staged_paths
    unstaged_paths = worktree.unstaged_paths
    untracked_paths = worktree.untracked_paths
    worktree_paths = worktree.changed_paths

    if scope.base_ref:
        committed_result = _git(
            project,
            (
                "diff",
                "--name-only",
                "-z",
                f"{scope.merge_base}...{scope.target_sha}",
            ),
            environment=environment,
        )
        _require_git_success(committed_result, "Unable to read committed Git scope.")
        committed_paths = _sorted_nonempty_nul_fields(committed_result.stdout)
    else:
        committed_paths = ()

    changed_paths = tuple(sorted(set(committed_paths) | set(worktree_paths)))
    return GitScopeSnapshot(
        status=status,
        staged_paths=staged_paths,
        unstaged_paths=unstaged_paths,
        untracked_paths=untracked_paths,
        committed_paths=committed_paths,
        worktree_paths=worktree_paths,
        changed_paths=changed_paths,
    )


def _git(
    project: Path,
    arguments: Sequence[str],
    *,
    environment: Optional[Mapping[str, str]],
) -> subprocess.CompletedProcess[str]:
    try:
        return run_git(project, arguments, environment=environment)
    except OSError as exc:
        raise GitScopeError("Git command is unavailable.") from exc


def _require_git_success(
    completed: subprocess.CompletedProcess[str], message: str
) -> None:
    if completed.returncode != 0:
        raise GitScopeError(message)


def _parse_status_porcelain_z(
    value: str,
) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    fields = list(_nonempty_nul_fields(value))
    staged = set()
    unstaged = set()
    untracked = set()
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if len(record) < 4 or record[2] != " ":
            raise GitScopeError("Git returned an invalid working tree status payload.")

        status = record[:2]
        path = record[3:]
        if status == "??":
            untracked.add(path)
        elif status != "!!":
            if status[0] != " ":
                staged.add(path)
            if status[1] != " ":
                unstaged.add(path)

        if status[0] in "RC" or status[1] in "RC":
            if index >= len(fields):
                raise GitScopeError("Git returned an invalid rename status payload.")
            index += 1

    return tuple(sorted(staged)), tuple(sorted(unstaged)), tuple(sorted(untracked))


def _nonempty_nul_fields(value: str) -> Tuple[str, ...]:
    fields = value.split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    return tuple(field for field in fields if field)


def _sorted_nonempty_nul_fields(value: str) -> Tuple[str, ...]:
    return tuple(sorted(set(_nonempty_nul_fields(value))))


def _write_lines(path: Path, lines: Sequence[str]) -> None:
    if any(_contains_unsafe_control(line) for line in lines):
        raise GitScopeError(
            "Git paths containing control characters are unsupported by Shell compatibility snapshots."
        )
    content = "".join(f"{line}\n" for line in lines)
    path.write_text(content, encoding="utf-8", errors="surrogateescape")


def _write_nul_paths(path: Path, paths: Sequence[str]) -> None:
    content = b"".join(
        item.encode("utf-8", errors="surrogateescape") + b"\0" for item in paths
    )
    path.write_bytes(content)


def read_nul_paths(path: Path) -> Tuple[str, ...]:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise GitScopeError(f"Unable to read NUL-delimited Git paths: {path}") from exc
    if not payload:
        return ()
    if not payload.endswith(b"\0"):
        raise GitScopeError(f"Invalid NUL-delimited Git paths: {path}")
    return tuple(
        field.decode("utf-8", errors="surrogateescape")
        for field in payload[:-1].split(b"\0")
        if field
    )


def _contains_unsafe_control(value: str) -> bool:
    return any(
        unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
        or 0xDC80 <= ord(character) <= 0xDC9F
        for character in value
    )
