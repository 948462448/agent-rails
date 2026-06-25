#!/usr/bin/env bash
# Claude Code / Codex SessionStart hook for Agent Rails.
#
# It stays quiet unless the current project already has an Agent Rails adapter
# marker. The project-local adapter keeps the exact profile and commands; this
# hook only lifts the trigger matrix into session-start context.

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENT_RAILS_HOME="${AGENT_RAILS_HOME:-$(cd "$script_dir/.." && pwd)}"
AGENT_RAILS_BIN="$AGENT_RAILS_HOME/bin/agent-rails"

json_escape() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//$'\r'/\\r}"
  value="${value//$'\n'/\\n}"
  value="${value//$'\t'/\\t}"
  printf '%s' "$value"
}

emit_context() {
  local context="$1"

  if [[ -n "${PLUGIN_DATA:-}" ]]; then
    local escaped_context
    escaped_context="$(json_escape "$context")"
    printf '{"systemMessage":"AGENT RAILS:ON","hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":"%s"}}' "$escaped_context"
  else
    printf '%s\n' "$context"
  fi
}

project_root="${CLAUDE_PROJECT_DIR:-}"
if [[ -z "$project_root" || ! -d "$project_root" ]]; then
  if git_root="$(git rev-parse --show-toplevel 2>/dev/null)"; then
    project_root="$git_root"
  else
    project_root="$PWD"
  fi
fi
project_root="$(cd "$project_root" 2>/dev/null && pwd || printf '%s' "$project_root")"

has_agent_rails_marker=0
for marker_path in \
  "$project_root/CLAUDE.local.md" \
  "$project_root/CLAUDE.md"; do
  if [[ -f "$marker_path" ]] && grep -Fq 'agent-rails:start' "$marker_path"; then
    has_agent_rails_marker=1
    break
  fi
done
if [[ "$has_agent_rails_marker" -ne 1 && -f "$project_root/.claude/AGENT_RAILS.md" ]] \
  && grep -Fq 'Visible session marker protocol' "$project_root/.claude/AGENT_RAILS.md"; then
  has_agent_rails_marker=1
fi
if [[ "$has_agent_rails_marker" -ne 1 && -f "$project_root/.codex-plugin/plugin.json" ]] \
  && grep -Fq '"name": "agent-rails"' "$project_root/.codex-plugin/plugin.json"; then
  has_agent_rails_marker=1
fi

if [[ "$has_agent_rails_marker" -ne 1 ]]; then
  exit 0
fi

profile_path=""
for source_path in \
  "$project_root/.claude/AGENT_RAILS.md" \
  "$project_root/CLAUDE.local.md" \
  "$project_root/CLAUDE.md"; do
  if [[ -f "$source_path" ]]; then
    profile_path="$(sed -n -E 's/.*--profile "([^"]+)".*/\1/p' "$source_path" | sed -n '1p')"
    [[ -n "$profile_path" ]] && break
  fi
done

profile_arg=""
if [[ -n "$profile_path" ]]; then
  profile_arg=" --profile \"$profile_path\""
fi

context="$(cat <<EOF
AGENT RAILS SESSION HOOK ACTIVE

This project has a local Agent Rails adapter. Treat Agent Rails as active from session start, before broad repository reads or file edits.

Visible marker protocol:
- Pack/lite: first tell the user the AGENT RAILS: ON marker printed by the pack command.
- Check-only: first tell the user AGENT RAILS: CHECK-ONLY (reason=<reason>).
- Intentional skip: first tell the user AGENT RAILS: SKIPPED (reason=<reason>).

Trigger matrix:
- Deep pack: 2+ subprojects, APIs/contracts/schemas/data models, ADRs/handbooks, migrations/refactors, or ambiguous product decisions.
- Lite pack: POCs, quick prototypes, version/Dockerfile/OSS/deploy prep, codegen freshness checks, or continuation from an existing handbook.
- Check-only: deploy/release/upload workflows that consume the current branch, and final verification planning.
- Skip: pure status queries, simple command output, or fixed operations with no repo change and no branch-consumption risk.

Commands:
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
$AGENT_RAILS_BIN pack --project "\$project_root"$profile_arg "<goal>"
$AGENT_RAILS_BIN pack --project "\$project_root"$profile_arg --pack-mode lite "<goal>"
$AGENT_RAILS_BIN check --project "\$project_root"$profile_arg --print-only

Read the generated Task Pack before continuing. The project-local Agent Rails adapter remains the source for exact project details.
EOF
)"

emit_context "$context"
