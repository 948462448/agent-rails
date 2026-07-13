# Agent Rails

个人本地 Agent Rails kit。这个项目不绑定业务仓库；业务仓库只作为 `--project` 目标被读取。

## 边界

- 不把 Agent Rails 文件提交到业务仓库。
- 不把 AccessKey、cookie、token 写入本项目。
- base64/URL 编码不是脱敏；读取日志、DOM、Job 表格时只取决策需要的字段，不展开 auth-bearing context。
- 用户级配置放在 `~/.agent-rails/`；项目级配置只放目标项目的 `.agent-rails/`，不要放进本 kit 的运行目录。
- SessionStart 注入的 profile 只属于来源仓库；同仓 worktree 要传准确根目录，sibling/不同仓库必须重新解析 profile。
- OpenMemory 写入、修复、导出验证脚本不在最小 kit 内；在线 memory 只作为 `pack` 的可选读取 provider。

## 常用命令

```bash
bin/agent-rails --version
bin/agent-rails update --project /path/to/project --profile /path/to/profile --session-hook
bin/agent-rails pack --project /path/to/project "本次任务目标"
bin/agent-rails run --project /path/to/project --model qwen3.7-max --pack-mode deep "本次任务目标"
bin/agent-rails run --project /path/to/project --model qwen3.7-max --pack-mode lite "POC / deploy prep 目标"
bin/agent-rails check --project /path/to/project --print-only
bin/agent-rails publish check --project /path/to/project
bin/agent-rails estimate --model glm5.1 --tokenizer char --file /path/to/task-pack.md
bin/agent-rails doctor --project /path/to/project --profile /path/to/profile
bin/agent-rails doctor --project /path/to/project --profile /path/to/profile --fix
bin/agent-rails doctor --project /path/to/project --profile /path/to/profile --openmemory-smoke
bin/agent-rails profile init --project /path/to/project
bin/agent-rails profile init --project /path/to/project --scope project
bin/agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local
bin/agent-rails claude install --project /path/to/project --profile /path/to/profile --mode local --session-hook
bin/agent-rails claude uninstall --project /path/to/project --session-hook --dry-run
bin/agent-rails codex install
bin/agent-rails codex install --project /path/to/project --fix-project
bin/agent-rails codex doctor --project /path/to/project
bin/agent-rails codex uninstall --dry-run
bin/agent-rails opencode install --project /path/to/project
bin/agent-rails opencode doctor --project /path/to/project
bin/agent-rails opencode uninstall --project /path/to/project --dry-run
bin/agent-rails skills install --dest /path/to/project/.claude/skills
```

如需启用在线 memory，个人配置可放在：

```text
~/.agent-rails/openmemory.env
```

## Agent skills

### Issue tracker

Issues and PRDs live in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the canonical five-label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository. See `docs/agents/domain.md`.
