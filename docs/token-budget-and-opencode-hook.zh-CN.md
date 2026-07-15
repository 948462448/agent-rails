# Token 预算与 OpenCode 请求钩子

这次改造把 Agent Rails 的上下文从“先按字符裁剪、生成后估算”改为“先确定本轮可用 token，再按类别组装并硬限制最终 Pack”。它只管理 Agent Rails 注入块，不删除、摘要或改写 OpenCode 自己的消息历史。

## OpenCode 每轮如何计算

项目级插件安装在 `.opencode/plugins/agent-rails.mjs`，通过 `experimental.chat.system.transform` 在每次模型请求前运行。新用户消息会刷新候选 Pack；同一轮的工具调用复用候选和已经足够小的 Pack，避免重复扫描仓库和重复加载 tokenizer。

预算公式：

```text
input_ceiling = model.limit.input
              或 model.limit.context - model.limit.output

reserve = max(固定保留 token, input_ceiling * 保留比例)
available = input_ceiling - 当前 system/messages token - reserve
rails_budget = min(
  Pack 最大 token,
  input_ceiling * Agent Rails 占比,
  available
)
```

默认 Agent Rails 最多占输入窗口的 25%，同时保留 5% 和至少 2048 token。剩余空间小于 512 token 时，本轮只注入带原因的 `SKIPPED` 标记。插件失败时也降级为标记或字符预算，不应阻塞正常模型请求。

## Pack 如何分配

最终 Pack 按以下类别分配：

| 类别 | 默认权重 | 典型内容 |
| --- | ---: | --- |
| 必保 | 10% | Session Marker、目标、预算、当前 Git 状态 |
| Git 证据 | 35% | 变更文件、优先级、文件摘录、工作区状态 |
| 契约 | 25% | Agent Rails Contract、项目配置、子 Agent 契约 |
| Memory | 15% | Memory provider 与 cards |
| 验证 | 15% | Verification Suggestions、Delivery Checklist |

先为关键栏目保留最小额度；某个类别没有内容或已经满足时，未使用 token 会回流给仍有内容需求的类别。最终输出再用同一 tokenizer 计数，保证不超过 hard cap。

## Tokenizer 选择

`auto` 的顺序是：本地 Hugging Face tokenizer 路径、外置命令、`tiktoken`、字符估算。OpenCode 插件启动一个常驻 Python JSONL 服务；Hugging Face tokenizer 只加载一次，相同文本的 token 数按内容哈希缓存。

DeepSeek 压缩包解压后可以直接把包含 `tokenizer.json` 和 `tokenizer_config.json` 的目录作为路径：

```bash
agent-rails pack \
  --project . \
  --token-budget 12000 \
  --tokenizer huggingface \
  --tokenizer-path /path/to/deepseek_v3_tokenizer \
  "任务目标"
```

这需要当前 `python3` 环境安装 `transformers`。Qwen、GLM 或其他 Hugging Face 兼容 tokenizer 使用同样方式。已有独立计数程序时也可以走外置命令；程序读取 `AGENT_RAILS_TOKENIZER_INPUT` 指向的 UTF-8 文件，并只向 stdout 输出一个非负整数：

```bash
agent-rails pack \
  --project . \
  --token-budget 12000 \
  --tokenizer command \
  --tokenizer-command 'my-token-counter "$AGENT_RAILS_TOKENIZER_INPUT"' \
  "任务目标"
```

## OpenCode Profile 配置

在项目对应的个人 Profile 中覆盖需要的值，然后重新执行 `agent-rails opencode install --project ... --profile ...`：

```bash
AGENT_RAILS_TOKENIZER="auto"
AGENT_RAILS_TOKENIZER_PATH="/path/to/tokenizer-directory"
AGENT_RAILS_TOKENIZER_CMD=""

AGENT_RAILS_OPENCODE_CONTEXT_PERCENT=25
AGENT_RAILS_OPENCODE_MAX_PACK_TOKENS=60000
AGENT_RAILS_OPENCODE_MIN_PACK_TOKENS=512
AGENT_RAILS_OPENCODE_RESERVE_PERCENT=5
AGENT_RAILS_OPENCODE_RESERVE_TOKENS=2048
AGENT_RAILS_OPENCODE_HOOK_TIMEOUT_MS=30000
```

安装后运行：

```bash
agent-rails opencode doctor --project .
```

Doctor 应同时报告 request hook 和 config plugin 为 `OK`。OpenCode 需要重启或新建 session 才会载入更新后的插件。

## 明确不做的事

- 不裁剪 OpenCode 原始历史；历史压缩仍由 OpenCode 负责。
- 不在每次 token 计数时重新加载 tokenizer。
- 不修改 `~/.config/opencode`；所有接入仍是项目本地且默认 Git local-ignore。
- 不把 tokenizer 模型文件复制进 Agent Rails 仓库。
