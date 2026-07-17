# Agent Rails Coding Agent 演进方向

## 北极星目标

Agent Rails 不试图提升模型的内生智力。它面向开发者，在相同模型、相同 token
预算和相同任务环境下，通过检索、拆解、约束和反馈闭环，提高 coding agent 的
任务成功率。

主要结果不是“生成了更多上下文”或“拦截了更多命令”，而是：

- 任务成功率；
- 首次修改通过率；
- 完成任务所需的修复轮数；
- 无效文件读取和重复工具调用；
- scope、worktree、验证和发布等严重错误；
- provider 实际报告的总 token。

## 当前状态

Agent Rails 当前最成熟的是约束与交付可靠性：

- Target Project、Profile 和 worktree 隔离；
- Git Scope、Sensitive Output 和 Managed Artifact 所有权；
- Verification Plan、Related Test Selection 和 Publish Check；
- Task Pack token 预算、Release 安装和回滚。

这些能力是后续 coding 闭环的基础，但它们主要防止流程错误。Task Pack Change
Evidence 原先只围绕 changed paths 和 diff 工作；clean worktree 中没有 changed
paths 时，模型仍需自己搜索整个仓库。强模型通常能够补足这一步，弱模型更容易找错
文件、遗漏调用关系或消耗大量 token。

## GitHub 调研

### Aider

