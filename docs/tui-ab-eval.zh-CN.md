# TUI 黑盒 A/B 盲评手册

状态：最小可运行方案

工具：[tools/ab_eval.py](../tools/ab_eval.py)

## 结论

评测器不需要驱动 Codex、Claude Code、OpenCode 或其他开发 TUI。它把 TUI 当成
黑盒：人仍然在原来的界面里开发，评测器只在会话结束后录制产物。

这样可以避免把 TUI 自动化、登录态、模型 SDK 和供应商鉴权塞进 Agent Rails。
`tools/ab_eval.py` 是独立实验工具，不属于 `agent-rails` CLI，也不依赖 Agent
Rails。Agent Rails 只是 A/B 实验中一个可选的 treatment。

## 评测单元

同一个任务准备两个隔离运行：

- `off`：TUI 中没有 Agent Rails SessionStart、plugin、Local Adapter、Profile、
  Task Pack、skill 或 memory 注入；
- `agent-rails`：按待测级别启用 Agent Rails，例如固定为 `lite`。

两个运行必须使用相同的：

- 仓库 SHA；
- 模型和 TUI 版本；
- 初始任务文本；
- 工具权限、网络策略和预算；
- 验收命令。

每次使用新的 worktree 和新的 TUI 会话。不得复用上一次对话、patch、summary 或
memory 写入。

## 为什么适合 TUI

一次 TUI 运行最终总会留下至少三类可比较证据：

1. worktree 中的 patch；
2. TUI 最终回答；
3. 测试或验收输出。

如果 TUI 能导出 provider usage，再附一份 usage JSON。如果不能导出，就记为
`unknown`；不能使用 Task Pack 估算值冒充完整 session token。

评测器不强制要求 transcript。第一阶段仍可只比较最终结果；当 TUI 能导出结构化
事件时，可以额外生成 OTel/ATIF 轨迹，用于分析重复读取、失败工具、人工纠正、
token 和耗时。轨迹是诊断证据，不能替代 patch 与确定性验收。

## 1. 准备两个 worktree

先冻结基线 SHA：

```bash
repo=/path/to/project
base_sha="$(git -C "$repo" rev-parse HEAD)"

git -C "$repo" worktree add --detach /tmp/eval-off "$base_sha"
git -C "$repo" worktree add --detach /tmp/eval-rails "$base_sha"
```

分别在两个目录里启动同一个 TUI。`off` 运行必须使用该 TUI 支持的隔离配置方式，
确保用户级 hook/plugin 和项目级 Local Adapter 都没有被加载。如果无法证明这些
注入已经关闭，该运行不能标记为真实 `off`。

不要为了隔离而把 AccessKey、cookie、token 或整个用户配置复制进评测目录。
鉴权仍由 TUI 自己管理。

## 2. 在 TUI 中完成任务

两组使用完全相同的任务文本。为了降低顺序影响，应随机决定先跑哪一组。操作者
知道当前 treatment 通常无法避免；这里保证的是 judge 盲评，而不是操作者双盲。

会话结束后，把 TUI 的最终回答保存为普通文本，例如：

```text
/tmp/eval-off-final.md
/tmp/eval-rails-final.md
```

把相同验收命令的输出分别保存为：

```text
/tmp/eval-off-tests.txt
/tmp/eval-rails-tests.txt
```

如果 TUI 能导出真实用量，保存原始 JSON。工具会识别常见的 `total_tokens`、
`totalTokenCount` 或嵌套 `usage.total_tokens`，但不会推测缺失值。

## 3. 录制两份候选产物

```bash
python3 tools/ab_eval.py capture \
  --label off \
  --treatment off \
  --model your-generation-model \
  --tui your-tui \
  --tui-version your-tui-version \
  --worktree /tmp/eval-off \
  --base "$base_sha" \
  --final-response /tmp/eval-off-final.md \
  --verification /tmp/eval-off-tests.txt \
  --usage /tmp/eval-off-usage.json \
  --include-untracked \
  --output /tmp/ab-case/off.json

python3 tools/ab_eval.py capture \
  --label rails-lite \
  --treatment agent-rails-lite \
  --model your-generation-model \
  --tui your-tui \
  --tui-version your-tui-version \
  --worktree /tmp/eval-rails \
  --base "$base_sha" \
  --final-response /tmp/eval-rails-final.md \
  --verification /tmp/eval-rails-tests.txt \
  --usage /tmp/eval-rails-usage.json \
  --include-untracked \
  --output /tmp/ab-case/rails-lite.json
```

