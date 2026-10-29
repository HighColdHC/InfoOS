# InfoOS 项目约束

## 产品与平台边界

- InfoOS 是用户自有 Linux 设备上的个人情报系统，本地 SQLite 与附件为真相：采集 → 信息卡 → 外部 agent 分诊、拆解与归并 → 知识卡 → 冻结日报。
- InfoOS 不调用 LLM；GPU ASR 只产转录。外部 agent 只经 `/mcp`，按 Token scope 与版本化画像/标准写回；历史内容保持可读。
- 决策、行动、认知与写作属于用户的 PKM；PKM 以深链接和 `card_id` / `knowledge_id` 引用 InfoOS，InfoOS 不读 PKM 原文件。
- 官方范围：RSS、Hacker News、GitHub、Product Hunt 的 registry 声明公开能力；YouTube 公开内容（当前仍需浏览器会话）；Web 仅捕获兜底。
- 实验性：Reddit（免登录采集交付后再评估升级）、知识星球、小红书、抖音、X，以及 YouTube 登录型用途 / home_feed。用户自行登录并遵守平台条款，不保证登录型适配器可用。Google 只辅助会话，不是可配置来源。
- 平台能力以 [registry](backend/domain/sources/registry.py) 为唯一登记处，分级不表示匿名采集已经实现；任务与退役状态见 [tasks](backend/domain/tasks.py)，不恢复内置 LLM 或旧 semantic/score/decision/output 链。来源降频、停用及画像修改须用户确认。

## 分层与检查

Python 产品代码只在 `backend/`，前端只走 `/api/*`，`scripts/` 不承载业务实现。

```text
api -> application / read_models / infrastructure
workers -> application / domain / infrastructure
application -> domain / infrastructure
read_models -> domain / infrastructure
domain -> 标准库 / domain（无 I/O）
```

application 不导入 api/workers/read_models；api 与 workers 互不导入；read model 只读。边界校验输入，内部信任类型与 schema；失败上抛任务层或记录显式状态，不吞错。细节见 [工程结构](docs/engineering/project-structure-target.md)。

```bash
make install && npm --prefix frontend install
.venv/bin/python scripts/check_file_size.py
.venv/bin/python scripts/check_layer_deps.py
# 使用自行配置的 SSH alias，目标为隔离 Linux 开发机
INFOOS_TEST_SSH_TARGET=linux-dev bash scripts/infoos-remote-test --python -- tests/infoos/test_source_registry.py -q
```

首次产品安装按 [agent 安装指南](docs/INSTALL-for-agents.md)。`scripts/run-infoos-test-env.sh` 是本地开发辅助入口，依赖用户自备测试种子库、Chrome 和 OpenCLI；不是空环境安装命令，前提见 [开发环境](docs/engineering/local-env-playbook.md)。

## 安全与贡献

- 编辑前检查 `git status --short`，保留无关改动；小改动不治理无关模块。多步贡献可使用 [任务模板](docs/tasks/README.md)，无需维护者私有材料。
- 先实现并演示功能方向，确认后再新增护栏或回归测试；已报告失败可补定向测试。运行相关现有检查，通过后只为新变化或未解疑点扩展。
- UI 布局、响应式和 overflow 用真实浏览器核验；只关闭本次新建标签页，不退出用户浏览器或使用 `pkill` / `killall`。
- 隔离测试使用 `~/.infoos-test` 或显式临时路径，不写默认 `~/.infoos` 或运行数据。开发验证不等于生产授权；见 [开发环境](docs/engineering/local-env-playbook.md)。
- 凭据留在受保护的忽略文件或运行配置；不读取、代填或记录密码、MFA、Cookie。升级保留 SQLite、附件、state 和专用 Chrome profile。
- Browser Runtime 使用非 root UID 与 sandbox，Chrome/OpenCLI/Bridge 同 UID；被动就绪检查不启动 Chrome、不建页。CDP、VNC、OpenCLI daemon 与 debug 端口不公开。
- 产品端口仅绑定 loopback 或受限可信局域网；公网管理面、控制台、阅读和附件须经双因素网关，`/mcp` 独立 Bearer 鉴权。安全与恢复见 [部署手册](deploy/docker/README.md) 与 [Docker 合同](docs/engineering/docker-appliance-target.md)。
- 部署从干净审核版本用完整 Dockerfile 构建，先预览再切换，核对源码/镜像/容器 revision 与真实业务链；不做单文件覆盖发布。
- 交付写清完成项、验证与缺口；API 接收、附件落盘、转录和 finalize 分别报告。文档变更核对 diff 与相对链接，修正当前文档漂移。

按需阅读：[产品合同](docs/product/infoos-intelligence-workbench-target.md)、[领域语言](CONTEXT.md)、[文档索引](docs/README.md)、[SQLite CRUD](docs/engineering/sqlite-crud-playbook.md)、[迁移手册](docs/engineering/sqlite-migrations.md)。
