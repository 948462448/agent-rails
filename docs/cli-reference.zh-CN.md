# Agent Rails CLI Reference

[简体中文](./cli-reference.zh-CN.md) | [English](./cli-reference.en.md)

本文档面向需要自定义、排障或开发 Agent Rails 的用户。日常路径只需要 README 中的 `setup`、`run`、`verify`。

## 使用面门面

### `setup`

```bash
agent-rails setup \
  [--project PATH] \
  [--profile PATH] \
  [--tool auto|claude|codex|opencode|all] \
  [--mode local|project] \
  [--no-session-hook] \
  [--dry-run]
```

- `auto` 只在恰好检测到一个受支持 CLI 时继续。
- 多工具环境必须显式选择；`all` 表示明确接受全部个人安装。
- 三种工具都默认使用 `local`：文件位于项目内，但只写入本地 Git exclude，不影响协作者。
- `project` 会移除受管本地忽略块并生成可提交、无个人绝对路径的 Adapter；适合验证后推广。
- Claude 默认安装 SessionStart hook；`--no-session-hook` 只关闭这个个人 Hook。
- Codex 复用已有 plugin 安装和 project repair 流程；OpenCode 不修改全局配置。

### `run`

```bash
agent-rails run \
  [--project PATH] \
  [--profile PATH] \
  [--model NAME] \
  [--pack-mode lite|normal|deep|audit] \
  [--budget CHARS|--token-budget TOKENS] \
  [--tokenizer auto|char|tiktoken|command|huggingface] \
  [--tokenizer-command CMD] \
  [--tokenizer-path PATH] \
  [--print-only] \
  "目标"
```

`run` 编排 `pack`、`estimate`、`check` 和 memory handoff，但不硬控制 Agent 内核。

指定 `--token-budget` 后，`pack` 会按栏目权重组装并硬限制最终 token 数；空闲类别的额度会回流。`huggingface` 从 `--tokenizer-path` 加载本地 tokenizer，`command` 从 `AGENT_RAILS_TOKENIZER_INPUT` 读取输入文件。OpenCode 的逐请求预算与 Profile 参数见 [Token 预算与 OpenCode 请求钩子](./token-budget-and-opencode-hook.zh-CN.md)。

### `pack`

```bash
agent-rails pack \
  [--project PATH] \
  [--profile PATH] \
  [--task-file PATH] \
  [--rubric-file PATH] \
  [--pack-mode lite|normal|deep|audit] \
  "目标"
```

复杂开发或评测任务应显式传入冻结的任务和评分文件。相对路径按目标项目解析，绝对路径可用于个人评测目录；文件必须是普通、非符号链接的严格 UTF-8 文本，进入 Task Pack 前会经过敏感输出脱敏。

显式文件会完整进入受保护的 Product Contract，并生成稳定的 `AC-*` / `RUB-*` 编号和 Acceptance Evidence Matrix。硬 token 预算不足以容纳完整合同时，`pack` 会失败，不会静默截断合同。如果目标声称存在“附件”或“冻结合同”却没有传入文件，`pack` 同样拒绝生成，避免 agent 在缺失需求时猜测。Profile 中配置的验证命令优先；未配置时，`pack` 会从项目结构探测建议命令，并在干净工作树中使用任务相关代码证据确定验证范围。

OpenCode 等逐请求 Hook 会重复生成 Task Pack。启动 coding agent 进程时可设置 `AGENT_RAILS_TASK_FILE` 和 `AGENT_RAILS_RUBRIC_FILE`，让冻结合同在整个会话持续生效；命令行参数优先于同名环境变量。不要把这两个变量写进共享 Profile。

### `verify`

```bash
agent-rails verify \
  [--project PATH] \
  [--profile PATH] \
  [--print-only] \
  [--publish] \
  [--base REF] \
  [--target-ref REF] \
  [--no-secret-scan]
```

- 默认通过 `check --run` 执行 Verification Plan。
- `--print-only` 只预览。
- `--publish` 在普通验证后运行只读的 `publish check`。
- `--no-secret-scan` 只允许与 `--publish` 一起使用。

## 高级命令分组

| 场景 | 命令 |
| --- | --- |
| 上下文 | `pack`、`estimate` |
| 验证与发布 | `check`、`publish check`、`doctor` |
| Profile | `profile init` |
| Adapter | `claude install/uninstall`、`codex install/doctor/uninstall`、`opencode install/doctor/uninstall` |
| 维护 | `update`、`upgrade self`、`init`、`home` |
| 扩展 | `skills install`、`memory suggest` |

每个命令的准确参数以 `agent-rails <command> --help` 为准。

TUI A/B 评测不属于 Agent Rails 产品 CLI。使用独立的 `python3 tools/ab_eval.py`；录制、盲评和轨迹转换流程见 [TUI 黑盒 A/B 盲评手册](./tui-ab-eval.zh-CN.md)。

