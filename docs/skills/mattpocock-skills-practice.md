# InfoOS 智能体技能使用方法

方法来源：[mattpocock/skills](https://github.com/mattpocock/skills)。本文只说明可复用的选择原则、方法路由和记录模板；具体 Skill 名称、版本、安装与初始化步骤以所用工具实际提供的说明为准。

当前产品和工程约束见根目录 [AGENTS.md](../../AGENTS.md) 与[文档索引](../README.md)。技能文档不替代当前代码、SQLite 运行证据或用户授权。

## 采用原则

InfoOS 的采用原则：

1. **按问题选 Skill**：只引入当前任务真正需要的能力，不默认套用整套流程。
2. **明确适用范围**：用户当前要求、项目合同和环境隔离优先于技能流程建议；验证范围遵循 `AGENTS.md`。
3. **真实闭环优先**：规格、TDD、诊断和 review 最终都要回到真实代码、真实浏览器、SQLite 或 durable task 证据。
4. **保留用户控制**：issue tracker、triage label、domain docs 位置和不可逆动作由用户决定。
5. **记录版本**：官方仓库会变化；每次重要实践记录仓库 commit、安装日期或 Skill 文件版本。
6. **沉淀可复用结论**：不把流水账堆进本文；完成一次实践后提炼有效模式、失败模式和适用边界。


## 按问题选择方法

下面是 InfoOS 的初步映射，不是固定流水线：

| 场景 | 优先考察的 Skill | InfoOS 交付证据 |
|---|---|---|
| 需求仍模糊，需要逼近真实意图 | `grill-with-docs` | 统一术语、明确边界、必要时更新 CONTEXT/ADR |
| 已充分讨论，需要固化规格 | `to-spec` | 可执行规格及验收标准 |
| 大任务需要拆成可独立推进的切片 | `to-tickets` | tracer-bullet ticket、依赖边 |
| 按规格实现 | `implement` | 小步代码、测试、review、提交证据 |
| 新功能或缺陷适合测试先行 | `tdd` | red → green → refactor 证据 |
| 难复现缺陷或性能回归 | `diagnosing-bugs` | 复现、最小化、假设、观测、回归测试 |
| 设计模块边界 | `codebase-design` / `domain-modeling` | 深模块接口、统一语言、ADR |
| 评审实现 | `code-review` | standards 与 spec 两个维度的结论 |
| 定期治理架构债务 | `improve-codebase-architecture` | 基于当前代码的候选项与用户选择 |
| 跨会话移交 | `handoff` | 可验证的当前状态、剩余工作和风险 |
| 不知道该用哪个 Skill | `ask-matt` | 明确路由理由，不直接制造额外流程 |

InfoOS 不要求每个任务走完全部 Skill。小修复可以只用 `diagnosing-bugs` 或 `tdd`；跨前后端的大功能才可能需要从澄清、规格、ticket、实现到 review 的组合。


## 实践记录模板

```markdown
### YYYY-MM-DD：标题

- InfoOS 任务：
- 使用的 Skill：
- Skill 来源版本/commit：
- 为什么选择它：
- 输入上下文与约束：
- 实际执行路径：
- 产生的文档/代码/测试证据：
- 结果：成功 / 部分成功 / 失败：
- 有效模式：
- 失败模式：
- 对 InfoOS 的适配：
- 下次是否继续使用：
- 可形成文章的核心观点：
```

完成标准不是“调用过 Skill”，而是能回答：它是否让需求更准确、改动更小、反馈更快、架构更清晰，或减少了返工。
