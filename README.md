# my-agents-settings

个人 AI Coding Agent（Codex / Claude Code / Qoder）跨工具的统一配置库。

## 结构

```
├── AGENTS.md               # 统一全局指令（Codex/Qoder 直接读；CLAUDE.md @引用本文件）
├── CLAUDE.md               # 一行指针 → AGENTS.md
├── mcp/mcp.json            # MCP 服务器配置（密钥用环境变量占位）
├── skills/<层>/<技能>/      # 技能库，按层组织；全部是指向 repos/ 的 junction（脚本生成，不入库）
│   ├── engineering/        # 生产层：工程实践（默认安装）
│   ├── productivity/       # 生产层：通用工作流（默认安装）
│   ├── in-progress/        # 试验层：默认不装，需点名
│   └── deprecated/         # 已淘汰：永不安装，只留记录
├── plugins/                # Claude 官方插件（junction），由脚本管理
├── repos/                  # 上游仓库浅克隆缓存（可随时删除重建）
│   └── local/              # 自研技能源（随库提交，勿删）
└── scripts/
    ├── install-skills.ps1 / .sh   # 选择安装技能到 Agent 技能目录；技能自带 scripts/setup.* 时顺带运行其自举
    ├── link-agents.ps1 / .sh      # 把全局指令（AGENTS.md/CLAUDE.md）链接接入各 Agent 全局位置
    └── update-skills.ps1 / .sh    # 首次初始化/更新技能库：拉取上游 + 刷新 skills/、plugins/ 链接
```

## Agent 约定（本仓库内的工作提示，不随 link-agents 全局分发）

- 根 `AGENTS.md` 是**全局共享面**：只放跨机器/跨项目通用的沟通、编码守则、网络、统一记忆约定；本仓库自身的工作约定只住在本 README，不进 AGENTS.md（否则会被链到所有机器所有项目，出了本库全是死引用）。
- 全局指令接入：`scripts/link-agents.*` 把根 AGENTS.md / CLAUDE.md 链接到各 Agent 全局位置（agents 约定、Claude Code、Codex、Qoder 国内版、opencode、ZCode），改根 AGENTS.md 即对所有 Agent 生效。
- 技能按层放在 `skills/<层>/<技能>/`：`engineering`、`productivity` 生产层（默认安装），`in-progress` 需点名，`deprecated` 永不安装；全部是指向 `repos/` 的 junction，由 `scripts/update-skills.*` 生成（新克隆机器先跑一次）。上游技能源在 `repos/<仓库>/`（改进提 PR 上游）；自研源在 `repos/local/<层>/<技能>/`（随库提交，本地定制改这里）。
- 技能自带 `scripts/setup.*` 时（如 inone-memory 配置凭据/装全局命令/拉记忆分发），`install-skills.*` 装完自动运行；`-SkipSetup` / `--skip-setup` 跳过，事后手动重跑幂等。
- 生成 spec（to-spec）前：先读 [notes/to-spec-patches.md](notes/to-spec-patches.md) 的两条补丁。

## 安装技能

> 首次使用（新克隆的机器）：先运行一次 `scripts/update-skills.*` 生成 `skills/` 下的技能链接（自研与上游技能），否则技能库为空，安装脚本无从选择。

