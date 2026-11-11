# 编排机制指南（agent-crew）

> 本文件是 agent-crew 的**机制解释**：为什么这样分层、这样分角色、这样设门，以及 Herdr 与 plan-with-files 各自承担什么。
> 它不写可执行规则、操作步骤与命令清单。技能侧的编号规则正文在 [crew-rules.md](crew-rules.md)，验收与呈报正文在 [crew-acceptance.md](crew-acceptance.md)，plan 字段与看板在 [plan-discipline.md](plan-discipline.md)，Herdr 命令速查在 [herdr-cheatsheet.md](herdr-cheatsheet.md)；项目内运行期规则以项目 `AGENTS.md` 编排节与各角色文件为准。
>
> **占位符阅读约定**：本文中的 `{{doc_req}}` / `{{doc_accept}}` / `{{doc_context}}` / `{{data_dir}}` / `{{portfolio_dir}}` / `{{red_lines}}` 等指目标项目 `.planning/orchestration.json` 中对应配置项的值（文档名/路径/红线清单因项目而异）。项目内文件（AGENTS.md 编排节、角色文件、plan 骨架）已由 init 渲染为实际值；本技能目录内的语料保持占位符形态，一份语料服务所有项目。

## 0. 指针地图

| 要查什么 | 去哪里 |
|---|---|
| 派单 | crew-rules.md **D1–D6** |
| 回执 | crew-rules.md **R1–R6** |
| 工位 | crew-rules.md **W1–W5** |
| 占用与并发 | crew-rules.md **O1–O3、P1–P3** |
| 收口 | crew-rules.md **F1–F16** |
| 值守 | crew-rules.md **G1–G6** |
| 状态机与放行条件 | crew-rules.md **S1–S4** |
| 边界红线 | crew-rules.md **X1–X5** |
| 逐节点抽验 / 关键交互物自验 / 完成声明 / AOP 六维 | crew-acceptance.md **A1–A4** |
| 向 Captain 汇报 | crew-acceptance.md **A5** |
| plan 三文件字段、看板派生、归档 | [plan-discipline.md](plan-discipline.md) |
| Herdr 命令与按键 | [herdr-cheatsheet.md](herdr-cheatsheet.md) |
| Agent 生命周期/工位变更 | `scripts/dispatch.py`（唯一入口）；`start-*.sh` 只是薄包装 |
| 安装 / 升级 / 回滚 / 体检 | `scripts/init.py`、`scripts/upgrade.py`、`scripts/doctor.py` |
| 技能基类与项目角色继承 | `templates/agents/<runtime>/<role>.md` + 项目角色 `extends: skill:<role>`（append-only 组合） |
| 项目运行期规则 | 项目 `AGENTS.md` 编排节 + 各角色文件 |

## 1. 设计前提

单 Agent 的瓶颈是注意力，多 Agent 的瓶颈是任务拆解、上下文契约和验收，不是 Agent 数量。本体系只做三件事：

1. 把需求拆成有明确输入、输出、门禁的工位；
2. 用 Herdr 让 Agent 之间能派活、等结果、读输出；
3. 把正文和状态留在仓库文件里，不留在聊天记录里。

设计优先级是**稳定性 > 功能完整 > token 节省**：状态可从文件重建、动作可重试、检查交给脚本、正常路径零输出。

底层原因：Agent 之间没有共同记忆，只有文件与运行时通道。任何依赖「双方都记得」的协作，都会在上下文压缩、重启或换人时断掉。因此规则写成编号正文、状态写进 plan、回执写成文件——这是整个体系的地基。

## 2. 分层与真源

### 2.1 分层

| 层 | 选择 | 职责 |
|---|---|---|
| 调度 | 总舵 Helm / 大副 First Mate | 拆任务、派活、收口 |
| 运行时 | Herdr | workspace/tab/pane、Agent 生命周期、worktree、远程 |
| 工单 | `.scratch/<topic>/issues/`（临时；可接远端 Issue 系统） | 执行层任务卡，不是状态真源 |
| 状态 | `.planning/<task>/` 三文件（task_plan / findings / progress） | 任务状态唯一真源；看板只是派生视图 |
| 路由 | `<git-common-dir>/crew/registry.json` | 只存 Herdr 不知道的路由（非 Herdr 运行时的收件箱/会话），默认不创建 |
| 回执 | `<git-common-dir>/crew/inbox/` | crew → 编排者的文件回执；是触发器，不是状态 |
| 组合产物 | `<git-common-dir>/crew/compose/<run-id>/` | dispatch 启动时一次性产物，可丢弃；不写项目发现目录 |
| 正文 | 需求文档 `{{doc_req}}`（含『落地要点』） | 唯一需求真源 |
| 技能正文 | 技能源 `skillRoot/references/**`、`skillRoot/SKILL.md` | 通用规则/验收/机制/plan 纪律唯一落点；项目只留指针，不复制 |
| 代码 | Git worktree + 分支/MR | 隔离、评审、合并 |

