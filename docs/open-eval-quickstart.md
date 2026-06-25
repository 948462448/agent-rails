# OpenEval 接入 Agent Rails 快速开始

这份文档用于把个人本地的 Agent Rails kit 接到 `open-eval` 开发流程里。目标是让 Claude Code / Codex / Qwen Code 在动手前先读同一份 Task Pack，按同一套验证建议收尾，并由模型判断哪些经验值得沉淀成本地 memory。

Agent Rails 不绑定业务仓库。`open-eval` 只作为 `--project` 目标被读取；默认模式下生成的 Claude Code 配置会写入本地 ignore，不应该提交到 `open-eval`。

## 0. 初始化本地命令

第一次使用时，先在 Agent Rails 仓库里打印初始化指引：

```bash
cd /Users/songlei/workspace/agent-rails
bin/agent-rails init
```

按输出把这一段放进 `~/.zshrc`：

```bash
# Agent Rails
export AGENT_RAILS_HOME="/Users/songlei/workspace/agent-rails"
export PATH="$AGENT_RAILS_HOME/bin:$PATH"
alias ar="agent-rails"
export OPEN_EVAL_HOME="/Users/songlei/workspace/open-eval"
export OPEN_EVAL_PROFILE="$HOME/.agent-rails/profiles/projects/open-eval.profile"
mkdir -p "$(dirname "$OPEN_EVAL_PROFILE")"
test -f "$OPEN_EVAL_PROFILE" || cp "$AGENT_RAILS_HOME/profiles/open-eval.profile" "$OPEN_EVAL_PROFILE"
```

然后重新加载：

```bash
source ~/.zshrc
```

验证：

```bash
agent-rails --help
agent-rails home
ar doctor --project "$OPEN_EVAL_HOME" --profile "$OPEN_EVAL_PROFILE"
```

后面的命令都默认你已经完成这一步。你可以用完整命令 `agent-rails`，也可以用短 alias `ar`。

如果你的 `open-eval` 在其他路径，把 `OPEN_EVAL_HOME` 换成实际路径即可。

## 1. 先确认 CLI 可用

```bash
agent-rails --help
```

如果能看到 `pack`、`run`、`check`、`doctor`、`claude`、`eval` 等命令，说明入口正常。

## 2. 安装前做一次 doctor

```bash
ar doctor \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE"
```

这一步会检查：

- profile 是否能加载
- Task Pack 输出路径是否可写
- OpenMemory 是否配置
- Claude adapter 是否已安装
- skills 是否可安装
- target project 是否被 Git 跟踪

第一次跑时，Claude adapter 未安装是正常的。

## 3. 安装 Claude Code 本地 adapter

推荐先用本地模式：

```bash
ar claude install \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --mode local
```

本地模式会在 `open-eval` 里生成：

```text
.claude/skills/
.claude/commands/agent-rails-pack.md
.claude/commands/agent-rails-lite.md
.claude/commands/agent-rails-check.md
.claude/AGENT_RAILS.md
CLAUDE.local.md
```

同时会把这些 Agent Rails 文件写入 `open-eval/.git/info/exclude`。这不会修改业务仓库的 `.gitignore`，不会改团队共享的 `CLAUDE.md`，也不会把个人工具默认提交给团队。

安装后确认没有污染业务仓库状态：

```bash
git -C "$OPEN_EVAL_HOME" status --short
```

正常情况下不应该看到 `.claude/` 或 `CLAUDE.local.md` 出现在待提交文件里。已有团队版 `CLAUDE.md` 不属于 local 模式的写入目标。

再跑一次 doctor：

```bash
ar doctor \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE"
```

## 4. 第一轮推荐跑法

先在 Agent Rails 侧生成一次完整 Task Pack：

```bash
ar run \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --model qwen3.7-max \
  --pack-mode deep \
  --tokenizer char \
  "阅读当前 open-eval 变更，做一次 code review，优先找真实风险和需要验证的点"
```

