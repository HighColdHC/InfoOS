#!/bin/bash
set -euo pipefail
# Select an installed UTF-8 locale for the suite's Bash character count.
for candidate in C.UTF-8 en_US.UTF-8; do
  if LC_ALL="$candidate" locale charmap 2>/dev/null | grep -qi utf; then
    export LC_ALL="$candidate"; break
  fi
done
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
ALERT="$ROOT/deploy/agent/agent-run-alert.sh"
TEST_DIR=$(mktemp -d "${TMPDIR:-/tmp}/infoos-agent-alert.XXXXXX")
trap 'rm -rf "$TEST_DIR"' EXIT
# HOME is deliberately isolated: never touch the real status history or logs.
export HOME="$TEST_DIR/home"
export INFOOS_ALERT_DRY_RUN=1
mkdir -p "$HOME" "$TEST_DIR/bin"
# Any accidental osascript call must fail the suite, even though alerts fail open.
cat > "$TEST_DIR/bin/osascript" <<'SH'
#!/bin/bash
touch "$HOME/osascript-called"
exit 1
SH
chmod +x "$TEST_DIR/bin/osascript"
export PATH="$TEST_DIR/bin:$PATH"
export INFOOS_AGENT_PATH="$PATH"
export INFOOS_AGENT_ALERT_DIR="$HOME/alerts"
export INFOOS_AGENT_CONFIG_FILE="$HOME/absent.env"
STATUS="$INFOOS_AGENT_ALERT_DIR/status.tsv"
LOG="$TEST_DIR/run.log"
checks=0