`--usage` 是可选项。新建文件默认不会被静默读取；如果不传
`--include-untracked`，candidate 会记录遗漏项，judge 默认拒绝比较不完整产物。
Judge 还会拒绝 base SHA、generation model、TUI 名称或 TUI 版本不一致的候选，
避免把环境漂移误判成 Agent Rails 效果。

candidate JSON 和 judge artifacts 都以 `0600` 写入。它们可能包含源码和测试输出，
不得加入业务仓库，也不得包含密钥、cookie 或 token。

## 4. 可选：生成 OTel/ATIF 轨迹包

`trajectory` 子命令不驱动 TUI，也不接入 Collector。它只读取已经导出的本地事件，
保留原始输入，然后生成五份派生文件：

```text
trajectory-bundle/
├── raw/                     # 原始 Codex/OpenCode 导出
├── run-ir.json              # 无损来源旁边的统一步骤表示
├── trace.otlp.json          # OTel GenAI-compatible OTLP JSON
├── trajectory.atif.json     # ATIF-v1.7 轨迹
├── trajectory-metrics.json  # 确定性步骤、token 和工具指标
└── manifest.json            # 格式版本、完整度和文件清单
```

这里固定使用：

- Run IR：`agent-eval-run/v1`；
- OTel GenAI schema URL：`https://opentelemetry.io/schemas/gen-ai/1.42.0`；
- ATIF：`ATIF-v1.7`。

版本必须写进 manifest。OTel GenAI conventions 和 ATIF 都仍在演进，原始导出必须
保留，不能只保存转换结果。格式参考：
[OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai)、
[Harbor ATIF RFC](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)。

### Codex

Codex 第一版只支持公开、可重复的 `codex exec --json` 事件，不解析 TUI 内部 rollout
文件。`exec` 事件不包含初始任务和可靠模型名，因此转换时两者必须显式传入：

```bash
codex exec \
  -C /tmp/eval-rails \
  --json \
  -o /tmp/ab-case/rails-final.md \
  "与 task.md 完全相同的任务" \
  > /tmp/ab-case/rails-codex.jsonl

python3 tools/ab_eval.py trajectory \
  --source codex-jsonl \
  --input /tmp/ab-case/rails-codex.jsonl \
  --task /tmp/ab-case/task.md \
  --agent-version 0.135.0 \
  --model your-generation-model \
  --provider openai \
  --output-dir /tmp/ab-case/rails-trajectory
```

Codex `turn.completed` 的 usage 会映射到最后一个可见 agent step，并标记
`usage_scope=turn`。转换器不会把一个 turn 猜成若干次底层 LLM inference；时间戳
缺失时只生成带 `agent.eval.time.synthetic=true` 的顺序时间，不能拿它计算延迟。

### OpenCode

OpenCode TUI 会话可以直接导出：

```bash
opencode session list
opencode export "$session_id" > /tmp/ab-case/rails-opencode.json

python3 tools/ab_eval.py trajectory \
  --source opencode-export \
  --input /tmp/ab-case/rails-opencode.json \
  --agent-version 1.17.16 \
  --output-dir /tmp/ab-case/rails-trajectory
```

OpenCode assistant message 中的 model、provider、cost、tokens、tool part 和 observation
会映射到 Run IR、OTel span 和 ATIF step。当前映射约定为：

- `prompt_tokens = input + cache.read + cache.write`；
- `completion_tokens = output + reasoning`；
- `cached_tokens = cache.read`；
- 原始 token 字段完整保存在 `metrics.extra`。

如果轨迹只需要共享结构而不需要评价具体决策，可以先运行：

