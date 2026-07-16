# Shell 到 Python 重构交接与开发台账

状态：Shell 主体迁移完成（仅保留 47 行真实 OS/宿主/cold-start Seam）
最后核对：2026-07-16

这份文档记录 Agent Rails 当前已经完成的调研、实现、验证边界，以及把 Shell 主体迁移到 Python 时必须保留的契约。它是重构入口，不是愿望清单；后续每完成一个迁移切片，都应同时更新这里的状态和对应回归测试。

## 一句话决策

Python 重构已经完成主体切换。产品 CLI、策略与写入 Implementation 全部由 Python 拥有；只保留软链感知入口、两层 SessionStart host wrapper 和 standalone installer 四个无法由运行中 Python 替代的 Shell Seam。

评测继续放在独立 `tools/` 中，不重新塞回 `agent-rails` 产品 CLI。OpenCode 宿主要求的 JavaScript 插件模板也继续保留；“Shell 全改 Python”不等于把宿主原生插件改成 Python。

## 当前产品边界

- Agent Rails 是个人本地护栏，Target Project 只通过 `--project` 被读取或安装本地 Adapter。
- Agent Rails 管理自己注入的 SessionStart/Task Pack 上下文，不裁剪或改写 TUI 自己的历史消息。
- Profile 必须按准确仓库或 worktree 解析，不能向 sibling repository 泄漏。
- 在线 Memory 只保留 provider-neutral 的只读搜索 Interface：外部命令 Adapter 自管凭证和服务协议，读取查询元数据并向 stdout 输出 UTF-8 Markdown；失败不阻断 Pack，退回本地 Memory 切片。
- 评测是 Agent Rails 的外部证据，不是运行时能力。
- Release 能力冻结，不再驱动前期重构设计；等迁移到安装/升级阶段再决定保留哪些安装契约，Phase 1 不以 Release 兼容为 Gate。

## 调研结论与已采用决策

### 1. 评测先做真实 off 对照

核心问题不是“某个 Pack 看起来是否更完整”，而是同一模型、同一 TUI、同一仓库 SHA、同一任务下：

1. 完全没有 Agent Rails 的 `off`；
2. `session-only`；
3. `lite`、`normal`、`deep`、`audit`；
4. 固定模式有结果后再评 `auto`。

产物以 patch、最终回答、验收输出和 provider usage 为主，轨迹只解释过程，不能替代代码正确性。完整设计见[评测策略（中文）](./evaluation-strategy.zh-CN.md)和[TUI 黑盒 A/B 盲评手册](./tui-ab-eval.zh-CN.md)。

已采用的代码边界：

- 删除产品 CLI 中原有的 `agent-rails eval`、Shell logger 和 eval skill。
- `tools/ab_eval.py` 负责录制候选、匿名化标签、镜像交换 A/B 并调用外部 Judge。
- `tools/agent_trajectory.py` 把 Codex JSONL 或 OpenCode export 转成版本化 Run IR、OTel GenAI-compatible OTLP JSON、ATIF-v1.7 和确定性指标。
- 原始导出必须保留；OTel/ATIF 都可能演进，不能只留转换结果。

参考来源：

