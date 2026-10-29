# SQLite CRUD 手册

更新日期：2026-09-08

修改 SQLite CRUD、schema 或实际查询语义时，使用本手册中相关部分；仅调整展示或文案不触发整套数据库流程。

## 1. 核心规则

SQLite 是运行时真相源。不要根据旧文档、前端类型、JSON payload、测试印象或变量名猜测表和列。

## 2. 编辑前

1. 在 [顺序迁移目录](../../backend/infrastructure/storage/migrations/) 中核对涉及的表与字段；从 v0001 基线逐项累计到当前版本，并核对目标数据库的 `PRAGMA user_version`。
2. 在 `backend/infrastructure/storage/` 找到拥有该表的 repository/query 模块。
3. 沿受影响的读取、写入与 migration 调用链确认依赖，不默认扫描全部调用者或测试。
4. 核对默认路径与 `INFOOS_DATA_HOME`，避免读错环境。
5. 优先复用 repository；直接 SQL 只能位于 storage query/repository 或明确的只读 projection 边界，并显式列出字段。

## 3. Schema 变更

- 在顺序迁移目录新增下一版本，并按 [迁移手册](sqlite-migrations.md) 更新 runner；已发布迁移不改写。
- 同步实际受影响的 repository、DTO、read model、API payload 或前端类型。
- 保持新建数据库、旧库升级与重复启动的正确语义；验证和新增测试遵循下方范围，不为未改变的路径添加兼容层。
- additive change 优先；删列、改语义或重写历史数据需要明确迁移与回滚策略。
- 不得用恢复 YAML/JSONL fallback 的方式掩盖 schema 问题。

## 4. 事务所有权

- `InfoOSRepository` 构造时解析并缓存绝对数据库路径。同步读模型可用 `backend.read_models._connections.shared_read_connection` 在入口建立请求内只读连接；对应的 `read_connection_scope` 在同一线程设置与复位 contextvar。卡片详情/列表、来源列表/详情与运行列表已接入。
- 作用域内，同库的 `connect_existing()` / `connect_readonly()` 借用连接，不关闭它；不同库和不同线程各自连接。借用期间新开的读事务在该仓库上下文退出时结束，已有外层事务由外层拥有。作用域结束（含异常）复位上下文并关闭自有连接；作用域外保持原有独立连接行为。写连接不复用只读连接。
- `initialize_runtime_storage()` 调用启动迁移运行器创建父目录并启用 WAL（包括测试临时库）。`connect()` 依赖此启动前置条件，不再逐次建目录或设置 WAL；它与 `connect_writable()` 均设置 `synchronous=normal`，前者仍在成功退出时 commit、异常时 rollback。后者保留调用方拥有连接/事务及较长 timeout 的合同，不与前者合并。
- 接收共享 `sqlite3.Connection` 的 repository 方法只能使用该连接，不能自行 `commit` 或 `rollback`。
- 事务由最外层业务动作拥有；网络、OpenCLI、LLM 和长时间文件操作不能放进 SQLite 写事务。
- 任务终态、task event、checkpoint、下游 task 与 batch 关系必须按当前 repository 合约保持可恢复因果。
- cursor 只能与它证明的 acquisition commit 一起前移。

当前 acquisition commit 至少保证：

```text
RawItem upsert
  + source cursor
  + acquisition checkpoint/evidence
  + CARD_BUILD enqueue/batch linkage
  -> one owned transaction boundary
```

具体字段与顺序以当前 `fetch_commit`、task finalization 和 repository 实现为准，不要从历史计划照抄。

## 5. 查询检查

写查询前回答：

- 哪个表当前拥有这个字段？
- 它是 SQLite column、JSON field，还是 read-model 派生值？
- 是否已有 repository/query 方法？
- 查询是否使用正确的环境路径与 connection？
- 旧行缺少可选字段时，默认语义是什么？
- 排序、分页、唯一性和查询预算是否明确？
- projection 是否保持只读？

## 6. 常见错误

- 把前端字段名当成数据库列。
- 在 FastAPI route 中写业务 SQL。
- 在 domain 层导入 `sqlite3` 或 storage。
- 单独提前写 cursor、状态或 batch，留下事务间隙。
- 用 YAML/JSONL 重新建立第二份运行时真相。
- 把 `config_path` 当成 source execution 输入。
- 只测试全新数据库，不测试已有 SQLite 的升级与幂等。

## 7. 验证

遵循根目录 [AGENTS.md](../../AGENTS.md) 的功能优先与验证范围。选能证明本次变更的最小检查；新增测试只用于已获授权的阶段或用户报告的失败条件。涉及 schema/migration 时覆盖实际改变的新建、升级或重复执行行为。

```bash
bash scripts/infoos-remote-test --python -- tests/infoos/<relevant-test-file>.py -q   # 在远端开发机执行，不在本机跑 pytest
```

检查通过后不自动扩大到全套 gate。端到端或部署验收按请求在目标环境走有界正式入口；本地 CRUD 修改不授权操作远端数据，也不要求逐次运行全平台链路。
