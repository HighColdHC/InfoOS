# 小红书首页 Feed 变现初筛

实验性登录来源；使用者自行登录并遵守平台条款。初筛是确定性宽召回，不证明经营或成交；默认开关关闭。

## xhs_prefilter_v4：强弱信号分层

当前规则如下。规则真源为 [domain 词表](../../backend/domain/sources/monetization_prefilter.py)，`TOKENS` 保留字段类别，`STRONG_TOKENS` / `WEAK_TOKENS`（小红书加 `XHS_` 前缀）定义两类；投影的 `token_strengths` 随本平台版本进入既有 FETCH 指纹，不影响另一平台或关闭初筛的来源。适配器不维护另一份词表。ASCII 大小写折叠、同字段同词去重保持不变。

### 最终词表

弱信号：`@`、`👇`、`⬇️`、`戳`、`dd`。

强信号（仍按原字段类别限定使用范围）：

- `contact`：`🍠`、`绿泡泡`、`绿色软件`、`vx`、`wx`、`v❤`、`滴滴`、`微信`、`邮箱`、`私信`、`私我`、`私聊`、`私戳`、`私v`、`私滴`。
- `commercial_intent`：`合作`、`商务`、`店铺`、`橱窗`、`链接`、`群聊`、`进群`、`粉丝群`、`课程`、`陪跑`、`代理`、`同款`。
- `redirect`：`主页`、`看简介`、`看主页`、`置顶`、`全网同名`。
- `account_reference`：`小号`、`大号`。
- `comment_demand`：`求链接`、`怎么买`、`哪里买`、`求教程`、`求分享`、`多少钱`、`蹲`、`出教程`。

弱词表示标点、引用、单字联系方式暗示或泛指向，单独或组合都不足以构成变现线索；保留其它词为原有宽召回强信号，避免本轮扩大收紧范围。小红书额外将 `⬇️` 与 `👇` 同类处理；单字“私”仍不在词表。强信号只是初筛资格，不代表已证实变现。

### 判定与证据

至少一个适用于当前字段的强信号才可能 matched；评论需求词仍须达到原 `demand_min_comments` 阈值，单条需求评论不因标为 strong 而绕过阈值。小红书标题、正文、话题、作者简介和置顶标题均使用同一强弱映射；弱命中仍按原顺序检查首屏评论、再进入有界主页批次。可见群入口 `author_group_entry: visible` 沿用已有命中语义，标为 `strength=strong`。结构化 @ 仍不追踪，不新增二级账号读取。

资料中的店铺/橱窗权限、电商权限、认证与其它原本仅记录的信号仍不能单独命中。`unknown` 语义不变：证据不足按原路径保留；可选抖音被 @ 资料/置顶失败仍不改变原判定。仅弱信号且原未命中条件满足时为 missed，保留轻量 `prefilter_misses`，不成卡、不下载。

每个新增 hit 包含 `field`、`token`、`strength`（`strong` / `weak`）；跨检查阶段合并而不覆盖已有弱 hits，轻量 misses 同样保存 hits。原 token_hits 仍计观察到的词，不等于强词命中数。既有群信号也标注 strength；历史不含 strength 的证据继续原样读取、展示，不重判、不回填。候选仍按 `prefilter.status in (matched, unknown)`，调度、停留、条数、评论阈值与来源配置均不变。

## 页面与请求边界

来源 `raw_metadata.monetization_prefilter` 为布尔，默认 false。开启时 `screening-note` 在一次详情导航中读取正文、标题、话题、作者 ID 和已加载首屏根评论；不增加评论接口请求或第二次详情导航。未开启时保持原详情路径。

主页读取简介和有明确 DOM 置顶标志的标题；简介合法空串视作读取成功。缺少渲染卡片且无法确认作品数时，置顶为 unknown，不推断无置顶。可见群按钮只记录不点击；店铺入口仅作证据。评论按 ID 去重，排除作者与 is_author 标记者，每条需求最多计一次，不展开子回复；结构化 @ 不追踪。

### 判定与分批（强词准入按上节）

