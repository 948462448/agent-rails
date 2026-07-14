# Agent Rails 评测策略

状态：设计提案

英文版：[evaluation-strategy.md](./evaluation-strategy.md)

## 决策摘要

Agent Rails 首先必须证明：与完全不使用 Agent Rails 的同一个 coding agent
相比，它确实能带来收益。只有完成这个因果对照，才有必要继续比较 Agent Rails
的不同级别。

因此，核心评测采用配对实验，设置一个真实的 `off` 对照组，以及以下明确的
Agent Rails 实验组：

1. `off`
2. `session-only`
3. `lite`
4. `normal`
5. `deep`
6. `audit`
7. `auto`

每个固定模式都必须在相同的仓库快照、任务、模型、agent harness、工具策略和
验收条件下运行。只有固定模式已经证明不同任务类型应该选择什么级别之后，才
评测 `auto`。

评测结果不能压缩成一个总分。产品决策应基于质量、安全性和 token 的 Pareto
对比：

- Agent Rails 是否提高了任务成功率，或者避免了实质性错误？
- 在质量相同的情况下，它是否减少了总 token？
- 在 token 预算相同的情况下，它是否提高了质量？
- 更高的 Pack Mode 是否提供了足够的边际收益，值得付出额外上下文成本？

## 为什么评测放在 Agent Rails 之外

评测是证明 Agent Rails 是否有效的证据，不是 Agent Rails 的运行时能力。产品 CLI
应聚焦上下文生成、注入、adapter 和确定性检查；独立评测器负责实验组、TUI 产物
录制、judge 接入和配对报告。

原先的内置运行日志命令已经移除，因为 baseline 标签并不能生成真实 baseline：它
仍会经过 Agent Rails 能力，把命令完成当成任务成功，并且只能测量 Task Pack 估算，
不能得到 provider 的完整 session 用量。继续把这条链路放在产品 CLI 中，会让接口
看起来比证据更完整。

因此，第一个评测里程碑仍然必须先建立真实基线。在 `off` 能够绕过所有 Agent Rails
能力之前，扩充任务集没有意义。

## TUI 黑盒执行

评测器不需要自动操作开发 TUI。同一个任务分别在两个隔离 worktree 和全新 TUI
会话中完成，然后录制 patch、最终回答、验证输出和可选 provider usage。独立 Python
工具只负责匿名化这些产物，并通过 stdin 调用任意受信任的大模型 judge 命令。

默认盲评执行两轮镜像对比：第二轮交换 Response A 和 Response B。只有两轮揭盲后
都映射到同一实验组，才认为结果不受位置影响。可运行流程见
[TUI 黑盒 A/B 盲评手册](./tui-ab-eval.zh-CN.md)。

## 评测问题

### Q1：Agent Rails 到底有没有效果？

在相同任务上，将每个 Agent Rails 实验组与 `off` 配对比较。

主要结果包括：

- 任务成功率；
- 严重的 scope、worktree、验证和发布错误；
- provider 实际报告的总 token。

这是 Agent Rails 是否值得存在的首要检验。如果所有实验组都不能改善安全性或
质量，也没有任何实验组能够在保持基线质量的同时减少 token，就不应该继续投入
更复杂的路由机制。

### Q2：哪个级别适合哪类任务？

固定级别之间也要相互比较。上下文密度更高不等于效果一定更好。尤其是 `deep`
和 `audit` 可能帮助复杂任务，但也可能因为额外上下文和 token 成本而伤害聚焦
任务。

评测要找出每类任务中“最便宜且效果不劣”的级别，而不是选出一个全局最高级别。

### Q3：Auto 路由能否接近最佳性价比？

固定模式结果齐备后，冻结一版路由策略，将 `auto` 与以下结果比较：

- `off`；
- 全局表现最好的单一固定模式；
- 事后 oracle：针对每个任务选择最便宜且成功的固定模式。

oracle 只是分析上限，不是可运行的实验组。`auto` 绝不能读取当前任务的运行结果
再决定路由。

## 实验组契约

