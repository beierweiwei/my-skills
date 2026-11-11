# 剧本：agents-md-slimming（AGENTS 瘦身与维护）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/agents-md-slimming.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。方法依据 `writing-for-agents` 技能（single source of truth / progressive disclosure / context pointers / pruning）。

## 适用与退出条件

- 适用：项目 `AGENTS.md` 体量膨胀或职责混杂（工作约定、交付纪律、决策链、角色动作、进度规则写在同一份常驻文件里），需要诊断并给出「通用约定 + 指针」结构方案。
- 不适用：单条路径/措辞修正（直接改）；技能 `references/`、角色模板、受管块正文的瘦身（走对应技能维护流）。
- 退出：方案（逐条 keep / move / delete + 目标落点 + 预计节省 + 风险）与只读证据交付，交 Captain / 目标项目总舵确认；**本剧本不修改 `AGENTS.md`**，改动需单独授权并另开实施流。

## Phases

### Phase 1: 圈定输入与只读边界
- owner: firstmate
- [ ] 列输入：目标项目 `AGENTS.md` + 项目真源（`CONTEXT.md`、`docs/`、技能 `references/`、`.planning/`）
- [ ] 声明只读：不动 `AGENTS.md`、角色文件、技能源与目标项目任何文件；诊断证据写独立路径
- [ ] 记录基线：`wc -l -c AGENTS.md`、`sha256sum AGENTS.md`、`git status --short`，收口复核一致
- **Status:** pending

### Phase 2: 分节量化
- owner: scout
- [ ] 按 `##` / `###` 标题切节，逐节统计行数、字节与占比，产出分段账本
- [ ] 区分受管块（技能生成标记区间）与项目自有段，分别汇总字节与占比
- [ ] 给出可复算命令或脚本，账本数字与命令输出逐项对账（合计 = 文件总量）
- **Status:** pending

### Phase 3: 重复、过期与失焦识别
- owner: scout
- [ ] 跨文件重复：与项目其他真源、技能 `references/` 逐句与语义比对；语义重复人工判定，不依赖只抓逐字重复的 doctor
- [ ] 过期失效：对每条被引用路径做 `test -e` / `grep` 校验；核对反向引用（技能、角色文件是否按条目编号反向引用 `AGENTS.md`）
- [ ] 失焦识别：把交付纪律、决策链、角色动作、进度规则写成常驻正文的段落，标为结构性外移候选（细节应下沉到单一真源并留指针）
- **Status:** pending

### Phase 4: 分类与目标落点
- owner: scout
- [ ] 按 `writing-for-agents` 四原则（single source of truth / progressive disclosure / context pointers / pruning）逐条给 keep / move / delete
- [ ] 每条 move 写目标落点（`docs/<主题>.md`、`CONTEXT.md`、需求/验收文档、技能 `references/`）与指针措辞（说清材料是什么 + 何时去读）
- [ ] 列「不宜删」清单：项目红线、客户口径、技能兜不住的规则，逐条说明为何不能只留通用指针
- **Status:** pending

### Phase 5: 产出移植方案并交确认
- owner: firstmate
- [ ] 逐条 diff 草案（原文 → 替换文本）+ 预计节省（字节/行数）+ 风险（谁在读、哪个文件反向引用、是否断编号）
- [ ] 分批：零风险去重 → 摘要化/指针化 → 结构性外移（需 Captain / 目标项目总舵确认）
- [ ] 方案与证据交确认；未获授权即退出，不在本剧本内动 `AGENTS.md`
- **Status:** pending

### Phase 6: 实施后验证与证据
- owner: builder
- [ ] 实施流（单独授权）完成后跑 `doctor --only agents.block,docs.pointers`（或项目配置的等价校验）
- [ ] 块外 diff 只含方案内条目；`grep` 确认无重复正文、无断链指针、反向引用编号仍有效
- [ ] 复算 `wc -l -c` 与分节占比，与方案预计值对账；前后字节/行数/占比与命令输出落盘为证据
- [ ] 未授权实施时留痕跳过：`- [x] 实施后验证（跳过：方案交付即退出，改动未授权）`
- **Status:** pending

### Phase 7: 交付与退出
- owner: firstmate
- [ ] 交付：方案、证据路径、未决项（需 Captain / 目标项目总舵决策项）单列
- [ ] 结论与剧本来源层记入流 plan 的 `## Decisions Made`；方案正文落文件，不塞聊天
- **Status:** pending

## 跳过规则

- 跳过留痕：`- [x] <步骤>（跳过：<原因>）`，禁止删行。
- 可跳过：Phase 6 的实施后验证（方案交付即退出、改动未授权）；不可跳过：基线记录、分节量化、不宜删清单、证据落盘。
- 跳过与剧本/来源层选择记入流 plan 的 `## Decisions Made`。
