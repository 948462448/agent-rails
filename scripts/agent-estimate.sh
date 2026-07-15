#!/usr/bin/env bash
# Estimate context size with Agent Rails model presets.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails estimate [--profile PATH] [--model NAME] [--tokenizer auto|char|tiktoken|command|huggingface] [--tokenizer-command CMD] [--tokenizer-path PATH] [--chars-per-token N] [--file PATH] [text...]

Examples:
  agent-rails estimate --model qwen3.7-max --file ~/.agent-rails/agent-context/project-task-pack.md
  agent-rails estimate --tokenizer tiktoken --file ~/.agent-rails/agent-context/project-task-pack.md
  agent-rails estimate --tokenizer-command 'my-token-counter "$AGENT_RAILS_TOKENIZER_INPUT"' --file pack.md

Use --tokenizer command for exact Qwen/GLM tokenizers when a local tokenizer command is available.
Without a tokenizer dependency, auto falls back to a character estimate.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-model-presets.sh
source "$AGENT_RAILS_HOME/scripts/agent-model-presets.sh"

profile_path="$AGENT_RAILS_HOME/profiles/default.profile"
model_arg=""
chars_per_token_arg=""
tokenizer_arg=""
tokenizer_command_arg=""
tokenizer_path_arg=""
input_file=""
text_parts=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path="$2"
      shift 2
      ;;
    --model)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      model_arg="$2"
      shift 2
      ;;
    --chars-per-token)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      chars_per_token_arg="$2"
      shift 2
      ;;
    --tokenizer)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_arg="$2"
      shift 2
      ;;
    --tokenizer-command)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_command_arg="$2"
      shift 2
      ;;
    --tokenizer-path)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      tokenizer_path_arg="$2"
      shift 2
      ;;
    --file)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      input_file="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      text_parts+=("$1")
      shift
      ;;
  esac
done

if [[ -f "$profile_path" ]]; then
  # shellcheck source=/dev/null
  source "$profile_path"
fi

AGENT_RAILS_MODEL="${model_arg:-${AGENT_RAILS_MODEL:-generic}}"
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="${chars_per_token_arg:-${AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE:-2}}"
AGENT_RAILS_TOKENIZER="${tokenizer_arg:-${AGENT_RAILS_TOKENIZER:-auto}}"
AGENT_RAILS_TOKENIZER_CMD="${tokenizer_command_arg:-${AGENT_RAILS_TOKENIZER_CMD:-}}"
AGENT_RAILS_TOKENIZER_PATH="${tokenizer_path_arg:-${AGENT_RAILS_TOKENIZER_PATH:-}}"
AGENT_RAILS_TIKTOKEN_ENCODING="${AGENT_RAILS_TIKTOKEN_ENCODING:-cl100k_base}"

normalize_positive_int() {
  local value="$1"
  local default_value="$2"
  if [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]]; then
    printf '%s\n' "$value"
  else
    printf '%s\n' "$default_value"
  fi
}

percent_x100() {
  local numerator="$1"
  local denominator="$2"
  if [[ -z "$denominator" || "$denominator" -eq 0 ]]; then
    printf ''
    return 0
  fi
  printf '%s\n' $((numerator * 10000 / denominator))
}

format_percent_x100() {
  local value="$1"
  [[ -n "$value" ]] || return 0
  printf '%s.%02s%%\n' "$((value / 100))" "$((value % 100))"
}

chars_for_file() {
  LC_ALL=en_US.UTF-8 wc -m < "$1" | tr -d '[:space:]'
}

bytes_for_file() {
  wc -c < "$1" | tr -d '[:space:]'
}

token_count_with_char_estimate() {
  local chars="$1"
  printf '%s\n' "$(( (chars + AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE - 1) / AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE ))"
}

token_count_with_command() {
  local path="$1"
  [[ -n "$AGENT_RAILS_TOKENIZER_CMD" ]] || return 1
  AGENT_RAILS_TOKENIZER_INPUT="$path" sh -c "$AGENT_RAILS_TOKENIZER_CMD"
}

