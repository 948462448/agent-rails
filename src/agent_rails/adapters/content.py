from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import Enum
import re
import shlex
import unicodedata


class AdapterContentError(ValueError):
    """Raised when generated adapter content cannot be rendered safely."""


_NON_VISIBLE_CATEGORIES = {"Cc", "Cf", "Cs", "Zl", "Zp"}


class AdapterType(str, Enum):
    CLAUDE = "claude"
    OPENCODE = "opencode"


class AdapterArtifact(str, Enum):
    GUIDE = "guide"
    PACK = "pack"
    LITE = "lite"
    CHECK = "check"
    CLAUDE_BLOCK = "claude-block"


@dataclass(frozen=True)
class AdapterContentRequest:
    adapter: AdapterType
    version: str
    executable: str
    profile: str = ""


def render_adapter_content(
    request: AdapterContentRequest, artifact: AdapterArtifact
) -> str:
    """Render one deterministic, generated local-adapter artifact."""

    if not isinstance(request, AdapterContentRequest):
        raise AdapterContentError("Invalid Agent Rails adapter content request.")
    if not isinstance(request.adapter, AdapterType):
        raise AdapterContentError("Invalid Agent Rails adapter content type.")
    if not isinstance(artifact, AdapterArtifact):
        raise AdapterContentError("Invalid Agent Rails adapter artifact.")
    for name, value in (
        ("version", request.version),
        ("executable", request.executable),
        ("profile", request.profile),
    ):
        if not isinstance(value, str):
            raise AdapterContentError(
                f"Agent Rails adapter content {name} must be text."
            )
    _validate_scalar("version", request.version)
    _validate_scalar("executable", request.executable)
    _validate_scalar("profile", request.profile)
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+-]{0,63}", request.version) is None:
        raise AdapterContentError(
            "Agent Rails adapter content version is invalid."
        )
    if not request.executable:
        raise AdapterContentError("Agent Rails adapter content executable is empty.")
    if artifact is AdapterArtifact.CLAUDE_BLOCK:
        if request.adapter is not AdapterType.CLAUDE:
            raise AdapterContentError(
                "The Claude project block requires the Claude adapter."
            )
        return _render_claude_block(request)
    if artifact is AdapterArtifact.GUIDE:
        if request.adapter is AdapterType.CLAUDE:
            return _render_claude_guide(request)
        return _render_opencode_guide(request)
    return _render_command(request, artifact)


def _validate_scalar(name: str, value: str) -> None:
    if any(
        ord(character) < 32
        or ord(character) == 127
        or 0xD800 <= ord(character) <= 0xDFFF
        for character in value
    ):
        raise AdapterContentError(
            f"Agent Rails adapter content {name} must be one valid UTF-8 visible line."
        )


def _executable(request: AdapterContentRequest) -> str:
    return shlex.quote(request.executable)


def _profile_argument(request: AdapterContentRequest) -> str:
    return render_profile_argument(request.profile)


def render_profile_argument(profile: str) -> str:
    """Render one optional profile flag as inert shell command text."""

    if not isinstance(profile, str):
        raise AdapterContentError("Agent Rails adapter content profile must be text.")
    _validate_scalar("profile", profile)
    if not profile:
        return ""
    if _requires_encoded_shell_literal(profile):
        encoded = profile.encode("utf-8", errors="strict")
        literal = "$'" + "".join(f"\\x{byte:02x}" for byte in encoded) + "'"
        return f" --profile {literal}"
    if not any(
        character in profile for character in ('\\', '"', '$', '`')
    ):
        return f' --profile "{profile}"'
    return f" --profile {shlex.quote(profile)}"


_PROFILE_MARKER_PREFIX = "<!-- agent-rails:profile-b64:"


def _requires_encoded_shell_literal(value: str) -> bool:
    return any(
        unicodedata.category(character) in _NON_VISIBLE_CATEGORIES
        for character in value
    )


def _profile_marker(request: AdapterContentRequest) -> str:
    if not request.profile or not any(
        character in request.profile for character in ('\\', '"', '$', '`')
    ) and not _requires_encoded_shell_literal(request.profile):
        return ""
    encoded = base64.urlsafe_b64encode(request.profile.encode("utf-8")).decode("ascii")
    return f"{_PROFILE_MARKER_PREFIX}{encoded} -->\n"


