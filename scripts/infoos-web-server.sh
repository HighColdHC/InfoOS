#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${INFOOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
HOST="${INFOOS_WEB_HOST:-127.0.0.1}"
PORT="${INFOOS_WEB_PORT:-8000}"
export INFOOS_DATA_HOME="${INFOOS_DATA_HOME:-$HOME/Library/Application Support/InfoOS}"

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

if [[ -z "${INFOOS_OPENCLI_EXECUTABLE:-}" ]]; then
  for candidate in \
    "$HOME/.nvm/versions/node/v24.15.0/bin/opencli" \
    "$HOME/.nvm/current/bin/opencli" \
    "/opt/homebrew/bin/opencli" \
    "/usr/local/bin/opencli"; do
    if [[ -x "$candidate" ]]; then
      INFOOS_OPENCLI_EXECUTABLE="$candidate"
      break
    fi
  done
fi

if [[ -n "${INFOOS_OPENCLI_EXECUTABLE:-}" ]]; then
  export INFOOS_OPENCLI_EXECUTABLE
  export PATH="$(dirname "$INFOOS_OPENCLI_EXECUTABLE"):$PATH"
fi

cd "$REPO_DIR"

if [[ -f ".venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

# If a previous run is still bound to HOST:PORT, kill it before starting
# a new one. Without this, a stale process silently keeps the port
# (e.g. after a Ctrl-C that didn't propagate) and a second `make run-web`
# dies with "address already in use" while the user sees the OLD server
# (often the wrong service) responding to requests. The stale process
# is the same uvicorn binary, so killing it is safe. Restrict lsof to LISTEN
# sockets so browser clients connected to 127.0.0.1:8000 are never killed.
if command -v lsof >/dev/null 2>&1; then
  STALE_PIDS="$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "$STALE_PIDS" ]]; then
    echo "infoos-web-server: killing stale process(es) on $HOST:$PORT: $STALE_PIDS" >&2
    # shellcheck disable=SC2086
    kill $STALE_PIDS 2>/dev/null || true
    sleep 1
  fi
fi

exec python -m uvicorn backend.app.main:app --host "$HOST" --port "$PORT"
