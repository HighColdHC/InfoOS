# InfoOS 手动浏览器捕获与按需媒体交付需求

> 文档版本：v1.1
>
> 日期：2026-07-30
>
> 状态：合同已冻结，InfoOS 首期实现完成，验收未关闭
>
> 当前现实：开发工作区自动化门禁已通过；真实 Chrome、Docker Appliance 与客户端验收尚未关闭
>
> 目标交付：Docker Appliance 单一认证产品入口

关联文档：

- [Save to InfoOS 浏览器扩展需求](save-to-infoos-extension.md)
- [InfoOS 信息源与信息卡系统目标](infoos-intelligence-workbench-target.md)
- [SQLite CRUD Playbook](../engineering/sqlite-crud-playbook.md)

## 1. 目标

InfoOS 新增两个受控能力：

1. 接收用户日常浏览器中逐次明确提交的 URL、选区或当前可读正文，把它们作为持久化原始条目进入现有信息卡链；
2. 为 Obsidian 选择性物化提供轻量目录、按需私有图片、资产离线读取和 InfoOS Web 播放深链接。

本需求只规定捕获输入及信息卡/附件读取；捕获后仍进入统一信息卡链，并可由外部 agent 继续形成知识卡与日报，当前产品终点以 [产品合同](infoos-intelligence-workbench-target.md) 为准。浏览器捕获是输入 adapter；Obsidian 仍是下游客户端。

## 2. 产品边界

### 2.1 InfoOS 负责

- 捕获 Token 和最小权限鉴权；
- 捕获 capabilities、提交和回执查询；
- URL 规范化、平台识别、幂等和内容去重；
- URL 安全抓取和客户端正文安全接收；
- 将捕获内容持久化为 source/run/raw item/task 证据；
- 复用既有信息卡、ContentBlock、CardAsset 和 finalize 链；
- 在现有任务、来源追踪和卡片预览中展示捕获结果；
- 向 Obsidian 提供稳定卡片目录、详情和资产；
- 提供可定位卡片/媒体的 InfoOS Web 深链接；
- 继续由 InfoOS 持久卷拥有视频和其他完整资产。

### 2.2 InfoOS 不负责

- 直接写 Obsidian Markdown；
- 保存 Obsidian 客户端游标、待同步队列或同步回执；
- 判断一张卡是否值得进入用户知识库；
- 自动采集用户浏览历史；
- 接收 Cookie、localStorage、密码或 MFA；
- 新建 bookmark、clip、archive 或 event card 产品对象；
- 为浏览器捕获建立第二套同步任务或 JSONL/YAML fallback；
- 发布 raw CDP、VNC、OpenCLI、debug 或 supervisor 端口；
- 为 Obsidian 内嵌播放器暴露长期 query Token。

## 3. 总体链路

```text
用户日常浏览器
  -> POST /api/capture/v1/items
  -> capture receipt / 幂等
  -> 系统管理的 browser inbox source/run
  -> raw item
  -> CARD_BUILD
  -> CONTENT_ENRICH
  -> MEDIA_DOWNLOAD / VIDEO_TRANSCRIBE
  -> CARD_FINALIZE
  -> /api/plugin/v1（下游读取）
  -> 外部 agent 经 /mcp 分诊、拆解与归并
  -> KnowledgeCard / DailyBrief
```

浏览器捕获不得直接写 `cards` 绕过 raw item，也不得在 HTTP route 内执行长时抓取、媒体下载或 LLM。

## 4. 平台、来源和捕获通道

### 4.1 捕获通道不是产品平台

`browser_extension` 是采集通道，不替代外部来源平台。持久化证据必须同时保留：

- `capture_channel=browser_extension`
- 原始 URL；
- 识别出的外部平台；
- 捕获模式；
- 客户端捕获时间；
- 服务端接收时间；
- 是否使用客户端可读正文。

### 4.2 平台识别

