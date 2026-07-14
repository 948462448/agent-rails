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
project_path="${AGENT_RAILS_PROJECT:-}"
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

if [[ -z "$profile_path" && -n "$project_path" ]]; then
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
printf '# Agent Rails\n'
if [[ "$shell_name" == "fish" ]]; then
  printf 'set -gx AGENT_RAILS_HOME "%s"\n' "$AGENT_RAILS_HOME"
else
  printf 'export AGENT_RAILS_HOME="%s"\n' "$AGENT_RAILS_HOME"
fi
printf '%s\n' "$path_line"
printf '%s\n' "$alias_line"
if [[ -n "$project_path" ]]; then
  if [[ "$shell_name" == "fish" ]]; then
    printf 'set -gx AGENT_RAILS_PROJECT "%s"\n' "$project_path"
  else
    printf 'export AGENT_RAILS_PROJECT="%s"\n' "$project_path"
  fi
fi
if [[ -n "$profile_path" ]]; then
  if [[ "$shell_name" == "fish" ]]; then
    printf 'set -gx AGENT_RAILS_PROFILE "%s"\n' "$profile_path"
  else
    printf 'export AGENT_RAILS_PROFILE="%s"\n' "$profile_path"
  fi
fi

printf '\n2. Reload your shell:\n\n'
printf '%s\n' "$reload_command"

printf '\n3. Verify:\n\n'
cat <<'EOF'
agent-rails --help
agent-rails home
EOF

if [[ -n "$project_path" && -n "$profile_path" ]]; then
  printf 'ar doctor --project "$AGENT_RAILS_PROJECT" --profile "$AGENT_RAILS_PROFILE"\n'
fi

printf '\n4. Connect a project:\n\n'
cat <<'EOF'
cd /path/to/project
agent-rails setup --tool claude  # or codex / opencode

# Restart the selected coding agent, then work normally.
# Before delivery:
agent-rails verify
EOF
