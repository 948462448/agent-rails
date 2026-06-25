# Agent Rails

Agent Rails 是一个个人本地工程化辅助 kit，用来给 Claude Code / Codex / Qwen Code 这类 coding agent 提供同一套 Task Pack、memory cards、skill 蓝图和验证建议。

它不应该作为业务仓库的共享规范提交；业务仓库只是 `--project` 指向的 target project。

## Layout

```text
bin/agent-rails                 # CLI 入口
scripts/agent-context-pack.sh   # 生成 Task Pack
scripts/agent-run.sh            # 串起 pack/estimate/check/memory curator 的本地 wrapper
scripts/agent-check.sh          # 根据 diff 选择验证命令
scripts/agent-eval.sh           # 初始化评测集、记录 JSONL、生成报告
scripts/agent-estimate.sh       # 估算字符数和近似 token 数
scripts/agent-doctor.sh         # 诊断项目接入状态
scripts/agent-init-profile.sh   # 生成本地 project profile
scripts/agent-install-claude.sh # 安装 Claude Code adapter
scripts/agent-uninstall-claude.sh # 卸载 Claude Code adapter
hooks/agent-rails-session-start.sh # 可选 Claude Code SessionStart hook
codex-marketplace/.agents/plugins/marketplace.json # 本地 Codex plugin marketplace
scripts/agent-memory-suggest.sh # 记录模型 memory 判断，可选写本地 card
scripts/agent-install-skills.sh # 安装本地 skill 蓝图
scripts/agent-init-shell.sh     # 打印本地 shell 初始化指引
tests/run.sh                    # 本地 e2e 回归测试
profiles/default.profile        # kit 内置通用默认 profile
profiles/open-eval.profile      # 可选的 OpenEval profile 模板
~/.agent-rails/profiles/projects/*.profile # 用户级项目 profile
<project>/.agent-rails/profile  # 可选项目级 profile
~/.agent-rails/memory/<project>/*.md # 用户级本地 memory cards
skills/*/SKILL.md               # Claude Code / Codex 可安装 skill 蓝图
templates/task-pack.md          # Task Pack 模板
```

内置技能里已经包含 `agent-grill` 做开工前方案拷问，`agent-review` 做 code review，`agent-tdd` 做测试驱动开发，`agent-refactor` 做行为保持型代码重构，`agent-diagnose` 做问题排查，`agent-memory-curator` 做收尾 memory 价值判断。
`agent-eval` 用来把日常任务沉淀成可复现评测集，并记录 JSONL 运行日志。

## Quick Start

OpenEval 项目接入可以直接看 [docs/open-eval-quickstart.md](docs/open-eval-quickstart.md)。

先初始化本地命令：

```bash
cd /Users/songlei/workspace/agent-rails
bin/agent-rails init
```

把输出写入 `~/.zshrc` 后，后续就可以直接使用：

```bash
agent-rails pack \
  --project /path/to/project \
  "本次任务目标"
```

默认 Task Pack 输出到 `~/.agent-rails/agent-context/`，不会写入 kit 仓库或 target project。默认文件名包含当前 worktree 路径指纹，避免同一个 repo 的多个 worktree 互相读到旧 Task Pack。

想把默认闭环串起来，可以用：

```bash
agent-rails run \
  --project /path/to/project \
  --model qwen3.7-max \
  --pack-mode deep \
  "本次任务目标"
```

`run` 会生成 Task Pack、估算大小，并打印 agent 应该读取 Task Pack、执行检查、结束后运行 memory curator 的指令。它不硬控制 Claude/Codex 内核，只是把本地工作流变成一个稳定入口。对重构、迁移、架构、诊断、review 这类任务，如果你没有显式传 `--pack-mode`，`run` 会自动升到 `deep`。

可以先估算任意文件或文本的近似 token：

```bash
agent-rails estimate \
  --model glm5.1 \
  --file /path/to/task-pack.md
```

