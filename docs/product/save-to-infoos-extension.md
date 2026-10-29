# Save to InfoOS 浏览器扩展需求

> 文档版本：v1.1
>
> 日期：2026-07-30
>
> 状态：服务端合同已冻结；扩展实现独立推进
>
> 目标环境：用户日常使用的 Chrome/Chromium 浏览器
>
> 服务端目标：InfoOS Docker Appliance 认证产品入口

关联文档：

- [InfoOS 手动浏览器捕获与按需媒体交付需求](browser-capture-contract.md)

## 1. 目标

Save to InfoOS 是安装在用户日常浏览器中的轻量采集扩展。它只解决一个问题：

> 用户正在浏览网页时，可以用一次明确操作，把当前链接、选区或当前可读正文交给 InfoOS，并立即知道请求是已接收、已存在还是失败。

扩展不是阅读器、收藏夹、AI 助手或 Obsidian 写入工具。用户临时保存到 Obsidian Inbox 时继续使用官方 Obsidian Web Clipper；需要进入 InfoOS 的抓取、媒体处理、转录、去重和长期证据链时使用 Save to InfoOS。InfoOS 自身不调用 LLM，理解与知识整理交给外部 agent。

## 2. 产品边界

### 2.1 扩展负责

- 保存当前标签页 URL；
- 读取页面标题、canonical URL、语言和用户选中的文本；
- 在用户明确选择时提取当前页面的可读正文；
- 向 InfoOS 捕获 API 提交版本化请求；
- 使用稳定的 `client_capture_id` 支持安全重试；
- 展示已接收、重复、拒绝和连接失败；
- 提供进入 InfoOS 任务或信息卡的链接；
- 保存 InfoOS 地址、最小权限 Token 和扩展 UI 配置。

### 2.2 扩展不负责

- 直接写入 Obsidian；
- 自动采集浏览历史、后台页面或全部打开标签页；
- 自动判断信息是否重要；
- 在浏览器中调用 LLM；
- 保存或上传 Cookie、localStorage、密码、MFA、表单值或浏览器 Profile；
- 执行 InfoOS 的正文抓取、媒体下载、转录、翻译或信息卡生成；
- 建立本地收藏数据库、离线阅读库或长期失败队列；
- 管理 InfoOS 专用 Chrome、OpenCLI、CDP、VNC 或 Browser Bridge；
- 默认生成完整 SingleFile 页面归档。

## 3. 与 Obsidian Web Clipper 的双入口

两个扩展必须保持不同名称、图标、快捷键和动作语义：

| 入口 | 用户意图 | 结果 |
|---|---|---|
| Obsidian Web Clipper | 临时放进个人 Inbox，稍后人工处理 | 直接生成 Obsidian Markdown |
| Save to InfoOS | 交给系统完整抓取、处理和形成证据卡 | 创建 InfoOS 捕获请求 |

Save to InfoOS 不检测、不修改也不接管 Obsidian Web Clipper 的设置和文件。两条入口允许同时安装。

## 4. MVP 平台与入口

### 4.1 浏览器范围

- MVP 支持 macOS 上的 Google Chrome Stable；
- 实现采用 Chromium Manifest V3，可兼容 Edge、Brave 等 Chromium 浏览器；
- Firefox、Safari、iOS 和 Android 分享入口不属于 MVP；
- 扩展安装在用户日常浏览器中，不安装在 InfoOS 专用采集 Profile 中。

### 4.2 用户入口

MVP 提供：

1. 工具栏按钮；
2. 浏览器右键菜单“交给 InfoOS”；
3. 浏览器右键菜单“把选中内容交给 InfoOS”；
4. 可配置快捷键。

工具栏弹窗只保留一个主按钮“交给 InfoOS”，并提供捕获模式选择。不得加入标签、分类、AI 提示词、工作流或复杂表单。

## 5. 捕获模式

### 5.1 URL 模式

默认模式。提交：

- 当前 URL；
- canonical URL；
- 页面标题；
- 页面语言；
- 用户选区；
- 捕获时间；
- 页面来源域；
- 客户端版本。

