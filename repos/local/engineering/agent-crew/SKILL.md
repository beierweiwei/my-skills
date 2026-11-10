---
name: agent-crew
description: 多 agent 编排体系一键启用与日常编排（Herdr 运行时 + 角色船员 + plan 工作记忆 + 派生看板）。Use when 需要在目标项目启用多 agent 协作编排、初始化角色与总舵、拆任务派活收口、维护 portfolio 多流 plan、生成进度看板，或搭建 Captain/总舵/大副/船员分工体系；触发词：编排、总舵、大副、副舵、船员、crew、herdr、派活、看板、portfolio。
version: 2.1.7
---

# agent-crew：多 Agent 编排体系（v2.1）

把「一人 + 多 Agent 船员」的协作编排放进目标项目：7 个核心角色的技能基类（Qoder / opencode 两套 runtime）、唯一 dispatcher、Herdr 运行时封装、plan 工作记忆与派生看板、安装/升级/体检脚本。安装后项目只保留配置、指针与项目自有文件；通用规则/验收/机制只在技能源一份。

**读哪里**：规则正文 `references/crew-rules.md`（D/R/W/O/F/P/G/S/X）；验收与呈报 `references/crew-acceptance.md`（A1–A5）；机制与拓扑 `references/orchestration-guide.md`；plan 纪律 `references/plan-discipline.md`；Herdr 速查 `references/herdr-cheatsheet.md`。角色基类在 `templates/agents/<runtime>/`；项目不复制这些正文。

## 何时用 / 不适用

- **用**：一个仓库里要多 Agent 分工（实现/评审/调研/杂活）+ 人工门 + 进度看板；新项目从零启用，或既有项目接入编排。
- **不用**：单 Agent 能完成的简单任务；只需要个人工作流、没有多角色分工的场景。

## 启用（≤3 步）

| 步 | 谁操作 | 做什么 | 成功判据 |
|---|---|---|---|
| 1 | Captain（一次性） | 安装 my-skills（`bash scripts/update-all.sh`，已装则跳过） | 技能目录出现本 `SKILL.md` |
| 2 | Captain → 对主 agent 说：「在本项目启用 agent-crew 编排」 | agent 执行 `python3 <skillRoot>/scripts/init.py`；需要定制先改 `.planning/orchestration.json` 再重跑 | init 逐产物标注新建/跳过/冲突；无 ERROR |
| 3 | Captain：「启动总舵」 | agent 执行 `bash <项目>/scripts/start-helm.sh`（内部走 dispatch） | Herdr 出现总舵 tab，总舵 agent 就绪并首报 |

`init` 的行为契约：

- **只读检测**：git/worktree、既有角色目录、脚本、`.planning/`、`AGENTS.md`、术语文档、python3 版本。
- **写入清单内**：`.planning/orchestration.json`、项目角色 stub（仅在缺失时；发现字段 + `extends: skill:<role>` + `managed:false`）、受管脚本、`docs/crew/AGENTS-block.pending.md`（AGENTS 无标记时）、portfolio 三文件、AGENTS 受管块、术语文档骨架。
- **不写**：规则/验收/机制/plan 纪律副本；第二份项目角色文件；组合产物。
- **AGENTS**：标记块外永远不动；无标记零写入并返回 2；多对/嵌套/围栏内/缺半 → fail closed。
- **幂等**：一致则跳过；已存在且不一致默认不覆盖并列冲突；`--force` 仅限 `kind:file` 受管文件。

## 角色与继承（v2.1 核心）

| 核心角色 | capabilities | 默认 lifecycle | Qoder | opencode |
|---|---|---|---|---|
| helm | read, dispatch | persistent | 基类 | `mode: primary` |
| firstmate | read, write-docs, dispatch | persistent | 基类 | `mode: primary` |
| builder | read, write-code, run-commands | keep-until | 基类 | `mode: primary` |
| planner | read, write-docs | one-shot | 基类 | `mode: primary` |
| reviewer | read, report | one-shot | 基类（`disallowedTools: [Write, Edit]`） | `mode: primary` |
| runner | read, run-commands | one-shot | 基类 | `mode: primary` |
| scout | read, report | one-shot | 基类 | `mode: primary` |

- **角色 = 技能基类 + 项目角色文件**：项目文件只放发现字段 + 一个 `crew-role` 描述符（`extends: skill:<role>`、`managed:false`）+ 项目自有正文；运行权限/frontmatter 只取基类。
- **append-only 组合**：dispatch 启动时 base 先、项目后拼接；不合并、不替换、不删 base；受保护标题（`## 引用` 等）冲突、残留 token、项目 frontmatter 权限键 → 拒绝启动（rc=1）。
- **升级传播**：只改技能基类；下次 dispatch 自动生效，项目文件无需改动。项目可在 `.qoder/agents/custom/<id>.md`（Qoder）或 `.opencode/agent/custom-<id>.md`（opencode）追加自定义角色，能力为基类子集，禁止 `dispatch`/`merge`/`push`。
- **组合产物**不写项目发现目录，只落 `<git-common-dir>/crew/compose/<run-id>/`（可丢弃）。

## 脚本与唯一入口

