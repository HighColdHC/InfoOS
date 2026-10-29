# InfoOS 单容器 Docker Appliance 合同

状态：容器交付合同；实际安装与兼容须按目标环境验收

本文冻结容器交付、进程、网络、持久化和 Browser Runtime 边界。产品对象与流水线分别以[产品合同](../product/infoos-intelligence-workbench-target.md)和[工程结构标准](project-structure-target.md)为准。

## 1. 交付与验收范围

Docker Appliance 承载 FastAPI/React、scheduler、durable workers、SQLite 与持久卷、Chrome/OpenCLI/Bridge、Browser Console 和媒体/可选 GPU ASR。平台能力按 registry 与 [AGENTS.md](../../AGENTS.md) 的官方/实验性分级说明，不以维护者某次采集成功推定全部安装可用。

知识星球仅通过 `zsxq` 接入，旧 `scys` 命令与定时来源退役；不承诺完整镜像平台全部内容。原生 systemd 是遗留辅助面，不能代替容器采集验收。每个安装分别取得版本、备份恢复、升级回滚、资源与稳定性证据，不从开发测试推断生产授权。

## 2. 单容器边界

```text
用户浏览器 / Obsidian / capture extension
                 |
                 | 一个受认证 HTTP(S) 入口
                 v
+--------------------------------------------------+
| InfoOS Docker Appliance                          |
|  gateway + FastAPI + React                       |
|  scheduler + durable workers                     |
|  connectors + media + ASR                        |
|  Chrome Stable + OpenCLI + Browser Bridge        |
|  SQLite + assets + state + logs                  |
+--------------------------------------------------+
            |                         |
      infoos-state volume       infoos-data volume
```

宿主只提供 Docker、持久卷、可选 NVIDIA GPU 和一个产品发布端口。容器内部可以有多个进程、loopback 端口和 Unix socket；“单容器”不等于“单进程”。

## 3. 进程与技术合同

| 模块 | 职责 |
|---|---|
| 产品入口 | FastAPI 挂载 React 与 `/api/*`，统一认证、日志和 Browser Console 代理 |
| 后台执行 | scheduler 与按资源队列运行的 durable workers，共享 SQLite task 合同 |
| 进程管理 | s6/PID 1 管理依赖、健康、日志与优雅停止 |
| Browser Runtime | Chrome Stable、内部 display、OpenCLI daemon 与 Bridge；同一非 root UID |
| 内容处理 | connector、ffmpeg、下载器、OCR、媒体处理与可选 GPU ASR |
| 数据 | SQLite、CardAsset、导出和日志位于本机持久卷 |

六个 worker 角色按持久化 `queue_name` 原子认领：`public_fetch`、`browser_fetch`、`urgent_media`、`card_pipeline`、`gpu_asr`、`maintenance_io`。`cloud_ai` 仅保留为历史队列名，不启动 worker。不能只按 TaskType 猜资源队列。

GPU 路径使用 `faster-whisper large-v3-turbo`、CUDA、`int8_float16` 和单 GPU 锁，只处理 `VIDEO_TRANSCRIBE`。InfoOS 不调用 LLM，摘要与评估由外部 agent 写回；历史翻译与总结 block 继续可读。

镜像必须版本化且可重复构建，记录 commit/image ID 以及 Chrome、OpenCLI、扩展和系统依赖版本。不能在运行容器内手工升级形成不可重现状态。

## 4. 网络与认证

- 默认只发布一个产品 HTTP(S) 端口；面板、API、插件读取、capture、Webhook 管理和 Browser Console 共用该入口；
- raw CDP、VNC/noVNC、OpenCLI daemon、debug、SQLite 和 supervisor socket 不得通过 Docker `ports` 发布；
- 宿主产品端口默认只绑定 loopback。可显式配置同时绑定 loopback 与宿主局域网 RFC1918 私有地址，仅供可信内网通过容器自签名 HTTPS 和面板 Web 登录访问；禁止绑定 `0.0.0.0` 或公网地址。在不可信网络访问时仍使用 SSH tunnel 或受信任 TLS 反向代理；
- Browser Console 必须经产品认证和短时授权；InfoOS 不代填、读取或保存密码、MFA、Cookie；
- passive health/readiness 不启动 Chrome、不创建 tab、不调用平台抓取；主动检查必须显式触发；
- 可选容器内出站网关只监听 loopback，不启用 TUN、透明代理、LAN listener 或 `NET_ADMIN`。

