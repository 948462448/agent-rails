# Agent Rails

个人本地 Agent Rails kit。这个项目不绑定业务仓库；业务仓库只作为 `--project` 目标被读取。

## 边界

- 不把 Agent Rails 文件提交到业务仓库。
- 不把 AccessKey、cookie、token 写入本项目。
- OpenMemory 写入、修复、导出验证脚本不在最小 kit 内；在线 memory 只作为 `pack` 的可选读取 provider。

## 常用命令

```bash
bin/agent-rails pack --project /path/to/open-eval "本次任务目标"
bin/agent-rails check --project /path/to/open-eval --print-only
bin/agent-rails skills install --dest /path/to/open-eval/.claude/skills
```

个人配置放在：

```text
~/.agent-rails/openmemory.env
```
