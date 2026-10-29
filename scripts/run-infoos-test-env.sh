#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${INFOOS_REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

EXPECTED_TEST_ROOT="$HOME/.infoos-test"
TEST_ROOT="${INFOOS_TEST_ROOT:-$EXPECTED_TEST_ROOT}"
TEST_DATA_HOME="${INFOOS_TEST_DATA_HOME:-$TEST_ROOT/data}"
TEST_STATE_HOME="${INFOOS_TEST_STATE_HOME:-$TEST_ROOT}"
TEST_CONFIG_PATH="${INFOOS_TEST_CONFIG_PATH:-$TEST_ROOT/config.yaml}"
TEST_SEED_DATA_HOME="${INFOOS_TEST_SEED_DATA_HOME:-$TEST_ROOT/platform-task-consumption-20260705_150336/data}"
# This is an explicit local fixture source, never a download target. It keeps
# the isolated test runtime reproducible while preserving TEST_ROOT/chrome-profile.
TEST_ASR_MODEL_SOURCE="${INFOOS_TEST_ASR_MODEL_SOURCE:-$TEST_ROOT/backups/data-20260707_230128/asr_models/sensevoice-small-int8}"
TEST_ASR_MODEL_DESTINATION="${INFOOS_TEST_ASR_MODEL_DESTINATION:-$TEST_DATA_HOME/asr_models/sensevoice-small-int8}"
TEST_CHROME_PROFILE="$TEST_STATE_HOME/chrome-profile"
TEST_BROWSER_SESSION_JSON="$TEST_STATE_HOME/browser-session.json"
TEST_BROWSER_BINDING_HELPER="$REPO_DIR/scripts/infoos-browser-binding.py"
TEST_MANAGED_OPENCLI_BIN="$HOME/InfoOS/runtime/opencli/1.8.6/node_modules/.bin/opencli"
TEST_RUNTIME_DIR="$TEST_STATE_HOME/runtime"
TEST_PID_FILE="$TEST_RUNTIME_DIR/test-env.pids"
TEST_SUPERVISOR_LOCK_DIR="$TEST_RUNTIME_DIR/test-env-supervisor.lock"
TEST_SCHEDULER_LOG="$TEST_DATA_HOME/logs/test-scheduler-cycle.log"

detect_stable_chrome() {
  if [[ -n "${INFOOS_CHROME_BIN:-}" && -x "$INFOOS_CHROME_BIN" ]]; then
    printf '%s\n' "$INFOOS_CHROME_BIN"
    return 0
  fi
  if [[ "$(uname -s)" == "Darwin" ]]; then
    local mac_chrome="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    [[ -x "$mac_chrome" ]] && { printf '%s\n' "$mac_chrome"; return 0; }
  else
    local linux_chrome
    for linux_chrome in /usr/bin/google-chrome-stable /usr/bin/google-chrome; do
      [[ -x "$linux_chrome" ]] && { printf '%s\n' "$linux_chrome"; return 0; }
    done
  fi
  return 1
}

BACKEND_HOST="${INFOOS_TEST_BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${INFOOS_TEST_BACKEND_PORT:-8000}"
FRONTEND_HOST="${INFOOS_TEST_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${INFOOS_TEST_FRONTEND_PORT:-5173}"
SCHEDULER_INTERVAL_SECONDS="${INFOOS_TEST_SCHEDULER_INTERVAL_SECONDS:-900}"

PROCESS_LABELS=()
PROCESS_PIDS=()
CLEANING_UP=0
LOCK_ACQUIRED=0

log() {
  printf '[infoos-test-env] %s\n' "$*"
}

die() {
  printf '[infoos-test-env] ERROR: %s\n' "$*" >&2
  exit 1
}

require_fixed_test_paths() {
  [[ "$TEST_ROOT" == "$EXPECTED_TEST_ROOT" ]] \
    || die "INFOOS_TEST_ROOT must equal $EXPECTED_TEST_ROOT"
  [[ "$TEST_STATE_HOME" == "$EXPECTED_TEST_ROOT" ]] \
    || die "INFOOS_TEST_STATE_HOME must equal $EXPECTED_TEST_ROOT"
  [[ "$TEST_DATA_HOME" == "$EXPECTED_TEST_ROOT/data" ]] \
    || die "INFOOS_TEST_DATA_HOME must equal $EXPECTED_TEST_ROOT/data"
  [[ "$TEST_CONFIG_PATH" == "$EXPECTED_TEST_ROOT/config.yaml" ]] \
    || die "INFOOS_TEST_CONFIG_PATH must equal $EXPECTED_TEST_ROOT/config.yaml"
}