| 实验组 | 允许使用的 Agent Rails 能力 | 目的 |
| --- | --- | --- |
| `off` | 完全禁用：无 SessionStart 注入、Local Adapter、Profile 派生上下文、Task Pack、Agent Rails skill、memory provider 和 Agent Check 指引 | 真实对照组 |
| `session-only` | 只使用稳定的 SessionStart/Local Adapter 路由与安全指引，禁止生成 Task Pack | 单独测量常驻轻量护栏的效果 |
| `lite` | 稳定启动指引 + 强制 Lite Task Pack | 测量最小任务上下文 |
| `normal` | 稳定启动指引 + 强制 Normal Task Pack | 测量当前默认密度 |
| `deep` | 稳定启动指引 + 强制 Deep Task Pack | 测量更密集的证据，以及完整规划和 grill 行为 |
| `audit` | 稳定启动指引 + 使用 Profile 最大值的强制 Audit Task Pack | 建立高上下文上限，而不是默认推荐 |
| `auto` | 稳定启动指引 + 不显式指定 Pack Mode 的 `agent-rails run` | 评测冻结后的路由策略 |

Profile 是配置，不是独立实验组。只有当 Profile 内容通过 adapter、Task Pack、
memory card 或其他注入产物进入模型时，它才会影响模型。所有 Agent Rails 实验组
必须冻结使用相同的 Profile 配置。

第一轮实验关闭可选 memory provider。memory-on 与 memory-off 应作为后续独立的
因子实验，否则 memory 质量会与 Task Pack 本身的效果混杂。

## 两条评测轨道

### Track A：Agent 任务效果与 Token 效率

Track A 实际运行 coding agent，并评测它产出的 patch、review 或诊断结果，覆盖：

- 聚焦修改；
- 缺陷修复；
- 代码审查和问题诊断；
- 跨模块修改和模糊产品任务。

在这条轨道中，不能只给 Agent Rails 实验组追加 Agent Check 或 publish check，
否则各组边界不再对等。模型在运行结束前可以使用的任何验证能力，都必须预先声明
为实验组契约的一部分。

### Track B：工作流护栏

Track B 将 Agent Rails 的确定性护栏与模型解题能力分开评测。测试夹具应主动植入
以下错误：

- 错误 worktree，或向 sibling repository 复用 Profile；
- 超出任务范围的改动文件；
- 无效或无法解析的 base ref；
- 把源码控制基线错误地当作部署基线；
- 支持识别的敏感信息新增；
- 缺失或不安全的验证步骤。

Track B 报告检出率、误报率、漏报严重度，以及 token/交互开销。这样可以避免
强确定性检查的价值被混入模型质量平均分后稀释。

## 实验单元

一个实验单元定义为：

```text
任务夹具 + 仓库 SHA + 模型 + agent harness 版本
+ 实验组 + 重复次数
```

每个任务都必须能够从干净 worktree 重放，并包含足够的 ground truth，使评分过程
不需要读取其他实验组的产物。

建议的任务 Schema：

```yaml
id: focused-bugfix-001
category: bugfix
difficulty: medium
repo: https://example.invalid/repository.git
setup_ref: 0123456789abcdef
task: "修复描述中的行为，不要修改无关文件。"

acceptance:
  commands:
    - "bash tests/run.sh focused-bugfix-001"
  required_exit: 0

scope:
  allowed_paths:
    - "src/**"
    - "tests/**"
  forbidden_paths:
    - "deployment/**"

expected:
  required_behaviors:
    - "原始故障已修复"
  forbidden_behaviors:
    - "修改无关 API"

diagnostics:
  gold_files: []
  gold_symbols: []
```

`diagnostics` 是可选项。Gold context 可以帮助解释某个实验组为什么有效，但任务
是否成功仍然是主要结果。

## 公平性与隔离规则

每组配对比较必须满足以下规则：

1. 从相同且不可变的仓库 SHA 开始，每次使用全新的 worktree 或容器。
2. 使用相同的模型版本、agent harness、实验处理之外的 system prompt、工具权限、
   网络策略、时间限制和最大 token 预算。
3. 从 `off` 环境中彻底移除 Agent Rails plugin、hook、环境变量、生成文件、skills
   和用户 memory。只让模型“忽略它们”不构成有效对照。
