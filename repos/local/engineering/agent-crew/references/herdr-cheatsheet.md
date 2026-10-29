# Herdr 命令速查（agent-crew）

> 日常操作手册。角色分工、门禁与编排契约以 [orchestration-guide.md](orchestration-guide.md) 为准；本文只讲怎么按键、怎么发命令。

## 1. 原理（够用版）

Herdr 是**终端里的多 Agent 运行时**，不是模型，也不是任务规划器。

- **四层模型**：workspace（项目）→ tab（视角）→ pane（终端格）→ agent（跑在 pane 里的编码 Agent）。
- **后台 server + 前台 TUI**：关掉客户端或断 SSH，server 里的 Agent 继续跑；重连后用 `herdr` 回到现场。
- **状态识别**：Herdr 识别 pane 里的 Agent，标记 `working / blocked / idle / done / unknown`。装 integration 后靠 Agent 上报生命周期，状态更准。
- **Agent 互调**：一个 Agent 通过 `herdr agent prompt / read / wait` 给另一个 Agent 派活、读结果，不需要人复制粘贴。
- **为什么用 tab-per-role**：tab 是你的切换单位；一个 tab 一个 Agent，避免分屏过窄和误发到错误 pane。全局状态看侧边栏或 `herdr agent list`。

## 2. 开局

```bash
cd <项目根目录>
herdr status          # 确认 server: running
herdr                 # 进入 TUI；已在 Herdr 内则跳过
```

- workspace 按 cwd 识别：首次进入某项目目录时 Herdr 自动建 workspace。
- 日常布局建议：主控 tab（Captain + 编排者）、需求探讨 tab（Captain + Planner）、各角色 tab（实现阶段才开）、「杂活」tab（Runner 临时任务）。

## 3. 新建角色 tab

```bash
# 默认当前 workspace；--no-focus 不抢焦点
herdr tab create --cwd "$PWD" --label "<task>-builder" --no-focus
```

返回 JSON 里取 `result.tab.tab_id` 和 `result.root_pane.pane_id`。

## 4. 启动 Agent

```bash
herdr agent start <task>-builder --kind qodercli --pane <root_pane_id>
```

- `--kind` 可用 `herdr agent` 查看当前支持的 Agent。启动前先确认 `qodercli --version` 可用（qodercli 需在 PATH 中）。
- Agent 名必须匹配 `[a-z][a-z0-9_-]{0,31}`，如 `demo-builder`；tab label 可以用中文。
- 成功时返回 `interactive_ready: true`。
- **角色固化**：项目在 `.qoder/agents/builder.md` / `reviewer.md` / `scout.md` / `runner.md` 定义了 Qoder subagent（角色 + 工具权限），启动时带 `-- --agent builder|reviewer|scout|runner`，不再每次发身份 prompt。启动后界面应显示对应 `@角色`。
- **YOLO 模式**：副手启动 worker/crew 时加 `--permission-mode bypass_permissions`（界面显示 YOLO），减少实现过程中的权限确认；只在隔离 worktree 和明确工单边界内使用，合并/推送/清理与数据安全门禁不因此放宽。opencode worker 用 `--auto` 代替，副舵自身不用 `--auto`。
  完整启动命令：

  ```bash
  herdr agent start <流>-builder --kind qodercli --pane <pane_id> -- --agent builder --permission-mode bypass_permissions
  herdr agent start <流>-reviewer --kind qodercli --pane <pane_id> -- --agent reviewer --permission-mode bypass_permissions
  herdr agent start misc-runner --kind qodercli --pane <pane_id> -- --agent runner --permission-mode bypass_permissions
  ```

- **总舵**：不手敲裸命令——跑 `scripts/start-helm.sh` 一键完成（建 tab、起 agent、发首条汇报）。总舵 agent 名＝`orchestration.json` 的 `helm.agentName`，默认派生 `helm-<项目名>`；herdr agent 名同机全局唯一，不用裸名 `helm`（跨项目撞名），撞名时脚本提示关闭冲突 agent 或换名后重跑。
- **opencode**：项目级角色在 `.opencode/agent/`，已支持 `--agent`，不再需要手动身份 prompt；**角色按 tab/会话选择，项目不设默认 agent**（普通 opencode 会话是 `build`）。当前技能只带 `firstmate` 一份 opencode 角色，crew 默认继续用 Qoder；需要 opencode crew 时先补 `.opencode/agent/<role>.md` 并重启：

  ```bash
  # 主控 tab：副舵（agent 名同机唯一，一律带流/项目前缀）
  herdr agent start <流>-firstmate --kind opencode --pane <pane_id> -- --agent firstmate
  # 已补齐 builder 角色文件后
  herdr agent start <流>-builder --kind opencode --pane <pane_id> -- --agent builder
  ```

  用 `opencode agent list` 确认角色已加载，`opencode debug skill` 确认技能可见；改完 agent/skill/config 必须重启会话（opencode 配置不热重载）。

> 编排封装：日常用 `scripts/start-agent.sh <agent-name> <pane-id> <role>` 一条命令完成 YOLO 启动，不必手敲裸命令。

## 5. 派活、等待、读结果

