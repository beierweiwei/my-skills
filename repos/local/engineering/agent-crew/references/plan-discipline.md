# Plan 纪律与看板派生（agent-crew）

> plan-with-files 工作记忆 + portfolio 多流 plan 模式的使用纪律；运行期规则以项目 `AGENTS.md`「工作记忆与进度」节为准，本文是字段说明与配套机制说明。

## 1. plan-with-files 使用纪律

- **使用范围**：≥3 步或 ≥5 次工具调用的任务；简单问答、单文件小修不建 plan。
- **三文件**：`task_plan.md`（真源）、`findings.md`、`progress.md`——队列、进度、决策、错误的唯一记录（原生三文件，ASCII 命名）；并行任务各自一个命名 plan。
- **建/恢复/校验**：用上游技能 planning-with-files 的脚本 `init-session.sh`（ASCII 名）建命名 plan（`.planning/YYYY-MM-DD-<slug>/`），填完跑 `check-complete.sh --validate` 校验；开工读**三份文件** + `git diff --stat`；有 `.planning/` 时不回退根 plan；多 plan 无 `PLAN_ID` 先纠偏。
- **写者**：一 plan 一 owner；worker 只写自己的报告/ledger，不改 `task_plan.md`（多写者会静默覆盖）。
- **阶段纪律**：阶段完成改该 Phase 下**唯一**的 `**Status:**` 行（pending → in_progress → complete）并刷新 `## Next Step`（单一动作）；错误进 `## Errors Encountered`（Error/Resolution）；决策进 `## Decisions Made`（Decision/Rationale，代决标注「代决（可覆盖）」）。
- **待办**：需用户/客户/IT 做的事记入需求文档待澄清节，或在 Backlog 阻塞列标 `待 Cx`；不另建待办文件。
- **Anti-pattern**：手改派生看板；同一状态多处记录；覆盖已有 plan；多写者改同一 plan；把队列字段塞进 Phases。

## 2. plan 骨架字段（plan-status.py 解析依赖）

**骨架来源**：流 plan 骨架＝技能目录 `templates/plans/stream-task_plan.md`，建流 plan 时复制该文件起步（portfolio 骨架由 init 直接生成，无需手取）。

### 流 plan（`.planning/<task>/task_plan.md`）

| 字段 | 说明 |
|---|---|
| `## Goal` | 一句话目标 |
| `## Next Step` | 单一动作（含执行者）；每次阶段变化后刷新 |
| `## Current Phase` | 当前阶段名 |
| `## Phases` | 每 Phase = `### Phase N: <名>` 标题 + `- owner:` 行 + checklist + **恰好一行** `**Status:** pending\|in_progress\|complete`；portfolio 内的流 plan 另有 `- plan: \`.planning/<路径>\`` 引用行（看板解析流引用的锚点） |
| `## Decisions Made` | 表：Decision / Rationale |
| `## Errors Encountered` | 表：Error / Resolution |

### portfolio plan（多流总览）

- 与流 plan 同骨架，另有两节：
- `## Backlog`：`| # | 目标 | P | 依赖 | 阻塞 |` 五列表。编号 **T\*＝agent 可执行、C\*＝待 Captain**（看板据此前缀分流「Backlog」与「待 Captain」两节）；T→C 阻塞映射见配置 `backlog.captainBlocks`（映射存在时该行「阻塞」列在流表改写为「待 C 项」）。
- `## Deviation`：`| 预期 | 实际 | 原因 | 待决策 |` 四列表（待决策列可引用 C 编号）。
- Phases 是「活跃流」视图（≤4 条）；未派发项只住 Backlog，不另建队列文件。

**硬规则**：每个 Phase 标题下恰好一行 `**Status:**`（check-complete 与 plan-status 都以此为准）；`**Status:**` 行不得出现两条；字段标题（`## Goal` 等）勿改名。

## 3. 看板派生规则

- **真源与视图**：`.planning/*/task_plan.md` 是唯一状态真源；派生看板只由 `python3 scripts/plan-status.py --write` 生成，**勿手改**。
- **落点与标题**：由项目 `.planning/orchestration.json` 的 `board.path` / `board.title` 配置（默认 `docs/进度追踪.md`）；父目录不存在会自动创建。
- **portfolio 识别**：按配置 `portfolioMatch` glob 匹配 plan 目录名（默认 `*portfolio*`）。
- **看板结构**：流表（当前阶段/完成数/健康/该流 Next Step）+ Deviation + Backlog（T\*）+ 待 Captain（C\*）。
- **归档**：不内置；需要归档历史看板时以 `--archive <path>` 一次性显式执行。`board.archive` 配置为空时看板不写归档链接行。
- **自检**：`python3 scripts/plan-status.py --selftest` 验证 T/C 分流与流表结构（核心契约），部署后先跑一次。
