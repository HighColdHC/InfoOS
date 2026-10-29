# 048eeb05 旧初始化测试夹具

这三份文件来自本仓库提交 `048eeb0592781c1f526d2b09f21c6873b1324c9f` 的 `backend/infrastructure/storage/`，通过本地 `git show <commit>:<path>` 一次性逐字节复制：

- `database.py.txt`：旧版 `initialize_database` 实现；`.txt` 后缀避免被 pytest 与 Python 产品代码扫描器当作可执行模块。
- `schema.sql`、`schema_collector.sql`：旧版结构定义。

`test_versioned_migrations.py` 在临时 SQLite 中加载它们，比较旧初始化结果与 B1 迁移后的结构和回滚兼容性。CI 运行测试时不需要 Git 历史。
