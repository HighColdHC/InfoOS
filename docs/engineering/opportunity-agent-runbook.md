# 外部 agent 运行手册

InfoOS 自身不调用 LLM。用户需要自备**付费外部 agent**及其账号、额度与登录环境；目前提供 [Codex CLI 启动器](../../deploy/agent/run-knowledge.sh)。正式集成接口是[提示词模板](../../deploy/agent/knowledge-prompt.md) + `/mcp`，不保证任意 agent 开箱即用，不捆绑账号或费用。使用其他 agent 时须自行适配鉴权、工具白名单、超时和结果核对。

## 前提和权限

确认目标 Appliance 健康、版本与画像已经由用户确认，外部 agent 能连接目标 `/mcp`。Codex CLI 须已由用户完成付费账号登录。启动器和告警需要 Bash、Python 3、date（Linux GNU coreutils / macOS BSD）、标准 Unix 工具；有 GNU `timeout` 时使用它，否则需要 Perl。安装还需要 Git；Linux 定时需要支持带时区 OnCalendar 的 systemd user manager 和已安装的 IANA tzdata，macOS 定时需要当前用户的 launchd 图形会话。

定时 Token **只授予 `intelligence:read`、`evaluation:write`**，不授予采集、来源、画像修改权限。`verification:write` 只给交互式用户调研，用于用户明确决定后的 `set_research_status` 等操作，不能加进定时 Token。用户自行把 Token 放入受保护的文件（0600），默认 `$CONFIG_DIR/mcp-agent.token`；安装器不读取、复制或修改它。不要打印凭据，不使用 `bash -x`。

每轮固定 `since` 和启动时刻 `until`，最多分诊150张、拆解60张。画像和版本从 `get_profile/get_evaluation_rubric/get_knowledge_schema` 获取并固定；归并前检索相似知识卡。达到上限不代表窗口清空，不自动推进 `since`；`--now` 只跳过时刻筛选，不更改窗口、上限和权限。

## 共用配置

把 [agent.env.example](../../deploy/agent/agent.env.example) 复制到用户自己的配置目录并设为0600，填写服务地址和 UTC 起始时刻。配置是**可信的本地 Bash 文件**，只写非秘密配置，不能来自信息卡或外部材料。所有目录和可执行文件路径请使用绝对路径。加载顺序为：显式环境变量 → 配置文件 → 默认值。

下表中的 `CONFIG_DIR` 表示 `INFOOS_AGENT_CONFIG_DIR`；`XDG_CONFIG_HOME` 默认 `$HOME/.config`，`XDG_STATE_HOME` 默认 `$HOME/.local/state`。这些都是用户自己的目录。

| 变量 | 默认值 | 含义 |
|---|---|---|
| `INFOOS_AGENT_CONFIG_DIR` | `$XDG_CONFIG_HOME/infoos` | 配置和共享目录锁所在目录 |
| `INFOOS_AGENT_CONFIG_FILE` | `$CONFIG_DIR/agent.env` | 配置文件位置；自定义时先从环境指定 |
| `INFOOS_MCP_URL` | 无，必填 | 目标 `/mcp` 的完整 HTTP(S) 地址；非 loopback 连接应使用 HTTPS |
| `INFOOS_MCP_TOKEN_FILE` | `$CONFIG_DIR/mcp-agent.token` | 用户自备 Token 文件，只在运行时读取 |
| `INFOOS_AGENT_MODEL` | 空，采用 Codex CLI 默认模型 | 需要可复现时显式固定模型 |
| `INFOOS_AGENT_EFFORT` | `medium` | 推理强度，须被所选模型支持 |
| `INFOOS_AGENT_SERVICE_TIER` | `default` | Codex 服务层级 |
| `INFOOS_AGENT_CODEX_BIN` | `codex` | Codex 可执行文件名或绝对路径 |
| `INFOOS_AGENT_PATH` | Linux：`$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin`；macOS 另含 `/opt/homebrew/bin` | 定时任务的 PATH；自定义安装位置须加入此处 |
| `INFOOS_KNOWLEDGE_SINCE` | 无，必填 | 部署起始时刻 `YYYY-MM-DDTHH:MM:SSZ`；安装时也可传 `--since` 覆盖 |
| `INFOOS_KNOWLEDGE_TIMEZONE` | `UTC` | 已安装的 IANA 时区名 |
| `INFOOS_KNOWLEDGE_HOURS` | `06` | 空格分隔的两位小时（00–23） |
| `INFOOS_KNOWLEDGE_MINUTE` | `30` | 两位分钟（00–59） |
| `INFOOS_KNOWLEDGE_TIMEOUT_SEC` | `3300` | 包含重试等待在内的总运行预算 |
| `INFOOS_AGENT_RETRY_DELAY_SEC` | `300` | 唯一一次自动重跑前的等待秒数 |
| `INFOOS_KNOWLEDGE_INSTALL_DIR` | `$CONFIG_DIR/knowledge` | 固定版本快照安装目录 |
| `INFOOS_KNOWLEDGE_LOG_DIR` | Linux：`$XDG_STATE_HOME/infoos/knowledge`；macOS：`$HOME/Library/Logs/infoos-knowledge` | 每次 agent 日志；macOS 还含 launchd 输出 |
| `INFOOS_AGENT_ALERT_DIR` | Linux：`$XDG_STATE_HOME/infoos/agent-alerts`；macOS：`$HOME/Library/Logs/infoos-agent-alerts` | 告警状态 `status.tsv` |
| `INFOOS_LAUNCHD_DIR` | `$HOME/Library/LaunchAgents` | macOS plist 目录 |
| `INFOOS_SYSTEMD_USER_DIR` | `$XDG_CONFIG_HOME/systemd/user` | Linux user service/timer 目录 |

