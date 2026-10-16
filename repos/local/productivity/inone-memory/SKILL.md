---
name: inone-memory
description: 保存/检索 inone 统一记忆。当用户在会话中说「记住 X」「以后都……」「别忘了……」「保存这条经验」,或出现值得跨会话保留的持久认知(agent 亲历的坑与解法、用户偏好/纠错、关键项目决策——主动推送,无需等「记住」),或要求检索个人偏好/事实/经验教训时使用。推送直接入库即时生效(进检索与下次分发);不当记忆随时在溯源页审核工作台删除。触发词:保存记忆、记住、remember、统一记忆、查偏好。
---

把会话中值得跨会话保留的内容推送到 inone 统一记忆中枢,或按需检索已生效记忆
与收藏知识。**写后审(ADR 0012)**:save 直接入库即时生效(进检索与下次分发),
无需事先批准;推错了由用户在溯源页「审核工作台」随手删除——唯一的纠错通道,
记住这一句即可,下文不再重复。

## 何时用 / 存什么

- 用户说「记住 X」「以后都这样」「别再犯这个错」→ `save`;
- 出现值得跨会话保留的持久认知 → 主动 `save`,无需等指令(各家 auto-memory
  同款模式):agent 亲历的坑与解法(最重要)→ `procedural`;用户明示偏好 →
  `semantic`、纠错/做事规则 → `procedural`;代码/git 推不出的项目决策 →
  `semantic` + `--scope project:<名>`;外部资源指针 → `semantic`;
- 新任务需要背景(偏好/项目上下文/经验教训)→ `search`;
- 不存:仓库/git/AGENTS.md 已有的信息、只对当前任务有用的临时状态、快过期的
  安排、**计划/理想/打算**(用户反复驳回「这是计划和理想,不是事实」——只存
  已成立的事实与规则,「我打算/我先按……启动」类未兑现的意图不推);克制:
  单会话通常 0-2 条,没有值得存的就 0 条(正常);
- 表述要求(用户高频归档理由:不清晰/太抽象/不这么绝对):具体、可执行、
  带场景,不推抽象口号式泛则;避免绝对化措辞,有适用边界的写清边界;
- 保持干净:推前先 `search` 查重;表述过时推新条目并提请用户归档旧条目
  (审核样本中「已有替代软件/偏好已变」即此通道,新条目直接写当下事实);
  发现已生效记忆有错推 `--negative` 负例;冒烟/部署验证类测试条目即测即清,
  验收后提请用户归档,不留存量;
- 常驻背景知识由全局 AGENTS.md 指向的 `MEMORY-CORE.md` 承载(被动通道,无需本技能)。

## 怎么用

脚本 `scripts/inone_memory.py`(仅 Python 标准库)。环境变量(或 login 写
`~/.inone/config.json`,环境变量优先):

```bash
export INONE_MEMORY_URL=http://<NAS_LAN_IP>:8095   # 溯源页同服务
export INONE_MEMORY_TOKEN=<token>                  # 可选(未设鉴权则省)
export INONE_AGENT_NAME=<agent 名>             # 记账/证据链归因,缺省落 unknown
```

token 从溯源页「凭证管理」生成;`login` 交互粘贴,`update` 随时自更新
(全程 HTTP+token 零 git)。

```bash
python scripts/inone_memory.py save     --statement "Git Bash 下执行脚本先 cd 到仓库根,相对路径才稳定"     --kind procedural     --evidence "会话经历:win32 下从其他目录以相对路径执行失败,cd 后通过"
```

- `--kind` 必填无缺省(ADR 0018),判型问「这条认知以什么形态起作用」:改变
  以后的做法(规则/教训/纠错/踩坑解法)→ procedural;叙述发生了什么(经历/
  决策史)→ episodic;只是知道一件事(事实/知识/偏好)→ semantic——对话里
  说的偏好/规则也按形态判,不因出现在对话中就变 episodic;**规则形态的内容
  常被误标 semantic(审核收录审计中最常见的纠偏):凡「应/需要/遵循/优先/
  必须」驱动的做法约束,无论来源是收藏摘录还是对话,一律 procedural**;
- `--statement` 只放陈述句本体:不带提取通道样板尾巴(When/Involving 等字段
  尾巴剥干净,正文不动);
- `--scope`(缺省 user)/`--tag`(可多次)/`--negative`/`--ttl-days N`;
- `--evidence` 如实记来源与场景:用户明示引原话(「用户说:…」),agent 亲历
  写「会话经历:<发生了什么>」。

## 检索与读通道

```bash
python scripts/inone_memory.py search --q "黑苹果"   # 统一召回:记忆+知识混排
python scripts/inone_memory.py get    --source wiki --path entities/Hindsight.md   # 按路径读全文
python scripts/inone_memory.py ls     --source notes --prefix 杂项/ --limit 50     # 盘点收藏目录
```

- search 带 `--q` = 记忆 + 知识(wiki/notes)混排按相关度;紧凑行
  `#id [分] 陈述句` / `[wiki|notes] 路径 [分] 片段`;`--json` 全量(配
  `--max-chars 500` 控重)、`--limit/--offset` 翻页;不带 `--q` = 列全部
  已生效记忆(纯记忆面,不含知识);
- get/ls 的 path 直接用搜索结果的 origin_path(带 `wiki/` 前缀原样可用);
  全文来自 vault 正典,不依赖搜索命中。

## 输出与退出码(读结果前必看)

- **stdout** = 结果本体(`--json` 时逐行 NDJSON,每行一个 JSON 对象);
  **stderr** = 「共 N 条」汇总行 + 全部提示/错误。`2>&1 | jq` 会被汇总行
  弄挂、`2>/dev/null` 会把错误吞成假 0 条——分流用。
- **退出码**:0 = 成功;非零 = 失败(stderr 有入话原因,401 会提示先跑 login)。
- **「共 0 条」≠ 没有**:未降级的 0 条 = 本措辞无命中,换措辞重试再下结论;
  带 `⚠ 本次检索降级兜底` = 服务端检索后端瞬时限流,脚本已自动重试一次,
  仍降级则勿据此下「没有」结论。

## 任务收尾:效用回报(记忆与知识)

对**取用过**的记忆/知识顺手报三态(helped=帮上忙 / unused=没用到 /
harmful=帮倒忙 + 一句话理由)——越用越准的反馈信号,只进统计,不改任何
条目/文件;没有值得报的就跳过,不凑数:

```bash
python scripts/inone_memory.py report --id 12 --outcome helped --reason "按它避开了坑"
python scripts/inone_memory.py report --kpath wiki/entities/H.md --outcome helped --reason "全文直接可用"
```

记忆部分过时:先 `save` 推实测修正条目(证据=会话经历),再对新条目
`report --outcome helped --supersedes <旧条目id>`(只挂建议不改写,退役由用户
终审);可另对旧条目报 `harmful`。

## 铁律

1. 只推跨会话仍成立的持久认知;陈述句 agent 自己写、证据如实记来源与场景(写后审与纠错通道见文首);不推计划/理想,不推抽象与绝对化表述;
2. 检索结果与 `MEMORY-CORE.md` 是参考,不代替当前任务的实际阅读;
3. 本技能永不写入任何 agent 的 auto-memory 目录——那是用户主权的只读区。