- X、YouTube、Reddit、GitHub、小红书、抖音和其他已登记平台 URL 优先进入共享平台识别规则；
- 已有 per-item 能力时复用对应 adapter；
- 未知公共网页归类为 `web` capture-only adapter；
- `web` 必须进入统一 platform registry，但标记为非配置、非定时、capture-only；
- API、connector 和前端不得分别维护捕获平台列表。

### 4.3 系统来源

不得每捕获一个 URL 新建一个用户来源。

若 repository 外键要求 source 与 platform 对齐，InfoOS 为捕获支持的平台建立稳定的系统管理来源：

```text
browser-inbox::<platform>
```

系统来源：

- 不出现在用户可配置来源数量中；
- 不由 scheduler 规划周期 FETCH；
- 不能由普通来源 CRUD 删除；
- 可以在 Source Run Trace 中作为“浏览器收件箱”查看；
- 每次捕获形成可追踪 run 或 capture batch；
- 删除语义遵守 raw/card/history 外键，不做机械清理。

## 5. Token 与权限

新增 scope：

```text
captures:write
```

规则：

- Token 管理页面支持创建、编辑、撤销和轮换该 scope；
- Save to InfoOS Token 默认只含 `captures:write`；
- `captures:write` 不能读取卡片或资产；
- `cards:read`/`assets:read` 不能提交捕获；
- 明文 Token 只展示一次；
- Token digest 继续使用现有 hashed token store；
- request audit 记录 token_id、scope、结果、request_id 和安全摘要，不记录明文、Authorization header 或捕获正文；
- 撤销和轮换立即作用于新请求。

## 6. Capture Interface

Interface 根：

```text
/api/capture/v1
```

### 6.1 Capabilities

```http
GET /api/capture/v1/capabilities
Authorization: Bearer <captures:write token>
```

```json
{
  "interface_version": "capture.v1",
  "capabilities": ["captures:write", "url_only", "readable_content"],
  "limits": {
    "url_bytes": 4096,
    "title_bytes": 2048,
    "selected_text_bytes": 65536,
    "rendered_content_bytes": 2097152,
    "request_bytes": 2228224
  },
  "accepted_content_formats": ["readability_html"],
  "accepted_content_encodings": ["identity"],
  "rate_limits": {
    "token_requests_per_minute": 30,
    "source_requests_per_minute": 60
  }
}
```

限制是服务端配置和能力声明，客户端不得硬编码更大的值。
MVP 只接受未压缩的 JSON 请求；带 `Content-Encoding` 或传输解压需求的请求
返回稳定错误，不进入业务持久化。

### 6.2 提交

```http
POST /api/capture/v1/items
Authorization: Bearer <captures:write token>
Idempotency-Key: <client_capture_id>
Content-Type: application/json
```

请求 schema 与 Save to InfoOS 文档中的 `infoos.capture-request.v1` 完全一致。

服务端验证：

- body `client_capture_id` 与 header 一致；
- URL 为公共 HTTP(S)；
- `capture_mode` 与正文存在性一致；
- 字段和 UTF-8 字节数在 capabilities 上限内；
- rendered content format 被支持；
- `captured_at` 是带时区 ISO 8601；
- client name/version 是安全短文本。

### 6.3 回执

成功或业务重复返回 `infoos.capture-receipt.v1`：

- `capture_id`
- `status`
- `disposition`
- `raw_item_id`
- `task_id`
- `card_id`
- `infoos_url`
- `request_id`
- `processing_status`
- `error_code`
- `created_at`
- `updated_at`

`disposition` 仍只表示当前捕获与既有身份/内容的关系：
`new|refresh|unchanged|idempotent_replay`。异步处理状态只由
`processing_status=pending|running|succeeded|failed` 表达，不增加
`pending` disposition。

HTTP 建议：