def extract_adapter_profile(content: str) -> str:
    """Read generated profile metadata with legacy command-text fallback."""

    if not isinstance(content, str):
        raise AdapterContentError("Adapter content must be text.")
    for line in content.splitlines():
        if line.startswith(_PROFILE_MARKER_PREFIX) and line.endswith(" -->"):
            encoded = line[len(_PROFILE_MARKER_PREFIX) : -4]
            try:
                if not encoded:
                    raise ValueError("empty profile metadata")
                raw = base64.b64decode(encoded, altchars=b"-_", validate=True)
                profile = raw.decode("utf-8")
                canonical = base64.urlsafe_b64encode(raw).decode("ascii")
                if not profile or canonical != encoded:
                    raise ValueError("non-canonical profile metadata")
            except (ValueError, UnicodeError) as exc:
                raise AdapterContentError(
                    "Invalid Agent Rails adapter profile metadata."
                ) from exc
            _validate_scalar("profile", profile)
            return profile
    legacy = re.search(r'--profile "([^"\r\n]+)"', content)
    return legacy.group(1) if legacy else ""


def _render_claude_guide(request: AdapterContentRequest) -> str:
    executable = _executable(request)
    profile_argument = _profile_argument(request)
    profile_marker = _profile_marker(request)
    return f'''<!-- agent-rails:generated -->
{profile_marker}# Agent Rails

This project is configured to use Agent Rails for context orchestration.

Agent Rails Version: {request.version}

Before work, choose the smallest useful Agent Rails path:

- Deep pack: 2+ subprojects, API/contracts/schema/data-model changes, ADR/handbook work, migrations/refactors, or ambiguous product decisions.
- Lite pack: POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook.
- Check only: read-only deploy/release/upload workflows that consume the current branch.
- Skip: pure status queries, simple command output, or fixed operations with no repo change and no branch-consumption risk.

Visible session marker protocol:

- Pack or lite: tell the user the AGENT RAILS: ON marker printed by the pack command before continuing.
- Check only: tell the user AGENT RAILS: CHECK-ONLY (reason=<reason>) before continuing.
- Skip: tell the user AGENT RAILS: SKIPPED (reason=<reason>) before continuing.

Generate and read a Task Pack when the matrix says pack:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} "<goal>"
```

For lite POC/deploy-prep work:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} --pack-mode lite "<goal>"
```

Task Pack path is worktree-specific. Read the path printed by the pack command, not a stale pack from another worktree.

Follow the Task Pack sections in order:

1. Agent Rails Contract
2. Relevant Entry Docs
3. Memory Cards
4. Grill Gate
5. Verification Suggestions
6. Subagent Result Contract
7. Delivery Checklist

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

When delegating to a subagent, require the subagent to return the Subagent Result Contract from the Task Pack.

Use `project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"; {executable} check --project "$project_root"{profile_argument} --print-only` before final delivery, and as Step 0 for deploy/release/upload workflows that consume this branch.

After delivery, use `agent-memory-curator` to decide whether this task produced reusable memory. If not, record a skip reason:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} memory suggest --project "$project_root"{profile_argument} --decision skip --reason "<why no durable memory>"
```

If the lesson is durable, write one small local card:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} memory suggest --project "$project_root"{profile_argument} --decision keep --write-local --title "<short title>" --trigger "<trigger>" --applies-to "<scope>" --verify "<check>" --caution "<scope limits>" "<brief reusable lesson>"
```

This kit writes only curated local memory. Treat the external online memory Adapter as read-only; its credentials and provider protocol stay outside Agent Rails.
'''


def _render_claude_block(request: AdapterContentRequest) -> str:
    executable = _executable(request)
    profile_argument = _profile_argument(request)
    profile_marker = _profile_marker(request)
    return f'''<!-- agent-rails:start -->
{profile_marker}## Agent Rails

Agent Rails Version: {request.version}

Use Agent Rails before reading broad context or editing files when this work touches 2+ subprojects, APIs/contracts/schemas/data models, ADRs/handbooks, migrations/refactors, or ambiguous product decisions. For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use `--pack-mode lite`. Pure status queries or fixed operations with no repo change and no branch-consumption risk can skip pack.

Visible session marker protocol:

- If using pack or lite, first tell the user the AGENT RAILS: ON marker printed by the pack command.
- If using check-only, first tell the user: AGENT RAILS: CHECK-ONLY (reason=<reason>).
- If intentionally skipping Agent Rails, first tell the user: AGENT RAILS: SKIPPED (reason=<reason>).

1. Generate the Task Pack:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} "<goal>"
```

   For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} --pack-mode lite "<goal>"
```

2. Read the generated Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

3. Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist.

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

When delegating to a subagent, require the subagent to return the Subagent Result Contract from the Task Pack.

Before final delivery, print verification suggestions:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} check --project "$project_root"{profile_argument} --print-only
```

For deploy/release/upload workflows that consume the current branch, treat that check command as Step 0.
<!-- agent-rails:end -->
'''