token_count_with_tiktoken() {
  local path="$1"
  local encoding="$2"
  command -v python3 >/dev/null 2>&1 || return 1
  python3 - "$path" "$encoding" <<'PY'
import sys

path = sys.argv[1]
encoding_name = sys.argv[2]

try:
    import tiktoken
except Exception:
    sys.exit(3)

with open(path, "r", encoding="utf-8", errors="replace") as handle:
    text = handle.read()

encoding = tiktoken.get_encoding(encoding_name)
print(len(encoding.encode(text)))
PY
}

token_count_with_huggingface() {
  local path="$1"
  local tokenizer_path="$2"
  command -v python3 >/dev/null 2>&1 || return 1
  [[ -n "$tokenizer_path" ]] || return 1
  python3 - "$path" "$tokenizer_path" <<'PY'
import sys

input_path = sys.argv[1]
tokenizer_path = sys.argv[2]

try:
    from transformers import AutoTokenizer
except Exception:
    sys.exit(3)

with open(input_path, "r", encoding="utf-8", errors="replace") as handle:
    text = handle.read()

tokenizer = AutoTokenizer.from_pretrained(tokenizer_path, trust_remote_code=True)
print(len(tokenizer.encode(text, add_special_tokens=False)))
PY
}

looks_like_integer() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

count_tokens() {
  local path="$1"
  local chars="$2"
  local mode="$3"
  local raw_count=""

  case "$mode" in
    char)
      AGENT_RAILS_TOKENIZER_EFFECTIVE="char-estimate"
      AGENT_RAILS_TOKEN_COUNT="$(token_count_with_char_estimate "$chars")"
      return 0
      ;;
    command)
      if raw_count="$(token_count_with_command "$path" 2>/dev/null)" && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="command"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      printf 'Tokenizer command failed or did not print an integer.\n' >&2
      return 1
      ;;
    tiktoken)
      if raw_count="$(token_count_with_tiktoken "$path" "$AGENT_RAILS_TIKTOKEN_ENCODING" 2>/dev/null)" && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="tiktoken:$AGENT_RAILS_TIKTOKEN_ENCODING"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      printf 'tiktoken tokenizer unavailable. Install tiktoken or use --tokenizer char/command.\n' >&2
      return 1
      ;;
    huggingface|hf)
      if raw_count="$(token_count_with_huggingface "$path" "$AGENT_RAILS_TOKENIZER_PATH" 2>/dev/null)" && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="huggingface:$AGENT_RAILS_TOKENIZER_PATH"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      printf 'Hugging Face tokenizer unavailable. Set --tokenizer-path and install transformers.\n' >&2
      return 1
      ;;
    auto)
      if [[ -n "$AGENT_RAILS_TOKENIZER_PATH" ]] \
        && raw_count="$(token_count_with_huggingface "$path" "$AGENT_RAILS_TOKENIZER_PATH" 2>/dev/null)" \
        && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="huggingface:$AGENT_RAILS_TOKENIZER_PATH"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      if [[ -n "$AGENT_RAILS_TOKENIZER_CMD" ]] \
        && raw_count="$(token_count_with_command "$path" 2>/dev/null)" \
        && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="command"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      if raw_count="$(token_count_with_tiktoken "$path" "$AGENT_RAILS_TIKTOKEN_ENCODING" 2>/dev/null)" && looks_like_integer "$raw_count"; then
        AGENT_RAILS_TOKENIZER_EFFECTIVE="tiktoken:$AGENT_RAILS_TIKTOKEN_ENCODING"
        AGENT_RAILS_TOKEN_COUNT="$raw_count"
        return 0
      fi
      AGENT_RAILS_TOKENIZER_EFFECTIVE="char-estimate"
      AGENT_RAILS_TOKEN_COUNT="$(token_count_with_char_estimate "$chars")"
      return 0
      ;;
    *)
      printf 'Unknown tokenizer: %s\n' "$mode" >&2
      return 2
      ;;
  esac
}

AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE="$(normalize_positive_int "$AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE" 2)"
agent_model_preset_load "$AGENT_RAILS_MODEL"

