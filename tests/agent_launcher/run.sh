#!/bin/bash
set -euo pipefail
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/infoos-agent-launcher.XXXXXX")
trap 'rm -rf "$TEST_DIR"' EXIT
export TEST_REAL_DATE=$(command -v date)
export TEST_REAL_CAT=$(command -v cat)
export TEST_REVISION=$(git -C "$ROOT" rev-parse HEAD)
ORIGINAL_PATH=$PATH
mkdir -p "$TEST_DIR/bin"
cat > "$TEST_DIR/bin/uname" <<'STUB'
#!/bin/bash
printf '%s\n' "$TEST_PLATFORM"
STUB
cat > "$TEST_DIR/bin/cat" <<'STUB'
#!/bin/bash
# Never create/read a credential file, even in the isolated HOME.
if [ "$#" -eq 1 ] && [ "$1" = "$INFOOS_MCP_TOKEN_FILE" ]; then
  printf 'launcher-test-placeholder\n'
else
  exec "$TEST_REAL_CAT" "$@"
fi
STUB
cat > "$TEST_DIR/bin/date" <<'STUB'
#!/bin/bash
set -euo pipefail
case "$*" in
  +%s) printf '%s\n' "$(< "$HOME/clock")" ;;
  +%H) printf '%s\n' "${TEST_HOUR:-06}" ;;
  +%M) printf '%s\n' "${TEST_MINUTE:-30}" ;;
  '-u -d @'*|'-u -r '*)
    if [ "$TEST_PLATFORM" = Linux ]; then [ "$2" = -d ]; else [ "$2" = -r ]; fi
    printf '%s\n' "$2" >> "$HOME/date-branch"
    python3 - "${3#@}" <<'PY'
from datetime import datetime, timezone
import sys
print(datetime.fromtimestamp(int(sys.argv[1]), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
PY
    ;;
  *) exec "$TEST_REAL_DATE" "$@" ;;
esac
STUB
cat > "$TEST_DIR/bin/sleep" <<'STUB'
#!/bin/bash
set -euo pipefail
[ ! -e "$INFOOS_AGENT_ALERT_DIR/status.tsv" ]
[ -d "$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock" ]
printf '%s\n' "$1" >> "$HOME/sleeps"
printf '%s\n' "$(( $(< "$HOME/clock") + $1 + 1 ))" > "$HOME/clock"
STUB
cat > "$TEST_DIR/bin/codex" <<'STUB'
#!/bin/bash
set -euo pipefail
calls=0
if [ -f "$HOME/calls" ]; then calls=$(< "$HOME/calls"); fi
calls=$((calls + 1))
printf '%s\n' "$calls" > "$HOME/calls"
printf '%s\n' "$@" > "$HOME/args-$calls"
[ ! -e "$INFOOS_AGENT_ALERT_DIR/status.tsv" ]
[ -d "$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock" ]
if IFS= read -r input; then exit 90; fi
elapsed=3
if [ "$TEST_SCENARIO" = slow ]; then elapsed=601; fi
printf '%s\n' "$(( $(< "$HOME/clock") + elapsed ))" > "$HOME/clock"
case "$TEST_SCENARIO:$calls" in
  success:*|retry_success:2) printf 'codex\naccepted: 1\nrejected: 0\n' ;;
  corrected:*|final_rejected:*)
    echo 'mcp: infoos/submit_evaluations({"items":[]})'
    echo 'mcp: infoos/list_extraction_candidates({})'
    echo 'codex'
    echo '重新提交条数: 1，改正后 accepted: 1'
    if [ "$TEST_SCENARIO" = corrected ]; then echo '最终 rejected: 0'; else echo '最终 rejected: 1，错误码: invalid_exclusion'; fi ;;
  submitted:*)
    echo 'mcp: infoos/submit_knowledge_extractions({"items":[]})'
    echo 'ERROR: transport failed'; exit 1 ;;
  twice_fail:2) echo 'ERROR: transport still failed'; exit 1 ;;
  *)
    echo 'ERROR: transport failed'
    if [ "$TEST_SCENARIO" != retry_success ]; then exit 1; fi ;;