为什么这样分：运行时信息（pane、进程、状态）变化快、无历史价值，交给 Herdr；任务信息（目标、阶段、决策、错误）要跨会话、跨重启存活，必须落仓库文件；回执是瞬时事件，落文件即可触发，不承担状态。任何一方都不是第二份真源：回执与 plan 冲突时以 plan 为准（R4），registry 只是 Herdr 缺失路由的瘦表。

状态机按 S1–S4 派生与放行：状态从工单、回执、占用、registry、plan 推导，事件日志只作审计与恢复，plan 仍是任务状态真源。

### 2.2 工具职责边界

| 工具 | 负责 | 不负责 |
|---|---|---|
| Herdr | workspace/tab/pane、Agent 启动与状态、`prompt/read/wait` 互调、detach/远程、worktree 拓扑 | 任务拆解、需求正文、业务验收、AOP 六维实现 |
| 编码 Agent CLI + subagent | 单会话执行、技能加载、工具调用、角色系统提示与工具权限 | 跨 tab 调度、验收拍板、需求正文 |
| 工作流技能 | 单工位流程纪律：盘问/规格/拆单/实现+TDD/评审 | 跨 Agent 调度、业务口径、AOP 切面实现 |
| dispatch.py | 唯一派单与生命周期入口：组合、启动、提示、关闭、状态、工位；agent-log | 需求正文、验收拍板、合并/推送 |
| init / upgrade / doctor | 安装与 bootstrap、受管脚本升级与回滚、只读体检与证据 | 项目角色正文、项目决策、自动修复 |
| 仓库文档 | 需求/进度/决策/术语真源；handoff 载体 | 运行时状态、进程管理 |
| 远端 Issue/MR（可选） | Issue/MR/CI/审计与合并 | Agent 运行时、编排调度 |

**与 AOP 六维的边界**：AOP 六维（目标与结果验证、可观测、可验证、I/O 契约+状态+幂等、可审计可干预、可进化）由需求文档、技能、平台切面和人工门共同承担，编排工具不替代其中任何一维。Herdr 只提供运行时状态、输出与人工干预入口，可作为「可观测 / 可审计可干预」的证据来源；业务目标的验证、I/O 契约、幂等、可进化仍必须写进需求文档（含『落地要点』），并由脚本、切面或人工门落实。所以需求门检查 AOP 六维时，不能以「Herdr 已支持」作为答案。

## 3. 角色

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

- **Captain 是唯一的人**；总舵/大副是编排角色，不执行具体任务；Builder / Reviewer / Runner / Scout 是 crew，各自独立 tab。
- **只编排不下场**：副舵不执行实现、调研、调试、数据处理或文档撰写。原因很实际：编排者的价值在全局进度与判断；一旦下场，既失去视角，又与成果产生利益重合。
- **角色固化**：crew 身份写在角色文件里（Qoder `.qoder/agents/`，opencode `.opencode/agent/`），由 `dispatch.py start` 加载，不靠每次手发身份 prompt。角色文件是项目自有 overlay（`extends: skill:<role>`、`managed: false`）；动作基类与权限在技能源 `templates/agents/<runtime>/<role>.md`，启动时 append-only 组合、权限只取基类。worker 的可执行边界（单写者、最小改动、AMEND、发现清单）见 D3–D6 与 O、P 编号。
- **核心角色能力**：helm/firstmate/builder/planner/reviewer/runner/scout 七个核心角色的 capabilities/gates/lifecycle 由技能基类与 dispatch 默认表统一约束；项目只能追加 gate，不能删改核心门。
- **船员复用**：同一任务流复用同一船员与 tab；任务无关或 Captain 指定才新开。复用省下的是重新读文件、重建上下文与交接成本。
- **评审独立**：Builder 自检不能替代独立 Reviewer 的评审——自评有确认偏差，独立评审才提供第二视角。
- **交互式盘问必须 Captain 在场**：后台自动跑完再交人，盘问就退化成填表。

