# InfoOS 个人情报系统产品合同

状态：当前产品定位与边界。当前代码已实现分诊、知识卡拆解与归并、冻结日报、画像和 MCP；工程导航见 [处理链说明](../engineering/intelligence-layer-plan.md)。事件重设计与 MCP feedback 工具尚未交付；具体安装可用性仍须目标环境验收。

本文回答 InfoOS 是什么、处理哪些输入、产出什么，以及哪些能力不属于它。工程分层见 [`../engineering/project-structure-target.md`](../engineering/project-structure-target.md)，部署边界见 [`../engineering/docker-appliance-target.md`](../engineering/docker-appliance-target.md)。

## 1. 一级定位

InfoOS 是一个**运行在用户自有 Linux 设备上、以本地持久数据为真相的个人情报系统**：持续采集用户关注的信息源，生成信息卡，保存外部 agent 依据用户关注画像写回的分诊、拆解与归并结果，按用途形成知识卡和各个库，并据此冻结每日简报，是用户获取信息的唯一入口。

“用户自有 Linux 设备”表示用户控制运行环境、持久卷和数据生命周期；Mac 只是开发与 SSH 运维控制面。“本地持久数据为真相”表示 SQLite、附件与运行状态以该 Linux 设备上的持久卷为权威，不表示所有计算都必须离线：GPU 可做本地 ASR。**InfoOS 自身不调用 LLM**（2026-09-30 起）：翻译、摘要、评估等“读懂信息”的工作由用户订阅内的 agent 完成，结果经 MCP 写回。

用户通过 agent（Claude Code、Codex 等）使用 InfoOS：询问日报、检索信息，也可以让 agent 发起调研、提交链接捕获、按用户决定维护来源；agent 也承担评估，按 InfoOS 给出的评分标准读卡并写回结果。偏好变更须用户确认后通过画像版本写回，当前没有通用 MCP feedback 工具。对 agent 而言，InfoOS 是可调用的采集与情报工具，`/mcp` 是唯一的 agent 入口（权限分级见 §5.3）。

手动浏览发生在各平台原生 App 或网页中，InfoOS 不做与之竞争的信息流；用户刷到值得处理的内容时一键捕获（§2.2），交给 agent 评估与拆解。InfoOS 的 Web 界面对用户只承担深链接阅读与偶尔的轻量漫步浏览（§5.1），管理面供运维使用。

FastAPI、React、SQLite、Docker 和具体平台都是当前实现或交付形态，不定义产品本身。

## 2. 三类输入与一类上下文

### 2.1 持续来源

用户创建和维护来源，由手动提交或 scheduler 触发。平台能力统一来自 `backend/domain/sources/registry.py`，不能在 API、前端和 connector 各自维护清单。

当前 10 个可配置平台是 X、YouTube、RSS、Reddit、GitHub、Hacker News、Product Hunt、小红书、抖音和生财有术。生财有术使用 `platform=zsxq`，提供通过 OpenCLI 帖子流采集普通帖与精华帖的 `topics`、精确“风向标” `opportunity` 和站内检索 `keyword`；旧 `platform=scys` 只保留历史数据辨识，不可配置或执行。

发布分级：RSS、Hacker News、GitHub、Product Hunt 的 registry 公开能力、YouTube 公开内容（当前仍需浏览器会话）为官方范围；Reddit（当前要求登录，免登录尚未交付）、知识星球、小红书、抖音、X，以及 YouTube 登录型用途和 home_feed 为实验性，不保证可用。分级同样适用于按需调研，不代表逐平台实机验收。

### 2.2 用户主动捕获

agent 可以经 `/mcp` 代用户提交链接。系统为这类输入使用 system-managed capture source，但后续仍进入同一 SourceRun、RawItem 和 InformationCard 合同。捕获 API `/api/capture/v1` 独立提供；Save to InfoOS 扩展尚未经真实浏览器端到端验证，不属于当前对外承诺的功能。

主动捕获是“手动刷”与 InfoOS 之间的连接点：用户在原生平台刷到内容，一键入库，之后由 agent 评估。手机端不另做 App 或快捷指令：用户把分享链接或分享文本交给 agent，agent 经 `/mcp` 提交；InfoOS 先查重（跨所有来源、识别短链），已采集直接返回现有卡片，未采集才进入捕获链。

### 2.3 按需调研