这个估算是 `字符数 / AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE`，默认 `2 chars/token`；所以 `12000 chars` 默认约等于 `6000 tokens`，不是 `120K tokens`。

需要真实 tokenizer 时有两种方式：

```bash
agent-rails estimate \
  --tokenizer tiktoken \
  --file /path/to/task-pack.md

agent-rails estimate \
  --tokenizer command \
  --tokenizer-command 'your-qwen-token-counter "$AGENT_RAILS_TOKENIZER_INPUT"' \
  --file /path/to/task-pack.md
```

`--tokenizer auto` 会优先使用 `AGENT_RAILS_TOKENIZER_CMD`，其次尝试本机 `tiktoken`，最后 fallback 到字符估算。Qwen/GLM 的精确 token 计数建议通过本地 tokenizer 命令接入。

需要限制上下文时可以加近似字符预算：

```bash
agent-rails pack \
  --project /path/to/project \
  --budget 12000 \
  "本次任务目标"
```

预算按 profile 中的比例分区，默认是 git 状态 20%、memory 40%、验证建议 20%、固定契约/清单 20%。默认不限制总预算，但本地 memory card 会以内嵌摘要形式进入 Task Pack，默认每张最多 1600 字符。Task Pack 也会摘取前几个文本变更文件，默认 8 个文件、每个最多 4000 字符，帮助 agent 先读关键 diff 附近的内容。没有命中任务或变更路径的本地 memory card 不会被强行塞进 Task Pack。
变更文件默认按 `smart` 排序：目标词命中、入口文档、Agent Rails 控制脚本、源码、测试和构建配置会被提前，并在 Task Pack 的 `Changed File Priority` 中展示分数和理由。

也可以按模型预设自动选择预算：

```bash
agent-rails pack \
  --project /path/to/project \
  --model qwen3.7-max \
  --pack-mode deep \
  "本次任务目标"
```

当前内置预设：

| model | context | max input | thinking input | max output | max reasoning | rpm | tpm | lite | normal | deep | audit |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `qwen3.7-max` | 1M | 991K | 983K | 64K | 256K | - | - | 24K | 60K | 160K | 320K |
| `glm5.1` | 202K | 202K | 166K | 128K | - | - | - | 12K | 24K | 60K | 100K |
| `deepseek-v4-pro` | 1M | 1M | - | 384K | - | 15000 | 1200000 | 24K | 60K | 160K | 320K |

`lite/normal/deep/audit` 是 Task Pack 推荐 token 预算。`lite` 面向 POC、快速原型、版本/Dockerfile/OSS/部署准备、codegen freshness check，保留 scope、memory、verification 和 checklist，但跳过完整 grill。脚本会用 `AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE` 把 token 预算转换成字符预算，默认 `2 chars/token`。需要手动覆盖时可以用 `--token-budget TOKENS` 或 `--budget CHARS`。

为一个项目生成本地 profile：

```bash
agent-rails profile init \
  --project /path/to/project
```

生成后的用户级 profile 位于 `~/.agent-rails/profiles/projects/`，后续会被 `pack/run/check/doctor/claude install` 自动发现。也可以显式传入：

```bash
agent-rails pack \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  "本次任务目标"
```

如果希望把 profile 放在目标项目目录下，使用项目级 `.agent-rails`：

```bash
agent-rails profile init \
  --project /path/to/project \
  --scope project
```

项目级 profile 会写到 `/path/to/project/.agent-rails/profile`，解析优先级高于用户级 profile。个人 local adapter 会把 `.agent-rails/` 写入本地 ignore，避免误提交到业务仓库。

可在 profile 中调整预算分区：

