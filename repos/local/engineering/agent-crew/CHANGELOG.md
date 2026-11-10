# Changelog

## 2.2.7 — 2026-11-10

### 新增

- **A 交接摘要 `plan-status.py --handover`**：只读输出 ≤40 行交接摘要（Goal/Next Step/活跃流含 plan 引用与当前阶段/待 Captain/最近 3 条 progress 与完成/证据入口/看板新鲜），progress 只读尾 16KB（不足 3 条时扩 64KB→全文），不写任何文件；空 portfolio/缺文件给可读降级。
- **B progress 轮转 `plan-status.py --rotate`**：默认 dry-run 零写入，`--apply --keep N`（默认 10）才落盘；条目=`## 自动驾驶里程` 节内 `- YYYY-MM-DD` 块（含缩进续行），保留最近 N 条 + 尾部非条目区块，其余按原序归档 `.planning/_archive/progress-<YYYYMMDD>.md`（头含来源/日期/截止边界/before-after 行数与字节/源 sha256/操作角色）；live 尾部加归档指针；flock 非阻塞（冲突 rc=2）+ 临时文件 + fsync + `os.replace`；只归档不删除、可 `git revert`。无 `## 自动驾驶里程` 节时按日期前缀兜底并在 dry-run 提示。
- **C guard 交接前置归档**：`crew-guard.py` 的 `_start_handoff` 在创建继任 tab/pane **之前**执行 `plan-status.py --rotate --apply --keep 10`（`ROTATE_KEEP` 常量）；成功记日志，失败/异常只告警，不阻断交接、不回落 split；`handoff_attempted` 保证多轮只执行一次；新增 `run_plan_status_args` 按 argv 列表传多参数。
- **D 交接规则瘦身**：qoder/opencode helm 模板新增「交接读取范围（硬）」（看板 + `--handover` 摘要 + 活跃流 Next Step/Status grep + feedback 待复盘 grep + `guard.handoff`；禁整读 progress/task_plan/feedback/历史流 plan/共识，单文件 >20KB 不整读）；`plan-discipline` §5.1–5.2 改为交接流程内 `--rotate` 归档，阈值 progress ≥40KB 或条目 ≥100，替换未内置的 `plan-archive.py` 口径；`firstmate-dispatch` 恢复/交接顺序同步。
- **`--write` 体量告警**：portfolio progress ≥40960 字节或 ≥100 条时 stderr 提示「建议交接时 --rotate」，stdout/看板不变。

### 修复

- interrogate A/B 复核收口：`--handover` 尾窗兜底遇 `## ` 标题停止计条目（digest 日期行不再冒充 progress，告警计数同口径）；guard 继任 prompt 改为「交接读取范围（硬）」不再要求整读三文件；rotate 空转记「无需轮转（零写入）」、`--write` 的 rotate 建议进 guard.log、`--apply` 缺 `--rotate` rc=1、`_atomic_write` 保留目标权限、apply 先取 flock 再算计划、归档重试按「sha256+截止边界」去重不重复追加；`plan-discipline` §5.2 补同日分段说明。

### 测试

- 新增 `test_plan_status_handover_rotate.py`（29 例）：摘要行数与分块、只读尾部、空仓降级、超量截断、digest 日期行不冒充 progress；rotate dry-run 零写入、归档头、指针、守恒、幂等、锁冲突 rc=2、rename 失败 live 不变且清理 temp、fsync、权限保留、重试去重、digest `- ` 行不计条目、无节兜底、超长尾条目扩窗、`--apply` 缺 `--rotate` 拒绝。
- `test_crew_guard.py` 新增 12 例：rotate 先于 tab create、成功日志、失败/异常继续交接、两轮只 rotate 一次、warn 轮不 rotate、空转记「无需轮转」、生产 argv 分元素传递、继任 prompt 新读取集、`--write` 体量告警入 log。
- 全量 pytest 546 passed + 99 subtests；`crew-lint`/`plan-status --lint`/`--playbooks` 绿。
- 版本 2.2.7：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.6 — 2026-11-10

