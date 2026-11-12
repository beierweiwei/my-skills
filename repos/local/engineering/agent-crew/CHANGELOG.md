# Changelog

## 2.2.18 — 2026-11-12

反馈闭环（FB-041–FB-044）：评审/终验报告与回执成对、占用键统一、dispatch 双 runtime 判读、交接一屏摘要，含互检/评审补口与本地发布。

### 新增

- **FB-041 评审/终验「报告 + 回执」成对**：qoder/opencode reviewer 模板硬性要求「报告落盘后立即发回执」（回执含报告路径 + 结论 PASS/BLOCK/阻断+建议数 + 工位处置，缺回执视为未完成，不得以报告文件替代）；firstmate 收口核报告与回执成对；工单模板与 release 剧本同口径；opencode reviewer `permission.bash` 增 `python3 scripts/crew-msg.py write*` allow（S-8），无人值守不弹权限确认。
- **FB-042 占用键 canonical + 旧键兼容**：`scripts/crew-ticket.py` 占用键统一为仓库根相对路径（任意子目录/`./` 前缀解析为同一键，绝对路径/越界拒绝），旧式子目录相对键按后缀匹配兼容读取（claim 同 owner 幂等、异 owner 拒绝，新键一律写 canonical）；release 冲突仅按精确键异 owner 判定，后缀命中只清理同 owner 旧键（S-1/S-2：互为后缀不误锁、精确键缺失 no-op rc=0）。
- **FB-043 dispatch 双 runtime 注册表解析**：`compose`/`start` 无显式 `--runtime` 时按 `<crew-dir>/dispatch-agents.json` 同 role 最新 `startedAt` 记录唯一解析现役 runtime（输出 `runtimeResolvedFrom=registry:<agent>`），显式 flag 优先且不读注册表；无记录/最新并列/注册表损坏或不可读（S-4）统一拒绝并给 `--runtime qoder|opencode` 指引；release 剧本 Phase 6 ③ 与 `orchestration-guide` §5.11 ③ 写明判读。
- **FB-044 交接一屏摘要与预算**：helm（qoder/opencode）交接只读白名单与恢复顺序以 `plan-status.py --brief` 一屏替代整看板（看板仅按关键词 grep、不整读）；`--handover` 预算以 brief 字节替代整看板并输出分项；guard 自动交接 prompt 与 `firstmate-dispatch` 技能模板（第二真源）同步；`plan-discipline` §5.1 第 4 条与 `crew-rules` F11 同口径；超限提示补「写明只读段」。本仓交接必读 42,682B → 11,177B（brief 1,582B）。

### 修复

- 互检 S-1/S-2/S-4/S-8 四条已并入上述四条（release 后缀死锁与契约收紧、注册表损坏文案、opencode reviewer 回执权限）。
- 两轴评审 R-1（firstmate-dispatch 第二真源）/R-2（HINT 只读段措辞）返工闭环；R-3（release-scope 工单 JSON 驱动）/R-4（plan 登记 lint）记 D-11/D-12，本批不修。

### 测试

