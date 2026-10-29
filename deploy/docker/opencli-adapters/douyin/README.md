# InfoOS 抖音 OpenCLI 扩展

这些命令使用 InfoOS 专用 Chrome 登录态。视频可用数字 ID 或 `https://www.douyin.com/video/<id>`。

| 命令 | 用途 |
|---|---|
| `douyin profile-info <sec_uid-or-url>` | 作者公开简介 |
| `douyin user-collections <sec_uid-or-url>` | 作者主页公开合集名称、ID 与链接 |
| `douyin feed --limit 10` | 打开网页版“推荐”，逐条下刷并标记广告和直播 |
| `douyin comment-targets <video> [--parent-id <cid>]` | 一级评论或指定评论的回复与 ID |
| `douyin like <video>` | 视频点赞并读回状态 |
| `douyin comment <video> <text>` | 发表评论，返回平台评论 ID |
| `douyin reply <video> <comment-id> <text>` | 回复首页可见的一级评论 |
| `douyin group-list` | 网页消息面板中可识别的已加入群和最新预览 |
| `douyin group-messages <group-name>` | 指定群的最新消息预览 |
| `douyin group-join <author-sec_uid-or-url>` | 点击作者主页唯一可见的网页入群入口并核验列表变化 |
| `douyin dm-contacts` | 列出网页消息列表中可识别的一对一联系人及精确收件人参数 |
| `douyin dm-target <recipient>` | 只打开并核对私信目标，不发送 |
| `douyin dm-send <recipient> <text>` | 向作者或已在消息列表中的联系人发送一条文字私信 |

群消息当前只能读取消息列表中的最新预览，不支持群内历史消息或群内回复。群列表依靠名称和预览文本辨识群聊，可能漏掉未显示“群”字的会话；`group-join` 未做真实入群测试。评论命令返回 `accepted` 表示提交接口返回成功与评论 ID，公开可见性仍须重新读回。写命令只提交一次，不自动重试。

`user-collections` 读取作者主页“合集”标签当前加载的公开条目；无合集时返回空列表，页面加载失败则报错。私信的 `recipient` 可以是作者 `sec_uid`、完整主页 URL，或 `contact:<dm-contacts 返回的完整名称>`。联系人名称必须在当前可见列表中唯一；发送前会核对会话标题、个人会话和草稿，发送后核对自己发出的新消息已出现在会话中。正文仅支持非空单行文字，最多 500 字符。平台发送结果不明时命令报错，不要自动重试；目标可能限制陌生人私信。实际发送会对外投递消息，请先用 `dm-target` 核对收件人。

`feed` 使用当前账号的网页版“推荐”页面及真实滚轮事件；返回顺序包括广告和直播，`is_ad` 来自页面渲染数据的 `isAds/rawAdData`，`is_live` 标记直播。此命令每次从推荐页开始，默认 10 条，最多下刷 50 条。`--dwell_min/--dwell_max` 是 0–60 的整数秒数，默认均为 0，且 min 不超过 max；启用停留时每条随机等待指定范围，广告额外等待 3–8 秒，默认零停留保留原有 1 秒轮询。正式 `douyin/home_feed` 来源默认读取 40 条、停留 6–20 秒。

先打开 `/jingxuan` 再点击推荐入口；仅打开阶段的 `Detached while handling command` 重试一次，下刷不重开。站点未前进、跳条、条目不可读或会话断开时停止：已读到条目就返回真实的部分结果，首条不可读则失败。输出包含作者标识、互动计数、秒级时长、封面及按优先级去重的 `play_urls`，以及解析双层 JSON 的广告字段；广告 ID 保留原始数字字符串。直播没有下载地址，正式来源在连接器中跳过直播并计入 `dedup_skipped_count`。

