# Herdr 命令速查（agent-crew）

> 日常操作手册。角色分工、门禁与编排契约以 [orchestration-guide.md](orchestration-guide.md) 为准；本文只讲怎么按键、怎么发命令。

## 1. 原理（够用版）

Herdr 是**终端里的多 Agent 运行时**，不是模型，也不是任务规划器。

- **四层模型**：workspace（项目）→ tab（视角）→ pane（终端格）→ agent（跑在 pane 里的编码 Agent）。
- **后台 server + 前台 TUI**：关掉客户端或断 SSH，server 里的 Agent 继续跑；重连后用 `herdr` 回到现场。
- **状态识别**：Herdr 识别 pane 里的 Agent，标记 `working / blocked / idle / done / unknown`。装 integration 后靠 Agent 上报生命周期，状态更准。
- **Agent 互调**：派活走 `python3 scripts/dispatch.py prompt`（写 agent-log）；读结果用只读 `herdr agent read/get`；等待用后台 `agent-watch`，不前台 `--wait`，不需要人复制粘贴。
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

新建 tab 只走 `dispatch.py tab create`（`--root` 指向项目根）：

```bash
# 默认当前 workspace；--no-focus 不抢焦点
python3 scripts/dispatch.py --root . tab create --cwd "$PWD" --label "<task>-builder" --no-focus
```

返回里取 `tab_id` 与 `pane_id`（dispatch 的 stdout 同时给出 tab/pane 两个键）。

## 4. 启动 Agent（只走 dispatch）

Agent 生命周期与工位变更的唯一入口是 `python3 scripts/dispatch.py`；`start-agent.sh` / `start-helm.sh` 只是薄包装，不直接拼 runtime 参数。只读查看仍用 `herdr agent list` / `tab list` / `pane list`。

```bash
# 组合 dry-run（不启动，先看 base/overlay/权限/残留 token）
python3 scripts/dispatch.py --root . compose --role builder --runtime qoder --json

# 启动角色（pane/tab 由 dispatch 创建或复用；同名预检失败 rc=1）
python3 scripts/dispatch.py --root . start --role builder --runtime qoder --pane <pane_id>

# 提示 / 关闭 / 状态（--agent 取 dispatch 托管的 agent 名）
python3 scripts/dispatch.py --root . prompt --agent <task>-builder --message "读工单 <路径>，执行。"
python3 scripts/dispatch.py --root . close --agent <task>-builder
python3 scripts/dispatch.py --root . status --json
```

- **角色与权限**：项目角色文件（`.qoder/agents/<role>.md` / `.opencode/agent/<role>.md`）只放发现字段与 `extends: skill:<role>`；运行时 frontmatter/权限只取技能基类。Qoder 基类 `disallowedTools` 翻译为 `--disallowed-tools`；opencode 基类 `permission`/`mode` 原样进入组合产物。
- **YOLO 模式**：副舵派 worker 时由 dispatch 透传 `--permission-mode bypass_permissions`（Qoder）或 `--auto`（opencode）；只在隔离 worktree 与明确工单边界内，合并/推送/清理与数据安全门禁不放宽。
- **总舵**：`bash scripts/start-helm.sh`（内部走 dispatch）；agent 名取 `orchestration.json` 的 `helm.agentName`，默认派生 `helm-<项目名>`，同机全局唯一。
- **opencode**：技能源已带 7 个 `mode: primary` 基类；项目 stub 由 init 生成。dispatch 以 `OPENCODE_CONFIG_DIR` 指向组合目录启动；用 `opencode agent list` 确认发现，改 agent/skill/config 后必须重启会话（不热重载）。

## 5. 派活、等待、读结果

```bash
python3 scripts/dispatch.py --root . prompt --agent <task>-builder --message "读 <需求文档路径>，执行工单 <工单路径>；只改指定文件。"
herdr agent read <task>-builder --source recent-unwrapped --lines 200
herdr agent get <task>-builder
```

- 提示走 dispatch（写 agent-log）；`herdr agent read/get` 只读。
- 派活/重发只走 dispatch（写 agent-log）；排障先看 dispatch 日志，不把手工生命周期命令当主路径。
- `recent-unwrapped` 适合读长输出；读不到时让它把结论写成 Markdown 文件，再读文件。
- 超时不等于 prompt 没送到，不要盲目重复提交。
- 编排会话内禁止前台 `--wait` 占住主控：改用后台 `python3 scripts/agent-watch.py --interval 15` 或完成通知。

## 6. 遇到 blocked

```bash
herdr agent read <task>-builder --source visible --lines 80
```

先看清是权限确认、选择项还是提问。只读且与任务相关时再允许：

```bash
herdr agent send-keys <task>-builder enter    # 仅人工恢复：默认选中 Allow once
```

`send-keys` 只用于人工恢复卡住的会话，不作派活或日常主路径。

拿不准就用 `esc` 或 `ctrl+c` 取消，再人工处理。

## 7. 日常操作

```bash
herdr agent list                      # 所有 Agent 状态（只读）
herdr tab list                        # 当前 workspace 的 tab（只读）
herdr pane list                       # pane 清单（只读）
herdr tab rename <tab_id> 新名字
# 关 tab / pane 走 dispatch（写 agent-log、带工位约束）
python3 scripts/dispatch.py --root . tab close --tab <tab_id>
python3 scripts/dispatch.py --root . pane close --pane <pane_id>
```

- TUI 里 `Ctrl+B` 然后 `Q` detach；Agent 继续在后台跑，重新 `herdr` 回来。
- 上下文：看 CLI 状态栏的 `ctx` 用量。运行中的会话**不压缩**；worker（Builder/Reviewer/Scout）≥30% 不再接新切片，完成当前切片后停止，下一轮会话开始前统一 `/compact` 或重建；压缩摘要即交接，不单独写 handoff；主控/Planner ≥60% 停止接新任务，下一轮开始前压缩/重建。Herdr 只保活进程，不保存对话上下文。
- 后台监听：`python3 scripts/agent-watch.py --interval 15`（可加 `--workspace` / `--agent`）轮询 `herdr agent list`，状态变化发 Herdr 通知；不要前台 `--wait` 长时间阻塞。`worktree-watch.py` 只监控 worktree，不监控 Agent。状态去重文件默认 `.git/agent-watch.state`（全 worktree 共用一份，键为 `workspace/agent`，可用 `--state-file` 另指）；手工抽查用 `python3 scripts/agent-watch.py --once --no-notify --state-file /tmp/aw-check.state`，不要污染常驻监听的基线；通知只在 `HERDR_ENV=1`（Herdr 内）生效，Herdr 外只打印不通知。
- 后台建 tab / 启动 Agent 用 `--no-focus`，不要抢当前输入；dispatch 的 prompt 不抢焦点，无需额外参数。
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
