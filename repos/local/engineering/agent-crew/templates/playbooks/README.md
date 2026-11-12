# 剧本库（playbook）

> 剧本＝派活的方法模板：按任务类型给出阶段与步骤骨架，**不是规则源**。可执行规则正文只在 `references/crew-rules.md`，剧本不新增规则、不复制红线。
> 解析契约与流 plan 一致，出处为 `templates/plans/stream-task_plan.md` 头部注释与 `references/plan-discipline.md` §2。

## 两层库

| 层 | 位置 | 说明 |
|---|---|---|
| 通用层（技能层） | `templates/playbooks/` | 随技能分发；**不复制进项目** |
| 项目层 | `.planning/playbooks/` | 项目特有剧本；同名文件覆盖技能层，未覆盖的按技能层解析 |

- 派活解析顺序：`.planning/playbooks/<name>.md` → `templates/playbooks/<name>.md` → 两层都无匹配时不硬套，按 firstmate-dispatch 通用派活 SOP 拆任务。
- 项目层骨架由 init 渲染 `templates/project/playbooks-README.md` 生成；已存在不重建，项目自有内容不被技能升级覆盖。

## 现有剧本

| 剧本 | 适用任务类型 |
|---|---|
| `autonomous-run.md` | 目标可写成可检查谓词、授权内需要连续自主推进的流 |
| `babysit.md` | 已派活在跑，需收尾监护、逐个清阻塞到人工线 |
| `session-pickup.md` | 换舵/重启后接续前任：读 plan/progress/inbox，不重做已完成 |
| `investigation.md` | 只读调研、方案对比与结论输出，不产生代码改动 |
| `arena.md` | 形状敏感、需多候选择优的方案/设计（经济档默认 2 候选、跳过评委） |
| `release.md` | 技能版本发布/升级（版本五处、测试与静态检查、同步 push、用户目录刷新、项目升级、升级后重启与可交接检查、安装后验证技能交付门、发布后维护循环先于收口回执） |
| `maintain-verification.md` | 验证技能维护循环：source wave + live pass 审计 feature map 漂移；结论 clean/changed/blocked |
| `agents-md-slimming.md` | AGENTS 瘦身与维护流程 |
| `rework-reset.md` | 同一 task/fix 回执 ≥3 或同一功能 ≥3 轮返工：停手归集、根因归类、补验收、重开需求门、对抗审查、切片重排、验收门 |
| `adversarial-review.md` | 涉主数据/架构口径/验收口径/数据安全/范围变更的关键决策实施前：独立对抗审查、挑战清单、阻断清零与证据落点 |

- 触发与范围：`maintain-verification.md` 于发布执行完成（最终收口回执前）当轮启动，最迟下一个工作会话；每周复盘兜底。范围＝完整执行维护剧本 Phase 1–6；发布后下限 source wave + live pass，不替代不可跳过项。
- 触发与范围：`adversarial-review.md` 于关键决策（主数据/架构口径/验收口径/数据安全/范围变更）实施前必走，Captain 直令不豁免（取证可并行）；阻断清零才实施，报告与回执成对。

## 大副选板与铺设

1. 按任务类型选剧本；剧本名与来源层（项目层/技能层）记入该流 plan 的 `## Decisions Made`。
2. 剧本 `## Phases` 里的每个 Phase（标题、`- owner:`、步骤、Status 行）**逐字铺进**该流 `task_plan.md` 的 `## Phases`，不擅改阶段顺序。
3. 不执行的步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
4. 跳过与改序的原因同样记入 `## Decisions Made`。

## 角色→模型选择

- 配置位置：`.planning/orchestration.json` 顶层 `agents`（`default` / `roles` / `playbooks`）；backend ∈ {qoder,opencode,claude}；默认 roles＝builder/planner/reviewer/adversary/scout/runner；init 默认生成，存量缺键按默认回落。
- 优先级：剧本级角色 > 全局角色（`agents.roles.<role>`）> `agents.default`；`model` 为空＝该通道默认模型（派单不传 `--model`）；缺配置回落 `default`，默认 backend 为 `qoder`。
- 透传：派单时把选出的 model 传给 `python3 scripts/dispatch.py start --model <model>`；空 model 不传（走通道默认）。
- 切换/切回只改 1–2 行：改 `agents.roles.<role>` 或 `agents.playbooks.<playbook>.roles.<role>` 的 `backend`/`model`，不改剧本与派单命令。
- 剧本正文只声明 owner 角色，不写模型/后端；派单方按上述优先级解析，实际 backend/model 记录到回执/工位（summary/findings 或工位标签）。
- claude 通道：`dispatch start --runtime` 只支持 qoder/opencode；backend=claude 时由 firstmate 派 Runner 走 `python3 scripts/crew-claude.py run --role <role> --prompt <prompt.md> --out-dir <证据目录>` 统一入口执行（报告落证据、发现由副舵复验），不走 `dispatch start`；禁止绕过统一入口直调 claude。
- 审计口径：技能层 `templates/playbooks/*.md` 随分发剧本（README 文档除外）不得出现角色/模型赋值（`backend`/`model` 键、具体模型 ID）；项目层 `.planning/playbooks/` 由项目自管。安装命令的 runtime 目标（如 `--agent qoder-cn,claude`）不属角色模型。

## 解析契约（硬）

- 每个剧本 ≥1 个 `### Phase N: <中文阶段名>`，全部位于 `## Phases` 节内；节外说明文字不得出现 `### Phase` 或 Status 行。
- 每 Phase 恰一行 Status 行，写法 `- **Status:** pending`（与流 plan 模板一致，解析器接受有无行首 `- `）；铺入 plan 后按实际进度改 `pending|in_progress|complete`。
- Phase 条目只用 `- [ ]` / `- [x]` / `- owner:` / `- plan:`，与 `plan-status.py --help` 及流 plan 骨架一致。

## 新增剧本

- 项目特有剧本放项目层 `.planning/playbooks/`：复制技能层同名文件再改写，或按契约新建 `<name>.md`。
- 通用价值高、跨项目可用的剧本才进技能层 `templates/playbooks/`，随版本发布。

## 校验

```bash
python3 scripts/plan-status.py --playbooks .planning/playbooks   # 项目层
python3 scripts/plan-status.py --playbooks templates/playbooks   # 技能层（技能源仓内）
```
