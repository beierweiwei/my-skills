# 编排机制指南（agent-crew）

> 本文件是 agent-crew 技能的编排机制正文：谁负责哪段、用什么运行时、产物写哪里、什么情况下找人拍板。
> 分层：**Herdr** 是运行时与通信总线；**工作流技能**是每个工位的流程纪律（可选上游技能）；**First Mate** 是编排副手层。
> 安装到目标项目后，运行期规则以项目 `AGENTS.md` 的「编排节」（由本技能生成）为准；本文是机制说明与背景语料。
>
> **占位符阅读约定**：本文中的 `{{doc_req}}` / `{{doc_accept}}` / `{{doc_context}}` / `{{data_dir}}` / `{{portfolio_dir}}` / `{{red_lines}}` 等指目标项目 `.planning/orchestration.json` 中对应配置项的值（文档名/路径/红线清单因项目而异）。项目内文件（AGENTS.md 编排节、角色文件、plan 骨架）已由 init 渲染为实际值；本技能目录内的语料保持占位符形态，一份语料服务所有项目。

## 1. 设计前提

单 Agent 的瓶颈是注意力，多 Agent 的瓶颈是任务拆解、上下文契约和验收，不是 Agent 数量。本体系只做三件事：

1. 把需求拆成有明确输入、输出、门禁的工位；
2. 用 Herdr 让 Agent 之间能派活、等结果、读输出；
3. 把正文和状态留在仓库文件里，不留在聊天记录里。

## 2. 分层

| 层 | 选择 | 职责 |
|---|---|---|
| 调度 | 总舵 Helm / 大副 First Mate | 拆任务、派活、收口 |
| 运行时 | Herdr | workspace/tab/pane、Agent 生命周期、worktree、远程 |
| 工作流 | 各工位 SOP 技能（可选上游技能，如盘问/拆单/实现+TDD/评审） | 单工位流程纪律 |
| 任务跟踪 | 本地 `.scratch/<topic>/issues/`（临时；可接远端 Issue 系统） | 执行工单，不是需求真源 |
| 状态 | `.planning/<task>/task_plan.md` | 阶段/决策/错误真源；派生看板为脚本生成的视图 |
| 正文 | 需求文档 `{{doc_req}}`（含『落地要点』） | 唯一需求真源 |
| 代码 | Git worktree + 分支/MR | 隔离、评审、合并 |

### 2.1 工具职责边界

| 工具 | 负责 | 不负责 |
|---|---|---|
| Herdr | workspace/tab/pane、Agent 启动与状态、`prompt/read/wait` 互调、detach/远程、worktree 拓扑 | 任务拆解、需求正文、业务验收、AOP 六维实现 |
| 编码 Agent CLI + subagent | 单会话执行、技能加载、工具调用、角色系统提示与工具权限 | 跨 tab 调度、验收拍板、需求正文 |
| 工作流技能 | 单工位流程纪律：盘问/规格/拆单/实现+TDD/评审 | 跨 Agent 调度、业务口径、AOP 切面实现 |
| 仓库文档 | 需求/进度/决策/术语真源；handoff 载体 | 运行时状态、进程管理 |
| 远端 Issue/MR（可选） | Issue/MR/CI/审计与合并 | Agent 运行时、编排调度 |

**与 AOP 六维的边界**：AOP 六维（目标与结果验证、可观测、可验证、I/O 契约+状态+幂等、可审计可干预、可进化）由需求文档、技能、平台切面和人工门共同承担，编排工具不替代其中任何一维。

- Herdr 只提供运行时状态、输出与人工干预入口，作为「可观测 / 可审计可干预」的证据来源；
- 业务目标的验证、I/O 契约、幂等、可进化仍必须写进需求文档（含『落地要点』），并由脚本、切面或人工门落实；
- 需求门检查 AOP 六维时，不能以「Herdr 已支持」作为答案。

## 3. 角色与技能