- 新增 `test_2218_fb041_receipt_pairing.py`、`test_2218_fb042_occupancy_keys.py`、`test_2218_fb043_compose_runtime.py`、`test_2218_fb044_handover_brief.py`；补 `test_224_retro.py`、`test_2213_context_budget.py`、`test_crew_guard.py`、`test_plan_status_handover_rotate.py` 等用例（先红后绿）。
- 全量 pytest 0 fail（本版 1234 passed + 982 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.18：`SKILL.md` frontmatter 与标题、`init.py` fallback、本段、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py`、`test_2214_guard_presence.py` 版本断言同步。

## 2.2.17 — 2026-11-12

回执到达直推唤醒（FB-039，T37 延伸）、crew-claude 运行注册与 guard 在跑口径（FB-040）、评审建议 S3–S13、维护 map 补齐与发布。

### 新增

- **FB-039 回执到达直推唤醒**：`scripts/crew-msg.py write` 对同项目 herdr agent 目标默认直推收件方 pane（复用 `deliver_qoder`，`delivery=herdr`），收件方回合已结束也能到达；保持 T37 聚焦守卫——目标聚焦/无法判定时只落 inbox + 通知（`delivery=inbox_fallback`）；跨项目/不可达/显式 `--via herdr` 保持 `notification`（T28 语义不变）；S3 `ls --mark-read` 重写游标改为 `{id: 时间}` 映射 + `setdefault`，不覆写历史标记时间；`--help`/docstring 写明 A-1 情形③界定。
- **FB-040 crew-claude 运行注册 + guard 在跑口径**：`scripts/crew-claude.py run` 期间在 `<crew-dir>/runs/` 原子写存活注册（pid/role/backend/model/started_at/out_dir/root），正常/非零/超时/异常统一清理，注册失败只 stderr 告警不阻断运行；`scripts/crew-guard.py` 每轮把存活注册计入在跑 worker（`--once --json` 增 `claudeRuns`；有待办也不叫醒），死 pid 删除并记日志、坏 JSON 忽略；**pid 复用核验**——argv 不含 `crew-claude` 按死删除、cmdline 不可读 24h 超龄忽略、缺 `started_at` fail-safe；S4 `pid_alive` 捕获 `PermissionError`/`OverflowError`，与 preflight 同口径。
- **评审建议 S5–S9/S12/S13**：S5 `plan-status.py --brief` 改显式行数上限（截断+告警，替代 `assert`）；S6 opencode helm 反向断言（防旧双口径回归）；S7 `crew-preflight.py` `_lock_held` 只读探测不留空锁；S8 preflight agent 口径纳入 linked worktree（与 guard `_in_project` 一致）；S9 `crew-evidence.py` 新增 `--timeout` 并清未使用常量；S12 `pane-audit.py` 固化 pane 内后台 job SIGHUP 语义；S13 `crew-msg.py ls --mark-read --all` 跳过 legacy 文件名（stderr 汇总，rc=0），显式 id 缺失仍整体拒绝；S10/S11 记不适用（Decisions D-3）。
- **维护 map 补齐 5 条**：verify-crew 新增 `crew-evidence` / `crew-preflight` / `release-check` / `crew-msg-cursor` 4 个 feature；`plan-status-freshness` 补 `--brief`/`--lint`、`pane-audit` 补 `--blocked-grace`、`guard-wake` 补 `guard.wake generated_at:`/2h 过期与 FB-040 runs 口径；`features/README.md` 索引与 `guard_probe.py` source wave 同步；修 HEAD 既有红测试（`test_2214` 期待已废弃的 `skill-src` 字面）。
- **文档与模板同步**：`references/crew-rules.md` R1 投递语义 + G4 在跑口径；`references/herdr-cheatsheet.md` 回执三情形与 guard 行；`references/orchestration-guide.md` registry 行与 §7.5 G4 引用；qoder/opencode 的 helm/firstmate/runner 6 个角色模板同步新行为（职责不变）。

### 修复

- **互检建议-1/2/4**：cheatsheet A-1 三情形限定；guard pid 复用永久静默（并入 FB-040 身份核验/超龄/fail-safe）；legacy 回执未读噪音处置行 + feature Gotchas；建议-3（多 workspace 同名 agent 路由取第一条）记 backlog（D-9，本批不修）。
- **评审 R-1–R-5**：R-1 verify-crew guard-wake 配方改用含 `crew-claude` 的标记子进程并重驱证据；R-2 证据重驱/注记；R-3 R1 与 `crew-msg.py` docstring 补 A-1 情形③ + docs 位置断言；R-4 guard/preflight/release-check 三处 `pid_alive` 加固 `OverflowError`（超范围 pid 删注册并记日志）；R-5 第 10 条测试先红日志注记；文字级残留 2 条（`--help` 情形③、证据计数口径）顺手修。

### 测试

- 新增 `test_2217_fb039_wake_on_receipt.py`、`test_2217_fb040_claude_runs.py`、`test_2217_docs_delivery_runs.py`；补 `test_crew_msg.py`、`test_pane_audit.py`、`test_2214_guard_presence.py`、`test_2216_docs_integration.py`、`test_2216_t40_t41_tools.py`、`test_2216_t44_brief_lint.py` 用例（先红后绿）。
- 全量 pytest 0 fail（本版 1195 passed + 975 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.17：`SKILL.md` frontmatter 与标题、`init.py` fallback、本段、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py`、`test_2214_guard_presence.py` 版本断言同步。

## 2.2.16 — 2026-11-12

交接/总舵启动 YOLO 补参（T46）、证据采集器（T40）、轮次预检（T41）、回执已读游标（T42）、发布检查汇总（T43）、看板一屏化与 plan lint（T44）、pane-audit 自动收敛（T45）、文档/模板接线，以及互检修复、独立评审与终验。

### 新增

