---
name: agent-crew
description: 多 agent 编排体系一键启用与日常编排（Herdr 运行时 + 角色船员 + plan 工作记忆 + 派生看板）。Use when 需要在目标项目启用多 agent 协作编排、初始化角色与总舵、拆任务派活收口、维护 portfolio 多流 plan、生成进度看板，或搭建 Captain/总舵/大副/船员分工体系；触发词：编排、总舵、大副、副舵、船员、crew、herdr、派活、看板、portfolio。
---

# agent-crew：多 Agent 编排体系

把「一人 + 多 Agent 船员」的协作编排装进目标项目：角色模板（Captain 人 / 总舵 Helm / 大副 First Mate / Builder / Reviewer / Planner / Runner / Scout）、Herdr 运行时封装脚本、plan-with-files 工作记忆与派生看板。装完后的运行规则全部落在项目内（`AGENTS.md` 编排节 + 角色文件 + plan 骨架），本技能目录只提供语料与脚本。

**术语与机制正文**：[references/orchestration-guide.md](references/orchestration-guide.md)（角色/分层/拓扑/端到端流程/三道门/上下文契约）；命令速查：[references/herdr-cheatsheet.md](references/herdr-cheatsheet.md)；plan 字段与看板：[references/plan-discipline.md](references/plan-discipline.md)。

## 何时用 / 不适用

- **用**：一个仓库里要多 Agent 分工（实现/评审/调研/杂活）+ 人工门 + 进度看板；新项目从零启用，或既有项目追加编排体系。
- **不用**：单 Agent 能完成的简单任务；只想要个人工作流（无多角色分工）的场景。

## 启用（≤3 步）

| 步 | 谁操作 | 做什么 | 成功判据 |
|---|---|---|---|
| 1 | Captain（一次性） | 安装 my-skills：终端跑 `bash scripts/update-all.sh`（已装则跳过） | 本技能 SKILL.md 出现在已安装技能目录 |
| 2 | Captain → **对主 agent 说一句话**：「在本项目启用 agent-crew 编排」 | agent 按本文件执行 `python3 <技能目录>/scripts/init.py`——零交互默认直通（agent 执行时 stdin 非 tty，问答不触发）；要定制参数，先写/改 `.planning/orchestration.json` 再跑 init（渲染结果变化时按冲突处理：人工比对后 `--force`） | init 输出零 ERROR；报告逐产物标注「新建 / 已存在跳过 / 冲突」 |
| 3 | Captain：「启动总舵」 | agent 执行 `bash <项目>/scripts/start-helm.sh` | Herdr 出现总舵 tab，总舵 agent（agent 名＝orchestration.json 的 `helm.agentName`，默认派生 `helm-<项目名>`）就绪并发出首条汇报（状态 3 行 + 待决策 ≤3） |

此后总舵/大副的全部操作走角色文件内封装的 SOP 与脚本，Captain 只对话。

**自举说明**：本技能不带 `setup.sh`——init 的作用域是目标项目仓，只有在项目内由 agent 按 SKILL.md 执行才能完成检测与渲染（与 experience-kb-init / project-landing-init 同模式）。

## init 做什么（行为契约）

- **只读检测**：git 仓、既有 `.qoder/agents/`、`.opencode/`、`scripts/plan-status.py`、`.planning/`（含 `*portfolio*` 目录）、`AGENTS.md`、术语文档（默认 `CONTEXT.md`，文档名可配置）、`docs/`、planning-with-files 是否安装、python3 ≥3.10。
- **生成（只写清单内路径，清单外零写入）**：
  1. `.planning/orchestration.json`（单一参数载体：文档名/看板落点/portfolio 目录/决策清单/红线等，全部有默认值）；
  2. 角色模板渲染：`.qoder/agents/` 7 份、`.opencode/agent/firstmate.md`、`.opencode/skills/firstmate-dispatch/`；
  3. 脚本落项目 `scripts/`（start-agent / start-helm / agent-watch / worktree-watch / herdr-events / plan-status）；
  4. portfolio 目录三文件骨架（`task_plan.md` / `findings.md` / `progress.md`；目录见配置 `portfolioDir`，默认 `.planning/portfolio`）；
  5. `AGENTS.md` 编排节（标记节定界：无则追加、有则替换节内内容，原内容不动）；
  6. 术语文档不存在才生成骨架。
- **幂等**：重复执行零副作用——一致则跳过；不一致的已存在产物**默认不覆盖**，列冲突清单给双方路径，`--force` 才覆盖（仅限本技能产物路径）。
- **交互边界**：agent 执行（stdin 非 tty）零交互——已有 `*portfolio*` 目录默认沿用、关键产物冲突默认不覆盖并列冲突清单；仅人工终端（tty）跑 init 才出现沿用/新建问答。

## 项目参数（orchestration.json）

init 后所有项目差异集中在 `<项目>/.planning/orchestration.json`：项目名、portfolio 目录、看板落点/标题、需求/验收/术语文档名（无需求文档体系的项目可映射为其既有 PRD/issue 路径）、必须找 Captain 的决策清单、项目红线、原始数据目录、总舵 agent 名（`helm.agentName`，默认派生 `helm-<项目名>`，同机多项目各自唯一）、总舵 tab 标签与期望模型。改配置后重跑 init 重渲染（或手改已渲染文件）。

## 日常编排导航