- [OpenTelemetry GenAI Semantic Conventions](https://github.com/open-telemetry/semantic-conventions-genai)
- [Harbor ATIF RFC](https://github.com/harbor-framework/harbor/blob/main/rfcs/0001-trajectory-format.md)

### 2. OpenCode 使用项目插件，不靠手工命令

[Ponytail](https://github.com/DietrichGebert/ponytail) 证明了项目级 `.opencode/plugins/*.mjs` 的接入形态可行：插件可以在每轮请求前注入规则，而用户仍然只在 TUI 中正常对话。

Agent Rails 当前采用 OpenCode 的 `experimental.chat.system.transform`：

- Hook 类型接收 `sessionID` 和 `model`，修改 `output.system`；
- OpenCode 在组装本轮模型请求时触发该 Hook；
- 插件读取当前 session messages，计算已占用空间，再组装本轮 Agent Rails Pack；
- 新用户消息刷新候选，同一轮工具调用复用候选与 tokenizer 服务。

源码依据：

- [OpenCode Plugins 文档](https://opencode.ai/docs/plugins/)
- [OpenCode plugin hook 类型](https://github.com/anomalyco/opencode/blob/dev/packages/plugin/src/index.ts)
- [OpenCode request 组装与 hook 调用](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/session/llm/request.ts)

风险边界：该 Hook 仍带 `experimental` 前缀。重构 Adapter 时必须保留一条契约测试，并在升级 OpenCode 后重新核对签名和触发位置。

### 3. Tokenizer 外置、可替换、按进程复用

用户提供的 DeepSeek tokenizer 包验证了本地 Hugging Face 目录的可行性：包含 tokenizer 配置/词表的目录可以由 `AutoTokenizer.from_pretrained(local_path)` 加载。包内容和用户本机路径不进入仓库。

Qwen 与 GLM 不需要各写一套 Agent Rails tokenizer 代码：

- [Qwen3 官方示例](https://github.com/QwenLM/Qwen3)使用 `AutoTokenizer.from_pretrained(model_name)`；
- [GLM 官方模型](https://huggingface.co/zai-org/GLM-4.7)同样提供 Transformers tokenizer 入口；
- Agent Rails 接受本地 Hugging Face tokenizer 目录，模型文件由用户管理；
- 已有计数程序时，使用外置命令协议：读取 `AGENT_RAILS_TOKENIZER_INPUT` 指向的 UTF-8 文件，只向 stdout 输出非负整数。

`auto` 的当前优先级是：本地 Hugging Face 路径、外置命令、`tiktoken`、字符估算。OpenCode 插件启动常驻 JSONL Python 服务，Hugging Face tokenizer 只加载一次，并按文本内容哈希缓存计数。

尚未完成：按具体线上模型名自动解析 Qwen/GLM/DeepSeek tokenizer 路径。当前映射仍由 Profile 显式提供；不能把通用 Hugging Face 支持宣传成所有模型都已经精确匹配。

### 4. 上下文只按可用输入窗口组装

OpenCode 本轮预算使用：

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

Agent Rails 只对注入 Pack 做 hard cap。默认类别权重为必保 10%、Git 证据 35%、契约 25%、Memory 15%、验证 15%；先满足关键栏目下限，再把空闲额度回流给仍有内容的类别。详见[Token 预算与 OpenCode 请求钩子](./token-budget-and-opencode-hook.zh-CN.md)。

### 5. 在线 Memory 只保留 provider-neutral Interface

已删除 Task Pack 内原专有在线实现的 HTTP、鉴权、表结构和 JSON 响应解析。Python `online-memory` Interface 现在只负责调用一个外部命令 Adapter，并通过 `AGENT_RAILS_MEMORY_QUERY_FILE`、`AGENT_RAILS_MEMORY_PROJECT`、`AGENT_RAILS_MEMORY_LIMIT` 提供最小查询元数据。

边界固定为：

- 只读 search，不在 Agent Rails 中提供在线 Memory 写入、修复或导出流程；
- Adapter 自行管理凭证、服务 URL、请求/响应协议和 provider SDK；
- Adapter 只向 stdout 返回 UTF-8 Markdown，stderr 和服务端错误细节不进入 Task Pack；
- Python 宿主以总 deadline 和流式 1 MB 上限读取 stdout，失败时终止整个 Adapter 进程组；Adapter 不得 daemonize 或主动脱离该进程组；
- 在线结果经过敏感输出脱敏后，以缩进的不可信数据区进入 Task Pack，不能伪造顶层章节；
- 在线查询未配置或失败时仍保留本地 Memory 切片，不阻断 Pack 生成；
- 默认 `MEMORY_PROVIDER=local`，只有显式配置 `online` 或 `hybrid` 以及 `AGENT_RAILS_ONLINE_MEMORY_CMD` 时才调用 Adapter；宿主默认用 `AGENT_RAILS_ONLINE_MEMORY_TIMEOUT_SECONDS=8` 限制单次查询时间。

## 开发进度台账

| 状态 | 能力 | 代码或文档 | 说明 |
| --- | --- | --- | --- |
| 已完成 | 独立 A/B 盲评 | `tools/ab_eval.py`、`tests/test_ab_eval.py` | 不依赖 Agent Rails CLI；支持候选完整性检查和位置交换 |
| 已完成 | Codex/OpenCode 轨迹标准化 | `tools/agent_trajectory.py` | 保留 raw，派生 Run IR、OTel、ATIF 和 metrics |
| 已完成 | 移除内置 eval | `bin/agent-rails`、`scripts/agent-eval.sh`（已删除） | 评测不再污染产品命令面 |
| 已完成 | OpenCode 项目插件 | `src/agent_rails/adapters/opencode.py`、`templates/opencode-agent-rails-plugin.mjs` | Python Application Service 负责 install/doctor/uninstall；不修改全局配置，Public CLI 直接进入 Python helper |
| 已完成 | 实时窗口预算 | `templates/opencode-agent-rails-plugin.mjs` | 读取 session/model limit；不裁剪 OpenCode 历史 |
| 已完成 | hard token Pack | `src/agent_rails/context/assembler.py`、`scripts/agent-context-assemble.py` | Python Module 拥有类别下限、权重、额度回流和最终硬上限；原脚本只保留 Trusted Python Bootstrap |
| 已完成 | 可替换 tokenizer | `src/agent_rails/models/tokenizer.py`、`src/agent_rails/context/assembler.py` | char、tiktoken、command、Hugging Face、常驻服务、缓存和 failover 共用一个 Tokenizer Interface |
| 已完成 | Python `estimate` tracer bullet | `src/agent_rails/estimate.py`、`tests/test_estimate.py` | Model/Tokenizer/Profile/CLI precedence 全部进入 Python；最终 Public CLI wrapper 已删除 |
| 已完成 | Python Target Project Context 全部调用者 | `src/agent_rails/core/paths.py`、`src/agent_rails/config/` | Run、Pack、Doctor、Verify、Update、Setup、Check、Publish、Claude/Codex/OpenCode 与 Memory Suggest 均已切换；调用者显式声明可跨 seam 的 Profile 字段 |
| 已完成 | Python `profile init` 纵向切片 | `src/agent_rails/config/profile_init.py`、`tests/test_profile_init.py` | Python 统一 canonical Git root、类型探测、Profile 渲染和原子私有写入；project scope 以 no-follow dirfd 锚定仓库边界 |
| 已完成 | Model Preset 与 Pack Policy | `src/agent_rails/models/presets.py`、`src/agent_rails/context/pack_policy.py` | Python 统一模型表、模式归一化、预算优先级、密度 cap 和分区字符额度；Doctor 复用 known-model Interface，旧 Model Preset Shell 已删除 |
| 已完成 | provider-neutral Online Memory Interface | `src/agent_rails/memory/online.py`、`src/agent_rails/context/memory_evidence.py` | 已移除 vendor-specific 实现；外部命令 Adapter 自管凭证/协议并返回 UTF-8 Markdown，失败回退本地 Memory |
| 已完成 | Python Git Scope Module | `src/agent_rails/git/scope.py`、`tests/test_git_scope.py` | Check、Publish Check 与 Task Pack 逐个切换到结构化 ref/path 快照；旧 Shell Implementation 已删除，显式项目隔离继承的 repo-local `GIT_*` |
| 已完成 | Python Sensitive Output Guard | `src/agent_rails/security/sensitive_output.py`、`tests/test_sensitive_output.py` | Task Pack 保守 fail-closed redaction 与 Publish 高精度 added-line scan 共用一个检测 Implementation；旧 AWK/Shell Implementation 已删除 |
| 已完成 | Python Task Pack Change Evidence | `src/agent_rails/context/change_evidence.py`、`tests/test_change_evidence.py` | Python 组合 Git Scope、goal ranking、diff-first 脱敏摘录、no-follow untracked 读取和五个 Git 栏目；保留 raw changed paths 给 Entry Docs 与 Memory |
| 已完成 | Python Task Pack Project Docs | `src/agent_rails/context/project_docs.py`、`tests/test_project_docs.py` | Python 消费 raw changed paths，统一 Entry Docs 路由、显式 target 存在性、Context Gaps、Project Configuration 与安全 Markdown 渲染 |
| 已完成 | Python Verification Plan | `src/agent_rails/verification/plan.py`、`tests/test_verification_plan.py` | Check 与 Task Pack 共用稳定 matcher、首因去重、NUL 命令 bundle 和隔离 target-tree shell 检查；Pack 不再递归启动 Check |
| 已完成 | Python Task Pack Memory Evidence | `src/agent_rails/context/memory_evidence.py`、`tests/test_memory_evidence.py` | Python 统一本地卡片发现、单双引号 trigger、预算切分、Sensitive Output 脱敏、安全 Markdown 与 provider-neutral 在线只读回退 |
| 已完成 | Python Task Pack Contract Sections | `src/agent_rails/context/contract_sections.py`、`tests/test_contract_sections.py` | Python 统一 Agent Rails、Subagent Result、Delivery 三类契约的顺序、Lite 提示、空规则与控制字符安全渲染 |
| 已完成 | Python Final Task Pack Renderer | `src/agent_rails/context/pack_renderer.py`、`tests/test_pack_renderer.py` | Python 统一 17 栏顺序、Goal/路径安全、Verification 动态 fence、hard cap、strict UTF-8 与同目录 `0600` 原子替换；过小预算保留旧 Pack 并失败 |
| 已完成 | Task Pack Markdown Interface | `src/agent_rails/context/markdown.py`、`tests/test_context_markdown.py` | Change Evidence、Project Docs、Memory、Contract 与 Final Renderer 共用控制字符、code span、fence 和 UTF-8 原语 |
| 已完成 | Task Pack Application Service | `src/agent_rails/context/pack_application.py`、`tests/test_pack_application.py` | Profile/env 与 Policy 各解析一次，context Module 全部进程内组合，显式 target SHA 冻结，Verification 非致命回退；Public CLI 直接进入 Python helper |
| 已完成 | Memory Suggestion Application Service | `src/agent_rails/memory/suggestion.py`、`tests/test_memory_suggestion.py` | Profile 仅允许本地 memory 目录且不加载 env file/在线凭证；Git 快照、ID、YAML/Markdown、安全扫描与发布均在 Python |
| 已完成 | Private Text Publisher | `src/agent_rails/core/private_text.py`、`tests/test_private_text.py` | Task Pack 与 Memory Suggest 共用完整 staging、`0600`、no-follow 目标检查、逐文件原子发布和部分提交结果；不虚构跨目录事务 |
| 已完成 | Python Adapter Content | `src/agent_rails/adapters/content.py`、`tests/test_adapter_content.py` | Claude/OpenCode guide、pack/lite/check 与 Claude project block 统一 typed 渲染；旧 276 行 Shell renderer 已删除，特殊 Profile 通过安全引用和 metadata 被 SessionStart 无损恢复 |
| 已完成 | Agent Check Application Service | `src/agent_rails/verification/check_application.py`、`tests/test_check_application.py` | Profile 只加载一次，Git Scope/Verification Plan 进程内冻结，target/scope 漂移 fail closed，子 Shell 使用显式 argv 与隔离环境 |
| 已完成 | Doctor Application Service | `src/agent_rails/diagnostics/doctor.py`、`tests/test_doctor_application.py` | Profile/env 失败分层、系统/插件/Memory/Adapter/Git 诊断和 Claude `--fix` 组合均进入 Python；bounded no-follow 读取与 visible escaping 封住跨项目和终端注入 |
| 已完成 | Publish Check Application Service | `src/agent_rails/verification/publish_check.py`、`tests/test_publish_check_application.py` | Profile 单次加载，冻结 Git Scope、Verification Plan、部署基线、remote 脱敏和四层 secret scan 均进入 Python；untracked 流式 no-follow |
| 已完成 | Python Managed Adapter Workspace | `src/agent_rails/adapters/workspace.py`、`tests/test_adapter_workspace.py` | Python 统一 Generated File、strict v2 Managed Skill Inventory、tracked/unmanaged 保护、技能生命周期、local Git exclude 解析与 local-ignore；项目内写入/递归复制删除以 no-follow dirfd 锚定，旧 439 行 Workspace Shell 已无调用者并删除 |
| 已完成 | Adapter Output Module | `src/agent_rails/adapters/events.py` | Claude、Codex、OpenCode 共用 terminal-safe stdout/stderr event、错误历史与结果渲染；工具特定请求和 lifecycle policy 保持独立 |
| 已完成 | OpenCode Adapter Application Service | `src/agent_rails/adapters/opencode.py`、`tests/test_opencode_adapter.py` | Profile 只解析一次，Python 直接组合 Content 与 Workspace；strict v2 skill fingerprint 与完整 preflight 保留 modified/legacy-unowned 内容 |
| 已完成 | Claude Adapter Application Service | `src/agent_rails/adapters/claude.py`、`tests/test_claude_adapter.py` | Python 直接组合 Target Context、Content 与 Workspace；rules/global reminder/settings/ignore 在 mutation 前 fail closed，local/project 与 dry-run/force 合同不变 |
| 已完成 | Codex Adapter Application Service | `src/agent_rails/adapters/codex.py`、`tests/test_codex_adapter.py` | Python 负责 install/doctor/uninstall、严格 action 参数、外部命令顺序/退出码、Profile-free Target Context、no-follow marker 与可见终端输出 |
| 已完成 | Run Facade | `src/agent_rails/run_application.py`、`tests/test_run_application.py` | Python 共享一次 Profile/environment，直接准备 Task Pack 并估算同一产物，保留阶段结果与退出码 |
| 已完成 | Setup Facade | `src/agent_rails/setup_application.py`、`tests/test_setup_application.py` | Python 只解析一次 Target Context/Profile，按显式工具意图组合 Adapter install 与 Doctor |
| 已完成 | Verify Facade | `src/agent_rails/verification/verify_application.py`、`tests/test_verify_application.py` | Python 共享一次 Context/Profile，直接组合 Agent Check 与可选 Publish Check，保留 live 输出、失败短路与 child exit |
| 已完成 | Public CLI Dispatcher | `src/agent_rails/public_cli.py`、`tests/test_public_cli.py`、`bin/agent-rails` | Python 拥有 help/version/home、嵌套命令校验、精确 argv/env 和 `--project` cwd；顶层 Shell 从 228 行降为 11 行 symlink-aware bootstrap |
| 已完成 | Update Application Service | `src/agent_rails/update_application.py`、`tests/test_update_application.py` | Python 统一 Git/Release 来源、clean/ff-only、skip/dry-run、Profile-free target 解析、Doctor/Adapter argv 和 Release re-exec |
| 已完成 | Release Install Application Service | `src/agent_rails/release/install.py`、`tests/test_release_install.py`、`scripts/agent-release-install.sh` | Python 拥有下载/checksum/archive/版本校验、完整目录发布、managed ownership 与失败回滚；279 行 installer Shell 降为 16 行相邻资产 bootstrap |
| 已完成 | Release Build Application Service | `src/agent_rails/release/build.py`、`tests/test_release_build.py` | Python 拥有隔离 Git file set、确定性单根 archive/checksum、固定安装资产、安全解包后的 full-CLI import smoke、no-clobber 发布、rollback 与失败 recovery 保留；CI/测试直接调用 isolated Python helper |
| 已完成 | SessionStart Application Service | `src/agent_rails/session_start.py`、`tests/test_session_start.py`、`hooks/agent-rails-session-start.sh` | Python 拥有 host/worktree 路由、no-follow marker、Profile metadata、稳定 guardrail 与 Claude/Codex envelope；144 行 hook 降为 12 行 bootstrap |
| 已完成 | Init Application Service | `src/agent_rails/init_application.py`、`tests/test_init_application.py` | Python 拥有 CLI/env 优先级和 zsh/bash/fish literal-safe guide 渲染；不写启动文件 |
| 已完成 | Skills Installer Application Service | `src/agent_rails/skills_install.py`、`tests/test_skills_install.py` | Python 拥有 manifest 选择、source tree 校验、完整 preflight、原子刷新和 rollback recovery；拒绝 symlink/traversal |
| 已完成 | Estimate Profile 收口 | `src/agent_rails/estimate.py`、`tests/test_estimate.py` | Profile allowlist、默认/显式路径和 CLI override 全部进入 Python |
| 已完成 | Paths Shell 删除 | `src/agent_rails/core/paths.py`、`src/agent_rails/config/{profile,target_project}.py` | 所有调用者已使用 Python Interface，删除无调用者的 108 行 `agent-paths.sh` |
| 已完成 | Public wrapper 删除 | `src/agent_rails/public_cli.py`、`scripts/agent-python-cli.py` | 17 个 public Shell 全部删除；Dispatcher 直接 process-replace 到 `-E/-I` isolated Python helper，保留 cwd/env/umask/信号/退出码 |
| 已完成 | 配置入口 | `profiles/default.profile` | OpenCode 占比、最大/最小 Pack、reserve、timeout 和 tokenizer |
| 已完成 | 设计与操作文档 | `docs/evaluation-strategy*.md`、`docs/tui-ab-eval.zh-CN.md`、`docs/token-budget-and-opencode-hook.zh-CN.md` | 当前调研结论已进仓库 |
| 待完成 | 真实 OpenCode GUI 多轮验证 | 尚无固定 fixture | 当前完成了 Hook API 源码核对和 mock runtime smoke，不等于真实 provider E2E |
| 待完成 | 第一条真实 A/B 数据 | 按 TUI 手册生成，产物不得提交源码仓库 | 先做一个任务、`off` 对 `rails-lite`、镜像盲评 |
| 待完成 | 模型名到 tokenizer 的本地映射 | 未来 Profile/registry | 需要按实际使用的 Qwen/GLM/DeepSeek 型号逐个核对 |
| 已完成 | Shell 主体迁移到 Python | 见下方阶段 | 所有产品策略 Implementation 已进入 Python；生产 Shell 只保留操作系统、宿主协议和 cold-start bootstrap |

### 2026-07-15 剩余量快照

统计 `bin/agent-rails`、`scripts/*.sh` 与两处生产 SessionStart hook，排除 tests、声明式 Profile 和 Python helper。相对本分支起点 `12bf738`，生产 Shell 从 7,425 行降到 47 行，净减 7,378 行（99.4%）。剩余 4 个文件分别是 11 行 symlink-aware CLI、12+8 行 SessionStart host wrappers 和 16 行 standalone installer；没有 public command、业务分支、配置解析、校验、渲染或写入策略。

| 责任 | 当前 Shell 行数 | 分支起点 | 下一阶段判断 |
| --- | ---: | ---: | --- |
| Task Pack / runtime | 0 | 2,533 | Pack、Memory Suggest、Check、Publish Check、Estimate 由 Public CLI 直接进入 Python |
| Adapter lifecycle | 0 | 2,291 | Skills 与 Codex/Claude/OpenCode lifecycle 由 Public CLI 直接进入 Python |
| Journey orchestration | 11 | 1,441 | 只保留顶层 symlink-aware POSIX CLI；分发和 Journey 均在 Python |
| Release / session host | 36 | 1,160 | 只保留两层 SessionStart host wrapper 和 standalone installer；Builder 已直调 Python |

目标不是零 Shell。当前值得迁移或去壳的行数均为 0；继续追求“零 Shell”会删掉 POSIX 启动、软链 home 解析、SessionStart host wrapper 和独立 Release installer 所需的真实平台 Seam。下一步只做完整回归、Release 安装 smoke 和真实 Adapter E2E。

## 当前 Shell 责任地图

重构不是逐文件机械翻译。应按责任边界迁移：

| 责任 | 当前入口 | 迁移注意点 |
| --- | --- | --- |
| CLI 分发与 home 解析 | `src/agent_rails/public_cli.py`、`bin/agent-rails` | Python 已拥有命令树/version/cwd；11 行 bootstrap 保持软链感知并覆盖父进程的旧 `AGENT_RAILS_HOME` |
| Target Project / Profile | `src/agent_rails/config/target_project.py`、`src/agent_rails/config/profile.py`、`src/agent_rails/config/profile_init.py` | 精确 Git root、worktree slug、Profile 优先级、调用者字段白名单和 sibling repo 隔离 |
| 模型与 token | `src/agent_rails/models/`、`src/agent_rails/context/pack_policy.py`、`src/agent_rails/context/assembler.py` | Tokenizer、Model Preset、Pack Policy 与最终预算分配已归入 Python；Assembler 旧脚本路径只保留兼容入口 |
| Task Pack | `src/agent_rails/context/` | Python Application Service 负责参数/Profile/Policy、全部证据/契约、17 栏总渲染、hard cap 和 `0600` 原子输出 |
| Memory Suggest | `src/agent_rails/memory/suggestion.py`、`src/agent_rails/core/private_text.py` | Python Application Service 负责本地决策与可选卡片；不加载在线 memory 凭证 |
| Git / 验证 / 发布 | `src/agent_rails/git/scope.py`、`src/agent_rails/verification/{plan,check_application,publish_check,verify_application}.py` | Python 统一 ref/path 快照、Verification Plan、Agent Check、Publish 报告/secret scan 与 Verify Journey |
| 敏感输出 | `src/agent_rails/security/sensitive_output.py` | 一个检测 Implementation；Task Pack 与 Publish 分别保留保守脱敏和高精度扫描策略 |
| Adapter 生命周期 | `src/agent_rails/adapters/{content,workspace,claude,opencode,codex}.py`、`src/agent_rails/diagnostics/doctor.py` | Content、Workspace、Codex/Claude/OpenCode lifecycle 与共享 Doctor 全部由 Python 拥有 |
| SessionStart | `src/agent_rails/session_start.py`、`hooks/agent-rails-session-start.sh` | Python 已拥有 exact worktree/Profile 路由、稳定短上下文和 Claude/Codex 宿主格式；Shell 仅余 12 行 bootstrap |
| Init / Skills / Estimate | `src/agent_rails/{init_application,skills_install,estimate}.py` | guide 渲染、skill tree publication、Profile/CLI precedence 均由 Python 拥有，无 Shell 入口 |
| 安装与升级 | `src/agent_rails/update_application.py`、`src/agent_rails/release/{install,build}.py`、`scripts/agent-release-install.sh` | Update、Release Install/Build 均进 Python；只保留 16 行 cold-start installer 资产 |
| 外部评测 | `tools/ab_eval.py`、`tools/agent_trajectory.py` | 保持产品外置；不得依赖 Target Project Profile |

## Python 目标结构

建议使用仓库内 `src` layout，Release 仍打包源码，不要求用户先执行 `pip install`：

```text
src/agent_rails/
├── __main__.py
├── cli.py
├── core/
│   ├── paths.py
│   ├── process.py
│   └── result.py
├── config/
│   ├── profile.py
│   └── target_project.py
├── models/
│   ├── presets.py
│   └── tokenizer.py
├── context/
│   ├── collect.py
│   ├── assembler.py
│   └── render.py
├── git/
│   └── scope.py
├── security/
│   └── sensitive_output.py
├── adapters/
│   ├── workspace.py
│   ├── claude.py
│   ├── codex.py
│   └── opencode.py
├── verification/
│   ├── check.py
│   └── publish.py
└── release/
    ├── build.py
    ├── install.py
    └── update.py
```

`tools/ab_eval.py` 和 `tools/agent_trajectory.py` 保持在 `tools/`，不要移动进 `src/agent_rails`。`templates/opencode-agent-rails-plugin.mjs` 保持 JavaScript，因为它运行在 OpenCode 插件宿主中。

## 必须冻结的兼容契约

在删除对应 Shell 前，每项都要有 Bash/Python 黑盒对照：

1. **CLI**：命令名、参数、默认值、帮助文本中的关键字段和退出码；参数错误为 `2`，缺少必要运行时沿用现有失败码。
2. **可见协议**：`AGENT RAILS: ON`、`CHECK-ONLY`、`SKIPPED` 及 Doctor 的 `[OK]`、`[WARN]` 输出不能无意漂移。
3. **路径**：用户级配置、项目级 `.agent-rails/`、worktree-specific Task Pack、Release 目录和软链保持兼容。
4. **Profile**：显式参数、项目配置、用户配置和默认 Profile 的解析顺序保持一致；不同仓库必须重新解析。
5. **Git**：target/base ref 校验、merge base、committed/staged/unstaged/untracked 范围和 deleted path 语义保持一致。
6. **写入安全**：Task Pack 事务替换和 `0600`；tracked path 保护；generated marker；精确 managed-skill inventory；dry-run/print-only 不写入。
7. **敏感输出**：Task Pack fail-closed redaction 与 publish 高精度扫描继续共用一套检测语法，但保留不同证据策略。
8. **安装模型**：源码 checkout 和 GitHub Release 安装都能运行；基础功能只依赖 Python 标准库，`tiktoken`、`transformers` 保持可选。

## 迁移阶段与 Gate

### Phase 0：冻结现状

- [x] Shell 测试按 core/adapters/workflows/context 分组，并支持 `tests/run.sh --related [PATH ...]` 按迁移切片选择相关 Suite。
- [x] 外置 A/B 工具已有 Python 单测并由总 runner 调用。
- [x] Token assembler 已形成第一个 Python 实现岛。
- [ ] 为每个 CLI 命令保存关键 stdout/exit-code golden contract；不要保存机器绝对路径或秘密。

Gate：`bash tests/run.sh` 全绿；删除 13 个只验证已删除 Shell 形态的入口并加入 Related Test Selection 契约后，保留 172 个行为/安全回归入口。

### Phase 1：第一个端到端 Python 切片

先迁移 `estimate`，不要先碰会修改 Adapter 或 Release 的命令：

- [x] 建立 `src/agent_rails` 和 Python CLI 启动器。
- [x] 迁移 Model Preset、tokenizer 选择和 estimate 渲染。
- [x] Shell dispatcher 只把 `estimate` 转发给 Python，旧 Shell Implementation 已删除。
- [x] 覆盖 char、command、auto failover/cache、stdin/file、Profile、已知/未知模型及公开错误码。
- [x] 基础路径只使用 Python 3.9 标准库；`tiktoken`、`transformers` 仍是可选 Adapter。
- [x] Release smoke 从 Phase 1 Gate 移除，留到安装/升级迁移阶段处理。

Gate：公开输出和退出码兼容，没有新增强依赖；workflows 与 context Test Suite 全绿。已完成。

### Phase 2：迁移纯共享模块

- [x] Paths 与 Release home 解析。typed Paths/Profile/Target Context、顶层软链感知和 Release home 均已进入 Python；最后一个调用者迁走后删除共享 Paths Shell。
- [x] Profile 和 Target Project Context。全部生产调用者已切换到 Python Module；现有可执行 Shell Profile 只作为隔离子进程 Adapter，旧共享 Shell Implementation 已删除。
- [x] Model Preset、Pack Policy 与 tokenizer registry。Python 为唯一数据源；旧 Model Preset Shell 已删除。
- [x] Online Memory 只读 Interface。Python 调用 provider-neutral 外部命令 Adapter；Agent Rails 不再包含 provider 专有协议、凭证或响应解析。
- [x] Git Scope 的只读 ref/path 快照。Check、Publish Check 与 Task Pack 已逐个切换，旧 Shell Implementation 已删除。
- [x] Sensitive Output Guard 的纯检测与证据策略。Task Pack 与 Publish 已切换，旧 AWK/Shell Implementation 已删除。

Gate：每个 Python 模块先有单测，再让一个现有命令切换调用；禁止同时重写调用者和删除原实现。

### Phase 3：收拢 Context Pipeline

- [x] 把 `agent-context-assemble.py` 移入 package，保留脚本兼容入口。
- [x] 将 Task Pack collect/render 与 Application 编排从 Shell 迁移到 Python；Shell 只保留 Trusted Python Bootstrap。
- [x] 将 Task Pack 的 Git Scope、changed-file ranking/excerpt 与五个 Git 栏目迁入 Python。
- [x] 将 Entry Docs 选择、Context Gaps 与 Project Configuration 迁入 Python。
- [x] 将 Verification Plan 的 matcher、顺序、去重、target-tree shell 检查与建议渲染迁入 Python；Check/Pack 共用同一 Interface。
- [x] 将本地卡片选择、Memory 预算与 provider-neutral 在线结果渲染迁入 Python；在线失败继续非致命回退本地。
- [x] 将 Agent Rails、Subagent Result 与 Delivery 契约栏目迁入 Python，并保持独立插入位置与控制字符安全。
- [x] 保持 hard cap、动态完整标题下限、额度回流、UTF-8 完整行、balanced fence、事务写入和 `0600`；过小预算 fail closed 并保留旧 Pack。
- [x] 将最终 17 栏渲染、Goal/路径/Verification 安全包裹与原子发布迁入 Python。
- [x] 抽取 Task Pack Markdown Interface，解除 Project Docs/Memory/Renderer 对 Change Evidence 格式化实现的依赖。
- [x] 将 Memory Suggest 的参数、Target Context、Git worktree 快照、决策/卡片渲染和私有发布迁入 Python；在线 memory 保持只读且不参与写入路径。
- [x] OpenCode 常驻 tokenizer 服务通过兼容脚本入口导入同一 `agent_rails.context.assembler` 模块，不复制实现。

Gate：同一 fixture 的关键栏目、Git 范围、敏感输出和 token hard cap 等价；允许非语义空白差异，但要显式记录。

### Phase 4：迁移 Adapter 生命周期

- [x] Generated Adapter Content：Claude/OpenCode guides、commands 与 Claude project block 已由 Python typed renderer 统一生成，旧共享 Shell renderer 已删除。
- [x] Workspace ownership 与 local-ignore 已进入 Python；所有调用者切换后旧 439 行共享 Shell 已删除。
- [x] OpenCode install/doctor/uninstall 已进入 Python，Public CLI wrapper 已删除。
- [x] Claude install/uninstall 已进入 Python，两处 Public CLI wrapper 已删除；global reminder 与 SessionStart settings 仍保留原公开合同。
- [x] Claude Doctor 已作为共享 Doctor Application Service 进入 Python，并通过 Claude lifecycle 组合 `--fix`。
- [x] Codex install/doctor/uninstall 已进入 Python，Public CLI wrapper 已删除；project 检查不执行 Profile，外部命令失败保留退出码。
- [x] SessionStart 已进入 Python，保持 Claude 纯文本/Codex JSON、exact worktree、Profile metadata/fallback、控制字符安全和无 marker 静默协议；Shell 仅余 12 行 bootstrap。
- [x] OpenCode `.mjs` 仍由模板生成并做 JavaScript 语法检查。

Gate：tracked/user-authored 文件不被覆盖；install→doctor→reinstall→uninstall 的 fixture 全绿。

### Phase 5：迁移验证、发布和更新

- [x] Check：完整报告、target guard、scope/plan 漂移防护与 child-shell 执行已迁入 Python；Public CLI wrapper 已删除。
- [x] Publish Check：部署基线、repo metadata、四层 secret scan 与 Verification suggestions 已进入 Python；Public CLI wrapper 已删除。
- [x] Verify Facade：共享一次 Context/Profile，直接组合 Check 与可选 Publish，保留 live 输出、失败短路和公开退出码；Public CLI wrapper 已删除。
- [x] Update：Git/Release 来源、clean/ff-only、source-test gate、Doctor/Adapter 编排和 Release re-exec 已进入 Python；Public CLI wrapper 已删除。
- [x] Release install：下载/checksum、archive layout、版本目录发布、managed symlink/metadata ownership 与失败 rollback 已进入 Python；Shell 仅余 16 行 cold-start bootstrap。
- [x] Release build：隔离 tracked/worktree file set、确定性 archive/checksum、固定安装资产、安全解包后的 full-CLI import smoke、no-clobber 发布、失败 rollback/recovery 保留已进入 Python；CI/测试直调 helper，Builder Shell 已删除。
- [x] Setup / Run Facade 只编排共享模块，不复制 Adapter Workspace、Pack Policy、Verification 或 Git 规则；Public CLI wrappers 已删除。

Gate：发布范围、部署基线、secret scan、Git/Release 双安装模式和 archive smoke 全绿。

### Phase 6：切换入口并删除 Shell

- [x] `bin/agent-rails` 已改为 11 行最小平台启动器，Python Public CLI 拥有命令树。
- [x] 17 个 public Compatibility Shell 已由直接 isolated Python helper dispatch 取代并删除；Release Builder Shell 同步删除。
- [x] 所有保留的 Shell entrypoint 都只拥有平台 bootstrap 责任，无产品策略，并由公开/安全合同回归覆盖。
- [x] Release 打包、成对安装资产和安装说明已切换到 Python Builder/Installer；`AGENTS.md` 公开命令不变。
- [x] 删除已失效的 Shell 形态断言，保留公开行为、隔离、cwd/env/argv、退出码与 Release archive 安全契约。

Gate：完整 Test Suite 与全新 Release install/update smoke 全绿；平台 bootstrap 作为稳定 Seam 保留，不以零 Shell 为目标。

## 发布前验证状态

Shell-to-Python 主体迁移与 0.6.1 本地 Release candidate 验证已经完成：

1. [x] 全部 172 个回归入口通过，并保留 Release Build/Install、SessionStart、Init、Skills、Estimate 的直接测试证据；
2. [x] 最终成对资产完成 checksum、隔离 cold-start install、用户级版本目录切换和 Release `update --skip-pull` smoke；
3. [x] OpenCode 在真实 Target Project 完成 install、doctor、uninstall、reinstall、插件语法、实际配置加载和 Task Pack E2E，业务仓库保持 Git clean；
4. [ ] Claude/Codex 宿主的真实新会话 E2E 与 OpenCode `off` 对 `rails-lite` A/B 仍作为后续产品证据，不阻塞 Python 迁移完成。

迁移后的关键边界如下：

- Git Scope 已覆盖 project/publish 默认基线、invalid ref、无共同祖先、committed/staged/unstaged/untracked/deleted/rename、target-only 和继承 `GIT_*` 隔离；三个调用者逐个切换后才删除旧 Shell；
- Sensitive Output Guard 已覆盖 placeholder、tokenizer 例外、代码表达式抑制、PEM/PGP 私钥块、diff added-line 行号、流式大文件处理与非 UTF-8 round-trip；Task Pack 与 Publish 的取证策略仍留在各自 Adapter；
- Verify、Check 与 Publish 已复用 Python Target Project Context，同时保持 `--publish`、`--print-only`、Verification Plan、部署基线和公开 exit-code 合同；
- `profile init` 已完成完整迁移，并新增 nested Git canonical root、repo-local `GIT_*` 隔离、project scope 软链逃逸防护、显式 `--force`、相对输出、检测优先级、Shell 注入防护和 `0600` 原子写入回归；
- Doctor 已完成完整迁移：缺失/非法 Profile 仍累计 `[FAIL]` 并最终退出 `1`，Profile/env file 失败精确归因，系统探测、在线 smoke 与 `--fix` 均由 Python Application Service 编排；
- Target Project 调用者已按 required、inspect、resolve-only 三类完成迁移；顶层软链感知与 Release home 也已由 Python dispatcher/Update/Release Interface 接管。
- Project Docs 已覆盖 changed-path 前缀路由、working-tree/显式 target 来源、继承 `GIT_*` 隔离、反引号路径与控制字符栏目注入防护。
- Verification Plan 已覆盖全部 matcher、稳定 suite 顺序、首因去重、opaque override、NUL round-trip、target tree、继承 `GIT_*` 隔离、symlink 拒绝，以及 Check/Pack 同一建议结果。
- Final Renderer 已覆盖固定栏目顺序、四类预算展示、动态 Verification fence、Goal/path 注入、hard cap、candidate、strict UTF-8、`0600` 原子替换和失败保留旧 Pack；Assembler 对实际输入结构动态保底并闭合截断 fence。

本阶段新增的回归证据包括：运行时 `HOME` 解析、完整 Profile 优先级、真实 Git worktree slug、嵌套 Git root、Profile/环境文件 finalize 顺序、sibling 项目 Profile 隔离、Shell 回填引用安全、Git Scope 结构化 fixture、Sensitive Output 两种证据策略、Context Budget Assembler、Change Evidence、Project Docs、Verification Plan、Release Build/Install、SessionStart、Init、Skills 与 Estimate 直接包测试，以及现有 Pack/Check/Publish 黑盒契约。Python Profile Module 仍将现有可执行 Shell Profile 视为受限内部 Adapter，尚未引入新的配置格式。

当前 macOS 系统 Python 为 3.9，因此迁移阶段最低版本保持 Python 3.9；`tiktoken`、`transformers` 等模型相关依赖继续通过可选 Adapter 提供。

## 真实验证仍欠什么

在开始大重构前，建议先留一条真实运行证据：

1. 在一个小型 fixture 创建 `off` 和 `rails-lite` 两个 worktree；
2. 使用同一 OpenCode 版本、模型、任务和验收命令，各开全新 session；
3. 用 `opencode export` 保存未脱敏本地轨迹，敏感内容只留本机私有目录；
4. 用 `tools/ab_eval.py capture` 录制 patch/final/verification/usage；
5. 做镜像盲评并记录 token；
6. 只把不含业务源码和鉴权信息的汇总结论更新到评测文档。

这条数据的目的不是证明统计显著性，而是证明“插件注入 → 真实 provider → 产物录制 → 盲评 → token 对比”的整条链路可用。

## 重构续接清单

新任务开始时按顺序执行：

1. 读 `CONTEXT.md` 和本文档；
2. 读[Token 预算设计](./token-budget-and-opencode-hook.zh-CN.md)与[评测策略](./evaluation-strategy.zh-CN.md)；
3. `git status --short --branch`，确认没有进入错误 worktree；
4. `bash tests/run.sh` 建立当前基线；
5. 一次只迁移一个责任边界，先加 Python 测试，再切一个调用者；
6. 每个切片更新本台账的复选框、已知差异、验证命令和 Changelog；
7. 不把评测 CLI、模型鉴权、tokenizer 模型文件或 TUI 登录态引入产品包。
