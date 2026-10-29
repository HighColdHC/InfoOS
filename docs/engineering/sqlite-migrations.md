# SQLite 版本化迁移

B1 起，`backend/infrastructure/storage/migrations/` 是 SQLite 结构与历史回填的迁移来源。已发布的 `v0001_core.sql`、`v0001_collector.sql` 和各编号迁移不可改写；旧的 `storage/schema.sql` 与 `storage/schema_collector.sql` 已由冻结基线取代。CRUD 与事务规则仍见 `sqlite-crud-playbook.md`。

## v0022：退役内置 LLM 熔断清理

`v0022_retire_llm_circuits.sql` 删除全部 `provider` 熔断，以及已退役的 `scys` / `wechat` 平台熔断。每条删除在同一迁移事务内追加一条 `task_events.runtime_circuit_retired` 审计，保存 scope、原状态、原因代码和失败次数，不保存配置或凭据。版本提交与审计、删除一起回滚；重复启动无重复审计。历史任务事件保持可读，不因旧 provider 事件把当前状态判为异常。

状态读取不再使用内置 LLM provider；`providers` 响应字段保留为空列表以兼容客户端。运行保护列表只展示 registry 当前平台及按来源隔离的 RSS 熔断。平台采集熔断、资源租约、Webhook 保护均保留。无新表或索引；每条删除多写一条审计及现有事件索引。旧 `state/config/llm.json` 不由迁移读取或删除。发布须使用 `--update-kind data-migration`，先在一致性备份副本上预演并保留恢复点；旧镜像不能直接打开 v22 数据卷。

## 新增版本

1. 在 `migrations/` 追加连续编号的 `vNNNN_name.sql`（DDL 或无需条件编排的数据更新）或 `vNNNN_name.py`（需条件判断、历史数据回填），并将运行器的 `LATEST_VERSION` 推进到该编号。不要在已发布版本中补写新定义。
2. SQL 用 `sqlite3.complete_statement` 累积完整语句，逐条 `conn.execute`；不要用 `split(';')` 或 `executescript`。迁移在 `BEGIN IMMEDIATE` 后重读 `user_version`，检查允许的旧结构，完成并校验后于同一事务写入新版本。
3. 长回填按稳定主键分批处理，默认每批不超过 500 行。每批行修改与持久进度同事务提交，批间释放写锁；中断重跑不得重复增加卡片版本或丢失 FTS 条目。原有 `migration.*` 标记只接受 `complete`，值异常应拒绝启动。
4. 关键约束与索引漂移（主键、唯一性、外键、`NOT NULL`、FTS）先拒绝并报告；一般性能索引差异只告警。结构比较按列名及属性、索引键列名顺序、唯一性和部分索引条件，不比较列位置、`index_xinfo.cid` 或建表 SQL 文本。

## 入口与验证

Web、worker、scheduler shell loop 和写入型 CLI 在启动时显式迁移。请求、任务处理及只读/预览 CLI 只调用 `ensure_database_initialized`，不得隐式创建或迁移数据库。

新增版本至少在隔离目录验证全新库、受支持历史库、重复启动、并发启动、故障中断续跑及高版本拒绝。部署前在一致性备份副本上执行 `python3 scripts/migration-dry-run.py --db <副本路径>`，核对版本、批次、`quick_check` 和主要表计数；涉及迁移的发布使用 `--update-kind data-migration`。

## v0003：任务读取索引

`v0003_task_read_indexes.py` 只追加五个索引并校验键语义，不改表或既有索引：

| 索引 | 服务的读取 | 写入代价 |
|---|---|---|
| `idx_tasks_created_at_task_id` | 无筛选最近任务按 `(created_at DESC,task_id)` 直接 LIMIT | 每个 task 插入/删除维护一棵 B-tree |
| `idx_tasks_fetch_schedule` | 无已提交 run 的 fetch 按任务派生时间索引推进，partial `task_type='fetch'` | 只维护 fetch；其 `artifacts_summary` 或调度时间更新时也需改索引 |
| `idx_run_history_scheduled_run` | 已提交运行按 `scheduled_for` 时间索引推进；同时间戳再按 task ID 局部稳定排序 | 每条 run_history 插入/删除维护一棵 B-tree |
| `idx_acquisition_checkpoints_run_task` | 按 run_id 将时间流点查回 fetch task | 每条 checkpoint 插入/删除维护一棵 B-tree |
| `idx_tasks_fetch_target` | 计划来源去重时按 source ID 点查已有 fetch，避免每个来源扫描全部 fetch | 只维护 fetch 插入/删除或 target_id 更新 |

