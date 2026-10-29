# 抖音推荐流变现初筛

实验性登录来源；使用者自行登录并遵守平台条款。初筛是确定性宽召回，不证明经营或成交；默认开关关闭。

## prefilter_v5：强弱信号分层

当前规则如下。规则真源为 [domain 词表](../../backend/domain/sources/monetization_prefilter.py)，`TOKENS` 保留字段类别，`STRONG_TOKENS` / `WEAK_TOKENS`（小红书加 `XHS_` 前缀）定义两类；投影的 `token_strengths` 随本平台版本进入既有 FETCH 指纹，不影响另一平台或关闭初筛的来源。适配器不维护另一份词表。ASCII 大小写折叠、同字段同词去重保持不变。

### 最终词表

弱信号：`@`、`：`、`v`、`V`、`🌍`、`🌏`、`👇`、`⬇️`、`戳`。

强信号（仍按原字段类别限定使用范围）：

- `contact`：`卫星`、`🛰`、`vx`、`wx`、`微信`、`薇`、`威`、`✉️`、`📧`、`邮箱`、`私信`、`私我`、`私聊`、`私戳`、`私v`、`私滴`、`滴滴`。
- `commercial_intent`：`合作`、`商务`、`咨询`、`带货`、`橱窗`、`同款`、`代理`、`加盟`、`接单`、`定制`、`课程`、`学员`、`陪跑`、`训练营`、`社群`、`粉丝群`、`进群`。
- `redirect`：`全网同名`、`置顶`、`看主页`。
- `account_reference`：`小号`、`大号`。
- `comment_demand`：`求教程`、`求链接`、`求分享`、`怎么做`、`怎么弄`、`在哪买`、`多少钱`、`出教程`、`蹲`。

弱词表示标点、引用、单字联系方式暗示或泛指向，单独或组合都不足以构成变现线索；保留其它词为原有宽召回强信号，避免本轮扩大收紧范围。小红书额外将 `⬇️` 与 `👇` 同类处理；单字“私”仍不在词表。强信号只是初筛资格，不代表已证实变现。

### 判定与证据

至少一个适用于当前字段的强信号才可能 matched；评论需求词仍须达到原 `demand_min_comments` 阈值，单条需求评论不因标为 strong 而绕过阈值。抖音原作者简介、被 @ 账号简介与其置顶描述均使用同一强弱映射；弱命中不会跳过原评论检查。非空粉丝群对象（原作者 `r_fans_group_info`、目标 `mention_fans_group`）沿用已有命中语义，作为 `strength=strong` 的结构化群信号。

资料中的店铺/橱窗权限、电商权限、认证与其它原本仅记录的信号仍不能单独命中。`unknown` 语义不变：证据不足按原路径保留；可选抖音被 @ 资料/置顶失败仍不改变原判定。仅弱信号且原未命中条件满足时为 missed，保留轻量 `prefilter_misses`，不成卡、不下载。

每个新增 hit 包含 `field`、`token`、`strength`（`strong` / `weak`）；跨检查阶段合并而不覆盖已有弱 hits，轻量 misses 同样保存 hits。原 token_hits 仍计观察到的词，不等于强词命中数。既有群信号也标注 strength；历史不含 strength 的证据继续原样读取、展示，不重判、不回填。候选仍按 `prefilter.status in (matched, unknown)`，调度、停留、条数、评论阈值与来源配置均不变。

## 采集顺序与请求边界

来源 `raw_metadata.monetization_prefilter` 为布尔，默认 false；评论数门槛默认 20、需求评论阈值默认 2，范围以来源配置校验为准。普通视频先读取作者资料，命中按上述强弱规则；未满足条件时按原视频首屏评论判定，不读回复。广告、直播无初筛请求，不做点赞、评论、关注或入群互动。

### 被引用账号资料

- 作者资料成功后，从 `signature_extra` 按数组出现顺序选取 `type===0`、非空字符串 `sec_uid`、非本人的元素，去重后最多两个。缺失或非数组不产生目标；不靠昵称搜索，不递归追踪目标的引用。
- 即使原作者已命中，也依次读取所有选中目标。同一推荐页内复用 `/aweme/v1/web/user/profile/other/` GET、登录凭据与 fetch/JSON 共用的 5 秒 deadline；每次目标请求前，等待上一笔资料请求结束后至少 1 秒（上一笔失败也等待），无重试。
- 校验目标返回的 `sec_uid` 精确一致、简介为字符串。简介使用除 `comment_demand` 外的当前强弱词表，ASCII 大小写折叠后去重，命中记为 `mention_signature`；非空对象群信息记为 `mention_fans_group: non_empty`。每个目标保留自身命中；合并到条目 `hits` 时按 field/token 去重。
- 目标命中即可保留条目；目标失败独立记录，不改变原作者的 matched/missed。未命中时继续原评论判定；原作者资料失败、必要评论失败仍按原规则为 unknown。
- `prefilter.mentions` 保存 `{sec_uid, profile_ok, signature, hits, has_fans_group}`；未读到简介时为空字符串。`author_profile` 在原简介后追加“被 @ 账号简介”，每目标一段并区分空简介与未取得；`screening_evidence` 列出合并后的 mention 命中，两个 block 的 `raw_payload` 均保存 mentions。视频正文保持原值。
- 运行与 FETCH 摘要新增 `mention_targets`（选中目标总数）、`mention_profiles_ok`（读取成功总数）、`mention_hit_items`（包含目标简介或群命中的观察条目数）。同一条多个目标命中只计一个 hit item，原作者本来就已命中的条目也计入；不同观察条目的相同目标分别计数。`prefilter_misses` 仅增加 `mention_sec_uids`，不附目标简介或完整资料。

