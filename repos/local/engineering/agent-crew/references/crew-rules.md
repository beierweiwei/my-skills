# crew 运行规则（技能源唯一正文）

> 本文件是可执行规则的唯一正文，只存在于技能源 `skillRoot/references/`：每条规则只写一次，以稳定编号供 AGENTS 编排节、角色文件与工单引用。项目只保留指针，不复制副本。
> 机制与背景见 [orchestration-guide.md](orchestration-guide.md)，本文件不复述机制解释，也不写操作步骤与实现细节。
> 与项目决策清单冲突时，以决策清单为准，并列入发现清单上报 Captain。
> 适用范围：总舵、副舵与全部 crew。Captain 明确授权可对管理类规则作例外；数据安全红线不因授权放宽。

## 编号索引

| 前缀 | 领域 | 编号 |
|---|---|---|
| D | 派单 | D1–D9 |
| R | 回执与反馈 | R1–R6 |
| W | 工位 | W1–W5 |
| O | 占用 | O1–O3 |
| F | 收口 | F1–F16 |
| P | 并发 | P1–P3 |
| G | 值守 | G1–G7 |
| S | 状态机 | S1–S4 |
| X | 边界红线 | X1–X5 |

## 0. 入口与角色继承（非编号动作）

- **普通技能引用**：通用规则/验收/机制/plan 纪律只在技能源一份；项目 `AGENTS.md` 与角色文件用 `{{skill_root}}`/`{{crew_rules}}`/`{{crew_acceptance}}` 指针引用，`skillRoot` 变化用 `init --refresh-block --confirm` 重渲染块。项目内出现规则/验收副本按 doctor `skill.source` 失败处理。
- **唯一 dispatcher**：Agent 生命周期与工位变更只走 `python3 scripts/dispatch.py`（compose/start/prompt/close/notify/status/list/tab/pane）；`start-agent.sh`/`start-helm.sh` 只是薄包装。角色文件、项目文档、项目脚本禁止直写 `herdr agent start/prompt/close`、`herdr tab/pane` 变更或 runtime `--agent` 直启；只读 `agent list`/`tab list`/`pane list`/`status` 允许。
- **跨项目通知（外部 agent）**：向未托管的兄弟项目 agent 发提示只允许 `python3 scripts/dispatch.py notify --agent <外部名> --message <文本>`——不查/不写 dispatch 注册表，不 start/close、不动生命周期；`--agent <外部名>` 直发；或 `--workspace <id> --role helm` 解析当前 workspace 最新活跃代际并默认要求回执；多实例非 helm 角色用 `--agent` 指定；仍禁止直写 `herdr agent prompt`。notify 先解析目标 agent 的 cwd 与 git common dir，把通知**原子写入收件方** `<git-common-dir>/crew/inbox/`（`kind=notification`，含来源/目标/时间/message/receipt_required/status，按 `notification_id` 内容寻址去重），再调 `herdr agent prompt`；解析失败（不在本机/非 git/无 cwd）降级为只 prompt，但输出与日志必须明确「未落收件方 inbox」，不得静默。收件方 `status=pending` 的通知属于 R1 文件真源：每轮开工与换舵交接必查，`plan-status.py --handover` 摘要含「未处理通知」（数量+路径）；处置后 `dispatch.py notify --ack <id>` 置 processed，未处理口径以 ack 终结、不永久堆积。notify 目标活跃态判定＝`idle/working`（`strip().lower()` 后），其余状态（含缺失/空白/done/unknown/blocked）拒绝；需要时用 `--agent` 显式指定。
- **跨项目回执落点**：`crew-msg.py` 向跨项目 agent 推送回执时，除 herdr/HTTP 推送外必须把回执副本原子写入收件方 `<git-common-dir>/crew/inbox/`（同 `receipt_id` 幂等去重）；解析失败只报「未落收件方 inbox」并继续推送，禁止静默或只 prompt。
- **角色继承与组合**：角色 = 技能基类（`skillRoot/templates/agents/<runtime>/<role>.md`）+ 项目角色文件（`extends: skill:<role>`、`managed: false`）。dispatch 启动时 base 先、项目后 append-only 组合；frontmatter/权限只取基类；受保护标题冲突、残留 token、项目 frontmatter 权限键一律拒绝；组合结果不写项目发现目录。
- **安装 / 升级 / 体检**：`init` 只在缺失时生成项目角色 stub，AGENTS 无标记零写入并生成 pending 文件；受管块只经 `--refresh-block --confirm` 重渲染；`upgrade` 只替换受管脚本并保留逐字节回滚；`doctor` 只读，输出 JSON 证据与可执行 `next`。
- **版本与迁移**：技能版本见 `SKILL.md`/`CHANGELOG.md`；安装版本与配置 `skillVersion` 由 doctor 校验。基类升级只改技能源，下次 dispatch 自动生效，项目角色文件不需改。