def _render_opencode_guide(request: AdapterContentRequest) -> str:
    executable = _executable(request)
    profile_argument = _profile_argument(request)
    profile_marker = _profile_marker(request)
    return f'''<!-- agent-rails:generated -->
{profile_marker}## Agent Rails

Agent Rails Version: {request.version}

This project has a local opencode adapter for Agent Rails. Treat Agent Rails as active before broad repository reads or file edits when this work touches 2+ subprojects, APIs/contracts/schemas/data models, ADRs/handbooks, migrations/refactors, or ambiguous product decisions. For POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook, use `--pack-mode lite`. Pure status queries or fixed operations with no repo change and no branch-consumption risk can skip pack.

Visible session marker protocol:

- If using pack or lite, first tell the user the AGENT RAILS: ON marker printed by the pack command.
- If using check-only, first tell the user: AGENT RAILS: CHECK-ONLY (reason=<reason>).
- If intentionally skipping Agent Rails, first tell the user: AGENT RAILS: SKIPPED (reason=<reason>).

Generate the Task Pack:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} "<goal>"
```

For lite mode:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} --pack-mode lite "<goal>"
```

Read the generated Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist before making changes.

Use the Grill Gate before architecture, refactor, migration, API contract, data model, or ambiguous product work. Ask one decision question at a time, provide your recommended answer, and inspect repo evidence before asking the user. Keep full grills to the Task Pack question budget; move remaining non-blocking choices into deferred decisions. In lite mode, skip full grill and ask only blockers.

Before final delivery, print verification suggestions:

```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} check --project "$project_root"{profile_argument} --print-only
```

For deploy/release/upload workflows that consume the current branch, treat that check command as Step 0.
'''


_DESCRIPTIONS = {
    (AdapterType.CLAUDE, AdapterArtifact.PACK): (
        "Generate and read the Agent Rails Task Pack before engineering work; "
        "use --pack-mode lite for POCs and deploy prep",
        "argument-hint: [goal]",
    ),
    (AdapterType.CLAUDE, AdapterArtifact.LITE): (
        "Generate and read a lite Agent Rails Task Pack for POCs, deploy prep, "
        "codegen checks, and quick continuation work",
        "argument-hint: [goal]",
    ),
    (AdapterType.CLAUDE, AdapterArtifact.CHECK): (
        "Print Agent Rails verification suggestions for the current project",
        "argument-hint: [optional check args]",
    ),
    (AdapterType.OPENCODE, AdapterArtifact.PACK): (
        "Generate and read the Agent Rails Task Pack before engineering work; "
        "use lite mode for POCs and deploy prep.",
        "agent: build",
    ),
    (AdapterType.OPENCODE, AdapterArtifact.LITE): (
        "Generate and read a lite Agent Rails Task Pack for POCs, deploy prep, "
        "codegen checks, and quick continuation work.",
        "agent: build",
    ),
    (AdapterType.OPENCODE, AdapterArtifact.CHECK): (
        "Print Agent Rails verification suggestions for the current project.",
        "agent: build",
    ),
}


def _render_command(
    request: AdapterContentRequest, artifact: AdapterArtifact
) -> str:
    description, metadata = _DESCRIPTIONS[(request.adapter, artifact)]
    executable = _executable(request)
    profile_argument = _profile_argument(request)
    prefix = f'''---
description: {description}
{metadata}
---

<!-- agent-rails:generated -->

Run this command:

'''
    if artifact is AdapterArtifact.PACK:
        body = f'''```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} "$ARGUMENTS"
```

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Follow its Agent Rails Contract, Grill Gate, Memory Cards, Verification Suggestions, Subagent Result Contract, and Delivery Checklist before making changes.
'''
    elif artifact is AdapterArtifact.LITE:
        body = f'''```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} pack --project "$project_root"{profile_argument} --pack-mode lite "$ARGUMENTS"
```

Then read the Task Pack path printed by the command. Do not reuse a pack generated for another worktree.

Before continuing, tell the user the AGENT RAILS: ON (...) marker printed by the command.

Use lite mode for POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook. Skip full grill; keep only blocker questions, assumptions, deferred decisions, Memory Cards, Verification Suggestions, and Delivery Checklist.
'''
    else:
        body = f'''```bash
project_root="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
{executable} check --project "$project_root"{profile_argument} --print-only $ARGUMENTS
```

Before continuing, tell the user:

```text
AGENT RAILS: CHECK-ONLY (reason=verification)
```

Use the output to decide which verification commands to run before final delivery.
'''
    return prefix + body
