# GitHub capture 固定样本

2026-10-27 从公开 GitHub 页面/API 响应裁剪；测试运行不访问网络。

- `repository.json`、`readme.html`：https://api.github.com/repos/modelcontextprotocol/servers 与 `/readme`（Accept: application/vnd.github.html+json）。README 保留首节真实 article/markdown-heading 结构。
- `issue.json`：https://api.github.com/repos/modelcontextprotocol/servers/issues/100 （Accept: application/vnd.github.full+json）。GitHub Issue API 同时覆盖 Issue/PR，保留 task-list/input 的真实结构。
- `pull_request.json`：同 API 的 `/issues/2`，保留正文/代码/图片/引用结构。
- `release.json`：同 API 的 `/releases/latest`，仅保留发布说明首几行。
- `discussion.html`：https://github.com/orgs/community/discussions/1 的标题与主帖容器；保留 class 和 table/task-lists 嵌套，附最小导航/登录/页脚/回复噪声以验证边界。

仓库、用户、标题和附件 URL 已替换为 example；删除页面会话属性、表单与临时签名 URL。未保存凭据。
