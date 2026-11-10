# 项目剧本库（项目层）

> 由 agent-crew init 首次生成，之后由项目维护；技能升级不覆盖已存在版本。

## 1. 覆盖规则

- 通用剧本在技能层 `{{skill_root}}/templates/playbooks/`，随技能分发，不复制进项目。
- 本项目特有剧本放本目录 `.planning/playbooks/`；同名文件覆盖技能层，未覆盖的回落技能层。
- 两层都无匹配时不硬套，按大副派活 SOP 直接拆任务；剧本是方法模板，不是规则源。

## 2. 新增方式

1. 复制技能层同名剧本到本目录再改写，或按契约新建 `<name>.md`。
2. 保留解析契约：`## Phases` 下每个 `### Phase N: <名>` 恰一行 Status 行，写法 `- **Status:** pending`（与流 plan 模板一致，解析器接受有无行首 `- `），Phase 条目只用 `- [ ]` / `- [x]` / `- owner:` / `- plan:`。
3. 铺入流 plan 时逐字复制 Phase；不执行的步骤留痕 `- [x] <步骤>（跳过：<原因>）`。

## 3. 校验

```bash
python3 scripts/plan-status.py --playbooks .planning/playbooks
```