| 角色 | 输入 | 产出 | 边界 |
|---|---|---|---|
| Captain（人） | 会议/沟通输入、工单 | 口径/方案/验收确认 | 三道门签认；合并/推送授权 |
| 总舵 Helm | portfolio 与各流 plan | 拆解、派发副舵、聚合汇报 | 只编排，不执行；深读即派 |
| 大副 First Mate | portfolio/流 plan、工单、进度摘要 | 工单、派活、进度汇报 | 只编排，不执行；不参与需求盘问；不写正文 |
| Planner | 需求材料、`{{doc_context}}` | `{{doc_req}}`（含『落地要点』）、ADR、术语 | 必须与 Captain 实时对话 |
| Builder | 需求文档『落地要点』、工单 | worktree 分支、测试 | 测试红绿、类型检查 |
| Reviewer | diff、`{{doc_req}}` | 两轴评审结论 | 独立于 Builder；人审 |
| Scout（可选） | 调研问题、指定系统/材料 | 带来源调研报告 | 无；不写需求正文 |
| Runner（杂活） | First Mate 指派的临时小任务 | 小改动/脚本/检查结果 | 不写需求正文；不合并 |

规则：

- **Captain 是唯一的人**；总舵/大副是编排角色，不执行具体任务；Builder / Reviewer / Runner 是 crew，各自独立 tab。
- **副舵只编排**：First Mate / Orchestrator 不执行实现、调研、调试、数据处理或文档撰写；只拆任务、派船员、跟踪进度、向 Captain 汇报。可做 Herdr 编排操作与更新进度状态。
- **船员复用**：同一任务流/相关任务复用同一船员与 tab；任务无关或 Captain 指定才新开 tab/agent；临时小任务交给「杂活」tab 的 Runner。
- crew 角色固化：Qoder 在 `.qoder/agents/`，opencode 在 `.opencode/agent/`；Herdr 启动时用 `-- --agent builder|reviewer|scout|runner` 指定，不要每次手动发身份 prompt。
- **副手启动 worker/crew 时使用 YOLO 模式**：Qoder CLI 加 `--permission-mode bypass_permissions`（界面显示 YOLO）；opencode 加 `--auto`（自动批准未显式拒绝的权限）。两者都只在隔离 worktree + 明确工单边界内使用，不放宽合并、推送、清理与数据安全门禁；副舵自身不用 `--auto`。
- 交互式盘问类技能**必须 Captain 在场**，不允许后台自动跑完再交人。
- Builder 的实现自检不能替代独立 Reviewer 的评审；二者不互相替代。
- 同一时间同一个文件只有一个写者；写前读，写后更新对应 plan 的阶段 Status 与 Next Step。
- Agent 产出必须标来源（文件路径 / 行 / 命令）；识别不了写「待识别」。

## 4. Herdr 拓扑（tab-per-role）

> 日常按键与命令速查见 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

- **一条流一个 workspace；一个角色一个 tab**。不默认分屏：每个 tab 一个 pane、一个 Agent，用 tab 切换视角，用侧边栏或 `herdr agent list` 看全局状态。
- tab 命名 `<task>-<角色>`，Agent 同名（如 `demo-builder`）；临时小任务统一放「杂活」tab，Agent 名 `misc-runner`；总舵/大副按角色模板运行。总舵 agent 名＝orchestration.json 的 `helm.agentName`，默认派生 `helm-<项目名>`（herdr agent 名同机全局唯一，同机多项目各自唯一，不用裸名 `helm`）。
- 只有临时需要同屏对照时才在 tab 内分 pane，用完即关，不作为默认布局。
- **多条流并行**：一条流一个 workspace/worktree（`herdr worktree create`）；文档真源留在主 worktree，代码 worktree 只读需求文档与工单。
- 已验证命令：

```bash
# 新建角色 tab（默认当前 workspace；不抢焦点；从返回 JSON 取 tab 和 root pane id）
herdr tab create --cwd "$PWD" --label "demo-builder" --no-focus

# 在 tab 的 root pane 启动 Agent
herdr agent start demo-builder --kind qodercli --pane <root-pane-id> -- --agent builder --permission-mode bypass_permissions
herdr agent start misc-runner --kind qodercli --pane <杂活-root-pane-id> -- --agent runner --permission-mode bypass_permissions

# 派活（示例；需求文档路径按项目配置 {{doc_req}} 所在位置）
herdr agent prompt demo-builder "读 <需求文档路径> 并执行工单 <工单路径>" --wait --timeout 600000

# 读结果（优先 recent-unwrapped）
herdr agent read demo-builder --source recent-unwrapped --lines 200

# 等阻塞 / 查看状态
herdr agent wait demo-reviewer --until blocked --timeout 600000
herdr agent list
herdr tab list

# 已有 pane 迁到独立 tab / 重命名
herdr pane move <pane-id> --new-tab
herdr tab rename <tab-id> demo-reviewer
```