默认输出：

```text
~/.agent-rails/agent-context/open-eval-<worktree-fingerprint>-task-pack.md
```

`run` 会做三件事：

- 生成 Task Pack
- 估算上下文大小
- 打印本轮推荐的 check / memory curator 命令

它不会自动执行模型；收尾 memory 是否写入由 `agent-memory-curator` 判断。
重构、迁移、架构、诊断、review 这类目标没有显式传 `--pack-mode` 时，`run` 会自动升到 `deep`；你也可以继续显式传 `--pack-mode deep` 固定行为。
POC、快速原型、whl/Dockerfile/OSS/部署准备、codegen freshness check 这类轻量但容易漏上下文或验证的任务，用 `--pack-mode lite`。lite 保留 Task Pack、memory cards、verification 和 checklist，但跳过完整 grill。

## 5. 在 Claude Code 里怎么用

进入 `open-eval` 后启动 Claude Code。因为已经安装了本地 adapter，Claude 会读取本地 `CLAUDE.local.md` 中的 Agent Rails 引导。团队共享规则仍然应该留在 `CLAUDE.md` 或项目已有的入口文档里。

每轮任务开始时，优先发：

```text
/agent-rails-pack 本次任务目标
/agent-rails-lite POC / deploy prep 目标
```

例如：

```text
/agent-rails-pack review 当前分支的后端改动，重点看任务提交、鉴权、runtime 参数传递
```

收尾前跑：

```text
/agent-rails-check
```

或者在终端直接跑：

```bash
ar check \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --print-only
```

`check --print-only` 只给验证建议，不会擅自执行重命令。
部署、发布、上传这类会消费当前分支的固定 skill，应把这一步作为 Step 0，即使本轮没有生成 Task Pack。

## 6. 可选：接入在线 memory

个人密钥不要写进 Agent Rails 仓库，也不要写进 `open-eval`。放到：

```text
~/.agent-rails/openmemory.env
```

示例：

```bash
MEMORY_PROVIDER=hybrid
OPENMEMORY_BASE_URL=https://debug-openmemory.alibaba-inc.com
OPENMEMORY_MEMORY=open_eval_agent_rails
OPENMEMORY_INSTANCE=agent_rails_memory_card
OPENMEMORY_TOKEN_ENV=OPENMEMORY_ACCESS_KEY
OPENMEMORY_LIMIT=5
OPENMEMORY_USER_ID=agent-rails
OPENMEMORY_ACCESS_KEY=你的本地密钥
```

配置后先普通 doctor：

```bash
ar doctor \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE"
```

需要试探在线 memory 读取链路时，再显式打开 smoke：

```bash
ar doctor \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --openmemory-smoke
```

在线 memory 仍然只作为读取 provider；这个 kit 不写 OpenMemory。任务结束后由 `agent-memory-curator` 自动判断是否值得写本地 memory。没有可复用价值时记录 skip：

```bash
ar memory suggest \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --decision skip \
  --reason "本轮是一次性输出，没有可复用规则"
```

有可复用价值时写一张小的本地 card：

```bash
ar memory suggest \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --decision keep \
  --write-local \
  --title "OpenEval checkpreload first probe" \
  --trigger "checkpreload auth readiness" \
  --applies-to "backend" \
  --verify "curl the target checkpreload.htm before reading business handlers" \
  --caution "environment-specific SSO behavior must still be verified" \
  "For OpenEval auth/readiness debugging, checkpreload.htm is the fastest first probe before reading deeper business handlers."
```

本地 card 写到 profile 的 `MEMORY_LOCAL_DIR`。默认 OpenEval profile 会让它留在 `~/.agent-rails/memory/open-eval`，不提交到 `open-eval`。

## 7. 可选：记录评测日志

如果想感受这套工具到底有没有提升，可以先用轻量评测日志记录日常任务。

初始化评测目录：

```bash
ar eval init \
  --dir "$AGENT_RAILS_HOME/evals/open-eval"
```