[Aider Repository Map](https://aider.chat/docs/repomap.html) 使用 tree-sitter 提取
符号，把文件依赖构造成图，再按相关性和 token 预算生成紧凑 repo map。它还会在
没有明确文件时扩大 map 预算。

可借鉴：

- 代码结构比整文件正文更适合作为初始导航证据；
- 相关性排序必须服从当前 token 预算；
- clean worktree 也需要仓库级方向感。

不能直接照搬：

- [Aider FAQ](https://aider.chat/docs/faq.html) 明确指出 repo map 可能让弱模型混淆，
  因此更多上下文不一定更好；
- Agent Rails 应输出更少、更准并带相关原因的证据，而不是默认展开完整 repo map。

### Agentless

[Agentless](https://github.com/OpenAutoCoder/Agentless) 把软件修复固定为分层定位、
候选修复和 patch 验证。定位从文件逐步收缩到类、函数和具体编辑位置；验证结果再
用于选择候选。

可借鉴：

- 先定位再修改，避免模型同时承担全仓检索和编辑；
- 将最终测试结果反馈到候选选择；
- 窄阶段可以获得很高的 Leverage，不需要先建设完整 agent runtime。

### AutoCodeRover

[AutoCodeRover](https://github.com/AutoCodeRoverSG/auto-code-rover) 使用程序结构感知
的代码搜索，在有测试时结合故障定位寻找潜在修改点。

可借鉴：

- 优先检索类、函数和引用，而不是只做纯文本片段搜索；
- 测试失败不仅用于判定成功，还应帮助缩小代码位置；
- 未来可通过外部 Adapter 接入 AST、Language Server 或测试覆盖信息。

### SWE-agent 与 mini-SWE-agent

[SWE-agent ACI](https://swe-agent.com/latest/background/aci) 的经验包括：编辑时立即
lint、每轮只显示有限代码、搜索结果保持简洁、空输出也明确反馈。其重点是把 agent
看到和操作的 Interface 调整成模型更容易使用的形状。

[mini-SWE-agent](https://github.com/SWE-agent/mini-swe-agent) 则证明执行循环可以
保持极简：模型调用、独立动作、观察结果、步骤和成本限制、线性轨迹。

可借鉴：

- 工具输出形状是产品能力，不只是显示细节；
- Agent Rails 不应先扩张为复杂调度、多 agent 或 MCTS runtime；
- 简单、可观测、可复放的闭环优先于复杂框架。

### Serena

[Serena](https://github.com/oraios/serena) 通过 Language Server 或 IDE 提供符号、
引用、实现、诊断和精确编辑。

可借鉴：

- Agent Rails 不需要为每种语言自己维护完整解析器；
- 内置可靠降级路径与外部语义 Adapter 可以形成真实 Seam；
- 外部 Adapter 只返回受限、可显示的代码证据，不把凭证或供应商协议带入 kit。

### ContextBench 与 CodeScaleBench

[ContextBench](https://github.com/EuniAI/ContextBench) 发现复杂 agent scaffolding
对检索只有有限增益，模型探索过的代码和实际利用的代码之间也存在明显差距。

[CodeScaleBench](https://github.com/sourcegraph/CodeScaleBench) 使用相同 agent 和
模型做检索能力的配对比较，公开快照显示外部检索带来改善，但提升幅度并不夸张。

因此 Agent Rails 必须同时评估：

- file/span recall；
- precision；
- explored context 与 utilized context；
- 最终任务成功率和 token；
- 不能用“检索了多少文件”代替有效性。

## 架构方向

### 1. 任务级代码检索

深化 Task Pack Change Evidence Module。changed paths 仍然是修改后的首要证据；
clean snapshot 则根据 Goal 从固定 target commit 中寻找相关文件、符号和测试。

P0 约束：

- 不新增公开命令；
- 不引入向量数据库或在线 embedding；
- 只搜索 Git 跟踪的 target snapshot；
- 固定字符串搜索，避免把 Goal 当作正则表达式；
- 最多保留 Pack Mode 允许的少量结果；
- 单个 Git blob 有大小上限；
- 只输出路径、行号、符号和相关原因，不输出未请求的代码正文；
- 无 diff 时启用，有 changed paths 时继续以 changed evidence 为主；
- 中文 Goal 保留可搜索的双字词，同时过滤常见动作词。

后续可添加 tree-sitter 或 Language Server Adapter，但至少出现两个真实 Adapter 后
再固定新的外部 Seam。

### 2. Repair Pack

Verification Plan 执行失败后，把输出转成下一轮聚焦证据：

- 失败命令与退出状态；
- 首个高价值错误；
- 相关文件、符号和测试；
- 已排除的原因；
- 下一条可证伪假设。

Repair Pack 不应无限复制日志，也不应自行宣称根因。

第一刀采用终端内 tracer bullet：`agent-rails verify` 的验证步骤非零退出时，在保留
原始流式输出和退出码的同时，追加一个有界 Repair Pack。它包含失败步骤、退出码、
已完成步骤、首个高价值诊断，以及能从冻结 changed paths 中确认的项目位置。

这一刀刻意不做：

- 不新增公开命令或修改 Task Pack 固定结构；
- 不把完整日志或 opaque Profile command 再复制到 Repair Pack；
- 不静态编造“已排除原因”或根因假设；
- 不写磁盘 artifact，先用真实任务验证终端反馈是否提高下一轮成功率。

后续只有在配对评测出现正向信号后，才继续做错误到代码的二次检索、私有 Repair Pack
artifact 和有界重试协议。

### 3. 任务拆解

在代码证据稳定后，再生成：

- 行为不变量；
- 修改步骤；
- 验收条件；
- 禁止修改范围；
- 未决假设。

拆解仍可能需要模型参与。工程侧负责提供结构、证据和验证状态，不把静态模板包装成
虚假的自动规划能力。

### 4. 模型适配

Model Preset 不应只描述 context 和 Pack token：

- 弱模型使用更少、更精确的代码证据和更细步骤；
- 强模型可以使用更广的代码地图和更少流程提示；
- reasoning 与 editing 能力差异明显时，再评测 Architect/Editor 分离；
- 所有策略必须通过相同模型、相同 token 的配对实验验证。

### 5. Memory 学习层

Memory 不是当前代码事实的替代品。代码和测试必须实时读取，Memory 只提供经过验证
的历史先验。

成功修复后可以生成 Memory Candidate：

- 问题或错误指纹；
- 已验证根因；
- 相关文件和符号；
- 有效修复与验证命令；
- 适用范围和失效条件。

Candidate 需要去重和校验后才成为卡片。卡片数量增长不是目标，后续任务的有效命中
和减少重复踩坑才是目标。

## 分阶段路线

### P0：Task Code Evidence

- clean snapshot 根据 Goal 输出相关文件、符号和测试；
- 接入 Task Pack 固定结构和 token assembler；
- 使用标准库和 Git，保持无额外依赖；
- 通过 fixture 和真实 Target Project smoke 验证。

### P1：Repair Pack

- Verification 失败证据结构化（终端 tracer bullet 已落地）；
- 错误到相关代码证据的再检索；
- 有界重试和失败升级协议。

### P2：任务拆解与验收

- 行为不变量、修改计划和验收条件；
- 推理与编辑分离实验；
- 评估首次修改通过率和修复轮数。

### P3：模型感知路由与 Memory Candidate

- 根据模型和任务选择证据密度；
- 经过验证的修复生成 Memory Candidate；
- 固定路由策略后与 `off`、单一 Pack Mode 和事后 oracle 比较。

## 非目标

- 把 Agent Rails 变成另一个 Claude Code、Codex、OpenHands 或 IDE；
- 用更多 prompt 规则冒充编程能力；
- 默认安装向量数据库或复制模型权重；
- 在没有因果评测前引入多 agent、MCTS 或多候选 patch；
- 让 Memory 覆盖当前代码、测试和 Git 事实；
- 只优化 Pack 大小而忽略完整 session token 和最终任务结果。

## 评测门槛

每个切片至少比较 current 与 candidate；成熟评测保留真实 `off`：

- 相同仓库 SHA；
- 相同任务、模型、harness 和工具权限；
- 相同总输入 token 上限；
- 独立 worktree 和新会话；
- patch 与确定性验收优先；
- LLM judge 只补充主观质量；
- 记录 file/span 检索、工具调用、失败轮数、成功率和真实 token。

只有任务成功率、安全性或质量/token Pareto 出现稳定信号，才继续增加更复杂的
retrieval、routing 或 orchestration。