## D 派单

- **D1 工单五段**：每张工单必须写齐目标、现状入口、验收断言、边界规则、回执要求五段；现状入口必须含「上一版为什么没生效（根因）」——返工/修复/升级类工单必填，首版/新建写「无（首版）」；缺段或缺根因不得派出（无根因不派活）。
- **D2 只给边界不给做法**：工单不写文件白名单、行数预算、实现步骤与字段级设计；根因属上下文（解释上一版为何失效），不属实现步骤；实现方式由 worker 现场决定并回写证据。
- **D3 单写者入界**：边界规则必须写明单写者约束（见 P1）；归属不确定时，先由 worker 取占用（见 O1）再开工。
- **D4 最小改动**：worker 只做验收断言要求的事；不顺手重构、不校正范围外问题、不新增未要求的功能。
- **D5 AMEND**：任何扩围必须先提交 AMEND（原因/文件/风险）并获确认；禁止静默扩围。
- **D6 发现清单**：范围外问题只报不修；回执必须含发现清单，无发现写「无」。
- **D7 关键决策对抗审查（硬）**：涉主数据、架构口径、验收口径、数据安全、范围变更的关键决策，实施前先派独立 Reviewer 以对抗立场审查（Captain 直令不豁免，取证可并行），阻断项清零才允许实施；结论与证据写 plan Decisions 或需求目录。未出示报告路径与 PASS/阻断清零记录即阻断，流程骨架见 `templates/playbooks/adversarial-review.md`。
- **D8 YOLO 派单**：`dispatch.py start` 派 qoder worker 一律带 `--permission-mode bypass_permissions`；缺省由配置 `agents.permissionMode` 自动补传并记录来源（explicit/config-default），显式传参以显式值为准。opencode 工位的 YOLO 由角色模板 frontmatter `permission:` 块承担（不经 CLI 旗标；传 `--permission-mode` 会被 dispatch 拒绝 rc=2，registry/agent-log 记录 `permissionMode=template`）。所有角色无例外（含 scout/runner/planner）；发送前自检命令参数与运行时匹配；派单记录与回执写明 permissionMode 及来源；只代表工单边界内免确认，合并/推送/清理与数据安全门禁不放宽。缺该参数的 qoder 工位由 pane-audit `yolo-missing` 巡检告警；opencode 工位由 pane-audit 按组合产物 frontmatter `permission:` 块静态校验。
- **D9 派单前置纪律（FB-055）**：行为参数（阈值/频率/默认值）必须给依据（口径来源/对标/数据），拍脑袋数值禁止进工单；改动既有机制/规则/参数前先答原设计意图——原设计的合理内核是什么、新方案是否破坏、使用者视角体验（频率/打扰/噪音）如何；审查不过不派单（流程骨架见 `templates/playbooks/dispatch-preflight.md` Phase 0，项目层同名剧本可扩展）。

## R 回执

