# 文件结构

```
项目根目录/
├── .agent/
│   ├── agents/           # Agent 职责定义
│   ├── config.env        # 运行配置（tmux 布局策略等）
│   ├── context.md        # 共享上下文
│   ├── decisions.md      # 长期决策记录
│   ├── escalations/      # 运行时升级记录
│   ├── hooks/            # 钩子脚本
│   ├── prompts/          # 提示词模板
│   ├── reviews/          # 评审日志
│   ├── runs/             # 执行日志
│   ├── scripts/          # 辅助脚本
│   │   ├── agent-status          # 显示任务和 git 状态
│   │   ├── task-archive          # 归档活跃任务
│   │   ├── task-create           # 创建 task id 作用域任务
│   │   ├── task-list             # 展示活跃任务索引
│   │   ├── task-set-status       # 更新单个任务状态
│   │   ├── task-show             # 输出单个任务合约
│   │   ├── tmux-spawn-agent      # 在 tmux 面板中启动子 CLI
│   │   ├── worktree-create       # 创建隔离 git worktree
│   │   └── worktree-cleanup      # 清理 worktree
│   └── tasks/            # 当前任务合约和并行任务文件
│       ├── current.md            # 单任务兼容合约
│       ├── index.md              # 活跃任务短索引
│       ├── active/               # 并行任务合约
│       └── archive/              # 已归档任务合约
├── .claude/
│   └── settings.local.json         # Stop 钩子注册（如果安装）
├── AGENTS.md             # Codex 等工具入口
├── CLAUDE.md             # Claude Code 入口
├── skills/               # Skill 源码（开发目录）
│   └── multi-agent-cli-coding-workflow/
│       ├── SKILL.md
│       ├── references/   # 参考文档
│       └── scripts/      # 安装和辅助脚本
├── package.json          # npm 包入口和 bin 配置
└── README.md             # npm 包使用说明
```

## 核心文件说明

| 文件 | 用途 |
|------|------|
| `AGENTS.md` | Codex 等读取 `AGENTS.md` 的工具入口 |
| `CLAUDE.md` | Claude Code 入口，@引用共享协议 |
| `.agent/context.md` | 共享上下文（架构、常用命令、约束） |
| `.agent/tasks/current.md` | 单任务兼容合约（状态、目标、验收标准） |
| `.agent/tasks/active/*.md` | task id 作用域任务合约 |
| `.agent/tasks/index.md` | 活跃任务短索引，可由 `task-list --write-index` 重建 |
| `.agent/decisions.md` | 长期决策记录 |
| `.agent/agents/*.md` | 子 CLI 角色说明 |
| `.agent/runs/implementer.md` | 单任务兼容执行者运行日志 |
| `.agent/runs/<task-id>/<role>.md` | 并行任务作用域运行日志 |
| `.agent/reviews/planner-reviewer.md` | 单任务兼容评审日志 |
| `.agent/reviews/<task-id>/reviewer.md` | 并行任务作用域评审日志 |
| `.agent/escalations/<task-id>/` | 并行任务作用域升级记录 |
