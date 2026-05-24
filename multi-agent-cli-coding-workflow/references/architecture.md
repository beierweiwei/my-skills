# 架构概览

```
init-workflow.sh  →  生成 .agent/ 文件树
                          │
tmux-spawn-agent  ───────→  子 CLI 在新 tmux 面板中启动
                          │
worktree-create   ───────→  创建隔离 worktree（git worktree add -b）
worktree-cleanup  ───────→  清理 worktree 目录和 git 记录
                          │
主 CLI 编排流程：
  规划 → 写入 tasks/current.md
       → tmux-spawn-agent implementer --worktree $(worktree-create feat/xxx)
       → 监测 pane 退出（Stop 钩子自动检测）
       → 读取 runs/implementer.md → 进入评审

并行任务流程：
  task-create → 写入 tasks/active/<task-id>.md
              → tmux-spawn-agent implementer --task <task-id> --worktree <path>
              → 子 CLI 只读 AGENT_TASK_FILE，写 AGENT_RUN_FILE
              → Stop 钩子按任务作用域检查状态和 run file
```

## 核心原则

- 项目文件是事实来源，聊天历史只是辅助。
- `.agent/` 是所有工具共享的上下文和状态目录。
- 主 CLI 拆小任务、写合约、评审结果。
- 子 CLI 只实现合约，不继承主 CLI 的聊天历史。
- 子 CLI 默认读取最小上下文：入口文件、`.agent/context.md`、分配的任务文件、自己的 `.agent/agents/<role>.md`。
- `.agent/tasks/current.md` 是单任务兼容路径；并行任务使用 `.agent/tasks/active/<task-id>.md`。

## 工作流架构

- **主 CLI 编排**：主 CLI 负责规划→分派→评审的全流程控制
- **子 CLI 通过 worktree 隔离**：不再使用写锁，git worktree 实现并行隔离
- **动态 CLI 绑定**：`.agent/config.env` 的 `SUB_CLI_COMMAND` 可设为 Claude、Codex、Gemini 等
- **运行时升级通道**：子 CLI 遇到阻塞通过 `.agent/escalations/` 抛给上层决策
- **任务作用域状态**：显式 task id 让任务合约、运行日志、评审日志和升级记录互相隔离

## 高效调度

- 小任务优先：每次派发一个可独立验证的垂直切片。
- 单写多读：同一业务区域同一时间只给一个 implementer 写，reviewer 只读 diff。
- 并行只用于低耦合任务：不同 worktree、不同文件边界、不同验收命令。
- 主 CLI 不把长聊天复制给子 CLI，只把必要事实写进任务合约。
- 并行分派时必须显式传 `--task <task-id-or-path>`，避免子 CLI 误读全局 `current.md`。

## 任务作用域

并行任务由 `.agent/scripts/task-create` 创建。task id 形如 `task-YYYYMMDD-HHMM-slug`，用于关联：

- `.agent/tasks/active/<task-id>.md`
- `.agent/runs/<task-id>/<role>.md`
- `.agent/reviews/<task-id>/reviewer.md`
- `.agent/escalations/<task-id>/`

`.agent/tasks/index.md` 是短摘要，不是事实来源。必要时可以通过 `task-list --write-index` 从 active 任务重建。

`tmux-spawn-agent --task` 会导出：

- `AGENT_TASK_FILE`
- `AGENT_RUN_FILE`
- `AGENT_REVIEW_FILE`
- `AGENT_ESCALATION_DIR`

## Stop 钩子机制

tmux-spawn-agent 在启动子 agent 时动态注册 Claude Code Stop 钩子：

1. 写入 `.claude/settings.local.json` 注册 Stop 事件
2. 子 agent 每轮运行结束后，钩子脚本 `agent-done-hook.sh` 检查任务状态
3. 状态为 `ready_for_review`/`approved` 且对应 run file 存在时 → `tmux kill-pane`

钩子也可通过 `init-workflow.sh --install-hook project|global` 提前安装。
