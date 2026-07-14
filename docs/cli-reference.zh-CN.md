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
  [--no-session-hook] \
  [--dry-run]
```

- `auto` 只在恰好检测到一个受支持 CLI 时继续。
- 多工具环境必须显式选择；`all` 表示明确接受全部个人安装。
- Claude 使用 local mode，默认安装 SessionStart hook。
- Codex 复用已有 plugin 安装和 project repair 流程。
- OpenCode 只写项目本地 Adapter，不修改全局 OpenCode 配置。

### `run`

```bash
agent-rails run \
  [--project PATH] \
  [--profile PATH] \
  [--model NAME] \
  [--pack-mode lite|normal|deep|audit] \
  [--budget CHARS|--token-budget TOKENS] \
  [--tokenizer auto|char|tiktoken|command] \
  [--print-only] \
  "目标"
```

`run` 编排 `pack`、`estimate`、`check` 和 memory handoff，但不硬控制 Agent 内核。

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
| 扩展 | `skills install`、`memory suggest`、`eval init/record/report` |

每个命令的准确参数以 `agent-rails <command> --help` 为准。

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

`agent-rails update` 是更宽的维护流程：更新 kit 后，还会按参数运行测试、目标项目 Doctor 和 Adapter 刷新。源码 checkout 继续使用 `git pull --ff-only`；Release 安装下载归档并校验 SHA-256 后原子切换版本。`--skip-pull` 在两种安装模式下都表示跳过 kit 本身的更新。

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

Managed Adapter Workspace 只刷新或删除带 Agent Rails ownership marker 的生成物和清单中记录的 skill。tracked 文件、用户自建同路径文件和无关 `agent-*` skill 默认保留。Git 仓库优先使用 `.git/info/exclude`，不修改团队 `.gitignore`。

## 发布基线

`publish check` 的 base 应是当前已部署源码 revision。upstream 只是源码基线，不证明部署状态；无法建立部署增量时命令会报告 `Deployment delta: UNRESOLVED`。

## Memory 与敏感输出

本地 card 位于 `~/.agent-rails/memory/<project>/`。在线 memory 只作为可选读取 provider；本 kit 不写 OpenMemory。AccessKey、cookie、token 不得写入仓库；Base64/URL 编码不算脱敏。

## 相关设计

- [Agent Rails Context](../CONTEXT.md)
- [Local Adapters And Release Safety](./local-adapters-and-release-safety.md)
- [GitHub Release Distribution](./github-release-distribution.md)
- [Development Milestones](./development-milestones.md)