四条候选流（已提交、未提交、待执行 fetch、计划来源）按原 `scheduled_at` 和 ID 顺序归并，只把候选页的 task ID 交给事件与流水线分类查询。`task_ids_for_card` 复用已有的 `idx_card_assets_card` 与 `idx_tasks_target`，不为此新增索引。

在 20 万 tasks、80 万 events、2 万 fetch 且半数 fetch 有提交记录的合成库中，五个新索引依次占 16.58、1.18、0.55、0.37、0.38 MiB；只代表本地样本。100 个计划来源时，第五索引使完整目录热中位从 763.2 ms 降至 56.3 ms。上线前用 `scripts/benchmark-task-read-paths.py --db <备份副本>` 对比 v2 与 v3，并用 `--catalog-offset 1000` 复核深页索引范围；`--migrate-copy` 仅在显式副本上预演。旧镜像只接受最高 v2；v3 发布必须使用 `--update-kind data-migration` 并保留可验证恢复点，不能直接复用 v3 卷启动旧镜像。

来源运行目录保留原先的 `scheduled_at` 排序、状态和其他筛选语义。返回新增 `counts_exact`：为 `true` 时 `total` 与 `summary` 覆盖全部匹配候选；为 `false` 时 `total` 是已确认下界，`summary` 仅覆盖本次已扫描、已分类范围，界面显示“至少”。`next_offset` 由额外读取一个匹配条目决定。按状态筛选仍分批分类：首批 `max(32, limit*2)`，后续翻倍，上限取不超过 2000 的旧批大小整数倍（首批已大于 2000 时不再增长）；计数仍截到旧固定批次凑足一页加一项时的边界，预读的额外候选不计入 `summary`、`total` 或 `counts_exact`。匹配稀疏或深页仍需读取更多候选；同时间戳的大量已提交运行会增加局部排序成本。此优化为 code-only，不新增迁移或写入状态列。

分析报告中其余随历史增长的路径暂列后续：`webhook_replay_candidates` 需要先确认失败/阻塞高命中负载及 JSON token 过滤成本，再决定是否加索引；`reconciliation_parent_page` 已按主键范围扫描，但稀疏 JSON 匹配会扩大扫描；`tasks_by_target` 已点查目标索引，单目标历史增长需另定分页合同。本版不为这些路径增加写入成本。

## v0004：付费调用回执与供应商预算

`v0004_provider_receipts.sql` 新增 `provider_receipts`（按逻辑输入唯一，保存无密钥请求摘要、原始响应、usage 与恢复状态）和 `provider_receipt_attempts`（每次实际发起的请求一行）。`idx_provider_receipt_attempts_budget` 支持按供应商和发起时间统计分钟、小时、UTC 日窗口；`idx_provider_receipts_status_updated` 支持过期 pending 与 unknown 维护。每次供应商调用至少增加一条 attempt，并更新回执及两棵索引；保存原始 JSON 响应会增加 SQLite 空间。

供应商三个可选上限默认 `0`（不限）；旧 `daily_budget` 只保留配置与运维展示，不映射为供应商上限，以免旧默认值阻断现有任务。`requests_per_minute` 仍为旧配置字段；现有 `processor_throttle` 只限制 FETCH，不与供应商预算重复。回执内发送后连接中断标 `unknown`，10 分钟过期 pending 转 unknown，30 分钟后仅首次 unknown 自动放行一次，之后需运维手动放行。自动或手动放行会唤醒关联的待运行任务。此版本必须用 `--update-kind data-migration` 发布，并保留可验证恢复点；旧镜像不能直接打开 v4 数据卷。

## v0005：原料内容修订

`v0005_raw_item_revisions.sql` 为 `raw_items` 增加可空 `content_hash` 和默认 0 的 `revision`，并新增 `raw_item_revisions`，以 `(connector_id, raw_item_id, revision)` 为主键、同一原料的内容哈希为唯一约束。无额外性能索引；唯一约束在每次真实内容修订时维护 B-tree，修订表另存标题、正文和观测时间，会增加 SQLite 写入与占用。计数与采集时间不参与内容哈希；重复内容、历史回摆和仅 U+FFFD 替换的观测不新增修订或建卡任务。真实新内容才更新当前原料并排入建卡。

