# Changelog

## 2.1.3 — 2026-11-10

### 修复

- `doctor.py` 的 `skill.source`：同名技能源候选先做 git-ignored 判定（`git -C <root> check-ignore -q -- <rel>`，rc=0 跳过）；被 `.gitignore` 忽略的外部技能源（如项目内只读 `skill-src/`）不再误判为项目副本；非 git 仓/未忽略/git 不可用时维持原判定，不硬编码目录名。
- `crew-guard.py` 的 `acquire_lock`：拿到 flock 后把当前 pid 写入 `guard.lock`（seek/truncate 后写 pid+换行并 flush），供 doctor 识别存活 guard。
- `doctor.py` 的 `runtime.lock`：`guard.lock` 解析到存活 pid 时判 PASS（detail=guard 运行中 pid），无 pid/死 pid 仍 WARN stale；其他 `*.lock`（init/upgrade 等）活锁 FAIL / stale WARN 语义不变。

## 2.1.2 — 2026-11-10

### 修复

- BUG-009：`crew-guard.py` 的 blocked 叫醒排除测试探针——agent 名以 `s9-`/`probe-`/`test-` 开头（不区分大小写）或 `cwd` 位于 `/tmp` 下时不参与 blocked 判定；真实 blocked worker 仍叫醒，探针的 `working` 状态仍计入在跑。

## 2.1.1 — 2026-11-10

### 修复

- `doctor.py` 的 `manifest.orphan` 消费 `hooks.projectScripts[].path`：已登记项目脚本不再报孤儿；文件缺失仍由 `hooks.scripts` 报错，orphan 不重复 fail。
- `doctor.py` 的 `role.runtime-direct` 仅扫描 AGENTS、`docs/orchestration/**`、`docs/crew/**`、角色文件与 `scripts/custom/**`；`docs.links` 排除 `.venv`/`site-packages`/`node_modules` 与 `docs/历史/**`，避免第三方依赖和归档文档产生误报。
- `doctor.py` 的 `skill.source` 跳过 `.venv`/`site-packages`/`node_modules` 内的同名文件，避免把第三方依赖误判为技能源/规则副本。

## 2.1.0 — 2026-11-09（v2.1）

### 新增

- **唯一 dispatcher**：`scripts/dispatch.py` 提供 compose/start/prompt/close/status/list/tab/pane；`start-agent.sh`/`start-helm.sh` 改为薄包装。init/upgrade 安装 managed 组合基础代理（Qoder `.qoder/agents/crew/<role>.md`、opencode `.opencode/agent/crew-<role>.md`）；dispatch 用 `--agent crew-<role>`，agent-log 记录 `composedSha256`/`delivery`/`composedPath`/`baseRef`。
- **角色继承与组合**：技能基类（`templates/agents/qoder|opencode/*.md`，各 7 个）+ 项目角色文件 `extends: skill:<role>`；append-only 组合、frontmatter/权限只取基类、受保护标题与残留 token fail-closed；组合产物只落 `<git-common-dir>/crew/compose/`。
- **双栈补全**：opencode 7 个核心基类全部 `mode: primary`（helm/firstmate/builder/planner/reviewer/runner/scout），frontmatter `permission` 由基类提供；qoder 7 个基类补 `crew-role` 基类描述符（`extends:null`）。
- **体检与安装**：`scripts/doctor.py` 只读体检（manifest/AGENTS/指针/角色/发现/运行时，JSON + 退出码 0/1/2/3）；`scripts/init.py` 安装/升级/bsp 行为契约（stub 仅缺失生成、AGENTS pending、`--refresh-block --confirm`）；`scripts/upgrade.py` 受管脚本升级与逐字节回滚；`scripts/crew_paths.py` 状态目录单一解析。
- **普通技能引用**：通用规则/验收/机制/plan 纪律只在技能源 `references/` 一份；项目 AGENTS/角色用 `{{skill_root}}` 指针，项目不复制副本。
- **AGENTS 受管块瘦身**：只放通用动作与指针（dispatch/init/upgrade/doctor/`{{skill_root}}`），不复制规则正文；≤150 行、恰好一对标记。
- **项目模板**：`templates/project/` 提供 topology 骨架、project-ext 说明、AGENTS pending 模板（三步插入指引）。
- **资产自检**：`crew-lint.py` 增加 opencode 7 角色行数预算与 `--purity`（绝对路径 + 维护者本地未跟踪 denylist；项目名/业务词不进入技能发布物）。

### 变更

- 版本升至 `2.1.0`；角色文件只保留发现字段与描述符，权限/工具字段一律由技能基类提供。
- `templates/AGENTS-编排节.md` 标记改为精确的 start/end 一对；块内不再承载规则/验收正文。

### 迁移

- 既有项目：在角色文件补 `extends: skill:<role>` 与 `managed:false`，删除 frontmatter 的 `permission`/`tools`/`disallowedTools`；与基类同名的 H2–H6 标题改为项目独有名称。
- `skillRoot`/版本变化后执行 `init --refresh-block --confirm`；升级前跑 `doctor.py` 留证，升级后复跑 `manifest.*`/`agents.*`/`role.*` 检查。

## 2.0.0 — 2026-11-09

### 新增

- **轻量状态机**：`crew-state.py`（任务/工位状态、事件日志、幂等迁移、`recover`）与稳定性八条验收。
- **八类优化落地**：
  1. 监听值守 → `crew-guard.py`（单实例、两轮去抖、正常零输出）；
  2. 派单与回执通道 → `crew-msg.py`（文件回执为真源，Qoder/opencode 推送）；
  3. 工位管理 → `pane-audit.py`（≤3 pane、孤儿 pane/registry 巡检）；
  4. 收口、提交与并发 → `crew-ticket.py`（占用、暂存、precommit、close）；
  5. 验收与可验收态 → `crew-acceptance.md`（逐节点抽验、关键交互物自验）；
  6. 看板、plan 与汇报 → `plan-status.py` 新鲜度检查与结构 lint；
  7. 单一真源与发布 → `crew-rules.md` 唯一正文、模板/角色编号指针化；
  8. 轻量状态机 → 事件日志 + 可重建派生状态。
- **规则与验收唯一正文**：`references/crew-rules.md`（D/R/W/O/F/P/G/S/X）、`references/crew-acceptance.md`（A1–A5）；安装副本落在项目 `docs/crew-rules.md`、`docs/crew-acceptance.md`。
- **技能资产自检**：`crew-lint.py`（行数预算、规则编号引用、指针写法、跨文档重复句）。
- **版本化**：`SKILL.md` 增 `version`；本 CHANGELOG；init 版本戳与 `--check`、`--only-failed`。

### 破坏性变更

- 模板/角色中的规则引用改为 `{{crew_rules}}` / `{{crew_acceptance}}` token；裸 `references/crew-rules.md` / `references/crew-acceptance.md` 不再作为可渲染指针（由 `crew-lint.py` 拦截）。
- v1 安装产物需重跑 init 重新渲染；工位/任务状态与回执改由 v2 脚本维护。

### 迁移

- 升级前备份 `.qoder/agents/`、`.opencode/`、`AGENTS.md`、`scripts/` 与 `.planning/`；
- 重跑 init（`--force` 仅限本技能产物路径；`--check` 先核对源版与安装版，`--only-failed` 重跑失败项）；
- 异常时用备份回滚脚本恢复，不删除客户既有产物。