process_command() {
  /bin/ps -p "$1" -o command= 2>/dev/null || true
}

stop_process_tree() {
  local pid="$1"
  local signal_name="${2:-TERM}"
  local child
  if ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  for child in $(/usr/bin/pgrep -P "$pid" 2>/dev/null || true); do
    stop_process_tree "$child" "$signal_name"
  done
  kill -"$signal_name" "$pid" 2>/dev/null || true
}

wait_for_process_exit() {
  local pid="$1"
  local waited=0
  while kill -0 "$pid" 2>/dev/null && [[ "$waited" -lt 20 ]]; do
    sleep 0.5
    waited=$((waited + 1))
  done
  if kill -0 "$pid" 2>/dev/null; then
    stop_process_tree "$pid" KILL
  fi
  wait "$pid" 2>/dev/null || true
}

register_process() {
  local label="$1"
  local pid="$2"
  PROCESS_LABELS+=("$label")
  PROCESS_PIDS+=("$pid")
  {
    printf 'supervisor=%s\n' "$$"
    local index
    for ((index = 0; index < ${#PROCESS_PIDS[@]}; index++)); do
      printf '%s=%s\n' "${PROCESS_LABELS[$index]}" "${PROCESS_PIDS[$index]}"
    done
  } > "$TEST_PID_FILE"
  log "$label 已启动 (pid=$pid)"
}

acquire_supervisor_lock() {
  local owner_pid stale_dir
  if mkdir "$TEST_SUPERVISOR_LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$TEST_SUPERVISOR_LOCK_DIR/owner.pid"
    LOCK_ACQUIRED=1
    return 0
  fi
  owner_pid="$(cat "$TEST_SUPERVISOR_LOCK_DIR/owner.pid" 2>/dev/null || true)"
  if [[ "$owner_pid" =~ ^[0-9]+$ ]] && kill -0 "$owner_pid" 2>/dev/null; then
    die "固定测试环境启动锁已由 supervisor pid=$owner_pid 持有"
  fi
  stale_dir="$TEST_SUPERVISOR_LOCK_DIR.stale.$$"
  if ! mv "$TEST_SUPERVISOR_LOCK_DIR" "$stale_dir" 2>/dev/null; then
    die "固定测试环境启动锁正在被其他 launcher 接管；拒绝并发启动"
  fi
  rm -f "$stale_dir/owner.pid"
  rmdir "$stale_dir" 2>/dev/null \
    || die "旧启动锁包含未知内容，拒绝自动删除: $stale_dir"
  if ! mkdir "$TEST_SUPERVISOR_LOCK_DIR" 2>/dev/null; then
    die "另一个 launcher 已先取得固定测试环境启动锁"
  fi
  printf '%s\n' "$$" > "$TEST_SUPERVISOR_LOCK_DIR/owner.pid"
  LOCK_ACQUIRED=1
}

release_supervisor_lock() {
  [[ "$LOCK_ACQUIRED" == "1" ]] || return 0
  local owner_pid
  owner_pid="$(cat "$TEST_SUPERVISOR_LOCK_DIR/owner.pid" 2>/dev/null || true)"
  if [[ "$owner_pid" == "$$" ]]; then
    rm -f "$TEST_SUPERVISOR_LOCK_DIR/owner.pid"
    rmdir "$TEST_SUPERVISOR_LOCK_DIR" 2>/dev/null || true
  fi
  LOCK_ACQUIRED=0
}

cleanup_stale_runtime() {
  [[ -f "$TEST_PID_FILE" ]] || return 0
  local old_supervisor label pid command_line parent_pid expected_fragment
  old_supervisor="$(awk -F= '$1 == "supervisor" {print $2; exit}' "$TEST_PID_FILE")"
  if [[ -n "$old_supervisor" ]] && kill -0 "$old_supervisor" 2>/dev/null; then
    die "固定测试环境已运行 (supervisor pid=$old_supervisor)；请先在原终端按 Ctrl-C"
  fi
  for label in backend frontend scheduler task-worker; do
    pid="$(awk -F= -v label="$label" '$1 == label {print $2; exit}' "$TEST_PID_FILE")"
    [[ "$pid" =~ ^[0-9]+$ ]] || continue
    kill -0 "$pid" 2>/dev/null || continue
    command_line="$(process_command "$pid")"
    case "$label" in
      backend) expected_fragment="backend.app.main" ;;
      frontend) expected_fragment="npm run dev" ;;
      scheduler) expected_fragment="bash scripts/run-infoos-test-env.sh" ;;
      task-worker) expected_fragment="backend.workers.task_worker" ;;
    esac
    [[ "$command_line" == *"$expected_fragment"* ]] \
      || die "旧 PID 文件中的 $label 身份不匹配，拒绝终止 pid=$pid: $command_line"
    parent_pid="$(/bin/ps -p "$pid" -o ppid= 2>/dev/null | tr -d ' ' || true)"
    [[ "$parent_pid" == "$old_supervisor" || "$parent_pid" == "1" ]] \
      || die "旧 PID 文件中的 $label 不属于旧 supervisor，拒绝终止 pid=$pid ppid=$parent_pid"
    log "回收异常退出 supervisor 遗留的 $label (pid=$pid)"
    stop_process_tree "$pid" TERM
    wait_for_process_exit "$pid"
  done
  rm -f "$TEST_PID_FILE"
}

