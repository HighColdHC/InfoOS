#!/bin/bash
# Shared, non-secret configuration. Source this file from the launcher/installers.
INFOOS_AGENT_CONFIG_FILE=${INFOOS_AGENT_CONFIG_FILE:-${INFOOS_AGENT_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/infoos}/agent.env}
INFOOS_AGENT_CONFIG_KEYS=(
  INFOOS_AGENT_CONFIG_DIR INFOOS_KNOWLEDGE_INSTALL_DIR INFOOS_KNOWLEDGE_LOG_DIR
  INFOOS_AGENT_ALERT_DIR INFOOS_MCP_URL INFOOS_MCP_TOKEN_FILE INFOOS_AGENT_MODEL
  INFOOS_AGENT_EFFORT INFOOS_AGENT_SERVICE_TIER INFOOS_AGENT_PATH INFOOS_AGENT_CODEX_BIN
  INFOOS_KNOWLEDGE_SINCE INFOOS_KNOWLEDGE_TIMEZONE INFOOS_KNOWLEDGE_HOURS
  INFOOS_KNOWLEDGE_MINUTE INFOOS_KNOWLEDGE_TIMEOUT_SEC INFOOS_AGENT_RETRY_DELAY_SEC
  INFOOS_LAUNCHD_DIR INFOOS_SYSTEMD_USER_DIR
)
# Environment overrides the trusted local shell file; never snapshot Token values.
_config_env_keys=() _config_env_values=()
for _key in "${INFOOS_AGENT_CONFIG_KEYS[@]}"; do
  if declare -p "$_key" >/dev/null 2>&1; then
    _config_env_keys+=("$_key"); _config_env_values+=("${!_key}")
  fi
done
if [ -f "$INFOOS_AGENT_CONFIG_FILE" ]; then
  source "$INFOOS_AGENT_CONFIG_FILE"
fi
for ((_i=0; _i<${#_config_env_keys[@]}; _i++)); do
  printf -v "${_config_env_keys[_i]}" '%s' "${_config_env_values[_i]}"
done
unset _config_env_keys _config_env_values _key _i
INFOOS_AGENT_CONFIG_DIR=${INFOOS_AGENT_CONFIG_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/infoos}

if [ "$(uname -s)" = Darwin ]; then
  _log_default="$HOME/Library/Logs/infoos-knowledge"
  _alert_default="$HOME/Library/Logs/infoos-agent-alerts"
  _path_default="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
else
  _log_default="${XDG_STATE_HOME:-$HOME/.local/state}/infoos/knowledge"
  _alert_default="${XDG_STATE_HOME:-$HOME/.local/state}/infoos/agent-alerts"
  _path_default="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin"
fi
INFOOS_KNOWLEDGE_INSTALL_DIR=${INFOOS_KNOWLEDGE_INSTALL_DIR:-$INFOOS_AGENT_CONFIG_DIR/knowledge}
INFOOS_KNOWLEDGE_LOG_DIR=${INFOOS_KNOWLEDGE_LOG_DIR:-$_log_default}
INFOOS_AGENT_ALERT_DIR=${INFOOS_AGENT_ALERT_DIR:-$_alert_default}
INFOOS_AGENT_PATH=${INFOOS_AGENT_PATH:-$_path_default}
INFOOS_MCP_URL=${INFOOS_MCP_URL:-}
INFOOS_MCP_TOKEN_FILE=${INFOOS_MCP_TOKEN_FILE:-$INFOOS_AGENT_CONFIG_DIR/mcp-agent.token}
INFOOS_AGENT_MODEL=${INFOOS_AGENT_MODEL:-}
INFOOS_AGENT_EFFORT=${INFOOS_AGENT_EFFORT:-medium}
INFOOS_AGENT_SERVICE_TIER=${INFOOS_AGENT_SERVICE_TIER:-default}
INFOOS_AGENT_CODEX_BIN=${INFOOS_AGENT_CODEX_BIN:-codex}
INFOOS_KNOWLEDGE_SINCE=${INFOOS_KNOWLEDGE_SINCE:-}
INFOOS_KNOWLEDGE_TIMEZONE=${INFOOS_KNOWLEDGE_TIMEZONE:-UTC}
INFOOS_KNOWLEDGE_HOURS=${INFOOS_KNOWLEDGE_HOURS:-06}
INFOOS_KNOWLEDGE_MINUTE=${INFOOS_KNOWLEDGE_MINUTE:-30}
INFOOS_KNOWLEDGE_TIMEOUT_SEC=${INFOOS_KNOWLEDGE_TIMEOUT_SEC:-3300}
INFOOS_AGENT_RETRY_DELAY_SEC=${INFOOS_AGENT_RETRY_DELAY_SEC:-300}
INFOOS_LAUNCHD_DIR=${INFOOS_LAUNCHD_DIR:-$HOME/Library/LaunchAgents}
INFOOS_SYSTEMD_USER_DIR=${INFOOS_SYSTEMD_USER_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}
for _key in "${INFOOS_AGENT_CONFIG_KEYS[@]}"; do export "$_key"; done
export PATH="$INFOOS_AGENT_PATH"
unset _key _log_default _alert_default _path_default

knowledge_validate_config() {
  if ! [[ "$INFOOS_KNOWLEDGE_SINCE" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$ ]]; then
    echo 'Set INFOOS_KNOWLEDGE_SINCE (or --since) to YYYY-MM-DDTHH:MM:SSZ' >&2; return 2
  fi
  if ! [[ "$INFOOS_MCP_URL" =~ ^https?://[^[:space:]]+$ ]]; then
    echo 'Set INFOOS_MCP_URL to the target /mcp URL' >&2; return 2
  fi
  if ! [[ "$INFOOS_KNOWLEDGE_TIMEZONE" =~ ^[A-Za-z0-9_+/-]+$ ]] ||
      [ ! -f "/usr/share/zoneinfo/$INFOOS_KNOWLEDGE_TIMEZONE" ]; then
    echo 'INFOOS_KNOWLEDGE_TIMEZONE must be an installed IANA timezone' >&2; return 2
  fi
  local hour
  if [ -z "$INFOOS_KNOWLEDGE_HOURS" ]; then echo 'Configure at least one run hour' >&2; return 2; fi
  for hour in $INFOOS_KNOWLEDGE_HOURS; do
    if ! [[ "$hour" =~ ^([01][0-9]|2[0-3])$ ]]; then
      echo 'INFOOS_KNOWLEDGE_HOURS requires space-separated HH values (00..23)' >&2; return 2
    fi
  done
  if ! [[ "$INFOOS_KNOWLEDGE_MINUTE" =~ ^[0-5][0-9]$ ]] ||
      ! [[ "$INFOOS_KNOWLEDGE_TIMEOUT_SEC" =~ ^[1-9][0-9]*$ ]] ||
      ! [[ "$INFOOS_AGENT_RETRY_DELAY_SEC" =~ ^(0|[1-9][0-9]*)$ ]]; then
    echo 'Invalid minute, timeout or retry delay' >&2; return 2
  fi
}

epoch_utc() {
  if [ "$(uname -s)" = Darwin ]; then
    date -u -r "$1" +%Y-%m-%dT%H:%M:%SZ
  else
    date -u -d "@$1" +%Y-%m-%dT%H:%M:%SZ
  fi
}

toml_string() {
  local value=${1//\\/\\\\}
  value=${value//\"/\\\"}
  printf '"%s"' "$value"
}
