#!/usr/bin/env bash
# Shared sensitive-output detection and redaction for Agent Rails integrations.

_AGENT_SENSITIVE_OUTPUT_AWK='
function trim_value(value, quote) {
  gsub(/^[[:space:]]+/, "", value)
  gsub(/[[:space:]]+$/, "", value)
  sub(/,[[:space:]]*$/, "", value)
  gsub(/[[:space:]]+$/, "", value)
  quote = substr(value, 1, 1)
  if ((quote == "\"" || quote == sprintf("%c", 39) || quote == "`") && substr(value, length(value), 1) == quote) {
    value = substr(value, 2, length(value) - 2)
  }
  return value
}
function assignment_key(content, key) {
  key = substr(content, 1, RSTART - 1)
  sub(/^.*[{,][[:space:]]*/, "", key)
  sub(/^[[:space:]]*export[[:space:]]+/, "", key)
  gsub(/^[[:space:]\"`]+/, "", key)
  gsub(/[[:space:]\"`]+$/, "", key)
  return tolower(key)
}
function assignment_value(content, value) {
  value = substr(content, RSTART + 1)
  return trim_value(value)
}
function is_placeholder(value, lower) {
  lower = tolower(value)
  return value == "" ||
    value ~ /^[$][A-Za-z_][A-Za-z0-9_]*$/ ||
    value ~ /^[$][{][A-Za-z_][A-Za-z0-9_:-]*[}]$/ ||
    value ~ /^[A-Z0-9_]+$/ ||
    lower ~ /^(dummy|example|placeholder|changeme|todo|null|none|redacted|<redacted>)$/
}
function is_sensitive_key(key) {
  if (key ~ /(tokenizer|tiktoken)/) {
    return 0
  }
  return key ~ /(^|[_.-])(access[_-]?key|api[_-]?key|secret|token|cookie|auth|authorization|password|private[_-]?key)([_.-]|$)/
}
function is_code_expression(value, lower, bracket) {
  lower = tolower(value)
  bracket = index(value, "[")
  return value ~ /^[$][(]/ ||
    value ~ /^[$][{]?[A-Za-z_]/ ||
    value ~ /^[$][0-9@*#?!-]/ ||
    (substr(value, 1, 2) == "${" && substr(value, length(value), 1) == "}") ||
    value ~ /[$][{][A-Za-z_][A-Za-z0-9_:-]*[}]/ ||
    value ~ /^[A-Za-z_][A-Za-z0-9_.]*[[:space:]]*[(]/ ||
    value ~ /^[A-Za-z_][A-Za-z0-9_]*[.][A-Za-z_][A-Za-z0-9_]*$/ ||
    (bracket > 1 && substr(value, bracket + 1, length(value) - bracket - 1) != "" &&
      substr(value, 1, bracket - 1) ~ /^[A-Za-z_][A-Za-z0-9_]*$/ &&
      substr(value, length(value), 1) == "]") ||
    substr(value, 1, 1) == "/" ||
    substr(value, 1, 2) == "./" ||
    substr(value, 1, 3) == "../" ||
    substr(value, 1, 2) == "~/" ||
    value ~ /^[0-9]+([.][0-9]+)?$/ ||
    lower ~ /^(true|false)$/
}
function redact_assignment(prefix, content, head, tail, spacing, quote, comma) {
  head = substr(content, 1, RSTART)
  tail = substr(content, RSTART + 1)
  spacing = tail
  sub(/[^[:space:]].*$/, "", spacing)
  sub(/^[[:space:]]+/, "", tail)
  quote = substr(tail, 1, 1)
  if (quote != "\"" && quote != sprintf("%c", 39) && quote != "`") {
    quote = ""
  }
  comma = (tail ~ /,[[:space:]]*$/ ? "," : "")
  return prefix head spacing quote "<redacted>" quote comma
}
{
  original = $0
  prefix = ""
  content = original
  source_line = FNR
  if (mode == "scan" && format == "diff") {
    if (content ~ /^[+][+][+] /) {
      source_name = substr(content, 5)
      next
    }
    if (content ~ /^@@ /) {
      inside_private_key = 0
      diff_hunk = 0
      if (match(content, /[+][0-9]+/)) {
        diff_line = substr(content, RSTART + 1, RLENGTH - 1) + 0
        diff_hunk = 1
      }
      next
    }
    if (!diff_hunk) {
      next
    }
    if (substr(content, 1, 1) == "+") {
      source_line = diff_line
      diff_line++
      content = substr(content, 2)
    } else if (substr(content, 1, 1) == " ") {
      diff_line++
      next
    } else {
      next
    }
  }
  if (mode == "redact" && format == "diff" && content ~ /^[+ -]/ && content !~ /^-----(BEGIN|END) / && content !~ /^(---|[+][+][+])[[:space:]]/) {
    prefix = substr(content, 1, 1)
    content = substr(content, 2)
  }

  if (inside_private_key) {
    if (content ~ /-----END [A-Z ]*PRIVATE KEY-----/) {
      inside_private_key = 0
    }
    next
  }
  if (content ~ /-----BEGIN [A-Z ]*PRIVATE KEY-----/) {
    if (mode == "scan") {
      printf "%s:%s: <redacted private key block>\n", source_name, source_line
    } else {
      print prefix "<redacted private key block>"
    }
    inside_private_key = 1
    next
  }

  separator_found = match(content, /[=:]/)
  sensitive = 0
  if (separator_found) {
    key = assignment_key(content)
    value = assignment_value(content)
    sensitive = is_sensitive_key(key) && !is_placeholder(value) &&
      (mode != "scan" || !is_code_expression(value))
  }

  if (sensitive) {
    redacted = redact_assignment(prefix, content)
    if (mode == "scan") {
      printf "%s:%s: %s\n", source_name, source_line, redacted
    } else {
      print redacted
    }
  } else if (mode == "redact") {
    print original
  }
}
'

agent_sensitive_redact_file() {
  [[ "$#" -ge 2 && "$#" -le 3 ]] || {
    printf 'agent_sensitive_redact_file expects input/output paths and optional text|diff format.\n' >&2
    return 2
  }
  [[ "$1" != "$2" ]] || {
    printf 'Sensitive-output redaction requires different input and output paths.\n' >&2
    return 2
  }
  local format="${3:-text}"
  case "$format" in
    text|diff) ;;
    *)
      printf 'Unknown sensitive-output format: %s\n' "$format" >&2
      return 2
      ;;
  esac
  LC_ALL=C awk -v mode=redact -v format="$format" "$_AGENT_SENSITIVE_OUTPUT_AWK" "$1" > "$2"
}

agent_sensitive_scan_file() {
  [[ "$#" -ge 1 && "$#" -le 2 ]] || {
    printf 'agent_sensitive_scan_file expects an input path and optional text|diff format.\n' >&2
    return 2
  }
  local format="${2:-text}"
  case "$format" in
    text|diff) ;;
    *)
      printf 'Unknown sensitive-output format: %s\n' "$format" >&2
      return 2
      ;;
  esac
  LC_ALL=C awk -v mode=scan -v format="$format" -v source_name="$1" \
    "$_AGENT_SENSITIVE_OUTPUT_AWK" "$1"
}