### 修复

- **T15 doctor runtime.lock 空 `occupancy.lock` 误报**：`crew-ticket.py` 用 `open(<crew>/occupancy.lock, "a+")` + `fcntl.flock` 做互斥，锁文件空闲时内容恒为空，删除文件反而引入竞态；`check_runtime_lock()` 之前把所有空锁按「无法识别 pid」计入 stale warn。现在空 `occupancy.lock` 视为空闲 flock 目标跳过，不计 stale；其他空锁（`guard.lock` / `init.lock` / `upgrade.lock` 等）保持原判 warn，不放宽。只有「读成功且内容为空/纯空白」的 `occupancy.lock` 放行；读失败、目录、悬空 symlink 等非普通文件仍按 stale warn，不因「空内容放行」而假绿。无 guard pid 时终态返回语义正确的 pass「无活跃锁（occupancy.lock 为空闲锁文件）」，不再出现 `guard 运行中（pid=None）`；无任何锁仍「无锁文件」，guard 活 pid 仍「guard 运行中（pid=…）」。

### 测试

- `test_doctor_runtime.py` 新增：仅空 `occupancy.lock` → pass 且 detail 不含 `stale`；空 `occupancy.lock` + 空 `init.lock` → warn 且只列 `init.lock`；活 `guard.lock` + 空 `occupancy.lock` → pass「guard 运行中（pid=…）」；`occupancy.lock` 为目录/悬空 symlink（读失败）→ warn 且 detail 含 `occupancy.lock`；既有空 `guard.lock` → warn「无法识别 pid」保持通过。
- 版本 2.2.6：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.5 — 2026-11-10

### 修复

- **BUG-025（F1）strict rc 文档口径**：2.2.1 条目末句改为「默认 profile 不升级为失败（rc 不变）；`--strict` 下按既有语义将 warn 计为失败（rc=1）」；strict 语义与代码均未改，补 dev-worktree fixture 下 strict/非 strict rc 断言与本文关键词断言。
- **BUG-026（F2）稳定副本误报开发 worktree**：`doctor.py` / `init.py` 的 `linked_worktree_root` 只把 `.git` 文件解析到 `<common>/worktrees/<name>`（`git worktree add` 形态）时判为开发 worktree；submodule（`modules/`）、`--separate-git-dir` 与其他形态不报，读取/解析失败 fail-soft；补真实 git 正反例测试与两脚本行为一致性断言。
- **BUG-027（F4）不存在 skillRoot 归因**：`init.py --check` 提示块先判 `is_dir()`，不存在时输出「skillRoot 不存在或不可读」+ 可执行 next，不再输出「开发 worktree」归因；rc 语义不变，`~nosuchuser` 不崩溃。
- **T13 pane-audit 自定义总舵标签**：新增 `--root` 读取配置 `helm.tabLabel`（缺失/损坏/字段非法 fail-soft 回落「总舵」），总舵 tab 识别兼容「标签含总舵」；总舵识别优先于 `Captain·`/`流程优化·` 白名单前缀；docstring/`--help` 限定「空 tab（非总舵）--apply 自动关闭」并同步 `--helm-pane` 识别口径。

### 变更

- **NF-001 总舵派单落文件**：qoder/opencode helm 模板改为所有派单一律先落临时文件（不区分长短），统一用 `"$(cat <file>)"` 传递，禁止命令行内联 prompt。
- **NF-002 interrogate 超时覆盖**：`Fan out`/`Verify` 要求派出前声明超时窗口、等待期间不结束回合、超时角度记 coverage gap；`Output` 新增 Coverage gaps；`autonomous-run` Phase 2 追加同口径 checklist。
- **NF-003 herdr-cheatsheet 示例改 dispatch**：§1/§3/§5 的派活与建 tab 示例改走 `dispatch.py`，手工生命周期命令不再作主路径；只读命令与 `send-keys`（仅人工恢复）保留。