> 需要显式指定 workspace 时，先 `herdr pane current --current` 取 `workspace_id`；日常命令省略 `--workspace` 即作用于当前 workspace。

- 约束（来自 Herdr 官方 skill，可用 `herdr --skill` 查看）：先在 Herdr 内确认 `HERDR_ENV=1`；不要裸跑 `herdr` 做发现（会启动 TUI）；ID 从 JSON 响应解析；后台任务用 `--no-focus`。
- Agent 选择：默认 `qodercli`；`opencode` 作为备选——项目级角色在 `.opencode/agent/`，项目级技能在 `.opencode/skills/`；**角色按 tab/会话用 `--agent <role>` 选择，项目不设 `default_agent`**，避免所有 opencode 会话都变成副舵。
- `workspace create` 的语法以本机 `herdr workspace` 帮助为准，不固化未验证参数。

### 4.1 opencode 副舵

- 副舵角色按 tab/会话选择：主控 tab 启动时带 `--agent firstmate`；普通 opencode 会话仍是默认 agent。显式启动：

```bash
# agent 名同机唯一：一律带流/项目前缀（-- --agent 后是角色名 firstmate）
herdr agent start <流>-firstmate --kind opencode --pane <pane-id> -- --agent firstmate
```

- 副舵提示词在 `.opencode/agent/firstmate.md`，派活 SOP 在 `.opencode/skills/firstmate-dispatch/SKILL.md`；opencode 同时加载项目 `AGENTS.md` 与 `{{doc_context}}`（后者由项目配置 `instructions` 指定）。
- crew 用 opencode：仅当 `.opencode/agent/<role>.md` 已存在时可用，命令为 `herdr agent start <task>-builder --kind opencode --pane <pane-id> -- --agent builder`。本技能默认只带 `firstmate` 一份 opencode 角色，crew 默认继续用 Qoder 栈；需要时先补角色文件并重启会话。
- 与 Qoder 的差异：

| 项 | Qoder CLI | opencode |
|---|---|---|
| 角色加载 | `--agent <role>` | `--agent <role>`（按 tab/会话） |
| 权限放宽 | `--permission-mode bypass_permissions` | `--auto`（仅 worker/worktree） |
| 压缩 | `/compact` | `/compact` |
| 角色文件 | `.qoder/agents/*.md` | `.opencode/agent/*.md` |
| 技能目录 | `~/.qoder-cn/skills` | `~/.config/opencode/skills` + `.opencode/skills/` |
| 配置热载 | — | 不热载，改 agent/skill/config 后重启会话 |

- 排障：`opencode agent list` 看角色是否加载；`opencode debug skill` 看技能；`opencode debug config` 看最终模型与默认 agent。全局模型由用户级 opencode 配置决定，不在项目配置里改。
- opencode 首次加载项目配置会在 `.opencode/` 自动生成 `package.json` / `node_modules`（自带 `.gitignore` 已忽略），不要手工提交。
- 普通 opencode 会话用默认 agent；要副舵就显式 `opencode --agent firstmate`，不要为单个 tab 改项目默认 agent。

## 5. 端到端流程

### 5.1 需求入口

1. 原始诉求登记到项目约定的入口文档（只记原话、来源、日期）。
2. 为每个需求建立唯一落点与目录，原始材料就近存放；大批量数据不入库。

### 5.2 需求盘问（Planner + Captain）

