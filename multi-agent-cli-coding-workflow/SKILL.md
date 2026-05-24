---
name: multi-agent-cli-coding-workflow
description: Multi-agent CLI coding workflow system. DO trigger when user asks to coordinate multiple coding CLIs, set up task-driven development, or initialize the multi-agent workflow. Do NOT trigger for general coding questions or single-agent tasks.
---

# 多 Agent CLI 编码工作流

此 skill 帮助用户在项目中初始化和使用多 CLI 编码工作流。核心目标是：文件同步、任务小切片、子 CLI 少读上下文、主 CLI 统一评审。

## 先决条件

- **git 2.5+** — worktree 功能
- **tmux** — 子面板调度
- **Claude Code / Codex / Gemini 等编码 CLI**
- **Bash**

## 使用流程

### 第 1 步：初始化项目

如果目标项目尚未初始化工作流，运行：

```bash
multi-agent-init --target /path/to/project [--install-hook project|global]
```

在 skill 源码目录内开发时，也可以直接运行：

```bash
bash scripts/init-workflow.sh --target /path/to/project
```

注意：`init-workflow.sh` 是 Bash 脚本，不要用 `python3` 执行。

- `--install-hook project`：安装 Claude Code Stop 钩子（项目级 `.claude/settings.local.json`），子 agent 任务完成后自动关闭 tmux 面板
- `--install-hook global`：安装到全局 `~/.claude/settings.json`，所有项目生效
- 不传 `--install-hook`：`.agent/scripts/tmux-spawn-agent` 会在运行时动态注册项目钩子
- `--force`：覆盖已存在的 workflow 托管文件，但仍不覆盖已有 `AGENTS.md` / `CLAUDE.md`；入口文件继续写入 `.agent/templates/*.append.md`

初始化后生成 `.agent/` 文件树，包含脚本、agent 定义、任务合约模板。

**如果 `AGENTS.md` 或 `CLAUDE.md` 已存在**，init 脚本不会覆盖它们，而是把内容写入 `.agent/templates/AGENTS.md.append.md` 和 `.agent/templates/CLAUDE.md.append.md`。你需要检查这些文件，将必要的内容追加到已有的入口文件中。

具体来说，`CLAUDE.md` 需要添加以下 @ 引用：
```markdown
## 共享规则

@.agent/context.md
@.agent/tasks/current.md
@.agent/agents/implementer.md
```

`AGENTS.md` 需要补充角色绑定和 `.agent/` 共享协议说明。读取 `.agent/templates/*.append.md` 按需合并。

### 第 2 步：设置子 CLI

默认子 CLI 是 Claude Code。可编辑 `.agent/config.env`：

```bash
SUB_CLI_COMMAND=claude
SUB_CLI_ARGS="--dangerously-skip-permissions --max-turns 80"
```

也可以调度时覆盖：

```bash
bash .agent/scripts/tmux-spawn-agent implementer --cli codex --cli-args "exec" --prompt "按任务合约实现"
```

### 第 3 步：按工作流操作

完整工作流见 `@references/architecture.md`。最短路径：

**规划**：主 CLI 写入 `.agent/tasks/current.md`（状态、目标、上下文、验收标准）→ 设为 `ready_for_implementation`

**实现**：

```bash
wt_path=$(bash .agent/scripts/worktree-create feat/my-feature)
bash .agent/scripts/tmux-spawn-agent implementer \
  --worktree "$wt_path" \
  --prompt "读取 .agent/tasks/current.md，只实现验收标准，完成后写 .agent/runs/implementer.md"
```

**评审**：读取 `runs/implementer.md` → 按验收标准评审 → 更新 `reviews/planner-reviewer.md` 和任务状态

**批准与清理**：
```bash
bash .agent/scripts/worktree-cleanup "$wt_path"
```

### 并行任务

`.agent/tasks/current.md` 继续作为单任务兼容路径。需要并行分派时，先创建任务作用域文件：

```bash
task_id=$(bash .agent/scripts/task-create --goal "实现独立垂直切片" --slug feature-slice)
bash .agent/scripts/task-set-status --task "$task_id" --status ready_for_implementation
bash .agent/scripts/task-list
```

分派子 CLI 时显式传任务：

```bash
wt_path=$(bash .agent/scripts/worktree-create feat/feature-slice)
bash .agent/scripts/tmux-spawn-agent implementer \
  --task "$task_id" \
  --worktree "$wt_path" \
  --prompt '读取 $AGENT_TASK_FILE，只实现验收标准，完成后写 $AGENT_RUN_FILE'
```

子 CLI 会收到这些环境变量：

- `AGENT_TASK_FILE`
- `AGENT_RUN_FILE`
- `AGENT_REVIEW_FILE`
- `AGENT_ESCALATION_DIR`

任务作用域日志位于 `.agent/runs/<task-id>/`、`.agent/reviews/<task-id>/`、`.agent/escalations/<task-id>/`。

## 省 token 规则

- `.agent/context.md` 只放稳定事实，默认控制在 80 行内
- 任务细节只写 `.agent/tasks/current.md`
- 子 CLI 默认只读入口文件、`.agent/context.md`、`.agent/tasks/current.md`、自己的 `.agent/agents/<role>.md`
- 一次只派发一个小的垂直切片；主 CLI 负责拆分和评审
- 子 CLI 遇到范围不清、验证缺失、两轮失败或高风险变更时写 `.agent/escalations/`，不要继续试错

## 脚本参考

| 脚本 | 用途 |
|------|------|
| `scripts/init-workflow.sh` | 在目标项目中初始化 `.agent/` 文件树 |
| 安装后 `.agent/scripts/tmux-spawn-agent` | 在 tmux 面板中启动子 CLI，自动注册 Stop 钩子 |
| 安装后 `.agent/scripts/worktree-create` | 创建隔离 git worktree |
| 安装后 `.agent/scripts/worktree-cleanup` | 清理 worktree 目录和 git 记录 |
| 安装后 `.agent/scripts/agent-status` | 显示任务状态和 git 状态 |
| 安装后 `.agent/scripts/task-create` | 创建 task id 作用域任务合约 |
| 安装后 `.agent/scripts/task-list` | 显示活跃任务短索引 |
| 安装后 `.agent/scripts/task-show` | 输出单个任务合约 |
| 安装后 `.agent/scripts/task-set-status` | 更新单个任务状态 |
| 安装后 `.agent/scripts/task-archive` | 归档已完成或放弃的任务 |

## 深入阅读

- `@references/architecture.md` — 架构概览和钩子机制
- `@references/file-structure.md` — 完整文件树和核心文件说明
- `@references/roles.md` — 角色定义、状态机和升级规则