### 测试

- 新增 `scripts/tests/test_225_patch.py`（F1 口径、F4 提示、NF-001/002/003 grep 防回退与版本断言）；`test_doctor_config.py` / `test_init_v2.py` 补真实 git submodule 与 `--separate-git-dir` 反例、strict rc 与不存在 skillRoot 用例；`test_pane_audit.py` 补 `helm.tabLabel` 用例。
- 版本 2.2.5：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py` 与 `test_224_retro.py` 版本断言同步。

## 2.2.4 — 2026-11-10

### 新增

- **R5 反馈分诊**：`references/crew-rules.md` 新增 R5（阻断级走回执、repo 犯错记 Errors、跨项目认知走 inone、仅流程问题进 feedback；去向留痕；待复盘未清零=未完成），索引改 R1–R5、领域名「回执与反馈」；14 个角色模板引用区间同步（opencode reviewer 正文回执句不动）。
- **复盘路由**：qoder helm 动作清单第 8 条改为自学习分诊、新增第 9 条复盘（大收口四条定义 + 每周兜底 + 四路归类 + 报告 `portfolio/retro-YYYYMMDD.md` + 批后清条目留去向）；opencode helm 新增第 13 条同等语义；新增报告骨架 `templates/_templates/retro.md`。
- **feedback 分诊表**：`references/plan-discipline.md` §5.3 重写为 9 列表结构、状态/去向枚举、Bug 表冻结与未闭环归并、归档与报告保留；新增骨架 `templates/project/portfolio-feedback.md`。
- **inone 接入**：qoder/opencode firstmate 新增第 11 条（跨项目认知当场 inone，`INONE_AGENT_NAME=firstmate`；船员只报不推）；AGENTS 编排节与 `firstmate-dispatch` §5 补边界。
- **guard 周复盘兜底**：`crew-guard.py` 解析 feedback 复盘信箱与最近 retro 报告，到期且无在跑 worker 时每日最多提醒一次（`retro.wake` 标记），文件/表缺失或行坏只记日志；`init.py` 新增 `retro` 默认配置与 feedback 骨架（缺失才写）；`doctor.py` 登记 `retro` 并轻校验（不新增检查项）。

### 测试

- 新增 `scripts/tests/test_224_retro.py`；`test_crew_guard.py` 追加周复盘到期/每日一次/损坏 fail-safe/文案用例；`test_init_v2.py` 追加 feedback 骨架新建/不覆盖/幂等。
- 版本 2.2.4：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py` 与 `test_220_final.py` 期望同步。

## 2.2.3 — 2026-11-10

### 新增

- **engineering-disciplines 通用技能（跨仓，my-skills 分发）**：单技能六节——断言二元、先磨后干、止损不盲试、运行证实、报告契约、无发现合法 + 同根因合并；每节 3–5 行，`disable-model-invocation: true`，手动或主舵点名触发。本仓不复制正文、不加接线引用。
- **reviewer 强化四条（F13–F16）**：`references/crew-rules.md` 新增意图前置、证据门槛分级、防凑数、择优场景盲审（默认关）；索引 F1–F16，收口节名不变。reviewer qoder/opencode 两模板动作清单嵌入同样四条，保留结论枚举 PASS/BLOCK/可延后；firstmate-dispatch §5 收口补评审报告验收口径。
- **经济档选板**：firstmate qoder/opencode 两模板与 firstmate-dispatch §2.1 增加经济档/升档表（interrogate 1 攻击者→2；arena 2 候选跳过评委→开评委；swarm 只分片→救火竞速）；升档判断权在大副并记流 plan Decision。
- **arena 剧本**：`templates/playbooks/arena.md`（Frame/Fan out/Pick/Graft/Verify/Output；2 候选、跳过评委、盲审默认关、败者 1–2 点嫁接、真实验证）；README 剧本表补 `arena.md`。

