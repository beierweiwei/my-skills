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

## 大副选板与铺设

1. 按任务类型选剧本；剧本名与来源层（项目层/技能层）记入该流 plan 的 `## Decisions Made`。
2. 剧本 `## Phases` 里的每个 Phase（标题、`- owner:`、步骤、Status 行）**逐字铺进**该流 `task_plan.md` 的 `## Phases`，不擅改阶段顺序。
3. 不执行的步骤必须留痕：`- [x] <步骤>（跳过：<原因>）`，禁止直接删行。
4. 跳过与改序的原因同样记入 `## Decisions Made`。

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