## 4. Herdr 拓扑（tab-per-role）

> 命令与按键见 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

- **一条流一个 workspace；一个角色一个 tab；默认一个 tab 一个 pane、一个 Agent**。用 tab 切换视角，用侧边栏或 `herdr agent list` 看全局状态。
- 为什么 tab-per-role：tab 是上下文与身份的边界——读什么、写什么、权限多大，都能从所属 tab 与角色判断；常驻分屏会让角色与窗格错位，交接和回收时容易误关。只有临时同屏对照才在 tab 内分 pane，用完即关。
- 命名：tab 与 Agent 用 `<task>-<角色>`；总舵 agent 名由 `.planning/orchestration.json` 的 `helm.agentName` 派生（同机全局唯一）。工位标签、容量与回收见 W1–W5。
- **多条流并行**：一条流一个 workspace/worktree；文档真源留在主 worktree，代码 worktree 只读需求文档与工单。这样代码隔离与文档单一真源同时成立。
- **工位生命周期**：新建、复用、容量、标签、回收窗与巡检属于 W1–W5；回收的机制解释见 §5.10。

### 4.1 双栈（Qoder / opencode）

- **两栈都完整**：技能源 `templates/agents/qoder/` 与 `templates/agents/opencode/` 各 7 个核心基类；opencode 核心角色全部 `mode: primary`，项目侧由 init 生成同名 stub。**项目不设默认 agent**，主控 tab 显式指定副舵角色。
- 角色文件位置：Qoder `.qoder/agents/*.md`，opencode `.opencode/agent/*.md`；技能目录不同。opencode 配置/角色/技能**不热重载**，改动后重启会话。
- **唯一入口**：两栈的启动/提示/关闭/工位变更都走 `python3 scripts/dispatch.py`；Qoder/opencode 均使用 init/upgrade 安装的 managed 组合基础代理（`crew-<role>`）启动。禁止直写 `herdr agent start/prompt/close` 或 runtime `--agent` 直启（`start-*.sh` 走 dispatcher 是唯一例外）。命令与排障见 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

## 5. 端到端流程（机制）

五个阶段，三道人工门插在中间。每个阶段有唯一 owner 与唯一产物落点；门只看产物，不看过程。派单、收口与状态放行的可执行规则分别见 D1–D6、F1–F16、S1–S4。

### 5.1 需求入口

原始诉求先登记到项目约定的入口文档（只记原话、来源、日期），每个需求只有一个落点、一套原始材料。入口一旦分裂，后面所有门都会与真源对不上。

### 5.2 需求盘问（Planner + Captain）

- 盘问由 Captain 与 Planner 完成，编排者不参与每轮。原因：需求口径只能由人拍板；编排者提前介入，会把「能不能做」混进「要什么」，也会提前耗尽编排注意力。
- 产物写进 `{{doc_req}}`：背景、业务核心流程图、范围、口径、验收、输入输出契约、澄清记录；流程图节点与验收用例一一对应。盘问/建模技能是可选上游技能。
- Scout 只在批量读料时开，只读调研，完成即关；Builder/Reviewer 在需求阶段不介入。
- **门禁 1（单需求需求门）** 的意义见 §6。

### 5.2.1 全局需求基线

- 所有需求逐一过单需求门后，才梳理全集、依赖、共享数据/系统、优先级与边界。原因：单需求门只能保证「这一份说得清」，全局门回答的是「合在一起有没有漏、有没有撞」。
- 基线材料写跨需求视图（业务链、数据流、共享数据/系统/接口、共享 AOP 设计、实施顺序），单需求细节不重复。
- **门禁 2（全局需求基线门）** 的意义见 §6。

### 5.3 基线材料与拆解

