# README 功能截图复现

这些截图来自全新演示数据库和真实前端，不使用已有数据库、采集快照或真实平台登录。全部文章、来源、价格和表现数字来自 [合成样例](../../../scripts/readme-demo-content.json)；来源和文章地址仅使用保留的 `example.com`。页面上的 `localhost` 是本次临时服务，不是部署地址。

## 运行

在源码根目录准备 Python 3.12+、Node.js 与 npm。以下只安装本地开发依赖、构建页面并启动一次演示，不启动 scheduler，不安装或运行外部 agent：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[web]' 'playwright==1.64.1' 'Pillow==13.0.0'
.venv/bin/python -m playwright install chromium
npm --prefix frontend ci
npm --prefix frontend run build
npm --prefix clients/save-to-infoos ci
npm --prefix clients/save-to-infoos run build
.venv/bin/python scripts/run-readme-demo.py
```

[演示入口](../../../scripts/run-readme-demo.py) 自动选择空闲的 loopback 端口并创建 `/tmp/infoos-demo-*`，显式隔离数据、状态、配置、密码库和浏览器 profile。后端通过产品启动迁移建空库；临时管理员密码用安全随机数生成，再用产品密码存储组件保存哈希，明文不输出。账号表单截图通过本地开发请求提供私有网关的认证结果头，只证明表单与临时账户可读，不证明生产网关登录。

[数据生成脚本](../../../scripts/seed-readme-demo.py) 只调用产品 HTTP API 和 MCP，不执行 SQL 写入，也不复制数据库：

1. 在空库拍摄首次引导、权限管理和临时账号表单，然后通过页面关闭引导。
2. API 创建演示 Token；MCP 写入五节关注画像并回读画像、评估和知识卡标准。
3. API 创建三个停用的虚构 RSS 来源。它们不会被抓取，画面中的停用与零启用状态是实际结果。
4. 捕获 API 使用 `readable_content` 和匹配的 `Idempotency-Key` 写入五篇虚构文章，不请求文章地址。产品 worker 执行抓取接收、卡片构建、正文富化、媒体阶段和定稿；样例没有媒体 URL，媒体阶段跳过，不调用 ASR。
5. 等待捕获回执为 `succeeded` 且存在信息卡，再经 MCP 写入五条评估、五类知识卡和一条商机拆解。检查逐条 accepted，回读知识卡原文证据。
6. 选择包含本次知识卡写入时间的北京时间 07:00 窗口，通过管理 API 强制冻结一份日报，并核对 `layout=knowledge_v1` 和五个非空条目。这是人工构造演示窗口，不是等候定时冻结的验收。
7. [页面截图脚本](../../../scripts/capture-readme-screenshots.py) 使用独立的无头 Chromium 拍摄真实页面，不替换 API 响应或修改页面内容。桌面 1440×900，知识库移动端 390×844；组件和平台状态通过实际滚动展示。
8. [扩展截图脚本](../../../scripts/capture-readme-extension.py) 加载构建后的真实扩展；无头环境无法点击权限弹窗，因此只在临时副本的 manifest 中预授予 `http://localhost/*`，UI 和运行代码不变。它填写演示实例的专用捕获 Token，通过真实接口测试连接，再对浏览器内提供的 `example.com` 虚构文章执行实际扩展动作和正文捕获。弹窗以独立扩展页、390×844 拍摄，不包含配置页或密钥；该回执只证明接收成功。

运行结束或抛出异常时，入口脚本终止自己创建的 worker 和 API 进程，并删除整个临时目录，包括密码哈希、Token、数据库、日志、扩展副本与浏览器 profile。它不连接运行中的设备实例，不启动用户浏览器，不安装定时任务。重新运行会覆盖同名截图，日期、运行 ID、日志条数和临时端口会随运行变化。

## 功能与证据入口

功能由 [前端路由](../../../frontend/src/App.tsx)、[管理面入口](../../../frontend/src/AppShell.tsx)、[API 路由](../../../backend/api/main.py) 和 [来源 registry](../../../backend/domain/sources/registry.py) 核对；术语参考 [领域语言](../../../CONTEXT.md)。

| 功能 | 截图文件 | 代码依据 |
| --- | --- | --- |
| 日报 | [daily-brief.webp](daily-brief.webp) | `BriefReaderPage`、`KnowledgeBriefContent`、`intelligence_daily` |
| 知识库 | [knowledge-library.webp](knowledge-library.webp)、[knowledge-library-mobile.webp](knowledge-library-mobile.webp) | `KnowledgeLibraryPage`、`intelligence_knowledge` |
| 知识卡详情 | [knowledge-detail.webp](knowledge-detail.webp) | `KnowledgeReaderPage`、`get_knowledge_card` |
| 信息卡浏览 | [card-browse.webp](card-browse.webp) | `BrowseReaderPage`、`card_preview` |
| 信息卡详情 | [card-detail.webp](card-detail.webp) | `CardReaderPage`、`card_preview` |
| 信息卡管理 | [card-management.webp](card-management.webp) | `CardPreviewModule` |
| 来源管理 | [source-management.webp](source-management.webp) | `SourceManagementModule`、`sources`、registry |
| 采集任务 | [collection-tasks.webp](collection-tasks.webp) | `CollectionTasksModule`、`source_runs`、`research_runs`、`task_manager` |
| 运行追踪 | [run-trace.webp](run-trace.webp) | `SourceRunTraceView`、`source_runs` |
| 采集环境 | [collection-environment.webp](collection-environment.webp) | `CollectionEnvironmentModule`、`collection_environment` |
| 通知设置 | [notification-settings.webp](notification-settings.webp) | `NotificationSettings`、`settings_notifications` |
| Token 与请求审计 | [token-permissions.webp](token-permissions.webp) | `TokenAuthenticationModule`、`token_admin` |
| 账号设置 | [account-settings.webp](account-settings.webp) | `LanAccountModule`、`lan_account` |
| 导出、备份与日志 | [export-and-logs.webp](export-and-logs.webp) | `ExportLogsModule`、`exports`、`logs` |
| 商机跟进 | [opportunity-board.webp](opportunity-board.webp) | `OpportunityBoardPage`、`opportunities` |
| 商机统计 | [opportunity-stats.webp](opportunity-stats.webp) | `OpportunityStatsPage`、`opportunities` |
| 首次引导 | [onboarding.webp](onboarding.webp) | `OnboardingModal`、`SetupMode`、`setup` |
| 浏览器扩展 / Web 捕获 | [web-capture.webp](web-capture.webp) | `clients/save-to-infoos`、`capture` |
| 关注画像与 MCP | README 流程图；没有独立画像编辑页，MCP 是协议接口 | `intelligence_profile`、`backend/api/mcp` |
| 知识卡启动器 | README 流程图；命令行和系统定时器没有产品网页 | `deploy/agent/run-knowledge.sh` 与安装脚本 |

## 检查与边界

截图由 Playwright 产生后，只重编码像素为无损 WebP，不保留 EXIF、XMP、ICC 或文本元数据，不拼接或美化界面。单张限制 400,000 字节，总量限制 5,000,000 字节；发布前还需逐张目视检查、检查 README 相对链接、扫描新增文本与图片元数据中的私有标识，并执行 `git diff --check`。

本次没有验证真实来源抓取、平台登录持久化、媒体下载和转录、飞书发送、生产网关登录、备份恢复或外部 agent 定时运行。这些功能的页面展示、接口代码核对和合成链路，不替代真实环境的功能验收。