| 要做什么 | 谁做 | 去哪里 |
|---|---|---|
| 判定请求去向、拆流、派副舵、聚合汇报 | 总舵 Helm | 角色文件 `.qoder/agents/helm.md` |
| 拆任务、派活、监听、收口、三道门 | 大副 First Mate | `.opencode/skills/firstmate-dispatch/SKILL.md`（SOP 全文） |
| 实现/评审/写文档/调研/杂活 | 各 crew | 各角色文件（Qoder `.qoder/agents/`，opencode `.opencode/agent/`） |
| 记进度、记决策、记错误 | 各 plan owner | portfolio 三文件 + 各流 plan（目录见配置 `portfolioDir`） |
| 看全局进度 | Captain/编排者 | 派生看板（`python3 scripts/plan-status.py --write` 生成，勿手改） |
| 排查 Herdr 操作问题 | 任何人 | [references/herdr-cheatsheet.md](references/herdr-cheatsheet.md) |
| 编排漏洞/流程问题上报 | 任何角色 | portfolio 目录下 `feedback.md`（场景/问题/影响/建议/状态） |

## 环境假设与已知边界

| 异常/边界 | 检测 | 提示与下一步 |
|---|---|---|
| herdr 未安装 | `command -v herdr` 失败（init 不阻断，start-* 阻断） | 「未找到 herdr。请先安装 Herdr（教程：references/herdr-cheatsheet.md §开局），装好后重跑『启动总舵』。」 |
| herdr server 未运行 | `herdr status --json` 非 running | 「server 未运行：在终端执行 `herdr` 打开 TUI（server 随 TUI 启动），再对我说『启动总舵』。」 |
| 同机多项目 agent 名撞名 | herdr agent 名同机全局唯一；总舵不占裸名 `helm`，取 `helm.agentName`（默认派生 `helm-<项目名>`） | 「总舵 agent 名 `<名字>` 已被另一项目占用。二选一：关闭旧的总舵 agent 后重跑，或改 `.planning/orchestration.json` 的 `helm.agentName` 后重跑。」 |
| qodercli 缺失/不在 PATH | `command -v qodercli` 失败 | 「qodercli 不可用：确认安装与 PATH（`qodercli --version`），或先用 opencode 栈（`.opencode/agent/` 已带 firstmate）。」 |
| 目标仓非 git | `git rev-parse` 失败 | init 继续安装但告警：worktree-watch/agent-watch 状态文件依赖 `.git/`，监听不可用；建议 `git init` 后重跑 init 补齐；plan-status 不受影响。 |
| 已有同名角色文件（内容不同） | init 冲突检测 | 「未覆盖。请人工比对后二选一：删除旧文件重跑 init，或加 `--force` 覆盖（仅限本技能产物路径）。」 |
| AGENTS.md 已有内容 | init 检测 | 标记节追加/替换，原内容不动；报告注明追加位置。 |
| planning-with-files 未装 | 检测技能脚本 | init 仍生成 portfolio 骨架；提示按 my-skills 流程补装该上游技能；不影响启动。 |
| python3 < 3.10 | 版本检测 | 「版本过低：agent-watch/herdr-events/plan-status 需 ≥3.10，升级后重跑 init。」 |
| opencode 栈 | `.opencode/` 存在 | 可用；**helm 仅 Qoder 版**（见下）；改 agent/skill/config 后需重启会话（不热重载）。 |
| 非 qoder/opencode 运行时 | 环境检测 | 当前版本支持 qodercli + opencode 双栈，其他运行时待扩展；不阻断文件安装。 |
| Windows | 平台检测 | Herdr Windows 为 beta；建议 Linux/macOS 跑编排。 |
| 既有项目结构差异（无 docs/ 等） | init 检测 | 缺目录自动创建；文档名占位符按配置渲染，可在配置里把「需求文档」映射为既有 PRD/issue 路径。 |
| 批量收尾（关大量 tab/pane） | — | `herdr pane list` 按 cwd 过滤后逐个 `herdr pane close`，或直接关闭 workspace。 |
| worktree-watch / agent-watch | — | 依赖 git（状态文件在 `.git/` 下）；通知只在 `HERDR_ENV=1`（Herdr 内）生效。 |
| herdr-events.py | — | **可选增强件**（socket 事件监听），编排 SOP 未强依赖；主 pane 排除用 `--main-pane` 指定，默认不排除。 |
| ctx 用量查看 | — | 依赖 qodercli 会话文件布局（状态栏 `ctx N%` 或 `herdr pane process-info`）；其他 CLI 待扩展。 |

## 双栈说明（Qoder / opencode）

- **Qoder 栈（完整）**：角色 7 份（helm/firstmate/builder/reviewer/planner/runner/scout）在 `.qoder/agents/`；worker 启动用 `--permission-mode bypass_permissions`（封装脚本 `scripts/start-agent.sh`）。
- **opencode 栈（部分）**：默认只带 `firstmate` 角色与 firstmate-dispatch SOP；**总舵 helm 仅 Qoder 版**——opencode 栈下总舵暂缺，由 firstmate 承接主控。crew 默认继续用 Qoder；需要 opencode crew 时先补 `.opencode/agent/<role>.md` 并重启会话。
- opencode 的配置、agent、skill **不热重载**；改动后必须重启会话。

## 提交与发布边界

- 本技能是项目基础设施安装器；init 只写上方清单内路径，清单外零写入。
- 原始数据不全量读取；init 不读取、不迁移任何业务数据。
- 合并/推送/worktree 清理/花钱采购/合规/主数据口径等属 Captain 决策（清单见项目 `orchestration.json` 的 `captainDecisions`），Agent 不得自批。
