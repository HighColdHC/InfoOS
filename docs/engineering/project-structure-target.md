# InfoOS 当前工程结构标准

状态：当前权威工程结构

本文说明代码应该放在哪里、依赖如何流动以及运行时合同如何映射。产品边界以 [`../product/infoos-intelligence-workbench-target.md`](../product/infoos-intelligence-workbench-target.md) 为准。

## 1. 总体结构

```text
React / Vite
    |
    | HTTP /api/*
    v
FastAPI app / API
    |
    +-- application use cases / workflows
    +-- domain rules
    +-- read models
    +-- infrastructure adapters
    +-- scheduler / durable workers
    v
SQLite + local assets
```

相同应用代码可用于隔离测试和 Docker Appliance；部署形态不得反向侵入 application/domain 合同。

## 2. 目录职责

| 位置 | 职责 |
|---|---|
| `backend/app/` | FastAPI app factory、router 装配、前端产物挂载 |
| `backend/api/` | HTTP 请求解析、鉴权、use case/read model 调用与响应映射 |
| `backend/application/usecases/` | 单个可复用业务动作 |
| `backend/application/workflows/` | 采集、构卡、导出等多步骤编排 |
| `backend/application/tasks/` | durable task 的规划与执行合同 |
| `backend/domain/` | 无 I/O 的来源、卡片、内容、任务、调度与失败规则 |
| `backend/infrastructure/` | SQLite、connector、浏览器、OpenCLI、媒体、运行时和外部系统 adapter |
| `backend/read_models/` | API/UI 只读投影，不触发业务副作用 |
| `backend/workers/` | scheduler 与持久化任务消费者 |
| `frontend/src/` | React 前端；只通过 `/api/*` 使用后端 |
| `clients/save-to-infoos/` | Manifest V3 捕获扩展源码；尚未经真实浏览器端到端验证，不属于当前对外承诺的功能 |
| `deploy/docker/` | Dockerfile、Compose、s6/rootfs、Chrome/OpenCLI 与容器运行面 |
| `deploy/linux/` | 遗留 systemd Web 辅助面的维护资料，不是当前采集入口 |
| `scripts/` | 环境、运维、迁移和验收脚本，不承载第二套业务实现 |
| `tests/infoos/` | 后端产品、运行时和合同测试 |
| `docs/` | 当前合同、操作手册、ADR、证据和归档 |

所有 Python 产品代码都在 `backend/`。不得恢复 `infoos/*` 产品 package、`backend/app/web_compat/` 或已删除路径的 shim。

## 3. 分层依赖

允许的主要方向：

```text
backend.api            -> application / read_models / infrastructure
backend.workers        -> application / domain / infrastructure
backend.application    -> domain / infrastructure
backend.read_models    -> domain / infrastructure
backend.infrastructure -> external systems / SQLite / infrastructure peers
backend.domain         -> standard library / domain peers
```

约束：

- route 只解析请求、调用用例或投影、映射响应；
- application 不导入 API、worker 或 read model；API 与 worker 不互相导入；
- domain 不连接 SQLite、不调用外部系统、不承担 UI 格式化；
- read model 只读，不启动 Chrome、抓取、富化或 LLM；
- repository/schema 不接收 FastAPI request，不执行 OpenCLI；
- scheduler 只规划到期任务；业务执行统一由 durable worker 完成。

## 4. 统一领域链

持续来源、用户主动捕获和注册表限定的按需调研统一进入：

```text
Source / system-managed capture source
  -> SourceRun
  -> RawItem
  -> InformationCard
  -> ContentBlock / CardAsset
```

SQLite 是 source、run、task、raw item、information card、ContentBlock 和 CardAsset 的运行时真相源。不得恢复 YAML/JSONL source-execution fallback；`config_path` 只能用于诊断。

时间事实分为 `raw_items.first_seen_at`（不可变首次入库）、规范化的 `published_at`（来源发布时间，无法解析则 unknown）和 `cards.updated_at`（卡片最后变更）。`domain/timestamps.py` 统一解析绝对时间；历史原始表示仍保留，SQL 读连接可使用 `source_timestamp()`。CardRepository 将 first_seen_at 作为只读关联字段投影，不能用历史 captured_at 猜首次新增，也不通过时间显示修复触发历史内容重新处理。

