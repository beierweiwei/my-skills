# 复盘 <YYYY-MM-DD>

- 触发：<版本发布 / 验收门签认 / 批量收口 / 每周兜底>
- 窗口：<YYYY-MM-DD> ~ <YYYY-MM-DD>（上次复盘：<日期|无>）
- 输入：feedback 待复盘 <N>；progress 里程 <M> 段；流 plan Errors <K> 条；收口 gap <G> 条
- 结论：<闭环通过 / 未完成（原因）>

## 四路归类

| # | 条目（来源） | 归类 | 证据（文件:行 / commit / 回执） | 建议（文件+条目） | 需 Captain | 去向 |
|---|---|---|---|---|---|---|
| 1 | FB-003 | ①编排流程 | progress.md:41 | `references/crew-rules.md` **R5**：补… | 是（批次） | 已批 → 工单 2.2.4 |
| 2 | F-001 复发 | ②repo 犯错 | commit abc / 回执 x | 转 correct：执行器=… | 否 | 转_correct（待 T12） |
| 3 | 工具坑 X | ③跨项目 | 会话经历 | inone save（kind=procedural） | 否 | 已_inone #123 |
| 4 | FB-002 | ④单次/过期 | — | 已被 2.2.2 覆盖 | 否 | 归档（重复） |

## 闭环检查

- 待复盘入：<N> 条；出：<N> 条；剩余：0 条（或：剩余 <k> 条，均 ⏸ 待 Captain，已列下节）
- 已改：<文件:条目>（commit <sha>，测试 <命令/结果>）
- 转出：correct <k> 条 / inone <k> 条
- 归档：<k> 条（原因）
- 信箱只进不出判定：<通过 / 未完成>

## 待 Captain

1. <批次批准：N 条修改建议（明细见上表）｜为什么｜选项（推荐）>