`comments`、`score` 只有可解析为数值时按计数处理；评论正文列表及非数值同名字段仍属内容。计数变化只更新 `raw_items.raw_payload`；卡片预览详情按 `(raw_connector_id, raw_item_id)` 读取最新计数，不升卡片版本。

迁移不回填既有原料。旧行首次再次观测时以原库存储内容建立 revision 1 基线，再与本次观测正常比较：内容相同则不重建，真实变化则保存 revision 2、更新原料并排入建卡。仅建立基线本身不触发重建。此版本须用 `--update-kind data-migration` 发布，旧镜像不能直接打开 v5 数据卷。

## v0006：Webhook 历史卡片开关

`v0006_webhook_backfill.sql` 在 `api_tokens` 增加 `webhook_include_backfill`（默认 0），不新增索引。更新 Token 配置时多写一个整数；投递规划读取此列，现有 Token 默认不接收首次抓取或发现时已超过 48 小时的卡片。旧文判断使用既有 `raw_items.first_seen_at`、卡片 `published_at` 和任务链的 `initial_fetch` 标记，不回填历史数据。发布时间比发现时间晚超过 1 小时按不可信时间处理，原始值仍保留。此版本须用 `--update-kind data-migration` 发布；旧镜像不能直接打开 v6 数据卷。

## v0007：来源建议间隔读取索引

`v0007_source_cadence_index.sql` 只新增 `idx_raw_items_first_seen_source(first_seen_at, source_id)`，支持按近 7 天首次发现时间扫描原料并按来源统计。每条 `raw_items` 插入及 `first_seen_at`/`source_id` 更新会维护一棵 B-tree；不回填、不修改来源配置或 scheduler。建议值只在来源管理读取时计算，排除已记录的 initial fetch 与按发布时间判定的旧文；缺失旧任务首次抓取证据的历史记录无法可靠识别。此版本须用 `--update-kind data-migration` 发布，旧镜像不能直接打开 v7 数据卷。

## v0008：情报层基础表

`v0008_intelligence.sql` 只新增 `interest_profiles`、`card_evaluations`、`daily_briefs` 三张表及两棵评估读取索引，不修改旧表或回填旧数据。画像按递增版本保存 Markdown、解析段落、内容哈希、来源与 UTC 创建时间；重复推送当前内容不写新版本。评估按卡片版本、画像版本和提示词版本唯一，支持时间窗质量/相关性排序及按卡片读取最新结果；日报以 Asia/Shanghai 日期为主键冻结 UTC 时间窗和编号条目。每次评估插入维护唯一约束及两棵读取 B-tree；画像和日报各维护主键索引并保存文本/JSON，增加 SQLite 写入与空间占用。发布必须使用 `--update-kind data-migration` 和可验证恢复点；旧镜像不能直接打开 v8 数据卷。


## v0009：active 卡片原料身份读取索引

`v0009_entry_read_indexes.sql` 只新增 `idx_cards_active_raw(raw_connector_id, raw_item_id, card_id)`（active 部分索引），从时间窗内的原料点查 active 卡片；不回填、不改存储标题、卡片版本或内容哈希。

`raw_items.first_seen_at` 由 repository 写为 UTC 秒级 `YYYY-MM-DDTHH:MM:SSZ`；`card_evaluations.created_at` 由评估 repository 固定为 UTC 六位微秒 `YYYY-MM-DDTHH:MM:SS.ffffffZ`。查询边界在 Python 转为 UTC 秒级 Z，SQL 直接比较和排序原列。时间窗及游标复用 v0007 的 `idx_raw_items_first_seen_source`，每卡最新评估复用 v0008 的 `idx_card_evaluations_card_latest`；不新增时间表达式索引。正文仍在分页/选条后按 ID 批量读取。

active 卡片原料身份或状态变化维护一棵部分索引。发布必须使用 `--update-kind data-migration` 和可验证恢复点；旧镜像不能直接打开 v9 数据卷。

## v0010：captured_at 采集时刻回填

旧连接器把平台发布时间写进 `raw_items.captured_at`，再带进由它构建的 `cards.captured_at`。写进去的格式五花八门：X 原始字符串、Reddit epoch 秒、YouTube 本地化相对时间甚至 URL、仅日期、带偏移量，以及格式合法但与 `published_at` 同一时刻。写入端已由 `backend.domain.timestamps.capture_timestamp` 规范为采集时刻。

