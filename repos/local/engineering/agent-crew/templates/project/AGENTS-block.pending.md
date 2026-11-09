# AGENTS.md 编排节待插入（pending）

> init 检测到 `AGENTS.md` 存在但没有 agent-crew 受管标记：按契约零写入，只生成本文件。
> 完成下面三步后重跑 `python3 scripts/init.py`，由 init 接管受管块。

## 第一步：插到哪

- 打开项目根目录 `AGENTS.md`。
- 选择编排规则应处的位置：通常在项目自有规则之后、文档/命令清单之前；不要插进代码块、引用块或已有受管标记内。

## 第二步：插什么

- 把本次 init 渲染出的编排节整体插入：以 `<!-- agent-crew:orchestration:start -->` 开头、`<!-- agent-crew:orchestration:end -->` 结尾，标记各一行、行首无缩进。
- 不要只插半对标记，不要嵌套第二对，不要复制成两份。

## 第三步：插完怎么验

- 重跑 `python3 scripts/init.py --check`：无「无标记 / pending」告警。
- 跑 `python3 scripts/doctor.py --root . --only agents.markers --json`：状态为 pass，且恰好一对标记。
- 跑 `python3 scripts/doctor.py --root . --only agents.block --json`：块体非空、≤150 行、无未解析 token、指针指向当前 skillRoot。
- 若提示块体漂移：先人工比对，再执行 `python3 scripts/init.py --refresh-block --confirm`（只改块体，块外不动）。
