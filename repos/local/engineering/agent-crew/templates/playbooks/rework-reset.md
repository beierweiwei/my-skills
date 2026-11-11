# 剧本：rework-reset（返工重置）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/rework-reset.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。重置只重排执行顺序与门禁，不复制规则正文、不新增红线。

## 适用与退出条件

- 适用：同一 task/fix 回执 ≥3 或同一功能 ≥3 轮返工，继续打补丁只会放大分歧；或验收反复不过、需求口径已不可信。
- 不适用：单点小 bug（直接修）、首次返工（按原计划修）。
- 退出：验收门通过 + Captain 签认重置结论 + 证据齐备；未签认不得重开下游派活。

## Phases

### Phase 1: 停手与事实归集
- owner: firstmate
- [ ] 停止新增补丁与派活，冻结在跑切片（只允许收口与取证）
- [ ] 归集需求、验收、回执与证据缺口：逐项列「已声称完成 / 实际证据 / 缺口」
- **Status:** pending

### Phase 2: 根因归类
- owner: scout
- [ ] 按需求口径 / 验收缺失 / 实现缺陷 / 外部依赖四类归因，同类合并
- [ ] 每条根因写触发证据与影响面，不写解决方案
- **Status:** pending

### Phase 3: 先补验收
- owner: planner
- [ ] 无项目验收文档（配置 `docNames.accept`）先建；已有则按根因补缺口与复算路径
- [ ] 验收项引用项目需求文档（配置 `docNames.req`）编号，证据位置与失败处理逐条写清
- **Status:** pending

### Phase 4: 重开需求门
- owner: firstmate
- [ ] 口径/范围/流程图节点逐项重确认，歧义标「待澄清」并指明找谁补
- [ ] Captain 签认重置范围与验收口径；签认前不进入切片重排
- **Status:** pending

### Phase 5: 对抗审查或 arena
- owner: reviewer
- [ ] 关键需求用 interrogate 对抗审查；方向分歧大用 arena；默认经济档，升档记流 plan Decision
- [ ] 阻断项清零才放行，建议项登记去向
- **Status:** pending

### Phase 6: 切片重排
- owner: firstmate
- [ ] 按验收节点切最小可验证单元，上游未定不派下游
- [ ] 新切片顺序、owner 与依赖记入流 plan `## Phases`
- **Status:** pending

### Phase 7: 验收门
- owner: firstmate
- [ ] 真实数据回放逐节点抽验，证据齐（脚本输出/明细/抽验表）
- [ ] Captain 签认验收门；未过则回 Phase 2 重新归因
- **Status:** pending

## 跳过规则

- 跳过留痕：`- [x] <步骤>（跳过：<原因>）`，禁止删行。
- 可跳过：Phase 5 对抗审查（非关键需求且无方向分歧）；不可跳过：停手归集、根因归类、补验收、重开需求门、切片重排、验收门。
- 跳过与阶段顺序调整记入流 plan 的 `## Decisions Made`。