`--with_meta true -f json` 返回 `{items: [...], acquisition: {...}}`，默认关闭时仍为原条目数组。
已核实 Docker 固定的 OpenCLI 1.8.6：`commanderAdapter.js` 把执行返回值直接交给 `output.js`，JSON 分支直接 `JSON.stringify`，不把对象包装成数组，也不按 columns 裁剪。
正式来源启用此参数。`observed_count` 是过滤直播、广告和去重前的实际读取数；仅读满 requested 且没有停止原因时 `complete=true`。
`stop_reason` 是稳定代码 `no_progress`（两次下刷未前进）、`skipped_position`、`item_unreadable`、`detached` 或 `unknown`；`stop_position` 是停止时最后成功读到的位置。
`stop_message` 使用固定安全文案且最多 160 字符，不包含账号或视频身份；原始异常与原 code 只保留在 `FeedReadError.cause`，不导出。

```json
{"items":[{"index":1,"title":"示例条目"}],"acquisition":{"requested_count":40,"observed_count":1,"complete":false,"stop_reason":"no_progress","stop_position":1,"stop_message":"Feed did not advance after two scroll attempts"}}
```

部分批次照常入库、生成 CARD_BUILD，FETCH 和 run 的执行状态仍为 success；`raw_payload`、任务 artifacts 与 checkpoint 另存 `acquisition`、`completeness_status=partial`、requested/observed/stop_reason，run 的 `error_summary` 记录原因。没有 metadata 的来源保持未知，不推断完整。
重放原样保留这些证据，不重试采集、不补刷；平台仍记成功。Web 显示部分完成计数与原因。
MCP 来源体检按 `[since, until)` 内 FETCH 的 `finished_at` 统计 `fetch_run_count`、`partial_run_count`、`partial_run_rate`（部分轮数/全部轮数，无轮次为 null）及最近部分轮次的 `latest_stop_reason`，无新卡但有运行的来源也保留；无 metadata 的旧轮次计入分母，但不被当作完整证据。verdict 仍只依据卡片评估。

- `search <query> --sort <排序> --publish-time any|day|week|half-year`：关键词搜索可按排序与发布时间筛选，默认保留上游综合搜索。

## 推荐流变现初筛（默认关闭）

来源 `raw_metadata.monetization_prefilter=true` 时，后端将版本化词表及阈值投影为 `feed --prefilter <JSON>`；适配器没有内置词表。`prefilter_comment_min_count` 默认 20，范围 0–1,000,000；`prefilter_demand_min_comments` 默认 2，范围 1–20。关闭时不传此参数，保持原命令、输出、停留与来源版本指纹。

每条普通视频原位请求作者资料一次；简介命中词表或 `r_fans_group_info` 为非空对象即命中。其余商业入口、企业权限（JSON 二次解码）、认证、群卡展示与 `textExtra[].isCommerce` 只留证据。资料未命中且评论数达阈值时，再原位请求评论一次（count=20、cursor=0，不翻页、不读回复）。匹配需求词的评论条数达阈值即命中。同一评论多个词只算一条；只输出词和计数，不输出评论正文或评论者身份。

`prefilter_v3` 在作者资料成功后，还会按 `signature_extra` 数组顺序选取 `type===0`、非空字符串 `sec_uid`、非本人的账号，去重后最多两个；原作者已命中仍读取。每个目标先请求资料，返回 `sec_uid` 须一致；简介用同一词表（排除 `comment_demand`）命中记 `mention_signature`，非空对象群信息记 `mention_fans_group: non_empty`，均并入 hits。目标资料成功后，再用字符串 URL `fetch(url.toString(), ...)` 请求 `/aweme/v1/web/aweme/post/` 首屏（aid=6383、sec_user_id、max_cursor=0、count=20）。页面内只取 `is_top` 严格为 1 或 true 的最多 3 条作品，将 `desc` 截断到 300 个 Unicode 字符后用相同的非需求词表匹配，命中记 `mention_pinned`，按 field/token 去重并入目标与条目 hits；仅返回描述与命中词，不返回完整作品列表。资料或作品失败不改变已有判定；无命中时继续原评论规则。不读原作者作品、不翻页、不读目标作品评论、不递归引用。

