from __future__ import annotations

import re
from typing import Iterable, Iterator, Tuple


_ASCII_SPACE = " \t\r\v\f"
_PRIVATE_KEY_BEGIN = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_PRIVATE_KEY_END = re.compile(r"-----END [A-Z ]*PRIVATE KEY(?: BLOCK)?-----")
_SENSITIVE_KEY = re.compile(
    r"(^|[_.-])"
    r"(access[_-]?key|api[_-]?key|secret|token|cookie|auth|authorization|password|private[_-]?key)"
    r"([_.-]|$)"
)


class SensitiveOutputError(RuntimeError):
    pass


def redact_sensitive_output(text: str, *, format_name: str = "text") -> str:
    return "".join(
        f"{line}\n"
        for line in redact_sensitive_records(
            _records(text), format_name=format_name
        )
    )


def scan_sensitive_output(
    text: str,
    *,
    source_name: str,
    format_name: str = "text",
) -> Tuple[str, ...]:
    return tuple(
        scan_sensitive_records(
            _records(text), source_name=source_name, format_name=format_name
        )
    )


def redact_sensitive_records(
    records: Iterable[str], *, format_name: str = "text"
) -> Iterator[str]:
    _validate_options(mode="redact", format_name=format_name)
    return _process_records(
        records, mode="redact", source_name="", format_name=format_name
    )


def scan_sensitive_records(
    records: Iterable[str],
    *,
    source_name: str,
    format_name: str = "text",
) -> Iterator[str]:
    _validate_options(mode="scan", format_name=format_name)
    return _process_records(
        records,
        mode="scan",
        source_name=source_name,
        format_name=format_name,
    )


def _validate_options(*, mode: str, format_name: str) -> None:
    if format_name not in {"text", "diff"}:
        raise SensitiveOutputError(f"Unknown sensitive-output format: {format_name}")
    if mode not in {"redact", "scan"}:
        raise SensitiveOutputError(f"Unknown sensitive-output mode: {mode}")


def _process_records(
    records: Iterable[str],
    *,
    mode: str,
    source_name: str,
    format_name: str,
) -> Iterator[str]:
    inside_private_key = False
    diff_hunk = False
    diff_line = 0

    for file_line, original in enumerate(records, start=1):
        prefix = ""
        content = original
        source_line = file_line

        if mode == "scan" and format_name == "diff":
            if content.startswith("diff --git "):
                inside_private_key = False
                diff_hunk = False
                continue
            if not diff_hunk and content.startswith("+++ "):
                source_name = content[4:]
                continue
            if content.startswith("@@ "):
                inside_private_key = False
                diff_hunk = False
                match = re.search(r"\+([0-9]+)", content)
                if match:
                    diff_line = int(match.group(1))
                    diff_hunk = True
                continue
            if not diff_hunk:
                continue
            if content.startswith("+"):
                source_line = diff_line
                diff_line += 1
                content = content[1:]
            elif content.startswith(" "):
                diff_line += 1
                continue
            else:
                continue

        if (
            mode == "redact"
            and format_name == "diff"
            and content.startswith(("+", "-", " "))
            and not re.match(r"^-----(BEGIN|END) ", content)
            and not re.match(r"^(---|\+\+\+)[ \t\r\v\f]", content)
        ):
            prefix = content[0]
            content = content[1:]

        if inside_private_key:
            if _PRIVATE_KEY_END.search(content):
                inside_private_key = False
            continue
        if _PRIVATE_KEY_BEGIN.search(content):
            redacted = f"{prefix}<redacted private key block>"
            if mode == "scan":
                yield f"{source_name}:{source_line}: {redacted}"
            else:
                yield redacted
            inside_private_key = True
            continue

        separator = re.search(r"[=:]", content)
        sensitive = False
        if separator:
            key = _assignment_key(content, separator.start())
            value = _assignment_value(content, separator.end())
            sensitive = _is_sensitive_key(key) and not _is_placeholder(value)
            if mode == "scan" and sensitive:
                sensitive = not _is_code_expression(value)

        if sensitive and separator:
            redacted = _redact_assignment(prefix, content, separator.start())
            if mode == "scan":
                yield f"{source_name}:{source_line}: {redacted}"
            else:
                yield redacted
        elif mode == "redact":
            yield original