媒体 stage 输入只包含媒体事实和处理策略，不包含处理状态、错误和更新时间；正常派发与 reconcile 共用已有产物检查。capture 根任务的终态和 receipt/run 状态在同一事务结束，并保留真实对象标识。

平台能力由 `backend/domain/sources/registry.py` 统一登记。当前 10 个可配置平台是 X、YouTube、RSS、Reddit、GitHub、Hacker News、Product Hunt、小红书、抖音和生财有术。Google 是辅助登录检查，公众号以普通 `rss` 来源接入第三方公开 RSS，`web` 是 capture-only；Google 与 Web 不计入 10 平台。可配置不代表官方支持：Reddit、知识星球、小红书、抖音、X、YouTube 登录型用途及 home_feed 为实验性；官方范围与登录边界见 [README](../../README.md#实验性平台)。

生财有术当前登记为 `platform=zsxq`，来源类型为 `topics`、`opportunity`、`keyword`：`topics` 通过 OpenCLI 帖子流采集普通帖与精华帖，`opportunity` 读取群菜单“风向标”标签页（hashtag 接口），`zsxq:keyword` 声明 `research_capable=true`。`backend/domain/sources/zsxq.py` 负责参数规则，`deploy/docker/opencli-adapters/zsxq/` 承担容器内采集；旧 `scys` 只保留历史数据辨识，不可配置或调度。

同星球有启用的 `opportunity(all)` 来源时，`topics` 不入库标签为精确“风向标”的帖子；含“风向标”的变体标签照常入库并进商机候选。`keyword` 的关键词 `*` 配合“全部”或“精华”可按日期范围列帖；登录探针使用 `https://wx.zsxq.com`。飞书引用须保留原帖/文档 URL 与读取状态，权限失败明确展示，不以 teaser 冒充全文。

## 5. Durable pipeline

```text
FETCH
  -> CARD_BUILD
  -> CONTENT_ENRICH
  -> MEDIA_DOWNLOAD
  -> media processing / VIDEO_TRANSCRIBE
  -> CARD_FINALIZE
  -> optional WEBHOOK_DELIVER
```

`backend.workers.task_worker` 是统一消费者。Docker Appliance 按 `public_fetch`、`browser_fetch`、`urgent_media`、`card_pipeline`、`gpu_asr`、`maintenance_io` 六个持久化 `queue_name` 启动角色进程，共享同一 `tasks` 表并原子认领。`cloud_ai` 仅保留为历史队列名，不启动 worker。

scheduler 每个 UTC 小时幂等规划 `PIPELINE_RECONCILE`；对账由 worker 执行，用 SQLite keyset cursor 修复信息卡链中的缺失任务和终态矛盾。它不能启动或扩展旧 semantic、score、decision、output 链。

已核清零调用的遗留 API 及专属投影、用例和仓库代码已删除；仍被脚本、保留入口使用的旧类型与兼容代码保留，历史表、迁移与任务枚举不变。新工作不得继续扩展旧链；后续退役仍须确认数据与调用方边界。

`backend/app/` 不再聚合导出 repository 或模型，调用方从 storage/domain 的真实位置导入。旧 CardView、list_cards/get_card 读模型、decision/studio/topic 投影、事件聚类、来源 CSV 导入、旧 readiness/forecast 和无人调用的回补/发布审计入口已删除。`read_models/cards.py` 只保留现行卡片读页共用的来源展示函数；`human_cards.is_user_facing_content_block` 仍用于 Source Run 追踪，继续保留。

运行入口之外仍保留：`chain_outputs` 与 `domain/outputs`（live-source-acceptance）、`migrate_feedback` 与 feedback/domain feedback/TopicRepository/UserActionRepository（反馈迁移脚本）、`reliability` 与 reliability_query（可靠性报告）、`card_text_search`（索引重建脚本）。这些是脚本兼容依赖，不接入现行 worker 调度。`finalization`、`daily_brief`、`exports` 转导出仍有现行 application/API/MCP 调用方，按调用方保留；旧 SearchRepository 已移除，卡片检索由现行 FTS 投影提供；旧 ai_insight 任务上下文与 Source Run 的解读查询仍保留历史读取，但无业务调用的 CardInsightRepository 已删除。这些兼容读取不代表 InfoOS 调用 LLM。

## 6. ContentBlock 与媒体合同

- 跨平台内容优先映射到共享 `block_kind`；前端按 `block_kind` 分发，不按 platform 分发整卡 renderer；
- 平台私有字段进入 `raw_payload`，没有跨平台需求时不新增 schema 列；
- OCR、转录、历史图片说明与翻译、agent 写回的摘要与评估是独立 derived/intelligence block；
- `card_assets` 保存本地文件事实，`card_content_blocks` 保存用户或 AI 可读内容；
- GPU ASR 只产生转录事实；InfoOS 不调用 LLM，摘要与评估由外部 agent 写回；
- 历史 `summary`、`translation` 等 block 保留并继续展示；
- 采集链及 agent 写回后的 `CARD_FINALIZE` 推进同一张卡的版本，不创建第二张卡。

## 7. Browser Runtime 与捕获边界

- 浏览器采集使用 InfoOS 专用 Google Chrome Stable profile，不复用个人 profile；
- Docker Appliance 内 Chrome、OpenCLI 和 Bridge 由同一非 root UID 持有，保持正常 sandbox；
- passive health/readiness 不启动 Chrome、不新建 tab、不调用采集；
- 当前会话可用与登录态持久化是两个状态，持久化必须经过同 profile 重启前后功能探针；
- Save to InfoOS 扩展的设计约束是只经认证产品入口提交捕获，不连接内部运行时；该扩展尚未经真实浏览器端到端验证；
- raw CDP、VNC/noVNC、OpenCLI daemon 和 supervisor 端口不得发布。

## 8. 正式入口

```text
uvicorn backend.app.main:app
python -m backend.application.workflows.source_batch --base-dir .
python -m backend.workers.scheduler.cycle --base-dir .
python -m backend.workers.task_worker --base-dir .
```

`backend.application.workflows.run_once` 和直接 `fetch` 只允许通过 `make diagnostics-run` / `make diagnostics-fetch` 在隔离数据目录诊断。

## 9. 结构变更纪律

修改前先声明目标环境并检查 `git status --short`。以职责和依赖决定拆分，保持 route 精简、domain 纯净、SQLite 单一真相。SQLite CRUD/schema 变更先读 [CRUD 手册](sqlite-crud-playbook.md)与[迁移手册](sqlite-migrations.md)。

### 输入、失败与测试

- 在 HTTP/MCP 请求体、外部平台/OpenCLI/子进程输出、文件与 JSON 解析，以及 schema 不保证结构的历史 JSON 列校验与兜底。
- 内部信任类型与 schema：不对 NOT NULL 列、dataclass 字段、内部构造的 dict 或非可空 TS 类型重复写 `str(x or "")`、`x or {}`、`getattr` 默认值、`isinstance`、`?.` 或 `??`；不写 SPA 的 SSR 守卫、不会发生的分支，也不在 route/usecase/repository 重复校验同一值。
- 失败上抛给任务层持久化，或记录显式状态（如 `enrichment_gap`）。宽泛 `except Exception` 只用于事务回滚后 `raise`、worker 循环与心跳顶层、进程边界协议；例外写明 `# noqa: BLE001 - <原因>`。异常规则由 `pyproject.toml` 中的 ruff 配置检查。
- 不写断言某模块/文件已删除的墓碑测试、逐字复述源码或配置文本的测试（安全边界除外），不为防御分支补测试；新增测试与验证范围遵循根目录 [AGENTS.md](../../AGENTS.md)。

### 检查工具

分层由 `scripts/check_layer_deps.py` 检查，尺寸分类、阈值与入口文件余量以 `scripts/check_file_size.py` 为准。超限例外在文件头写明 `noqa: file-size` 与原因，不借小改动治理无关超限文件。命令见根目录 [AGENTS.md](../../AGENTS.md)。