记录一次任务运行：

```bash
ar eval record \
  --dir "$AGENT_RAILS_HOME/evals/open-eval" \
  --task "$AGENT_RAILS_HOME/evals/open-eval/tasks/sample-code-review.yaml" \
  --project "$OPEN_EVAL_HOME" \
  --mode agentrails \
  --model qwen3.7-max \
  --pack-mode deep \
  --tokenizer char
```

生成报告：

```bash
ar eval report \
  --runs "$AGENT_RAILS_HOME/evals/open-eval/runs" \
  --output "$AGENT_RAILS_HOME/evals/open-eval/report.md"
```

这套日志不是最终标准评测集，只是先把日常任务、运行参数、产物和结果留下来。后面要做标准数据集，可以从这些记录里挑任务、补 rubric、补 expected findings。

## 8. 日常命令

生成上下文：

```bash
ar pack \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --model qwen3.7-max \
  --pack-mode deep \
  "本次任务目标"
```

轻量 POC / deploy prep：

```bash
ar pack \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --model qwen3.7-max \
  --pack-mode lite \
  "本次任务目标"
```

串起上下文、估算、检查建议：

```bash
ar run \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --model qwen3.7-max \
  --pack-mode deep \
  "本次任务目标"
```

只看验证建议：

```bash
ar check \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --print-only
```

升级 Claude adapter：

```bash
ar claude upgrade \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --mode local
```

预览卸载：

```bash
ar claude uninstall \
  --project "$OPEN_EVAL_HOME" \
  --dry-run
```

确认后卸载：

```bash
ar claude uninstall \
  --project "$OPEN_EVAL_HOME"
```

## 9. 推荐的第一次体验

可以先连续试三类任务：

- grill：挑一个架构、重构、迁移或接口设计问题，让 agent 先一问一答压实方案，再进入实现。
- code review：让 agent review 当前分支，观察它是否更快命中真实风险、验证建议是否贴近 open-eval。
- diagnose：给一个你熟悉的 open-eval 问题，让 agent 先读 Task Pack 再排查，观察它是否少走弯路。
- TDD/refactor：挑一个小改动，让 agent 先写测试或先锁定行为，再做实现。

每轮结束后都让它输出：

- 本轮读了哪些关键文件
- 哪些判断来自 Task Pack 或 memory
- 执行了哪些验证命令
- curator 判断结果：skip / 写入了哪张本地 memory card

这样几轮之后，就能比较直观地判断 Agent Rails 是否真的改善了本地 agent 的工程表现。

## 10. 常见问题

`doctor` 提示 Claude adapter 未安装：

先执行 `claude install --mode local`，再跑 doctor。

`git status` 里出现 `.claude/` 或 `CLAUDE.local.md`：

说明本地 ignore 没生效，或者这些文件之前已经被 Git 跟踪。先看：

```bash
grep -n "AGENT RAILS" "$OPEN_EVAL_HOME/.git/info/exclude"
```

Task Pack 太大：

降低 `--pack-mode`，例如从 `deep` 改成 `normal` 或 `lite`；或者显式设置：

```bash
--token-budget 30000
```

Task Pack 太小：

提高到 `--pack-mode audit`，或者调大 `--token-budget`。Qwen 3.7 Max 可以承受更大的 Task Pack，但仍建议先让上下文聚焦当前任务。

OpenMemory 没读到：

检查 `~/.agent-rails/openmemory.env`，确认 `MEMORY_PROVIDER`、`OPENMEMORY_*` 和 token env 名称一致。需要实际 smoke 时使用 `--openmemory-smoke`。

想把配置提交到 `open-eval`：

使用 project 模式：

```bash
ar claude install \
  --project "$OPEN_EVAL_HOME" \
  --profile "$OPEN_EVAL_PROFILE" \
  --mode project
```

但这会把 Agent Rails 引导变成业务仓库内容。当前推荐先用 `--mode local` 个人体验，不要急着团队化。
