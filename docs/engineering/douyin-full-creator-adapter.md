# 抖音博主有界增量适配器

## 目标与环境边界

本适配器服务于 Docker Appliance 目标/验收环境。它只能通过容器内受
InfoOS 管理的 Chrome Stable、专用 Profile、OpenCLI daemon 与 Bridge
执行；当前 Linux 原生 systemd 环境必须继续 fail closed，不能退回宿主
Chrome。

InfoOS 仍是唯一运行时真相源：

- `sources` 保存博主来源；
- `source_cursors` 保存该来源最近一次已见视频；
- scheduler/worker 保存抓取与处理任务；
- `raw_items`、信息卡、ContentBlock 和 CardAsset 保存内容与素材事实。

容器内 OpenCLI 适配器只负责读取抖音博主作品分页，不保存自己的来源、
任务、Cookie、数据库或下载清单。

## 抓取合同

抖音 `creator` 来源投影为：

```text
opencli douyin user-videos <sec_uid>
  --limit <max_items_per_run>
  --max_pages 25
  --with_comments false
```

- 首次抓取：只读取并提交最新的 `max_items_per_run` 条，明确记录
  `bounded_initial`，不宣称已经回填账号全量历史。
- 日常增量：把 InfoOS 的 canonical cursor
  `https://www.douyin.com/video/<aweme_id>` 投影为
  `--limit 0 --stop_aweme_id <aweme_id>`；适配器必须先扫描到旧游标，且
  单轮最多翻 25 页（约 500 条新视频）。
- 如果扫描到的新作品超过 `max_items_per_run`，本轮只提交紧邻旧游标的
  最老一段，并把游标推进到该段最新作品；后续轮次继续同一缺口。不能把
  游标直接推进到账号最新作品而跳过中间视频。
- 增量抓取找不到已保存 cursor 时失败关闭，不会把账号历史静默当作增量
  再次入库；只有显式清除该账号 cursor 才会重新初始化。
- 同一轮按 `aweme_id` 去重；只有带可用 `video.play_addr` 的视频进入输出。
- cursor 不前进、超过 25 页或分页请求失败时，本轮失败关闭，不推进
  InfoOS 游标，也不宣称全量完成。
- OpenCLI 命令最长运行 900 秒。25 页是单轮增量扫描的安全上限；
  `max_items_per_run` 是单轮进入构卡、媒体下载和 GPU 转录链的工作量上限。

## 内容与素材

每条作品至少投影：

- canonical 视频 URL 与 `aweme_id`；
- 完整简介 `desc`；
- 作者昵称、账号标识和作者页；
- 发布时间；
- 时长、点赞、评论、分享；
- 无水印候选播放地址与封面地址。

抓取结果进入 InfoOS 的正式处理链。视频与封面下载仍由
`MEDIA_DOWNLOAD` 和 CardAsset 承担；有音轨的视频继续进入
`VIDEO_TRANSCRIBE`。适配器本身不直接写素材目录，避免生成第二套资产
真相。

## 历史回填边界

普通首次抓取和定时抓取不承担全量历史回填。若以后提供历史回填，必须是
单独的、可暂停且有独立 continuation 的维护操作，不能复用 source cursor
把几千条作品一次性扇出到构卡、媒体下载和 GPU 转录链。当前没有这条产品
入口，因此不得通过清空 cursor 暗示“全量历史已抓取”。

验收必须记录容器 image ID/commit、volume、每个来源的分页抓取结果、
SQLite 增量、信息卡、CardAsset、下载失败与反爬/登录缺口；不能只用命令
退出码证明全量完成。
