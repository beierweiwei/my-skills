# Task Plan: <流名称>

<!-- agent-crew 流 plan 骨架（portfolio 之下的一条流，或独立任务 plan）。
     约定（plan-status.py 解析依赖，勿改结构）：
     - 每个 Phase 标题下恰好一行 `**Status:** pending|in_progress|complete`；
     - 阶段完成只改 Status 并刷新 Next Step；错误进 Errors、决策进 Decisions；
     - Backlog/Deviation 收敛在 portfolio plan，流 plan 不建这两节。 -->

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

<一句话目标：该流要达成的最终状态>

## Next Step

<单一动作：当前唯一要推进的事（含执行者）>

## Current Phase

<!-- 由 plan-status.py 自动推导（首个未完成的 Phase），勿手工维护；全部完成后可改写为收口说明，作看板兜底显示。 -->

Phase 1

## Phases

### Phase 1: <阶段名称>
- owner: <工位/执行者名>
- [ ] <首个切片>
- **Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|

## Errors Encountered
| Error | Resolution |
|-------|------------|