`INFOOS_MCP_TOKEN` 是运行时传给 Codex 的秘密环境变量；若已由用户安全注入则无需读 Token 文件，不会进入安装快照。`INFOOS_ALERT_DRY_RUN=1` 仅供隔离测试时把通知写到 stdout。

安装器只接受干净 checkout。两种安装器读取同一套配置，复制启动器、配置加载器、告警脚本、提示词、`source-revision` 和非秘密 `installed.env` 到安装目录；文件权限为0600，目录及 shell 文件为0700。定时任务读取该快照，不随仓库切分支变化；之后修改源配置或调度参数须重新安装。`--copy-only` 只生成快照，不写 plist/unit，不调用调度器。自定义目录时，停用也须使用同一配置。

## macOS 安装、调度和回退

以下命令由用户在准备好自己的配置后执行；先备份现有安装目录、配置和 plist，再更新。示例采用默认配置位置：

```bash
bash deploy/agent/install-knowledge-agent.sh
# 或显式固定配置文件/起始窗口：
INFOOS_AGENT_CONFIG_FILE="$HOME/.config/infoos/agent.env" \
  bash deploy/agent/install-knowledge-agent.sh --since 2026-01-01T00:00:00Z
launchctl print "gui/$(id -u)/com.infoos.knowledge"
```

保留标签 `com.infoos.knowledge`。launchd 每分钟唤醒，启动器只在配置时区的小时和分钟执行，兼容夏令时和非整数 UTC 偏移；其他分钟不读 Token、不启动 agent。错过时刻不承诺补跑。安装会停用旧 `com.infoos.evaluate` 和 `com.infoos.opportunities` 标签，保留其本机 plist；这些旧入口已退役，仓库不再提供安装器或提示词。

日志在 `INFOOS_KNOWLEDGE_LOG_DIR`，包括 `launchd.out`、`launchd.err`、各次 `.log`；状态在 `INFOOS_AGENT_ALERT_DIR/status.tsv`。macOS 失败通知是尽力发送，受通知权限和图形会话影响；状态记录不证明用户已看到通知。

停用：

```bash
bash deploy/agent/install-knowledge-agent.sh --disable
```

停用会卸载并禁用调度；已经启动的 CLI 是否退出须另外检查。回退时先停用，恢复备份的安装目录、非秘密配置和 plist，再执行：

```bash
launchctl enable "gui/$(id -u)/com.infoos.knowledge"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/com.infoos.knowledge.plist"
```

自定义 plist 目录时替换最后路径。旧 evaluate/opportunities 的本机备份仅供维护者明确选择的历史回退，不作为新用户安装路径。

## Linux systemd user 安装、调度和回退

准备自己的配置后，用普通用户执行：

```bash
bash deploy/agent/install-knowledge-agent-systemd.sh
systemctl --user status infoos-knowledge.timer
systemctl --user list-timers infoos-knowledge.timer
journalctl --user -u infoos-knowledge.service
# 手动验证一轮，会真实调用付费 agent 和 MCP：
systemctl --user start infoos-knowledge.service
```

[service 模板](../../deploy/agent/systemd/infoos-knowledge.service.in)和 [timer 模板](../../deploy/agent/systemd/infoos-knowledge.timer.in)由安装器渲染，不要直接把 `.in` 文件交给 systemd。`OnCalendar=*-*-* HH[,HH]:MM:00 IANA时区` 与共用配置一致；`Persistent=true` 会在用户 manager 恢复后补一次错过的触发，因此 service 使用 `--now`，不会被运行时段过滤掉。重复的 timer 触发不会启动重叠的同名 oneshot service；目录锁还防止手动运行与定时任务重叠。

