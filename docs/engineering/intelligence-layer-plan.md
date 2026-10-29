# InfoOS 情报与知识卡处理链

当前产品范围以 [产品合同](../product/infoos-intelligence-workbench-target.md) 和 [领域语言](../../CONTEXT.md) 为准。本文只提供工程导航，不记录维护者实例计划或部署进度。

InfoOS 采集信息卡，外部 agent 经 MCP 依据版本化画像完成分诊、拆解与归并；InfoOS 保存知识卡与证据，生成 `knowledge_v1` 冻结日报。决策、行动、写作与认知属于用户的 PKM。InfoOS 不调用 LLM，GPU ASR 仅产转录。

- 画像与分诊：[画像存储](../../backend/infrastructure/storage/interest_profiles.py)、[分诊标准](../../backend/domain/intelligence/triage.py)、[rubric](../../backend/domain/intelligence/rubric.py)。每轮固定 profile_version，保留历史评估版本。
- 拆解与归并：[知识卡标准](../../backend/domain/intelligence/knowledge.py)、[MCP 工具](../../backend/api/mcp/knowledge_tools.py)。提交后核对 accepted/rejected，回读知识卡与证据；接收回执不代表归库或阅读验收。
- 日报：[布局](../../backend/domain/intelligence/daily_brief.py)、[读模型](../../backend/read_models/daily_brief.py)。新布局与旧快照读取分别核验，不回写旧日报。
- 旧商机工具与记录保留历史读取，不作为新知识卡主流程；核实与跟进写权限仅交互式 Token 使用。
- [agent 运行说明](opportunity-agent-runbook.md)；正式集成接口是提示词模板与 `/mcp`，外部 agent 账号、模型与费用由用户自备。

来源降频/停用及画像调整需用户确认。公开访问、最小 scope Token、双因素网关与数据恢复按 [Docker 手册](../../deploy/docker/README.md) 执行；实际运行与安全验收依各自环境证据，源码实现不等于通过。
