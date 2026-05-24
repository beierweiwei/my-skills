#!/usr/bin/env bash
set -euo pipefail

target_dir="."
force=0
install_hook=""

usage() {
  cat <<'USAGE'
用法:
  bash init-workflow.sh [options]
  ./init-workflow.sh [options]

注意: 这是 Bash 脚本，不要用 python3 执行。

在项目中创建多 agent 编码工作流（v2 架构）。

选项:
  --target DIR                 目标项目目录，默认当前目录
  --force                      覆盖已存在的工作流托管文件；
                               不覆盖已有 AGENTS.md / CLAUDE.md，
                               入口文件仍写入 .agent/templates/*.append.md
  --install-hook {project|global}
                               安装 Claude Code Stop 钩子（自动关闭子 agent 面板）
                               project = 项目级（.claude/settings.local.json）
                               global  = 全局（~/.claude/settings.json）
  -h, --help                   显示帮助
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target)
      target_dir="${2:?--target 需要一个目录}"
      shift 2
      ;;
    --force)
      force=1
      shift
      ;;
    --install-hook)
      install_hook="${2:?--install-hook 需要 project 或 global}"
      case "$install_hook" in
        project|global) ;;
        *) echo "错误: --install-hook 值必须为 project 或 global，实际为 '$install_hook'" >&2; exit 2 ;;
      esac
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "未知参数: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

mkdir -p "$target_dir"
cd "$target_dir"
mkdir -p \
  .agent/scripts \
  .agent/hooks \
  .agent/prompts \
  .agent/agents \
  .agent/escalations \
  .agent/escalations/active \
  .agent/templates \
  .agent/tasks/active \
  .agent/tasks/archive \
  .agent/runs \
  .agent/reviews

write_file() {
  local path="$1"
  local mode="${2:-}"
  local dir
  dir="$(dirname "$path")"
  mkdir -p "$dir"
  if [[ -e "$path" && "$force" -ne 1 ]]; then
    echo "跳过已有文件: $path"
    return 0
  fi
  cat > "$path"
  if [[ -n "$mode" ]]; then
    chmod "$mode" "$path"
  fi
  echo "写入: $path"
}

write_entry_file() {
  # 入口文件（AGENTS.md / CLAUDE.md）特殊处理：
  # - 不存在 → 直接创建
  # - 已存在 → 写入 .agent/templates/ 让 AI 做增量合并，不覆盖
  local path="$1"
  local dir
  dir="$(dirname "$path")"
  mkdir -p "$dir"
  if [[ ! -e "$path" ]]; then
    cat > "$path"
    echo "写入: $path"
  else
    local template_name=".agent/templates/$(basename "$path").append.md"
    cat > "$template_name"
    echo "文件已存在，追加模板写入: $template_name"
    echo "      请手动将 $template_name 的内容合并到 $path"
  fi
}

write_executable() {
  write_file "$1" "+x"
}

# ---- 入口文件 ----

write_entry_file "AGENTS.md" <<'EOF'
# Agent 协作规则

本文件是 Codex 等读取 `AGENTS.md` 的工具入口。共享协议位于 `.agent/`。

## 当前工具身份

用户显式指定职责时，以用户指令为准。

## 启动前读取

1. `.agent/context.md`
2. `.agent/tasks/current.md`
3. `.agent/agents/` 下与当前职责匹配的 agent 定义

## 角色绑定

本工作流不预设固定角色。每个任务根据需要动态绑定 agent：

- 规划阶段 → planner agent
- 实现阶段 → implementer agent
- 评审阶段 → reviewer agent

手动启动子 CLI 时，传递对应的 agent 定义和任务合约。
EOF

write_entry_file "CLAUDE.md" <<'EOF'
# Claude Code 协作入口

本文件是 Claude Code 的项目入口。共享协议位于 `.agent/`。

## 当前工具身份

用户显式指定职责时，以用户指令为准。

## 共享规则

@.agent/context.md
@.agent/tasks/current.md
@.agent/agents/implementer.md
EOF

# ---- 配置 ----

write_file ".agent/config.env" <<'EOF'
# 多 agent 工作流配置
# 由 init-workflow.sh 初始化生成。

# 子 CLI 命令。可改为 claude、codex exec、gemini 等。
SUB_CLI_COMMAND=claude

# 子 CLI 默认参数。目标是限制回合、减少 token、避免长时间悬挂。
SUB_CLI_ARGS="--dangerously-skip-permissions --max-turns 80"

# Tmux 子面板布局策略: auto | h | v
# auto: 水平栏数 < 2 则水平分割, >= 2 则垂直分割
TMUX_SPAWN_LAYOUT=auto

# 子 CLI 超时（秒），0 表示不超时
TMUX_SUB_CLI_TIMEOUT=0
EOF

# ---- 上下文与决策 ----

write_file ".agent/context.md" <<'EOF'
# Agent 共享上下文

本项目使用文件作为多 CLI 共享状态：`.agent/tasks/current.md` 是任务合约，`.agent/runs/` 是执行日志，`.agent/reviews/` 是评审结论，`.agent/escalations/` 是阻塞升级。

工作流：主 CLI 规划并写任务合约 → 子 CLI 在 worktree/tmux 中实现 → 主 CLI 按 diff、日志和验收标准评审。

## 常用命令

- 测试：未知
- 类型检查：未知
- Lint：未知

## 已知约束

- 用文件同步上下文，不用聊天历史同步上下文。
- 子 CLI 只读入口文件、任务合约和自己的 agent 定义，避免加载整套文档。
- 长期上下文只记录稳定事实，本文件尽量控制在 80 行以内。
EOF

write_file ".agent/decisions.md" <<'EOF'
# 决策记录

## 2026-05-22：使用基于文件的多 agent 协作

通过仓库内文件协调不同编码工具，而不是尝试共享聊天历史。

原因：

- 不同 CLI 的对话状态彼此隔离
- 文件可检查、可版本化、可被任意工具读取
- 任务合约、执行日志、评审日志和 git diff 比聊天记忆更可靠

影响：

- 每个任务必须先写入 `.agent/tasks/current.md`
- 子 agent 按任务合约工作，按需动态绑定角色
- 主 CLI 手动启动子 agent 执行任务。不再使用自动调度脚本。
- 入口文件只负责入口指引，真正共享协议放在 `.agent/`
EOF

# ---- 任务合约（4 字段） ----

write_file ".agent/tasks/current.md" <<'EOF'
# 当前任务

## 状态

planning

允许值：

- planning
- ready_for_implementation
- implementing
- ready_for_review
- needs_fix
- approved

## 目标

待填写

## 上下文

待填写

## 验收标准

- 待填写
EOF

write_file ".agent/tasks/index.md" <<'EOF'
# 任务索引

此文件是活跃任务的短摘要，不是事实来源。任务事实来源是 `.agent/tasks/current.md` 或 `.agent/tasks/active/<task-id>.md`。

运行 `.agent/scripts/task-list` 查看当前活跃任务。
EOF

# ---- Agent 定义模板 ----

write_file ".agent/agents/implementer.md" <<'EOF'
---
name: implementer
allowed_tools: "Read Write Edit Bash(chmod *) Bash(bash tests/*.sh) Bash(bash tests/*_test.sh)"
---

# Agent: implementer

## 职责描述

- 按 `.agent/tasks/current.md` 实现
- 补充或更新测试
- 运行验证命令
- 根据评审结果修复 P0/P1 问题
- 把执行结果写入 `.agent/runs/implementer.md`

## 触发条件

由主 CLI 在以下情况调用：

- 任务状态为 `ready_for_implementation` 或 `needs_fix`
- 用户显式指定使用 implementer agent

## 允许工具范围

- Read
- Write
- Edit
- Bash(chmod *)
- Bash(bash tests/*.sh)
- Bash(bash tests/*_test.sh)

## 可以更新

- 任务合约中列出的业务文件
- 任务合约中列出的测试文件
- `.agent/runs/implementer.md`
- `.agent/tasks/current.md` 中的状态

## 默认不做

- 不维护 `.agent/context.md`
- 不维护 `.agent/decisions.md`
- 不修改 `AGENTS.md` 或 `CLAUDE.md`
- 不扩大任务范围
- 不处理 P2 问题，除非用户明确要求

## 硬性要求

- 不修改 `AGENTS.md`、`CLAUDE.md`、`.agent/context.md`、`.agent/decisions.md`
- 执行结束前必须写入 `.agent/runs/implementer.md`
- 执行结束前必须把 `.agent/tasks/current.md` 的状态更新为 `ready_for_review` 或 `needs_fix`
- 执行结束前必须检查并清理运行时数据、临时文件、调试文件和测试残留
- 如果无法完成，先写入 `.agent/escalations/` 记录故障，再写入 `.agent/runs/implementer.md`

## 停止条件

遇到以下情况，停止并先写入 `.agent/escalations/<timestamp>-implementer.md` 记录故障，再写入 `.agent/runs/implementer.md`：

- 需要修改范围外文件
- 任务合约不清楚
- 验证命令缺失且无法从项目中可靠推断
- 两轮修复后仍失败
- 发现安全、权限、数据迁移、API 兼容性或数据丢失风险

完成后直接结束进程，不要等待继续对话。
EOF

write_file ".agent/agents/planner.md" <<'EOF'
---
name: planner
allowed_tools: "Read Write Edit Bash(.agent/scripts/tmux-spawn-agent *)"
---

# Agent: planner

## 职责描述

- 澄清需求并写成任务合约
- 识别影响范围和风险
- 编写验收标准和验证命令
- 通过 tmux 启动子 agent（implementer / reviewer）执行任务
- 维护项目上下文和决策记录
- 判断任务是否可以进入实施阶段

## 触发条件

由主 CLI 在以下情况调用：

- 用户直接请求规划
- 任务状态为 `planning`
- 新需求需要分解成可执行的任务合约

## 允许工具范围

- Read
- Write
- Edit

## 可以更新

- `.agent/tasks/current.md`
- `.agent/context.md`
- `.agent/decisions.md`
- `AGENTS.md`
- `CLAUDE.md`
- `.agent/agents/` 下的 agent 定义文件

## 默认不做

- 不直接做大量机械实现，除非用户明确要求
- 不把临时错误日志写入长期记忆
EOF

write_file ".agent/agents/reviewer.md" <<'EOF'
---
name: reviewer
allowed_tools: "Read"
---

# Agent: reviewer

## 职责描述

- 评审实现是否符合任务合约
- 识别 bug、安全问题、性能问题和兼容性问题
- 检查测试覆盖是否充分
- 判断任务是否可以进入批准或需要修复

## 触发条件

由主 CLI 在以下情况调用：

- 任务状态为 `ready_for_review`
- 用户显式指定使用 reviewer agent

## 允许工具范围

- Read
- 读取 git diff（如果项目是 git 仓库）

## 可以更新

- `.agent/reviews/planner-reviewer.md`
- `.agent/tasks/current.md` 中的状态

## 默认不做

- 不改业务代码
- 不在评审阶段顺手重构无关代码
- 不把临时错误日志写入长期记忆

## 评审检查项

- 是否满足任务合约
- 是否超出范围
- 是否存在 bug
- 是否缺测试
- 是否有安全、性能、兼容性或边界问题

## 输出

- 写入 `.agent/reviews/planner-reviewer.md`
- 如果存在 P0/P1，把状态设为 `needs_fix`
- 如果不存在 P0/P1，把状态设为 `approved`
EOF

# ---- 升级记录 ----

write_file ".agent/escalations/README.md" <<'EOF'
# Escalation Records

When a sub-agent encounters a blocking failure, it writes an escalation record here.

## Format

Filename: `<timestamp>-<agent-name>.md`

```markdown
---
agent: <agent-identity>
task: <task-reference>
failed_attempts: <N>
last_error: <error-description>
escalated_to: <superior>
status: pending | resolved | escalated_to_human
---

## Failure context

...

## Attempts

- Attempt 1: ...
- Attempt 2: ...

## Resolution

...
```
EOF

# ---- 日志文件 ----

write_file ".agent/runs/implementer.md" <<'EOF'
# 执行者运行日志

## 摘要

尚未执行。

## 修改文件

- 无

## 运行命令

- 无

## 结果

- 无

## 遇到的问题

- 无

## 范围疑问

- 无

## 下一状态

planning
EOF

write_file ".agent/reviews/planner-reviewer.md" <<'EOF'
# 评审日志

## 结论

not_reviewed

允许值：

- not_reviewed
- approved
- needs_fix

## 问题

### P0

- 无

### P1

- 无

### P2

- 无

## 验证说明

- 无

## 剩余风险

- 无
EOF

# ---- 提示词模板 ----

write_file ".agent/prompts/plan.md" <<'EOF'
# plan

先读取：

- `AGENTS.md` 或当前 CLI 的入口文件
- `.agent/context.md`
- `.agent/decisions.md`
- `.agent/agents/planner.md`

然后创建或更新任务合约。

单任务场景使用 `.agent/tasks/current.md`。并行任务场景优先使用 `.agent/scripts/task-create` 创建 `.agent/tasks/active/<task-id>.md`，并在分派子 CLI 时传 `tmux-spawn-agent <role> --task <task-id-or-path>`。

任务合约必须包含：

- 状态
- 目标
- 上下文
- 验收标准

完成后把状态设为 `ready_for_implementation`。
EOF

write_file ".agent/prompts/implement.md" <<'EOF'
# implement

你负责实现任务。

先读取：

- 当前 CLI 的入口文件，例如 `CLAUDE.md` 或 `AGENTS.md`
- `.agent/context.md`
- 分配给你的任务文件：优先读取 `$AGENT_TASK_FILE`，未设置时读取 `.agent/tasks/current.md`
- `.agent/agents/implementer.md`

按任务合约实现。

规则：

- 不修改 `AGENTS.md`、`CLAUDE.md`、`.agent/context.md`、`.agent/decisions.md`
- 如果必须扩大范围，停止并写入 `.agent/runs/implementer.md`
- 如果遇到无法解决的故障（如需要修改范围外文件、验证命令缺失、两轮修复后仍失败），在停止前先写入 `.agent/escalations/` 记录故障
- 运行验证命令
- 完成前清理任务产生的运行时数据、临时文件和调试产物，除非任务合约明确允许保留
- 写入 `$AGENT_RUN_FILE`，未设置时写入 `.agent/runs/implementer.md`
- 把分配任务文件的状态设为 `ready_for_review`
EOF

write_file ".agent/prompts/review.md" <<'EOF'
# review

你负责评审任务。

先读取：

- 当前 CLI 的入口文件，例如 `AGENTS.md` 或 `CLAUDE.md`
- `.agent/context.md`
- 分配给你的任务文件：优先读取 `$AGENT_TASK_FILE`，未设置时读取 `.agent/tasks/current.md`
- `$AGENT_RUN_FILE` 或 `.agent/runs/implementer.md`
- `.agent/agents/reviewer.md`
- 当前 git diff，如果项目是 git 仓库

只评审，不改业务代码。

检查：

- 是否满足任务合约
- 是否超出范围
- 是否存在 bug
- 是否缺测试
- 是否有安全、性能、兼容性或边界问题

写入 `$AGENT_REVIEW_FILE`，未设置时写入 `.agent/reviews/planner-reviewer.md`。

如果存在 P0/P1，把状态设为 `needs_fix`。
如果不存在 P0/P1，把状态设为 `approved`。
EOF

# ---- 脚本 ----

write_executable ".agent/scripts/agent-status" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ -x "$PROJECT_DIR/.agent/scripts/task-list" ]; then
  "$PROJECT_DIR/.agent/scripts/task-list"
else
  echo "任务状态:"
  awk '
    /^## 状态$/ { show=1; next }
    /^## / && show { exit }
    show { print }
  ' "$PROJECT_DIR/.agent/tasks/current.md" 2>/dev/null || true
fi

echo
if git -C "$PROJECT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "Git 状态:"
  git -C "$PROJECT_DIR" status --short
else
  echo "Git 状态: 当前目录不是 git 仓库"
fi
EOF

write_executable ".agent/scripts/task-create" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

goal=""
slug=""
branch=""
worktree=""
role="implementer"
context="待填写"

usage() {
  cat <<'USAGE'
Usage: task-create --goal <text> [--slug <slug>] [--branch <branch>] [--worktree <path>] [--role <role>] [--context <text>]

创建一个任务作用域合约文件，并输出 task id。
USAGE
}

slugify() {
  printf '%s' "$1" |
    tr '[:upper:]' '[:lower:]' |
    sed 's/[^a-z0-9._-]/-/g; s/-\{2,\}/-/g; s/^-//; s/-$//' |
    cut -c 1-40
}

while [ $# -gt 0 ]; do
  case "$1" in
    --goal) goal="${2:?--goal requires text}"; shift 2 ;;
    --slug) slug="${2:?--slug requires text}"; shift 2 ;;
    --branch) branch="${2:?--branch requires text}"; shift 2 ;;
    --worktree) worktree="${2:?--worktree requires path}"; shift 2 ;;
    --role) role="${2:?--role requires text}"; shift 2 ;;
    --context) context="${2:?--context requires text}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -z "$goal" ] && { echo "Error: --goal is required" >&2; usage >&2; exit 2; }

if [ -z "$slug" ]; then
  slug="$(slugify "$goal")"
fi
[ -z "$slug" ] && slug="task"

stamp="$(date +%Y%m%d-%H%M)"
task_id="task-${stamp}-${slug}"
counter=2
while [ -e "$PROJECT_DIR/.agent/tasks/active/${task_id}.md" ]; do
  task_id="task-${stamp}-${slug}-${counter}"
  counter=$((counter + 1))
done

task_file="$PROJECT_DIR/.agent/tasks/active/${task_id}.md"
run_dir="$PROJECT_DIR/.agent/runs/${task_id}"
review_dir="$PROJECT_DIR/.agent/reviews/${task_id}"
escalation_dir="$PROJECT_DIR/.agent/escalations/${task_id}"

mkdir -p "$(dirname "$task_file")" "$run_dir" "$review_dir" "$escalation_dir"

cat > "$task_file" <<TASK
# 任务：${task_id}

## 状态

planning

允许值：

- planning
- ready_for_implementation
- implementing
- ready_for_review
- needs_fix
- approved
- abandoned

## Task ID

${task_id}

## 目标

${goal}

## 上下文

${context}

## 验收标准

- 待填写

## 分支

${branch:-待填写}

## Worktree

${worktree:-待填写}

## 分派角色

${role}

## 依赖

- 无

## 日志路径

- Run: .agent/runs/${task_id}/${role}.md
- Review: .agent/reviews/${task_id}/reviewer.md
- Escalation: .agent/escalations/${task_id}/
TASK

cat > "$run_dir/${role}.md" <<RUN
# ${role} 运行日志

## 摘要

尚未执行。

## 修改文件

- 无

## 运行命令

- 无

## 结果

- 无

## 遇到的问题

- 无

## 下一状态

planning
RUN

cat > "$review_dir/reviewer.md" <<'REVIEW'
# 评审日志

## 结论

not_reviewed

## 问题

### P0

- 无

### P1

- 无

### P2

- 无

## 验证说明

- 无
REVIEW

"$PROJECT_DIR/.agent/scripts/task-list" --write-index >/dev/null 2>&1 || true
printf '%s\n' "$task_id"
EOF

write_executable ".agent/scripts/task-show" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

task="${1:-}"
[ -z "$task" ] && { echo "Usage: task-show <task-id-or-path>" >&2; exit 2; }

if [ -f "$task" ]; then
  task_file="$task"
elif [ -f "$PROJECT_DIR/.agent/tasks/active/${task}.md" ]; then
  task_file="$PROJECT_DIR/.agent/tasks/active/${task}.md"
elif [ "$task" = "current" ] && [ -f "$PROJECT_DIR/.agent/tasks/current.md" ]; then
  task_file="$PROJECT_DIR/.agent/tasks/current.md"
else
  echo "Error: task not found: $task" >&2
  exit 1
fi

cat "$task_file"
EOF

write_executable ".agent/scripts/task-list" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
write_index=0

if [ "${1:-}" = "--write-index" ]; then
  write_index=1
fi

extract_section() {
  local section="$1"
  local file="$2"
  awk -v section="$section" '
    $0 == "## " section { found=1; next }
    /^## / && found { exit }
    found && /./ {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "")
      print
      exit
    }
  ' "$file"
}

render() {
  echo "# 任务索引"
  echo
  echo "| Task | Status | Role | Branch | Worktree | Goal |"
  echo "|------|--------|------|--------|----------|------|"

  shopt -s nullglob
  for file in "$PROJECT_DIR"/.agent/tasks/active/*.md; do
    task_id="$(basename "$file" .md)"
    status="$(extract_section "状态" "$file")"
    role="$(extract_section "分派角色" "$file")"
    branch="$(extract_section "分支" "$file")"
    worktree="$(extract_section "Worktree" "$file")"
    goal="$(extract_section "目标" "$file")"
    printf '| `%s` | %s | %s | %s | %s | %s |\n' \
      "$task_id" "${status:-unknown}" "${role:-unknown}" "${branch:-}" "${worktree:-}" "${goal:-}"
  done
}

if [ "$write_index" -eq 1 ]; then
  render > "$PROJECT_DIR/.agent/tasks/index.md"
else
  render
fi
EOF

write_executable ".agent/scripts/task-set-status" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

task=""
status=""

while [ $# -gt 0 ]; do
  case "$1" in
    --task) task="${2:?--task requires id or path}"; shift 2 ;;
    --status) status="${2:?--status requires value}"; shift 2 ;;
    -h|--help)
      echo "Usage: task-set-status --task <task-id-or-path> --status <status>"
      exit 0
      ;;
    *) echo "Error: unknown argument $1" >&2; exit 2 ;;
  esac
done

[ -z "$task" ] && { echo "Error: --task is required" >&2; exit 2; }
[ -z "$status" ] && { echo "Error: --status is required" >&2; exit 2; }

case "$status" in
  planning|ready_for_implementation|implementing|ready_for_review|needs_fix|approved|abandoned) ;;
  *) echo "Error: invalid status: $status" >&2; exit 2 ;;
esac

if [ -f "$task" ]; then
  task_file="$task"
elif [ -f "$PROJECT_DIR/.agent/tasks/active/${task}.md" ]; then
  task_file="$PROJECT_DIR/.agent/tasks/active/${task}.md"
elif [ "$task" = "current" ] && [ -f "$PROJECT_DIR/.agent/tasks/current.md" ]; then
  task_file="$PROJECT_DIR/.agent/tasks/current.md"
else
  echo "Error: task not found: $task" >&2
  exit 1
fi

tmp="$(mktemp)"
awk -v new_status="$status" '
  /^## 状态$/ { print; in_status=1; replaced=0; next }
  /^## / && in_status {
    if (!replaced) {
      print ""
      print new_status
      replaced=1
    }
    in_status=0
    print
    next
  }
  in_status {
    if (!replaced && $0 !~ /^[[:space:]]*$/) {
      print ""
      print new_status
      replaced=1
    }
    next
  }
  { print }
  END {
    if (in_status && !replaced) {
      print ""
      print new_status
    }
  }
' "$task_file" > "$tmp"
mv "$tmp" "$task_file"
"$PROJECT_DIR/.agent/scripts/task-list" --write-index >/dev/null 2>&1 || true
EOF

write_executable ".agent/scripts/task-archive" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

task="${1:-}"
[ -z "$task" ] && { echo "Usage: task-archive <task-id>" >&2; exit 2; }

src="$PROJECT_DIR/.agent/tasks/active/${task}.md"
dst_dir="$PROJECT_DIR/.agent/tasks/archive"
dst="$dst_dir/${task}.md"

[ ! -f "$src" ] && { echo "Error: active task not found: $task" >&2; exit 1; }
mkdir -p "$dst_dir"
mv "$src" "$dst"
"$PROJECT_DIR/.agent/scripts/task-list" --write-index >/dev/null 2>&1 || true
echo "$dst"
EOF

# ---- Tmux 子面板启动脚本 ----

write_executable ".agent/scripts/tmux-spawn-agent" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

# tmux-spawn-agent — 主 CLI 在 tmux 子面板中启动子 agent
#
# 用法: tmux-spawn-agent <role> [--task <id-or-path>] [--worktree <path>] [--cli <command>] [--cli-args <args>] [--prompt <text>] [--pane <pane_id>]
#
# 通过 Claude Code Stop 钩子检测子 agent 任务完成，自动关闭 tmux 面板：
# 1. 写入 .claude/settings.local.json 注册 Stop 钩子
# 2. 子 agent 每轮结束后钩子检查任务状态
# 3. 发现 task=ready_for_review + runs/implementer.md → 自动 kill-pane
#
# 角色:
#   implementer  — 实现 agent
#   reviewer     — 评审 agent
#   planner      — 规划 agent

TMUX_CMD="$(command -v tmux)"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

role=""
task_arg=""
worktree_path=""
cli_command=""
cli_args=""
prompt=""
target_pane=""

while [ $# -gt 0 ]; do
  case "$1" in
    --task) task_arg="$2"; shift 2 ;;
    --worktree) worktree_path="$2"; shift 2 ;;
    --cli) cli_command="$2"; shift 2 ;;
    --cli-args|--claude-args) cli_args="$2"; shift 2 ;;
    --prompt) prompt="$2"; shift 2 ;;
    --pane) target_pane="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: tmux-spawn-agent <role> [--task <id-or-path>] [--worktree <path>] [--cli <command>] [--cli-args <args>] [--prompt <text>] [--pane <pane_id>]"
      echo ""
      echo "启动指定角色的子 agent 到新的 tmux 面板中。"
      echo "通过 Claude Code Stop 钩子自动检测任务完成并关闭面板。"
      echo ""
      echo "角色: implementer | reviewer | planner"
      echo "选项:"
      echo "  --task <id-or-path>  指定任务 id 或任务文件路径，默认 .agent/tasks/current.md"
      echo "  --worktree <path>    指定 worktree 路径"
      echo "  --cli <command>      子 CLI 命令，默认读取 SUB_CLI_COMMAND 或 claude"
      echo "  --cli-args <args>    传给子 CLI 的额外参数"
      echo "  --prompt <text>      初始提示词"
      echo "  --pane <pane_id>     目标面板 ID（默认自动检测当前面板）"
      exit 0
      ;;
    -*)
      echo "Error: unknown option $1" >&2; exit 1 ;;
    *)
      [ -z "$role" ] && role="$1" || { echo "Error: unexpected argument $1" >&2; exit 1; }
      shift ;;
  esac
done

[ -z "$role" ] && { echo "Error: role is required" >&2; exit 1; }
case "$role" in implementer|reviewer|planner) ;; *) echo "Error: unknown role '$role'" >&2; exit 1 ;; esac
[ -z "${TMUX:-}" ] && { echo "Error: not inside a tmux session" >&2; exit 1; }

if [ -f "$PROJECT_DIR/.agent/config.env" ]; then
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.agent/config.env"
fi

cli_command="${cli_command:-${SUB_CLI_COMMAND:-claude}}"
cli_args="${cli_args:-${SUB_CLI_ARGS:---dangerously-skip-permissions --max-turns 80}}"
if ! command -v "${cli_command%% *}" >/dev/null 2>&1; then
  echo "Error: child CLI not found: $cli_command" >&2
  exit 1
fi

# 确定目标面板：--pane > TMUX_PANE > display-message > .agent/.main-pane
if [ -z "$target_pane" ]; then
  target_pane="${TMUX_PANE:-}"
fi
if [ -z "$target_pane" ]; then
  target_pane=$(tmux display-message -p '#{pane_id}' 2>/dev/null || true)
fi
if [ -z "$target_pane" ]; then
  mf="$PROJECT_DIR/.agent/.main-pane"
  [ -f "$mf" ] && target_pane=$(cat "$mf" 2>/dev/null || true)
fi
[ -z "$target_pane" ] && { echo "Error: cannot determine target tmux pane (try --pane)" >&2; exit 1; }

# 记录主面板 ID（供后续调用使用）
echo "$target_pane" > "$PROJECT_DIR/.agent/.main-pane"

target_dir="${worktree_path:-$PROJECT_DIR}"

resolve_task_file() {
  local task="$1"
  local base_dir="$2"
  if [ -z "$task" ]; then
    printf '%s\n' "$base_dir/.agent/tasks/current.md"
    return 0
  fi
  if [ -f "$task" ]; then
    cd "$(dirname "$task")" && printf '%s/%s\n' "$(pwd)" "$(basename "$task")"
    return 0
  fi
  if [ -f "$base_dir/.agent/tasks/active/${task}.md" ]; then
    printf '%s\n' "$base_dir/.agent/tasks/active/${task}.md"
    return 0
  fi
  if [ "$task" = "current" ] && [ -f "$base_dir/.agent/tasks/current.md" ]; then
    printf '%s\n' "$base_dir/.agent/tasks/current.md"
    return 0
  fi
  return 1
}

task_file="$(resolve_task_file "$task_arg" "$target_dir" 2>/dev/null || resolve_task_file "$task_arg" "$PROJECT_DIR" 2>/dev/null || true)"
[ -z "$task_file" ] && { echo "Error: task not found: ${task_arg:-current}" >&2; exit 1; }

task_id="$(basename "$task_file" .md)"
if [ "$task_id" = "current" ]; then
  run_file="$target_dir/.agent/runs/${role}.md"
  review_file="$target_dir/.agent/reviews/planner-reviewer.md"
  escalation_dir="$target_dir/.agent/escalations"
else
  run_file="$target_dir/.agent/runs/${task_id}/${role}.md"
  review_file="$target_dir/.agent/reviews/${task_id}/reviewer.md"
  escalation_dir="$target_dir/.agent/escalations/${task_id}"
fi

mkdir -p "$(dirname "$run_file")" "$(dirname "$review_file")" "$escalation_dir"

# ── 注册 Stop 钩子 ──────────────────────────────────────────
mkdir -p "$target_dir/.agent/hooks" "$target_dir/.claude"

HOOK_SRC="$SCRIPT_DIR/../hooks/agent-done-hook.sh"
HOOK_DST="$target_dir/.agent/hooks/agent-done-hook.sh"
[ -f "$HOOK_SRC" ] && [ ! -f "$HOOK_DST" ] && cp "$HOOK_SRC" "$HOOK_DST" && chmod +x "$HOOK_DST"

cat > "$target_dir/.claude/settings.local.json" <<'HOOKCFG'
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "bash .agent/hooks/agent-done-hook.sh" }
        ]
      }
    ]
  }
}
HOOKCFG

# ── 创建 tmux 面板并启动子 agent ──────────────────────────
# 创建 sentinel 标记，钩子脚本据此判断是子 agent 会话
touch "$target_dir/.agent/.sub-agent"
cat > "$target_dir/.agent/.sub-agent-task.env" <<TASKENV
AGENT_ROLE=$(printf '%q' "$role")
AGENT_TASK_ID=$(printf '%q' "$task_id")
AGENT_TASK_FILE=$(printf '%q' "$task_file")
AGENT_RUN_FILE=$(printf '%q' "$run_file")
AGENT_REVIEW_FILE=$(printf '%q' "$review_file")
AGENT_ESCALATION_DIR=$(printf '%q' "$escalation_dir")
TASKENV
columns=$($TMUX_CMD list-panes -t "$target_pane" -F '#{pane_left}' | sort -u | wc -l | tr -d ' ')
LAYOUT="${TMUX_SPAWN_LAYOUT:-auto}"
case "$LAYOUT" in
  h) split_flag="-h" ;;
  v) split_flag="-v" ;;
  auto) [ "$columns" -ge 2 ] && split_flag="-v" || split_flag="-h" ;;
esac

# 创建 runner 脚本直接执行（比 send-keys 更可靠）
runner=$(mktemp /tmp/agent-runner-XXXXXX 2>/dev/null)
if [ -z "$runner" ]; then
  echo "Error: failed to create runner script" >&2; exit 1
fi

if [ -n "$prompt" ]; then
  prompt_file=$(mktemp /tmp/agent-prompt-XXXXXXXX 2>/dev/null || echo "/tmp/agent-prompt-${$}-${RANDOM}")
  printf '%s\n' "$prompt" > "$prompt_file"
  cat > "$runner" <<-RUNNER
	export PAGER=cat GIT_PAGER=cat IS_SANDBOX=1
	export AGENT_ROLE='${role}'
	export AGENT_TASK_ID='${task_id}'
	export AGENT_TASK_FILE='${task_file}'
	export AGENT_RUN_FILE='${run_file}'
	export AGENT_REVIEW_FILE='${review_file}'
	export AGENT_ESCALATION_DIR='${escalation_dir}'
	cd '${target_dir}' || exit 1
	if [ "${TMUX_SUB_CLI_TIMEOUT:-0}" != "0" ]; then
	  exec timeout "${TMUX_SUB_CLI_TIMEOUT}" ${cli_command} ${cli_args} "\$(cat '${prompt_file}')"
	else
	  exec ${cli_command} ${cli_args} "\$(cat '${prompt_file}')"
	fi
	RUNNER
else
  cat > "$runner" <<-RUNNER
	export PAGER=cat GIT_PAGER=cat IS_SANDBOX=1
	export AGENT_ROLE='${role}'
	export AGENT_TASK_ID='${task_id}'
	export AGENT_TASK_FILE='${task_file}'
	export AGENT_RUN_FILE='${run_file}'
	export AGENT_REVIEW_FILE='${review_file}'
	export AGENT_ESCALATION_DIR='${escalation_dir}'
	cd '${target_dir}' || exit 1
	if [ "${TMUX_SUB_CLI_TIMEOUT:-0}" != "0" ]; then
	  exec timeout "${TMUX_SUB_CLI_TIMEOUT}" ${cli_command} ${cli_args}
	else
	  exec ${cli_command} ${cli_args}
	fi
	RUNNER
fi
chmod +x "$runner"

# split-window 直接执行 runner（shell 就绪后立即运行，无需 send-keys）
pane_id=$($TMUX_CMD split-window -t "$target_pane" "$split_flag" -P -F '#{pane_id}' -c "$target_dir" "exec bash '$runner'" 2>&1) || {
  echo "Error: failed to create tmux pane: $pane_id" >&2; exit 1
}

command -v tmux-agent-hook > /dev/null 2>&1 && tmux-agent-hook agent-start "$pane_id" 2>/dev/null || true

echo "$pane_id"
SCRIPT

write_executable ".agent/scripts/worktree-create" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

# worktree-create — 从当前仓库创建 git worktree
#
# 用法: worktree-create <branch> [--target <dir>]
#
# 自动清理分支名中的非法字符，如果 --target 目录不存在则自动创建。
# 输出 worktree 路径到 stdout。

branch=""
target_dir=""

while [ $# -gt 0 ]; do
  case "$1" in
    --target) target_dir="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: worktree-create <branch> [--target <dir>]"
      echo ""
      echo "从当前仓库创建 git worktree。"
      echo ""
      echo "参数:"
      echo "  <branch>             分支名"
      echo ""
      echo "选项:"
      echo "  --target <dir>       worktree 目标路径（默认: ../<branch>）"
      echo "  -h, --help           显示此帮助信息"
      echo ""
      echo "退出码:"
      echo "   0  成功"
      echo "   1  脏工作区"
      echo "   2  分支已存在"
      echo "   3  参数错误"
      echo "   4  创建失败"
      exit 0
      ;;
    -*)
      echo "Error: unknown option $1" >&2
      exit 3
      ;;
    *)
      [ -z "$branch" ] && branch="$1" || { echo "Error: unexpected argument $1" >&2; exit 3; }
      shift
      ;;
  esac
done

[ -z "$branch" ] && { echo "Error: branch name is required" >&2; exit 3; }

# 从 CWD 找到 git 仓库根目录
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "Error: not inside a git repository" >&2
  exit 4
}

cd "$GIT_ROOT"

# 检查脏工作区
if ! git diff --quiet HEAD || [ -n "$(git ls-files --others --exclude-standard)" ]; then
  echo "Error: working tree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

# 清理非法分支名字符（替换为连字符）
clean_branch=$(echo "$branch" | sed 's/[^a-zA-Z0-9._/-]/-/g')

# 检查分支是否已存在（本地）
if git show-ref --verify --quiet "refs/heads/$clean_branch"; then
  echo "Error: branch '$clean_branch' already exists locally" >&2
  exit 2
fi

# 设置默认 target 路径（仓库目录的同级目录）
if [ -z "$target_dir" ]; then
  repo_name="$(basename "$GIT_ROOT" | sed 's/[^a-zA-Z0-9._-]/-/g')"
  branch_path="$(echo "$clean_branch" | sed 's#[/][/]#/#g; s#/#-#g')"
  target_dir="$(dirname "$GIT_ROOT")/${repo_name}-${branch_path}"
fi

# 确保 target 父目录存在
target_parent="$(dirname "$target_dir")"
mkdir -p "$target_parent"

# 创建新分支的 worktree。stdout 只保留最终路径，便于 wt_path=$(...) 调用。
if ! git worktree add -b "$clean_branch" "$target_dir" >&2; then
  echo "Error: failed to create worktree at '$target_dir'" >&2
  exit 4
fi

echo "$target_dir"
SCRIPT

write_executable ".agent/scripts/worktree-cleanup" <<'SCRIPT'
#!/usr/bin/env bash
set -euo pipefail

# worktree-cleanup — 删除 git worktree 目录和记录
#
# 用法: worktree-cleanup <path>
#
# 先 git worktree remove 再删除目录。

target_path=""

while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help)
      echo "Usage: worktree-cleanup <path>"
      echo ""
      echo "删除 git worktree 目录和 git worktree 记录。"
      echo ""
      echo "参数:"
      echo "  <path>               worktree 路径"
      echo ""
      echo "选项:"
      echo "  -h, --help           显示此帮助信息"
      echo ""
      echo "退出码:"
      echo "   0  成功"
      echo "   1  路径不存在"
      echo "   2  路径不是 git worktree"
      echo "   3  参数错误"
      echo "   4  删除失败"
      exit 0
      ;;
    -*)
      echo "Error: unknown option $1" >&2
      exit 3
      ;;
    *)
      [ -z "$target_path" ] && target_path="$1" || { echo "Error: unexpected argument $1" >&2; exit 3; }
      shift
      ;;
  esac
done

[ -z "$target_path" ] && { echo "Error: path is required" >&2; exit 3; }

# 检查路径是否存在
[ ! -e "$target_path" ] && { echo "Error: path '$target_path' does not exist" >&2; exit 1; }

# 从 CWD 找到 git 仓库根目录
GIT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "Error: not inside a git repository" >&2
  exit 3
}

# 检查是否为 git worktree
target_resolved="$(cd "$target_path" && pwd)"
if ! git -C "$GIT_ROOT" worktree list | grep -qF "$target_resolved"; then
  echo "Error: '$target_path' is not a git worktree" >&2
  exit 2
fi

# 先尝试 git worktree remove，失败则强制删除
if ! git -C "$GIT_ROOT" worktree remove "$target_resolved" 2>/dev/null; then
  echo "Warning: git worktree remove failed, trying with --force" >&2
  git -C "$GIT_ROOT" worktree remove --force "$target_resolved" 2>/dev/null || true
fi

# 删除残留目录
if [ -d "$target_resolved" ]; then
  rm -rf "$target_resolved"
fi

# 确认 worktree 记录已清理
if git -C "$GIT_ROOT" worktree list | grep -qF "$target_resolved"; then
  echo "Error: failed to fully remove worktree '$target_resolved'" >&2
  exit 4
fi

exit 0
SCRIPT

# ---- Stop 钩子（自动关闭子 agent 面板） ----

write_executable ".agent/hooks/agent-done-hook.sh" <<'EOF'
#!/usr/bin/env bash
# agent-done-hook.sh — Claude Code Stop 钩子
# 子 agent 每轮结束后触发，检测任务是否完成，完成后关闭 tmux 面板

IFS= read -r line < /dev/stdin 2>/dev/null || true

# 只处理子 agent 会话（仅 tmux-spawn-agent 会创建此标记）
[ ! -f .agent/.sub-agent ] && exit 0

# 读取 tmux-spawn-agent 写入的任务作用域。没有该文件时 fallback 到旧单任务路径。
if [ -f .agent/.sub-agent-task.env ]; then
  # shellcheck disable=SC1091
  . .agent/.sub-agent-task.env
fi

TASK_FILE="${AGENT_TASK_FILE:-.agent/tasks/current.md}"
RUN_FILE="${AGENT_RUN_FILE:-.agent/runs/implementer.md}"

# 检查任务状态（跳过状态后的空行）
TASK_STATUS=$(awk '
  /^## 状态$/ { found=1; next }
  /^## / && found { exit }
  found && /./ { gsub(/^[[:space:]]+|[[:space:]]+$/, ""); print; exit }
' "$TASK_FILE" 2>/dev/null || echo "")

# 检查 implementer 运行日志是否存在
IMPL_DONE=0
[ -f "$RUN_FILE" ] && IMPL_DONE=1

# 两项条件都满足 → 任务完成 → 关闭面板
if [ "$IMPL_DONE" = "1" ] && { [ "$TASK_STATUS" = "ready_for_review" ] || [ "$TASK_STATUS" = "approved" ]; }; then
  tmux kill-pane -t "$TMUX_PANE" 2>/dev/null || true
fi
EOF

# ---- 安装 Stop 钩子（可选） ----

install_stop_hook() {
  local scope="$1"
  local settings_file=""
  local scope_label=""
  local hook_command=""

  case "$scope" in
    project)
      settings_file=".claude/settings.local.json"
      scope_label="项目级"
      hook_command="bash .agent/hooks/agent-done-hook.sh"
      mkdir -p ".claude"
      ;;
    global)
      settings_file="$HOME/.claude/settings.json"
      scope_label="全局"
      # 全局安装需要绝对路径，复制钩子脚本到 ~/.claude/hooks/
      mkdir -p "$HOME/.claude/hooks"
      if [ -f ".agent/hooks/agent-done-hook.sh" ]; then
        cp ".agent/hooks/agent-done-hook.sh" "$HOME/.claude/hooks/agent-done-hook.sh"
        chmod +x "$HOME/.claude/hooks/agent-done-hook.sh"
      fi
      hook_command="bash $HOME/.claude/hooks/agent-done-hook.sh"
      mkdir -p "$HOME/.claude"
      ;;
  esac

  local hook_config
  hook_config=$(cat <<'HOOKJSON'
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          { "type": "command", "command": "HOOK_COMMAND_PLACEHOLDER" }
        ]
      }
    ]
  }
}
HOOKJSON
)
  # 替换占位符
  hook_config="${hook_config//HOOK_COMMAND_PLACEHOLDER/$hook_command}"

  if [ -f "$settings_file" ]; then
    if command -v jq &>/dev/null; then
      local merged
      merged=$(jq --arg cmd "$hook_command" '.hooks.Stop = [{"hooks": [{"type": "command", "command": $cmd}]}]' "$settings_file" 2>/dev/null) && {
        printf '%s\n' "$merged" > "$settings_file"
        echo "已更新 Stop 钩子（$scope_label）: $settings_file"
        return 0
      }
    fi
    echo "警告: 无法合并已有设置，请手动编辑 $settings_file 添加 Stop 钩子:" >&2
    echo "$hook_config" >&2
  else
    printf '%s\n' "$hook_config" > "$settings_file"
    echo "已安装 Stop 钩子（$scope_label）: $settings_file"
  fi
}

if [ -n "$install_hook" ]; then
  install_stop_hook "$install_hook"
fi

echo
echo "多 agent 编码工作流已初始化: $(pwd)"
echo "下一步: 由主 CLI 根据任务需要启动对应 agent。"
