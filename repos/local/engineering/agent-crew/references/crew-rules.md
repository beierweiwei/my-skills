# crew 运行规则（技能源唯一正文）

> 本文件是可执行规则的唯一正文，只存在于技能源 `skillRoot/references/`：每条规则只写一次，以稳定编号供 AGENTS 编排节、角色文件与工单引用。项目只保留指针，不复制副本。
> 机制与背景见 [orchestration-guide.md](orchestration-guide.md)，本文件不复述机制解释，也不写操作步骤与实现细节。
> 与项目决策清单冲突时，以决策清单为准，并列入发现清单上报 Captain。
> 适用范围：总舵、副舵与全部 crew。Captain 明确授权可对管理类规则作例外；数据安全红线不因授权放宽。

## 编号索引

| 前缀 | 领域 | 编号 |
|---|---|---|
| D | 派单 | D1–D6 |
| R | 回执 | R1–R4 |
| W | 工位 | W1–W5 |
| O | 占用 | O1–O3 |
| F | 收口 | F1–F16 |
| P | 并发 | P1–P3 |
| G | 值守 | G1–G5 |
| S | 状态机 | S1–S4 |
| X | 边界红线 | X1–X5 |

## 0. 入口与角色继承（非编号动作）

- **普通技能引用**：通用规则/验收/机制/plan 纪律只在技能源一份；项目 `AGENTS.md` 与角色文件用 `{{skill_root}}`/`{{crew_rules}}`/`{{crew_acceptance}}` 指针引用，`skillRoot` 变化用 `init --refresh-block --confirm` 重渲染块。项目内出现规则/验收副本按 doctor `skill.source` 失败处理。
- **唯一 dispatcher**：Agent 生命周期与工位变更只走 `python3 scripts/dispatch.py`（compose/start/prompt/close/status/list/tab/pane）；`start-agent.sh`/`start-helm.sh` 只是薄包装。角色文件、项目文档、项目脚本禁止直写 `herdr agent start/prompt/close`、`herdr tab/pane` 变更或 runtime `--agent` 直启；只读 `agent list`/`tab list`/`pane list`/`status` 允许。
- **角色继承与组合**：角色 = 技能基类（`skillRoot/templates/agents/<runtime>/<role>.md`）+ 项目角色文件（`extends: skill:<role>`、`managed: false`）。dispatch 启动时 base 先、项目后 append-only 组合；frontmatter/权限只取基类；受保护标题冲突、残留 token、项目 frontmatter 权限键一律拒绝；组合结果不写项目发现目录。
- **安装 / 升级 / 体检**：`init` 只在缺失时生成项目角色 stub，AGENTS 无标记零写入并生成 pending 文件；受管块只经 `--refresh-block --confirm` 重渲染；`upgrade` 只替换受管脚本并保留逐字节回滚；`doctor` 只读，输出 JSON 证据与可执行 `next`。
- **版本与迁移**：技能版本见 `SKILL.md`/`CHANGELOG.md`；安装版本与配置 `skillVersion` 由 doctor 校验。基类升级只改技能源，下次 dispatch 自动生效，项目角色文件不需改。

## D 派单

- **D1 工单五段**：每张工单必须写齐目标、现状入口、验收断言、边界规则、回执要求五段；缺段不得派出。
- **D2 只给边界不给做法**：工单不写文件白名单、行数预算、实现步骤与字段级设计；实现方式由 worker 现场决定并回写证据。
- **D3 单写者入界**：边界规则必须写明单写者约束（见 P1）；归属不确定时，先由 worker 取占用（见 O1）再开工。
- **D4 最小改动**：worker 只做验收断言要求的事；不顺手重构、不校正范围外问题、不新增未要求的功能。
- **D5 AMEND**：任何扩围必须先提交 AMEND（原因/文件/风险）并获确认；禁止静默扩围。
- **D6 发现清单**：范围外问题只报不修；回执必须含发现清单，无发现写「无」。

## R 回执

- **R1 文件真源**：回执以 `<git-common-dir>/crew/inbox/` 下的文件为真源；先原子落盘（见 P3）再尝试推送，推送失败不影响回执成立。
- **R2 回执字段**：`receipt_id`、`from`、`runtime`、`task`、`status`（`done|blocked|captain`）、`commit`、`changed_files[]`、`evidence[]`、`work_slot`、`findings[]`、`summary`、`ts`；缺字段视为无效回执；⏸ 一律用 `status=captain`。
- **R3 去重**：回执按 `receipt_id` 去重，同一 `receipt_id` 只处理一次。
- **R4 触发器不是状态**：回执只触发收口与审计，任务状态只认 plan；回执与 plan 不一致时以 plan 为准并触发核对。