esac
STUB
cat > "$TEST_DIR/bin/timeout" <<'STUB'
#!/bin/bash
# Record the remaining budget; the fake agent terminates immediately.
printf '%s\n' "$2" >> "$HOME/budgets"
shift 2
exec "$@"
STUB
cat > "$TEST_DIR/bin/git" <<'STUB'
#!/bin/bash
case "$*" in
  *'status --porcelain') if [ "${TEST_DIRTY:-0}" = 1 ]; then echo ' M file'; fi ;;
  *'rev-parse --verify HEAD^{commit}') printf '%s\n' "$TEST_REVISION" ;;
  *) exit 95 ;;
esac
STUB
cat > "$TEST_DIR/bin/systemctl" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$HOME/systemctl-calls"
STUB
cat > "$TEST_DIR/bin/launchctl" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >> "$HOME/launchctl-calls"
STUB
chmod +x "$TEST_DIR/bin/"*
checks=0
new_home() {
  export HOME="$TEST_DIR/$1"
  export INFOOS_AGENT_CONFIG_DIR="$HOME/config" INFOOS_AGENT_CONFIG_FILE="$HOME/agent.env"
  export INFOOS_KNOWLEDGE_LOG_DIR="$HOME/logs" INFOOS_AGENT_ALERT_DIR="$HOME/alerts"
  export INFOOS_KNOWLEDGE_INSTALL_DIR="$HOME/install with space"
  export INFOOS_LAUNCHD_DIR="$HOME/launchd" INFOOS_SYSTEMD_USER_DIR="$HOME/units"
  export INFOOS_MCP_TOKEN_FILE="$HOME/never-read.token"
  export INFOOS_AGENT_PATH="$TEST_DIR/bin:$ORIGINAL_PATH" PATH="$TEST_DIR/bin:$ORIGINAL_PATH"
  export INFOOS_KNOWLEDGE_SINCE=2026-01-01T00:00:00Z INFOOS_MCP_URL=http://localhost:8000/mcp
  export INFOOS_KNOWLEDGE_HOURS=06 INFOOS_KNOWLEDGE_MINUTE=30 INFOOS_KNOWLEDGE_TIMEZONE=UTC
  export INFOOS_KNOWLEDGE_TIMEOUT_SEC=3300 INFOOS_AGENT_MODEL=test-model
  export INFOOS_AGENT_CODEX_BIN=codex INFOOS_AGENT_EFFORT=medium INFOOS_AGENT_SERVICE_TIER=default
  export TEST_SCENARIO=success
  export INFOOS_AGENT_RETRY_DELAY_SEC=0 INFOOS_ALERT_DRY_RUN=1
  unset INFOOS_MCP_TOKEN TEST_DIRTY TEST_HOUR TEST_MINUTE
  mkdir -p "$HOME"
  printf '1800000000\n' > "$HOME/clock"
}
pass() { checks=$((checks + 1)); printf 'PASS: %s\n' "$*"; }
check_run() {
  local scenario=$1 calls=$2 notifications=$3 expected_exit=$4 result=0 output status summary
  new_home "$TEST_PLATFORM-$scenario"
  export TEST_SCENARIO=$scenario
  if [ "$scenario" = twice_fail ]; then unset INFOOS_AGENT_RETRY_DELAY_SEC; fi
  output=$(bash "$ROOT/deploy/agent/run-knowledge.sh" --now 2>&1) || result=$?
  [ "$result" -eq "$expected_exit" ]
  [ "$(< "$HOME/calls")" -eq "$calls" ]
  [ "$(printf '%s\n' "$output" | grep -c '^InfoOS 定时任务失败：' || :)" -eq "$notifications" ]
  [ ! -d "$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock" ]
  status="$INFOOS_AGENT_ALERT_DIR/status.tsv"
  [ "$(wc -l < "$status")" -eq 1 ]
  awk -F '\t' -v code="$expected_exit" -v fail="$notifications" '
    NF != 6 || $2 != "knowledge" || $4 != code || $3 != (fail ? "fail" : "ok") { exit 1 }
  ' "$status"
  [ -f "$(cut -f6 "$status")" ]
  summary=$(cut -f5 "$status")
  if [ "$calls" -eq 2 ]; then
    [[ "$summary" = *'已自动重跑 1 次，首次：ERROR: transport failed' ]]
    [ "$(wc -l < "$HOME/sleeps")" -eq 1 ]
    # Both attempts share the window and deadline, but the timeout budget shrinks.
    cmp "$HOME/args-1" "$HOME/args-2"
    [ "$(sed -n '1p' "$HOME/budgets")" = 3300s ]
    [ "$(sed -n '2p' "$HOME/budgets")" != 3300s ]
    if [ "$scenario" = twice_fail ]; then [ "$(< "$HOME/sleeps")" = 300 ]; fi
  else
    [ ! -e "$HOME/sleeps" ]
  fi
  grep -Fq '2027-01-15T08:00:00Z' "$HOME/args-1"
  grep -Fq '2027-01-15T08:55:00Z' "$HOME/args-1"
  ! grep -Eq '\{(SINCE|UNTIL|DEADLINE|EVALUATOR)\}' "$HOME/args-1"
  if [ "$TEST_PLATFORM" = Linux ]; then grep -qx -- -d "$HOME/date-branch"; else grep -qx -- -r "$HOME/date-branch"; fi
  pass "$TEST_PLATFORM / $scenario (calls=$calls, status=1)"
}
for TEST_PLATFORM in Darwin Linux; do
  export TEST_PLATFORM
  check_run retry_success 2 0 0
  check_run submitted 1 1 1
  check_run twice_fail 2 1 1
  check_run slow 1 1 1
  check_run success 1 0 0
  check_run corrected 1 0 0
  check_run final_rejected 1 1 0
  new_home "$TEST_PLATFORM-schedule"
  export TEST_SCENARIO=success TEST_HOUR=07
  bash "$ROOT/deploy/agent/run-knowledge.sh"
  [ ! -e "$HOME/calls" ]
  export TEST_HOUR=06 TEST_MINUTE=29
  bash "$ROOT/deploy/agent/run-knowledge.sh"
  [ ! -e "$HOME/calls" ]
  export TEST_MINUTE=30
  bash "$ROOT/deploy/agent/run-knowledge.sh"
  [ "$(< "$HOME/calls")" -eq 1 ]
  pass "$TEST_PLATFORM / schedule gate"
  new_home "$TEST_PLATFORM-lock"
  mkdir -p "$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock"
  bash "$ROOT/deploy/agent/run-knowledge.sh" --now >/dev/null
  [ ! -e "$HOME/calls" ]
  [ -d "$INFOOS_AGENT_CONFIG_DIR/.knowledge.lock" ]
  pass "$TEST_PLATFORM / concurrent run skipped"