- **T46/FB-037 交接与总舵启动 YOLO 补参**：`scripts/crew-guard.py` 自动交接与 `scripts/start-helm.sh` 的 `dispatch start` 均显式传 `--permission-mode bypass_permissions`；新增双路径参数断言（guard 注入 dispatch 断言 argv、start-helm 脚本结构化断言）；guard 以修复后代码重启并通过 `doctor --only runtime.guard`（pass 带 pid），当前总舵未重启。
- **T40 证据采集器 `crew-evidence.py`**：一条命令执行校验并把「command/rc/stdout/stderr/时间/git status」按固定标记原子落盘，rc 镜像被采命令；缺参 rc=2、写失败 `WRITE_FAILED` rc=1；纳入 `SCRIPT_MANIFEST`。
- **T41 轮次预检 `crew-preflight.py`**：一次输出 wake 新鲜度、未读回执（复用 T42 游标）、agent 状态 + ctx、看板新鲜度、guard pid 五项；单项不可用 fail-safe 标注不崩，人读 + `--json`，仅 `--strict` 非 0；纳入 `SCRIPT_MANIFEST`。
- **T42 回执已读游标**：`crew-msg.py ls --unread` / `ls --mark-read <id>...` / `--mark-read --all`；游标落 `<git-common-dir>/crew/inbox-read-cursor.json`（id 映射 + flock 原子写），inbox 文件仍为唯一真源、不改不删；游标缺失/损坏/不可写偏重复读不误漏。
- **T43 发布检查 `release-check.py`**：版本（源 frontmatter/标题/CHANGELOG/init fallback、项目 `skillVersion`、用户已装副本）、源↔用户副本↔项目安装 diff、upgrade/init/doctor、guard lock 与 `--helm` 现役舵一致性、源/my-skills 工作区干净度汇总为 pass/fail/skip 表；支持 `--json`/`--dry-run`，rc 0/1/2/3；纳入 `SCRIPT_MANIFEST`。
- **T44 看板一屏化 + plan lint**：`plan-status.py --brief` 输出活跃流一屏表（流｜阶段｜完成数/健康｜下一步｜待 Captain）+ 待 Captain 摘要 + 今日变化（≤40 行）；新增内容卫生 lint（Next Step 超长/含 ✅、🟡 无 owner、样板残留）默认只提醒（rc=0），`--strict` 才升 rc=1；既有命令契约未动。
- **T45 pane-audit 自动收敛**：`--apply` 自动关多余空 pane（非白名单、working 永不关；前台进程组组长是 shell + shell_pid 兜底 + argv 无脚本/-c 三重判空）、完成流回收 tab（非聚焦/白名单/总舵 tab）、总舵 tab 单 pane 检查；guard 交接 `tab create` 后断言 pane 数=1 并关闭多余空 pane（FB-038 根因修，失败只告警不中断）。
- **文档与模板接线**：qoder/opencode 的 helm/firstmate/runner 接入每轮预检、inbox 游标、自检默认 `pane-audit --apply`、证据用 `crew-evidence.py` 采集；`crew-rules.md`（R1 游标与「已读≠已处置」、W5 自动收敛）、`plan-discipline.md`（`--brief`/`--lint`）、release/maintain 剧本（release-check 汇总门、证据采集器）、ticket 模板同步，并补 grep 防回退断言。

### 修复

- **互检阻断 X-1/X-2/X-3**：X-1 空 pane 判空改三重判定（进程组组长/argv/shell_pid 兜底），argv 缺失或组长缺席一律只报告不关闭；X-2 新增流 Status 保守解析（`### Phase` 分段、缺省 pending、非法值告警且不回收，与 guard 口径 0 差异）；X-3 release-check 校验 guard `--helm` 与 registry 现役舵一致（不一致 fail，解析不到 skip）。
- **互检建议 S-1..S-9 处置**：guard 交接残余窗口如实声明只报告、跨模块 PHASE 解析口径统一、release-check 新增 `source.git-clean`、证据器写失败标记、hygiene 豁免口径、preflight 游标未初始化口径等按安全/口径分流落地。

### 测试

- 新增 `test_2216_t46_helm_yolo.py`、`test_2216_t40_t41_tools.py`、`test_2216_t43_release_check.py`、`test_2216_t44_brief_lint.py`、`test_2216_docs_integration.py`；补 `test_crew_guard.py`、`test_crew_msg.py`、`test_pane_audit.py`、`test_2215_upgrade_new_scripts.py` 用例。
- 全量 pytest 0 fail（本版 1141 passed + 959 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.16：`SKILL.md` frontmatter 与标题、`init.py` fallback、本段、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py`、`test_2214_guard_presence.py` 版本断言同步。

## 2.2.15 — 2026-11-12

报告落盘方式（T32）、AGENTS 块模板漂移（T35）、跨项目消息落点（T28）、升级安装源新增脚本（T34）、update-all 代理回退（T36）、回执/通知聚焦守卫（T37）、firstmate 写权口径（T38）、claude 后端包装（T39）、互检修复（A/B）与发布。

### 新增

- **T32/FB-029 报告落盘方式**：`templates/playbooks/release.md`（退出条件 + Phase 8）、`templates/playbooks/maintain-verification.md`、`templates/_templates/ticket.md`、qoder/opencode 的 `firstmate.md` 与 `runner.md` 固化「禁止无引号 heredoc / 命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具」；评审补充 qoder/opencode `reviewer.md`（含无 Write 退路：分段单引号 heredoc / helper 代写），防回退测试落点纳入 reviewer。新会话非交互实测报告落盘 rc=0、无权限弹窗。
- **T35/FB-030 AGENTS 受管块模板漂移**：`scripts/init.py --check` 识别「技能模板渲染结果与项目块漂移」，rc=1 报 `[过期] AGENTS.md#block(template-drift)` 并提示 `--refresh-block --confirm`；`scripts/doctor.py` 新增 `agents.drift`（stale warn、strict rc=1、一致 pass、模板不可读 skip）；一致场景不误报。
- **T28 跨项目消息落点**：`dispatch.py notify` 解析目标 cwd/git-common-dir，原子落收件方项目 `<git-common-dir>/crew/inbox/`（内容寻址去重，解析失败显式降级为 pane prompt）；`crew-msg.py` 跨项目回执同步落收件方 inbox；`plan-status.py --handover` 新增「未处理通知」段与 `notify --ack`；规则与 qoder/opencode helm/firstmate 同步。
- **T34 upgrade 安装源新增受管脚本**：`scripts/upgrade.py` 受管集合改为「manifest entries ∪ 安装源 `SCRIPT_MANIFEST`」（AST 读源清单，兼容项目内旧 upgrade），源仅新增脚本时安装新脚本并写 entries，消除「doctor warn → 只能 upgrade」死路；rollback 校验新增脚本未改动后移除并去条目，被改动 rc=1。
- **T36 update-all 代理回退**：`scripts/update-all.sh` 在 `--skill` 且本地可解析时代理失败自动回退 scoped install（rc=0，输出「update 失败 → 已回退安装」），`--offline` 直装；无 `--skill`/缺缓存/install 失败保持非 0；release 剧本 Phase 4 写明判定、回退、离线与证据。
- **T37 回执/通知直推聚焦守卫**：`crew-msg.py`、`dispatch.py`（prompt/notify）、`crew-guard.py` 的 herdr 注入前按 agent→pane 查 `focused`；聚焦或无法判定一律不注入（crew-msg 落 inbox + 通知 `delivery=inbox_fallback`；dispatch prompt rc=2 拒绝；notify 落 inbox + 通知；guard 写 guard.wake + 通知），投递通道可审计；6 角色模板与 cheatsheet 同步。
- **T39 claude 后端包装脚本**：新增受管 `scripts/crew-claude.py`（模型级联、`ANTHROPIC_MODEL` 传自定义网关不传 `--model`、只读 + 1500s 超时、报告/stderr/rc/meta 一次落盘、失败非零）；纳入 `SCRIPT_MANIFEST`，6 模板 + cheatsheet 统一入口。