```bash
AGENT_RAILS_MODEL=generic
AGENT_RAILS_PACK_MODE=normal
AGENT_RAILS_GRILL_MAX_QUESTIONS=8
AGENT_RAILS_CONTEXT_BUDGET_TOKENS=
AGENT_RAILS_CONTEXT_BUDGET_CHARS=12000
AGENT_RAILS_CHARS_PER_TOKEN_ESTIMATE=2
AGENT_RAILS_TOKENIZER=auto
AGENT_RAILS_TOKENIZER_CMD=
AGENT_RAILS_TIKTOKEN_ENCODING=cl100k_base
AGENT_RAILS_BUDGET_GIT_PERCENT=20
AGENT_RAILS_BUDGET_MEMORY_PERCENT=40
AGENT_RAILS_BUDGET_VERIFY_PERCENT=20
AGENT_RAILS_BUDGET_CONTRACT_PERCENT=20
AGENT_RAILS_LOCAL_MEMORY_CARD_CHARS=1600
AGENT_RAILS_CHANGED_FILE_EXCERPT_LIMIT=8
AGENT_RAILS_CHANGED_FILE_EXCERPT_CHARS=4000
AGENT_RAILS_CHANGED_FILE_SORT=smart
```

安装到个人 Claude Code project skills：

```bash
agent-rails skills install \
  --dest /path/to/project/.claude/skills \
  agent-context-pack agent-check agent-review agent-diagnose
```

安装 Claude Code adapter，让 Claude 更容易优先使用 Agent Rails 做上下文编排。

接入后可先跑 doctor 看状态：

```bash
agent-rails doctor \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile
```

本地模式适合个人开发：生成 `.claude/` 和 `CLAUDE.local.md`，并把它们写入本地 ignore。Git 仓库中优先使用 `.git/info/exclude`，不改业务仓库的 `.gitignore`，也不改团队共享的 `CLAUDE.md`。

```bash
agent-rails claude install \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --mode local
```

如果发现 Claude Code 读到 `CLAUDE.local.md` 太靠后、容易跳过 Agent Rails，可以加一个个人全局提醒。它只写 `~/.claude/CLAUDE.md`，不改业务仓库：

```bash
agent-rails claude install \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --mode local \
  --global-reminder
```

这个全局块只负责提前提醒“如果当前 repo 有本地 Agent Rails adapter，按触发矩阵选择 deep/lite/check-only”；没有 `CLAUDE.local.md`/`.claude/AGENT_RAILS.md` marker 的项目会被明确要求忽略这段提醒。项目路径、profile、Task Pack 路径仍保留在本地 `CLAUDE.local.md` 和 slash command 里。

如果要像 Ponytail 一样在 Claude Code 会话启动时默认生效，用个人 SessionStart hook：

```bash
agent-rails claude install \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --mode local \
  --session-hook
```

`--session-hook` 会把一条个人 hook 写入 `~/.claude/settings.json`。hook 在 `startup|resume|clear|compact` 时运行，只在当前 repo 已经有 Agent Rails marker 时输出启动上下文；没有 marker 的项目保持静默。它负责把触发矩阵和 session marker 协议升到 SessionStart context，项目路径、profile 和 slash command 仍由本地 adapter 管理。

仓库也带了 `.claude-plugin/plugin.json`、`.codex-plugin/plugin.json` 和 `hooks/claude-hooks.json`。Claude/Codex plugin 形态都复用同一个 SessionStart hook；Codex 模式下 hook 会输出 `hookSpecificOutput.additionalContext` JSON。日常 Claude 个人使用时 `--session-hook` 更直接。安装/卸载 settings hook 需要本机有 `python3`，hook 运行本身只依赖 `bash`。

本地 Codex 试装可以用 repo-local marketplace：

```bash
codex plugin marketplace add /Users/songlei/workspace/agent-rails/codex-marketplace
codex plugin add agent-rails@agent-rails-local
```

安装后新开 Codex 线程才能看到 plugin 注入。当前线程内可用下面的自测模拟 Codex hook 输出：

```bash
PLUGIN_DATA=/tmp/agent-rails-plugin-data \
CLAUDE_PROJECT_DIR=/Users/songlei/workspace/agent-rails \
/Users/songlei/workspace/agent-rails/hooks/agent-rails-session-start.sh
```