1. 笔记标题、正文、话题命中适用强词即 matched。否则先判断详情中已有的首屏根评论，排除作者评论，每条含任一需求词只计一次；可用评论达到原阈值即 matched，不进入主页目标列表。没有额外评论请求或导航。
2. 剩余条目按作者 ID 去重，总上限仍为 20 位，顺序拆成每批最多 5 位，各调用一次 `screening-profiles`。12 位作者为 5/5/2 三批。适配器已有的批内至少 3 秒间隔不变；连接器在上一批返回或抛异常后，等待至少 3 秒再调用下一批。
3. 简介、置顶或可见群入口命中仍为 matched。笔记详情、主页简介、置顶和评论证据完整且均未命中才为 missed；其余 unknown，按命中保留。店铺入口仍只记录，不单独命中。
4. 每批异常仅使该批作者 unknown，不重跑，继续后续批次。仅当返回行出现 `login_or_risk` 才停止后续批；未调用的作者记录 `profile_checked=false`、`profile_error=login_or_risk`。适配器原有导航异常停止当前批的行为保留，但不会阻止连接器调用下一批。

当前规则版本为 `xhs_prefilter_v4`，启用来源的 FETCH 指纹包含版本与词表；跨平台指纹独立。适配器无需修改，5 位批次上限由连接器在调用边界执行。

### 错误与计数

批次异常不再丢弃原因：受影响记录的 `prefilter.profile_error_detail` 保存异常类型、消息、可用的 OpenCLI stderr 摘要及异常链；先去掉本轮作者 ID、主页路径中的作者 ID、URL 查询参数/片段和 token，再折叠空白并截断至最多 300 字符。`profile_error` 使用稳定代码 `batch_failed` 或 `batch_timeout`，超时识别包含 `_load_payload` 包装的 `TimeoutExpired`。返回缺行同样为 unknown，记录 `batch_failed` 和缺行原因。

每个实际调用批次的结果携带 `profile_batch_index` 与 `profile_batch_error`，供 `run_evidence` 去重聚合；同作者多条笔记不会重复计批。`prefilter_summary` 新增：

- `profile_batches`：实际调用的批数；风险后未执行的批次不计入。
- `profile_batches_failed`：发生异常、缺行、返回错误或主页/置顶不可用的批数。
- `profile_batch_errors`：稳定代码到失败批数的映射。一个失败批只计一次；返回明确风险优先归为 `login_or_risk`，超时归为 `batch_timeout`，其余归为 `batch_failed`。

`profiles_attempted_unknown` 仍区分进程失败/缺行导致的访问情况未知与风险后明确未调用；`comments_checked_items` 现在统计文本未命中后实际进入评论判断的条目，不再依赖主页是否成功。已有 RawItem、SourceRun 与 FETCH artifacts 的 JSON 证据通道承载新字段，不新增存储结构。

主页批内与批间至少等待 3 秒，批次不自动重试；每次命令至少 600 秒的既有超时设置不代表整轮耗时承诺。小批次增加进程/Bridge 开销。真实采集与正向群入口仍需在使用者环境验证。

## 流程与计数

来源配置 `raw_metadata.monetization_prefilter` 开启时，把规则版本、词表、阈值与证据投影纳入 FETCH 指纹；关闭时保持原链路。规则变化不回填历史证据。

missed 只进 run 的轻量 `prefilter_misses`，不写 RawItem、不建卡、不下载；matched / unknown 经原去重与过滤后进入建卡。`author_profile` 与 `screening_evidence` 是 context block，不替换正文。首屏评论数、需求计数、覆盖范围与未读取信号要分开说明，不把缺失当作零。

`prefilter_summary` 覆盖去重前观察，checked / matched / missed / unknown / skipped 与原 fetched / dedup_skipped 不可直接相加。MCP 批读有正文预算，truncated 时须单卡回读证据。账号详情、评论与主页访问有额外成本，真实成功率、吞吐与限流按使用环境验证，合成测试不证明真实平台通过。

代码入口：[规则](../../backend/domain/sources/monetization_prefilter.py)、[版本](../../backend/domain/sources/versioning.py)、[持久化流程](../../backend/application/workflows/acquisition.py)、[证据 block](../../backend/application/pipeline/prefilter_blocks.py)。