tmp_input=""
cleanup() {
  [[ -n "$tmp_input" && -f "$tmp_input" ]] && rm -f "$tmp_input"
  return 0
}
trap cleanup EXIT

source_label=""
input_path=""
if [[ -n "$input_file" ]]; then
  if [[ ! -f "$input_file" ]]; then
    printf 'Input file not found: %s\n' "$input_file" >&2
    exit 2
  fi
  input_path="$input_file"
  source_label="file: $input_file"
  char_count="$(chars_for_file "$input_file")"
  byte_count="$(bytes_for_file "$input_file")"
else
  tmp_input="$(mktemp)"
  input_path="$tmp_input"
  if [[ "${#text_parts[@]}" -gt 0 ]]; then
    printf '%s' "${text_parts[*]}" > "$tmp_input"
    source_label="arguments"
  else
    cat > "$tmp_input"
    source_label="stdin"
  fi
  char_count="$(chars_for_file "$tmp_input")"
  byte_count="$(bytes_for_file "$tmp_input")"
fi

AGENT_RAILS_TOKENIZER_EFFECTIVE=""
AGENT_RAILS_TOKEN_COUNT=""
count_tokens "$input_path" "$char_count" "$AGENT_RAILS_TOKENIZER"
estimated_tokens="$AGENT_RAILS_TOKEN_COUNT"

printf 'Agent Rails Estimate\n\n'
printf 'Source: %s\n' "$source_label"
printf 'Characters: %s\n' "$char_count"
printf 'Bytes: %s\n' "$byte_count"
printf 'Tokenizer: %s\n' "$AGENT_RAILS_TOKENIZER_EFFECTIVE"
if [[ "$AGENT_RAILS_TOKENIZER_EFFECTIVE" == "char-estimate" ]]; then
  printf 'Chars/token estimate: %s\n' "$AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE"
fi
printf 'Estimated tokens: %s\n' "$estimated_tokens"
printf 'Model: %s' "$AGENT_RAILS_MODEL_CANONICAL"
if [[ "$AGENT_RAILS_MODEL_PRESET_FOUND" -eq 1 ]]; then
  printf ' (preset)\n'
  printf 'Context: %s tokens' "$AGENT_RAILS_MODEL_CONTEXT_TOKENS"
  usage_percent="$(percent_x100 "$estimated_tokens" "$AGENT_RAILS_MODEL_CONTEXT_TOKENS")"
  if [[ -n "$usage_percent" ]]; then
    printf ' (%s used)' "$(format_percent_x100 "$usage_percent")"
  fi
  printf '\n'
  printf 'Max input: %s tokens' "$AGENT_RAILS_MODEL_MAX_INPUT_TOKENS"
  usage_percent="$(percent_x100 "$estimated_tokens" "$AGENT_RAILS_MODEL_MAX_INPUT_TOKENS")"
  if [[ -n "$usage_percent" ]]; then
    printf ' (%s used)' "$(format_percent_x100 "$usage_percent")"
  fi
  printf '\n'
  if [[ -n "$AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS" ]]; then
    printf 'Max input in thinking mode: %s tokens\n' "$AGENT_RAILS_MODEL_MAX_INPUT_THINKING_TOKENS"
  fi
  printf 'Max output: %s tokens\n' "$AGENT_RAILS_MODEL_MAX_OUTPUT_TOKENS"
  if [[ -n "$AGENT_RAILS_MODEL_MAX_REASONING_TOKENS" ]]; then
    printf 'Max reasoning: %s tokens\n' "$AGENT_RAILS_MODEL_MAX_REASONING_TOKENS"
  fi
  if [[ -n "$AGENT_RAILS_MODEL_RPM" ]]; then
    printf 'RPM: %s\n' "$AGENT_RAILS_MODEL_RPM"
  fi
  if [[ -n "$AGENT_RAILS_MODEL_TPM" ]]; then
    printf 'TPM: %s\n' "$AGENT_RAILS_MODEL_TPM"
  fi
else
  printf ' (no preset)\n'
fi