服务端自行选择平台 connector 或通用网页抓取。

### 5.2 当前正文模式

只有用户明确选择“同时提交当前正文”时才启用。适用于：

- 登录后页面；
- 付费墙后页面；
- InfoOS 服务端无法访问的页面；
- 页面正文依赖浏览器端渲染。

扩展必须先从克隆的 DOM 中提取主要正文，再按 InfoOS capabilities 声明的格式和大小上限提交。默认不发送完整 `document.documentElement.innerHTML`。

正文模式禁止包含：

- `input`、`textarea`、`select` 当前值；
- 密码框、MFA、支付和账户信息表单；
- Cookie、localStorage、sessionStorage；
- 扩展存储和浏览器账户信息；
- iframe 内不可验证的跨域内容；
- 脚本、事件处理器和可执行对象。

### 5.3 完整快照

SingleFile 或完整页面快照不属于 MVP。只有真实样本证明“URL + 可读正文”不足后，才单独立项。

## 6. 首次连接与配置

设置项：

- InfoOS 产品入口地址；
- 捕获 Token；
- 默认捕获模式；
- 是否在成功后自动关闭弹窗；
- 可选快捷键说明。

地址规则：

- 正式连接只允许 HTTPS；
- HTTP 仅允许 `localhost`、`127.0.0.1` 和 `::1`，用于本机或 SSH 隧道测试；
- 地址不得包含用户名、密码、查询参数或锚点；
- 扩展只连接产品 HTTP(S) 入口，不连接其他 InfoOS 内部端口。

Token：

- 只允许 `captures:write`；
- 不复用 Obsidian 的 `cards:read`/`assets:read` Token；
- 默认遮罩，不进入日志、错误、剪贴板或页面 DOM；
- 用户可以清除和替换；
- MVP 允许用户从 InfoOS Token 页面复制一次后粘贴，自动配对留待后续。

连接测试调用：

```http
GET /api/capture/v1/capabilities
Authorization: Bearer <capture-token>
```

测试成功后显示 Interface 版本、允许模式和服务端限制，不显示 Token。

## 7. 冻结 API 合同

### 7.1 提交

```http
POST /api/capture/v1/items
Authorization: Bearer <capture-token>
Idempotency-Key: <client_capture_id>
Content-Type: application/json
```

```json
{
  "schema": "infoos.capture-request.v1",
  "client_capture_id": "cap_client_uuid",
  "url": "https://example.com/article",
  "canonical_url": "https://example.com/article",
  "title": "Article title",
  "selected_text": "optional",
  "page_language": "zh-CN",
  "captured_at": "2026-07-30T10:00:00+08:00",
  "capture_mode": "url_only",
  "rendered_content": null,
  "client": {
    "name": "save-to-infoos",
    "version": "0.1.0"
  }
}
```

`capture_mode` 只允许：

- `url_only`
- `readable_content`

正文模式的 `rendered_content`：

```json
{
  "format": "readability_html",
  "html": "<article>...</article>",
  "text": "plain text fallback",
  "byline": "optional",
  "excerpt": "optional",
  "site_name": "optional"
}
```

### 7.2 回执

```json
{
  "schema": "infoos.capture-receipt.v1",
  "capture_id": "cap_server_id",
  "status": "accepted",
  "disposition": "new",
  "raw_item_id": "optional",
  "task_id": "optional",
  "card_id": null,
  "infoos_url": "/?menu=cards&capture_id=cap_server_id",
  "request_id": "req_id"
}
```

`status`：

- `accepted`
- `duplicate`
- `rejected`

`disposition`：

- `new`
- `refresh`
- `unchanged`
- `idempotent_replay`

服务端可以向回执增加 `processing_status`、`error_code`、`created_at` 和
`updated_at`。`pending` 是 `processing_status`，不是新的 disposition；
扩展必须忽略未知附加字段并保持 v1 向后兼容。

错误沿用：

```json
{
  "code": "stable_error_code",
  "message": "safe user-facing message",
  "request_id": "req_id"
}
```

