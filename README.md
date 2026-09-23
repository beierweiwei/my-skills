# my-agents-settings

个人 AI Coding Agent（Codex / Claude Code / Qoder）跨工具的统一配置库。

## 结构

```
├── AGENTS.md               # 统一全局指令（Codex/Qoder 直接读；CLAUDE.md @引用本文件）
├── CLAUDE.md               # 一行指针 → AGENTS.md
├── mcp/mcp.json            # MCP 服务器配置（密钥用环境变量占位）
├── skills/<层>/<技能>/      # 技能库，按层组织；全部是指向 repos/ 的 junction
│   ├── engineering/        # 生产层：工程实践（默认安装）
│   ├── productivity/       # 生产层：通用工作流（默认安装）
│   ├── in-progress/        # 试验层：默认不装，需点名
│   └── deprecated/         # 已淘汰：永不安装，只留记录
├── plugins/                # Claude 官方插件（junction），由脚本管理
├── repos/                  # 上游仓库浅克隆缓存（可随时删除重建）
│   └── local/              # 自研技能源（随库提交，勿删）
└── scripts/
    ├── install-skills.ps1 / .sh   # 选择安装技能到 Agent 技能目录
    └── update-skills.ps1 / .sh    # 拉取/更新上游技能与插件，刷新 junction
```

## 安装技能

### 1. 从本库选择安装（自研 + 上游都可）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1        # 生产层全部 → ~/.agents/skills
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -List  # 列出技能与安装状态
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Skill tdd,mao-methods
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Layer engineering -DryRun
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -DestDir "$HOME\.claude\skills"
```

```bash
bash scripts/install-skills.sh                    # Linux/macOS
bash scripts/install-skills.sh --list
bash scripts/install-skills.sh --skill tdd,mao-methods --dry-run
```

- 默认只装生产层（engineering + productivity）；`in-progress` 要显式 `-Skill` / `-Layer` 点名；`deprecated` 永不安装。
- `-All` 装除 deprecated 外的全部层；`-DestDir` / `--dest` 换目标目录（默认 `~/.agents/skills`）。
- 重复运行 = 覆盖更新。

### 2. 用 skills CLI 装（只覆盖随库提交的自研技能）

```bash
npx skills@latest add beierweiwei/my-skills
npx skills@latest add beierweiwei/my-skills --skill=mao-methods
npx skills@latest update mao-methods
```

上游技能在本库是 junction、不入库，`npx skills add` 看不到；装上游走第 1 种，或用上游仓库地址。

### 3. 更新上游快照

```powershell
powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1   # 加 -NoProxy 直连
```

```bash
bash scripts/update-skills.sh                                        # 加 --no-proxy 直连
```

## 接入各工具

- **技能**：`scripts/install-skills.*` 装到 `~/.agents/skills/`；Claude Code 另加 `-DestDir "$HOME\.claude\skills"`
- **全局指令**：将 `AGENTS.md` 链接到 `~/.agents/AGENTS.md` 和 `~/.config/opencode/AGENTS.md`，将 `CLAUDE.md` 链接到 `~/.claude/CLAUDE.md`
- **MCP**：`mcp/mcp.json` 内容合并进 `~/.claude.json` 的 `mcpServers`，并在环境变量设置 `EXA_API_KEY`、`ZREAD_API_TOKEN`
- **插件**：把 `plugins/*` 登记到 Claude Code（`/plugin`），或直接使用官方 marketplace 安装

## 技能清单

### 自研（源在 `repos/local/`，随库提交）

- **engineering**：experience-kb-init
- **productivity**：grill-the-goal
- **in-progress**：mao-methods、mao-collaboration

分隔说明见各层 README：`skills/engineering/README.md`、`skills/productivity/README.md`、`skills/in-progress/README.md`、`skills/deprecated/README.md`。

### 上游（junction，由 update-skills 脚本刷新）

| 上游仓库 | 技能 |
|---|---|
| [mattpocock/skills](https://github.com/mattpocock/skills) | ask-matt, code-review, codebase-design, diagnosing-bugs, domain-modeling, grill-with-docs, implement, improve-codebase-architecture, prototype, research, resolving-merge-conflicts, setup-matt-pocock-skills, tdd, to-spec, to-tickets, triage, wayfinder, wizard, grill-me, grilling, handoff, teach, to-questionnaire, wait-what, writing-for-agents |
| [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | ponytail, ponytail-audit/debt/gain/help/review |
| [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) | caveman |
| [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | ui-ux-pro-max |
| [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files) | planning-with-files |
| [microsoft/playwright-cli](https://github.com/microsoft/playwright-cli) | playwright-cli |
| [wshobson/agents](https://github.com/wshobson/agents) | e2e-testing-patterns |
| [humanlayer/skills](https://github.com/humanlayer/skills) | show-me |
| [anthropics/skills](https://github.com/anthropics/skills) | frontend-design |
| [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) | plugins: claude-code-setup, claude-md-management, code-review, commit-commands, feature-dev, hookify, ralph-loop, typescript-lsp |

## 已淘汰（勿再恢复）

- MCP：**gitnexus**（过时）、**github**（已禁用）
- 技能：computer-use / orca-cli / orchestration（上游已删除）→ 见 `skills/deprecated/README.md`
- SempFlow 项目专属技能留在 SempFlow 仓库 `.qoder/skills/`，不入本库

> 注意：mcp.json 中密钥一律走环境变量；若日后把本库推到远端，确认不含任何明文密钥。
