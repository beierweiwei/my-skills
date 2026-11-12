# 剧本：maintain-verification（验证技能维护循环）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/maintain-verification.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。改编自 pstack `maintain-verification-skill`（MIT），按经济档默认串行适配；不新增规则、不复制红线。

## 适用与退出条件

- 适用：项目已有验证技能（Launch/Doctor/Drive/Evidence/Cleanup/Helpers + feature map），需要发布后审计或周期兜底，防止 feature map 随产品代码漂移。
- 不适用：还没有验证技能 → 先按 create-verification-skill（外部工程技能，需环境已安装；未安装则 ⏸ 上抛 Captain 记录缺口）生成并自证跑通一个 feature，再进本剧本。
- 触发：发布执行完成（最终收口回执前）当轮启动，最迟下一个工作会话；每周复盘兜底；outcome 写入最终收口回执。
- 退出：Outcome 明确为 clean / changed / blocked 之一；运行记录（covered / unreachable / drift / outcome）已落盘且未提交；changed 已按一次交付落本地 commit 或已说明不交付原因；blocked 写明卡点、已覆盖范围与未到达的 feature。

## 角色分工

- firstmate：选板、派工、收尾与 outcome 写入回执；不直接修 map/harness，不 commit。
- scout：Phase 2 只读 source wave（或按预算增派只读子代理）。
- builder：Phase 1/3/4/5/6 的落盘、驱动与 commit；runner 可按派工替代 Phase 4 驱动。

## Outcomes（三选一，必须明说）

- **clean**：每个 feature 都拿到 source 与 live 覆盖，没有值得交付的修正；不建分支、不提交。
- **changed**：一次交付＝一份经证明的修正（doc / harness / map），按目标仓惯例落**一次本地 commit**；**不自行 push**，推送是仓库 owner/Captain 的决定。
- **blocked**：覆盖没跑完，或已证明的修正无法安全交付；写明确切卡点、已覆盖范围与未到达的 feature。

## Edit scope（硬边界）

- 只改验证技能自身目录（SKILL.md、features/ 与它自有的 harness 脚本）；绝不改产品代码。
- feature map 描述的行为若产品已不再提供：要么是 doc drift（修 map），要么是 product regression（只报不修，交产品流处理），不得在文档里掩盖。
- 产品回归/产品缺口只报不修（D6）；越界修改即违反本剧本，回执标 blocked 并说明。

## 经济档适配

- 默认串行：source wave 由 owner（scout）逐个 feature 只读通读，不强制并行子代理；覆盖率要求不变——每个 feature 都必须有一份结论，不许跳过。
- 需要并行时由副舵按预算增派只读子代理并记入流 plan 的 `## Decisions Made`；子代理只读：不驱动应用、不改文件。
- live pass 始终由 Phase 4 owner（builder，或按派工替换的 runner）独占驱动，不拆给子代理。

## 运行记录

- 记录落 `.scratch/maintain-verification-<日期>/`（或项目约定的 scratch 位置），**不提交**；至少含 covered（feature 清单）、unreachable（feature + 前置 + 尝试路径）、drift（确认漂移与证据）、outcome。
- 驱动证据优先用 `python3 scripts/crew-evidence.py --out <file> -- <命令>` 采集（命令/rc/stdout/stderr/时间/git status 一次落盘）；缺该工具时按验证技能 Evidence 段手写落盘；每次清理/teardown 后按命名位置复查仍在（`test -f` 或复读），不默认假设。
- 运行记录/outcome 报告落盘：禁止无引号 heredoc/命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具（无引号 heredoc 内的命令替换会触发 Qoder 权限弹窗并卡住会话）。

## Phases

### Phase 1: 定位与索引卫生
- owner: builder
- [ ] 定位目标：项目级验证技能目录（带 Launch/Drive 段与 feature map）；用户级安装不是目标；多个候选先上报由 firstmate 向 Captain 确认，一个都没有则停并转 create-verification-skill（外部技能未装则 ⏸ 上抛 Captain），不凭空造目标
- [ ] index hygiene：读 feature map README 并 glob 同级文件，修缺失、多余、重复、死链条目；轻量核对，不生成静态清单
- **Status:** pending

