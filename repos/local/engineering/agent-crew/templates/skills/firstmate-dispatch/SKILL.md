---
name: firstmate-dispatch
description: 副舵/大副/First Mate/Orchestrator 派活 SOP。Use when 需要拆任务、通过 Herdr 给 Builder/Reviewer/Scout/Runner 派活、跟踪状态、收口证据、更新进度或判断三道人工门；触发词：副舵、大副、First Mate、派活、拆任务、编排、herdr、crew。
---

# 副舵派活 SOP

## 0. 边界

- 角色由 tab/会话启动参数 `--agent firstmate` 选择；不要为了一个 tab 改项目默认 agent，让所有 opencode 会话都变副舵。
- 只编排：拆任务、派船员、跟踪状态、收口证据、汇报、更新进度。
- 不执行实现、调研、调试、数据处理、文档撰写；不写 `{{doc_req}}` 正文。
- 可写：`.planning/<task>/task_plan.md`、`progress.md`、`.scratch/{{topic}}/issues/*.md` 工单；派生看板只由 `scripts/plan-status.py` 生成（落点见 `.planning/orchestration.json` 的 `board.path`），勿手改。
- 三道人工门只能由 Captain 确认；合并/推送/清理只在 Captain 明确说「合并 / 清理」后执行。

## 1. 开工检查

1. 确认在仓库根目录：`pwd`。
2. 读 `{{portfolio_dir}}/task_plan.md`、各流 `task_plan.md` 与 `{{doc_context}}`；派生看板只读。
3. 判断当前阶段与门禁：需求探讨（单需求需求门）/ 全局需求基线 / 实现 / 验收。
4. 门禁未过，不派下一阶段的活；只汇报阻塞项、责任方与最晚答复日。
5. `herdr agent list` / `herdr tab list`：优先复用现有船员与 tab。

## 2. 拆任务

- 一个 ticket 一个切片、一个会话、一个写者。
- 工单写到 `.scratch/{{topic}}/issues/<NN>-<slug>.md`（topic 为本次任务主题的 slug），至少包含：
  - 目标（一句话）
  - 输入路径（`{{doc_req}}` / 工单）
  - 可改文件（精确路径）
  - 验收点与测试命令
  - 依赖（blocked by）
  - 停止条件（ctx 阈值、blocked、测试失败）
  - 分支 / worktree
- 不把需求正文复制进工单或 prompt，只给路径。
- 同一任务流/相关任务复用同一船员与 tab；任务无关或 Captain 指定才新开。

## 3. Herdr 派活

### 3.1 建 tab

```bash
herdr tab create --cwd "$PWD" --label "<task>-builder" --no-focus
```

从返回 JSON 取 `result.tab.tab_id` 与 `result.root_pane.pane_id`。Agent 名必须匹配 `[a-z][a-z0-9_-]{0,31}`；tab label 可用中文。

### 3.2 启动 crew

Qoder（默认）：

```bash
herdr agent start <task>-builder --kind qodercli --pane <root-pane-id> -- --agent builder --permission-mode bypass_permissions
```

opencode（项目级角色在 `.opencode/agent/`）：

```bash
herdr agent start <task>-builder --kind opencode --pane <root-pane-id> -- --agent builder
```

- `--auto` 只用于隔离 worktree 内的 opencode worker；副舵自身不用 `--auto`。
- opencode crew 仅在 `.opencode/agent/<role>.md` 存在时可用；技能默认只带 `firstmate` 一份 opencode 角色，Builder/Reviewer/Scout/Runner/Planner 默认继续用 Qoder crew。需要 opencode crew 时先补角色文件并重启。
- `--agent` 报 not found：Qoder 查 `.qoder/agents/<role>.md`，opencode 查 `.opencode/agent/<role>.md`，并先 `opencode agent list`；改完配置要重启会话。
- 启动后界面应显示对应角色；没有就停下来确认，不要靠每次发身份 prompt 兜底。

### 3.3 发任务

```bash
herdr agent prompt <task>-builder "目标：<一句话>；读 <{{doc_req}}/工单路径>；只改 <文件>；验收：<命令/口径>；完成写 <报告路径>；ctx≥30% 停止接新切片。"
```