单条最多 6 次 GET（作者资料 + 两个目标资料 + 两次目标作品首屏 + 原视频评论），每次 fetch 和 JSON 解码共用 5 秒 deadline，无重试。目标资料与作品请求均在上一笔请求结束后至少等 1 秒（最多四次间隔），页面内请求与间隔预算约 34 秒（不含通信、停留、滚动和事件循环延迟）。原作者资料／必要评论异常为 `unknown`。`matched/unknown` 用来源正常停留，`missed` 停留 1–2 秒；开启时普通视频末条也完成停留。广告、直播为 `skipped`，沿用原处理（广告可成卡，直播仍在后端排除）。不跳转主页或详情，不执行互动写操作。

适配器仍输出全部观察项，`observed_count/visible_count` 含未命中、广告、直播和重复项；完整性只反映是否读满或中断。snapshot 保留所有非直播条目的 ID，包含未命中项，原有去重不变。后端在持久化之前分流：`missed` 只写运行 `raw_payload.prefilter_misses`，其余通过去重的条目入库成卡。`fetched_count` 是实际提交的 RawItem 数；`dedup_skipped_count` 沿用原含义（含直播排除与 snapshot 重复），不会把新发生的初筛未命中当去重。

运行与 FETCH artifacts 都保存 `prefilter_summary`：`checked=matched+missed+unknown`，`skipped` 单列；`token_hits` 是大小写折叠后的词命中的观察项数，同一词在一项内多次出现只算一次，`v/V` 折叠为 `v` 后去重。原作者非空群用 `field=r_fans_group_info, token=non_empty` 标识。`mention_targets`、`mention_profiles_ok` 分别累计目标数和资料成功数，`mention_hit_items` 统计含目标简介／群命中的条目数（含原作者已经命中的条目）。`mention_pinned_checked`、`mention_pinned_ok` 分别累计发出作品请求的目标数与成功数（含首屏无置顶）；`mention_pinned_hit_items` 统计有置顶命中的观察条目数，同一条多个目标／置顶命中只计一次。原 `mention_hit_items` 仍只计简介／群命中。misses 只增加 `mention_sec_uids`。摘要与轻量未命中记录覆盖所有本轮观察项，所以可与去重计数重叠。

卡片以共享 `author_profile`（作者简介）与 `screening_evidence`（初筛证据）两个 context block 呈现；前者追加“被 @ 账号简介”，每个目标下列置顶描述，并区分首屏无置顶、未取得和未请求，后者列出 mention 命中；两个 block 的 `raw_payload` 均带 `mentions`（sec_uid、profile_ok、signature、hits、has_fans_group、pinned_checked、pinned_ok、pinned[{desc,hits}]；pinned_ok 未请求为 null、失败为 false、成功为 true）。平台结构放在 block/card `raw_payload`，可读证据含状态、规则版本、命中字段和词、请求状态、计数及仅记录的信号。MCP `get_cards` 沿用已有 4,000 字符预算，截断时用 `get_card`。没有 schema 迁移，也没有开启任何现有来源。


### prefilter_v5 强弱信号

当前规则由 domain 同时投影 `tokens` 与 `token_strengths`，仅强信号可使 matched（评论仍需原数量阈值）。`@`、`：`、`v/V`、`🌍`、`🌏`、`👇`、`⬇️`、`戳` 仅为弱证据；原作者、被 @ 简介和置顶均遵循此规则。弱命中继续原评论路径，hits 在各阶段保留并标注 strength，未命中的轻量记录也保留 hits。原非空粉丝群继续作为结构化强信号；其它资料字段仍仅记录。旧证据不回填、不重判。

完整词表、数据依据与边界见[实现说明](../../../../docs/engineering/douyin-monetization-prefilter.md#prefilter_v5强弱信号分层)。