### 修复

- **T38/FB-035 firstmate 写权口径**：qoder/opencode `firstmate.md` 统一为「portfolio 三文件由总舵写；本舵只写本流 `.planning/<task>/progress.md`」，保留 portfolio 总舵写权；组合产物 forbidden_scan 无残留。
- **互检 A 组（A-1 阻断 + A-2/A-3/A-4）**：`crew-msg.py` present 语义恢复——目标不可达 `ok=false` + rc=3（原 rc=0 伪成功），目标在而聚焦/不可判定仍 `inbox_fallback`；`--via herdr` 契约统一并同步文档；三处通知检查 returncode；TOCTOU 限制在 cheatsheet 明示。
- **互检 B 组（B-1/B-2）**：doctor/upgrade 受管覆盖集统一为「源 AST 清单 ∪ entries ∪ 本进程清单」；补 pre-T34 首升 → 二次补装夹具与 stale-init 回归；update-all 回退提示补「本地库/缓存可能落后远端」；release 剧本代理信号改 `via <代理地址>`、Phase 5 补两跑判定。
- **R-1/R-2 文档口径**：`crew-claude.py` docstring 明确「模型空白＝通道默认（rc=0，不设 env）」；剧本 README 改统一入口并并入防回退测试；AMEND 仅改 `test_2213_rules` 旧字面量断言。

### 测试

- 新增 `test_2215_t32_report_writing.py`、`test_2215_agents_drift.py`、`test_2215_t28_cross_project_inbox.py`、`test_2215_upgrade_new_scripts.py`、`test_2215_t37_focused_guard.py`、`test_2215_t38_firstmate_write_scope.py`、`test_2215_t39_claude_wrapper.py`；补 `test_2213_rules.py`、`test_crew_guard.py`、`test_crew_msg.py`、`test_dispatch.py`、`test_upgrade.py` 用例。
- 全量 pytest 0 fail（本版 961 passed + 904 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.15：`SKILL.md` frontmatter 与标题、`init.py` fallback、本段、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py`、`test_2214_guard_presence.py` 版本断言同步。

## 2.2.14 — 2026-11-11

值守存在性检查（T31/FB-028）、版本假绿防护（T33/FB-031）与发布。

### 新增

- **T31/FB-028 值守存在性检查**：`scripts/doctor.py` 新增 `runtime.guard`——`guard.lock` 缺失、内容无法解析或 pid 不活 → warn（`--strict` 下 rc=1）；进程存活 → pass 并带 pid；`--only`/`--json`/profile/strict 语义与 `runtime.lock` 一致，不误报运行中的 guard。`scripts/init.py`（`--check` 与实际 init 完成提示）与 `scripts/upgrade.py`（完成提示，含「已是最新」早退）在 guard 未运行时追加一行启动提示，退出码语义不变。
- **清单「必核 guard 存在」**：`templates/playbooks/release.md`、`references/orchestration-guide.md` §5.11、`templates/AGENTS-编排节.md` 安装/升级/体检、qoder/opencode 的 helm/firstmate 换舵与交接、`templates/playbooks/session-pickup.md` 均要求 `doctor --only runtime.guard` pass 带 pid；warn/fail 不得报发布、换舵或收口。项目侧使用指南与 verify-crew feature `doctor-guard-presence` 同步。

### 修复

- **T33/FB-031 版本假绿防护**：`scripts/doctor.py` 新增 `install.scripts_source`——受管脚本与安装源逐项比对（一致 pass、脚本旧 warn/strict fail、源缺失 skip）。`scripts/init.py` 在「旧脚本 + 新技能源 + 旧 manifest」时 rc=1、零写 manifest、保留旧 `skillVersion` 并提示先 `upgrade.py --apply --confirm`；`--refresh-block --confirm` 同根因旁路一并拦截。`scripts/upgrade.py` 源缺失与复制后复核不一致 rc=1。脚本比较按 CRLF 归一化文本哈希（仅换行差异视为一致，真实内容差异仍拦截）。

### 测试

- 新增 `test_2214_guard_presence.py`（清单落点 grep 防回退）、`test_2214_manifest_guard.py`（T33 CASE 场景/源缺失/新增脚本/CRLF/refresh-block）；补 `test_doctor_runtime.py`、`test_doctor_install.py`、`test_init.py`、`test_init_v2.py`、`test_upgrade.py` 用例。
- 全量 pytest 0 fail（本版 875 passed + 374 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.14：`SKILL.md` frontmatter 与标题、`init.py` fallback、本段、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py`、`test_2214_guard_presence.py` 版本断言同步。

