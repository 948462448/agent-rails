# Shell 到 Python 重构交接与开发台账

状态：可续接基线  
最后核对：2026-07-15

这份文档记录 Agent Rails 当前已经完成的调研、实现、验证边界，以及把 Shell 主体迁移到 Python 时必须保留的契约。它是重构入口，不是愿望清单；后续每完成一个迁移切片，都应同时更新这里的状态和对应回归测试。

## 一句话决策

采用渐进式替换，不做一次性重写：保留现有 CLI 和磁盘契约，以黑盒回归测试锁定行为；先迁移一个只读的端到端命令，再逐步抽走共享模块，最后删除兼容 Shell。

评测继续放在独立 `tools/` 中，不重新塞回 `agent-rails` 产品 CLI。OpenCode 宿主要求的 JavaScript 插件模板也继续保留；“Shell 全改 Python”不等于把宿主原生插件改成 Python。

## 当前产品边界

- Agent Rails 是个人本地护栏，Target Project 只通过 `--project` 被读取或安装本地 Adapter。
- Agent Rails 管理自己注入的 SessionStart/Task Pack 上下文，不裁剪或改写 TUI 自己的历史消息。
- Profile 必须按准确仓库或 worktree 解析，不能向 sibling repository 泄漏。
- 评测是 Agent Rails 的外部证据，不是运行时能力。
- Release 安装不能依赖源码 checkout，重构后仍需支持当前版本目录、`current` 软链和稳定 CLI 入口。

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

## 开发进度台账

| 状态 | 能力 | 代码或文档 | 说明 |
| --- | --- | --- | --- |
| 已完成 | 独立 A/B 盲评 | `tools/ab_eval.py`、`tests/test_ab_eval.py` | 不依赖 Agent Rails CLI；支持候选完整性检查和位置交换 |
| 已完成 | Codex/OpenCode 轨迹标准化 | `tools/agent_trajectory.py` | 保留 raw，派生 Run IR、OTel、ATIF 和 metrics |
| 已完成 | 移除内置 eval | `bin/agent-rails`、`scripts/agent-eval.sh`（已删除） | 评测不再污染产品命令面 |
| 已完成 | OpenCode 项目插件 | `scripts/agent-opencode.sh`、`templates/opencode-agent-rails-plugin.mjs` | install/doctor/uninstall；不修改全局配置 |
| 已完成 | 实时窗口预算 | `templates/opencode-agent-rails-plugin.mjs` | 读取 session/model limit；不裁剪 OpenCode 历史 |
| 已完成 | hard token Pack | `scripts/agent-context-pack.sh`、`scripts/agent-context-assemble.py` | 类别下限、权重、额度回流、最终硬上限 |
| 已完成 | 可替换 tokenizer | `scripts/agent-context-assemble.py`、`scripts/agent-estimate.sh` | char、tiktoken、command、Hugging Face、常驻服务与缓存 |
| 已完成 | 配置入口 | `profiles/default.profile` | OpenCode 占比、最大/最小 Pack、reserve、timeout 和 tokenizer |
| 已完成 | 设计与操作文档 | `docs/evaluation-strategy*.md`、`docs/tui-ab-eval.zh-CN.md`、`docs/token-budget-and-opencode-hook.zh-CN.md` | 当前调研结论已进仓库 |
| 待完成 | 真实 OpenCode GUI 多轮验证 | 尚无固定 fixture | 当前完成了 Hook API 源码核对和 mock runtime smoke，不等于真实 provider E2E |
| 待完成 | 第一条真实 A/B 数据 | 按 TUI 手册生成，产物不得提交源码仓库 | 先做一个任务、`off` 对 `rails-lite`、镜像盲评 |
| 待完成 | 模型名到 tokenizer 的本地映射 | 未来 Profile/registry | 需要按实际使用的 Qwen/GLM/DeepSeek 型号逐个核对 |
| 待开始 | Shell 主体迁移到 Python | 见下方阶段 | 当前只有 assembler、eval tools 和少量配置脚本是 Python |

## 当前 Shell 责任地图

重构不是逐文件机械翻译。应按责任边界迁移：

| 责任 | 当前入口 | 迁移注意点 |
| --- | --- | --- |
| CLI 分发与 home 解析 | `bin/agent-rails`、`scripts/agent-paths.sh` | 必须保持软链感知，不能让父进程的旧 `AGENT_RAILS_HOME` 劫持 Release 安装 |
| Target Project / Profile | `scripts/agent-target-project.sh`、`scripts/agent-init-profile.sh` | 精确 Git root、worktree slug、Profile 优先级和 sibling repo 隔离 |
| 模型与 token | `scripts/agent-model-presets.sh`、`scripts/agent-estimate.sh`、`scripts/agent-context-assemble.py` | 一个模型表；可选依赖不能变成基础安装的强依赖 |
| Task Pack | `scripts/agent-context-pack.sh` | Git 证据、Memory、契约、验证、敏感输出、事务写入和 `0600` 权限 |
| Git / 验证 / 发布 | `scripts/agent-git-scope.sh`、`scripts/agent-check.sh`、`scripts/agent-publish-check.sh`、`scripts/agent-verify.sh` | ref 校验、部署基线、deleted path、只读/执行边界 |
| Adapter 生命周期 | `scripts/agent-adapter-workspace.sh`、`scripts/agent-adapter-content.sh`、`scripts/agent-*.sh` | generated marker、tracked path 保护、精确 inventory、local-ignore、dry-run |
| SessionStart | `hooks/agent-rails-session-start.sh` | stdout 协议、稳定短上下文、Claude/Codex 宿主格式 |
| 安装与升级 | `scripts/agent-update.sh`、`scripts/agent-release-install.sh`、`scripts/build-release.sh` | Git checkout 与 Release 两种模式、checksum、原子软链、回滚 |
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
│   ├── assemble.py
│   └── render.py
├── git/
│   └── scope.py
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