接入后，agent 应在会话入口显式亮出状态：

```text
AGENT RAILS: ON (mode=<deep|lite|normal|audit>, pack=<task-pack-path>)
AGENT RAILS: CHECK-ONLY (reason=<reason>)
AGENT RAILS: SKIPPED (reason=<reason>)
```

这会写入 project-local 文件：

```text
.claude/skills/
.claude/commands/agent-rails-pack.md
.claude/commands/agent-rails-lite.md
.claude/commands/agent-rails-check.md
.claude/AGENT_RAILS.md
CLAUDE.local.md
```

Claude Code 中可直接用：

```text
/agent-rails-pack 本次任务目标
/agent-rails-lite POC / deploy prep 目标
```

这些 slash command 会在运行时解析当前 `git rev-parse --show-toplevel`，不会使用安装 adapter 时的旧 worktree 路径。执行后读命令打印的 `AGENT RAILS: ON (... pack=...)` 路径。

项目模式适合你想把 Claude 配置提交到业务仓库的情况：

```bash
agent-rails claude install \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --mode project
```

`--write-claude-md` 仍可用，等价于 `--mode project`。这两种模式都是强引导，不是 Claude Code 内核级硬拦截。

升级已安装的 adapter：

```bash
agent-rails claude upgrade \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --mode local \
  --global-reminder \
  --session-hook
```

卸载前可先预览：

```bash
agent-rails claude uninstall \
  --project /path/to/project \
  --global-reminder \
  --session-hook \
  --dry-run
```

卸载只移除 Agent Rails 生成的 `.claude/AGENT_RAILS.md`、slash commands、Agent Rails skill 目录、`CLAUDE.local.md` 中带 marker 的块、project/旧版 local `CLAUDE.md` 中带 marker 的块、本地 ignore marker、传入 `--global-reminder` 时的个人全局提醒块，以及传入 `--session-hook` 时的个人 SessionStart hook。

任务结束后由模型做 memory curator 判断。没有可复用价值时记录 skip：

```bash
agent-rails memory suggest \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --decision skip \
  --reason "本轮是一次性排查，没有可复用规则"
```

有明确可复用价值时，写一张小的本地 memory card：

```bash
agent-rails memory suggest \
  --project /path/to/project \
  --profile ~/.agent-rails/profiles/projects/project.profile \
  --decision keep \
  --write-local \
  --title "Pandora Boot stale jars" \
  --trigger "pandora boot" \
  --applies-to "backend" \
  --verify "after backend edits, run a full reactor mvn install before trusting pandora-boot:run" \
  --caution "verify on the current branch; build behavior can drift" \
  "Pandora Boot may serve stale BOOT-INF jars after backend edits, so compile-only restarts are weaker evidence than a full reactor install."
```

`memory suggest` 永远不会写 OpenMemory。在线 memory 在这个 kit 里仍然只是可选读取 provider。

Task Pack 还包含 `Subagent Result Contract`。当 Claude/Codex 派发 subagent 时，应要求子任务按该结构返回：目标、范围、证据、命令、未验证项、memory signals 和下一步建议；最终是否写 memory 由主 agent 的 `agent-memory-curator` 决定。

Task Pack 里的 `Trigger Matrix` 会把工作分成四档：

- `deep`：2+ 子项目、API/合约/schema/数据模型、ADR/实施手册、迁移/重构、需求含糊的产品决策。
- `lite`：POC、快速原型、版本/Dockerfile/OSS/部署准备、codegen freshness check、已有手册的续跑。
- `check-only`：部署、发布、上传等会消费当前分支的固定流程，以及改动后的最终交付。
- `skip`：纯查询、简单命令输出、无 repo 改动且不消费分支的固定操作。

无论进入哪一档，都应该先给用户一个可见 session marker；这样能直接判断本轮是否用了 Agent Rails，而不是事后靠总结推断。

