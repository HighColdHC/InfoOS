# InfoOS 小红书 OpenCLI 扩展

这些命令运行在 InfoOS 专用 Chrome 会话中。笔记命令使用搜索或 Feed 返回的含 `xsec_token` 完整 URL；群读取命令只访问当前账号已加入的群。

| 命令 | 用途 |
|---|---|
| `xiaohongshu profile-info <user-id-or-url>` | 作者公开简介 |
| `xiaohongshu comment-targets <note-url>` | 可见评论 ID，供精确回复 |
| `xiaohongshu group-list` | 已加入群列表 |
| `xiaohongshu group-messages <group-id> --limit 20` | 最新群消息 |
| `xiaohongshu group-messages <group-id> --last-id <store-id>` | 含该 ID 的历史页 |
| `xiaohongshu like <note-url>` | 点赞并核验状态 |
| `xiaohongshu comment <note-url> <text>` | 发表评论并读回 ID |
| `xiaohongshu reply <note-url> <comment-id> <text>` | 回复评论并读回 ID |
| `xiaohongshu group-join <invite-or-profile-url>` | 对唯一可见的群入口发起入群，读回成员状态 |

`group-join` 需要一个在网页版确实显示入群按钮的地址；二维码、App-only 页面和多入口页面会明确失败。群消息命令返回服务器提供的消息 ID、store ID 和时间，不将群内容写入 InfoOS 数据库。评论与回复命令的 `accepted` 仅表示平台提交接口返回成功和评论 ID；是否在重新加载的评论区可见要另行读回。写命令只执行一次，不自动重试。

- `search <query> --sort <排序> --publish-time any|day|week|half-year`：关键词搜索可按排序与发布时间筛选，默认保留上游综合搜索。