- [x] Shell 测试按 core/adapters/workflows/context 分组。
- [x] 外置 A/B 工具已有 Python 单测并由总 runner 调用。
- [x] Token assembler 已形成第一个 Python 实现岛。
- [ ] 为每个 CLI 命令保存关键 stdout/exit-code golden contract；不要保存机器绝对路径或秘密。

Gate：`bash tests/run.sh` 全绿，现有 92 个顶层回归入口不减少。

### Phase 1：第一个端到端 Python 切片

先迁移 `estimate`，不要先碰会修改 Adapter 或 Release 的命令：

1. 建立 `src/agent_rails` 和 Python CLI 启动器；
2. 迁移 Model Preset、tokenizer 选择和 estimate 渲染；
3. Shell dispatcher 暂时只把 `estimate` 转发给 Python；
4. 对 char、tiktoken、command、Hugging Face、stdin/file、已知/未知模型做 Bash/Python 对照；
5. Release archive smoke 必须证明无 `pip install` 仍能运行 char/command 模式。

Gate：公开输出和退出码兼容，没有新增强依赖，旧入口仍可回滚。

### Phase 2：迁移纯共享模块

- [ ] Paths 与 Release home 解析。
- [ ] Profile 和 Target Project Context。
- [ ] Model Preset 与 tokenizer registry。
- [ ] Git Scope 的只读 ref/path 快照。
- [ ] Sensitive Output Guard 的纯检测与证据策略。

Gate：每个 Python 模块先有单测，再让一个现有命令切换调用；禁止同时重写调用者和删除原实现。

### Phase 3：收拢 Context Pipeline

- [ ] 把 `agent-context-assemble.py` 移入 package，保留脚本兼容入口。
- [ ] 将 Task Pack collect/render 从 Shell 迁移到 Python。
- [ ] 保持 hard cap、类别下限、额度回流、UTF-8 行边界、事务写入和权限。
- [ ] OpenCode 常驻 tokenizer 服务改为导入同一 tokenizer/assembler 模块，不复制实现。

Gate：同一 fixture 的关键栏目、Git 范围、敏感输出和 token hard cap 等价；允许非语义空白差异，但要显式记录。

### Phase 4：迁移 Adapter 生命周期

- [ ] Workspace ownership 与 local-ignore。
- [ ] Claude、Codex、OpenCode install/doctor/uninstall。
- [ ] SessionStart 改为 Python 可执行入口，保持宿主 stdout 协议。
- [ ] OpenCode `.mjs` 仍由模板生成并做 JavaScript 语法检查。

Gate：tracked/user-authored 文件不被覆盖；install→doctor→reinstall→uninstall 的 fixture 全绿。

### Phase 5：迁移验证、发布和更新

- [ ] Check / Verify / Publish Check。
- [ ] Release build/install/update 与 checksum/rollback。
- [ ] Setup / Run 等 Facade 只编排共享模块，不复制规则。

Gate：发布范围、部署基线、secret scan、Git/Release 双安装模式和 archive smoke 全绿。

### Phase 6：切换入口并删除 Shell

- [ ] `bin/agent-rails` 改为 Python shebang 或最小平台启动器。
- [ ] 所有 Shell entrypoint 都已无调用者并通过等价性检查。
- [ ] 更新 Release 打包、安装说明和 `AGENTS.md` 常用命令。
- [ ] 删除 Shell 测试中的实现细节断言，保留公开行为与安全契约。

Gate：从全新 Release 安装到三个 Adapter 的 setup/doctor/verify smoke 通过后，才删除最后一层兼容 Shell。

## 推荐的下一条开发任务

只做 `estimate` Python tracer bullet，范围控制在：

- 新建 package/启动器；
- 迁移模型 preset 与 tokenizer 计数；
- 保持 `agent-rails estimate` 命令和输出兼容；
- 增加 Bash/Python 对照和 Release archive smoke；
- 不迁移 Pack、Adapter、Git Scope、Update；
- 不引入 Click/Typer/Pydantic 等强依赖，先使用标准库 `argparse` 和 `dataclasses`。

当前 macOS 系统 Python 为 3.9，因此第一阶段建议把最低版本定为 Python 3.9；若要提高版本，必须先更新安装前置条件和 Release smoke，而不是在实现中静默使用新语法。

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

