#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${INFOOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
export INFOOS_DATA_HOME="${INFOOS_DATA_HOME:-$HOME/Library/Application Support/InfoOS}"
POLL_SECONDS="${INFOOS_TASK_WORKER_POLL_SECONDS:-1}"
BATCH_LIMIT="${INFOOS_TASK_WORKER_BATCH_LIMIT:-50}"
MAX_WORKERS="${INFOOS_TASK_WORKER_MAX_WORKERS:-}"

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

worker_args=(
  --base-dir "."
  --poll-seconds "$POLL_SECONDS"
  --batch-limit "$BATCH_LIMIT"
)
if [[ -n "$MAX_WORKERS" ]]; then
  worker_args+=(--max-workers "$MAX_WORKERS")
fi

exec python -m backend.workers.task_worker "${worker_args[@]}"