done
# Exercise file config and environment precedence through the launched agent.
new_home config
unset INFOOS_AGENT_MODEL
printf "INFOOS_AGENT_MODEL='file-model'\nINFOOS_MCP_URL='http://file.invalid/mcp'\n" > "$INFOOS_AGENT_CONFIG_FILE"
bash "$ROOT/deploy/agent/run-knowledge.sh" --now
[ "$(sed -n '/^-m$/{n;p;}' "$HOME/args-1")" = file-model ]
grep -Fxq 'mcp_servers.infoos.url="http://localhost:8000/mcp"' "$HOME/args-1"
pass 'file config and environment precedence reach Codex'

# macOS without GNU timeout keeps the existing Perl alarm execution path.
new_home perl-fallback
export TEST_PLATFORM=Darwin
mkdir -p "$HOME/bin"
for stub in "$TEST_DIR/bin/"*; do
  if [ "${stub##*/}" != timeout ]; then ln -s "$stub" "$HOME/bin/"; fi
done
export INFOOS_AGENT_PATH="$HOME/bin:/usr/bin:/bin"
bash "$ROOT/deploy/agent/run-knowledge.sh" --now
[ "$(< "$HOME/calls")" -eq 1 ] && [ ! -e "$HOME/budgets" ]
pass 'macOS / Perl fallback executes the agent'