扩展不得根据错误文本推断状态，只读取 HTTP 状态和 `code`。

## 8. 提交与重试语义

1. 每次用户操作先生成 `client_capture_id`；
2. 同一用户操作的网络重试必须复用该 ID；
3. 收到 HTTP 响应后保存最近一次安全回执，不保存提交正文副本；
4. 超时或网络失败时显示“未确认”，允许用户显式重试；
5. 不在后台无限重试；
6. `401` 提示 Token 无效或已撤销；
7. `403` 提示权限不足；
8. `413` 提示当前正文超过服务端限制，可改用 URL 模式；
9. `409`/`200 duplicate` 显示已存在并提供 InfoOS 链接；
10. 服务器 `5xx` 不转换为成功。

URL 模式收到 `202` 只表示证据已持久化且任务已入队。扩展可以立即提供
`infoos_url`，但不得把 `202` 推断为信息卡已经生成；最终状态由
`GET /api/capture/v1/items/{capture_id}` 查询。

## 9. 状态与反馈

弹窗状态：

- 未配置；
- 正在检查连接；
- 可提交；
- 正在提交；
- 已接收；
- 已存在；
- 未确认，可重试；
- 被拒绝；
- Token 无效；
- 服务不可达。

成功反馈至少显示：

- 状态；
- 页面标题；
- 捕获模式；
- “在 InfoOS 查看”；
- “关闭”。

不得显示原始响应、Token、完整正文或内部堆栈。

## 10. 隐私与安全

- 所有采集必须由用户逐次触发；
- `activeTab` 权限优先，避免常驻全站读取权限；
- 所需 host permission 只覆盖用户配置的 InfoOS 地址；
- 页面正文读取权限只在明确动作发生后使用；
- URL 和内容提交前执行字段白名单；
- 不记录 Token、Authorization header、正文或私密 URL 查询参数；
- 扩展页面执行严格 CSP，不加载远端脚本；
- Token 使用浏览器扩展存储，不写入普通网页存储；
- 发布包必须可重现并列出权限用途；
- 禁止未经许可证审查直接复制 AGPL 项目代码。

## 11. 非功能要求

- URL 模式点击后应立即进入提交状态，服务端处理保持异步；
- 弹窗关闭后不得继续读取页面正文；
- 相同 `client_capture_id` 的重试结果稳定；
- 浏览器重启后保留地址和 Token，但不保留捕获正文；
- 扩展不影响页面滚动、输入和现有快捷键；
- 所有网络请求有明确超时；
- 中文为 MVP 默认界面，字符串集中管理。

## 12. 并行开发边界

Save to InfoOS 实现方拥有：

- Manifest、扩展 UI、content script、service worker；
- 页面字段提取和可读正文提取；
- 地址、Token 和客户端幂等状态；
- API client 与错误映射；
- 扩展自动化测试和发布包。

InfoOS 实现方拥有：

- `/api/capture/v1`；
- Token scope、鉴权、限制和错误代码；
- 去重、持久化、任务和信息卡链；
- InfoOS 查看链接。

扩展实现不得直接连接 SQLite、OpenCLI 或内部 worker，也不得通过猜测服务端表结构完成去重。

## 13. MVP 验收

1. Chrome Stable 中可以通过按钮、快捷键和右键提交当前页面；
2. URL 模式不读取或上传完整 DOM；
3. 当前正文模式只有用户明确选择后才读取正文；
4. 登录页面捕获不包含 Cookie、localStorage、密码、MFA 和表单值；
5. 正确 Token 返回已接收或已存在；
6. 撤销 Token 后下一次请求立即失败；
7. 网络超时后重试复用 `client_capture_id`，不产生重复 raw item；
8. 超大正文可降级为 URL 模式；
9. 成功后可以打开对应 InfoOS 页面；
10. 扩展与 Obsidian Web Clipper 可同时启用且快捷键不冲突；
11. 单元测试、类型检查、构建、权限审计和真实 Chrome 验收通过。