| 场景 | HTTP | status | disposition |
|---|---:|---|---|
| 新捕获已排队 | 202 | accepted | new |
| 同 URL 有新观察版本 | 202 | accepted | refresh |
| 相同客户端请求重放 | 200 | duplicate | idempotent_replay |
| 已有相同稳定内容 | 200 | duplicate | unchanged |
| 请求非法 | 422 | rejected | 无 |
| 正文过大 | 413 | rejected | 无 |

`url_only` 的首次提交或再次观察只做安全校验、证据持久化和持久任务入队，
因此返回 `202` 和 `processing_status=pending`；是否最终为 refresh 或
unchanged 由异步抓取后的内容身份决定，并通过回执查询更新。相同
Idempotency-Key 的安全重放无需等待 worker，立即返回原回执。

### 6.4 查询回执

```http
GET /api/capture/v1/items/{capture_id}
Authorization: Bearer <captures:write token>
```

只有创建该 capture 的 token_id 或同一管理员授权上下文可以读取。响应增加：

- `processing_status`: `pending|running|succeeded|failed`
- `card_id`
- `infoos_url`
- 安全的 `error_code`
- `updated_at`

不返回原始正文、Cookie 或内部堆栈。

## 7. 幂等与去重

### 7.1 请求幂等

- `(token_id, client_capture_id)` 唯一；
- 相同 key 与相同 payload hash 返回原回执；
- 相同 key 与不同 payload 返回 `409 idempotency_conflict`；
- 网络重试不能产生第二个 raw item、run 或初始任务。

### 7.2 内容身份

规范化顺序：

1. 平台 canonical item ID；
2. 页面 canonical URL；
3. 去除追踪参数后的规范 URL；
4. 客户端正文内容 hash。

同一 URL 的再次捕获不能一律静默丢弃：

- 内容或平台版本变化时进入 refresh；
- 内容 hash 未变化时返回 unchanged；
- 每次非幂等用户操作都形成独立 raw observation；用户选区或客户端正文不同
  时必须保留为新的 capture evidence；
- 同一 canonical identity 的多个 observation 仍收敛到同一信息卡，
  不因选区不同生成第二张信息卡；
- 来源相同但 source scope 不同的现有平台规则不得被捕获逻辑破坏。

## 8. 持久化

SQLite 是唯一运行真相。允许新增 `capture_receipts` 运行表，保存：

- capture_id；
- client_capture_id；
- token_id；
- payload hash；
- URL identity/hash；
- canonical identity hash、内容 identity hash 和 observation version；
- capture mode；
- disposition；
- source/run/raw item/task/card 关联 ID；
- processing status；
- error code；
- created_at/updated_at。

`capture_receipts` 不重复保存完整正文。正文进入 raw item/raw payload 或受管原始证据资产。

修改 schema 和 repository 前必须遵守 SQLite CRUD Playbook，提供迁移、索引、外键、并发幂等和备份恢复测试。

## 9. URL 抓取和客户端正文

### 9.1 URL-only

- 只有 registry 明确登记了 per-item capture adapter 的平台才复用对应 workflow；
- 已识别但没有 per-item adapter 的平台保留真实 platform 标签，同时使用
  generic web capture adapter，不伪装为已有 connector 已经支持；
- 公共网页使用共享安全 HTTP transport；
- 遵守代理、DNS 固定、私网阻止和逐跳重定向校验；
- route 只持久化并排队，不同步等待正文抓取；
- 抓取失败记录明确 failure fact，并允许任务重试。

### 9.2 readable_content

客户端正文被视为用户浏览器当时看到的捕获证据：

- 服务端再次执行 HTML sanitize；
- 删除脚本、事件属性、表单和危险 URL；
- `html` 与 `text` 至少一个非空；
- 原始可读正文写入 raw item；
- HTML 快照若保留，作为受管原始证据资产，不直接成为整卡 renderer；
- ContentBlock builder 把正文、引用、图片引用和链接映射到共享 block；
- 不因客户端提供正文而跳过来源、时间和缺口记录。

## 10. 任务与状态