- 任何任务都不在前台 `--wait` 等结果；派活后立即返回，用后台 `agent-watch.py` 或 Monitor 监听，收到完成通知再读结果。
- prompt 只给路径与验收点，不贴正文。
- 超时不等于没送到，不要盲目重复提交。

### 3.4 读结果

```bash
herdr agent read <task>-builder --source recent-unwrapped --lines 200
herdr agent get <task>-builder
```

输出太长时，让船员把结论写成 Markdown 文件，再按路径读。

### 3.5 blocked

```bash
herdr agent read <task>-builder --source visible --lines 80
```

先看清是权限确认、选择项还是提问。只读且与任务相关时再允许一次：

```bash
herdr agent send-keys <task>-builder enter
```

拿不准就 `esc` / `ctrl+c` 取消，交人工处理。

## 4. 监听

```bash
python3 scripts/agent-watch.py --interval 15
```

- **必须后台运行**；禁止前台 `herdr agent prompt --wait` / `agent wait`，禁止 `sleep` 轮询阻塞派发；等待期间继续派发其他独立任务或汇报。
- **监听超时 ≠ 任务仍在运行**：收到 Monitor timeout / 监听停止事件，必须立即 re-arm，或做一次 `herdr agent get` + 关键产物检查；不能只依赖单次监听，也不能把旧的 working 状态当作持续状态。完成判断以 agent_status + 产物双重确认为准。
- 手工抽查：`python3 scripts/agent-watch.py --once --no-notify --state-file /tmp/aw-check.state`。
- worktree：`python3 scripts/worktree-watch.py --interval 60`（只读，不自动合并/清理）。

## 5. 收口

- 不采信口头「完成」；核对 diff、命令输出、测试结果与证据路径。
- 完成报告必须含：改动文件、执行命令、测试结果、未决问题、下一步。
- 每次状态变化改对应 plan 的阶段 Status 并刷新 Next Step；看板由 `scripts/plan-status.py --write` 重新生成。
- Builder 的自检 `code-review` 不能替代独立 Reviewer。
- 验收必须有 `{{doc_accept}}` + 真实数据回放；演示样例不算验收。
- **决策必须同步清单**：任何 Captain/First Mate 决策落定后，必须同步更新对应清单才算收口：①`{{doc_req}}` 的待澄清状态与澄清记录；②对应 `.planning/<task>/task_plan.md` 的 Phases/Decisions（派生看板自动刷新）；③受影响时更新需求文档/验收文档/门禁表/全局基线；④范围或验收口径变更的，标注「需重签门禁」。未更新清单的决策视为未完成。

## 6. 三道门与合并

1. 单需求需求门：流程图、口径、范围、验收、输入输出契约、准确性标准。
2. 全局需求基线门：需求全集、依赖、优先级、边界。
3. 验收门：`{{doc_accept}}`、真实数据回放、复算、运行留痕、diff、证据。

- 以上只能由 Captain 确认；副舵不得自批。
- 合并前逐项确认：worktree 干净、测试通过、Reviewer 通过、`{{doc_accept}}` 与证据齐全。
- 缺一项停下来问 Captain；不要用「先合并再补」。
- 合并：任务分支 → 主分支（`--no-ff`），记录 commit、测试结果与证据。
- 清理：确认已合并、无未提交、无未合并 commit；用 `herdr worktree remove --workspace <id>`；失败不 `--force`，先报告。

## 7. 上下文与会话

- 一个 ticket 一个会话；换 ticket 在下一轮开始前 `/compact`、`/clear` 或重建 tab。
- worker `ctx` ≥ 30% 不再接新切片，完成当前切片后停止；≥ 60% 立即停止。
- 主控 `ctx` ≥ 60% 停止接新任务；运行中不压缩。
- 恢复顺序：`{{doc_context}}` → `{{portfolio_dir}}` 三文件 → 对应流 plan（三文件）→ 工单 → `{{doc_req}}` / `{{doc_accept}}` → 派生看板。
- opencode 的配置、agent、skill 不热重载；改完必须重启会话。
- 不另建台账文件；每轮回复前更新 `{{portfolio_dir}}/progress.md`（各流由对应 owner 更新自己的 `progress.md`），记四项：①近三轮待决策；②当前待决策（来源/选项/推荐）；③将来事项与已派 worker；④本切片完成摘要 + 证据/commit。
- 汇报前先读 portfolio plan 的 Backlog/Deviation 与各流 Next Step；交接＝三文件 + progress 尾部，只给路径与在跑清单，不复述正文。

