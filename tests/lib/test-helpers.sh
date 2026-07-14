# Shared assertions and execution helper for Agent Rails test suites.

git_commit() {
  local repo="$1"
  local message="$2"
  git -C "$repo" -c user.name=Agent -c user.email=agent@example.com commit -q -m "$message"
}

assert_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "$haystack" != *"$needle"* ]]; then
    printf 'Expected output to contain: %s\n' "$needle" >&2
    printf 'Actual output:\n%s\n' "$haystack" >&2
    exit 1
  fi
}

assert_not_contains() {
  local haystack="$1"
  local needle="$2"
  if [[ "$haystack" == *"$needle"* ]]; then
    printf 'Expected output not to contain: %s\n' "$needle" >&2
    printf 'Actual output:\n%s\n' "$haystack" >&2
    exit 1
  fi
}

assert_file_contains() {
  local path="$1"
  local needle="$2"
  if ! grep -Fq -- "$needle" "$path"; then
    printf 'Expected %s to contain: %s\n' "$path" "$needle" >&2
    printf 'Actual file:\n' >&2
    sed -n '1,160p' "$path" >&2
    exit 1
  fi
}

assert_file_not_contains() {
  local path="$1"
  local needle="$2"
  if grep -Fq -- "$needle" "$path"; then
    printf 'Expected %s not to contain: %s\n' "$path" "$needle" >&2
    printf 'Actual file:\n' >&2
    sed -n '1,160p' "$path" >&2
    exit 1
  fi
}

assert_file_not_exists() {
  local path="$1"
  if [[ -e "$path" ]]; then
    printf 'Expected %s not to exist.\n' "$path" >&2
    exit 1
  fi
}

assert_file_exists() {
  local path="$1"
  if [[ ! -e "$path" ]]; then
    printf 'Expected %s to exist.\n' "$path" >&2
    exit 1
  fi
}

run_test() {
  local test_function="$1"
  local label="$2"
  "$test_function"
  printf 'ok - %s\n' "$label"
}