### Phase 2: source wave（来源通读）
- owner: scout
- [ ] 每个 feature 文件产出一份只读结论：feature 摘要 / 源码入口 / 可能漂移（带引用，无则写「无」）/ 一条简洁 live 验证配方
- [ ] 默认串行逐个通读；需要并行由副舵按预算增派只读子代理；无论串并行，覆盖率不变，每个 feature 都要有结论
- [ ] 只读：scout 或增派的子代理都不驱动应用、不改文件
- **Status:** pending

### Phase 3: reconcile（对账）
- owner: builder
- [ ] 每个 feature 文件都有 source wave 结论，缺的补齐后才进 Phase 4
- [ ] 每个 feature 保留四 H2：`Sub-features` / `How to get to it (user POV)` / `Driving it with <harness>` / `Gotchas`，缺的按 create 契约补回
- [ ] 合并重叠配方到尽可能少的应用状态；抽查引用的漂移，不重复证明干净结论；扫近期改动找 map 漏掉的用户可见面（须有具体源码路径才判缺失）
- **Status:** pending

### Phase 4: live pass（真实驱动）
- owner: builder
- [ ] 真实驱动每个 feature 至少一次：按验证技能自己的 Launch 模型（长驻实例串行驱动，或短命 CLI 每驱动一个新会话）；首次驱动前先跑 Doctor，不通过不驱动
- [ ] doctor 不变式：首次驱动前、每个新会话、任何失败驱动后都 doctor；doctor 看不到的失败（健康进程里的 UI 卡死）复位到已知状态或重启，不赌
- [ ] 证据不变式：已采集证据在每次清理后仍在——按命名位置复查，不默认假设
- [ ] 残留不变式：驱动启动的任何东西不超过该驱动的有用期；失败迭代残留必清理（实例卡死、已退出或共享都要清；共享实例清残留不清实例）
- [ ] 失败先清理再重试：技能漂移导致的 doctor 失败按 Edit scope 修，再重启被该修复失效的东西（只重启必要的），重试一次；仍失败才判 blocked
- [ ] 无法到达的 feature 记 `verified-unreachable`，必须给具体前置（认证/权限/OS/外部状态）与尝试路径；map 漏写该前置即 drift
- [ ] triage 产生的 harness 修正在交付前重新 live 驱动一遍；最终 teardown 放在本次最后一条驱动（含复证）之后
- **Status:** pending

### Phase 5: triage（归类）
- owner: builder
- [ ] doc drift：用户视角描述错/缺 → 修 map
- [ ] harness gap：行为正常但驱动不了 → 修 harness（脚本可执行、调用写进技能正文）
- [ ] product gap：应用行为确实坏了 → 记给用户/产品流，不夹带进本次交付
- **Status:** pending

### Phase 6: ship or stop（交付或收口）
- owner: builder
- [ ] changed：一次交付＝本地 commit（不自行 push）；交付前重读每个改动文件
- [ ] clean / blocked：不建分支、不提交，只报 outcome 与覆盖范围
- [ ] 回执写明 outcome、covered / unreachable / drift、运行记录路径与交付 commit（changed 时）
- **Status:** pending

## 跳过规则

- 跳过留痕：`- [x] <步骤>（跳过：<原因>）`，禁止删行。
- 可跳过：Phase 2 的并行子代理增派（经济档默认串行，不派子代理；Phase 2 只读通读本身不可跳过）；不可跳过：完整执行 Phase 1–6，其中 Phase 1 定位与索引卫生、Phase 2 source wave、Phase 4 live pass、Phase 6 outcome 与运行记录不得省略；source wave + live pass 是下限，不替代其他不可跳过项。
- 跳过与阶段顺序调整记入流 plan 的 `## Decisions Made`。
