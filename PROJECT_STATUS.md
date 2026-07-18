# Project Status: Failed Experiment / 项目状态：实验失败

- Decision date: 2026-07-18
- Lifecycle: archived; feature development has stopped
- Recommendation: do not adopt Agent Rails for new projects

## 结论

Agent Rails 的核心产品假设没有通过真实开发评测，因此本项目作为失败实验结案。

最初假设是：通过 Task Pack、结构化合同、代码检索、memory、请求钩子和更大的
上下文预算，可以稳定提高 coding agent 的决策质量。VP-005 首先显示了负向趋势，
VP-006 的同基线 A/B 对照进一步表明，这条路径没有带来质量优势，反而显著增加了
步骤、工具调用、token 和耗时。

VP-006 使用相同的仓库基线、OpenCode 版本、`deepseek-v4-flash`、任务、rubric 和
验收命令：

| Metric | OFF | Agent Rails Deep |
|---|---:|---:|
| Final build | passed | passed |
| Unit tests | 78 | 83 |
| Visible steps | 29 | 91 |
| Tool calls | 57 | 117 |
| Reported session tokens, including cache | 1,957,935 | 8,796,294 |
| Duration | 392,775 ms | 882,493 ms |

外部镜像盲评给出 `off` 的 weak-consensus，但裁判理由包含事实错误，因此具体分数
不能作为最终质量证明。即便不依赖裁判分数，Agent Rails 也没有达到最基本的
non-inferiority 要求：它没有证明质量提升，却使用了约 4.49 倍的累计 token 和
2.25 倍的时间。

## 失败原因

- 完整 task/rubric 与 Task Pack 重复注入，增加上下文而没有增加新的决策证据。
- 静态 AC/RUB 清单改善了报告形式，却无法判断 Android API、生命周期和手势方案
  是否真正满足需求。
- Deep 模式鼓励继续读取和修补，未能在关键未知量上及时改变策略。
- 构建和单元测试护栏只能证明可执行性，不能替代语义判断和设备验收。
- 工程基础设施先于核心决策假设被大量建设，验证顺序倒置。

## 保留资产

以下工程成果仍可作为历史实现或未来项目的参考，但不构成 Agent Rails 成功：

- Target Project、worktree 和 Profile 隔离；
- Claude Code、Codex 和 OpenCode Adapter 的安装与诊断；
- deterministic check/verify、敏感输出保护和私有产物发布；
- A/B candidate capture、OpenCode/Codex trajectory、token metrics 和 mirrored blind judge。

这些能力解决的是可靠执行和可观测性，不会自行产生正确决策。

## 项目决定

- 停止 Task Pack、Deep context、retrieval 和 memory 方向的功能开发。
- 不再发布以“让 coding agent 更聪明”为承诺的新版本。
- 保留仓库、历史 release、测试和评测文档，作为可复核的失败记录。
- 只在出现安全、数据损坏或历史可复现性问题时考虑维护修复。
- 后续的附属决策 Agent 在独立仓库中从最小闭环重新开始，不作为本项目的重命名或续作。

## English Summary

Agent Rails is archived as a failed experiment. Its engineering guardrails remain useful
historical assets, but the central hypothesis that larger structured context would improve
coding-agent decisions did not survive paired evaluation. Feature development has stopped,
and the project is not recommended for new adoption.
