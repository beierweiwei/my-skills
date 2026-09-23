# In Progress（试验中）

新技能先放这里试用：**默认不安装**，需要显式点名才装。

```powershell
# 装本层全部
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Layer in-progress
# 只装其中一个
powershell -ExecutionPolicy Bypass -File scripts/install-skills.ps1 -Skill <名字>
```

毕业流程：用过一段时间、确认稳定后 `git mv skills/in-progress/<名字> skills/engineering/<名字>`（或 `productivity/`），它就进入默认安装。

## 当前

- **[mao-methods](./mao-methods/SKILL.md)**：用《毛选》的方法做工程决策——调查／主要矛盾／集中兵力／实践检验。
- **[mao-collaboration](./mao-collaboration/SKILL.md)**：用《党委会的工作方法》做汇报／评审／复盘（胸中有数、互通情报、不写党八股）。