### 修复

- **BUG-023**：`templates/skills/firstmate-dispatch/SKILL.md` §3 派活示例改走唯一入口——`dispatch.py tab create` / `scripts/start-agent.sh`（或 `dispatch.py start`）/ `dispatch.py prompt`；只读 `herdr agent list/tab list/get/read` 保留，`send-keys` 标注仅人工恢复。
- **BUG-024**：`scripts/crew-state.py` 文档中的回执状态枚举去掉 `ack`，与 `scripts/crew-msg.py` 的 `done|blocked|captain` 一致。

### 变更

- 版本 2.2.3：`SKILL.md` frontmatter 与标题、`init.py` 版本 fallback 同步。

## 2.2.2 — 2026-11-10

### 新增

- **W2 每 tab 容量 3→6**：`references/crew-rules.md` W2 上限改为 6 个 pane（含 root）；`pane-audit.py` 阈值同步（6 pane 合法、7 pane 告警 `tab-overflow`），docstring/`--help` 口径同步。
- **总舵 tab 单 pane**：W2 新增「总舵 tab 只保留总舵自身 1 个 pane，不得 split；总舵换舵另开新 tab，继任首报后关旧 pane/旧 tab」。`pane-audit.py` 新增 `helm-tab-multi-pane` 检查（tab 标签含「总舵」且 pane>1 → 仅报告 action=None，不自动关）；总舵 tab 同时兜底报告空 tab（`empty-tab`）与无 registry 的 pane（`orphan-pane`），均仅报告；`Captain·`/`流程优化·` 前缀仍整体跳过。

### 变更

- **guard 自动交接改新 tab**：`crew-guard.py` 交接流程由同 tab `pane split` 改为 `dispatch.py tab create --workspace <ws> --cwd <root> --label <helm.tabLabel 默认「总舵」> --no-focus` → `start` → `prompt`；`load_config` 读取 `helm.tabLabel`；`guard.handoff` 新增 `oldTab`/`newTab`（旧文件缺字段兼容，只关旧 pane）。首报后先 `pane close`；随后用 `herdr pane list` 确认旧 tab 无剩余 pane 才 `tab close`，有残留 pane 或无法确认时只记 note（phase=done + 通知），不重试；任一步失败 `phase=done result=failed` + 通知，不循环、不杀旧舵、不回落 pane split。
- **dispatch `tab create` 输出 tab+pane**：新增 `tab_id` 解析 helper（递归取 `tab_id`/`tabId`，缺失时用 `herdr pane list` 反查兜底）；stdout 同时输出 `{"tab": ..., "pane": ...}`，保留 `pane` 键兼容 `start-helm.sh`；仍未取得 tab_id 时 agent-log 记 `result=partial` 并在 stderr 告警（rc 仍 0，guard 侧 fail-closed）。
- helm 模板（Qoder/opencode）交接流程同步为「新 tab → start → prompt → 首报后关旧 pane/旧 tab」，去掉同 tab pane split 与「自关旧 pane」措辞；`templates/AGENTS-编排节.md`「进度与协同」补总舵单 pane 与 W2 容量提示。

## 2.2.1 — 2026-11-10

### 修复

- **BUG-022 开发 worktree skillRoot 风险提示**：消费项目 `skillRoot` 解析后位于项目根之外、且落在 linked worktree（祖先含 `.git` 文件而非目录，即开发 worktree）时，`doctor` 的 `skill.source` 与 `init.py --check` 给出 warn/风险提示：message 含「开发 worktree / 漂移」，next 指向改指稳定分发副本；默认 profile 不升级为失败（rc 不变）；`--strict` 下按既有语义将 warn 计为失败（rc=1）。稳定分发副本（主仓工作树，`.git` 为目录）与项目内 skillRoot（`.qoder/skills/`、`.opencode/skills/`）不误报；不新增 doctor 检查项名（维持 42 项）。