按需调研只执行平台注册表明确声明的 research capability。当前十平台分别提供关键词检索、GitHub 仓库检索、Hacker News 索引检索、用户选择的 RSS Feed 匹配或 Product Hunt 可见 Feed 过滤；知识星球使用 `zsxq:keyword`。逐平台语义、条数上限、未知日期和覆盖不足合同见 [多平台调研功能合同](multi-platform-research-contract.md)。结果进入统一持久化链。

平台注册表中的特殊项必须准确表述：

- Google 只用于辅助登录检查，不是可配置采集平台；
- 公众号以普通 `rss` 来源接入第三方公开 RSS，不额外计入可采集平台；
- `web` 是 capture-only，不是持续来源平台；
- 三者都不计入 10 平台验收数量。

### 2.4 关注画像（唯一的用户上下文）

关注画像是一页结构化 Markdown，由用户 PKM 侧的 agent 从项目状态、周期计划、领域地图和已确认决策中提炼，经 MCP 推送。必备段落：主线与 KR、进行中项目（各自需要的信息）、正在追问的问题、暂放方向（附决策引用）、长期领域。

InfoOS 为画像做版本管理，每条评估都记录所依据的画像版本。InfoOS 不读取 PKM 原文件，也不保存画像以外的个人资料。

## 3. 业务链与产品终点

```text
Source / system-managed capture source
  -> SourceRun -> RawItem -> InformationCard -> ContentBlock / CardAsset
  -> CardEvaluation（依据关注画像）
  -> KnowledgeExtraction -> KnowledgeCard / KnowledgeEvidence
  -> DailyBrief（冻结日报）-> 推送 / MCP
```

- `InformationCard`：一个外部条目的标准化信息对象，保留来源、处理状态、内容块、附件和无法补全的缺口。平台私有细节放入原始载荷。
- `CardEvaluation`：外部 agent 依据画像与评分标准（rubric）写回的一张卡的质量分、相关分、归属、内容类型和建议动作，InfoOS 负责校验并记录画像版本、rubric 版本与 evaluator。
- `KnowledgeCard`：外部 agent 按类型标准拆出的结构化知识，保留原卡与摘句证据；归并由 agent 判断。
- `KnowledgeExtraction` / `KnowledgeEvidence`：按原卡当前版本记录一次拆解及每张知识卡的证据动作，拆不出也留记录。
- `InfoEvent`：仍待重新设计，本期不复用旧事件对象。
- `DailyBrief`：按固定时间窗冻结、条目编号、可以引用的日报。

**产品终点包括经过分诊的信息卡与按用途归库的知识卡。** 知识卡包括项目、商机、方法、产品、资源、选题与资讯；一期启用项目、商机、方法、选题、资讯，产品与资源二期启用。用户采纳后才在 PKM 物化，保留 InfoOS 深链接、`knowledge_id` 与原卡 `card_id`。决策、任务、认知和写作仍在 PKM。

### 3.1 商机卡与调研

分界规则：描述“这是什么”的结构化知识卡保存在 InfoOS；用户决定做、执行与复盘在 PKM。

- 商机卡采用宽口径：具体卖什么、卖给谁，加收入、价格、销量、接单、成交或在投广告之一；只写“商务合作”的简介不算赚钱信号。外部 agent 不判 verify，只拆解并按信号强度排序。
- 生财精华与风向标是确定性渠道，依次尝试项目、商机、方法。抖音与小红书规则初筛只是输入证据；其他来源先分诊。
- 归并前检索同类型卡。商机按客户、要约、收费方式相同归并，追加原卡证据；新进展用 update，矛盾用 conflict，保留说明。
- 商机卡初始 `research_status=pending`；交互式 agent 依据用户调研决定写 `researching`、`project`、`observe` 或 `drop`，只追加状态事件。定时 agent 不替用户选择去向。
- 一期上线后由 Claude 经用户确认停用旧商机任务，旧看板 `/r/opportunities` 继续可读；旧拆解、核实与跟进表保留，不迁移或改变语义，二期看板改读商机卡。
- 站外接触由用户本人完成，不保存联系方式或聊天全文，不代用户入群、私信、评论或点赞。

## 4. 处理链

```text
FETCH
  -> CARD_BUILD
  -> CONTENT_ENRICH
  -> MEDIA_DOWNLOAD
  -> 媒体处理（有音轨视频 VIDEO_TRANSCRIBE；无音轨 not_applicable）
  -> CARD_FINALIZE
  -> 可选 WEBHOOK_DELIVER

agent 写回（MCP submit_evaluations）-> 写入评估与评估 block -> CARD_FINALIZE
每日 07:00（Asia/Shanghai）：DAILY_BRIEF_BUILD
```