## 5. 持久化合同

```text
/var/lib/infoos/state
  setup-state.json
  browser-session.json
  chrome-profile/
  opencli/
  config/
  models/
  locks/

/var/lib/infoos/data
  infoos.sqlite3
  assets/
  exports/
  logs/
```

容器可替换，普通升级不得删除卷。SQLite 必须位于本机文件系统，不放在 NFS/SMB。SQLite、WAL/SHM、assets、state、Chrome Profile 与 OpenCLI binding 必须纳入可恢复的数据保护；恢复时保持 UID/GID、权限和目录结构。Chrome Profile 只能由一个运行中的 Appliance 独占。

仅改业务代码的版本切换复用既有卷，不因镜像更新而重复复制全部附件；选择此路径前须确认没有 schema、启动迁移、附件格式或持久数据语义变化，并保有已验证的独立恢复点。涉及持久数据变更时，切换前取得同一时点的 SQLite、assets 与 state/Profile 快照。增量备份、异盘副本与保留策略属于数据保护生命周期，不能把同盘、每次部署生成的完整 tar 当成唯一备份。

## 6. Chrome 与登录态

- 使用 Google Chrome Stable，不使用个人 profile、Chrome for Testing 或 `--no-sandbox`；
- `/var/lib/infoos/state/chrome-profile` 是容器内唯一正式 profile；
- Chrome、OpenCLI daemon 与 Bridge 由同一非 root UID 持有；
- 用户通过受认证面板操作容器 Chrome 完成扫码、密码、MFA 和人工修复；
- 知识星球与飞书复用该专用 profile 的用户授权登录态；知识星球 setup/preflight 使用 `https://wx.zsxq.com` 功能探针；
- “当前会话可用”与“登录态已持久化”必须分开；只有重启前功能探针、同 UID/同 executable/同 profile 的优雅退出与重启、新 binding、重启后被动状态和功能探针全部通过，才可记录 `persisted=true`；
- 容器停止先停止新任务并让 Chrome 优雅退出，再停止其余进程。

## 7. 生命周期与健康

```text
volume / permission validation
  -> optional outbound gateway
  -> internal display and Browser Runtime
  -> FastAPI / Web
  -> scheduler
  -> durable worker roles
```

聚合健康必须分别保留 Web、scheduler、各队列 worker、Chrome、OpenCLI、可选出站网关和 SQLite 的证据。单个子进程持续崩溃或缺少队列消费者时，容器应显示 degraded/unhealthy，不能靠无限重启掩盖。

## 8. 正式生产关闭条件

只有同时满足以下条件，才能把当前 test Appliance 宣布为正式生产交付：

1. 从固定 commit 重复构建版本化镜像，记录 image ID 与关键依赖版本；
2. `docker inspect` 证明只发布产品入口；
3. 用全新 volumes 完成 onboarding、人工登录和 Browser Console 验收；
4. 同 profile 通过 Chrome 重启、容器 stop/start 和同镜像 recreate 的登录态恢复；
5. 三类输入均进入 Source/SourceRun/RawItem/InformationCard/ContentBlock/CardAsset 统一链；
6. 10 个可配置平台完成真实任务链和 UI 证据，且不把 Google、WeChat 或 capture-only web 计入；
7. 重复 passive healthcheck 不增加 Chrome target/tab；
8. 完成 SQLite、assets、state/Profile 的一致备份恢复和版本升级回滚演练；
9. 在目标 CPU、内存、`/dev/shm`、磁盘和可选 GPU 配额下完成 24 小时稳定性与任务恢复验收；
10. 明确生产授权、切换步骤和可执行回滚点。

每次宣称验收通过都必须记录 commit/image ID、容器配置、volume、真实任务链和浏览器/UI 证据；历史快照不能代替当前证据。
