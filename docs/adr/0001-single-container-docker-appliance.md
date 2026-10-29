# ADR 0001：以单容器 Docker Appliance 作为正式交付目标

日期：2026-07-22
状态：Accepted

## 决策

InfoOS 的正式交付目标改为运行在用户自有 Linux 主机上的单容器 Docker Appliance。React 面板、FastAPI、scheduler、durable worker、SQLite 访问、媒体处理、正式版 Google Chrome、容器内可见桌面、OpenCLI 与 Browser Bridge 由同一个镜像交付，并由容器内 supervisor 管理；这并不要求所有职责合并为一个进程。容器只发布一个受认证的 HTTP(S) 产品入口，该入口同时提供最终面板和 `/api/*`，首次登录与人工修复通过面板内的受认证浏览器维护会话完成。Chrome、OpenCLI、CDP、VNC/noVNC、调试和内部健康端口不得直接发布到宿主机或局域网。SQLite、附件、配置、Chrome Profile、OpenCLI binding 和登录态保存在 Docker 管理的持久卷中，使容器停止、重启和用同版本镜像重建后仍可恢复；容器内 Chrome 必须是正式稳定版、由同一非 root UID 持有专用 Profile、保持正常 sandbox，禁止 `--no-sandbox`、个人 Profile 和未受控的外部浏览器依赖。

## 背景

此前依赖宿主桌面与原生 Chrome 的部署方式使运行依赖分散。单容器方案把浏览器、Bridge、后台队列与数据访问纳入同一版本化交付；登录持久化和真实平台功能仍需在各目标环境独立验收，不能从架构选择推断生产可用。

## 后果

- 一级产品定位、领域模型、SQLite 真相源和正式任务链不变；变化集中在部署模块和 Browser Runtime seam。
- 交付面从宿主机 systemd、宿主桌面和分散依赖收敛为一个版本化镜像、一个容器、一个对外入口和两个持久数据域。
- Browser Runtime 仍是内部 adapter；业务 worker 不以 raw CDP、VNC 或 OpenCLI daemon 作为公共合同。
- `deploy/linux/` 暂时保留为当前原生 systemd 环境的迁移 runbook，直到 Docker Appliance 通过完整验收后归档。
- 正式切换前必须补齐统一镜像、supervisor、面板内登录代理、备份恢复、升级回滚和真实来源到信息卡的端到端验收。
