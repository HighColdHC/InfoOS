#!/bin/bash
# Best-effort local alerting; never change the job status.
set +e
umask 077
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
source "$SCRIPT_DIR/agent-config.sh"

verdict_only=0
first_summary=''
case "${1:-}" in
  --verdict-only) verdict_only=1; shift ;;
  --retry-summary) first_summary=$2; shift 2 ;;
esac
if [ "$#" -ne 3 ]; then
  echo 'Usage: agent-run-alert.sh [--verdict-only | --retry-summary <first_summary>] <job_name> <exit_code> <log_file>' >&2
  exit 0
fi
job=$1
job_exit=$2
log_file=$3
alert_dir="$INFOOS_AGENT_ALERT_DIR"
status_file="$alert_dir/status.tsv"

# ERROR lines can precede any report. Business-result checks use only the last
# standalone codex section, never the echoed prompt or earlier progress reports.
# Knowledge stops on any tool error. Retain legacy job verdicts for old logs.
result=$(awk -v job="$job" -v job_exit="$job_exit" '
  /^ERROR:/ && first_error == "" { first_error = $0 }
  /^tool call error/ && tool_error == "" { tool_error = $0 }
  /^codex$/ { report = 1; accepted = 0; rejected = 0; final_rejected_seen = 0; final_rejected = 0; empty = 0; next }
  report {
    if (index($0, job == "evaluate" ? "没有待评估的卡" : "本轮固定窗口查询未返回剩余候选")) empty = 1
    if (job == "knowledge" && index($0, "本轮分诊与拆解均无剩余候选")) empty = 1
    line = tolower($0)
    gsub(/：/, ":", line)
    gsub(/数/, "", line)
    gsub(/为/, "", line)
    gsub(/／/, "/", line)
    # Allow plain text, Markdown, JSON-style counts, table cells and paired
    # labels such as "accepted/已回读: 40/40".
    if (match(line, /accepted([[:space:]]*\/[^0-9]*)?[[:space:]*_`"|:=]*[0-9]+/)) {
      count = substr(line, RSTART, RLENGTH)
      sub(/^[^0-9]*/, "", count)
      if (count + 0 > 0) accepted = 1
    }
    # An explicit final count overrides historical rejection/retry counts.
    if (job == "knowledge" && match(line, /最终[[:space:]*_`]*rejected[[:space:]*_`]*:[[:space:]*_`]*[0-9]+/)) {
      count = substr(line, RSTART, RLENGTH)
      sub(/^[^0-9]*/, "", count)
      final_rejected_seen = 1
      if (count + 0 > 0) final_rejected = 1
    }
    if (job == "knowledge" && match(line, /rejected[[:space:]*_`"|:=]*[0-9]+/)) {
      count = substr(line, RSTART, RLENGTH)
      sub(/^[^0-9]*/, "", count)
      if (count + 0 > 0) rejected = 1
    }
    if (job == "knowledge" && match(line, /[0-9]+[[:space:]*_`"|]*(条|张)?[[:space:]*_`"|]*(仍[[:space:]*_`"|]*)?rejected/)) {
      count = substr(line, RSTART, RLENGTH)
      sub(/[^0-9].*$/, "", count)
      if (count + 0 > 0) rejected = 1
    }
    # Prose reports also put the count first: "40 条 accepted".
    if (match(line, /[0-9]+[[:space:]*_`"|]*(条|张)?[[:space:]*_`"|]*accepted/)) {
      count = substr(line, RSTART, RLENGTH)
      sub(/[^0-9].*$/, "", count)
      if (count + 0 > 0) accepted = 1
    }
  }
  END {
    if (first_error != "") print "fail\t" first_error
    else if (job_exit != "0") print "fail\texit=" job_exit "（日志无 ERROR 行）"
    else if ((job == "opportunities" || job == "knowledge") && tool_error != "") print "fail\t" tool_error
    else if (job == "knowledge" && (final_rejected_seen ? final_rejected : rejected)) print "fail\t最终报告仍有 rejected，知识整理未完成"
    else if ((job == "evaluate" || job == "opportunities" || job == "knowledge") && !accepted && !empty)
      print "fail\t最终报告未确认写回（accepted 为 0 或缺失）"
    else print "ok\t完成"
  }
' "$log_file" 2>/dev/null) || result=$'fail\t无法读取运行日志'
if [ -z "$result" ]; then
  result=$'fail\t无法读取运行日志'
fi
state=${result%%$'\t'*}
summary=${result#*$'\t'}
# Keep every record on one TSV line and bound the notification/error excerpt.
clean_field() { printf '%s' "$1" | tr '\t\r\n' '   '; }
# GNU cut -c does not reliably count UTF-8 characters; decode explicitly.
truncate_summary() {
  python3 -c 'import sys; sys.stdout.buffer.write(sys.stdin.buffer.read().decode("utf-8", "replace")[:120].encode("utf-8"))'
}
job=$(clean_field "$job")
job_exit=$(clean_field "$job_exit")
log_file=$(clean_field "$log_file")
summary=$(clean_field "$summary" | truncate_summary)
if [ "$verdict_only" -eq 1 ]; then
  printf '%s\t%s\n' "$state" "$summary"
  exit 0
fi
if [ -n "$first_summary" ]; then
  first_summary=$(clean_field "$first_summary" | truncate_summary)
  summary="${summary}；已自动重跑 1 次，首次：${first_summary}"
fi

failures=0
if [ "$state" = fail ]; then
  if [ -r "$status_file" ]; then
    failures=$(awk -F '\t' -v job="$job" '
      $2 == job { if ($3 == "fail") n++; else n = 0 }
      END { print n + 0 }
    ' "$status_file")
  fi
  failures=$((failures + 1))
fi
if mkdir -p "$alert_dir"; then
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$job" "$state" "$job_exit" \
    "$summary" "$log_file" >> "$status_file"
fi

if [ "$state" = fail ]; then
  body="job=${job}，exit=${job_exit}，连续失败 ${failures} 次：${summary}"
  if [ "${INFOOS_ALERT_DRY_RUN:-0}" = 1 ]; then
    printf 'InfoOS 定时任务失败：%s\n' "$body"
  elif [ "$(uname -s)" = Darwin ] && command -v osascript >/dev/null 2>&1; then
    osascript -e 'on run argv' \
      -e 'display notification (item 1 of argv) with title "InfoOS 定时任务失败"' \
      -e 'end run' -- "$body" >/dev/null 2>&1
  else
    # Headless Linux: status.tsv plus stderr/journal is the notification channel.
    printf 'InfoOS 定时任务失败：%s\n' "$body" >&2
  fi
fi
exit 0