Task Pack 里的 `Grill Gate` 会在架构、重构、迁移、API 合约、数据模型或需求不清的工作开始前触发。它要求 agent 先一问一答压实方案；能从代码、文档、ADR、测试或 Task Pack 找到答案时，先自己查证，再把推荐答案给出来。默认最多 8 问，剩余非阻塞问题进入 implementation handoff 的 deferred decisions。`lite` 模式跳过完整 grill，只问阻塞问题。

Memory 是跨 session 的长期真相，Task Pack 是本轮切片。Task Pack 不会写 memory；任务结束时应由 `agent-memory-curator` 判断是否 skip / create / update / merge。若 Task Pack 与 memory 口径不一致，先把旧 memory 视为待验证，而不是让 Task Pack 悄悄成为事实来源。

## Eval

先生成本地评测集骨架：

```bash
agent-rails eval init --dir evals
```

记录一次运行：

```bash
agent-rails eval record \
  --task evals/tasks/sample-code-review.yaml \
  --mode agentrails \
  --model qwen3.7-max \
  --pack-mode deep \
  --tokenizer char
```

生成报告：

```bash
agent-rails eval report \
  --runs evals/runs \
  --output evals/report.md
```

目录结构：

```text
evals/
  tasks/*.yaml      # 任务定义：repo/ref/prompt/expected/rubric
  rubrics/*.yaml    # 评分维度和人工打分模板
  runs/**/*.jsonl   # 每次运行的事件日志
  runs/**/artifacts # run/check 输出等证据
```

建议至少记录两种模式：`baseline` 和 `agentrails`。当前版本先记录可复现日志和基础报告，复杂自动判分可以后续叠加。

## Test

```bash
bash /Users/songlei/workspace/agent-rails/tests/run.sh
```

## OpenMemory

在线 memory 是可选读取 provider。默认 profile 不会读取个人配置；需要启用时，用 profile 或环境变量指定：

```text
AGENT_RAILS_ENV_FILE=~/.agent-rails/openmemory.env
```

示例：

```bash
export MEMORY_PROVIDER="hybrid"
export OPENMEMORY_BASE_URL="https://debug-openmemory.alibaba-inc.com"
export OPENMEMORY_MEMORY="agent_rails_memory"
export OPENMEMORY_INSTANCE="agent_rails_memory_card"
export OPENMEMORY_TOKEN_ENV="OPENMEMORY_ACCESS_KEY"
export OPENMEMORY_ACCESS_KEY=""
export OPENMEMORY_USER_ID="agent-rails"
export OPENMEMORY_SESSION_ID="agent-rails"
export OPENMEMORY_PROJECT_FILTER="your-project"
export OPENMEMORY_VECTOR_FIELD="body_vector"
export OPENMEMORY_VECTOR_SOURCE_FIELD="body"
```

不要提交这个配置文件。

检查在线读取链路时显式开启 smoke：

```bash
agent-rails doctor \
  --project /path/to/project \
  --profile /path/to/profile \
  --openmemory-smoke
```

离线预演请求体可设置：

```bash
export OPENMEMORY_DRY_RUN_REQUEST=1
export OPENMEMORY_REQUEST_DUMP_PATH=~/.agent-rails/agent-context/openmemory-doctor-smoke.json
```

## Design Notes

Agent Rails 摘取两类经验，但不直接照搬：

- 来自 PI：`搜 -> 读 -> 验 -> 交付`、质量门、失败后换道、修复后同类排查。
- 来自 small engineering skills：小技能可组合、每个项目先 setup 出 issue tracker / domain docs / ADR 布局、grill 先压实方案，TDD 和 diagnose 依赖快速反馈 loop。

因此默认配置保持轻量：Task Pack 只暴露执行契约、项目配置、memory cards 和验证建议；具体业务规则放在本地 profile 和 memory cards 里。
