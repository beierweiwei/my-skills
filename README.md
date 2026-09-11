# my-agents-settings

个人 AI Coding Agent（Codex / Claude Code / Qoder）跨工具的统一配置库。

## 结构

```
├── AGENTS.md               # 统一全局指令（Codex/Qoder 直接读；CLAUDE.md @引用本文件）
├── CLAUDE.md               # 一行指针 → AGENTS.md
├── mcp/mcp.json            # MCP 服务器配置（密钥用环境变量占位）
├── skills/
│   ├── local/              # 非开源/无上游的技能，本地快照，手工维护
│   └── <34 个开源技能>      # junction 指向 repos/，由脚本管理，勿直接改
├── plugins/                # Claude 官方插件（junction），由脚本管理
├── repos/                  # 脚本的克隆缓存（浅克隆，可随时删除重建）
└── scripts/
    ├── update-skills.ps1      # Windows
    └── update-skills.sh       # Linux/macOS
```

## 用法

```powershell
# 拉取/更新全部开源技能与插件到最新（幂等，可反复跑）
powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1
# 代理不可用时直连
powershell -ExecutionPolicy Bypass -File scripts/update-skills.ps1 -NoProxy
```

```bash
# Linux/macOS
bash scripts/update-skills.sh
# 代理不可用时直连
bash scripts/update-skills.sh --no-proxy
```

接入各工具：

- **技能**：把 `skills/*` 链接/复制到 `~/.agents/skills/`（Claude 的 `~/.claude/skills/` 已是指向它的符号链接）
- **全局指令**：将 `AGENTS.md` 链接到 `~/.agents/AGENTS.md` 和 `~/.config/opencode/AGENTS.md`，将 `CLAUDE.md` 链接到 `~/.claude/CLAUDE.md`
- **MCP**：`mcp/mcp.json` 内容合并进 `~/.claude.json` 的 `mcpServers`，并在环境变量设置 `EXA_API_KEY`、`ZREAD_API_TOKEN`
- **插件**：把 `plugins/*` 登记到 Claude Code（`/plugin`），或直接使用官方 marketplace 安装

## 开源技能来源（脚本 manifest 同源）

| 上游仓库 | 技能 |
|---|---|
| [mattpocock/skills](https://github.com/mattpocock/skills) | ask-matt, code-review, codebase-design, diagnosing-bugs, domain-modeling, grill-with-docs, implement, improve-codebase-architecture, prototype, research, resolving-merge-conflicts, setup-matt-pocock-skills, tdd, to-spec, to-tickets, triage, wayfinder, wizard, grill-me, grilling, handoff, teach, to-questionnaire, wait-what, writing-for-agents |
| [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | ponytail, ponytail-audit/debt/gain/help/review |
| [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman) | caveman |
| [nextlevelbuilder/ui-ux-pro-max-skill](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) | ui-ux-pro-max |
| [OthmanAdi/planning-with-files](https://github.com/OthmanAdi/planning-with-files) | planning-with-files |
| [anthropics/claude-plugins-official](https://github.com/anthropics/claude-plugins-official) | plugins: claude-code-setup, claude-md-management, code-review, commit-commands, feature-dev, frontend-design, hookify, ralph-loop, typescript-lsp |

## 本地快照技能（skills/local/，非开源或无可靠上游）

- **bailian-\***（5 个）：阿里云百炼官方技能（bailian-cli / finetune / gen / managed-agent / protocol）
- **playwright-cli**：浏览器自动化 CLI 技能
- **show-me**：可视化讲解
- **grill-the-goal**：grilling 的目标层对抗盘问（自研扩展）
- **demo-dataset-builder / experience-kb-init**：SempFlow 通用工具技能
- **typescript-reviewer / code-refactor / e2e-testing-patterns / python-testing / frontend-design**：审查/重构/测试/设计模式技能
- **learned**：个人经验笔记

## 已淘汰（勿再恢复）

- MCP：**gitnexus**（过时）、**github**（已禁用）
- 技能：computer-use / orca-cli / orchestration（符号链接已断，上游删除）
- SempFlow 项目专属技能留在 SempFlow 仓库 `.qoder/skills/`，不入本库

> 注意：mcp.json 中密钥一律走环境变量；若日后把本库推到远端，确认不含任何明文密钥。
