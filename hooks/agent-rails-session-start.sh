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
# shellcheck source=scripts/agent-paths.sh
source "$AGENT_RAILS_HOME/scripts/agent-paths.sh"
agent_rails_init_paths

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
if [[ "$has_agent_rails_marker" -ne 1 && -f "$project_root/.opencode/AGENT_RAILS.md" ]] \
  && grep -Fq 'Visible session marker protocol' "$project_root/.opencode/AGENT_RAILS.md"; then
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
  "$project_root/CLAUDE.local.md" \
  "$project_root/CLAUDE.md" \
  "$project_root/.claude/AGENT_RAILS.md" \
  "$project_root/.opencode/AGENT_RAILS.md"; do
  if [[ -f "$source_path" ]]; then
    profile_path="$(sed -n -E 's/.*--profile "([^"]+)".*/\1/p' "$source_path" | sed -n '1p')"
    [[ -n "$profile_path" ]] && break
  fi
done
if [[ -n "$profile_path" ]]; then
  profile_path="$(agent_rails_resolve_profile "$project_root" "$(basename "$project_root")" "$profile_path")"
fi

profile_arg=""
if [[ -n "$profile_path" ]]; then
  profile_arg=" --profile \"$profile_path\""
fi

context="$(cat <<EOF
AGENT RAILS SESSION HOOK ACTIVE

Local adapter active. Before broad reads/edits, choose the smallest useful path and show its marker.

Visible marker protocol:
- Pack/lite: relay the command's AGENT RAILS: ON marker.
- Check-only: say AGENT RAILS: CHECK-ONLY (reason=<reason>).
- Skip: say AGENT RAILS: SKIPPED (reason=<reason>).

Trigger matrix:
- Deep: cross-subproject, contract/schema/model, ADR, migration/refactor, ambiguous product work.
- Lite: POC, deploy prep, codegen check, focused continuation.
- Check-only: branch-consuming deploy/release/upload and final verification planning.
- Skip: read-only/fixed operations with no repo or branch-consumption risk.

Target scope:
- Session root: $project_root
- Same-repo worktree: pass its exact root to pack/check.
- Sibling/different repo: do not reuse this --profile; resolve the target's profile.
- After a target change, regenerate the pack and verify Current Git State.

Sensitive output:
- Base64 and URL encoding are not redaction.
- Project only decision fields from logs/DOM/tables/output; avoid auth-bearing context.
- Do not repeat exposed secrets; narrow reads and report the surface.

Commands:
ar="$AGENT_RAILS_BIN"
project_root="\$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
"\$ar" pack --project "\$project_root"$profile_arg "<goal>"
"\$ar" pack --project "\$project_root"$profile_arg --pack-mode lite "<goal>"
"\$ar" check --project "\$project_root"$profile_arg --print-only

Read the generated pack. The project adapter remains the source for exact details.
EOF
)"

emit_context "$context"
