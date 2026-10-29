# 旧表读取解耦（任务 G）

本文记录当前旧表读取例外与已完成的代码解耦。持久表、schema 和历史 ContentBlock 保留；本次未访问 Appliance。

## 已解耦的读取

- 任务投影：`task_manager_context.py` 不加载旧 `decision_records`、`output_artifacts`、`user_actions`。`/api/task-manager` 已下线，整体任务管理视图及其链路汇总已删除；任务列表与 `GET /api/task-batches/{batch_id}` 复用完整单任务投影，批次仅批量读取自身任务和关联卡片上下文，不输出 `product_artifacts`。历史任务仍显示任务自身状态与 `artifacts_summary`，不把旧决策展示为当前产物。
- 可靠性：`reliability.intelligence` 移除旧 `annotations`、`scores`、`decisions`、`valid_outputs`、`output_conversion_rate`、`local_actions`、`topics`、`events`、`feedback_events`、`summaries`；保留 `cards`，新增七日窗口的 `successful_source_runs`、`raw_items_from_successful_runs`、`cards_from_successful_runs`、`raw_to_card_yield`。`product_yield` 的原有抓取条数、卡片数和转化率键改为同一持久关联计数，移除旧摘要校验键；`degradation` 保留媒体缺口，移除旧 context pack 键。`pipeline` 移除 `fetch_to_output_p50_seconds` 和 `fetch_to_output_p95_seconds`。当前前端无这些字段的消费者。新卡片计数要求 `run_history → raw_items → cards` 的持久关联。
- 来源质量：`SourceQualityQueryRepository` 不再查询 `topic_cards`，旧 `/api/sources/quality*` 仍可用；其 `dimensions.topic_relevance` 现在固定为 `0.0`。`/api/sources/quality` 的 `cross_source_validation_score` 因不再加载旧 topic 关系而为 `0`，其余卡片与来源自身证据照常计算。`/api/sources/quality/{source_id}` 与 `/api/sources/quality/summary` 的 `dimensions.topic_relevance` 为 `0.0`。
- Source Run 追踪：现行追踪不再查询旧 annotation、score、decision、output、topic membership；对应历史产物数组保持空数组，任务与卡片、block、asset、现存卡片解读仍可读。旧记录的任务状态和任务自身摘要仍可显示。
- 旧 `workers/release/rc_audit.py` 没有生产或脚本调用方，已随遗留模块清理删除。任务审计修复与现行 worker 入口不受影响。

## 旧 SQL 引用白名单

`tests/infoos/test_legacy_sql_read_guard.py` 对 `backend/**/*.py` 的 `from/join` 旧表名做正则扫描。当前白名单只剩以下文件。无调用方的 semantic_loop/events 仓库已删除，历史数据兼容测试直接在临时库准备旧行：

| 文件 | 保留原因 |
|---|---|
| `application/tasks/chain_outputs.py` | `scripts/run-infoos-live-source-acceptance.py` 仍调用的旧链产出证据 |
| `infrastructure/storage/topics.py` | 反馈迁移脚本经 `resolve_feedback_topic` 查历史 topic |
| `infrastructure/storage/user_actions.py` | `scripts/migrate-infoos-feedback.py` 的反馈迁移写入与兼容读取 |
| `infrastructure/storage/migrations/v0002_legacy_backfills.py` | 历史数据迁移兼容，不是运行期投影 |

白名单是文件级的，不代表整文件可删除。测试同时要求白名单与当前扫描结果一致，防止静默扩张或过期例外。

## 剩余维护工作

1. 脚本仍在使用的旧链证据、反馈迁移兼容保留；无人读取的 SearchRepository 与旧 topic 索引写入已删除，旧搜索表保留至维护窗口；后续退役须先明确脚本替代方案与调用方边界。
2. 任务列表/批次上下文仍输出历史 `card_context.ai_insight`，Source Run 追踪仍展示历史解读；本轮不改这些 HTTP 字段或历史内容读取，也不增加 LLM 调用。
3. 旧表行数、最后写入、保留期限、归档可读性与恢复方案留维护窗口核实；本轮不删行、不改写存量数据、不 drop 表、不 VACUUM、不新增列/表或迁移。清理历史表不能随 code-only 部署执行。