check_run() {
  local job=$1 code=$2 expected=$3 excerpt=$4 streak=${5:-1}
  local before=0 after output
  if [ -f "$STATUS" ]; then before=$(wc -l < "$STATUS"); fi
  output=$(bash "$ALERT" "$job" "$code" "$LOG")
  after=$(wc -l < "$STATUS")
  [ "$after" -eq "$((before + 1))" ]
  awk -F '\t' -v job="$job" -v state="$expected" -v code="$code" \
    -v excerpt="$excerpt" -v expected_log="$LOG" '
    END {
      if (NF != 6 || $1 !~ /^[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z$/ ||
          $2 != job || $3 != state || $4 != code || $5 != excerpt || $6 != expected_log) exit 1
    }
  ' "$STATUS"
  if [ "$expected" = ok ]; then
    [ -z "$output" ]
  else
    [ "$output" = "InfoOS 定时任务失败：job=${job}，exit=${code}，连续失败 ${streak} 次：${excerpt}" ]
  fi
  [ ! -e "$HOME/osascript-called" ]
  checks=$((checks + 1))
}

printf 'codex\naccepted: 3\n剩余 0\n' > "$LOG"
check_run evaluate 0 ok '完成'
check_run opportunities 0 ok '完成'
echo 'PASS: 成功报告'

printf 'ERROR: {"status":400,"message":"Bad request"}\nERROR: second error\n' > "$LOG"
check_run evaluate 1 fail 'ERROR: {"status":400,"message":"Bad request"}'
echo 'PASS: 400 ERROR（保留第一条摘要）'

printf 'ERROR: Selected model is at capacity\n' > "$LOG"
check_run opportunities 0 fail 'ERROR: Selected model is at capacity'
echo 'PASS: capacity ERROR（exit=0 仍告警）'

printf 'codex\naccepted: 2\n' > "$LOG"
check_run opportunities 1 fail 'exit=1（日志无 ERROR 行）' 2
echo 'PASS: exit=1 无 ERROR'

printf 'user\naccepted: 0；剩余 0\ncodex\n没有待评估的卡\n' > "$LOG"
check_run evaluate 0 ok '完成'
echo 'PASS: 没有待评估的卡'

cat > "$LOG" <<'LOG'
user
如果没有待评估的卡就结束；最终报告 accepted: 0、剩余数量。
codex
读取下一页。
codex
- **accepted 数**：**5**
剩余 0
LOG
check_run evaluate 0 ok '完成'
echo 'PASS: 忽略提示词关键字，只读最后报告'

printf 'ERROR: Selected model is at capacity\n' > "$LOG"
check_run evaluate 1 fail 'ERROR: Selected model is at capacity'
check_run opportunities 0 fail 'ERROR: Selected model is at capacity' 3
check_run evaluate 1 fail 'ERROR: Selected model is at capacity' 2
echo 'PASS: 连续失败每次通知，分 job 计数，成功后重置'

cat > "$LOG" <<'LOG'
user
没有待评估的卡；剩余 0；accepted: 8。
codex
accepted: 9
codex
accepted: 0
尚未清空。
LOG
check_run evaluate 0 fail '最终报告未确认写回（accepted 为 0 或缺失）' 3
printf 'user\n没有待评估的卡\ncodex\n未完成报告。\n' > "$LOG"
check_run evaluate 0 fail '最终报告未确认写回（accepted 为 0 或缺失）' 4
echo 'PASS: accepted=0/缺失告警，提示词和旧报告不掩盖失败'

printf 'codex\n{"accepted": 2}\n' > "$LOG"
check_run evaluate 0 ok '完成'
printf 'codex\n| accepted | 4 |\n' > "$LOG"
check_run evaluate 0 ok '完成'
printf 'codex\n本轮检查 40 张，**40 条 accepted**。\n' > "$LOG"
check_run evaluate 0 ok '完成'
printf 'codex\n- accepted／已回读：`40／40`\n' > "$LOG"
check_run evaluate 0 ok '完成'
echo 'PASS: JSON、表格、计数在前与成对标签'

cat > "$LOG" <<'LOG'
user
空页只报告“本轮固定窗口查询未返回剩余候选”。
codex
- accepted：0；已回读：0
- 剩余探针：**本轮固定窗口查询未返回剩余候选**。
LOG
check_run opportunities 0 ok '完成'
echo 'PASS: 商机空候选'

cat > "$LOG" <<'LOG'
user
空页只报告“本轮固定窗口查询未返回剩余候选”。
codex
停止原因：所需 MCP 工具未挂载。accepted / 已回读 | 0 / 0 |
剩余探针未执行，剩余数量 unknown。
LOG
check_run opportunities 0 fail '最终报告未确认写回（accepted 为 0 或缺失）'
echo 'PASS: 商机 accepted=0 且无空候选标记，提示词不掩盖失败'

cat > "$LOG" <<'LOG'
tool call error: tool call failed for `infoos/get_profile`

Caused by:
    HTTP 502
codex
40 条 accepted；本轮固定窗口查询未返回剩余候选
LOG
check_run opportunities 0 fail 'tool call error: tool call failed for `infoos/get_profile`' 2
check_run evaluate 0 ok '完成'
echo 'PASS: 商机 tool call error 告警，评估回读重试后不告警'

printf 'codex\naccepted: 1\nrejected: 0\n' > "$LOG"
check_run knowledge 0 ok '完成'
printf 'codex\naccepted: 1\nrejected: 1\n' > "$LOG"
check_run knowledge 0 fail '最终报告仍有 rejected，知识整理未完成'
printf 'codex\n- 分诊：尝试101张，100张 accepted，1张仍 rejected。累计收到13次 rejected，其中11条已修正成功。\n- 拆解：26张 accepted、0 rejected。\n' > "$LOG"
check_run knowledge 0 fail '最终报告仍有 rejected，知识整理未完成' 2
printf 'codex\naccepted: 1\n最终 rejected: 0\n' > "$LOG"
check_run knowledge 0 ok '完成'
printf 'codex\naccepted: 1\n首次 rejected: 1；重新提交条数：1；改正后 accepted：1\n- **最终 rejected**：**0**\n' > "$LOG"
check_run knowledge 0 ok '完成'
printf 'codex\naccepted: 1\n- **最终 rejected**：**1**\n' > "$LOG"
check_run knowledge 0 fail '最终报告仍有 rejected，知识整理未完成'
printf 'codex\n本轮分诊与拆解均无剩余候选\n' > "$LOG"
check_run knowledge 0 ok '完成'
echo 'PASS: 知识卡写回、拒绝与空窗口'

long_error='ERROR: '
for ((i=0; i<130; i++)); do long_error="${long_error}错"; done
printf '%s\n' "$long_error" > "$LOG"
excerpt='ERROR: '
for ((i=0; i<113; i++)); do excerpt="${excerpt}错"; done
[ "${#excerpt}" -eq 120 ]
check_run truncation 1 fail "$excerpt"
echo 'PASS: 中文摘要截断至 120 字符'

rm "$LOG"
check_run missing 0 fail '无法读取运行日志'
echo 'PASS: 日志不可读也告警，脚本退出 0'
cat > "$TEST_DIR/bin/uname" <<'SH'
#!/bin/bash
echo Linux
SH
chmod +x "$TEST_DIR/bin/uname"
printf 'ERROR: headless failure\n' > "$LOG"
output=$(INFOOS_ALERT_DRY_RUN=0 bash "$ALERT" knowledge 1 "$LOG" 2>&1)
[[ "$output" = 'InfoOS 定时任务失败：'*'ERROR: headless failure' ]]
[ "$(tail -n 1 "$STATUS" | cut -f3)" = fail ]
[ ! -e "$HOME/osascript-called" ]
checks=$((checks + 1))
echo 'PASS: Linux 无桌面时写状态和 stderr，不调用 osascript'
printf 'PASS: 共 %s 次调用，全部使用临时 HOME，通知使用 DRY_RUN 或假命令\n' "$checks"