## 2.2.13 — 2026-11-11

验证技能交付门（A）、verify-crew 转正（B）、维护循环（C）、凭据红线（D）、派单根因（E）、T27 非阻断项（F）、交接上下文预算（H）、角色/模型选择（I）与发布。

### 新增

- **A 验证技能交付门**：`references/crew-rules.md` F6 明确运行态证据＝项目验证技能对应 feature 的真实驱动证据（命令+输出+退出码+证据路径），diff/接口 200/自述只作旁证；技能/模板/升级改动必须跑项目验证技能，无验证技能先 create（外部技能未安装则 ⏸ 上抛 Captain）；release 剧本交付门移到项目升级/安装之后执行；两 runtime helm/firstmate 收口断言同步。
- **B verify-crew 转正（本仓）**：按 pstack 契约补全 feature map（README 索引 + 5 feature 均含四 H2）、证据标准成文（真实 CLI、退出码+输出原文、/tmp 夹具隔离、清理后证据仍在）；5 feature 真实驱动留证。
- **C 维护循环**：新增 `templates/playbooks/maintain-verification.md`（Phase 1–6、source wave + live pass、Outcome clean/changed/blocked、Edit scope 只改验证技能自身）；`templates/playbooks/README.md` 登记触发（发布后必跑 + 每周兜底）；release 剧本新增发布后维护循环步骤；helm 复盘路由接入维护检查。
- **D/FB-023 凭据红线**：`crew-rules.md` X4 补凭据/会话文件落点（仅 `.scratch/` 0600 或平台 env；禁止复制进沙箱/平台/共享目录/线程工作区；涉凭据一律上抛不得代决）；firstmate/runner 模板加凭据自检；凭据复制检查器 + 测试（字面量扫描 + 模板断言兜底）。
- **E/FB-025 派单根因**：D1 工单必填「上一版为什么没生效（根因）」，无根因不派活；D2 澄清根因属上下文不属实现步骤；helm/firstmate 模板与 `templates/_templates/ticket.md` 同步。
- **H 交接上下文预算**：helm 模板交接读取白名单 + 32KB 必读预算；`plan-status.py --handover` 输出必读字节数并对超阈值提示；`doctor.py` 新增 `role.overlay_budget`（覆盖层 8KB / 组合 20KB）与 `context.baseline`（60KB）非阻断 warn；F11 与 `plan-discipline` §5.1 同步。
- **I 角色/模型选择**：`orchestration.json` 顶层 `agents`（default/roles/playbooks）由 init 默认生成、存量缺键深合并回落；`dispatch.py start --model` 对 qoder/opencode 透传 `-m`（空 model 不传）并记录；`doctor.py` 校验 `agents.config`（结构 fail / 通道命令缺失或剧本角色未知 warn）；helm/firstmate 模板与 playbooks README 写明「剧本级角色 > 全局角色 > default」；剧本不硬编码模型（测试防回退）。Claude 通道只读执行一次（网关实际模型 glm-5.3）并切回 qoder。

### 修复

- **S3/A-S5 返工提醒降噪**：`crew-guard.py` 按最长前缀匹配 receipt task → 流 plan 目录，过滤已关闭/不活跃 task；活跃 task ≥3 仍提醒。
- **A-S8 notify 活跃态**：`dispatch.py notify` 对缺失/None `agent_status` 不再视为活跃。
- **A-S9 交接摘要边界**：`plan-status.py` `_read_head` 在文件大小恰等于窗口且末行无换行时保留整行。

### 测试