捕获提交后：

- 初始持久化任务进入现有队列模型；
- 公开网页抓取进入 `public_fetch`；
- 需要平台浏览器会话的 per-item 抓取进入 `browser_fetch`；
- card、media、GPU 和 cloud 任务使用现有七队列合同；
- scheduler 不为 capture-only source 创建周期 FETCH；
- pipeline reconciliation 必须覆盖捕获产生的 raw/card 链；
- Capture receipt 只投影任务状态，不成为第二套状态机。

## 11. Plugin Interface 改造

### 11.1 目录范围

`GET /api/plugin/v1/cards` 只返回：

- `card_type=information`；
- `status=active`；
- `version>=1`；
- 有稳定 `content_hash`；
- 当前可发布的卡片。

不得返回 event、decision、output 或其他旧对象。

### 11.2 目录字段

在保持 v1 向后兼容的前提下允许增加：

- `completeness_status`
- `excerpt`
- `source_url`
- `asset_summary`

`asset_summary`：

```json
{
  "image_count": 1,
  "video_count": 1,
  "audio_count": 0,
  "total_size_bytes": 123456,
  "poster_asset_id": "optional"
}
```

目录支持可选过滤：

- `query`
- `platform`
- `completeness`
- `media_kind`

capabilities 必须声明实际支持项。

### 11.3 资产

现有：

```text
GET /api/plugin/v1/assets/{asset_id}
```

继续支持：

- `assets:read`；
- 完整响应；
- 单 HTTP Range；
- `Accept-Ranges`；
- 安全 MIME；
- 受管 assets 根路径限制；
- cleanup state 和稳定卡片交付检查；
- request audit 不记录 Token。

MVP 不新增长期签名 URL。Obsidian 私有图片由插件携带 Bearer 请求；视频在 InfoOS Web 中播放。

## 12. Web 卡片与媒体深链接

新增并稳定支持：

```text
/?menu=cards&card_id=<card_id>
/?menu=cards&card_id=<card_id>&asset_id=<asset_id>
/?menu=cards&capture_id=<capture_id>
```

行为：

- 先完成产品入口认证；
- 定位并打开卡片；
- `asset_id` 属于该卡且可交付时定位媒体；
- 视频使用现有本地资产和 HTTP Range；
- 本地资产不存在时显示原始来源和明确缺口，不静默生成未授权下载；
- 非法、越权或不存在 ID 返回安全 UI；
- URL 不包含 Bearer Token、Cookie 或本机路径。

## 13. InfoOS Web UI

不新增第七个顶级模块。捕获能力进入现有六模块：

- Token：创建 `captures:write` Token；
- 任务：按 capture_id 查看处理状态；
- 来源：显示系统“浏览器收件箱”运行证据；
- 卡片：通过 capture_id/card_id 定位结果；
- 日志：查询脱敏 request_id/capture_id；
- 环境：不增加浏览器捕获登录管理。

## 14. 媒体保留边界

- Obsidian 不下载视频不等于 InfoOS 不处理视频；
- 有音轨视频仍必须完成 `VIDEO_TRANSCRIBE` 或明确失败/不适用；
- 视频、音频和图片的服务端保留由 InfoOS CardAsset 与独立 retention 策略决定；
- 本需求不静默删除现有 InfoOS 资产；
- 播放优先使用 InfoOS 已有本地资产；
- 原始平台链接作为来源和 fallback；
- 以后若需要“仅保留转录和封面”的服务端 retention，必须另行确认并提供可恢复策略。

## 15. 安全与审计

- 捕获 API 只走产品 HTTP(S) 入口；
- body 和压缩后/解压后大小均有限制；
- URL 防 SSRF 规则不得弱化；
- sanitize 后内容才能进入用户可见 block；
- Token、Cookie、Authorization、私密正文不写操作日志；
- request audit 记录 capture_id、结果、字节数、模式和 request_id；
- 错误响应不包含内部路径和堆栈；
- 速率限制按 token_id 和来源地址执行；
- 捕获 Token 连续滥用可撤销，不影响 card read Token；
- InfoOS 不调用 LLM；外部 agent 按用户授权经 MCP 读取内容并写回，不恢复旧 LLM 任务链；
- 完整 URL 查询参数在 UI 和日志展示前脱敏。