4. 不同实验组之间不得共享对话状态、工作树改动、运行摘要或 memory 写入。
5. 在每个任务和重复区块内随机化实验组执行顺序。
6. Smoke Eval 至少重复两次，扩展评测至少重复三次。同一个任务的多次运行不能被
   当作多个相互独立的任务。
7. 评分前先识别环境或初始化失败。需要排除或重跑时，必须对同一个实验单元的所有
   实验组统一处理，不能只重跑失败的一组。
8. 记录实际注入的 SessionStart 文本和 Task Pack 产物，以便后续审计实验处理。

只有只读且与实验组无关的依赖缓存可以共享。Agent 历史、仓库摘要和语义索引属于
实验处理的一部分，不能跨组泄漏。

## 指标

### 主要质量与安全指标

- `task_success`：所有强制验收检查通过，且没有发生严重的禁止行为。
- `acceptance_pass_rate`：通过的可执行验收命令占比。
- `scope_violation`：修改或报告了声明范围之外的产物。
- `critical_mistake`：错误仓库/worktree、破坏性操作、敏感信息暴露、错误发布结论，
  或其他任务级 blocker。
- `regression_count`：运行后失败的、此前正常通过的强制检查数量。

对于没有可执行 patch 的 review 或诊断任务，使用冻结的评分标准，明确必需发现、
禁止的错误结论、证据质量和操作范围。在可行时，人工裁决应对实验组信息盲测。

### Token 与成本指标

只要 provider 能够提供，就记录以下真实用量：

- startup/system 输入 token；
- Task Pack 输入 token；
- 其他 prompt 和工具结果输入 token；
- 缓存输入 token；
- reasoning token；
- 输出 token；
- 总计费 token 和成本。

Task Pack 估算值只作为诊断字段，不能当作总 token 结果。报告必须单独展示 Agent
Rails 自身开销，不能把它藏在整个运行总量中。

### 运行诊断指标

- 总耗时；
- 模型轮数和工具调用次数；
- 查看过的文件和符号；
- 执行过的验证命令；
- 存在 gold context 时的 context precision/recall；
- 最终 Pack Mode 和 auto 路由原因。

Context precision/recall 用于解释行为，不能替代任务成功率。

## 配对分析

对实验组 `t` 和真实对照组计算：

```text
quality_uplift(t) = success(t) - success(off)
token_delta(t)    = total_tokens(t) - total_tokens(off)
token_saving(t)   = 1 - total_tokens(t) / total_tokens(off)
```

报告不能只给出整体平均值，还必须展示：

- 每个任务相对 `off` 的胜 / 平 / 负；
- 成功率和严重错误率；
- token 差值的中位数及分布；
- 按任务类型和难度拆分的结果；
- 从 `session-only` 到各固定 Pack Mode 的边际变化；
- 质量/token Pareto 前沿。

扩展数据集应以“任务”为单位进行重采样并计算置信区间，不能把单个任务的多次运行
作为独立样本。不同模型和 harness 版本应分别报告，不能混成一个头部数字。

## 初始决策门槛

Smoke Eval 只能提供方向性信号，不能建立统计显著性。扩展评测完成后，一个 Agent
Rails 实验组只有同时满足以下条件，才值得继续推进：

1. 没有引入新的严重错误类型。
2. 在其目标任务群中，至少满足以下一项：
   - 保持基线质量，同时将总输入 token 中位数降低至少 15%；或
   - 将任务成功率提高至少 10 个百分点，同时总 token 中位数增幅不超过 20%。
3. 更高的 Pack Mode 至少在一个预先声明的任务群中表现出正向边际价值；否则应
   合并、重路由，或从默认路径移除。

这些只是初始产品门槛，不是永久 benchmark 结论。如果真实 provider 成本或任务
方差表明阈值不合理，应在扩展评测开始前调整并冻结。

当 `auto` 在各任务类型上的质量接近最佳固定模式，同时相较“始终使用最高成功
模式”显著减少 token 时，才认为它通过。

## 推进计划

### Phase 0：证明评测框架可用

- 两个可重放任务。
- 实验组：`off`、`lite`、`deep`。
- 每组运行一次，共六次。
- Gate：证明 `off` 不包含任何 Agent Rails 产物；底层 agent 确实执行；验收能够
  评分；provider token 用量能够落盘。

