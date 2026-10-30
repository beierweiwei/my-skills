# Task Plan: <项目总览标题>

<!-- agent-crew portfolio plan 骨架。
     约定（plan-status.py 解析依赖，勿改结构）：
     - 每个 Phase 标题下恰好一行 `**Status:** pending|in_progress|complete`；
     - Phase 内 `- plan: \`.planning/<流目录>/task_plan.md\`` 行是流 plan 的引用锚点；
     - Phases 是「活跃流」视图（≤4 条），队列只住 Backlog；同一项只在一处。 -->

<!-- 可选节「业务链」＝业务主线节点视图（流程图节点级通断＋人话说明）；该表可选，
     启用时参照下方示例新增同名节并填表，看板将其自动置顶渲染。
     表列固定 | 节点 | 状态 | 说明 |；状态列单值简写：[x] 完成 / [ ] 未做 / ⏸ 待 Captain
     （单元格不换行、不塞多条 todo——表＝概览，todo 细节另起分节列表，行首标准语法
     - [x] / - [ ] / - [ ] 文字内带 ⏸ 与（待 Captain））；
     说明＝目标完成状态人话（一句做成了什么，禁过程流水账/裸编号）。示例：

## 业务链

| 节点 | 状态 | 说明 |
|---|---|---|
| <流程图节点名> | [x] | <一句做成了什么（目标完成状态，人话）> |

### <节点名> todo

- [x] <做成了什么>
- [ ] <还没做什么>
- [ ] ⏸ <待拍板事项>（待 Captain）

-->

## Goal

<一句话目标：项目要达成的最终状态>

## Next Step

<单一动作：当前唯一要推进的事（含执行者）>

## Current Phase

<!-- 由 plan-status.py 自动推导（首个未完成的 Phase），勿手工维护；全部完成后可改写为收口说明，作看板兜底显示。 -->

Phase 1

## Phases

### Phase 1: <流名称>
- plan: `.planning/<流目录>/task_plan.md`
- owner: <副舵/工位名>
- [ ] <该流首个切片>
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|

## Errors Encountered
| Error | Resolution |
|-------|------------|

## Backlog

> 编号约定：T*＝agent 可执行（plan-status 归入「Backlog」节）；C*＝待 Captain（归入「待 Captain」节）。T→C 阻塞映射见 `.planning/orchestration.json` 的 `backlog.captainBlocks`。

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|

## Deviation

<!-- 条目解决后在行内（建议「待决策」列）追加「已解决（YYYY-MM-DD）」标记，看板自动过滤折叠已解决条目。 -->

| 预期 | 实际 | 原因 | 待决策 |
|---|---|---|---|
