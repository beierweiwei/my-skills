# 工单：<一句话目标>
## 目标
<完成后要达成的业务状态；一句话。>
## 现状入口
<当前行为/缺口；真实入口：文档/页面/命令/接口；返工/修复/升级写上一版为什么没生效（根因），首版写「无（首版）」。>
## 验收断言
- <可现场复现的通过条件 1>
- <可现场复现的通过条件 2>
## 边界规则
<保护资产、单写者、最小改动、AMEND、发现清单；派单/工位变更只走 python3 scripts/dispatch.py；引用 D3–D6、X1–X5。>
## 回执要求
报告落盘：禁止无引号 heredoc/命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具。
<按 R1–R6 落盘：status、changed_files、evidence、findings、work_slot、summary；证据用 `python3 scripts/crew-evidence.py --out <file> -- <命令>` 采集（命令/rc/stdout/stderr/时间/git status），缺该工具时手写落盘仍有效。>
<blocked 处置映射（动作语义≠status）：re-task→done 标「已转出」；close→done 标「已闭环」；escalate→captain 标「待 Captain」。>
<未处置保持 `blocked`，不得用 done/captain 掩盖。>