### 被引用账号置顶

- 对最多两个被 @ 目标。仅在目标资料校验成功后读取该目标作品首屏一次；即使原作者或目标资料已命中，也完成这次读取。GET `/aweme/v1/web/aweme/post/`，参数 `aid=6383`、`sec_user_id=<目标>`、`max_cursor=0`、`count=20`；同一页面内使用 `fetch(url.toString(), {method: 'GET', credentials: 'include', signal})`。共用请求函数统一传字符串 URL，不跳转页面。
- 每次目标资料／作品请求在前一请求完成后至少等待 1 秒，失败后到下一目标也等待；fetch 与 JSON 解码共用原 5 秒 deadline，无重试。
- 完整 `aweme_list` 仅在页面内处理。按响应顺序选 `is_top === 1 || is_top === true` 的前 3 条；描述先截断为 300 个 Unicode 字符，再用除 `comment_demand` 外的同一词表匹配。ASCII 大小写折叠与去重不变。截断后的文字之外不参与匹配，使返回描述能够解释命中；只保留 `{desc, hits}`，不导出作品 ID、媒体地址、游标或完整作品列表。
- 命中记 `{field: 'mention_pinned', token}`，按 field/token 去重并入该 mention 的 `hits` 与条目 `result.hits`。作品 HTTP／平台／解析／超时／结构异常仅使作品证据失败，不清除作者或目标资料命中，也不改变原评论判定。
- mention 新增 `pinned_checked`（实际发出请求）、`pinned_ok`（未请求为 null、失败为 false、成功为 true）及 `pinned` 数组。首屏正常但无原生置顶是成功且数组为空，不推断账号所有页面均无置顶。选中的置顶描述缺失或非字符串时标为未取得。
- `author_profile` 的“被 @ 账号简介”下，每个目标追加置顶描述，区分“首屏无置顶作品”“置顶作品未取得”“置顶作品未取得（未请求）”；空描述注明“描述为空”。`screening_evidence` 列出 `mention_pinned` 命中；两个 block 的 `raw_payload.mentions` 均保留全部新字段。
- `prefilter_summary` 新增 `mention_pinned_checked`、`mention_pinned_ok`（分别按目标请求／成功数累计，成功含无置顶），以及 `mention_pinned_hit_items`（按观察条目计数，同一条多个目标、多条置顶或多个词命中只计 1；原作者已命中的条目也计入）。原 `mention_hit_items` 保持仅统计目标简介／群命中；`token_hits` 仍按每词命中的观察条目去重。misses 保持只带目标标识。
- 不读取原作者作品、不翻页、不读目标作品评论、不递归追踪、不改变默认关闭状态。

### 单条最坏耗时估算

每条普通视频最多 **3 次资料 + 2 次作品首屏 + 1 次原视频评论 = 6 次 GET**，广告／直播仍为 0。两个目标均成功读取资料时，相较 v2 最多增加 **12 秒**（2 × 5 秒作品请求 + 2 × 1 秒间隔）。若仍需评论，页面内请求与等待预算约 **34 秒**（6 × 5 + 4 × 1）；无需评论约 **29 秒**。默认 40 条请求上限为 240 次，最大 50 条为 300 次。

计入默认 matched/unknown 正常停留上限 20 秒后，单条预算约 **54 秒**；missed 停留上限 2 秒时约 **36 秒**。这些是代码 deadline 与显式等待的预算估算，不是真实墙钟硬上限，不含 OpenCLI 通信、页面事件循环／同步处理延迟与滚轮推进。

matched / unknown 沿用来源正常停留，missed 使用短停留。预算是 deadline 与显式等待估算，真实成功率、总耗时和平台限流须在使用者环境验证。

## 流程与计数

来源配置 `raw_metadata.monetization_prefilter` 开启时，把规则版本、词表、阈值与证据投影纳入 FETCH 指纹；关闭时保持原链路。规则变化不回填历史证据。

missed 只进 run 的轻量 `prefilter_misses`，不写 RawItem、不建卡、不下载；matched / unknown 经原去重与过滤后进入建卡。`author_profile` 与 `screening_evidence` 是 context block，不替换正文。首屏评论数、需求计数、覆盖范围与未读取信号要分开说明，不把缺失当作零。

`prefilter_summary` 覆盖去重前观察，checked / matched / missed / unknown / skipped 与原 fetched / dedup_skipped 不可直接相加。MCP 批读有正文预算，truncated 时须单卡回读证据。账号详情、评论与主页访问有额外成本，真实成功率、吞吐与限流按使用环境验证，合成测试不证明真实平台通过。

代码入口：[规则](../../backend/domain/sources/monetization_prefilter.py)、[版本](../../backend/domain/sources/versioning.py)、[持久化流程](../../backend/application/workflows/acquisition.py)、[证据 block](../../backend/application/pipeline/prefilter_blocks.py)。