- Planner 用盘问/建模类技能（可选上游技能）：盘问、更新 `{{doc_context}}`、必要时写 `docs/adr/`。
- 需求盘问由 **Captain + Planner** 完成；编排者不参与每轮盘问，只在单需求需求门通过后读取 `{{doc_req}}` 的结论、待确认项与进度摘要，再安排下一步。
- 需要批量读材料/做字段清单时，才开 Scout（只读调研），完成即关。Builder/Reviewer 在需求阶段不介入。
- 产出写入 `{{doc_req}}`：背景、**业务核心流程图**、范围、口径、验收、输入输出契约、澄清记录。
- 流程图必须标清触发起点、角色/系统泳道、关键动作与输入输出、人工与机器边界、异常/退回分支、闭环终点；节点与验收用例一一对应。
- **门禁 1（单需求需求门）**：Captain 确认业务核心流程图、口径、验收与输入输出契约；未确认不进入全局基线。

### 5.2.1 全局需求基线（所有需求之后）

- 各需求逐一完成 5.2 与单需求需求门；任何一个未确认，不进入全局基线。
- 梳理需求全集、依赖关系、共享数据/系统、优先级与边界；确认是否还有遗漏需求。
- 结论写进各 `{{doc_req}}` 的澄清记录与输入输出契约；基线条目只记对应 plan 的 Decisions/Phases，不另立需求正文。
- **门禁 2（全局需求基线门）**：Captain 确认需求全集与整体范围；未确认不启动实现。

### 5.3 基线材料与拆解（Planner / 编排者）

- 写跨需求基线材料：业务链、数据流、共享数据/系统/接口、共享 AOP 六维设计、实施顺序、跨需求依赖；不重复单需求细节。它是**全局需求基线门的材料**，不单独设门。
- 再在各 `{{doc_req}}` 写『落地要点』（≤1 页：谁在什么环境做什么、依赖什么能力、契约语义与责任边界、红线与边界、异常降级原则、切片顺序），引用基线材料，不重复共性设计；不写实现细节。
- tracker 中的 spec 只保留指向需求文档的链接，不复制正文。
- 只有全局需求基线门通过后，编排者才拆实现工单，写入 `.scratch/<topic>/issues/` 或远端 Issue 系统。

### 5.4 实现（Builder，全局需求基线门通过后）

- 在独立 worktree/分支实现；用 TDD，先与 Captain 确认测试 seam。
- 红 → 绿，一次一个切片；实现完成后自检评审。
- 提交到任务分支；更新对应 plan 的阶段状态。

### 5.5 独立评审（Reviewer）

- Reviewer 做两轴评审：Standards（`AGENTS.md` 的编码守则与上下文契约节；有代码后补 `CODING_STANDARDS.md`）+ Spec（`{{doc_req}}`（含『落地要点』）与工单）。
- 结论写回工单与进度；问题交 Builder 修，Reviewer 不直接改代码。
- **门禁 3（验收门）**：Captain 审 diff、真实数据回放与验收证据；必须先有 `{{doc_accept}}`（人工自测步骤 + 预期结果 + 证据位置 + 验收标准映射）。

### 5.6 验收与合并

- 三道门通过后，Captain 明确说「合并 / 清理」，大副（First Mate / Orchestrator）才可执行合并与 worktree 清理；Builder/Reviewer 不可合并。
- 合并前逐项确认：worktree 干净、测试通过、Reviewer 评审通过、验收证据齐全；缺一项停下来问 Captain。
- 合并：任务分支 → 主分支（`--no-ff`）；合并后跑测试/真实数据回放，记录合并 commit、测试结果与证据到需求/进度。
- 清理：确认已合并、无未提交、无未合并 commit；用 `herdr worktree remove --workspace <id>`；失败不 `--force`，先报告。
- 未合并/未提交 worktree 用 `python3 scripts/worktree-watch.py --interval 60` 监控；**实现阶段启用**，状态变化发 Herdr 通知。脚本只读，不自动合并/清理。
- 验收用真实数据回放；演示样例不算。完成后更新进度。

### 5.7 决策分流（当前 vs 将来）

