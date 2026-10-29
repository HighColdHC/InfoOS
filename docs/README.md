# InfoOS 文档索引

文档必须区分产品合同、工程合同、运行证据和历史背景。当前代码、SQLite schema 与运行时证据可以揭示文档漂移，但不能静默改写产品边界。

## 真相优先级

1. 产品定位与对象边界：[产品目标](product/infoos-intelligence-workbench-target.md)
2. 当前工程结构与依赖：[工程结构标准](engineering/project-structure-target.md)
3. Docker 交付与验收边界：[Docker Appliance 合同](engineering/docker-appliance-target.md)
4. 智能体协作、代码导航与命令：仓库根目录 `AGENTS.md`
5. 当前环境操作：[环境手册](engineering/local-env-playbook.md)
6. SQLite 变更：[SQLite CRUD 手册](engineering/sqlite-crud-playbook.md)

发生冲突时，先核对当前实现和对应环境的实时证据，再在同一变更中修正文档；必须明确写出“当前现实”与“目标/未关闭事项”。

## 专题文档

- [给 AI agent 的安装与使用说明](INSTALL-for-agents.md)
- [浏览器捕获与按需媒体合同](product/browser-capture-contract.md)
- [Save to InfoOS 浏览器扩展需求](product/save-to-infoos-extension.md)
- [多平台调研功能合同](product/multi-platform-research-contract.md)
- [外部 agent 运行手册](engineering/opportunity-agent-runbook.md)
- [抖音变现初筛](engineering/douyin-monetization-prefilter.md)、[小红书变现初筛](engineering/xiaohongshu-monetization-prefilter.md)
- [遗留 SQL 读取白名单](engineering/legacy-sql-read-allowlist.md)
- [抖音博主有界增量适配器](engineering/douyin-full-creator-adapter.md)
- [单容器部署 ADR](adr/0001-single-container-docker-appliance.md)
- [智能体技能使用方法](skills/mattpocock-skills-practice.md)
- [原生 systemd 遗留维护面](../deploy/linux/README.md)

日期化文件不天然等于历史文档。仍被当前合同引用、仍承担实现或验收真相的日期化文档可以留在现行目录；内容已被更高优先级文档替代、或工作完成且仅剩背景价值时，移出当前导航；个人运维与业务记录不进入公共文档。