不带项目参数的 `agent-rails init` 只打印 Shell 命令安装配置，不绑定仓库。只有显式传入 `--project` 或已有对应环境变量时，才会继续打印固定项目/Profile 的兼容配置。

## 安装与自更新

GitHub Release 安装默认使用以下路径：

- 版本目录：`~/.local/share/agent-rails/releases/<version>`
- 当前版本：`~/.local/share/agent-rails/current`
- CLI 入口：`~/.local/bin/agent-rails`

只更新 kit，不解析当前目录的项目或 Profile：

```bash
agent-rails upgrade self [--version VERSION] [--repository OWNER/REPO] \
  [--install-root PATH] [--bin-dir PATH] [--skip-tests] [--dry-run]
```

同时更新 kit 并维护一个项目 Adapter：

```bash
agent-rails update --tool claude|codex|opencode [--project PATH] [--profile PATH] \
  [--mode local|project] [--session-hook] [--global-reminder] \
  [--skip-pull] [--skip-tests] [--skip-doctor] [--skip-adapter] [--dry-run]
```

`update` 必须显式选择一种工具，并依次运行该工具的 pre-update Doctor、Adapter 刷新和 final Doctor。三种工具统一支持 `--mode local|project`；`--session-hook` 和 `--global-reminder` 仍只属于 Claude。源码 checkout 使用 `git pull --ff-only` 并运行源码测试；Release 安装下载归档、校验 SHA-256、原子切换版本，并跳过不适用于归档安装的源码测试。`--skip-pull` 在两种安装来源下都表示跳过 kit 本身的更新。

## Profile 与项目边界

Profile 解析顺序：

1. 显式 `--profile`
2. `<project>/.agent-rails/profile`
3. `<project>/.agent-rails/profile.sh`
4. `~/.agent-rails/profiles/projects/<project>.profile`
5. `~/.agent-rails/profiles/<project>.profile`
6. kit 的 `profiles/default.profile`

同仓 worktree 可以复用仓库 Profile，但必须传准确 worktree 根；不同仓库不得沿用当前 SessionStart 注入的 Profile。

## Pack Mode

| 模式 | 使用场景 |
| --- | --- |
| `lite` | POC、部署准备、已有方案的聚焦续跑 |
| `normal` | 常规实现 |
| `deep` | 重构、迁移、架构、诊断、review |
| `audit` | 显式高密度审计 |

模式只改变证据密度，不删除能力栏目。Model Preset Module 统一维护模型 alias、限制和预算。

## Adapter 所有权

Managed Adapter Workspace 只刷新或删除带 Agent Rails ownership marker 的生成物和清单中记录的 skill。tracked 文件、用户自建同路径文件和无关 `agent-*` skill 默认保留。`local` 使用 `.git/info/exclude` 且不修改团队 `.gitignore`；`project` 移除受管 exclude 并写入可提交的 portable 内容。

## 发布基线

`publish check` 的 base 应是当前已部署源码 revision。upstream 只是源码基线，不证明部署状态；无法建立部署增量时命令会报告 `Deployment delta: UNRESOLVED`。

## Memory 与敏感输出

本地 card 位于 `~/.agent-rails/memory/<project>/`，也是本 kit 唯一会显式写入的 memory。在线 memory 通过可选的外部只读 Adapter 接入：

```bash
MEMORY_PROVIDER=hybrid
AGENT_RAILS_ONLINE_MEMORY_CMD='/path/to/read-only-memory-adapter'
AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS=8
```

Adapter 从 `AGENT_RAILS_MEMORY_QUERY_FILE`、`AGENT_RAILS_MEMORY_PROJECT` 和 `AGENT_RAILS_MEMORY_LIMIT` 读取查询上下文，向 stdout 输出 UTF-8 Markdown。一次调用必须是一个有界进程树，不支持 daemonize 或主动脱离进程组；宿主执行总 deadline、流式 1 MB 输出上限、敏感信息脱敏，并把返回内容包在不可信数据区。凭证、网络协议和供应商细节由 Adapter 的运行环境自管，不得写入 Profile、Task Pack 或仓库。Base64/URL 编码不算脱敏。可用 `doctor --online-memory-smoke` 显式测试该只读路径。

## 相关设计

- [Agent Rails Context](../CONTEXT.md)
- [Agent Rails 工作原理](./how-agent-rails-works.zh-CN.md)
- [How Agent Rails Works](./how-agent-rails-works.en.md)
- [Local Adapters And Release Safety](./local-adapters-and-release-safety.md)
- [GitHub Release Distribution](./github-release-distribution.md)
- [Development Milestones](./development-milestones.md)