- **当前决策/当前工作**：不决策就阻塞当前门禁、工单或验收。由大副直接与 Captain 对接，按三段式 + 推荐，一次最多 3 项。
- **将来决策/将来工作**：不阻塞当前阶段、留待后续任务或里程碑，或需要调研与多轮讨论。大副登记后新开 `future-<主题>` worker，由该 worker 直接与 Captain 多轮对齐；大副只做交接、跟踪、收口。
- 交接必含：议题与背景、为什么现在不决、已知事实与文档路径、需 Captain 拍板的问题、可选方案与影响、产出要求（结论/理由/影响/待同步清单）、停止条件。
- 讨论期间标「讨论中」，不得当已决；worker 收口后由大副核对并落清单（对应 plan 的 Decisions、`{{doc_req}}` 待澄清/澄清记录或 ADR），无结论标「待续」并保留 worker。
- 将来议题不占当前三段式名额；会阻塞当前推进的决策必须留在当前。

### 5.8 自动驾驶模式（First Mate）

> 目标：Captain 不在场时，副手按红线自主推进直到当日目标达成；只有不可逆/超授权事项最小范围暂停。Captain 回来先看 `{{portfolio_dir}}/progress.md` 的「自动驾驶里程」节。

**红线段（任何情况下不突破）**
1. **架构分层与项目红线**：{{red_lines}}
2. **用户交互**：图形化/一键优先；命令行只作隐藏排障入口；不跳出当前工作流；能自动化就自动化；错误提示必须给可执行的下一步。
3. **证据与验收**：真实数据验收，模拟/夹具只用于开发测试；不伪造证据、不把未跑通标绿；缺真实数据即标阻断。
4. **数据安全**：原始数据不全量读取，只读必要列/行；证据脱敏/哈希。

**代决范围**：可逆、可配置、不涉合同/合规/主数据/架构口径的事项，副手直接决定并标「代决（可覆盖）」。

**最小范围暂停（不可逆/超授权）**：删除、合并、推送、worktree 清理、花钱采购、客户数据对接、法律合规、主数据口径、门禁最终签认（除 Captain 另行授权）——仅暂停受影响项，其余独立任务继续；记录「待 Captain」并保留现场。

**上下文纪律（驾驶员清醒）**：副手自身 ctx ≥30% 即停止接新任务，完成当前切片；启动新副舵接替：①`herdr pane split <旧pane_id> --direction right` 在旧副舵所在 tab 内开新 pane；②起新副舵（`herdr agent start firstmate<N+1> ...`）；③新副舵读 `{{portfolio_dir}}/task_plan.md` + `progress.md` 尾部 + 各流 plan 接手并完成首条汇报后，执行 `herdr pane close <旧pane_id>` 关闭旧舵；④不新开 tab，tab 位置/编号不变。交接以 plan 文件为唯一凭据。不得在运行中压缩当前上下文后继续长跑。

**里程记录**：自动驾驶期间每个决定/暂停/跳过写入 `{{portfolio_dir}}/progress.md` 的「自动驾驶里程」节（时间、事项、决定、证据、待 Captain 项）；Captain 回来先看该节，再决定是否推翻。

### 5.9 编排效率原则（token 利用最大化）