- 新增 `test_2213_rules.py`、`test_2213_playbooks.py`、`test_2213_agent_model.py`、`test_2213_context_budget.py`；补 `test_crew_guard.py`、`test_dispatch.py`、`test_plan_status_handover_rotate.py` 用例。
- 全量 pytest 0 fail（≥ 2.2.12 基线 714 passed + 225 subtests）；`crew-lint`（含 `--purity`）、`plan-status --selftest`/`--playbooks` 绿。
- 版本 2.2.13：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.12 — 2026-11-11

2.2.12 小修（T22/T24/T25 + 流程缺口 FB-017–FB-022）与发布。

### 新增

- **T22/FB-015 跨项目通知代际**：`dispatch.py notify` 新增 `--workspace` + `--role`，按 herdr `agent list` 解析该 workspace 内当前最新代际的活跃 helm；目标不唯一/最新代际不可用/无候选时 rc≠0 并给可执行提示，消息自动要求回执；`--agent` 旧用法兼容。`crew-rules.md` 通信段补充代际解析与回执要求。
- **T22/FB-016 交接摘要取顶部最新 3 条**：`plan-status.py --handover` 的「最近 progress」改取 `## 自动驾驶里程` 节顶部最新 3 条（progress 倒序），条目续行合并，不再取文件尾。
- **T24/FB-020 返工重置**：新增技能层剧本 `templates/playbooks/rework-reset.md`（停手→根因归类→先补验收→重开需求门→对抗审查/arena→切片→验收门，含退出条件）；`templates/playbooks/README.md` 登记；两 runtime firstmate 选板加「同一功能 ≥3 轮返工」触发；`crew-guard.py` 对同一 task/fix 回执 ≥3 做非阻断提醒（日志 + `--once --json` 的 `rework` 字段，不叫醒、不阻断、不改 plan），重置动作仍需人签。
- **T25/FB-021 说人话模板**：qoder/opencode 的 helm/firstmate 四份模板面向 Captain 用业务语言三句（结论/进展/需要你），证据路径改「你问再给」；副舵→总舵回执＝首句人话结论 + 技术明细；加正反例与发送前自检。
- **FB-017 doctor 编排文档重复检查**：`docs.duplicate` 覆盖 docs 下与 AGENTS 同级的编排类文档，扫描清单在代码中明确，无关 docs 不误报。
- **FB-018 project-ext 指针闭环**：`init.py` 生成 `docs/crew/project-ext/` 目录与说明文件；`doctor.py` 以配置 `paths.projectExt` 为真源做指针存在性检查（含受管块与角色文件），缺失 fail 并给可执行 next。
- **FB-019/FB-022 firstmate 写权与完成状态**：两 firstmate 模板明确 portfolio 三文件由总舵写、本舵只写流 plan/progress 与证据；完成状态只在证据落盘后写，预写即假完成，收口必核证据文件存在。

### 修复

- **互检修复**：`notify --role` 仅对 helm 做代际解析（多实例角色歧义走 `--agent`）、最新代际 blocked 时拒绝发送、回执要求幂等；`handover` 顶部条目续行跨窗口正确合并；`crew-guard` 回执提醒按 `receipt_id` 去重并补 `--help` 说明。
- **G1–G12 清理**：默认配置与文档中的客户仓库名、扫码示例、CASE 路径夹具、CASE 平台专属红线清除或泛化；`swap-to-v2.sh` 客户名清零；测试防回退。
- **project-landing-init 平台名泛化**（my-skills 同批）：`SempClaw`/`SempFlow` 示例改为通用表述。

### 测试

- 新增 `test_2212_patch.py`（模板/剧本/规则 grep 防回退断言）；`test_dispatch.py`、`test_plan_status_handover_rotate.py`、`test_crew_guard.py`、`test_doctor_agents.py`、`test_init*.py` 补 notify 代际/handover 顶部/提醒阈值/docs.duplicate/project-ext 用例。
- 全量 pytest 0 fail（≥ 2.2.11 基线 672 passed + 184 subtests；本批 714 passed + 225 subtests）；`crew-lint`（含 `--purity`）、技能源与本仓 `plan-status --lint`/`--playbooks` 绿。
- 版本 2.2.12：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

> 注记：历史条目中的 CASE/扫码示例为历史记录保留原文；自 2.2.12 起新文本已按 G2/G6 泛化。

## 2.2.11 — 2026-11-11

T21 复盘批次（FB-009–FB-014）与发布。

### 新增

- **FB-012 跨项目通知**：`dispatch.py notify --agent <外部名> --message` 只调 `herdr agent prompt`，不 start/close、不查不写 dispatch 注册表；动作日志按既有 `append_log` 惯例记 `action=notify`；`crew-rules.md` 通信段说明用途与边界；`test_dispatch.py` 覆盖外部 agent 可用与生命周期隔离。
- **FB-014 升级后重启与可交接检查**：`orchestration-guide.md` §5.11 新增发布后检查清单——guard 必须重启并验证 lock/pid/cmdline/`--once`×2、长驻 helm 换舵、firstmate 重建、one-shot worker 下次启动取新模板（compose 启动时快照）、可交接性实测；新增技能层剧本 `templates/playbooks/release.md`（含证据路径），`templates/playbooks/README.md` 剧本表登记。
- **FB-011 wake 过期 fail-safe**：guard 写 wake 文件首行带 `generated_at:` 时间戳；>2h（7200s）或无法解析按过期消费并记日志；helm 两模板启动先核对时间戳；`test_crew_guard.py` 覆盖写格式/新鲜保留/过期删除/不可解析。

