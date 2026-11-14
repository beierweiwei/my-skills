# 剧本：派单前置检查（dispatch-preflight）

> 用途：总舵/副舵每次排计划（派单/续派/攒批）前逐 Phase 过一遍；跳过的步骤留痕 `- [x] <步骤>（跳过：<原因>）`。
> 来源：FB-055（2026-11-13，T63 行为参数无依据进工单 + 跳过对抗审查的复盘）；项目层同名剧本可加本项目特有段。

## Phases

### Phase 0: 原设计意图审查（改既有机制时必做）
- owner: helm/firstmate
- [ ] 改动既有机制/规则/参数前，先陈述：原设计为什么这样（意图与合理内核）、新方案是否破坏该内核、使用者视角体验（频率/打扰/噪音）
- [ ] 行为参数（阈值/频率/默认值）须给依据（口径来源/对标/数据），拍脑袋数值禁止进工单；依据写进工单现状段
- **Status:** pending

### Phase 1: 依赖与优先级两轴核对
- owner: helm/firstmate
- [ ] 依赖方向核对：上游未定不派下游（决策/真源/契约类上游项未闭环时，下游实现单不开工）
- [ ] 优先级标注：每单在工单首行与看板记 [P0]/[P1]/[P2]/[P3]；同一时点 P0 优先，冲突按资产归属串行
- **Status:** pending

### Phase 2: 可自动运行性判断
- owner: helm/firstmate
- [ ] 判定该单能否全自动跑完不阻断（无中途人工决策/凭据/窗口依赖）
- [ ] 派前查目标 worker ctx 并记录读数（T69）：>40% 先 `/compact`，≥60% 关闭重建；helm/firstmate 自身交接阈值（30% warn/50% 交接）不动；`dispatch.py prompt` 已内置 ctx 硬门（ctx 解析不出 fail-closed 拒派，人工确认后 `--allow-unknown-ctx` 放行）
- [ ] **compact 保真清单（Captain 硬要求，compact 前确保 plan/progress/findings 写全）**：目标、当前 Phase、Next Step、Decisions、Deviation、在途任务三时间戳、待回执/待核收、证据路径、占用/工位、验收断言；compact 后先读回 plan/progress/findings 再继续
- [ ] **compact 操作口径**：worker 不能自 compact（CLI 命令需外部注入）——由 firstmate/helm 经 `printf '/compact\n' > /tmp/msg.md && dispatch.py prompt --agent <worker> --message-file /tmp/msg.md` 注入（2.2.32/T76：--message 只收文件路径）；流程=compact 前 handoff/plan 保真落盘 → 注入 → worker R1 回执 → 复核 ctx <40% 且读回 handoff/plan → 再派下一片
- [ ] 工单要素完整：目标/现状根因/边界/验收断言/**回执要求（R1 文件回执段必写——dispatch prompt 自动附加回执行（2.2.28 FB-057②），`--no-require-receipt` 可显式关闭）**/证据要求
- [ ] 不能全自动时：把「提前需要什么决策、找谁、什么时候要」在派单前告知 Captain，且标为最紧急事项上抛；不派了再等
- **Status:** pending

### Phase 3: 决策前置告知
- owner: helm/firstmate
- [ ] 需 Captain 决策事项（AGENTS 必须清单 + 难回头/影响验收两类）派单前一次性列清（≤3 项/次，三段式+推荐），不挤牙膏
- **Status:** pending

### Phase 4: 攒批规则（P2/P3 适用）
- owner: helm/firstmate
- [ ] P2/P3 不即时派单：Backlog 标 `[batch]`，按资产/文件分组
- [ ] 触发批量执行：同资产累计 ≥3 项，或到收工/维护窗，或某条 P0 收口后一并处理
- [ ] P0/P1 一律即时派不进批；攒批不得掩盖验收阻断项；派批时一单装多小项减少交接与窗口次数
- **Status:** pending