- **R1 文件真源**：回执以 `<git-common-dir>/crew/inbox/` 下的文件为真源；先原子落盘（见 P3）再尝试推送，推送失败不影响回执成立。读取用 `python3 scripts/crew-msg.py ls --unread` 取未读，处置后 `ls --mark-read <receipt_id>...`（或 `--all`）记已读游标（`<git-common-dir>/crew/inbox-read-cursor.json`）；游标只记读取进度，不改回执状态、不删回执文件，inbox 文件仍是唯一真源。**已读≠已处置：处置以回执 status + plan 为准**（游标只表示读过，不代表 blocked/⏸ 已处理）。**投递语义**：`crew-msg.py write --to <同项目 herdr agent>`（`--target-runtime auto`，发送方 qoder）默认直推收件方 pane（`delivery=herdr`，复用 T37 聚焦守卫）；目标 pane 聚焦或聚焦无法判定时不注入，落 inbox + Herdr 通知（`delivery=inbox_fallback`）；跨项目目标、目标不可达或显式 `--target-runtime herdr` 时只写文件 + Herdr 通知（`delivery=notification`）。**A-1 情形③**：默认 auto 已判定同项目后，二次 `herdr agent list` 失败或目标从列表消失 → `delegate ok=false` + rc=3（note 写明原因；不冒充 notification/inbox_fallback）。
- **R2 回执字段**：`receipt_id`、`from`、`runtime`、`task`、`status`（`done|blocked|captain`）、`commit`、`changed_files[]`、`evidence[]`、`work_slot`、`findings[]`、`summary`、`ts`；缺字段视为无效回执；⏸ 一律用 `status=captain`。返工/修复/升级类任务的 findings 首条必须写「根因：成立」或「根因：修正为…」，形成派单—回执闭环。
- **R3 去重**：回执按 `receipt_id` 去重，同一 `receipt_id` 只处理一次。
- **R4 触发器不是状态**：回执只触发收口与审计，任务状态只认 plan；回执与 plan 不一致时以 plan 为准并触发核对。
- **R5 反馈分诊**：发现先分四路，不混用——①阻断级（流程跑不下去/工具坏）走回执 `blocked` + 即时推送总舵/副舵，20–30 分钟自检兜底，不进 feedback；②repo 犯错记该流 plan `## Errors Encountered`（回执 findings 带出），correct 启用后转其队列；③跨项目认知由总舵/大副当场 inone 推送（`INONE_AGENT_NAME` 用角色名），船员只报不推；④仅编排/流程/提示词问题进 feedback 信箱（项目 portfolio 目录 `feedback.md`），上报者预分诊填「预判去向」（改规则 R* / 改 SOP 步骤 / 改脚本 / 改模板措辞），格式与生命周期按 [plan-discipline.md](plan-discipline.md) §5.3。复盘去向必须留痕（已改 R*/已改文件、转_correct、已_inone、归档+原因）；待复盘未清零且无去向视为复盘未完成。
- **R6 blocked 回执处置闭环**：①`status=blocked` 回执由 owner（总舵/副舵）**当轮**处置：re-task / close / escalate，并在回执或原工位留痕；②blocked 工位**不适用** W4 的 2 小时保留，超过 worker 报 blocked 时建议的保留窗（含最晚处置时间）仍未处置即关闭工位或升级 Captain（升级须写明），未给出保留窗时当轮处置；③只改 plan、不回写原工位的视为未处置；re-task / close / escalate 是处置动作语义，不是回执 `status`；回写原工位时按合法枚举映射：`re-task` → `status=done` 且 summary 标「已转出：<新工单/工位/接手人>」；`close` → `status=done` 且 summary 标「已闭环：<原因/动作>」；`escalate` → `status=captain` 且 summary 标「待 Captain：<决策项>」；未处置保持 `status=blocked`，不得用 `done`/`captain` 掩盖；④区分「等 helm 决策」与「等人工权限/人工确认」：后者标 Captain 项并**释放工位**，不空挂。

## W 工位

