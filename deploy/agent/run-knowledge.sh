#!/bin/bash
# One bounded run. Scheduling/installation is a separate operation.
set -euo pipefail
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/agent-config.sh"
if [ "$#" -gt 1 ] || { [ "$#" -eq 1 ] && [ "$1" != --now ]; }; then
  echo 'Usage: bash run-knowledge.sh [--now]' >&2; exit 2
fi
knowledge_validate_config
if [ "${1:-}" != --now ]; then
  HOUR=$(TZ="$INFOOS_KNOWLEDGE_TIMEZONE" date +%H)
  MINUTE=$(TZ="$INFOOS_KNOWLEDGE_TIMEZONE" date +%M)
  if [[ " $INFOOS_KNOWLEDGE_HOURS " != *" $HOUR "* ]] || [ "$MINUTE" != "$INFOOS_KNOWLEDGE_MINUTE" ]; then exit 0; fi
fi
mkdir -p "$INFOOS_KNOWLEDGE_LOG_DIR" "$INFOOS_AGENT_CONFIG_DIR"
LOCK="$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -d "$LOCK" ]; then echo 'Another knowledge run holds the lock'; exit 0; fi
  echo 'Cannot create knowledge lock' >&2; exit 2
fi
trap 'rmdir "$LOCK"' EXIT
# Only the child agent receives the Token; installers never read it.
if [ -z "${INFOOS_MCP_TOKEN:-}" ]; then
  INFOOS_MCP_TOKEN=$(cat "$INFOOS_MCP_TOKEN_FILE") || exit 2
fi
export INFOOS_MCP_TOKEN
STARTED=$(date +%s)
DEADLINE_EPOCH=$((STARTED + INFOOS_KNOWLEDGE_TIMEOUT_SEC))
UNTIL=$(epoch_utc "$STARTED")
DEADLINE=$(epoch_utc "$DEADLINE_EPOCH")
PROMPT=$(< "$SCRIPT_DIR/knowledge-prompt.md")
PROMPT=${PROMPT//\{SINCE\}/$INFOOS_KNOWLEDGE_SINCE}
PROMPT=${PROMPT//\{UNTIL\}/$UNTIL}
PROMPT=${PROMPT//\{DEADLINE\}/$DEADLINE}
PROMPT=${PROMPT//\{EVALUATOR\}/codex:${INFOOS_AGENT_MODEL:-default}}
CODEX_ARGS=(exec --skip-git-repo-check -s read-only)
if [ -n "$INFOOS_AGENT_MODEL" ]; then CODEX_ARGS+=(-m "$INFOOS_AGENT_MODEL"); fi
CODEX_ARGS+=(
  -c "model_reasoning_effort=$(toml_string "$INFOOS_AGENT_EFFORT")"
  -c "service_tier=$(toml_string "$INFOOS_AGENT_SERVICE_TIER")"
  -c 'approval_policy="never"'
  -c "mcp_servers.infoos.url=$(toml_string "$INFOOS_MCP_URL")"
  -c 'mcp_servers.infoos.bearer_token_env_var="INFOOS_MCP_TOKEN"'
  -c 'mcp_servers.infoos.required=true'
  -c 'mcp_servers.infoos.startup_timeout_sec=60'
  -c 'mcp_servers.infoos.tool_timeout_sec=180'
  -c 'mcp_servers.infoos.default_tools_approval_mode="approve"'
  -c 'mcp_servers.infoos.enabled_tools=["get_profile","get_evaluation_rubric","list_new_cards","get_cards","get_card","submit_evaluations","get_knowledge_schema","list_extraction_candidates","search_knowledge_cards","submit_knowledge_extractions","get_knowledge_card"]'
)
cd "$INFOOS_KNOWLEDGE_LOG_DIR"
ALERT_ARGS=(knowledge)
RESULT=0
for ATTEMPT in 1 2; do
  LOG="$INFOOS_KNOWLEDGE_LOG_DIR/$(date -u +%Y-%m-%dT%H%M%SZ)-$$-$ATTEMPT.log"
  printf 'start since=%s until=%s deadline=%s attempt=%s\n' "$INFOOS_KNOWLEDGE_SINCE" "$UNTIL" "$DEADLINE" "$ATTEMPT" > "$LOG"
  REMAINING=$((DEADLINE_EPOCH - $(date +%s)))
  if [ "$REMAINING" -le 0 ]; then RESULT=124; printf 'exit=124\n' >> "$LOG"; break; fi
  RESULT=0
  if command -v timeout >/dev/null 2>&1; then
    timeout --kill-after=30s "${REMAINING}s" "$INFOOS_AGENT_CODEX_BIN" "${CODEX_ARGS[@]}" "$PROMPT" < /dev/null >> "$LOG" 2>&1 || RESULT=$?
  else
    perl -e 'alarm shift; exec @ARGV' "$REMAINING" "$INFOOS_AGENT_CODEX_BIN" "${CODEX_ARGS[@]}" "$PROMPT" < /dev/null >> "$LOG" 2>&1 || RESULT=$?
  fi
  printf 'exit=%s\n' "$RESULT" >> "$LOG"
  if [ "$ATTEMPT" -eq 1 ]; then
    VERDICT=$(bash "$SCRIPT_DIR/agent-run-alert.sh" --verdict-only knowledge "$RESULT" "$LOG")
    if [ "${VERDICT%%$'\t'*}" = fail ] && ! grep -q '^mcp: infoos/submit_' "$LOG" && [ "$(( $(date +%s) - STARTED ))" -lt 600 ]; then
      REMAINING=$((DEADLINE_EPOCH - $(date +%s)))
      if [ "$REMAINING" -gt "$INFOOS_AGENT_RETRY_DELAY_SEC" ]; then
        ALERT_ARGS=(--retry-summary "${VERDICT#*$'\t'}" knowledge)
        sleep "$INFOOS_AGENT_RETRY_DELAY_SEC"
        continue
      fi
    fi
  fi
  break
done
bash "$SCRIPT_DIR/agent-run-alert.sh" "${ALERT_ARGS[@]}" "$RESULT" "$LOG" || :
exit "$RESULT"
