#!/usr/bin/env bash
# Print local shell setup guidance for Agent Rails.

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: agent-rails init [--shell zsh|bash|fish] [--project PATH] [--profile PATH]

Prints a copy-paste setup guide for making `agent-rails` available as a normal
local command. This command does not edit shell rc files.
USAGE
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

shell_name="$(basename "${SHELL:-zsh}")"
project_path="${AGENT_RAILS_PROJECT:-$PWD}"
profile_path="${AGENT_RAILS_PROFILE:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --shell)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      shell_name="$2"
      shift 2
      ;;
    --project)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      project_path="$2"
      shift 2
      ;;
    --profile)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      profile_path="$2"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$profile_path" ]]; then
  project_name="$(basename "$project_path")"
  profile_path="$AGENT_RAILS_CONFIG_HOME/profiles/projects/${project_name}.profile"
fi

case "$shell_name" in
  zsh)
    rc_file="$HOME/.zshrc"
    path_line='export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
    alias_line='alias ar="agent-rails"'
    reload_command='source ~/.zshrc'
    ;;
  bash)
    rc_file="$HOME/.bashrc"
    path_line='export PATH="$AGENT_RAILS_HOME/bin:$PATH"'
    alias_line='alias ar="agent-rails"'
    reload_command='source ~/.bashrc'
    ;;
  fish)
    rc_file="$HOME/.config/fish/config.fish"
    path_line='fish_add_path "$AGENT_RAILS_HOME/bin"'
    alias_line='alias ar="agent-rails"'
    reload_command='source ~/.config/fish/config.fish'
    ;;
  *)
    printf 'Unsupported shell: %s\n' "$shell_name" >&2
    printf 'Supported shells: zsh, bash, fish\n' >&2
    exit 2
    ;;
esac

printf 'Agent Rails Init\n\n'
printf '1. Add this block to %s:\n\n' "$rc_file"
if [[ "$shell_name" == "fish" ]]; then
  cat <<EOF
# Agent Rails
set -gx AGENT_RAILS_HOME "$AGENT_RAILS_HOME"
$path_line
$alias_line
set -gx AGENT_RAILS_PROJECT "$project_path"
set -gx AGENT_RAILS_PROFILE "$profile_path"
EOF
else
  cat <<EOF
# Agent Rails
export AGENT_RAILS_HOME="$AGENT_RAILS_HOME"
$path_line
$alias_line
export AGENT_RAILS_PROJECT="$project_path"
export AGENT_RAILS_PROFILE="$profile_path"
EOF
fi

printf '\n2. Reload your shell:\n\n'
printf '%s\n' "$reload_command"

printf '\n3. Verify:\n\n'
cat <<'EOF'
agent-rails --help
agent-rails home
ar doctor --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE"
EOF

printf '\n4. Daily usage after init:\n\n'
cat <<'EOF'
ar run --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE" --model qwen3.7-max --pack-mode deep "本次任务目标"
ar run --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE" --model qwen3.7-max --pack-mode lite "POC / deploy prep 目标"
ar claude install --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE" --mode local
ar check --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE" --print-only
EOF