1. **四段排活**：需求 → 方案 → 开发 → 验证；验收按 `{{doc_accept}}` 人工自测。上游未定不派下游；能并行的只限互不依赖项。
2. **只定契约语义，不定实现**：接口/事件/数据落点只写「谁提供、谁消费、失败如何表现、幂等键是什么」；选择器、函数名、字段级结构、UI 细节交给 Builder 现场决定。工单同理：给目标 + 边界 + 验收断言 + 证据要求，不指定实现路径。
3. **不重复劳动**：派单前先查现状（git status、plan、已有产物），只派未完成部分；已闭合项不重审，复核只针对改动段。
4. **合并同类项**：同一文件/同一主题的小修合并成一个工单、一次派完，不逐点派活、不反复往返。
5. **一轮评审覆盖多处**：评审一次给全量阻断，修复一次改完，复评只核改动段；不按单点反复开评审。
6. **复用固定工位**：派 Herdr 工位 worker（planner/builder/reviewer/runner/scout），同任务流同工位，不新建临时 Agent、不建无必要 tab。
7. **停工判断**：入口条件不满足（缺真实数据/账号/授权）时，只暂停受影响项，不空转重试。
8. **需求确认即开工**：需求门或 Captain 明确口径确认后**立即建工单、派实现**，不得以「排后/先做别的」延误；确需调整顺序须 Captain 同意并在 plan Decisions 标注。设计落文不等于完成，代码未动不得报「已安排」。
9. **需求完成度判断＝流程图节点法**：判断需求是否完成，副手先读 `{{doc_req}}` 的业务核心流程图与节点映射，逐节点核对：①**落地载体**；②**通路与断路**；③**最小路径**（旧路/并行冗余标出并清理）；④**最短≠最优**（必经人工门、证据留痕、可观测不得为「走通」而砍）；⑤**验收红线**。输出「已通路径 / 断路清单 / 冗余清单 / 最小路径结论 / 红线检查」五项，再决定收口或继续派活。
10. **问题升级门槛**：worker 汇报「做不到 / 环境限制 / 只能这样 / 建议降级」时，副手**不得直接转述给 Captain**。必须先退回补齐三样才允许上抛：①**证据**（复现步骤 + 截图/HTTP 状态/响应头/DOM/日志）；②**已试方案矩阵**（≥3 种替代路径及结果）；③**可行路径与覆盖率**。**未做排查的「不可行」视为未完成**；worker 以「沙箱/环境」下结论必须先在**实际运行环境内**复现取证。
11. **进度视图**：维护 `{{portfolio_dir}}/task_plan.md`（Goal / Next Step / Phases=活跃流 / Backlog / Deviation）+ 各流 plan；派生看板由 `scripts/plan-status.py --write` 生成，勿手改。向 Captain 汇报以看板为准：流表 + Backlog + 待 Captain。
12. **交接省 token**：交接只给文件路径 + 当前在跑清单，不复述正文；接收方读 portfolio 三文件 + 各流 plan 接手；`/compact` 摘要即交接，不另写 handoff。**发送方负责执行**，不把规则只写在接收方角色里。

## 6. 三道人工门（不可由 Agent 自批）

1. **单需求需求门**：该需求的业务核心流程图、口径、范围、验收标准、输入输出契约、准确性标准。需求门对『落地要点』只核：核心流程节点完整、契约语义与责任边界清楚、红线与边界写明、切片顺序可执行；**不核实现细节**。
2. **全局需求基线门**：全部需求已确认、需求全集无遗漏、依赖/优先级/边界明确；跨需求基线材料作为该门材料一并评审，不单独设门。
3. **验收门**：`{{doc_accept}}`、真实数据回放、复算、运行留痕、diff、证据。

> 验收门以 **AOP 六维最小验收**为标准：六维各至少一条可核验证据，缺一维即不通过；不要求穷举功能。

> 验收门同时按「用户交互原则」核验：面向用户的功能不得以手敲命令为主路径，按「普通用户不求助能否完成」判定。

## 7. 上下文契约（防散落）

- 每个 Agent 只读自己角色的输入文档；不把正文复制进 prompt，引用路径。
- 需求正文（含『落地要点』）只写 `{{doc_req}}`；技能生成的 spec、ADR 必须归并，不另立第二份。
- 工单只写任务与依赖，不写需求全文；工单可保留执行态 `Status:`，但对外权威只记对应 `.planning/<task>/task_plan.md`，冲突以 plan 为准。
- 同一文件单写者；写前先读；写完改对应 plan 的阶段 Status 并刷新 Next Step。
- 数据安全：`{{data_dir}}` 不入库、不全量读取；证据放需求目录或就近指定位置。

### 记忆策略

- **共享事实只存仓库**：`{{doc_context}}`、`{{doc_req}}`（含『落地要点』）、`.planning/` plan、`docs/adr/` 是所有角色的共同记忆；每个 crew 按需读取，不各自复制。
- **角色规则固化**：Builder / Reviewer 的职责写在 `.qoder/agents/*.md`（Qoder）或 `.opencode/agent/*.md`（opencode），属于静态规则，不是记忆。
- **不建 per-role 自动记忆**：自动记忆按项目/用户隔离，不按 Agent 隔离；多角色共写会互相污染。保持默认关闭。
- **角色私有经验**：优先写进角色定义的检查清单，或沉淀为 skill / 工单记录；不依赖不可见的会话记忆。
- **handoff 显式化**：Builder 的完成报告、Reviewer 的评审结论写入工单或进度，不靠记忆传递。
- 若启用自动记忆，只用于用户偏好或外部资料位置，项目事实仍以仓库文档为准。