### 修复

- **FB-009 helm 口头决策落盘**：两 helm 模板要求 Captain 口头/会话决策当轮写入 portfolio/progress 并回显，交接摘要新增「未落盘决策」栏；`crew-rules.md` 新增 G6，索引/guide/helm/firstmate 的 G 范围同步为 G1–G6。
- **FB-010 firstmate 回合末回执纪律**：两 firstmate 模板每轮结束前查 inbox；有未处置回执/待续派不结束回合（R6）。
- **FB-013 Errors 落表**：`plan-discipline.md` 与两 firstmate 模板要求 Errors 当轮记录、收口空表写「无」、不得留「待记录」；10 个 worker 模板回执要求带出 Error/Resolution。

### 测试

- 新增 `test_2211_patch.py`（规则/模板/剧本 grep 防回退断言）；`test_crew_guard.py`、`test_dispatch.py` 补 wake 过期与 notify 用例。
- 全量 pytest 0 fail（≥ 2.2.10 基线 629 passed + 143 subtests）；`crew-lint`（含 `--purity`）、本仓 `plan-status --lint`/`--playbooks` 绿、技能源 `plan-status --playbooks templates/playbooks` rc=0。
- 版本 2.2.11：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.10 — 2026-11-11

T20 一致性小修（A–E + FB-008）与发布。

### 修复

- **A init↔doctor 对齐**：`init.py` 的 `forbidden_copies` 对 `.gitignore` 忽略路径（`git check-ignore -q`）不再误判为技能源副本，非 gitignored 真实副本仍失败；非 git 仓/无 git 可用时按未忽略处理。`test_init_v2.py` 补正反例。
- **B R6 处置动作映射**：`crew-rules.md` R6 明确 re-task/close/escalate 是处置动作语义、不是回执 `status`，并给出映射：`re-task`/`close` → `status=done`（summary 分别标「已转出/已闭环」）、`escalate` → `status=captain`（标「待 Captain」）、未处置保持 `status=blocked`；`templates/_templates/ticket.md` 回执要求引用 R1–R6 并含映射要点。
- **C doctor guard 配置先后**：`config.schema` 校验 effective `blockedRepeatMinutes <= blockedGraceMinutes`（缺省 15/30 参与比较），矛盾配置 fail 并给可执行提示；`test_doctor_config.py` 补正反例与缺省比较。
- **D upgrade 口径统一**：docstring 与 `--apply` 实际一致（仅同步 config `skillVersion`，不做其他配置迁移）；`diff_report` 以 manifest entries 为准；install-log `next` 不再过期（提示 `init.py --refresh-block --confirm`）；`test_upgrade.py` 补对应断言。
- **E 编号回退**：`ticket.md` 回执引用 R1–R4 → R1–R6；`orchestration-guide.md` 与 qoder/opencode `helm.md` 的 F1–F12 → F1–F16；新增 `test_2210_patch.py` grep 防回退断言。

### 新增

- **FB-008 guard ctx 阈值/交接检测**：`parse_ctx_k` 兼容 `ctx (412k/1M)`、`ctx 75.4k/1M`（小数）、`ctx ( 75.4k / 1M )`、`ctx 62.3%`，统一折算为每 1M 的 k 值，多行取最后一个；解析失败返回 None 不抛异常。总舵 pane 先按 agent name 解析，名字缺失/漂移时回退 `<crew-dir>/dispatch-agents.json`（按名或最近 `role=helm` 条目反查），解析不到 fail-safe 不动作、不误交接。`guard.handoff` `phase=done` 后继续监控继任总舵（不再永久短路），一次交接成功后重置 `handoff_attempted` 允许再次交接；失败只通知并写 `phase=done result=failed`，不循环、不回落 split。交接阈值 `ctxWarn` 默认 0.30 / `ctxHandoff` 默认 **0.50**（Captain 2026-11-11 修正，原 0.60）；交接收尾按 handoff `old` 识别旧舵（重启不误杀 working 旧舵），继任缺失/blocked/idle 无首报或旧舵停滞连续 3 轮转 `failed` 通知人工，失败通知区分孤儿资源与现役继任，registry 反查仅接受可解释关系（B1–B5）。`test_crew_guard.py` 新增解析/阈值 29/30/49/50/51/映射/phase 流转用例。

### 测试

- 新增 `test_2210_patch.py`（B/E 文档断言）；`test_init_v2.py`、`test_doctor_config.py`、`test_upgrade.py`、`test_crew_guard.py` 补对应正反例。
- 全量 pytest 629 passed + 143 subtests（2.2.9 基线 586+117，0 fail）；`crew-lint`（含 `--purity`）、本仓 `plan-status --lint`/`--playbooks` 绿。
- 版本 2.2.10：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.9 — 2026-11-10