- **W1 建与复用**：同流任务复用已有工位；同类任务可共用专用组 tab 的分 pane；新建工位用独立 tab，核验 workspace 与 cwd 后再启动 agent。
- **W2 容量与来源**：每 tab 最多 6 个 pane（含 root），超出另开组 tab；不得从总舵或他人 worker 的 tab 分裂出工位；迁移失败即关 pane，不留残位。总舵 tab 只保留总舵自身 1 个 pane，不得 split；总舵换舵另开新 tab，继任首报后关旧 pane/旧 tab。
- **W3 标签**：工位标签必须含「需求·任务」，禁用角色泛称；一个 pane 只跑一个 agent。
- **W4 回收窗**：one-shot 工位收口即关；builder 仅在「同资产下一件」时保留，窗口 2 小时；空 tab 自动关闭；保留工位标签标「·保留至 HH:MM」。blocked 工位见 R6，不适用本窗。
- **W5 巡检**：单 tab 超容、有 pane 无注册、有注册无 pane 均须告警；working 状态的 pane 不得关闭；白名单工位不自动清理。默认巡检与自检动作为 `python3 scripts/pane-audit.py --apply`：只自动关「非白名单、非 working 的明确空 pane / 空 tab / 完成流 tab」，个人 tab 不动；总舵 tab 只允许 1 个 pane，多余空 pane 自动关（交接后 pane 数=1）。

## O 占用

- **O1 声明先于写入**：worker 写前在 `<git-common-dir>/crew/occupancy.json` 声明占用，写明任务与将写的文件/资产；未声明不得写入。
- **O2 冲突即停**：目标已被占用时立即停止相关写入，回执报 `blocked` 并列出冲突项；不抢占、不协商共写。
- **O3 释放与回收**：切片结束（完成/中止/失败）立即释放；失联占用由派单方核验后回收，不得静默覆盖。

## F 收口

- **F1 核验三件**：收口必须核对 numstat（改动规模）、工作区状态（未提交与越界文件）、证据清单；缺证据不收口。关键决策类交付另核 D7 对抗审查结论与证据（报告路径 + PASS/阻断清零记录），缺失不收口、不得报完成。
- **F2 暂存范围**：只暂存回执列出的改动文件，不夹带无关改动。
- **F3 plan 更新**：owner 收到回执后必须更新对应 plan 的阶段 Status、Next Step 与证据指针；回执不替代 plan，未更新视为未收口。
- **F4 工位处置**：每段收口回执必须写明「工位处置：关闭 / 保留至 HH:MM」；不填视为未收口。
- **F5 越界即 BLOCK**：reviewer 对超出工单目标的 diff 可直接 BLOCK；收口方只看结果与证据，不看实现细节。
- **F6 prove-it-works**：结论只认真实运行证据（测试、复现、真实数据回放、可执行命令输出）；运行态证据分两类：行为/脚本改动必须驱动项目验证技能（`verify-<app>`）对应 feature 的真实驱动证据；规则/模板/提示词文本改动以 grep/lint/schema 测试为主证据 + 安装后 1 个 smoke feature 为辅证，并在回执写清 feature 名、命令 + stdout/stderr + 退出码、证据路径。diff、接口 200、自述完成只作旁证；无验证技能先按 create-verification-skill 生成（外部工程技能，需环境已安装；未安装则暂停该交付门并 ⏸ 上抛 Captain 记录缺口，不得跳过或用人肉清单替代），不以人工核对代替（pstack 原名 prove-it-works）。
  - 证据层级与顶层验证口径见 [verification-redlines.md](verification-redlines.md)（VR1/VR5）；本条目只定运行证据门槛，不重复层级判定。