| 命令 | 作用 |
|---|---|
| `python3 scripts/dispatch.py compose/start/prompt/close/status/list/tab/pane` | **唯一** Agent 生命周期与工位入口；`start-*.sh` 只是薄包装 |
| `python3 scripts/init.py [--check\|--dry-run\|--yes\|--refresh-block --confirm]` | 安装、bootstrap、AGENTS 受管块重渲染 |
| `python3 scripts/upgrade.py --diff\|--apply --confirm\|--rollback <id>` | 受管脚本升级与逐字节回滚 |
| `python3 scripts/doctor.py --root . --profile release --strict --json` | 只读体检：manifest/AGENTS/指针/角色/发现/运行时 |
| `python3 scripts/crew_paths.py --print` | 状态目录单一解析（git common dir / 非 git 回退） |
| `python3 scripts/plan-status.py --write\|--check-fresh\|--selftest\|--lint` | 派生看板与 plan 结构校验 |
| `python3 scripts/crew-state.py` / `crew-msg.py` / `crew-ticket.py` / `crew-guard.py` | 状态机 / 回执 / 占用与收口 / 值守叫醒 |
| `python3 scripts/pane-audit.py` | 工位巡检（dry-run 默认，`--apply` 才关） |
| `python3 scripts/crew-lint.py --root <技能仓> [--purity]` | 技能资产自检（行数/编号/指针/重复句/纯净度）；不安装到项目 |

- 禁止在角色文件、项目文档、项目脚本里直写 `herdr agent start/prompt/close`、`herdr tab/pane` 变更或 runtime `--agent` 直启；只读 `agent list`/`tab list`/`pane list`/`status` 允许。
- 退出码统一：0 成功；1 校验/门禁拒绝；2 待人工；3 环境缺失。

## 项目侧布局（v2.1）

```text
<project>/
├── AGENTS.md                    # 项目自有；最多一个受管块
├── docs/crew/{manifest.json,install-log.jsonl,AGENTS-block.pending.md,project-ext/}
├── docs/orchestration/topology.md
├── .planning/{orchestration.json,portfolio/,<task>/}
├── .qoder/agents/<role>.md + custom/<id>.md
├── .opencode/agent/<role>.md + custom-<id>.md
└── <git-common-dir>/crew/       # 运行时状态，可丢弃
```

项目内**不应存在** `docs/crew-rules.md`、`docs/crew-acceptance.md`、`orchestration-guide.md`、`plan-discipline.md` 等技能源副本；AGENTS/角色只用 `{{skill_root}}` 与技能 references 指针。

## 日常编排导航

| 要做什么 | 谁做 | 去哪里 |
|---|---|---|
| 判定请求去向、拆流、派副舵、聚合汇报 | 总舵 Helm | 项目 `.qoder/agents/helm.md` 或 `.opencode/agent/helm.md` |
| 拆任务、派活、监听、收口、三道门 | 大副 First Mate | `.opencode/skills/firstmate-dispatch/SKILL.md`（SOP 全文） |
| 实现 / 评审 / 写文档 / 调研 / 杂活 | 各 crew | 对应角色文件（项目发现目录） |
| 启动 / 提示 / 关闭 / 工位 | 编排者 | `dispatch.py`（唯一入口） |
| 记进度、记决策、记错误 | plan owner | portfolio 三文件 + 各流 plan |
| 看全局进度 | Captain / 编排者 | `plan-status.py --write` 生成的看板（勿手改） |
| 体检与证据 | 编排者 / 验收门 | `doctor.py --json` + `.planning/<task>/audit/` |
| 查规则与验收正文 | 所有角色 | `{{skill_root}}/references/crew-rules.md`、`crew-acceptance.md`（项目内用指针） |
| 工位回收 / 批量巡检 | 各段 owner / 副舵核对 | `orchestration-guide.md` §5.10；`pane-audit.py` |
| 流程问题上报 | 任何角色 | `{{portfolio_dir}}/feedback.md` |

## 环境与已知边界

| 异常/边界 | 检测 | 提示与下一步 |
|---|---|---|
| herdr 未安装 | `command -v herdr` 失败 | 先安装 Herdr（见 herdr-cheatsheet 开局）；init 不阻断，start 阻断 |
| herdr server 未运行 | `herdr status` 非 running | 终端执行 `herdr` 打开 TUI 后重试 |
| agent 名撞名 | dispatch 启动前 `herdr agent list` 预检 | 三选一：关闭旧 agent / 改 `projectId` / 改自定义 alias；不自动改名 |
| qodercli 缺失 | `command -v qodercli` 失败 | 确认安装与 PATH；或先用 opencode 栈 |
| opencode 角色未加载 | `opencode agent list` 无该角色 | 确认项目角色文件与 `mode: primary`；改完重启会话（不热重载） |
| 目标仓非 git | `git rev-parse` 失败 | 监听类脚本不可用；可 `git init` 后重跑 init，plan-status 不受影响 |
| python3 < 3.10 | 版本检测 | 升级 python3 后重跑 |
| 既有项目结构差异 | init 检测 | 缺目录自动创建；文档名/路径可在配置里映射 |
| Windows | 平台检测 | Herdr Windows 为 beta；建议 Linux/macOS |

## 提交与发布边界

- 本技能是项目基础设施安装器；init 只写清单内路径，清单外零写入。
- 原始数据不全量读取；init/upgrade/doctor/dispatch 不读取、不迁移业务数据，不执行项目脚本。
- 合并/推送/worktree 清理/采购/合规/主数据口径/门禁签认属 Captain 决策，Agent 不得自批。
- 版本与变更：`SKILL.md` frontmatter 的 `version`（`crew-lint.py --version` 可解析）与 `CHANGELOG.md` 同步；安装版记录在项目 `docs/crew/manifest.json`。