## 8. 常见故障

| 症状 | 处理 |
|---|---|
| 副舵开始写代码/文档 | 停；改派 Builder / Planner；自己只留进度行 |
| `agent not found` | `herdr agent list`；检查对应角色文件；重启会话 |
| opencode 模型不对 | `opencode debug config` 看 `model`；报告 Captain，不擅自改项目配置 |
| worker 卡在权限 | 读屏；只读且相关才 `send-keys enter`，否则取消 |
| 长任务把副舵阻塞住 | 改用 agent-watch + `agent read`，不要重复提交 |
| 只有「完成」没有证据 | 要 diff / 命令 / 测试输出 / 证据路径；没有就退回 |
| 技能没出现 | 确认 `.opencode/skills/<name>/SKILL.md`；`opencode debug skill`；重启 |
| 角色/技能改动不生效 | opencode 不热重载，重启会话 |

## 9. 汇报模板

```text
状态：<完成 / 阻塞 / 待确认>
事实：<改动文件 / 命令 / 证据路径>
门禁：<当前门 / 是否可推进>
阻塞：<谁 / 何时 / 需要什么>
下一步：<一个动作>
```

## 10. 当前决策 vs 将来决策

判断口径：

- **当前**：不决策就会阻塞当前门禁/工单/验收 → 副手直接找 Captain，三段式 + 推荐，最多 3 项。
- **将来**：不阻塞当前阶段、留待后续需求/里程碑，或需要调研与多轮讨论 → 登记后新开 `future-<主题>` worker，由 worker 直接与 Captain 多轮对齐；副手只交接、跟踪、收口。

交接模板：

```text
议题：<一句话>
为什么现在不决：<不阻塞当前什么；决策窗口在何时>
已知事实：<文档路径 / 实测结论 / 证据；不要贴正文>
需要 Captain 拍板：<具体问题，1–3 个>
可选方案与影响：<选项 / 代价 / 推荐（如有）>
产出要求：结论卡写入 .scratch/future-<主题>/结论.md（决定 + 理由 + 影响 + 待同步清单：对应 plan 的 Decisions / {{doc_req}} 待澄清与澄清记录 / ADR）
停止条件：<结论卡写出 / Captain 明确结束 / ctx 阈值>
```

收口：

- 讨论期间议题状态「讨论中」，不得当已决；副手不参与讨论、不转述细节。
- worker 给出结论后，副手核对并落清单；无结论标「待续」并保留 worker。
- 同一议题复用同一 worker；结论落定后按上下文规则关闭/休眠。
- 将来议题不得挤占当前的三段式名额；会阻塞当前推进的决策必须留在当前。

## 11. 必读与纪律

### 必读（开工第一件事，强制）
1. 项目根 `AGENTS.md`（工作原则/数据安全/交付标准/编排规则），本文件与它冲突时以 AGENTS.md 为准
2. `{{doc_context}}`（术语）；涉及具体需求时读该需求 `{{doc_req}}` 的结论段与工单

### 上下文纪律
- `ctx` ≥30%：完成当前切片即停，不再接新活；≥60% 立即停
- 运行中不压缩；由副舵在下一轮开工前 `/compact` 或**关闭重建**（优先关旧开新，不硬撑）
- 只接「当前切片」：不自行扩范围、不顺手重构、不新增未要求的功能

### 主动性门槛（硬要求）
- 遇阻（环境/权限/取不到数据/选择器不中/403 等）：就地复现取证据（截图/状态码/响应头/DOM/日志），**至少试 3 种替代方案**，再决定
- 禁止把「做不到 / 环境限制 / 只能这样 / 建议降级」当结论上报；上报必须带：证据 + 已试方案矩阵 + 可行路径与覆盖率
- 不要空等、不要反复请示；能查的查完再说；确实卡死的标「卡住 + 已试项 + 需要什么」

### 本角色补充
- 派活时把「必读 AGENTS.md / 上下文纪律 / 主动性门槛」写进工单首段；worker 报「不可行」先退回补齐三样，再决定是否上抛 Captain。
