# Agent Rails

[简体中文](./README.md) | [English](./README.en.md)

让 coding agent 在动手前读对项目、在交付前跑对检查。

Agent Rails 是一套个人本地护栏，支持 Claude Code、Codex 和 OpenCode。接入以后，你仍然像平时一样和 agent 对话，不需要每天记住一组新命令。

## 适合什么情况

- 你同时维护多个项目或 worktree，不想让 agent 看错分支、目录或配置。
- 你希望复杂任务先整理上下文，提交或发布前再检查真实改动范围。
- 你只想自己使用这些能力，不想把个人工具文件提交进业务仓库。

## 五分钟开始

准备条件：本机已有 Git、Bash，以及 Claude Code、Codex 或 OpenCode 中的至少一个。Claude 的启动 Hook 还需要 Python 3。

### 1. 让终端找到 Agent Rails

进入 Agent Rails 仓库：

```bash
cd /path/to/agent-rails
bin/agent-rails init
```

按输出提示，把几行 Shell 配置加入 `~/.zshrc`、`~/.bashrc` 或 Fish 配置，然后重新加载终端。

确认安装：

```bash
agent-rails --version
```

### 2. 接入你的项目

进入业务项目，选择你实际使用的 coding agent：

```bash
cd /path/to/your-project
agent-rails setup --tool codex
```

把 `codex` 换成 `claude` 或 `opencode` 即可。想先看看会做什么，可以加 `--dry-run`。

如果本机只安装了一个受支持工具，也可以直接运行：

```bash
agent-rails setup
```

### 3. 重启 coding agent

- Codex：新开一个任务。
- Claude Code：重新打开会话。
- OpenCode：重启或新开 session。

之后就可以正常使用了。

OpenCode 接入会安装项目本地 `.opencode/plugins/agent-rails.mjs`。它像 Ponytail
一样在每轮对话通过插件钩子注入短规则，不需要先输入 `/agent-rails-lite`。单模块
任务默认使用不超过 1200 字符的 capsule；只有跨模块、契约、迁移等任务才生成
Task Pack，避免简单任务反复携带整份上下文。

## 日常怎么用

和平时一样，在项目里直接告诉 agent 你要做什么，例如：

```text
看看当前分支的改动，找出必须修的问题。
帮我重构这个模块，但不要改变现有行为。
准备发布了，检查一下范围、测试和敏感信息。
```

Agent Rails 会根据任务决定读取多少上下文、是否只做检查，并在开始时显示状态：

```text
AGENT RAILS: ON (...)
AGENT RAILS: ON (mode=capsule)
AGENT RAILS: CHECK-ONLY (...)
AGENT RAILS: SKIPPED (...)
```

看到这些状态之一，就说明 agent 已经明确处理了 Agent Rails；`SKIPPED` 会同时说明为什么这次不需要使用。

## 交付前再确认一次

你可以直接让 agent 做“提交前检查”，也可以自己运行：

```bash
agent-rails verify
```

它会根据真实改动选择并执行合适的检查。发布或部署时，如果你知道线上当前使用的源码 revision：

```bash
agent-rails verify --publish --base <deployed-source-revision>
```

## 常见问题

### 没看到 `AGENT RAILS` 状态

先确认已经重启 coding agent，然后在目标项目运行：

```bash
agent-rails doctor --project .           # Claude
agent-rails codex doctor --project .     # Codex
agent-rails opencode doctor --project .  # OpenCode
```

仍未接入时，重新运行对应的 `agent-rails setup --tool ...`。

### 本机装了多个 coding agent

`setup` 不会猜。使用 `--tool claude`、`--tool codex` 或 `--tool opencode` 明确选择；只有确实需要全部接入时才使用 `--tool all`。

### 切换了项目或 worktree

从新的准确目录启动 coding agent。不同仓库不要沿用旧项目的本地接入；需要时在新目录重新运行 `setup`。

### 会不会污染业务仓库

个人接入默认写入本地忽略区域，并保护已经被 Git 跟踪或由你自己创建的同路径文件。Agent Rails 不会替你提交、推送或发布。

## 隐私与安全

- 不要把 AccessKey、cookie 或 token 写入 Agent Rails 仓库。
- Base64 和 URL 编码不等于脱敏。
- 读取日志、网页或任务表格时，只让 agent 提取做决定所需的字段。

## 需要更多控制

日常使用到这里就够了。更新、卸载、自定义模型、上下文预算、Profile、OpenMemory 和各工具的完整命令放在参考文档中：

- [中文 CLI 参考](./docs/cli-reference.zh-CN.md)
- [English CLI Reference](./docs/cli-reference.en.md)
- [设计与安全边界](./docs/local-adapters-and-release-safety.md)
- [更新记录](./CHANGELOG.md)