- **F7 sequence-verifiable-units**：工作切成可独立验证的最小单元，前一单元验证通过再开下一个，禁止最后批量补验（pstack 原名 sequence-verifiable-units）。
- **F8 test-behavior-not-implementation**：测试断言对外行为与契约，不断言内部实现细节，重构不因实现变动而重写测试（pstack 原名 test-behavior-not-implementation）。
- **F9 build-the-lever**：一次性验证沉淀为可复用的脚本、检查或看板，让同类验证自动完成，不重复手工操作（pstack 原名 build-the-lever）。
- **F10 explain-the-number**：报数必须带口径（来源、算法、时间窗口、限制），解释不了的数字不进结论（pstack 原名 explain-the-number）。
- **F11 guard-the-context-window**：主动管理上下文预算，长材料交子代理或脚本归纳只带回结论，到阈值按交接规则换舵，不在运行中压缩硬撑；读取量预算：交接先读 `plan-status.py --brief` 一屏，看板仅按关键词 grep（不整读）；交接必读 ≤32KB（含 `--handover` 输出与必读文件），超阈值先摘要/关键词 grep，写明只读段，再定向补读，`Decisions`/`Deviation`/`Backlog` 不整读（pstack 原名 guard-the-context-window）。交接预算只计实际要求读的段（摘要/brief/活跃流整行（路径/阶段/Next Step 未完成首条/待 Captain 计数）/feedback 待复盘命中/handoff），整看板/整 plan 不计入；`--handover` 输出硬 ≤8KB（必读预算 ≤32KB 语义不变），超限截断并标注「已截断，详情见 plan」。
- **F12 never-block-on-the-human**：只有不可逆、超授权或需偏好判断的事项交人工，其余按红线自主推进并留里程（pstack 原名 never-block-on-the-human）。
- **F13 意图前置**：评审开工先一句话复述「变更想干什么」（从工单/commit 提炼），钉死评审范围；范围外既有问题只报不审。
- **F14 证据门槛分级**：阻断级发现必须附运行复现（失败测试/repro 脚本/错误输出）；仅有静态推断的发现自动降「建议」级。
- **F15 防凑数**：「无发现」是合法结果，不编造不凑数；同根因发现合并为一条上报（列全部位置），不拆分、不重复。
- **F16 择优场景盲审（默认关）**：两流方案择一时启用匿名 candidate-A/B + rubric 逐条打分；有异族模型（Qoder/CC 互评）则用；主会话与评委双评分对照，分歧即信号；升档判断同经济档，由大副判断并记该流 plan Decision。

## P 并发

- **P1 单写者**：同一文件/资产同一时刻只有一个写者；写者身份由占用声明确定。
- **P2 锁**：占用变更、回执入库、状态迁移等互斥动作必须串行执行；锁只保护临界区，不跨长任务持有。
- **P3 原子写**：回执、占用、注册表、事件日志等共享状态文件必须原子替换写入；读者不得看到半份内容，崩溃恢复不得读到中间态。

## G 值守