- **InfoOS 不调用 LLM。** 内置 LLM 后端及 openai-compatible ASR 回退已删除；`CARD_LLM_ENRICH`、`IMAGE_UNDERSTAND`、`SUMMARY`、`CONTEXT_PACK` 任务类型已退役，枚举与历史数据保留，历史翻译与总结 block 继续可读。GPU ASR 只产生转录事实。
- **评估由外部 agent 完成。** agent 通过 MCP 读取评分标准（`triage_v1`，质量阈值 60，新增排除原因和可拆类型）、当前画像和新卡，再批量写回评估。InfoOS 校验枚举与范围；`attribution_ref` 必须原样出自画像，否则降级；执行暂放方向规则。通过阈值的卡写入“摘要”和“与你的关系”两个 block，重评时替换。
- 质量分作为底线过滤，相关分用于排序。低分卡只沉底，不删除，仍然可以检索。
- 暂放方向在分诊中沉底；评估仍记录格局级提示。确定性渠道的卡主体命中暂放方向时由 agent 写回 nothing，只有新鲜的格局级变化可拆资讯卡。知识卡归属必须逐字引用当前画像的主线与KR、进行中项目、正在追问的问题或长期领域，并按所在节确定归属类型。
- 新日报 `layout=knowledge_v1`：上海时间前日 07:00 至当日 07:00 按知识卡创建时间及更新证据选条，分资讯、商机、项目、选题、沉淀；存量只计数。旧快照保留原格式。知识卡写回不触发 CARD_FINALIZE 或信息卡 block。
- 通用反馈信号及 agent 每周反馈汇总属于未交付目标；当前没有对应 MCP 工具，不能把已允许的 `feedback:write` scope 当作能力已经实现。画像调整经用户确认后用 `push_profile` 写入新版本。

旧的 semantic、score、decision、output、topic、event 等对象不属于新链，不得继续调度或扩展。已核清零调用的遗留 API 及专属代码已删除；仍被脚本、保留入口使用的兼容代码与历史表保留。新情报层使用新表，不复用这些旧对象；历史 summary、translation 等内容块继续由阅读面渲染。

## 5. 当前产品能力

### 5.1 Web 页面

管理面（运维使用，局域网表单登录或公网 Authelia 双因素后可用）：卡片、来源、任务、采集环境、Token、账号与安全、日志；导出功能与日志合并在同一工作区。

阅读面（用户使用，可以通过公网认证网关访问）：信息卡 `/r/card/{card_id}`、知识卡 `/r/knowledge/{knowledge_id}`、日报 `/r/brief/{date}` 与漫步 `/r/browse`，不带管理导航；当前没有单事件阅读页。

商机跟进看板 `/r/opportunities` 与统计页 `/r/opportunities/stats`：展示从 agent 推荐、跟进、核实到转入行动的进度与漏斗。**看板跟进状态是阅读面唯一的写入**，仅 `PUT /api/opportunities/{card_id}/follow-up`，经过阅读鉴权与同源变更检查；核实记录仍由 agent 写入，行动发生在 PKM。

知识库 `/r/knowledge`：按类型、子类型、商机调研状态与关键词浏览知识卡，只读。

漫步浏览：按来源或平台浏览已评估的卡片，可见分数与评估理由，低分卡沉底但可见。不做已读状态、个性化首页或推送，不与原生平台的信息流竞争。

### 5.2 采集与内容处理

- 10 个可配置平台的持续采集；
- 注册表限定的按需调研；
- MCP `capture_url/get_capture` 按需捕获；
- SQLite 持久化任务、scheduler 与按队列 durable worker；
- 正文补全、媒体下载、视频 ASR；
- 保存 agent 写回的分诊与知识卡拆解、归并结果，并据此冻结日报。

知识星球帖子只充当飞书索引时，成功读取的飞书正文作为信息卡正文，并保留原帖 URL、外部文档 URL 与读取状态。登录或权限失败必须明确展示，不能把索引 teaser 冒充全文；原始记录只保留媒体 URL 与 provenance，不保存未消费的 base64 blob。该接入替换不代表知识星球完整镜像旧官网课程、全部航海栏目和所有历史内容。

### 5.3 对外接口