def _records(text: str) -> Iterator[str]:
    start = 0
    while start < len(text):
        newline = text.find("\n", start)
        if newline < 0:
            yield text[start:]
            return
        yield text[start:newline]
        start = newline + 1


def _assignment_key(content: str, separator_start: int) -> str:
    key = content[:separator_start]
    key = re.sub(r"^.*[{,][ \t\r\v\f]*", "", key, count=1)
    key = re.sub(r"^[ \t\r\v\f]*export[ \t\r\v\f]+", "", key, count=1)
    key = re.sub(r'^[ \t\r\v\f"`]+', "", key)
    key = re.sub(r'[ \t\r\v\f"`]+$', "", key)
    return key.lower()


def _assignment_value(content: str, separator_end: int) -> str:
    value = content[separator_end:]
    value = value.lstrip(_ASCII_SPACE).rstrip(_ASCII_SPACE)
    value = re.sub(r",[ \t\r\v\f]*$", "", value)
    value = value.rstrip(_ASCII_SPACE)
    if value[:1] in {'"', "'", "`"}:
        if len(value) == 1:
            value = ""
        elif value[-1] == value[0]:
            value = value[1:-1]
    return value


def _is_placeholder(value: str) -> bool:
    lower = value.lower()
    return (
        value == ""
        or re.fullmatch(r"\$[A-Za-z_][A-Za-z0-9_]*", value) is not None
        or re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_:-]*\}", value) is not None
        or re.fullmatch(r"[A-Z0-9_]+", value) is not None
        or lower
        in {
            "dummy",
            "example",
            "placeholder",
            "changeme",
            "todo",
            "null",
            "none",
            "redacted",
            "<redacted>",
        }
    )


def _is_sensitive_key(key: str) -> bool:
    if "tokenizer" in key or "tiktoken" in key:
        return False
    return _SENSITIVE_KEY.search(key) is not None


def _is_code_expression(value: str) -> bool:
    lower = value.lower()
    bracket = value.find("[")
    indexed_name = (
        bracket > 0
        and value[bracket + 1 : -1] != ""
        and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value[:bracket]) is not None
        and value.endswith("]")
    )
    return (
        value.startswith("$(")
        or re.match(r"^\$\{?[A-Za-z_]", value) is not None
        or re.match(r"^\$[0-9@*#?!-]", value) is not None
        or (value.startswith("${") and value.endswith("}"))
        or re.fullmatch(r"\$\{[A-Za-z_][A-Za-z0-9_:-]*\}", value) is not None
        or re.search(r"\$\{[A-Za-z_][A-Za-z0-9_:-]*\}", value) is not None
        or re.match(r"^[A-Za-z_][A-Za-z0-9_.]*[ \t\r\v\f]*\(", value) is not None
        or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value)
        is not None
        or indexed_name
        or value.startswith(("/", "./", "../", "~/"))
        or re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value) is not None
        or lower in {"true", "false"}
    )


def _redact_assignment(prefix: str, content: str, separator_start: int) -> str:
    head = content[: separator_start + 1]
    tail = content[separator_start + 1 :]
    spacing_match = re.match(r"[ \t\r\v\f]*", tail)
    spacing = spacing_match.group(0) if spacing_match else ""
    trimmed = tail.lstrip(_ASCII_SPACE)
    quote = trimmed[0] if trimmed[:1] in {'"', "'", "`"} else ""
    comma = "," if re.search(r",[ \t\r\v\f]*$", trimmed) else ""
    return f"{prefix}{head}{spacing}{quote}<redacted>{quote}{comma}"