- **G1 单实例轮询**：guard 单实例运行、固定周期轮询；多实例时后启动者退出。
- **G2 唤醒条件**：仅当「有待办且无在跑 worker」连续两轮成立才叫醒；**快速叫醒（FB-057①）**：完成差集边沿——任一 worker 从 working 消失（herdr 或 claude runs 口径，探针排除）即叫醒核收，不要求全部转 idle、不要求有待办；受全局冷却 `guard.wakeCooldownMinutes`（默认 10 分钟，夹取 1–240，非新参数），被冷却吞掉的边沿记 guard.log，不受 pending 心跳间隔压制，消息带完成名单与核收指引；全局冷却（参数见上句）内同一待办不重复叫醒；pending 且真 idle（无 working/blocked/awaiting_approval）持续时按 `guard.pendingHeartbeatMinutes`（默认 15 分钟，夹取 1–240）周期心跳叫醒，仍受冷却约束。**空闲兜底（2.2.26 T63）**：无待办、无在跑、无卡住且总舵 idle/done 连续 `guard.idleWakeRounds` 轮（默认 30≈60 分钟（T64，对齐每小时心跳口径），夹取 0–240，0=关闭）→ 报平安叫醒 + Backlog 清单（不含已完成；可派/⏸/⛔ 全列），受全局冷却去重、触发后计数清零，总舵 working/blocked 不叫——任何安静期都有最低可见性，不允许「待办=0 导致值守静默如熄火」。等待审批 worker 走 G4 的 awaiting_approval 通道，同轮不叠加 pending 叫醒；agent idle/done 的开口按审批结束处理不叫醒（FB-054，超龄 30 分钟记残留日志）。存在 awaiting_approval 时该轮不推导 pending 心跳（阈值内等待/超 max 停止重复同理）。
- **G3 待办口径**：活跃流 plan 中未勾选且未标 ⏸/待 Captain 的 Phase 待办，加 portfolio Backlog 中可派的 T 行；已完成 Phase 的残留待办只记日志，不叫醒。**Backlog ⏸ 两态（2.2.26 T63 AMEND）**：⏸ 行仅当含外部条件语义（待 Captain/待 C/留 Captain/待拍板/待评审/待授权/待窗口）才排除（「⏸ 待外部」，不计待办）；其余 ⏸（下批/batch/评估中等纯排队语义）计「⏸ 排队」——计入待办数据（idle 兜底清单可见）但不触发即时叫醒，由空闲兜底/攒批触发承载；⛔ blocked 行只进兜底清单不触发。
- **G4 在跑口径**：在跑 = Herdr `agent_status=working` + `<crew-dir>/runs/` 存活注册（`crew-claude.py run` 运行时原子写入，含 pid/role/started_at/out_dir）；死 pid、坏 JSON、缺 pid 的注册忽略并记日志，注册失败只告警不阻断运行；blocked 单独作为「卡住」叫醒理由，叫醒带年龄与重复叫醒——达到 15 分钟未处置再叫醒，达到 blockedGrace（默认 30 分钟）提示「应关闭或升级」，不自动杀 agent；idle/done/unknown 不算在跑。**awaiting_approval（2.2.21）**：qoder run 日志 `[permission-ask] prompt/settle` 按 callId 配对，未闭合 ask ≥ `guard.approvalWaitMinutes`（默认 15 分钟，夹取 1–240）视为卡住（此时 herdr 仍可能是 working，blocked 不出现）；重复叫醒按 agent+callId 去重、间隔 `guard.approvalRepeatMinutes`（默认 30 分钟，夹取 1–240），≥ `guard.approvalGraceMinutes`（默认 60 分钟，夹取 1–240）附「A 人工批准 / B 关闭重派 / C 继续等待」建议，≥ `guard.approvalMaxAgeMinutes`（默认 240 分钟，夹取 1–1440）停止重复并记日志；首次叫醒受全局冷却、重复叫醒不受。**安全矩阵**：目标有未闭合 ask 时 guard / crew-msg / dispatch 一律只通知不注入；绝不向等待审批的 pane 注入文本/按键，不自动 close。
- **G5 零输出与边界**：正常零输出；可提示「回执 done 但 plan 未更新」，不代写 plan；外置定时只检查 guard 存活；人工抽查工具不承担自动叫醒。
- **G6 口头决策落盘**：Captain 口头/会话决策由收到指令的 owner **当轮**写入对应 plan 的 `## Decisions Made` 或 portfolio `progress.md`（含生效口径与时间），并在回复回显「已落盘：<文件>/<条目>」；未落盘不得当作已决执行；交接 digest 必须列「未落盘决策」（无则写「无」）。
- **G7 guard 自动交接口径**：交接操作前以 `oldPane` 反查 `oldTab`（pane list 权威、agent list 兜底）；缺失/不一致以反查为准关闭并写回 `<crew-dir>/guard.handoff` + 告警，反查不到（pane/agent 消失）fail-soft，不按未验证 `oldTab` 关 tab。`phase=done` 后对残留旧 helm 做一次有界回收：仅 idle/done、非现役、非聚焦、非白名单才关；working/blocked/unknown 不关、只记日志。guard 周期调用 `crew-reaper.py --apply --json`（失败只记 guard.log、不阻断值守）；一键入口与安全边界见 [orchestration-guide.md](orchestration-guide.md) §5.12。