- 门禁 2 通过后，编排者才拆实现工单；工单落 `.scratch/<topic>/issues/` 或远端 Issue，只引需求路径，不复制正文。
- 『落地要点』（≤1 页）是需求与实现之间的桥：谁在什么环境做什么、依赖什么能力、契约语义与责任边界、红线与边界、异常降级原则、切片顺序；不写实现细节。
- 工单只给边界不给做法。原因：实现路径依赖现场代码与环境，过早写死会让 worker 要么盲从、要么偷偷偏离；让 worker 现场决定并回写证据，责任才清楚。字段与边界规则见 D1–D6。

### 5.4 实现（Builder）

- Builder 由 `dispatch.py start --role builder` 启动（技能基类 + 项目 overlay 组合），在独立 worktree/分支实现，按该工位的工作流技能（TDD 等）执行；改动范围与扩围边界按 D4–D5 执行。
- 单写者与占用（O1–O3、P1）解决同一个问题：多个 Agent 同时写同一文件时，谁的动作算数。没有占用声明，冲突事后无法判定。
- 提交到任务分支，并按 F3 更新对应 plan 的阶段状态与证据指针。

### 5.5 独立评审（Reviewer）

- 两轴评审：Standards（`AGENTS.md` 的编码守则与上下文契约节）+ Spec（`{{doc_req}}`（含『落地要点』）与工单）。
- Reviewer 独立于 Builder，只读 diff 与需求/工单，不直接改代码；Builder 自检不能替代评审。
- 越界 diff 的处理，以及收口方只看结果与证据、不看实现细节的边界，见 F5。
- **门禁 3（验收门）** 的意义见 §6；抽验、自验、完成声明与六维证据的要求见 crew-acceptance.md A1–A4，本文不复述。

### 5.6 验收与合并

- 三道门通过且 Captain 明确说「合并 / 清理」后，才可合并与清理 worktree；合并、推送、清理属 Captain 授权动作（X1–X2）。
- 合并前核对的前提可以概括成一句：**要合的东西必须就是被验过的东西**（worktree 干净、测试通过、评审通过、验收证据齐全）。验收以真实数据回放，演示样例不算。
- 合并后把 commit、测试结果与证据记回需求/进度；未合并/未提交 worktree 由 worktree-watch 监控——它只是提醒，不自动合并或清理。

### 5.7 决策分流（当前 vs 将来）

- 判据只有一条：不决策会不会阻塞当前门禁、工单或验收。会阻塞的必须当前解决；不阻塞的登记后交给 `future-<主题>` worker 与 Captain 多轮对齐，不占当前决策名额。
- 为什么分流：把「现在必须拍板」和「以后可以想清楚」混在一起，Captain 的注意力会被非关键项淹没，当前进度也会被远期议题拖住。
- 呈报格式、份数上限与代决范围见项目 `AGENTS.md` 编排节；决策落点见 [plan-discipline.md](plan-discipline.md)。

### 5.8 自动驾驶模式

- 目的：Captain 不在场时，副手按红线自主推进到当日目标；只有不可逆或超授权事项才最小范围暂停，其余独立任务继续。
- 红线不因自动驾驶放宽：项目红线、用户交互、证据与验收、数据安全（X1–X5 与 `AGENTS.md` 编排节）。
- 每个决定/暂停/跳过写「自动驾驶里程」，Captain 回来可复查、可推翻。
- 上下文纪律：副手到达阈值时由新副舵接替，交接以 plan 文件为唯一凭据。阈值、换舵步骤与启动命令见 `AGENTS.md` 编排节与 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

### 5.9 编排效率的机制

体系里最贵的资源不是 token，而是注意力与往返：每次派活、每次评审、每次交接都在消耗 Captain 与编排者的上下文。效率原则（`AGENTS.md` 编排节「编排效率原则」）都是围绕这一点做的经济选择——减少返工、减少确认往返、减少重复重建上下文。具体条目、问题升级门槛与需求完成度判定（流程图节点法）见 `AGENTS.md` 编排节，本文不复述。

### 5.10 工位回收的机制

- 工位是消耗品，不是资产：残留的 Agent 占进程与注意力，过期标签会让编排者误判「还在跑」。回收窗口因此存在。
- 回收按工位类型与「同资产是否还有下一件」判断，工位处置必须随收口回执一起给出（F4）——没有这一项，就无法判断该不该关。
- 判定窗口、标签、容量与巡检口径见 W1–W5；巡检工具只做报告与安全关闭，命令用法见 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

### 5.11 发布后重启与可交接检查