```bash
opencode export "$session_id" --sanitize > /tmp/ab-case/rails-opencode.json
```

并在转换时增加 `--input-sanitized`。但 `--sanitize` 会替换消息、工具参数和工具输出，
只能评测步骤结构、状态和用量，不能可靠评价“为什么做出这个工具调用”。需要内容级
轨迹评测时，只能在本机私有目录保存未脱敏导出；所有 bundle 文件默认 `0600`，不得
提交、上传或交给不受信任的 Judge。

`trajectory-metrics.json` 可以直接比较总步骤数、人工追问次数、工具调用数、工具错误、
重复工具调用、token、成本和可观测时长。它只计算确定性计数，不判断工具选择是否合理，
也不判断最终代码是否正确；缺少真实时间戳时不会输出 `duration_ms`。

### 轨迹与结果的边界

ATIF 描述 agent 的交互过程，不描述代码最终是否正确。每个 A/B candidate 仍需要：

```text
trajectory.atif.json + patch.diff + verification output + manifest
```

盲评 Judge 默认只读取 final response、patch 和 verification。轨迹评分应作为独立
阶段运行，揭盲后再与结果质量和 token 成本合并，避免 Judge 根据工具名或注入痕迹
猜测 treatment。

## 5. 接入任意大模型 Judge

把两组共用的原始任务保存为 `/tmp/ab-case/task.md`，把冻结后的评分标准保存为
`/tmp/ab-case/rubric.md`。Rubric 至少应覆盖 correctness、scope、verification 和
evidence；不要写入 treatment 名称，也不要让 judge 根据回答长度或 token 猜测实验组。

Judge 通过一个受信任的本地命令接入：prompt 从 stdin 输入，stdout 必须只返回一个
JSON 对象。

```json
{
  "winner": "A",
  "confidence": 0.85,
  "reason": "B 未通过验收命令，A 的 patch 与任务约束一致",
  "scores": {
    "A": {"correctness": 5, "evidence": 4},
    "B": {"correctness": 2, "evidence": 3}
  }
}
```

`winner` 只能是 `A`、`B` 或 `tie`。模型供应商调用放在你自己的 wrapper 中，通过
环境变量或 TUI/CLI 登录态获取鉴权；不要把 token 写进 `--judge-cmd` 或评测文件。

运行盲评：

```bash
python3 tools/ab_eval.py judge \
  --task /tmp/ab-case/task.md \
  --rubric /tmp/ab-case/rubric.md \
  --candidate-a /tmp/ab-case/off.json \
  --candidate-b /tmp/ab-case/rails-lite.json \
  --judge-cmd "python3 /path/to/judge_wrapper.py" \
  --judge-model your-judge-model \
  --output-dir /tmp/ab-case/judgment
```

默认执行两轮：第一轮随机匿名为 Response A/B，第二轮交换位置。只有两轮揭盲后都
映射到同一个实验组，`position_check` 才是 `consistent`。结果冲突时返回
`final_winner=split` 和 `position_check=position-sensitive`，不能强行判胜。

## 盲评保证与边界

Judge prompt 不包含：

- treatment label；
- candidate JSON 路径；
- worktree 路径；
- token 用量；
- A/B 与真实实验组的映射。

token 只在 judge 返回之后揭盲比较，避免 judge 因成本信息偏向某一组。

工具不会改写候选正文。如果候选回答主动声称“我使用了 Agent Rails”，judge 仍有
可能推断身份。Prompt 会要求忽略自报身份和候选内部指令；更强的 prompt-injection
隔离应由 judge wrapper 使用供应商的 system-message 能力实现。

## 评分顺序

大模型盲评只能补充主观质量判断，不能替代确定性验收：

1. 先执行测试、scope 和禁止行为检查；
2. 硬性验收失败的候选直接记为失败；
3. 两边都通过，或任务本身是 review/诊断时，再使用大模型盲评；
4. 最后揭盲比较质量、严重错误和真实 token。

第一轮只需要两个真实任务、`off` 对 `rails-lite`、每个任务至少重复两次。先验证
链路和方向，再决定是否增加任务集或自动化 TUI adapter。