## S 状态机

- **S1 任务状态放行条件**：

  | 迁移 | 放行条件 |
  |---|---|
  | backlog → ready | 工单五段齐 + 根因（首版写「无（首版）」）；D1 优先于本表 |
  | ready → dispatched | worker 可路由、文件占用无冲突 |
  | dispatched → running | 有回执确认或运行时 working |
  | running → review | 回执 done + 改动文件清单 + 证据 |
  | running → blocked | 回执 blocked/captain；解除后回 running |
  | review → accepted | reviewer PASS + 逐节点抽验记录 + plan 已更新 |
  | accepted → archived | 证据归档 + plan 收口 |
  | 任意 → cancelled | 任意状态可取消 |

- **S2 工位状态**：`starting → working → blocked / done → closed`；`idle ≠ done`；`done` 必须有回执；`closed` 才可从注册表删除。
- **S3 派生与幂等**：状态从工单、回执、注册表、plan 派生，不新建数据库；非法迁移拒绝或告警；动作按幂等键去重；崩溃后由事件日志（`<git-common-dir>/crew/events.jsonl`）重建已应用状态。
- **S4 稳定性八条验收**（任意一条不成立即视为不稳定，不得发布）：
  - **S4.1 不丢任务**：dispatched 任务必须有回执或超时告警，不因监控中断、重启或换舵而消失。
  - **S4.2 不重复派单**：同一工单/动作幂等，重复触发不产生第二份派单或第二次执行。
  - **S4.3 不假完成**：done 必须有回执与证据；accepted 必须有逐节点抽验记录；缺证据不得升级状态。
  - **S4.4 不跳步**：非法状态迁移必须拒绝或告警，不得静默放行。
  - **S4.5 崩溃可恢复**：事件日志与派生状态可重建已应用状态；重启后不丢任务、不重复执行。
  - **S4.6 可观测**：任务状态与历史（status/history）一屏可查，不靠翻聊天记录或日志拼状态。
  - **S4.7 并发安全**：占用、单写者、原子写、锁共同成立（O1–O3、P1–P3），并发下不互相覆盖。
  - **S4.8 低 token**：检查在脚本内完成，正常零输出；不靠长提示词或人工逐项核对维持稳定。

## X 边界红线

- **X1 不 push**：未经 Captain 明确授权不推送远端；授权后只推任务分支。
- **X2 不删除**：未经 Captain 明确授权不删除文件、分支、worktree 或数据；任何删除前必须备份。
- **X3 不无备份覆盖**：覆盖既有文件前必须备份并可回滚；强制覆盖不得用于客户既有产物。
- **X4 不碰客户业务数据与凭据**：原始数据不全量读取，只取必要列/行；证据脱敏或哈希；客户敏感数据不得以原始形态入库。凭据/会话文件只允许存 `.scratch/`（0600，用 `install -m 600` 或 `chmod 600` 落盘）或平台 env；禁止复制进沙箱、平台（含平台数据目录）、共享目录、线程工作区、仓库、证据与对话；「平台」指平台/CI 的密钥管理（环境变量或密钥服务）。「涉凭据」＝复制/移动/持久化/新增凭据或修改权限，必须上抛 Captain，不得代决；经 Captain 授权的平台 env 读取用于执行不算涉凭据。X4 优先于 F12。能自动扫的凭据风险做成检查器；无执行器时以角色模板断言 + 测试兜底（取舍：静态扫描只做技能源回归，不覆盖变量构造的运行时复制路径，行为靠模板自检覆盖）。
- **X5 遇红线即停**：触线只暂停受影响项并写「⏸ 待 Captain」，不擅自扩大范围，其余独立任务继续。