- 受认证的 Web 管理面与 `/api/*`；
- `/mcp`：面向 agent 的远程 MCP 端点，也是唯一的 agent 入口，使用设备级 Bearer Token 并按 scope 分级授权（见下表）。不另建 CLI；以后确有脚本需求时，只做调用同一接口的薄壳；
- Token 认证的插件与捕获接口；
- 日报推送（飞书机器人）与可选 Webhook 投递；
- 数据导出与日志。

公网管理面、浏览器控制台、阅读面与附件通过 Authelia 双因素认证网关访问；局域网保留表单登录。`/mcp` 与插件接口继续使用各自的 Bearer 鉴权，Token、环境与登录态等管理能力仍不进入 MCP。

`/mcp` 权限分级：

| 级别 | scope | 能力 | 规则 |
|---|---|---|---|
| 读 | `intelligence:read` | 读卡、检索、新卡、日报、画像、评分标准、来源体检 | 已上线 |
| 写回 | `profile:write`、`evaluation:write` | 推送画像、写回分诊与知识卡拆解 | 已实现；定时 agent 仅授予所需权限 |
| 采集 | `collect:write` | 发起注册表声明的按需调研、提交链接捕获、读取同 Token 的捕获回执 | 只走 durable queue、熔断与资源调度，不改常驻配置；设单日次数上限。调研进度 `get_research_run` 要求 `intelligence:read` |
| 交互专属 | `sources:write`、`verification:write` | 新增、修改、启停来源；知识商机卡 `set_research_status`，以及保留的 `submit_verification`、`set_opportunity_follow_up` | 只给交互式 agent，定时 Token 不授予；Token 隔离限制能力，具体变更仍须依据用户确认。改动留审计，来源停用不删除数据 |
| 不开放 | — | Token 管理、浏览器环境与登录态、部署、导出 | 通过已登录管理面操作；部署仍走运维流程 |

完整 MCP 工具清单见 [agent 安装指南](../INSTALL-for-agents.md#5-mcp-工具与权限)。`captures:write` 属于 `/api/capture/v1/`，`cards:read` / `assets:read` 属于插件读取接口，与 MCP scope 分开。`feedback:write` 虽在 Token 允许集合中，当前无对应 MCP 工具。

## 6. 明确非目标

InfoOS 不负责：

- 用户的决策、任务、认知、项目管理与写作发布，这些属于 PKM；
- 自行调用 LLM API 做翻译、摘要、评估或图片理解；
- 读取 PKM 原文件，或保存关注画像以外的个人资料；
- 保存商机接触对象的联系方式或聊天全文，或代用户在平台上入群、私信、评论；
- 提供与原生平台竞争的个性化信息流；
- 未经用户确认，根据反馈自动改变评估依据；
- 绕过平台注册表的任意搜索、研究或自动登录；
- 保存、代填或读取账号密码、MFA、Cookie；
- 以某个平台、AI 主题或某个 Web 技术定义产品边界。

## 7. 运行现实与未关闭事项

每个安装的运行真相位于用户自己的 Linux Docker Appliance 与持久卷，当前健康、平台登录与采集能力须用该环境证据确认。公共源码不携带维护者运行数据或私人验收记录；历史内置 LLM 链已退役，不能据旧验收记录视为当前能力。原生 systemd 只保留遗留辅助维护资料，不代表新安装具备或运行这些服务。

公网网关是可选模板，不能从源码实现推断已部署。离机备份、升级回滚、资源配额、完整从零安装和长期稳定性均须在目标环境验证，按部署手册记录真实结果和缺口。

## 8. 成功标准

判断产品工作是否完成，优先检查：

- 三类输入是否都进入统一对象链，信息卡能否追溯到 Source、SourceRun 和 RawItem；
- 每条评估能否追溯到画像版本、rubric 版本与 evaluator，低分卡是否仍可检索；
- InfoOS 运行时是否不再产生任何 LLM 调用；
- 日报是否冻结、可引用，知识卡分栏、上限及时效规则是否成立；
- agent 能否只通过 `/mcp` 完成“读新卡 → 写回分诊、拆解与归并 → 看日报 → 读卡”与“发起调研或捕获 → 查进度 → 读结果”，深链接是否可以打开；定时 agent 的 Token 是否无法改来源或画像；
- 公网管理面与阅读面是否要求双因素认证，内部鉴权路径是否不可外部访问，MCP 与插件接口是否仍要求各自的 Bearer 鉴权；
- 运行结论是否来自目标环境，而非 Mac、隔离测试或遗留 systemd 辅助面。