### 1. 从本库选择安装（自研 + 上游都可）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1        # 检测已装 Agent，生产层全部装到各 Agent 的 skills/
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -List  # 列出技能与各目标目录的安装状态
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Agent claude  # 只装到指定 Agent
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Skill tdd,mao-methods
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Layer engineering -DryRun
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -DestDir "$HOME\.claude\skills"  # 显式指定单目录（优先于 Agent 检测）
```

```bash
bash scripts/install-skills.sh                    # Linux/macOS，检测已装 Agent 全部安装
bash scripts/install-skills.sh --list
bash scripts/install-skills.sh --agent claude
bash scripts/install-skills.sh --skill tdd,mao-methods --dry-run
```

- 默认只装生产层（engineering + productivity）；`in-progress` 要显式 `-Skill` / `-Layer` 点名；`deprecated` 永不安装。
- `-All` 装除 deprecated 外的全部层；`-DestDir` / `--dest` 换目标目录（默认 `~/.agents/skills`）。
- 技能自带 `scripts/setup.*` 时装完自动运行自举（如 inone-memory 配置凭据/装全局命令 inone/拉取记忆分发）；`-SkipSetup` / `--skip-setup` 跳过，事后手动跑也幂等。
- 重复运行 = 覆盖更新。

### 2. 用 skills CLI 装（只覆盖随库提交的自研技能）

```bash
npx skills@latest add beierweiwei/my-skills
npx skills@latest add beierweiwei/my-skills --skill=mao-methods
npx skills@latest update mao-methods
```

上游技能在本库是 junction、不入库，`npx skills add` 看不到；装上游走第 1 种，或用上游仓库地址。

### 3. 初始化 / 更新技能库

首次克隆后必须先运行一次（生成 `skills/` 下的技能链接）；之后按需更新上游快照。

```powershell
powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1   # 加 -NoProxy 直连
```

```bash
bash scripts/update-skills.sh                                        # 加 --no-proxy 直连
```

## 接入各工具

- **技能**：`scripts/install-skills.*` 默认检测已装 Agent（基础目录存在即视为已装，规则同 `link-agents.*`：~/.agents、~/.claude、~/.codex、~/.qoder-cn、~/.config/opencode、~/.zcode），生产层装到全部已装 Agent 的 `skills/` 子目录；`-Agent claude` / `--agent claude` 只装指定 Agent（未检测到的警告跳过）；`-DestDir` / `--dest` 显式指定单目录、优先于 Agent 检测。ZCode 同时也扫描 `~/.agents/skills`，装一处即可见
- **全局指令**：运行 `scripts/link-agents.*` 接入（未装的 Agent 自动跳过，已有实体文件先备份再链接），把 `AGENTS.md` / `CLAUDE.md` 链接到：`~/.agents/AGENTS.md`（通用约定）、`~/.claude/CLAUDE.md`（Claude Code，指向仓库 CLAUDE.md → @AGENTS.md）、`~/.codex/AGENTS.md`（Codex）、`~/.qoder-cn/AGENTS.md`（Qoder 国内版）、`~/.config/opencode/AGENTS.md`（opencode）、`~/.zcode/AGENTS.md`（ZCode）。改仓库文件即对所有 Agent 生效。
- **MCP**：`mcp/mcp.json` 内容合并进 `~/.claude.json` 的 `mcpServers`，并在环境变量设置 `EXA_API_KEY`、`ZREAD_API_TOKEN`
- **插件**：把 `plugins/*` 登记到 Claude Code（`/plugin`），或直接使用官方 marketplace 安装

## 技能清单

### 自研（源在 `repos/local/`，随库提交）

- **engineering**：experience-kb-init
- **productivity**：grill-the-goal、inone-memory（统一记忆推送/检索；自带 setup 自举，上游 = inone 仓库蒸馏母本 `deploy/12-memory-hub/skills/_shared/`，母本修订后同步本库）
- **in-progress**：mao-methods、mao-collaboration

分隔说明见各层 README：`skills/engineering/README.md`、`skills/productivity/README.md`、`skills/in-progress/README.md`、`skills/deprecated/README.md`。

### 上游（junction，由 update-skills 脚本刷新）

| 上游仓库 | 技能 |
|---|---|
| [mattpocock/skills](https://github.com/mattpocock/skills) | ask-matt, code-review, codebase-design, diagnosing-bugs, domain-modeling, grill-with-docs, implement, improve-codebase-architecture, prototype, research, setup-matt-pocock-skills, tdd, to-spec, to-tickets, triage, wayfinder, wizard, grill-me, grilling, handoff, teach, to-questionnaire, wait-what, writing-for-agents |
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
