#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${INFOOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
EXPECTED_STATE_HOME="$HOME/.infoos-test"
EXPECTED_DATA_HOME="$EXPECTED_STATE_HOME/data"
EXPECTED_CONFIG_PATH="$EXPECTED_STATE_HOME/config.yaml"
EXPECTED_PID_FILE="$EXPECTED_STATE_HOME/runtime/test-env.pids"
EXPECTED_CHROME_PROFILE="$EXPECTED_STATE_HOME/chrome-profile"
EXPECTED_BROWSER_SESSION_JSON="$EXPECTED_STATE_HOME/browser-session.json"
BINDING_HELPER="$REPO_DIR/scripts/infoos-browser-binding.py"

require_test_path() {
  local variable_name="$1"
  local expected_value="$2"
  local actual_value="${!variable_name:-}"
  if [[ "$actual_value" != "$expected_value" ]]; then
    printf '[infoos-reliability-acceptance] ERROR: %s must equal %s; refusing default or alternate runtime\n' \
      "$variable_name" "$expected_value" >&2
    exit 2
  fi
}

require_test_path "INFOOS_STATE_HOME" "$EXPECTED_STATE_HOME"
require_test_path "INFOOS_DATA_HOME" "$EXPECTED_DATA_HOME"
require_test_path "INFOOS_CONFIG_PATH" "$EXPECTED_CONFIG_PATH"

cd "$REPO_DIR"

[[ -f "$EXPECTED_PID_FILE" ]] || {
  printf '[infoos-reliability-acceptance] ERROR: fixed test supervisor is not running; use scripts/run-infoos-test-env.sh\n' >&2
  exit 2
}

for label in supervisor backend frontend scheduler task-worker; do
  pid="$(awk -F= -v label="$label" '$1 == label {print $2; exit}' "$EXPECTED_PID_FILE")"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    printf '[infoos-reliability-acceptance] ERROR: %s process is not alive in the fixed supervisor\n' "$label" >&2
    exit 2
  fi
done

BROWSER_HEADLESS_DURING_FETCH="$(.venv/bin/python -c '
import sys, yaml
from pathlib import Path
payload = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
print("true" if payload.get("browser", {}).get("headless_during_fetch", True) else "false")
' "$EXPECTED_CONFIG_PATH")"

CHROME_RUNTIME_MODE="$(/bin/ps -axo command= | /usr/bin/awk -v profile="--user-data-dir=$EXPECTED_CHROME_PROFILE" '
  index($0, profile) && index($0, "/Contents/MacOS/Google Chrome") && !index($0, "/Helpers/") {
    if (index($0, "--headless")) { headless = 1 } else { visible = 1 }
  }
  END {
    if (headless && visible) print "mixed"
    else if (headless) print "headless"
    else if (visible) print "visible"
    else print "absent"
  }
')"

BROWSER_BINDING_REQUIRED=true
if [[ "$BROWSER_HEADLESS_DURING_FETCH" == "true" && "$CHROME_RUNTIME_MODE" == "absent" ]]; then
  # Headless Chrome is deliberately closed after a browser batch and relaunched on demand.
  BROWSER_BINDING_REQUIRED=false
elif [[ "$BROWSER_HEADLESS_DURING_FETCH" == "true" && "$CHROME_RUNTIME_MODE" != "headless" ]] \
  || [[ "$BROWSER_HEADLESS_DURING_FETCH" == "false" && "$CHROME_RUNTIME_MODE" != "visible" ]]; then
  printf '[infoos-reliability-acceptance] ERROR: dedicated test Chrome does not match headless_during_fetch=%s\n' "$BROWSER_HEADLESS_DURING_FETCH" >&2
  exit 2
fi

OPENCLI_BIN="${INFOOS_OPENCLI_EXECUTABLE:-}"
if [[ -z "$OPENCLI_BIN" ]]; then
  for candidate in \
    "$HOME/.nvm/versions/node/v24.15.0/bin/opencli" \
    "$HOME/.nvm/current/bin/opencli" \
    "/opt/homebrew/bin/opencli" \
    "/usr/local/bin/opencli"; do
    if [[ -x "$candidate" ]]; then
      OPENCLI_BIN="$candidate"
      break
    fi
  done
fi
binding_args=(check \
  --opencli "$OPENCLI_BIN" \
  --session-json "$EXPECTED_BROWSER_SESSION_JSON" \
  --profile-dir "$EXPECTED_CHROME_PROFILE" \
  --activate)
if [[ "$BROWSER_HEADLESS_DURING_FETCH" == "true" ]]; then
  binding_args+=(--allow-headless)
fi
if [[ "$BROWSER_BINDING_REQUIRED" == "true" ]] \
  && { [[ ! -x "$OPENCLI_BIN" ]] || ! .venv/bin/python "$BINDING_HELPER" "${binding_args[@]}"; }; then
  printf '[infoos-reliability-acceptance] ERROR: live infoos-fetch binding is not proven to belong to dedicated test Chrome\n' >&2
  exit 2
fi

curl --fail --silent --show-error http://127.0.0.1:8000/healthz >/dev/null || {
  printf '[infoos-reliability-acceptance] ERROR: fixed test backend is not healthy\n' >&2
  exit 2
}

REPORT_DIR="$INFOOS_STATE_HOME/acceptance/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$REPORT_DIR"

PYTHONPATH=. .venv/bin/pytest tests/infoos/faults -q
PYTHONPATH=. .venv/bin/pytest tests/infoos/acceptance -q
PYTHONPATH=. .venv/bin/python scripts/infoos-reliability-report.py \
  --base-dir "$INFOOS_STATE_HOME" \
  --output-json "$REPORT_DIR/reliability.json" \
  --output-markdown "$REPORT_DIR/reliability.md" \
  --required-acquisition-group browser

printf '[infoos-reliability-acceptance] report written to %s\n' "$REPORT_DIR"