`v0010_captured_at_backfill.py` 判定和修复规则：
- 用 `captured_at_needs_repair` 判定：`captured_at` 不是规范 UTC `…Z`，或与 `published_at` 是同一时刻，就需要修复。
- 修复值取同一原料的 `raw_items.first_seen_at`。卡片按 `(raw_connector_id, raw_item_id)` 精确关联；没有原料的卡片保持原值。
- 只改新值与旧值不同的行。不改 `version`、`content_hash`、任务和来源游标；`captured_at` 不参与内容修订或卡片版本。
- 先处理 cards，再处理 raw_items，各自按主键游标分批，默认每批 500 行。游标存在 `settings` 的 `_migration.v0010.*`，与该批改动在同一事务提交，完成后清除；中断后从游标续跑。
- 无新索引。

只读核对：
```bash
python scripts/preview-captured-at-backfill.py <db>
```
迁移前列出待修复数、平台分布和抽样，迁移后应只剩无原料可依的行。发布必须使用 `--update-kind data-migration` 并保留可验证恢复点；旧镜像不能直接打开 v10 数据卷。


## v0011：来源变更审计

`v0011_source_change_audit.sql` 新增 `source_change_audit`：保存来源 ID、create/update/enable/disable 动作、Web/MCP 主体、Token ID、用户请求以及前后配置版本和 UTC 创建时间。MCP 请求原因必填（1–500 字符）；Web 原因与 Token ID 为空。配置写入与审计写入同事务，失败一并回滚；不回填历史操作。审计不以外键绑定来源或 Token，避免删除这些对象时丢失既有证据。

新增 `idx_source_change_audit_source(source_id, created_at DESC)` 和 `idx_source_change_audit_created(created_at DESC)`，分别支持来源审计与全局倒序分页。每次来源改动多写一行并维护主键和两棵索引，增加 SQLite 写入与空间占用。新表属于持久数据结构变化，发布须使用 `--update-kind data-migration` 并保留可验证恢复点；旧镜像不能直接打开 v11 数据卷。

## v0012：商机拆解

`v0012_opportunity_breakdowns.sql` 新增 `opportunity_breakdowns`，以 `(card_id, card_version, lens_version)` 唯一，保存外部 agent、Token、候选规则版本、结构化拆解与首次创建时间；同键覆盖不改 `created_at`。列表字段用 JSON 文本，关联 `cards` 外键。拆解、同类 block 替换与 `CARD_FINALIZE` 入队同事务，不改通用评估表及 rubric。

索引 `idx_opportunity_breakdowns_card(card_id, created_at DESC)` 支持每卡最新拆解，`idx_opportunity_breakdowns_next_step(next_step, created_at DESC)` 支持建议筛选，`idx_opportunity_breakdowns_created(created_at DESC, breakdown_id DESC)` 支持全局倒序游标。`idx_cards_opportunity_raw(raw_connector_id, raw_item_id, card_id)` 支持全部卡状态的原料点查，不能复用只含 active 的 v9 部分索引；候选时间窗复用 `idx_raw_items_first_seen_source`，再按原料 `source_id` 点查来源。实际 RawItem 初筛在 `raw_payload.raw_metadata.prefilter`，同时接受直接 `raw_payload.prefilter`。

每次拆解增加一行及三棵索引维护，卡片原料身份维护一棵索引。新表与索引属于持久结构变化，发布必须使用 `--update-kind data-migration` 并保留一致性恢复点；旧镜像不能直接打开 v12 数据卷。

## v0013：人工核实记录

`v0013_opportunity_verifications.sql` 新增 `opportunity_verifications`，保存卡片、agent 自报记录者、Token、`verification_v1`、接触日期与渠道、结构化核实结论及 UTC 创建时间。只追加，无更新/删除用例；可选 `supersedes` 引用同卡既有记录，更正后原记录保留。`superseded_by` 读时派生为最新直接更正记录 ID。记录、`user_verified.via_agent` 内容块与 `verification:` finalize 任务同事务；不保存联系方式与聊天全文，本期不上传截图。

