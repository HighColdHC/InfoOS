#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${INFOOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
MAX_ATTEMPTS="${INFOOS_MAX_ATTEMPTS:-2}"
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

python -m backend.workers.scheduler.cycle \
  --base-dir "." \
  --max-attempts "$MAX_ATTEMPTS"