# Installation only ever sees this fake HOME, clean git stub and scheduler stubs.
for TEST_PLATFORM in Darwin Linux; do
  export TEST_PLATFORM
  new_home "$TEST_PLATFORM-install"
  installer="$ROOT/deploy/agent/install-knowledge-agent.sh"
  if [ "$TEST_PLATFORM" = Linux ]; then installer="$ROOT/deploy/agent/install-knowledge-agent-systemd.sh"; fi
  bash "$installer" --copy-only >/dev/null
  [ ! -e "$HOME/launchctl-calls" ] && [ ! -e "$HOME/systemctl-calls" ]
  [ "$(< "$INFOOS_KNOWLEDGE_INSTALL_DIR/source-revision")" = "$TEST_REVISION" ]
  # Execute the installed snapshot after clearing all instance config from the environment.
  env -i HOME="$HOME" TEST_PLATFORM="$TEST_PLATFORM" TEST_SCENARIO=success \
    TEST_REAL_CAT="$TEST_REAL_CAT" TEST_REAL_DATE="$TEST_REAL_DATE" \
    INFOOS_ALERT_DRY_RUN=1 INFOOS_AGENT_CONFIG_FILE="$INFOOS_KNOWLEDGE_INSTALL_DIR/installed.env" \
    /bin/bash "$INFOOS_KNOWLEDGE_INSTALL_DIR/run-knowledge.sh" --now
  [ "$(< "$HOME/calls")" -eq 1 ]
  bash "$installer" >/dev/null
  if [ "$TEST_PLATFORM" = Darwin ]; then
    python3 - "$INFOOS_LAUNCHD_DIR/com.infoos.knowledge.plist" <<'PY'
import plistlib, sys
with open(sys.argv[1], 'rb') as stream:
    value = plistlib.load(stream)
assert value['Label'] == 'com.infoos.knowledge'
assert len(value['StartCalendarInterval']) == 60
assert value['ProgramArguments'][1].endswith('/install with space/run-knowledge.sh')
PY
    grep -q 'bootstrap' "$HOME/launchctl-calls"
  else
    grep -Fq 'OnCalendar=*-*-* 06:30:00 UTC' "$INFOOS_SYSTEMD_USER_DIR/infoos-knowledge.timer"
    rm "$INFOOS_AGENT_ALERT_DIR/status.tsv"
    # Execute the generated service's argv/environment with a fake agent. This
    # also checks quoted paths and Persistent catch-up outside the schedule gate.
    python3 - "$INFOOS_SYSTEMD_USER_DIR/infoos-knowledge.service" <<'PY'
import configparser, os, shlex, subprocess, sys
unit = configparser.ConfigParser(interpolation=None)
unit.read(sys.argv[1])
argv = shlex.split(unit['Service']['ExecStart'])
argv[0] = argv[0].lstrip(':')
env = {key: os.environ[key] for key in ('HOME', 'TEST_PLATFORM', 'TEST_REAL_CAT', 'TEST_REAL_DATE')}
env.update(TEST_SCENARIO='success', TEST_HOUR='07', TEST_MINUTE='29', INFOOS_ALERT_DRY_RUN='1')
key, value = shlex.split(unit['Service']['Environment'])[0].split('=', 1)
env[key] = value
subprocess.run(argv, env=env, cwd=shlex.split(unit['Service']['WorkingDirectory'])[0], check=True)
PY
    [ "$(< "$HOME/calls")" -eq 2 ]
    grep -Fq -- '--user enable --now infoos-knowledge.timer' "$HOME/systemctl-calls"
  fi
  bash "$installer" --disable
  if [ "$TEST_PLATFORM" = Darwin ]; then
    grep -q 'disable gui/.*/com.infoos.knowledge' "$HOME/launchctl-calls"
  else
    grep -Fxq -- '--user disable --now infoos-knowledge.timer' "$HOME/systemctl-calls"
    grep -Fxq -- '--user stop infoos-knowledge.service' "$HOME/systemctl-calls"
  fi
  pass "$TEST_PLATFORM / snapshot, scheduler generation and disable (fake commands)"
  export TEST_DIRTY=1
  result=0
  bash "$installer" --copy-only >/dev/null 2>&1 || result=$?
  [ "$result" -eq 65 ]
  pass "$TEST_PLATFORM / dirty install refused"
done
printf 'PASS: %s launcher scenarios; isolated HOME, no real credentials or schedulers\n' "$checks"