### Phase 1：方向性 Smoke Eval

- 八个任务，覆盖聚焦修改、bugfix、review/诊断和跨模块工作。
- 实验组：`off`、`lite`、`normal`、`deep`。
- 重复两次，共 64 次运行。
- Gate：确认 Agent Rails 是否存在任何质量、安全性或 token 信号，并观察 Lite
  和 Deep 是否在不同任务类型上表现不同。

### Phase 2：完整级别与路由评测

- 二十到三十个任务，每个至少重复三次。
- 增加 `session-only`、`audit` 和冻结后的 `auto`。
- 增加配对置信区间和任务类型级分析。
- Gate：选择默认路由策略，并找出不值得其 token 开销的级别。

### Phase 3：外部有效性

- 使用一小组经过人工复核的 SWE-bench Verified 任务做端到端修复 sanity check。
- 只使用一小组 ContextBench verified 子集做上下文检索诊断。
- 两者都不能作为 Agent Rails 唯一的产品门槛；它们都无法替代真实的 `off` 与
  Agent Rails 对照实验。

## 实现里程碑

### M1：真实基线与运行清单

- 在独立运行清单中定义明确的实验组契约。
- 录制已经完成的 TUI 黑盒运行，`off` 组不得经过任何 Agent Rails 能力。
- 记录仓库 SHA、dirty-state 策略、模型/harness 版本、实验组、Pack Mode、重复
  编号、注入产物和环境指纹。
- 一旦发现 baseline 污染，评测框架必须直接失败。

### M2：录制任务并评分

- 录制每次 TUI 运行的最终 patch、最终回答、验证输出和可选 usage。
- 每个实验单元都恢复一个干净的任务环境。
- Agent 退出后执行验收检查和 scope 检查。
- 区分初始化失败、agent 运行失败和任务评分失败。

### M3：采集真实用量

- 保留 Codex/OpenCode 原始导出，并通过版本化 Run IR 生成 OTel GenAI-compatible
  OTLP JSON 与 ATIF-v1.7；缺失字段保持未知，不从可见 turn 推测底层 LLM 调用。
- 解析 provider 或 harness 的 usage 记录；记录来源的 token accounting 约定和
  capture fidelity，避免把 cache/reasoning token 重复相加。
- 将 Task Pack 估算值保留为独立字段。
- 生成独立的确定性轨迹指标文件，存储 token 明细、成本、可信耗时、人工追问、
  工具调用、工具错误和重复调用；该文件不承担正确性评分。
- patch、最终回答和确定性验收继续作为独立 outcome artifacts；ATIF 只承担轨迹
  回放与过程评分，不能替代结果正确性判断。

### M4：配对报告

- 按任务、模型、harness 版本和重复区块配对实验组。
- 报告质量提升、token 差值、安全错误和 Pareto 前沿。
- 拒绝比较仓库 SHA 或模型/harness 版本不兼容的运行。

### M5：任务集与外部适配器

- 落地第一批八个通用、可重放任务。
- 只有本地因果评测通过 Phase 1 后，才添加外部 benchmark adapter。
- 只有固定级别已经产生证据后，才冻结并评测 auto 路由。

## 非目标

- 宣称一个 Pack Mode 在所有任务上都最好。
- 只优化 Task Pack 大小，忽略完整 session token。
- 把 TUI 或录制命令成功当作任务成功。
- 在可以执行验收的情况下，只使用 LLM judge 判断正确性。
- 在保留评测任务上训练或调参。
- 把 ContextBench、GitHub stars 或 leaderboard 排名当作 Agent Rails 有效的证明。

## 第一个实现切片

第一个实现切片应刻意保持很小：

1. 在隔离的 `off` 和强制 `lite` TUI 会话中运行同一个任务；
2. 使用独立 Python 工具录制两边的 patch、最终回答和验证输出；
3. 执行一条确定性验收命令；
4. 当 TUI 或 provider 能够提供时，存储真实 token 用量；
5. 执行双轮镜像盲评，揭盲输出一条包含质量和 token 差值的配对结果。

在这个切片真正端到端跑通之前，继续增加 benchmark 任务只会增加数据量，仍然
回答不了最核心的因果问题。
