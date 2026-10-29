# 术语表

> 全项目共用词汇。新会话先读再用；新增术语先在此登记，避免同物异名。
> 由 agent-crew 技能生成骨架；「编排术语」节预置，业务术语由项目自行补充。

## 编排术语

| 术语 | 释义 | 备注 |
|---|---|---|
| Captain | 项目的人类负责人（唯一决策人）；三道人工门与合并/推送只由 Captain 确认 | |
| Herdr | 终端原生多 Agent 运行时：workspace/tab/pane、Agent 互调、worktree、远程常驻 | |
| 总舵（Helm） | Captain 唯一对话入口的编排 Agent：拆解、派发副舵、聚合进度与上抛决策；不执行实现 | 仅 Qoder 栈提供 |
| 大副（First Mate） | 只编排不执行的副手：拆任务、派船员、跟踪进度、收口汇报 | opencode 栈为主控入口 |
| 船员（Crew） | 被调度执行具体任务的 Agent：Builder/Reviewer/Planner/Runner/Scout | 与「大副」相对 |
| Runner（杂活船员） | 专接临时小任务的船员，固定「杂活」tab；不写需求正文、不合并 | |
| 工单（Ticket） | 执行层任务卡，只写任务与依赖；落点 `.scratch/<topic>/issues/` | 状态回写 plan |
| portfolio plan | 多流总览 plan：Goal/Phases(活跃流)/Decisions/Errors/Backlog/Deviation | 落点见 `.planning/orchestration.json` 的 `portfolioDir` |
| 流 plan | 单条流的 task_plan：Goal/Next Step/Current Phase/Phases/Decisions/Errors | 与 portfolio 同骨架，无 Backlog/Deviation |
| 派生看板 | 由 `scripts/plan-status.py --write` 从 plan 生成的进度视图，勿手改 | 落点见配置 `board.path` |
| 三道人工门 | 单需求需求门、全局需求基线门、验收门；人工确认，不可由 Agent 自批 | |
| YOLO 模式 | worker/副舵启动时放宽权限确认（`--permission-mode bypass_permissions` / opencode `--auto`） | 不放宽合并/推送/清理与数据安全门禁 |

## 业务术语

<!-- 项目业务词汇在此登记（格式同上表）；保持同物同名，跨角色共用。 -->

| 术语 | 释义 | 备注 |
|---|---|---|