```bash
herdr agent prompt <task>-builder "读 <需求文档路径>，执行工单 <工单路径>；只改指定文件。" --wait --timeout 600000
herdr agent read <task>-builder --source recent-unwrapped --lines 200
herdr agent get <task>-builder
```

- `--wait` 等到 `idle / done / blocked`；批量任务把超时设大。
- `recent-unwrapped` 适合读长输出；读不到时让它把结论写成 Markdown 文件，再读文件。
- 超时不等于 prompt 没送到，不要盲目重复提交。
- 编排会话内禁止前台 `--wait` 占住主控：改用后台 `python3 scripts/agent-watch.py --interval 15` 或完成通知。

## 6. 遇到 blocked

```bash
herdr agent read <task>-builder --source visible --lines 80
```

先看清是权限确认、选择项还是提问。只读且与任务相关时再允许：

```bash
herdr agent send-keys <task>-builder enter    # 默认选中 Allow once
```

拿不准就用 `esc` 或 `ctrl+c` 取消，再人工处理。

## 7. 日常操作

```bash
herdr agent list                      # 所有 Agent 状态
herdr tab list                        # 当前 workspace 的 tab
herdr tab rename <tab_id> 新名字
herdr pane move <pane_id> --new-tab   # 把误分屏的 pane 移成独立 tab
herdr tab close <tab_id>
herdr pane close <pane_id>
```

- TUI 里 `Ctrl+B` 然后 `Q` detach；Agent 继续在后台跑，重新 `herdr` 回来。
- 上下文：看 CLI 状态栏的 `ctx` 用量。运行中的会话**不压缩**；worker（Builder/Reviewer/Scout）≥30% 不再接新切片，完成当前切片后停止，下一轮会话开始前统一 `/compact` 或重建；压缩摘要即交接，不单独写 handoff；主控/Planner ≥60% 停止接新任务，下一轮开始前压缩/重建。Herdr 只保活进程，不保存对话上下文。
- 后台监听：`python3 scripts/agent-watch.py --interval 15`（可加 `--workspace` / `--agent`）轮询 `herdr agent list`，状态变化发 Herdr 通知；不要前台 `--wait` 长时间阻塞。`worktree-watch.py` 只监控 worktree，不监控 Agent。状态去重文件默认 `.git/agent-watch.state`（全 worktree 共用一份，键为 `workspace/agent`，可用 `--state-file` 另指）；手工抽查用 `python3 scripts/agent-watch.py --once --no-notify --state-file /tmp/aw-check.state`，不要污染常驻监听的基线；通知只在 `HERDR_ENV=1`（Herdr 内）生效，Herdr 外只打印不通知。
- 后台建 tab / 启动 Agent 用 `--no-focus`，不要抢当前输入；`herdr agent prompt` 本身没有 `--no-focus`，不要加。
- worktree：`python3 scripts/worktree-watch.py` 看未合并/未提交；常驻监控用 `--interval 60`，状态变化会发 Herdr 通知。合并与清理必须经 Captain 同意，由大副执行。
- 不要裸跑 `herdr` 做命令发现，它会启动 TUI；用 `herdr <命令组>` 看帮助。

## 8. 工位与技能配合（可选）

| tab | 角色 | 常配技能（可选，需另装对应上游技能） | 读 / 写 |
|---|---|---|---|
| 主控 | Captain + 副舵 | firstmate-dispatch / 拆单类 | 只编排、派活、进度、沟通 |
| 需求探讨 | Captain + Planner | 盘问/规格类 | 写需求文档 |
| Scout（可选） | crew | 调研类 | 写带来源调研报告 |
| Builder | crew | 实现 + TDD 类 | 只改工单指定文件 |
| Reviewer | crew | 评审类 | 只读 diff + 需求文档，写评审结论 |
| 杂活 | Runner | — | 临时小任务 |

派活时只给路径和验收点，不贴正文；每个 Agent 写完把回执交给副舵，由副舵记入对应 plan 的 `progress.md`。完整流程见 [orchestration-guide.md](orchestration-guide.md) §5。

## 9. 常见问题

- **`agent not found`**：名字只在 live agent 中存在；先 `herdr agent list`。
- **opencode `--agent` 报 not found**：确认 `.opencode/agent/<role>.md` 存在；`opencode agent list` 看是否加载；改完 agent 文件要重启会话（配置不热重载）。
- **opencode 模型不对**：`opencode debug config` 看最终 `model`；全局模型由用户级 opencode 配置决定，不在项目里改。
- **opencode 技能不出现**：确认 `.opencode/skills/<name>/SKILL.md`；`opencode debug skill` 检查；重启会话。
- **状态 `unknown`**：Herdr 认不出界面，不等于完成；用 `agent get` / `agent read` 确认。
- **读不到完整历史**：alternate-screen 历史有限；让它把结论写到文件，再按路径读。
- **分屏太窄**：不要默认分屏；一个角色一个 tab。
- **Windows**：Herdr Windows 仍是 beta；遇到问题优先 Linux / macOS。

---

> 来源与差异：本文是《Herdr 使用教程》（tab-per-role，见源项目 docs/）的通用速查版；去除了特定机器的路径、workspace id、业务 tab 布局与示例，机制内容与原教程一致。原教程以源项目仓库为准。