## 2.2.0 — 2026-11-10

### 新增

- **剧本库（playbook 路由，B1/B2/B4）**：技能层新增 `templates/playbooks/`（README + 4 个多线版种子剧本 `autonomous-run` / `babysit` / `session-pickup` / `investigation`），随技能分发、不复制进项目；项目层 `.planning/playbooks/` 由 init 渲染 `templates/project/playbooks-README.md` 生成骨架（已存在不重建），同名项目层覆盖技能层。剧本按流 plan 解析契约书写：每 Phase 恰一行 `- **Status:** pending`、条目只用 `- [ ]`/`- [x]`/`- owner:`。
- **大副选板 SOP**：`templates/skills/firstmate-dispatch/SKILL.md` 增「2.1 选板（剧本路由）」——按任务类型选剧本 → 步骤逐字铺进该流 task_plan 的 `## Phases` → 跳过留痕 `- [x] <步骤>（跳过：<原因>）` → 剧本名与来源层记入 Decisions；qoder/opencode firstmate 基类动作清单各补一句运行期指针。
- **收口规则 F6–F12（B5）**：`references/crew-rules.md` 收口节新增 prove-it-works、sequence-verifiable-units、test-behavior-not-implementation、build-the-lever、explain-the-number、guard-the-context-window、never-block-on-the-human 七条原则（含 pstack 原名溯源），索引表改 `F1–F12`；`templates/AGENTS-编排节.md` 新增「剧本库（playbook 路由）」与「收口与验证原则」两节（摘要 + 指针，不复述正文）；orchestration-guide 与角色模板引用区间同步 F1–F12。
- **剧本契约校验（B3/B6）**：`plan-status.py --playbooks <dir>` 校验剧本结构（至少一个 Phase、每 Phase 恰一行 Status、Phase 条目标准 todo）；doctor 增加剧本库体检（发现两层、校验契约、缺失给可执行 next）；init 生成项目层剧本目录与 README。

### 变更

- 版本 2.2.0：`SKILL.md` frontmatter、标题与「读哪里」同步剧本库指针。

## 2.1.9 — 2026-11-10

### 新增

- **禁用 SendMessage（权限层）**：Qoder 7 个角色基类 frontmatter 全部加入 `disallowedTools: [SendMessage]`（reviewer 为 `[Write, Edit, SendMessage]`），dispatch 组合后通过 `--disallowed-tools` 传给 qodercli，避免跨会话消息审批卡住主舵；opencode 7 个基类加 `tools: {SendMessage: false}` 等效禁用（opencode 无 SendMessage 工具，保留前向兼容的显式 deny）。项目角色仍不得覆盖 `disallowedTools`/`tools`/`permission`（只取基类）。
- **回执行为策略**：Qoder/opencode 14 个角色模板补一条——不主动使用 SendMessage；回执走 inbox + herdr；Qoder 弹出跨会话审批一律选 **Decline**（inbox 为真源），除非 Captain 明确要求 Delivered。
- **BUG-017 guard 按项目 cwd 隔离**：只统计 cwd 在本仓或 `git worktree list --porcelain` 列出的 linked worktree 内的 agent；外部项目（如 CASE）不参与 running/blocked/快速叫醒。cwd 缺失或不可解析时沿用旧口径包含（无法确认归属不改变既有行为，已在 CHANGELOG/帮助中说明）。

## 2.1.8 — 2026-11-10

### 新增

