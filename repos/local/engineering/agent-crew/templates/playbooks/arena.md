# 剧本：arena（多候选择优）

> 技能层通用剧本；项目同名剧本放 `.planning/playbooks/arena.md` 覆盖本文件。解析契约与跳过留痕写法见 `README.md`。
> 经济档默认：2 候选 + 跳过评委（主会话按 rubric 自评）；升档由大副判断并记该流 plan 的 `## Decisions Made`。

## 适用与退出条件

- 适用：形状敏感、方案/设计有多种走法，单次尝试会锁死错误形状；需要多候选择优。
- 退出：合成产物通过真实运行验证且 synthesis note 齐全；或升档/超授权事项上抛。

## Phases

### Phase 1: Frame（定契约与 rubric）
- owner: firstmate
- [ ] 写清候选共同输入、各自独立输出目录与产物形态
- [ ] 定 rubric 3–6 条可评分标准；候选只见任务，不见彼此产物
- [ ] 声明选择规则（first-pass / rank-all / best-of）与盲审开关（F16，默认关），记该流 plan Decisions
- [ ] 默认 2 候选、模型 inherit-parent；仅覆盖多个设计方向时增加候选
- **Status:** pending

### Phase 2: Fan out（并行候选）
- owner: builder
- [ ] 候选并行启动；harness 不能并行则串行并说明
- [ ] 每个候选产出 artifact + 简短 rationale（考虑过什么、拒绝什么）
- **Status:** pending

### Phase 3: Pick（选基底）
- owner: firstmate
- [ ] 默认跳过评委：主会话按 rubric 逐条自评并选基底，记「评委跳过：经济档默认」
- [ ] 升档：架构/API 设计且方向分歧大 → 开 1 个只读评委（有异族模型可用则用），记升档原因
- [ ] 盲审启用时用匿名候选（candidate-A / candidate-B），不看模型与作者；升档时主会话与评委双评分对照
- **Status:** pending

### Phase 4: Graft（嫁接）
- owner: builder
- [ ] 败者最强 1–2 点手工嫁接进基底，保持单一心智模型，不机械粘贴
- [ ] 候选收敛则采用共识形状并记录；方向剧烈分歧则回 Phase 1 重跑
- **Status:** pending

### Phase 5: Verify（验证）
- owner: builder
- [ ] 对合成产物做真实运行验证；失败按原因回 Phase 1 或 Phase 4
- **Status:** pending

### Phase 6: Output（交付）
- owner: firstmate
- [ ] synthesis note：基底与理由、评委跳过/升档原因、每条嫁接来源、拒绝项、收敛/掉队、验证结果
- [ ] 收口按 F1/F6 核证据；报告三态与证据落回执
- **Status:** pending

## 跳过规则

- 跳过任何步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
- 可跳过：候选收敛时的 Graft；不可跳过：选择规则声明、验证、证据核验。
- 跳过与阶段顺序调整记入该流 plan 的 `## Decisions Made`。