## 16. 兼容与迁移

- 现有 10 个可配置平台数量保持不变；`web` capture-only 不计入可配置平台；
- 现有 source CRUD、scheduler cadence 和 Initial Fetch 语义不变；
- 现有 `/api/plugin/v1` 客户端在不使用新字段时继续工作；
- 现有卡片、附件和 Obsidian 全量物化数据不自动删除；
- Docker Appliance 是最终验收环境；Mac 隔离测试通过不能替代容器验收；
- 当前 Linux 原生 systemd 环境不得恢复部署态浏览器捕获职责。

## 17. 并行开发边界

首期只实现 InfoOS 后端、Web UI、Plugin Interface 和深链接。浏览器扩展
`clients/save-to-infoos/**` 与 Obsidian 插件实现不属于本轮写入范围；它们只消费
本合同，不得反向定义服务端 SQLite 或任务合同。

### 17.1 InfoOS 后端

负责：

- token scope；
- capture routes/use case/workflow；
- schema/repository；
- platform registry 与 generic web adapter；
- task/reconciliation；
- plugin catalog；
- deep link 数据接口与安全。

### 17.2 InfoOS 前端

负责：

- Token scope UI；
- capture/card/task 深链接定位；
- 认证媒体播放；
- 安全错误状态。

### 17.3 Save to InfoOS

只依赖本文件第 6 节 Capture Interface，不依赖 SQLite 或内部任务类型。

### 17.4 Obsidian

只依赖本文件第 11、12 节 Plugin Interface 和深链接，不依赖 capture write API。

各实现可先使用文档中的 fixture；变更 schema、状态枚举或 URL 必须同步三份需求文档。

## 18. 验收

### 18.1 Capture API

1. 新 URL 返回 202 accepted 并创建一条可追踪 raw/item task 链；
2. 相同 idempotency key 重放不产生第二条链；
3. 同 key 不同 payload 返回冲突；
4. 同 URL 未变化返回 unchanged；
5. 同 URL 有新内容进入 refresh；
6. 被撤销或错误 scope Token 分别返回稳定 401/403；
7. 超限正文返回 413；
8. 私网、混合 DNS 和非法重定向被拒绝；
9. readable HTML 被清洗，脚本和表单值不进入 block；
10. request audit 不包含 Token 和正文。

### 18.2 Pipeline

1. URL-only 公共网页形成稳定信息卡；
2. 客户端 readable content 形成可追踪 raw item 和共享 ContentBlock；
3. 已知平台 URL 复用平台能力；
4. scheduler 不规划 capture-only source；
5. reconciliation 能修复捕获链缺失任务；
6. 视频处理继续遵守转录合同。

### 18.3 Obsidian Interface

1. L0 目录可分页并只返回稳定 information card；
2. 目录媒体摘要与详情资产一致；
3. 私有图片能用 `assets:read` 按需读取；
4. Range 响应可供 InfoOS Web 视频播放；
5. deep link 正确定位卡片、捕获和资产；
6. 默认选择性物化不要求视频进入 Vault。

### 18.4 环境和质量

1. 隔离测试环境完成 API、任务和浏览器扩展集成验收；
2. Docker Appliance 完成单产品入口、持久卷、Token、真实网页和媒体播放验收；
3. 不暴露任何内部浏览器或调试端口；
4. SQLite migration/backup/restore 测试通过；
5. 后端、前端、架构守卫、文件大小、构建和真实浏览器验收通过；
6. 验收记录同时包含 commit、工作树状态、容器 image/config/volume 和真实任务链证据。
