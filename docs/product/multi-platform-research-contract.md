# 多平台调研功能合同

> 当前实现补记（2026-08-12）：生财有术关键词调研由 `zsxq:keyword` 提供，使用已登录的官方知识星球站内搜索；旧 `scys:keyword` 已退出 active registry。下文批次、HTTP、持久化和隔离合同保持不变。

## 产品边界

调研功能是采集任务的批次编排层，不是新的知识、事件、决策或报告系统。

官方/实验性分级与持续来源一致：Reddit、知识星球、小红书、抖音、X，以及 YouTube 登录型用途为实验性，不保证可用；Reddit 当前仍要求浏览器登录。YouTube 公开内容登录可选，但仍需浏览器会话。逐平台能力与限制以 [registry](../../backend/domain/sources/registry.py) 为准。

```text
ResearchBatch
  -> 每个平台一个隐藏的 ResearchRuntimeSource
  -> 每个平台一个 FETCH / SourceRun
  -> RawItem
  -> 现有 CARD_BUILD / 富化 / 媒体 / CARD_FINALIZE
  -> InformationCard
```

调研结果仍以信息卡为终点。批次只聚合运行证据、覆盖度、失败缺口和信息卡引用。

## HTTP 合同

- `POST /api/research-runs`：创建批次，必须提供 `Idempotency-Key`。
- `GET /api/research-runs`：按创建时间倒序列出调研批次。
- `GET /api/research-runs/{research_run_id}`：返回批次、平台运行、覆盖度和分页信息卡。
- `GET /api/platform-capabilities`：每个平台提供 `research` 能力对象。

创建请求：

```json
{
  "name": "AI 网站出海",
  "keywords": ["AI 网站出海", "web 出海"],
  "date_range": {
    "start_date": "2025-01-01",
    "end_date": "2026-07-31",
    "timezone": "Asia/Shanghai"
  },
  "platforms": [
    {"platform": "x", "max_items": 100, "sort": "latest", "options": {}}
  ]
}
```

约束：

- 名称 1–120 字符。
- 关键词 1–20 个；去首尾空白并按大小写去重；单个不超过 100 字符。
- 平台 1–10 个且不能重复；只能选择平台注册表声明的调研能力。
- 单平台条数不得超过能力目录上限；总条数不得超过 5000。
- 日期为包含首尾的自然日范围；无日期的平台结果保留，但覆盖度必须声明未知日期数量。
- RSS 必须在 `options.source_ids` 选择已有 RSS 来源。
- 客户端不能指定 source/task ID，不能提交 Cookie、Token 或其他登录材料。

幂等语义：

- 同一 `Idempotency-Key` 与同一规范化请求返回原批次。
- 同一 key 对应不同请求返回 `409 idempotency_conflict`。
- 新建返回 `202`；重放返回 `200`。

## 平台语义

- X、YouTube、Reddit、小红书、抖音、生财有术（`zsxq:keyword`）：平台关键词搜索。知识星球单次公开上限为 100；返回穷尽只代表该关键词在当前账号权限和接口可见范围内穷尽，不代表旧官网或同义关键词全集。
- GitHub：首版调研对象是仓库，不包含 Issue、PR 与代码搜索。
- Hacker News：公开 Algolia story 索引。
- RSS：只在用户选定的已有 Feed 中匹配，不代表全网 RSS 搜索。
- Product Hunt：只过滤公开适配器当前可见 Feed，必须标注覆盖不完整。

## 持久化与隔离

- `task_batches.batch_type = 'research'` 是调研批次身份真相。
- 临时来源使用 `sources.source_scope = 'research'`，不出现在普通来源维护、不参与 scheduler、健康巡检或发布审计。
- 批次、临时来源、根 FETCH 任务必须在一个 `BEGIN IMMEDIATE` 事务内创建。
- 一个批次每个平台一个根 FETCH；平台失败互相隔离，批次可进入 `partial`。
- 同平台多关键词先按稳定平台 ID、再按 canonical URL 去重；保存 `matched_keywords`。
- 跨平台不做模糊去重。
- 任何覆盖不足、日期未知、平台限制和停止原因都必须显式返回，不能把可见样本宣称为完整结果。