### 上下文生命周期

- **一个 ticket 一个会话**：完成即收尾；换 ticket 时在下一轮会话开始前 `/compact`、`/clear` 或重建 tab，不把所有任务塞进同一窗口。
- **Worker 阈值（Builder / Reviewer / Scout）**：界面 `ctx` ≥ 30% 时不再接新切片，完成当前切片后停止；**运行中的会话不压缩**。下一轮会话开始前由大副统一 `/compact` 或重建 tab；压缩摘要即交接，不单独写 handoff；≥ 60% 立即停止接新切片，下一轮重建。
- **主控/Planner/大副阈值**：`ctx` ≥ 60% 停止接新任务；下一轮会话开始前 `/compact`、`/clear` 或重建；运行中不压缩。
- **handoff 模板**：完成内容、改动文件、测试结果、未决问题、下一步；写工单/评审记录/进度，不写聊天记录。
- **恢复顺序**：`{{doc_context}}` → 工单 → `{{doc_req}}` → 进度；不依赖旧聊天或自动记忆。
- **静态上下文精炼**：`AGENTS.md` / `{{doc_context}}` 只放稳定规则；需求文档按需读取，不整段粘贴进 prompt。
- **角色隔离**：一个 tab 一个角色；Reviewer 只看 diff + 需求文档，Builder 只看工单 + 需求文档。
- **大副上下文**：只保留当前批次的调度状态与文档链接，不复制需求正文；压缩摘要即交接，不另写 handoff。
- **异步状态监听**：后台运行 `python3 scripts/agent-watch.py --interval 15`（可加 `--workspace` / `--agent`），状态变化时发 Herdr 通知；不要前台 `herdr agent prompt --wait` / `agent wait` 长时间阻塞。`scripts/worktree-watch.py` 只监控 worktree 合并/提交状态，不监控 Agent 会话。状态去重文件默认 `.git/agent-watch.state`（全 worktree 共用一份，键为 `workspace/agent`，可用 `--state-file` 另指）；手工抽查用 `python3 scripts/agent-watch.py --once --no-notify --state-file /tmp/aw-check.state`，不要污染常驻监听的基线；通知只在 `HERDR_ENV=1`（Herdr 内）生效，Herdr 外只打印不通知。
- **长窗口不是免死金牌**：长上下文增加成本、延迟并稀释注意力；仍按 ticket 切片。
- Herdr 只保活进程，不保存对话上下文；重启或换机后靠文件恢复。

`/compact` 与 `/clear` 的取舍：`/compact` 保留摘要，适合继续同一任务；`/clear` 清空历史，适合切换 ticket；重建 tab 最彻底，角色会从角色文件重新加载。

## 8. 可选远端扩展

本节为可选扩展，默认 local-only 即可完整运行：

- **远端 Issue/MR**：接入 GitLab/GitHub 后，工单可迁到远端 Issue（`glab` / `gh` CLI），合并走 MR + CI 审计；创建 MR 由 worker 用 CLI 或人工完成。接入后同步更新项目 issue-tracker 约定。
- **Firstmate 调度层（可选上游工具）**：并行流 ≥3、需要自动监督/恢复时才考虑启用；启用需安装对应工具链并确认 Herdr backend protocol 满足要求。风险：上游成熟度与复杂度；先 local-only 跑通一轮再评估。

## 9. 并发与成本

- **需求探讨阶段不启用 crew/worktree**；全局需求基线门通过后，才启动 Builder/Reviewer 和 worktree。
- 实现阶段起步 2 个 crew（Builder + Reviewer）；Planner 与 Captain 同会话。
- Planner/Reviewer 用较强模型；Builder 可用便宜模型。
- 同时最多 2–3 个 worktree；流之间不共用 tab 或 Agent。
- 每个 crew 独立上下文预算；阈值、压缩与 handoff 见 §7「上下文生命周期」。
- 当并行收益低于验收能力时，不加 Agent。
