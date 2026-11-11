# 剧本：session-pickup（接续前任）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/session-pickup.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。

## 适用与退出条件

- 适用：换舵、工位重启、代理交接后接续前任工作；或 Captain 说「接着某某的活」。
- 退出：接续点明确并完成首报（含 guard 存在核验），后续工作交回对应剧本或派活 SOP；或确认前任足迹不可用、需要重新定界。

## Phases

### Phase 1: 定位前任足迹
- owner: firstmate
- [ ] 读该流 plan 三文件、进度尾部、`<crew-dir>/inbox/` 未消费回执与 guard 状态
- [ ] 看工位与 tab 清单（只读），确认前任在跑什么、停在哪个 Phase
- **Status:** pending

### Phase 2: 重建运行态
- owner: firstmate
- [ ] 以 plan 与回执为真源重建：当前 Phase/Status、Next Step、在跑工单、占用与分支
- [ ] **必核 guard 存在**：`python3 scripts/doctor.py --only runtime.guard`——pass 且 detail 带 pid 才算值守在线；warn/fail 按输出 next 启动 `python3 scripts/crew-guard.py --helm <当前舵> --interval 120` 后再继续
- [ ] 只读必要文件，长材料交子代理归纳，不把前任全文搬进上下文（F11）
- **Status:** pending

### Phase 3: 对齐已完成与待办
- owner: firstmate
- [ ] 列出已完成（含证据指针）与未完成项；已完成的不重做、不重新验证全部
- [ ] 对继承的关键结论做一次抽样复核（F6）；自述完成不算证据
- **Status:** pending

### Phase 4: 路由后续工作
- owner: firstmate
- [ ] 按剩余任务类型选剧本续派；无匹配不硬套，按通用派活 SOP 执行
- [ ] 更新流 plan 的 Next Step 与 Decisions，交接结论落文件不靠聊天
- **Status:** pending

### Phase 5: 首报与旧舵处置
- owner: firstmate
- [ ] 完成首报：状态、事实（路径/证据）、门禁、下一步，一段说清
- [ ] 旧舵/旧工位按记录处置（关闭或保留至明确时间），不留双写者
- **Status:** pending

## 跳过规则

- 跳过留痕：`- [x] <步骤>（跳过：<原因>）`，禁止删行。
- 可跳过：前任已完成且证据齐全的对齐步骤；不可跳过：真源重建、关键结论复核、旧舵处置。
- 跳过与剧本/来源层选择记入流 plan 的 `## Decisions Made`。