索引 `idx_opportunity_verifications_card(card_id, created_at DESC)`、`idx_opportunity_verifications_conclusion(conclusion, created_at DESC)`、`idx_opportunity_verifications_created(created_at DESC)` 分别支持按卡、按结论及全局倒序查询；同时间戳以 `verification_id DESC` 确定顺序。每次追加增加一行、一个内容块及持久任务，维护主键与三棵读取索引。新表属于持久结构变化，发布必须使用 `--update-kind data-migration` 并保留一致性恢复点；旧镜像不能直接打开 v13 数据卷。

## v0014：商机依据与人工核实快照

`v0014_opportunity_context.sql` 为 `opportunity_breakdowns` 增加 nullable `profile_version` 与 `raw_item_revision`，记录 agent 判断所用的画像及原料修订；旧行保持 NULL，不伪造历史依据。保留 `(card_id, card_version, lens_version)` 唯一键与同键覆盖。候选已处理状态按最新拆解的画像、视角和原料修订判断；发布时卡片版本递增不会重复入选，原料修订变化会重新入选。

`opportunity_verifications` 新增 nullable `client_verification_id`、`request_content_hash`、`breakdown_id`、`expected_breakdown_hash` 与 `breakdown_snapshot`。首次提交在同一写事务中校验最新拆解的稳定摘要并冻结 JSON 快照；无关联与旧记录保持 NULL。稳定摘要只包含明确保存的拆解字段（包括卡片、画像、原料修订与视角版本），不包含读时派生的当前画像状态。

唯一索引 `idx_opportunity_verifications_client(token_id, client_verification_id)` 为每个 Token 保留独立的客户端提交身份；旧 NULL 不冲突。同 ID 同规范化输入直接重放原结果，不再创建记录、block 或 finalize 任务；不同输入返回冲突。重放查询先于关联拆解解析，后续覆盖判断不破坏旧请求重放。每次首次核实额外维护一棵唯一索引并保存请求摘要及可选快照 JSON，增加 SQLite 写入与空间占用。

发布须使用 `--update-kind data-migration`，部署前在一致性备份副本上预演；旧镜像不能直接打开 v14 数据卷。回退使用既有部署恢复点，不在新 schema 上运行旧镜像。

## v0015：卡片读取键与 trigram 检索

`v0015_card_read_keys.py` 为 `cards` 新增 `sort_ts text not null default ''` 与 nullable `published_ts`，复用 `backend.domain.timestamps` 将原排序键和发布时间过滤键持久化。排序回退按 `(raw_connector_id, raw_item_id)` 取 `raw_items.first_seen_at`；非法发布时间保留 NULL，不改变卡片版本、内容哈希或原始时间。索引 `idx_cards_status_sort(status, sort_ts DESC, card_id DESC)` 支持按状态取最新页；`idx_cards_status_published(status, published_ts)` 支持按状态和发布时间范围缩小候选，避免为过滤读取 `raw_payload` 后的行尾列。

新增 `card_text_fts(body)` 与 `card_block_text_fts(body)`，均为 FTS5 `trigram`、`content=''`、`contentless_delete=1`，分别以卡片和 block 的 rowid 关联。卡片正文按标题、作者、正文、URL 换行拼接；block 排除内部 gap、LLM 上下文、图片/视频及四个平台的通用抓取 linked_article。六个插入/更新/删除触发器维护索引，另有卡片 `source_platform` 更新触发器重算其 block 资格，任意连接均生效。每次相关文本修改需删除旧 rowid 并写入 trigram 项，增加写入和磁盘占用；卡片写入额外点查原料并维护排序、发布时间两棵 B-tree（本次补充发布时间索引增加一棵）；插入/删除维护两棵索引，状态或对应时间键更新时维护相关索引。旧 `infoos_search` 仅清除 card 行，保留 topic 和表结构。

迁移由运行器在 `BEGIN IMMEDIATE` 后重读版本；cards、blocks 按稳定主键、旧 FTS 按 rowid 分批，每批最多 500 行。游标保存在 `settings._migration.v0015.progress`，每批修改与进度一起提交、释放写锁；中断后续跑，全部校验成功后才推进 `user_version=15` 并清除进度。新列、索引、FTS 参数和全部触发器纳入启动校验。

v15 首次发布属于 `data-migration`，旧镜像不能打开 v15 数据卷。已发布的 v0001/v0002/v0015 不改写；当前只移除旧 `SearchRepository` 和生产写入/导出，`infoos_search` 表及历史行继续保留，不新增迁移。当前代码清理可按 code-only 评估，不随部署执行任何维护。

### 离线空间回收与 FTS 重建（方案 b）