### 新增

- **大副收口 SOP 接入 interrogate 互检**：`templates/skills/firstmate-dispatch/SKILL.md` §5 收口新增「收口前互检」——代码/配置/脚本类改动收口前执行，纯文档可跳过并留痕；经济档默认 1 攻击者、inherit-parent，核心模块 / 对外 API / 数据安全红线升 2 并记该流 plan Decision；发现必须运行复现（失败测试 / repro / 错误输出），纯静态推断降「建议」；只报不修、同根因合并、「无发现」是合法结果；结果落 `.planning/<流>/evidence/interrogate.md`，结论进该流 plan Decisions；按 `interrogate` 技能执行。
- **firstmate 基类动作一行**：qoder/opencode `firstmate.md` 各加「收口前按 interrogate 技能做互检（经济档/升档规则）」，行数 66/74 仍在 80 行预算内。

### 测试

- 新增 `test_229_pilotc.py`：断言 SOP 关键词（触发/纯文档跳过留痕/1 攻击者/inherit-parent/升 2 条件/运行复现/静态降建议/只报不修/同根因合并/无发现合法/evidence 路径/plan Decisions）与两基类模板含 interrogate 且 ≤80 行。
- 全量 pytest 586 passed + 117 subtests；`crew-lint`（含 `--purity`）、本仓 `plan-status --lint`/`--playbooks` 绿。
- 版本 2.2.9：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

## 2.2.8 — 2026-11-10

### 新增

- **R6 blocked 回执处置闭环**：`references/crew-rules.md` 新增 R6——blocked 回执由 owner **当轮**处置（re-task / close / escalate）并在回执或原工位留痕；blocked 工位不适用 W4 的 2 小时保留，超过 worker 建议保留窗（含最晚处置时间）未处置即关闭工位或升级 Captain；只改 plan、不回写原工位视为未处置；区分「等 helm 决策」与「等人工权限/扫码」，后者标 Captain 项并释放工位，不空挂。索引改 R1–R6，W4/G4 交叉说明。
- **模板 blocked 处置动作**：qoder/opencode 的 helm、firstmate 模板加「每轮先处理 blocked/⏸ 回执再开新活」；builder/planner/reviewer/runner/scout 模板加 blocked 回执处置动作（14 模板各 +1 动作句）。
- **guard blocked 年龄与重复叫醒**：`crew-guard.py` 按 agent 身份（name，缺省 pane_id）记录首次 blocked 时间；首次叫醒沿用 10 分钟全局冷却，仍 blocked 且距该 agent 上次 blocked 叫醒 ≥ `guard.blockedRepeatMinutes`（默认 15，夹取 1–240）再叫醒，重复叫醒不受全局冷却压制；累计 ≥ `guard.blockedGraceMinutes`（默认 30，夹取 1–240）时叫醒消息提示「按 R6 处置（re-task / close / escalate），应关闭工位或升级 Captain」；不自动关闭/杀 agent，agent 解除或消失即清除年龄；探针/外部 cwd/helm/Captain 排除与 handoff 优先级保持不变。
- **pane-audit blocked 超时巡检**：`pane-audit.py` 读 `<crew-dir>/inbox/*.json` 中该 agent 最新回执（`work_slot == pane_id` 优先、其次 `from == name`，按 `ts` 最大），最新回执仍为 blocked 且年龄 > `--blocked-grace`（默认 30 分钟）报 `blocked-overdue`；仅报告，`--apply` 不关；helm/白名单 tab 跳过，回执损坏或 ts 缺失/非法 fail-soft。
- **doctor 登记 `guard` 配置键**：`CORE_TOP_KEYS` 加 `guard`，轻校验 `guard.blockedRepeatMinutes` / `guard.blockedGraceMinutes` 为 1–240 整数（缺省不写配置即可用，不新增检查项）。

### 修复

- G4 在跑口径更新为「blocked 单独作为卡住叫醒理由，达到 15 分钟未处置再叫醒，达到 blockedGrace（默认 30 分钟）提示应关闭或升级」，与 `guard.blockedRepeatMinutes`/`guard.blockedGraceMinutes` 一致。

### 测试

- `test_crew_guard.py` 新增 blocked 年龄/重复叫醒/grace 边界（14:59/15:00/30:00）/解除重置/多工位合并/handoff 优先；`test_pane_audit.py` 新增回执年龄边界、按 work_slot/agent 名匹配、最新回执口径、无回执不报、`--apply` 不关；`test_doctor_config.py` 新增 guard 配置合法/非法用例；`test_224_retro.py` 同步 R1–R6 断言与模板 token。
- 全量 pytest 582 passed + 117 subtests；`crew-lint`（含 `--purity`）、本仓 `plan-status --lint`/`--playbooks` 绿。
- 版本 2.2.8：`SKILL.md` frontmatter 与标题、`init.py` fallback、`test_crew_lint.py`、`test_220_final.py`、`test_224_retro.py`、`test_225_patch.py` 版本断言同步。

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