- `crew-guard.py` 自动交接：每轮用 `herdr pane read <pane> --lines 80` 读取总舵 `ctx Nk/1M`；按 config `helm.ctxWarn`（默认 0.30）/`helm.ctxHandoff`（默认 0.60）写 `<crew-dir>/guard.handoff`（phase=warn/started/done，含旧名/新名/pane/时间/原因）并通知。≥交接阈值一次性执行 split 新 pane → `dispatch.py start --role helm --name <继任名>`（`-2` 起、冲突递增；有注册表 runtime 时透传 `--runtime`）→ prompt 继任首报 → 首报后旧舵 idle/无响应则 close 旧 pane；任一失败只通知+记录（phase=done, result=failed），不循环、不杀旧舵；ctx 解析失败只记日志，正常叫醒（B/C/D/快速叫醒）不受影响。guard 重启时若 handoff 的 old 与配置 helm 名一致，优先按 new 继任名恢复监控。
- `dispatch.py start` 新增 `--name` 显式 Herdr/dispatch 实例名：沿用 `INSTANCE_NAME_RE` 校验与已存在拒绝；不传时保持原派生行为（helm 用 `helm.agentName`，其余按 projectId/role/instance 派生）。
- helm 模板（Qoder/opencode）补 `guard.handoff` 检查与自动交接步骤（30%/60%、`--name`、首报后关旧 pane），并写明多流建副舵：活跃流/并行切片 ≥2 时为每流建 `<流>-firstmate`（tab「副舵·<流>」），总舵只派副舵、不直接派 worker。

## 2.1.7 — 2026-11-10

### 新增

- `crew-msg.py` 的 qoder→qoder 推送默认改走 `herdr agent prompt <目标名>`（非交互、无 Qoder 跨会话审批）；新增 `--via {auto,herdr,sendmessage}`（默认 auto）。`--via sendmessage` 保留 DELEGATE 输出并告警「需接收方人工确认（Qoder 跨会话审批），inbox 为真源」；`--via herdr` 强制直推，失败时回退 DELEGATE 并返回诊断错误，不静默丢回执。`--via` 只作用于同 runtime=qoder 的推送；Herdr 目标/跨产品仍只写文件+通知。
- helm / firstmate 四个 Qoder/opencode 模板动作清单补两条：每轮开工检查 `<crew-dir>/inbox/` 与 `<crew-dir>/guard.wake` 并消费；Qoder 跨会话审批按「1. Delivered / 2. Decline」人工处置，不依赖 SendMessage 送达（inbox 为真源）。

## 2.1.6 — 2026-11-10

### 新增

- `crew-guard.py` 快速叫醒：run_once 记录上一轮 working 数；当「上一轮 working>0、本轮 working=0、且存在 pending」时立即叫醒，不再等连续 2 轮；连续 idle（上一轮也为 0）仍按原 2 轮去抖，冷却与 blocked 优先级不变。
- helm 模板（`templates/agents/qoder/helm.md`、`templates/agents/opencode/helm.md`）动作清单各补两条：每轮开工检查并消费 `<crew-dir>/guard.wake`；长 prompt/含反引号等特殊字符派单先落临时文件，再用 `"$(cat <file>)"` 传递（BUG-010）。

## 2.1.5 — 2026-11-10

### 修复

- BUG-013：`crew-guard.py` 的 `_parse_backlog` 跳过阻塞列已完成的 T 行——中文按子串「已完成」判定；英文 `complete`/`completed`/`done` 不区分大小写且按 ASCII 字母词边界匹配（`incomplete`/`undone` 不误命中）；已完成项不再计入 pending，避免 guard 对已完成 Backlog 误叫醒。

## 2.1.4 — 2026-11-10

### 新增

- `dispatch.py` 的 qoder 交付在 qodercli 参数中增加 `--name <启动名>`（位于 `--` 之后的 runtime 参数），使 Qoder 会话名与 Herdr/dispatch 名一致，`SendMessage`/peer 可互认；opencode 交付命令行不变。
- helm 模板（`templates/agents/qoder/helm.md`、`templates/agents/opencode/helm.md`）「上下文与工位」补总舵换舵物理步骤：ctx ≥30% 停止接新活 → 同 tab 新建 pane → `dispatch.py start --role helm --pane <新>`（启动名即会话名）→ 新舵读 portfolio 三文件 + progress 尾部并完成首条汇报 → 旧舵关闭自己 pane；不在运行中压缩。

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