继续保持 `cards` / `card_content_blocks` 的 TEXT 主键与 rowid 关联，不增加稳定列。SQLite 的 [VACUUM 文档](https://www.sqlite.org/lang_vacuum.html) 说明，没有显式 INTEGER PRIMARY KEY 的表可能被重排 rowid，因此固定流程是：**停读写 → 备份 → VACUUM → 重建两张 FTS → 校验 → 恢复服务**。本脚本不挂接启动、迁移运行器、部署或定时任务，不负责自动停止服务或制作备份。

1. 另获维护窗口授权，确认版本、数据库路径、运行身份和备份位置；停止 Web/API/MCP、worker、scheduler 及其他读写进程，关闭其连接，防止服务自动重启。使用 SQLite 一致性备份或停机后的完整数据库副本并验证可恢复；不要在线只复制主 DB、忽略 WAL，也不要手工删除 WAL/SHM。预留 VACUUM 临时空间（SQLite 提示最多需要原库大小两倍的额外空间），并另计备份、FTS 重建和 WAL 余量。
2. 窗口前后选同样的三个有命中的代表关键词（每个至少 3 字符），保存脚本 JSON；脚本对每张 FTS 输出命中数量与按稳定 card_id/block_id 排序的摘要，既比数量也比关联对象。未指定关键词时从少量正文自动抽取最多三个，稀疏/空库可能不足三个，不能冒充三个线上关键词验收。
3. 历史任务删除、raw_payload 改写、遗留表删除与索引调整须分别复核实时调用、兼容、备份与用户授权；当前维护脚本不执行这些操作，也不新增迁移。删除基线要求的表前，先解决启动 schema 校验与升级兼容，不能直接删除后启动现行镜像。

```bash
# 默认只读预览，不运行 VACUUM 或重建，不创建/迁移库。
python3 scripts/rebuild-card-text-search.py --db <数据库副本路径> \
  --keyword <关键词一> --keyword <关键词二> --keyword <关键词三>
# 独立授权的维护窗口内，显式执行。
python3 scripts/rebuild-card-text-search.py --db <已停机数据库路径> \
  --keyword <关键词一> --keyword <关键词二> --keyword <关键词三> --apply
```

脚本在同一独占连接中先验证原 FTS rowid 集合与数据库完整性、保存样本命中，随后仅清空可重建的两张 FTS 投影、提交事务，再执行 VACUUM，让旧 FTS 页也随本次回收；业务行不删改。独占连接在 VACUUM 与重建间持有锁；两表按稳定主键每批最多 500 行重建，在同一事务内检查 FTS 行数/rowid 双向集合、FTS 自身 integrity-check、样本命中稳定 ID 摘要和 `pragma integrity_check`。成功提交后 checkpoint WAL。JSON 同时保存 before、after_vacuum、after 的 page_count/freelist_count/free_bytes、FTS 预期/实际行数和最终主库文件大小；不修改 auto_vacuum。

5. 检查退出码 0、`integrity_check=["ok"]`、两表 expected=actual、sample_before=sample_after，核对 `after_vacuum.freelist_count` 接近 0。FTS 重建仍可能留下空闲页，应评估 `after.freelist_count`，不能保证最终严格为 0，也不能再裸跑一次 VACUUM（会重新引入 rowid 风险）。运行现有卡片预览/全文索引测试，恢复服务后按当时授权运行 revision-check 和有界产品入口，验证 SourceRun → RawItem → InformationCard，并核对三个真实关键词的检索命中；离线 fixture 不代替这些运行证据。
6. **失败/中断后保持服务停止。** VACUUM 与 FTS 重建不可能放进同一事务；如果清空 FTS 已提交或 VACUUM 已完成而重建失败，FTS 可能为空或与当前 rowid 不一致。修复可使用下列模式（同样先预览），跳过 VACUUM、按当前行重建并校验；此模式无法恢复窗口前样本，须人工与已保存 JSON/备份比对。若修复失败或业务数据有差异，恢复已验证的一致性备份后再核验，不自动恢复服务。

```bash
python3 scripts/rebuild-card-text-search.py --db <已停机数据库路径> --rebuild-only
python3 scripts/rebuild-card-text-search.py --db <已停机数据库路径> --rebuild-only --apply
```

`rebuild_card_text_search(db_path)` 仍提供原有原子重建函数供现有调用使用；维护脚本默认行为已改为 dry-run，执行维护必须加 `--apply`。脚本采用原地 VACUUM，不实现 VACUUM INTO 切换路径。

卡片预览中长度至少 3 个字符的每个词用双引号转义为 FTS5 短语，多个词仍为 AND；1–2 个字符用转义 LIKE。ASCII 大小写不敏感，中文匹配保持；SQLite trigram 不执行 Python casefold 的多字符展开（如 `Straße` 不匹配 `STRASSE`），SQLite 内置 LIKE/lower 也不提供非 ASCII 短词的完整大小写折叠。索引覆盖标题、作者、正文、URL，不索引独立的 `summary`；遗留 `/api/search` 路由已在前序清理中移除，本次删除无生产读取方的 SearchRepository，不改变现行 HTTP/MCP 字段。


## v0016：通知 outbox 与扫码恢复轮次

`v0016_notification_outbox.sql` 新增 `notification_outbox` 和 `auth_qr_rounds`。通知按逻辑去重键加渠道后缀唯一，outbox 与 `NOTIFICATION_DELIVER` 任务在同一事务入库；发送在事务外进行，claim token 与 60 秒租约保护结果回写。outbox 只保存正文、临时图片路径、关联对象与投递结果，不保存应用密钥或 tenant token。`idx_notification_outbox_due(status,next_attempt_at)` 与 `idx_notification_outbox_related(related_kind,related_id)` 分别支持维护与事件关联；每次通知状态写入维护这些 B-tree，另有主键和去重唯一约束，增加持久库写入与空间。

扫码轮次以 incident 外键作主键，保存轮次、码数、有效期、下轮时间、当地日期计数、专属 OpenCLI site 和最近租约标识。轮次转换与其下一个浏览器/维护任务同事务入库；重启后复用已入队通知的有效期，不刷新已发送的同一张码。图片仅在 state 的 `auth-qr/` 临时目录，以 `0600` 保存，发送终态、过期或恢复后删除，不进附件表或导出。

`card_evaluations` 与 `opportunity_breakdowns` 各新增 nullable `writeback_at`：新写入及覆盖均更新它，原有 `created_at` 的首次写入语义保持不变。历史行保留 NULL，看门狗读取时回退 `created_at`，不伪造旧覆盖时间。不新增这两列的索引，每次写回额外写一个时间值。

迁移采用单个原子 DDL 事务，可在失败后重跑；无历史数据重写。发布属于 `data-migration`，必须先做一致性快照与副本预演；旧镜像不能直接打开 v16 数据卷。本任务未执行部署或真实数据迁移。

## v0018：停用 scys 登录恢复

`v0018_retire_scys_auth.sql` 只更新历史数据：将未解决的 scys incident 标为 `resolved`、通知标为 `skipped`，记录 `platform_retired` 与收尾时间，清空验证和通知 claim/lease；将平台 scys 熔断关闭并恢复 automatic，清空原因、用户动作、冷却与 probe 字段。已解决 incident、其他平台和 provider 熔断保持原样。

不新增表、列或索引，schema 校验集合沿用 v17；数据更新与 `user_version=18` 在同一事务提交，失败整批回滚。发布须使用 `--update-kind data-migration`，并在一致性备份副本上预演；旧镜像不能直接打开 v18 数据卷。

## v0020：退役外部公众号采集服务

`v0020_retire_feed_provider.sql` 清除停用 RSS Feed 来源的旧 provider 与正文策略字段，恢复普通 RSS 默认值；不改 source_id、enabled、URL，不删除来源。历史 RawItem、卡片、内容块、附件及不可变配置版本（包括配置 JSON、哈希和创建时间）原样保留。公开 RSS 来源保持原配置。

同一事务将退役 `wechat` 平台尚未解决的登录事件与扫码轮次标为 resolved，清除租约；非成功/跳过的扫码任务改为 skipped，待发/发送中/失败通知改为 skipped，关闭平台熔断。历史同步任务继续以退役任务类型读取，执行时返回 skipped，不重新排队同步。微信作为人工核实联系渠道不受影响。

不新增表、列或索引，schema 校验沿用 v19；数据更新与 `user_version=20` 同事务提交，失败回滚，可重复启动。发布须用 `--update-kind data-migration`，先在一致性备份副本预演；旧镜像不能直接打开 v20 数据卷，回退须使用部署恢复点。本变更未执行部署或线上数据迁移。
