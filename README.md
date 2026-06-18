# Agent Rails

Agent Rails 是一个个人本地工程化辅助 kit，用来给 Claude Code / Codex / Qwen Code 这类 coding agent 提供同一套 Task Pack、memory cards、skill 蓝图和验证建议。

它不应该作为 OpenEval 仓库的共享规范提交；OpenEval 只是一个 target project。

## Layout

```text
bin/agent-rails                 # CLI 入口
scripts/agent-context-pack.sh   # 生成 Task Pack
scripts/agent-check.sh          # 根据 diff 选择验证命令
scripts/agent-install-skills.sh # 安装本地 skill 蓝图
profiles/open-eval.profile      # OpenEval 项目 profile
memory/open-eval/*.md           # OpenEval seed memory cards
skills/*/SKILL.md               # Claude Code / Codex 可安装 skill 蓝图
templates/task-pack.md          # Task Pack 模板
```

## Quick Start

```bash
/Users/songlei/workspace/agent-rails/bin/agent-rails pack \
  --project /private/tmp/open-eval-traj-fix \
  --target-ref feat/20260615_traj_eval_poc_songlei \
  --output .scratch/agent-context/trajectory-eval-task-pack.md \
  "试水 agent 轨迹评测功能"
```

安装到个人 Claude Code project skills：

```bash
/Users/songlei/workspace/agent-rails/bin/agent-rails skills install \
  --dest /private/tmp/open-eval-traj-fix/.claude/skills \
  agent-context-pack agent-check agent-review agent-diagnose
```

## OpenMemory

在线 memory 是可选读取 provider。个人配置放到：

```text
~/.agent-rails/openmemory.env
```

示例：

```bash
export MEMORY_PROVIDER="hybrid"
export OPENMEMORY_BASE_URL="https://debug-openmemory.alibaba-inc.com"
export OPENMEMORY_MEMORY="open_eval_agent_rails"
export OPENMEMORY_INSTANCE="agent_rails_memory_card"
export OPENMEMORY_TOKEN_ENV="OPENMEMORY_ACCESS_KEY"
export OPENMEMORY_ACCESS_KEY=""
export OPENMEMORY_USER_ID="agent-rails"
export OPENMEMORY_SESSION_ID="agent-rails"
export OPENMEMORY_PROJECT_FILTER="open-eval"
export OPENMEMORY_VECTOR_FIELD="body_vector"
export OPENMEMORY_VECTOR_SOURCE_FIELD="body"
```

不要提交这个配置文件。
