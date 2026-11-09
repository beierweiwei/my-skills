<!-- agent-crew:orchestration:start -->
## 编排体系（agent-crew）

本节由 agent-crew 技能生成与更新：重复执行 init 只替换标记之间的内容，标记外是本项目自有内容。

### 规则与文档指针（项目不复制正文）

- 通用可执行规则：`{{crew_rules}}`（D 派单 / R 回执 / W 工位 / O 占用 / F 收口 / P 并发 / G 值守 / S 状态机 / X 红线）。
- 验收与呈报：`{{crew_acceptance}}`（A1–A5）。
- 机制与拓扑：`{{skill_root}}/references/orchestration-guide.md`；plan 纪律与看板：`{{skill_root}}/references/plan-discipline.md`；Herdr 命令速查：`{{skill_root}}/references/herdr-cheatsheet.md`。
- 角色动作：`.qoder/agents/<role>.md`、`.opencode/agent/<role>.md`（项目自有；`extends: skill:<role>` 继承技能基类，启动时 append-only 组合，权限与 frontmatter 只取基类）。
- 项目事实：本文件标记外、`{{doc_context}}`、topology（默认 `docs/orchestration/topology.md`，以配置为准）、`docs/crew/project-ext/`。
- 指针形态：AGENTS 与角色文件只用 `{{skill_root}}` 与技能 references 指针；项目内不存放规则/验收/指南副本。

### 唯一入口（dispatch）

- Agent 生命周期与工位变更只走 `python3 scripts/dispatch.py <compose|start|prompt|close|status|list|tab|pane>`；`scripts/start-*.sh` 只是薄包装。
- 角色文件、项目文档、项目脚本禁止直写 herdr 生命周期命令（agent 的 start/prompt/close、tab/pane 的变更子命令）或 runtime `--agent` 直启；只读 `agent list` / `tab list` / `pane list` / `status` 允许。
- 退出码：0 成功；1 校验/门禁拒绝；2 待人工（pending/冲突）；3 环境缺失（runtime/CLI/权限）。

### 安装 / 升级 / 体检

- 安装与 bootstrap：`python3 scripts/init.py`（`--check` 只读、`--dry-run` 零写入、`--yes` 应用）；AGENTS 无标记时零写入，生成 `docs/crew/AGENTS-block.pending.md` 并返回 2。
- 块重渲染：`python3 scripts/init.py --refresh-block --confirm`（只重渲染本节，块外不动；skillRoot 变化后使用）。
- 受管脚本升级/回滚：`python3 scripts/upgrade.py --diff`、`--apply --confirm`、`--rollback <backup-id>`（只替换受管脚本，逐字节可回滚）。
- 体检：`python3 scripts/doctor.py --root . --profile release --strict --json`；失败按输出 `next` 处理，不静默放行。

### 进度与协同

- 任务状态：`.planning/<task>/` 三文件为唯一真源；看板由 `python3 scripts/plan-status.py --write` 派生，勿手改；交接前 `--check-fresh`。
- 状态/回执/占用/值守：`python3 scripts/crew-state.py`、`crew-msg.py`、`crew-ticket.py`、`crew-guard.py`；工位巡检 `pane-audit.py`。
- 角色启动复用同一工位与 tab；回收窗与标签按 W4–W5。

### 决策与门禁

- 可逆、可配置、不涉合同/合规/主数据/架构口径的事项可代决，标注「代决（可覆盖）」。
- 必须 Captain 决策的清单：

{{captain_decisions}}

- 项目红线：

{{red_lines}}

- 三道人工门（单需求需求门、全局需求基线门、验收门）不可由 Agent 自批；验收门必须持有 `{{doc_accept}}`，按 `{{crew_acceptance}}` A1–A5 抽验。

### 数据安全

- 原始数据目录：`{{data_dir}}`；只读必要列/行，证据脱敏或哈希，客户敏感数据不入库。
- 遇红线即停：只暂停受影响项并标「待 Captain」，其余独立任务继续。

### 版本与反馈

- 技能版本见 `{{skill_root}}/SKILL.md` 与 `{{skill_root}}/CHANGELOG.md`；安装版本记录在 `docs/crew/manifest.json`，`doctor.py` 校验一致。
- 流程与提示词问题记入 `{{portfolio_dir}}/feedback.md`（场景/问题/影响/建议/状态）。
<!-- agent-crew:orchestration:end -->
