# Agent Rails

[简体中文](./README.md) | [English](./README.en.md)

让 coding agent 在动手前读对项目、在交付前跑对检查。

Agent Rails 是一套个人本地护栏，支持 Claude Code、Codex 和 OpenCode。接入以后，你仍然像平时一样和 agent 对话，不需要每天记住一组新命令。

## 适合什么情况

- 你同时维护多个项目或 worktree，不想让 agent 看错分支、目录或配置。
- 你希望复杂任务先整理上下文，提交或发布前再检查真实改动范围。
- 你只想自己使用这些能力，不想把个人工具文件提交进业务仓库。

## 五分钟开始

准备条件：本机已有 Git、Bash、Python 3.9+，以及 Claude Code、Codex 或 OpenCode 中的至少一个。

### 1. 安装 CLI（不需要 clone）

下载安装器，先查看内容，再执行：

```bash
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/install.sh \
  -o /tmp/agent-rails-install.sh
curl -fsSL https://github.com/948462448/agent-rails/releases/latest/download/release_install.py \
  -o /tmp/release_install.py
less /tmp/agent-rails-install.sh /tmp/release_install.py
bash /tmp/agent-rails-install.sh
"$HOME/.local/bin/agent-rails" init
```

按 `init` 输出提示，把几行 Shell 配置加入 `~/.zshrc`、`~/.bashrc` 或 Fish 配置，然后重新加载终端。如果以前从源码目录使用 Agent Rails，用这段新配置替换旧的 `AGENT_RAILS_HOME`。

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

把 `codex` 换成 `claude` 或 `opencode` 即可。想先看看会做什么，可以加 `--dry-run`。默认的 `--mode local` 会把 Adapter 留在当前项目目录，但通过本地 Git exclude 隐藏，因此不会要求其他协作者安装 Agent Rails。

评测有效、准备推广给团队时，再显式提升为可提交的项目模式：

```bash
agent-rails setup --tool codex --mode project
```

project 模式会移除 Agent Rails 管理的本地忽略块，并生成不含个人绝对路径的可提交文件；提交前仍应检查 diff。

如果本机只安装了一个受支持工具，也可以直接运行：

```bash
agent-rails setup
```

### 3. 重启 coding agent

- Codex：新开一个任务。
- Claude Code：重新打开会话。
- OpenCode：重启或新开 session。

之后就可以正常使用了。

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
AGENT RAILS: CHECK-ONLY (...)
AGENT RAILS: SKIPPED (...)
```

看到这些状态之一，就说明 agent 已经明确处理了 Agent Rails；`SKIPPED` 会同时说明为什么这次不需要使用。

## 交付前再确认一次

你可以直接让 agent 做“提交前检查”，也可以自己运行：

```bash
agent-rails verify
```

如果某个验证命令失败，Verify 会保留原始退出码和流式输出，并在末尾追加一个有界、
脱敏的 Repair Pack，帮助下一轮先聚焦首个诊断、已确认的位置，以及从同一 Git
快照检索出的少量相关源码和测试位置。

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

### 怎么更新或回滚 CLI

更新到最新 GitHub Release 不需要源码仓库：

```bash
agent-rails upgrade self
```

回滚或固定到已发布版本：

```bash
agent-rails upgrade self --version 0.6.1
```

要同时更新 CLI、运行检查并刷新当前项目的 Adapter，显式选择正在使用的 coding agent。例如 OpenCode：

```bash
agent-rails update --tool opencode
```

在目标仓库的根目录或任意子目录运行即可；Agent Rails 会自动解析 Git 仓库根目录。只有从仓库外操作时才需要显式传 `--project PATH`。

`update` 不猜工具；Claude 和 Codex 分别使用 `--tool claude`、`--tool codex`。首次接入项目仍使用 `setup --tool ...`。

### 会不会污染业务仓库

个人接入默认使用 `--mode local` 写入本地忽略区域，并保护已经被 Git 跟踪或由你自己创建的同路径文件。只有显式使用 `--mode project` 才会让受管 Adapter 文件出现在 Git 状态中；Agent Rails 仍不会替你提交、推送或发布。

## 隐私与安全

- 不要把 AccessKey、cookie 或 token 写入 Agent Rails 仓库。
- Base64 和 URL 编码不等于脱敏。
- 读取日志、网页或任务表格时，只让 agent 提取做决定所需的字段。

## 需要更多控制

日常使用到这里就够了。更新、卸载、自定义模型、上下文预算、Profile、在线 memory Adapter 和各工具的完整命令放在参考文档中：

- [中文 CLI 参考](./docs/cli-reference.zh-CN.md)
- [English CLI Reference](./docs/cli-reference.en.md)
- [Agent Rails 工作原理（含架构图与流程图）](./docs/how-agent-rails-works.zh-CN.md)
- [How Agent Rails Works](./docs/how-agent-rails-works.en.md)
- [设计与安全边界](./docs/local-adapters-and-release-safety.md)
- [GitHub Release 分发设计](./docs/github-release-distribution.md)
- [Token 预算与 OpenCode 请求钩子](./docs/token-budget-and-opencode-hook.zh-CN.md)
- [Coding Agent 演进方向与 GitHub 调研](./docs/coding-agent-evolution.zh-CN.md)
- [Local Brain 本地模型决策设计（评审稿）](./docs/local-brain-design.zh-CN.md)
- [Agent Rails 评测策略](./docs/evaluation-strategy.zh-CN.md)
- [TUI 黑盒 A/B 盲评手册](./docs/tui-ab-eval.zh-CN.md)
- [Shell 到 Python 重构交接与开发台账](./docs/python-refactor-handoff.zh-CN.md)
- [更新记录](./CHANGELOG.md)
