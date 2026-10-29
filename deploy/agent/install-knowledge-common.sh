#!/bin/bash
# Shared installer functions; never read credentials or install from a dirty tree.
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/agent-config.sh"
COPY_ONLY=0
DISABLE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --copy-only) COPY_ONLY=1; shift ;;
    --disable) DISABLE=1; shift ;;
    --since)
      if [ "$#" -lt 2 ]; then echo '--since requires a UTC timestamp' >&2; exit 2; fi
      INFOOS_KNOWLEDGE_SINCE=$2; export INFOOS_KNOWLEDGE_SINCE; shift 2 ;;
    *) echo 'Usage: installer [--since YYYY-MM-DDTHH:MM:SSZ] [--copy-only | --disable]' >&2; exit 2 ;;
  esac
done
if [ "$COPY_ONLY" -eq 1 ] && [ "$DISABLE" -eq 1 ]; then echo 'Conflicting modes' >&2; exit 2; fi

install_knowledge_snapshot() {
  knowledge_validate_config
  local project_root revision key
  project_root=$(cd -- "$SCRIPT_DIR/../.." && pwd)
  if [ -n "$(git -C "$project_root" status --porcelain)" ]; then
    echo 'Refusing to install from a dirty checkout.' >&2; return 65
  fi
  revision=$(git -C "$project_root" rev-parse --verify 'HEAD^{commit}')
  install -d -m 0700 "$INFOOS_KNOWLEDGE_INSTALL_DIR" "$INFOOS_KNOWLEDGE_LOG_DIR"
  install -m 0700 "$SCRIPT_DIR/run-knowledge.sh" "$SCRIPT_DIR/agent-run-alert.sh" "$SCRIPT_DIR/agent-config.sh" "$INFOOS_KNOWLEDGE_INSTALL_DIR/"
  install -m 0600 "$SCRIPT_DIR/knowledge-prompt.md" "$INFOOS_KNOWLEDGE_INSTALL_DIR/"
  printf '%s\n' "$revision" > "$INFOOS_KNOWLEDGE_INSTALL_DIR/source-revision"
  # A private non-secret snapshot, consumed by both schedulers. Environment wins.
  for key in "${INFOOS_AGENT_CONFIG_KEYS[@]}"; do
    printf '%s=%q\n' "$key" "${!key}"
  done > "$INFOOS_KNOWLEDGE_INSTALL_DIR/installed.env"
  chmod 0600 "$INFOOS_KNOWLEDGE_INSTALL_DIR/source-revision" "$INFOOS_KNOWLEDGE_INSTALL_DIR/installed.env"
  printf 'Installed %s to %s\n' "$revision" "$INFOOS_KNOWLEDGE_INSTALL_DIR"
}