- **触发**：技能版本发布/升级后（用户目录刷新、项目 `upgrade`/`init` 之后）。发布不是 push 完就结束，升级后的运行时必须验证确实载入了新代码与新模板。
- **① 先无锁取证、再启动 guard（新代码）**：guard 是独立长驻进程，升级不会热更新已运行实例。找旧进程并停（记录旧 pid/cmdline）→ **启动长驻实例前，先在无 guard 持锁时跑 `python3 scripts/crew-guard.py --once --json` 取证**（stdout 必须为非空 JSON，空输出不算通过）→ 启动 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` → 验证 `<crew-dir>/guard.lock` / pid / cmdline、新代码生效探针（如 wake 写 `generated_at:`、`--help` 出现新子命令）；长驻实例持锁时第二实例静默 rc=0、不产生任何输出，**不得作为证据**；任一失败即停，不得报发布完成。
- **② 长驻角色**：compose 在启动时快照（agent 启动时组合基类与项目角色），升级不会热更新已运行会话——helm 需换舵（新 tab 新舵、首报后关旧）；firstmate 需重建/重派；one-shot worker 下次启动自动取新模板。发布回执必须写明该事实与处置。
- **③ 可交接性实测**：真实 pane ctx 读取（`herdr pane read <helm pane> --lines 80` 可见 `ctx Nk/1M`）、① 无锁窗口采集的 `python3 scripts/crew-guard.py --once --json`（stdout 为非空 JSON；长驻持锁时静默 rc=0 不算）、继任组合只读校验 `python3 scripts/dispatch.py compose --role helm --json` rc=0；证据落 `.planning/<task>/evidence/release/`。
- **④ 红线**：上述任一未完成或无证据，不得报发布收口。

## 6. 三道人工门（为什么不可自批）

三道门各回答一个不同的问题，顺序不能省：

| 门 | 回答的问题 | 看的产物 |
|---|---|---|
| 1 单需求需求门 | 这一份需求说清楚了吗？ | 业务核心流程图、口径、范围、验收、输入输出契约 |
| 2 全局需求基线门 | 合在一起完整吗，有没有漏、有没有撞？ | 需求全集、依赖、优先级、边界、跨需求基线材料 |
| 3 验收门 | 做出来的东西真的能用吗？ | 逐节点抽验、关键交互物自验、完成声明、AOP 六维证据 |

- **不可由 Agent 自批**：Agent 产出是概率性的，且天然倾向「报告完成」；门是外部校验点，必须有人承担责任。
- **只看产物与证据**：没有证据的「做完了」不算数；门不因解释、态度或过程辛苦而放宽。
- 验收门的执行标准见 [crew-acceptance.md](crew-acceptance.md)：A1 逐节点抽验、A2 关键交互物自验、A3 完成声明四要素、A4 AOP 六维最小验收；验收文档 `{{doc_accept}}` 与呈报包按 A3。汇报按 A5。
- 验收门同时按「用户交互原则」核验：面向用户的功能不得以手敲命令为主路径，按「普通用户不求助能否完成」判定。

## 7. 上下文契约（防散落）

### 7.1 防散落

- 每个 Agent 只读自己角色的输入文档；引用路径，不把正文复制进 prompt。
- 一份事实只有一个落点：需求正文只写 `{{doc_req}}`；任务状态只写 `.planning/<task>/` 三文件；运行时路由与回执只写 `.git/crew/`。
- 工单只写任务与依赖，不写需求全文；权威状态与冲突裁决见 §2.1。
- 单写者、写前读、写后更新 plan：见 P1、O1、F3。
- 数据安全：`{{data_dir}}` 不入库、不全量读取；证据脱敏或哈希（X4）。产出溯源与置信度标注见 `AGENTS.md`「交付纪律」。
- 核心不是格式，而是**任何角色都能从文件重建事实**——上下文一旦散落，Agent 就无法判断哪份是当前版本，而「以聊天为准」在重启后必然丢失。

### 7.2 Herdr 与 plan-with-files 的协作原理

- **分工**：Herdr 管「活的」——进程、pane、workspace、worktree、互调与状态；plan-with-files 管「记得的」——目标、阶段、决策、错误、Next Step。Herdr 只保活进程，不保存对话上下文；重启或换机后靠文件恢复。
- **桥**：回执把运行时事件固化成文件（R1–R6）；编排者按回执收口、更新 plan（F1–F16），再由 plan 派生看板。运行时状态目录统一由 `scripts/crew_paths.py` 解析为 `<git-common-dir>/crew/`（linked worktree 取主仓 common dir；非 git 用 `<root>/.crew`）。
- **恢复顺序**：`{{doc_context}}` → 工单 → `{{doc_req}}` → plan/进度；不依赖旧聊天或自动记忆。这样换人、换机、重启后都能接上。
- 这条分工也解释了为什么「派活/等结果/读输出」走 Herdr，而「做到哪、为什么这样做」永远写 plan：前者是瞬时协调，后者是持久事实。

### 7.3 记忆策略

- **共享事实只存仓库**：`{{doc_context}}`、`{{doc_req}}`（含『落地要点』）、plan、`docs/adr/` 是所有角色的共同记忆，按需读取，不各自复制。
- **角色规则固化**：Builder / Reviewer 的职责写在角色文件里，属静态规则，不是记忆。
- **不建 per-role 自动记忆**：自动记忆按项目/用户隔离，不按 Agent 隔离；多角色共写会互相污染。保持默认关闭。
- **handoff 显式化**：完成报告、评审结论写工单或进度，不靠记忆传递。若启用自动记忆，只用于用户偏好或外部资料位置，项目事实仍以仓库文档为准。

### 7.4 上下文生命周期

- **一个 ticket 一个会话**：完成即收尾，换 ticket 前 `/compact`、`/clear` 或重建 tab。原因：长上下文增加成本、延迟并稀释注意力，会话历史不是资产。
- **运行中的会话不压缩**：压缩会打断正在进行的判断，且压缩摘要与交接文件可能不一致；统一在下一轮开场处理。
- 阈值与操作归属见 `AGENTS.md` 编排节与角色文件；本文只解释原则。
- **Handoff 靠文件**：完成报告、评审结论写工单/评审记录/进度，不写聊天记录；交接纪律见 `AGENTS.md` 编排节。
- **角色隔离**：一个 tab 一个角色；Reviewer 只看 diff + 需求文档，Builder 只看工单 + 需求文档。

### 7.5 值守与监听机制

- 编排者不能靠前台阻塞等待（`herdr agent prompt --wait` / `agent wait`）过日子——一个 Agent 的长任务会把编排者钉死，其他流全部停摆。
- 因此采用「guard 常驻轮询 + 一次性人工抽查 + 外置看门狗」：guard 按 G1–G6 的待办/在跑口径判断是否叫醒，正常零输出；人工抽查用监听脚本的边沿模式，不承担自动叫醒；外置定时只检查 guard 进程存活。
- **启动与恢复**：Agent 只经 `dispatch.py start` 启动并写 agent-log（`composedSha256`/`delivery`/`baseRef`）；`doctor.py` 的 `role.delivery` 复核最近一次启动与 compose 报告一致。
- 叫醒条件、冷却、待办/在跑口径见 G1–G6；命令用法见 [herdr-cheatsheet.md](herdr-cheatsheet.md)。

## 8. 可选远端扩展

本节为可选扩展，默认 local-only 即可完整运行：

- **远端 Issue/MR**：接入 GitLab/GitHub 后，工单可迁到远端 Issue（`glab` / `gh` CLI），合并走 MR + CI 审计；创建 MR 由 worker 用 CLI 或人工完成。接入后同步更新项目 issue-tracker 约定。
- **Firstmate 调度层（可选上游工具）**：并行流很多、需要自动监督/恢复时才考虑启用；启用需安装对应工具链并确认 Herdr backend protocol 满足要求。风险是上游成熟度与复杂度；先 local-only 跑通一轮再评估。

## 9. 并发与成本

- 需求探讨阶段不启用 crew/worktree；门禁 2 通过后才启动 Builder/Reviewer 与 worktree——过早并行会让未定的需求长出多份实现。
- 默认从 Builder + Reviewer 起步；Planner/Reviewer 用较强模型，Builder 可用便宜模型。
- 并行上限由验收能力决定：当并行收益低于验收能力时，不加 Agent；并行流各占一个 worktree，同一文件/资产串行（P1）。
- 每个 crew 独立上下文预算；阈值、压缩与 handoff 见 §7.4。