`TimeoutStartSec` 等于配置的总预算，`TimeoutStopSec=30`、`KillMode=control-group` 会终止超时服务的子进程。启动器自身使用剩余预算约束每次 CLI，重试不重置窗口或截止时间；GNU timeout 另有30秒强制终止宽限。每次 agent 日志位于 `INFOOS_KNOWLEDGE_LOG_DIR`，启动器输出和 Linux 无桌面失败提示进入 journal，状态仍写到 `INFOOS_AGENT_ALERT_DIR/status.tsv`。Linux 不依赖 osascript。

user manager 只在用户会话可用时运行。若需要退出登录后持续调度，用户或管理员须按主机策略另行启用 linger（`loginctl enable-linger`）；安装器不修改 linger、不使用 root。

停用（停止 timer 和当前 service，保留快照、unit、日志及 Token）：

```bash
bash deploy/agent/install-knowledge-agent-systemd.sh --disable
```

更新前备份安装目录、配置及两个生成的 unit。回退时先停用，恢复备份，再执行：

```bash
systemctl --user daemon-reload
systemctl --user enable --now infoos-knowledge.timer
```

自定义安装/unit 目录时恢复原路径；安装器用绝对路径 `systemctl --user link` 支持自定义 unit 目录。回退启动器不回滚已经写入 InfoOS 的分诊或知识卡。

## MCP 最短调用示例

下面是调用顺序伪代码，使用实际 MCP 工具名和参数；`triage_items`、`extraction_items` 由真实卡片及返回的标准生成，不能提交占位值或空数组。知识卡接口见 [knowledge_tools.py](../../backend/api/mcp/knowledge_tools.py)，分诊见 [evaluation_tools.py](../../backend/api/mcp/evaluation_tools.py)。

```python
profile = get_profile()
rubric = get_evaluation_rubric()
schema = get_knowledge_schema()
v = profile["profile_version"]
assert rubric["profile_version"] == schema["profile_version"] == v
# 固定 UTC 窗口；until 是本轮启动时刻，不在续页或重试时改变。
window = {"since": SINCE, "until": UNTIL}
page = list_new_cards(**window, only_unevaluated=True, limit=20)
# card_ids 取自 page 返回的真实候选；续页传返回的 cursor。
cards = get_cards(card_ids=card_ids)
# truncated 且证据不足时 get_card(card_id=...)；按 rubric 填全部字段。
# triage_items 每条携带 profile_version=v；一批1–20条。
triage_receipt = submit_evaluations(evaluator=EVALUATOR, items=triage_items)
# 逐条核对 results[*].status、reason 和 warnings；有 rejected 停止并报告。
page = list_extraction_candidates(**window, limit=20)
cards = get_cards(card_ids=extraction_card_ids)
similar = search_knowledge_cards(kind=KIND, query=KEYWORDS, limit=20)
# 每张先检索再选 new/merge/update/conflict；类型字段放 fields，共有字段放顶层。
# extraction_items 每条携带 card_id、profile_version=v、outcome 及对应内容；一批1–10条。
receipt = submit_knowledge_extractions(evaluator=EVALUATOR, items=extraction_items)
for result in receipt["results"]:
    assert result["status"] == "accepted"  # rejected 时保留 reason，不能称完成
    for card in result["cards"]:
        persisted = get_knowledge_card(knowledge_id=card["knowledge_id"])
        # 核对持久字段、原卡 card_id、quotes 和归并结果；nothing 的 cards 为空。
```

每页读写后再取下一页。画像、rubric 或 extraction 版本变化、游标失效、HTTP/工具错误时停止；不重置窗口绕过错误，不自动重试不确定写回。`accepted` 是工具回执；知识卡归库须回读验证，最终阅读另在阅读界面验收。知识卡写回不触发 finalize；分诊涉及的信息卡 finalize 须另核对任务结果。

## 重试、锁与验收边界

启动器使用 `$CONFIG_DIR/.knowledge.lock` 目录锁。首次失败、日志没有任何 `mcp: infoos/submit_` 调用且耗时少于600秒、剩余预算足够时，等待配置秒数后最多重跑一次。已尝试提交或结果不确定时不自动重跑，须交互式回读。强制断电或 SIGKILL 可能留下目录锁；先确认没有运行中的 CLI/service，再人工删除该空锁目录，不能在任务活动时删除。

告警依据退出码、日志与最后一段报告判定；工具错误、rejected 或缺少有效结果判失败，写入状态。告警失败不改变 CLI 的退出码；CLI 为0而业务报告失败时以状态和日志为准。`--verdict-only` 只输出判定，不写状态或通知。告警不检测机器停机、启动前失败或未能走到告警步骤的情况。

安装快照、调度注册、手动真实调用、自然定时触发、持久回读、阅读和通知展示须分别验收。空候选、只有nothing或窗口无剩余，不证明真实知识卡归库；mock、静态语法检查及其他操作系统的测试也不能替代真实 Linux 定时触发。此手册提供安装步骤，不构成已完成任何实例部署的证明。