fail_on_untracked_consumers() {
  local pid command_line
  for pid in $(/usr/bin/pgrep -f "backend.workers.task_worker" 2>/dev/null || true); do
    command_line="$(process_command "$pid")"
    [[ "$command_line" == *"backend.workers.task_worker"* ]] || continue
    die "发现未登记的 task worker (pid=$pid)，拒绝启动第二个消费者: $command_line"
  done
  for pid in $(/usr/bin/pgrep -f "bash scripts/run-infoos-test-env.sh" 2>/dev/null || true); do
    [[ "$pid" == "$$" ]] && continue
    command_line="$(process_command "$pid")"
    [[ "$command_line" == *"bash scripts/run-infoos-test-env.sh"* ]] || continue
    die "发现未登记的测试 scheduler/supervisor (pid=$pid)，拒绝重复启动: $command_line"
  done
}

cleanup() {
  if [[ "$CLEANING_UP" == "1" ]]; then
    return 0
  fi
  CLEANING_UP=1
  trap - INT TERM EXIT
  local index pid label
  for ((index = ${#PROCESS_PIDS[@]} - 1; index >= 0; index--)); do
    pid="${PROCESS_PIDS[$index]}"
    label="${PROCESS_LABELS[$index]}"
    if kill -0 "$pid" 2>/dev/null; then
      log "停止 $label (pid=$pid)"
      stop_process_tree "$pid" TERM
      wait_for_process_exit "$pid"
    fi
  done
  if [[ -f "$TEST_PID_FILE" ]] && [[ "$(awk -F= '$1 == "supervisor" {print $2; exit}' "$TEST_PID_FILE")" == "$$" ]]; then
    rm -f "$TEST_PID_FILE"
  fi
  release_supervisor_lock
  if [[ "${#PROCESS_PIDS[@]}" -gt 0 ]]; then
    log "Web、前端、scheduler 与 worker 已停止；专用测试 Chrome 保留登录态并继续运行"
  fi
}

trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT

kill_listener_on_port() {
  local port="$1"
  local pid command_line
  command -v lsof >/dev/null 2>&1 || return 0
  for pid in $(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true); do
    command_line="$(process_command "$pid")"
    if [[ "$command_line" != *"backend.app.main"* && "$command_line" != *"vite"* ]]; then
      die "端口 $port 被非 InfoOS 测试进程占用 (pid=$pid): $command_line"
    fi
    log "清理端口 $port 上的旧 InfoOS 进程: $pid"
    stop_process_tree "$pid" TERM
    wait_for_process_exit "$pid"
  done
}

sqlite_count() {
  local db_path="$1"
  local table_name="$2"
  if [[ ! -f "$db_path" ]] || ! command -v sqlite3 >/dev/null 2>&1; then
    printf '0'
    return 0
  fi
  sqlite3 "$db_path" "select count(*) from $table_name;" 2>/dev/null || printf '0'
}

prepare_test_data() {
  mkdir -p "$TEST_DATA_HOME" "$TEST_STATE_HOME" "$TEST_RUNTIME_DIR"
  local db_path="$TEST_DATA_HOME/infoos.sqlite3"
  local card_count source_count reset_data
  card_count="$(sqlite_count "$db_path" cards)"
  source_count="$(sqlite_count "$db_path" sources)"
  reset_data="${INFOOS_TEST_RESET_DATA:-0}"

  if [[ "$reset_data" != "1" && -f "$db_path" && ( "$card_count" != "0" || "$source_count" != "0" ) ]]; then
    log "使用固定测试数据库: $db_path (cards=$card_count, sources=$source_count)"
    return 0
  fi

  [[ -f "$TEST_SEED_DATA_HOME/infoos.sqlite3" ]] \
    || die "测试种子数据库不存在: $TEST_SEED_DATA_HOME/infoos.sqlite3"
  if [[ -d "$TEST_DATA_HOME" && -n "$(find "$TEST_DATA_HOME" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    local backup_dir="$TEST_ROOT/backups/data-$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$(dirname "$backup_dir")"
    log "当前测试数据为空或要求重置，备份到: $backup_dir"
    mv "$TEST_DATA_HOME" "$backup_dir"
    mkdir -p "$TEST_DATA_HOME"
  fi
  log "从固定测试快照补齐测试数据: $TEST_SEED_DATA_HOME -> $TEST_DATA_HOME"
  cp -a "$TEST_SEED_DATA_HOME"/. "$TEST_DATA_HOME"/
  card_count="$(sqlite_count "$db_path" cards)"
  source_count="$(sqlite_count "$db_path" sources)"
  log "测试数据库已就绪: $db_path (cards=$card_count, sources=$source_count)"
}

provision_test_asr_model() {
  if [[ -f "$TEST_ASR_MODEL_DESTINATION/model.int8.onnx" && -f "$TEST_ASR_MODEL_DESTINATION/tokens.txt" ]]; then
    log "使用固定测试 ASR 模型: $TEST_ASR_MODEL_DESTINATION"
    return 0
  fi
  if [[ ! -d "$TEST_ASR_MODEL_SOURCE" ]]; then
    log "未配置可复制的测试 ASR 模型；保持显式 ASR blocked 行为"
    return 0
  fi
  mkdir -p "$(dirname "$TEST_ASR_MODEL_DESTINATION")"
  log "从固定本地备份补齐测试 ASR 模型"
  cp -a "$TEST_ASR_MODEL_SOURCE" "$TEST_ASR_MODEL_DESTINATION"
  [[ -f "$TEST_ASR_MODEL_DESTINATION/model.int8.onnx" && -f "$TEST_ASR_MODEL_DESTINATION/tokens.txt" ]] \
    || die "测试 ASR 模型文件不完整: $TEST_ASR_MODEL_DESTINATION"
}

visible_infoos_chrome_running() {
  /bin/ps -axo command= | /usr/bin/awk -v profile="--user-data-dir=$TEST_CHROME_PROFILE" '
    index($0, profile) &&
    (index($0, "/Contents/MacOS/Google Chrome") || $0 ~ /^\/opt\/google\/chrome\/(google-chrome|chrome)( |$)/) &&
    !index($0, "/Helpers/") && !index($0, "--type=") && !index($0, "--headless") { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

headless_infoos_chrome_running() {
  /bin/ps -axo command= | /usr/bin/awk -v profile="--user-data-dir=$TEST_CHROME_PROFILE" '
    index($0, profile) &&
    (index($0, "/Contents/MacOS/Google Chrome") || $0 ~ /^\/opt\/google\/chrome\/(google-chrome|chrome)( |$)/) &&
    !index($0, "/Helpers/") && !index($0, "--type=") && index($0, "--headless") { found = 1 }
    END { exit(found ? 0 : 1) }
  '
}

browser_mode_running() {
  if [[ "$1" == "true" ]]; then
    headless_infoos_chrome_running
  else
    visible_infoos_chrome_running
  fi
}

browser_runtime_healthy() {
  local headless="$1"
  if [[ "$headless" == "true" ]]; then
    # The worker owns the full headless lifecycle.  Between browser batches
    # Chrome may be absent, terminating after its binding disconnects, or
    # starting before OpenCLI reconnects.  None of those transient states may
    # take down the backend, scheduler, public acquisition lane, or worker.
    return 0
  fi
  browser_mode_running "$headless" && browser_binding_ready "$headless"
}

browser_binding_ready() {
  local headless="${1:-false}"
  [[ -f "$TEST_BROWSER_SESSION_JSON" && -x "$TEST_OPENCLI_BIN" ]] || return 1
  local check_args=(check \
    --opencli "$TEST_OPENCLI_BIN" \
    --chrome-bin "$TEST_CHROME_BIN" \
    --session-json "$TEST_BROWSER_SESSION_JSON" \
    --profile-dir "$TEST_CHROME_PROFILE")
  if [[ "$headless" == "true" ]]; then
    check_args+=(--allow-headless)
  fi
  .venv/bin/python "$TEST_BROWSER_BINDING_HELPER" "${check_args[@]}" >/dev/null 2>&1
}

read_browser_headless() {
  .venv/bin/python -c '
import sys
from pathlib import Path
from backend.application.setup.visibility import read_headless_during_fetch
print("true" if read_headless_during_fetch(Path(sys.argv[1])) else "false")
' "$TEST_CONFIG_PATH"
}

prepare_dedicated_chrome() {
  local headless="$1"
  local start_url="${INFOOS_CHROME_START_URL:-https://x.com/home}"
  if [[ "$headless" == "true" ]]; then
    log "采集浏览器遵循配置，使用后台 Headless 模式: $TEST_CONFIG_PATH"
    bash scripts/opencli-infoos-chrome "$start_url" --headless
  else
    log "采集浏览器遵循配置，使用可见模式: $TEST_CONFIG_PATH"
    bash scripts/opencli-infoos-chrome "$start_url"
  fi

  local waited=0
  while [[ "$waited" -lt 60 ]]; do
    if browser_mode_running "$headless" && browser_binding_ready "$headless"; then
      log "专用测试 Chrome 已就绪: profile=$TEST_CHROME_PROFILE alias=infoos-fetch headless=$headless"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  die "专用测试 Chrome 未在 60 秒内按配置完成启动与 OpenCLI 绑定"
}

scheduler_loop() {
  trap 'exit 0' INT TERM
  mkdir -p "$(dirname "$TEST_SCHEDULER_LOG")"
  while true; do
    if bash scripts/infoos-scheduler-cycle.sh >> "$TEST_SCHEDULER_LOG" 2>&1; then
      log "scheduler cycle 完成；详情: $TEST_SCHEDULER_LOG"
    else
      log "scheduler cycle 失败；详情: $TEST_SCHEDULER_LOG"
      return 1
    fi
    sleep "$SCHEDULER_INTERVAL_SECONDS" &
    wait "$!"
  done
}

wait_for_http() {
  local name="$1"
  local url="$2"
  local waited=0
  while [[ "$waited" -lt 60 ]]; do
    if curl --fail --silent --show-error "$url" >/dev/null 2>&1; then
      log "$name 已就绪: $url"
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  die "$name 未在 60 秒内就绪: $url"
}

cd "$REPO_DIR"
[[ -x ".venv/bin/python" ]] || die "缺少 .venv；请先运行 bash scripts/bootstrap_env.sh"
command -v npm >/dev/null 2>&1 || die "缺少 npm"
command -v curl >/dev/null 2>&1 || die "缺少 curl"
TEST_CHROME_BIN="$(detect_stable_chrome)" || die "缺少正式版 Google Chrome"

TEST_OPENCLI_BIN="${INFOOS_OPENCLI_EXECUTABLE:-}"
if [[ -z "$TEST_OPENCLI_BIN" ]]; then
  for candidate in \
    "$TEST_MANAGED_OPENCLI_BIN" \
    "$HOME/.nvm/versions/node/v24.15.0/bin/opencli" \
    "$HOME/.nvm/current/bin/opencli" \
    "/opt/homebrew/bin/opencli" \
    "/usr/local/bin/opencli"; do
    if [[ -x "$candidate" ]]; then
      TEST_OPENCLI_BIN="$candidate"
      break
    fi
  done
fi
[[ -x "$TEST_OPENCLI_BIN" ]] || die "缺少 OpenCLI executable"
[[ -f "$TEST_BROWSER_BINDING_HELPER" ]] || die "缺少浏览器绑定校验器: $TEST_BROWSER_BINDING_HELPER"

require_fixed_test_paths
mkdir -p "$TEST_RUNTIME_DIR"
acquire_supervisor_lock
cleanup_stale_runtime
fail_on_untracked_consumers
kill_listener_on_port "$BACKEND_PORT"
kill_listener_on_port "$FRONTEND_PORT"
prepare_test_data
provision_test_asr_model

export INFOOS_STATE_HOME="$TEST_STATE_HOME"
export INFOOS_DATA_HOME="$TEST_DATA_HOME"
export INFOOS_CONFIG_PATH="$TEST_CONFIG_PATH"
export INFOOS_OPENCLI_EXECUTABLE="$TEST_OPENCLI_BIN"
export INFOOS_CHROME_BIN="$TEST_CHROME_BIN"
export INFOOS_CHROME_LAUNCHER="$REPO_DIR/scripts/opencli-infoos-chrome"
export INFOOS_BROWSER_BINDING_HELPER="$TEST_BROWSER_BINDING_HELPER"
export INFOOS_CHROME_PROFILE_DIR="$TEST_CHROME_PROFILE"
export INFOOS_BROWSER_SESSION_JSON="$TEST_BROWSER_SESSION_JSON"
export INFOOS_BROWSER_SESSION_LOCK="$TEST_STATE_HOME/browser-session.json.lock"
export INFOOS_WEB_HOST="$BACKEND_HOST"
export INFOOS_WEB_PORT="$BACKEND_PORT"
export INFOOS_VITE_API_TARGET="http://$BACKEND_HOST:$BACKEND_PORT"
export INFOOS_TASK_WORKER_MAX_WORKERS="1"
export INFOOS_BROWSER_FETCH_WORKERS="1"
export INFOOS_PUBLIC_FETCH_WORKERS="1"

log "测试环境固定配置:"
log "  INFOOS_STATE_HOME=$INFOOS_STATE_HOME"
log "  INFOOS_DATA_HOME=$INFOOS_DATA_HOME"
log "  INFOOS_CONFIG_PATH=$INFOOS_CONFIG_PATH"
log "  INFOOS_CHROME_LAUNCHER=$INFOOS_CHROME_LAUNCHER"
log "  INFOOS_CHROME_BIN=$INFOOS_CHROME_BIN"
log "  INFOOS_BROWSER_BINDING_HELPER=$INFOOS_BROWSER_BINDING_HELPER"
BROWSER_HEADLESS_DURING_FETCH="$(read_browser_headless)"
[[ "$BROWSER_HEADLESS_DURING_FETCH" == "true" || "$BROWSER_HEADLESS_DURING_FETCH" == "false" ]] \
  || die "无法读取 browser.headless_during_fetch"
log "  Chrome profile=$TEST_CHROME_PROFILE (headless=$BROWSER_HEADLESS_DURING_FETCH)"
log "  后端=http://$BACKEND_HOST:$BACKEND_PORT/"
log "  前端=http://$FRONTEND_HOST:$FRONTEND_PORT/"
log "  scheduler interval=${SCHEDULER_INTERVAL_SECONDS}s"
log "  task worker=single process / browser lane concurrency 1"

prepare_dedicated_chrome "$BROWSER_HEADLESS_DURING_FETCH"

bash scripts/infoos-web-server.sh &
register_process "backend" "$!"

if [[ "$FRONTEND_HOST" == "127.0.0.1" ]]; then
  npm --prefix frontend run dev -- --port "$FRONTEND_PORT" &
else
  npm --prefix frontend run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" &
fi
register_process "frontend" "$!"

scheduler_loop &
register_process "scheduler" "$!"

bash scripts/infoos-task-worker.sh &
register_process "task-worker" "$!"

wait_for_http "后端" "http://$BACKEND_HOST:$BACKEND_PORT/healthz"
wait_for_http "前端" "http://$FRONTEND_HOST:$FRONTEND_PORT/"
log "完整固定测试环境已启动。后续测试不得另开 worker、scheduler 或替代 Chrome。"
log "按 Ctrl-C 停止 Web、前端、scheduler 与 worker；专用测试 Chrome 不关闭。"

while true; do
  for index in "${!PROCESS_PIDS[@]}"; do
    if ! kill -0 "${PROCESS_PIDS[$index]}" 2>/dev/null; then
      die "${PROCESS_LABELS[$index]} 异常退出 (pid=${PROCESS_PIDS[$index]})"
    fi
  done
  if ! browser_runtime_healthy "$BROWSER_HEADLESS_DURING_FETCH"; then
    die "专用测试 Chrome 或 OpenCLI 固定绑定丢失"
  fi
  sleep 2
done