## W 工位

- **W1 建与复用**：同流任务复用已有工位；同类任务可共用专用组 tab 的分 pane；新建工位用独立 tab，核验 workspace 与 cwd 后再启动 agent。
- **W2 容量与来源**：每 tab 最多 6 个 pane（含 root），超出另开组 tab；不得从总舵或他人 worker 的 tab 分裂出工位；迁移失败即关 pane，不留残位。总舵 tab 只保留总舵自身 1 个 pane，不得 split；总舵换舵另开新 tab，继任首报后关旧 pane/旧 tab。
- **W3 标签**：工位标签必须含「需求·任务」，禁用角色泛称；一个 pane 只跑一个 agent。
- **W4 回收窗**：one-shot 工位收口即关；builder 仅在「同资产下一件」时保留，窗口 2 小时；空 tab 自动关闭；保留工位标签标「·保留至 HH:MM」。
- **W5 巡检**：单 tab 超容、有 pane 无注册、有注册无 pane 均须告警；working 状态的 pane 不得关闭；白名单工位不自动清理。

## O 占用

- **O1 声明先于写入**：worker 写前在 `<git-common-dir>/crew/occupancy.json` 声明占用，写明任务与将写的文件/资产；未声明不得写入。
- **O2 冲突即停**：目标已被占用时立即停止相关写入，回执报 `blocked` 并列出冲突项；不抢占、不协商共写。
- **O3 释放与回收**：切片结束（完成/中止/失败）立即释放；失联占用由派单方核验后回收，不得静默覆盖。

## F 收口

- **F1 核验三件**：收口必须核对 numstat（改动规模）、工作区状态（未提交与越界文件）、证据清单；缺证据不收口。
- **F2 暂存范围**：只暂存回执列出的改动文件，不夹带无关改动。
- **F3 plan 更新**：owner 收到回执后必须更新对应 plan 的阶段 Status、Next Step 与证据指针；回执不替代 plan，未更新视为未收口。
- **F4 工位处置**：每段收口回执必须写明「工位处置：关闭 / 保留至 HH:MM」；不填视为未收口。
- **F5 越界即 BLOCK**：reviewer 对超出工单目标的 diff 可直接 BLOCK；收口方只看结果与证据，不看实现细节。
- **F6 prove-it-works**：结论只认真实运行证据（测试、复现、真实数据回放、可执行命令输出）；自述完成与演示样例不算数（pstack 原名 prove-it-works）。
- **F7 sequence-verifiable-units**：工作切成可独立验证的最小单元，前一单元验证通过再开下一个，禁止最后批量补验（pstack 原名 sequence-verifiable-units）。
- **F8 test-behavior-not-implementation**：测试断言对外行为与契约，不断言内部实现细节，重构不因实现变动而重写测试（pstack 原名 test-behavior-not-implementation）。
- **F9 build-the-lever**：一次性验证沉淀为可复用的脚本、检查或看板，让同类验证自动完成，不重复手工操作（pstack 原名 build-the-lever）。
- **F10 explain-the-number**：报数必须带口径（来源、算法、时间窗口、限制），解释不了的数字不进结论（pstack 原名 explain-the-number）。
- **F11 guard-the-context-window**：主动管理上下文预算，长材料交子代理或脚本归纳只带回结论，到阈值按交接规则换舵，不在运行中压缩硬撑（pstack 原名 guard-the-context-window）。
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
- **G2 唤醒条件**：仅当「有待办且无在跑 worker」连续两轮成立才叫醒；冷却期内同一待办不重复叫醒。
- **G3 待办口径**：活跃流 plan 中未勾选且未标 ⏸/待 Captain 的 Phase 待办，加 portfolio Backlog 中无阻塞的 T 行；已完成 Phase 的残留待办只记日志，不叫醒。
- **G4 在跑口径**：只有 working 算在跑；blocked 单独作为「卡住」叫醒理由；idle/done/unknown 不算在跑。
- **G5 零输出与边界**：正常零输出；可提示「回执 done 但 plan 未更新」，不代写 plan；外置定时只检查 guard 存活；人工抽查工具不承担自动叫醒。

## S 状态机

- **S1 任务状态放行条件**：

  | 迁移 | 放行条件 |
  |---|---|
  | backlog → ready | 工单有目标、验收断言、边界规则 |
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
- **X4 不碰客户业务数据**：原始数据不全量读取，只取必要列/行；证据脱敏或哈希；客户敏感数据不得以原始形态入库。
- **X5 遇红线即停**：触线只暂停受影响项并写「⏸ 待 Captain」，不擅自扩大范围，其余独立任务继续。
