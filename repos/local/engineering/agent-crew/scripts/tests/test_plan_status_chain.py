#!/usr/bin/env python3
"""plan-status.py 业务链视图用例（S8-b1）：python3 scripts/tests/test_plan_status_chain.py

覆盖看板与 plan 写法四规则的看板侧落地（AGENTS 编排效率原则 11 增补 + Captain 2026-10-30 渲染细化）：
① 业务链表解析：节名含「业务链」即命中（兼容「业务链节点（…）」带后缀形态），
   | 节点 | 状态 | 说明 | 表按位置取列（列头名宽松：说明/目标完成状态说明均兼容）；
   状态列 todo 三元 [x]/[ ]/[⏸]，其余值宽松映射 todo；说明列内嵌标记不翻转节点状态。
② 看板顶部业务链总览：按流分节、行首标准 md todo 语法（- [x] / - [ ] / - [ ] ⏸ …（待 Captain）），
   状态灯嵌行（[x]✅ / [ ]🟡 / ⏸🔴），链序=表序，附自解释图例。
③ 待 Captain 汇总置顶：各 plan Backlog/业务链表 [⏸] 条目汇总（标准 todo + ⏸ 入文字），
   portfolio C 前缀行保留并同样置顶。
④ 排序：业务链总览+待 Captain 在前，流表等工单细节置后；
   流表只留概览列（流/当前阶段/阶段完成/健康），各流 todo 细节在流表下方按流分节渲染
   （#### 流名 + 行首 todo 列表，[⏸] 转 - [ ] ⏸ …（待 Captain））。
⑤ 降级与兼容：无业务链表不渲染该视图（零破坏）；plan checklist 与业务链表两种载体都解析；
   与 S7-b1（Current Phase 推导 / Deviation 生命周期）共存。
fixture 全部为中性占位内容，不含任何业务词（通用性红线：渲染后无业务词残留）。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_chain", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_chain"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)


def make_repo(tmp: Path, plans: dict[str, str]) -> Path:
    """建一个最小目标仓：.planning/<name>/task_plan.md（无配置文件=内置默认）。"""
    root = tmp / "repo"
    planning = root / ".planning"
    planning.mkdir(parents=True)
    for name, body in plans.items():
        d = planning / name
        d.mkdir()
        (d / "task_plan.md").write_text(body, encoding="utf-8")
    return root


def chain_body(
    rows: list[tuple[str, str, str | None]],
    section: str = "业务链",
    header: str = "| 节点 | 状态 | 说明 |",
    title: str = "流 plan A",
    next_step: str = "",
    phases: list[tuple[str, str]] | None = None,
) -> str:
    """生成带业务链表的流 plan 正文；rows=[(节点, 状态列, 说明)]（说明 None=缺列）。"""
    lines = [f"# {title}", "", "## Goal", "目标。", "", f"## {section}", "", header, "|---|---|---|"]
    for node, mark, desc in rows:
        cells = [node, mark] if desc is None else [node, mark, desc]
        lines.append("| " + " | ".join(cells) + " |")
    lines += ["", "## Phases", ""]
    for i, (name, status) in enumerate(phases or [("阶段一", "pending")], 1):
        lines += [f"### Phase {i}: {name}", f"**Status:** {status}", ""]
    if next_step:
        lines += ["## Next Step", next_step, ""]
    return "\n".join(lines) + "\n"


def plain_body(title: str = "流 plan B") -> str:
    """生成无业务链表的普通流 plan 正文（降级夹具）。"""
    return "\n".join([
        f"# {title}", "", "## Goal", "目标。", "", "## Next Step", "下一步。", "",
        "## Phases", "", "### Phase 1: 阶段一", "**Status:** pending", "",
    ]) + "\n"


def portfolio_body(backlog_rows: list[str] | None = None) -> str:
    """生成 portfolio 正文（无 Deviation 节；backlog_rows=追加的 Backlog 数据行）。"""
    lines = [
        "# 测试项目组合 plan", "", "## Goal", "合成验证用目标。", "",
        "## Next Step", "推进流 A。", "", "## Phases", "",
        "### Phase 1: 流A", "- plan: `.planning/stream-a/task_plan.md`", "**Status:** pending", "",
        "## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|",
    ]
    lines += backlog_rows or []
    lines += [""]
    return "\n".join(lines) + "\n"


SAMPLE_ROWS = [
    ("节点一", "[x]", "节点一已打通（真实数据）。"),
    ("节点二", "[ ]", "节点二待接：聚合视图未通。"),
    ("节点三", "[⏸]", "[⏸] 真实发送待放行（待 Captain）。"),
]


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _parse(self, body: str, name: str = "stream-a") -> ps.Plan:
        root = make_repo(self.tmp, {name: body})
        return ps.parse_plan(root / f".planning/{name}/task_plan.md", root)

    def _render(self, plans_map: dict[str, str]) -> str:
        root = make_repo(self.tmp, plans_map)
        return ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))


class ChainParseTest(_TmpRepoTest):
    """契约①：业务链表解析（节名/列头宽松、三元状态、链序=表序、双载体兼容）。"""

    def test_plain_section_name_hits(self):
        plan = self._parse(chain_body(SAMPLE_ROWS))
        self.assertEqual([n for n, _, _ in plan.chain], ["节点一", "节点二", "节点三"])

    def test_section_name_with_suffix_hits(self):
        plan = self._parse(chain_body(SAMPLE_ROWS, section="业务链节点（合成 8 节点 · 实况）"))
        self.assertEqual(len(plan.chain), 3)

    def test_status_three_marks(self):
        plan = self._parse(chain_body(SAMPLE_ROWS))
        self.assertEqual([m for _, m, _ in plan.chain], ["done", "todo", "captain"])

    def test_unknown_status_maps_todo(self):
        # 状态列非三元写法（如「完成」二字）：宽松映射 todo，不抛错不吞行
        plan = self._parse(chain_body([("节点一", "完成", "人话。")]))
        self.assertEqual(plan.chain[0][1], "todo")

    def test_pause_mark_bracket_form_regression(self):
        # B1-fix 回归守护：[⏸] 原形态仍判 captain；裸 ⏸（b2 文档定稿单值简写）等价命中
        plan = self._parse(chain_body([("节点甲", "[⏸]", "。"), ("节点乙", "⏸", "。")]))
        self.assertEqual([m for _, m, _ in plan.chain], ["captain", "captain"])

    def test_header_variant_by_position(self):
        # 列头「目标完成状态说明」（真实表形态）仍按位置取第三列
        plan = self._parse(chain_body(SAMPLE_ROWS, header="| 节点 | 状态 | 目标完成状态说明 |"))
        self.assertEqual(plan.chain[0][2], "节点一已打通（真实数据）。")

    def test_missing_desc_column_ok(self):
        plan = self._parse(chain_body([("节点一", "[x]", None)]))
        self.assertEqual(plan.chain[0], ("节点一", "done", ""))

    def test_chain_order_is_table_order(self):
        rows = [("节点乙", "[x]", "。"), ("节点甲", "[ ]", "。")]  # 名字乱序，链序=表序
        plan = self._parse(chain_body(rows))
        self.assertEqual([n for n, _, _ in plan.chain], ["节点乙", "节点甲"])

    def test_desc_nested_mark_does_not_flip_status(self):
        # 状态列为准：说明列内嵌 [⏸] 子项不改变节点状态（已完成节点说明里嵌待放行的真实形态）
        plan = self._parse(chain_body([("节点一", "[x]", "[⏸] 子项待放行。")]))
        self.assertEqual(plan.chain[0][1], "done")

    def test_checklist_carrier_also_parsed(self):
        # 双载体：行首 todo checklist（plan Next Step 本形）与业务链表都能解析
        body = chain_body(SAMPLE_ROWS, next_step="- [x] 件一完成\n- [ ] ⏸ 件二放行（待 Captain）")
        root = make_repo(self.tmp, {"stream-a": body})
        lines = ps._next_step_lines(ps.parse_plan(root / ".planning/stream-a/task_plan.md", root))
        self.assertEqual(lines, ["- [x] 件一完成", "- [ ] ⏸ 件二放行（待 Captain）"])


class ChainRenderTest(_TmpRepoTest):
    """契约②④：顶部总览（标准 todo 语法+状态灯+图例）、降级、多流多表。"""

    def test_overview_at_top_with_legend(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": chain_body(SAMPLE_ROWS)})
        self.assertLess(board.index("## 业务链总览"), board.index("## 当前目标"))
        self.assertIn("图例：[x] ✅ 完成 · [ ] 🟡 未完成 · ⏸ 🔴 待 Captain", board)

    def test_node_lines_standard_todo_with_lights(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": chain_body(SAMPLE_ROWS)})
        overview = board.split("## 业务链总览", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("- [x] ✅ **节点一** — 节点一已打通（真实数据）。", overview)
        self.assertIn("- [ ] 🟡 **节点二** — 节点二待接：聚合视图未通。", overview)
        # 待 Captain 项：标准 checkbox + ⏸ 入文字；说明列内嵌 [⏸] 过状态灯
        self.assertIn("- [ ] ⏸ **节点三** — ⏸ 真实发送待放行（待 Captain）。", overview)

    def test_no_chain_section_degrades(self):
        # 全 portfolio 无业务链表：无总览节，其余结构同现状（零破坏）
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": plain_body()})
        self.assertNotIn("## 业务链总览", board)
        self.assertIn("## 当前目标", board)
        self.assertIn("## 流表", board)
        self.assertIn("## 偏差明细", board)
        self.assertIn("## Backlog", board)

    def test_partial_chain_renders_only_chain_plans(self):
        board = self._render({
            "x-portfolio": portfolio_body(),
            "stream-a": chain_body(SAMPLE_ROWS, title="流 plan A"),
            "stream-b": plain_body("流 plan B"),
        })
        overview = board.split("## 业务链总览", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("#### 流A", overview)  # 子块标题=流名（portfolio phase 名）优先
        self.assertNotIn("#### 流 plan B", overview)  # 无业务链表的流不渲染

    def test_multi_stream_tables_in_sorted_order(self):
        pbody = portfolio_body().replace(
            "**Status:** pending",
            "**Status:** pending\n\n### Phase 2: 流B\n"
            "- plan: `.planning/stream-b/task_plan.md`\n**Status:** pending")
        board = self._render({
            "x-portfolio": pbody,
            "stream-a": chain_body(SAMPLE_ROWS, title="流 plan A"),
            "stream-b": chain_body([("节点九", "[x]", "。")], title="流 plan B"),
        })
        overview = board.split("## 业务链总览", 1)[1].split("## 待 Captain", 1)[0]
        self.assertLess(overview.index("#### 流A"), overview.index("#### 流B"))
        self.assertIn("- [x] ✅ **节点九** — 。", overview)


class CaptainSummaryTest(_TmpRepoTest):
    """契约③：待 Captain 汇总置顶（标准 todo 语法，业务链 [⏸] 节点 + Backlog [⏸] 行 + C 行）。"""

    def _captain(self, board: str) -> str:
        return board.split("## 待 Captain", 1)[1].split("## 当前目标", 1)[0]

    def test_chain_captain_node_summarized(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": chain_body(SAMPLE_ROWS)})
        captain = self._captain(board)
        self.assertIn("- [ ] ⏸ **节点三**（流A）— ⏸ 真实发送待放行（待 Captain）。", captain)
        self.assertNotIn("**节点一**", captain)  # 非 captain 节点不进汇总

    def test_backlog_captain_row_summarized(self):
        board = self._render({"x-portfolio": portfolio_body(
            ["| T9 | [⏸] 真实放行任务 | P1 | — | 待 C4 |"])})
        captain = self._captain(board)
        self.assertIn("- [ ] ⏸ **T9**（测试项目组合 plan）⏸ 真实放行任务 — 待 C4", captain)

    def test_portfolio_c_rows_still_listed(self):
        board = self._render({"x-portfolio": portfolio_body(["| C1 | 决策一 | P0 | — | 待定 |"])})
        self.assertIn("- [ ] ⏸ **C1** 决策一 — 待定（待 Captain）", self._captain(board))

    def test_bare_pause_mark_rendered_captain_and_summarized(self):
        # B1-fix：状态列裸 ⏸（b2 文档定稿简写）渲染 captain 灯语义并进待 Captain 汇总
        board = self._render({"x-portfolio": portfolio_body(),
                              "stream-a": chain_body([("节点四", "⏸", "真实发送待放行。")])})
        overview = board.split("## 业务链总览", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("- [ ] ⏸ **节点四** — 真实发送待放行。", overview)
        self.assertNotIn("🟡 **节点四**", overview)  # 不再静默降级为未完成
        captain = self._captain(board)
        self.assertIn("- [ ] ⏸ **节点四**（流A）— 真实发送待放行。", captain)

    def test_n_prefix_pause_row_summarized(self):
        # S8-hotfix：N 前缀 + ⏸ 标记行进待 Captain 汇总（⏸ 语义优先于前缀）；
        # 阻塞列 `[ ] ⏸` 形态（行首 todo 标准语法嵌格）与本仓 N7–N10 实况同形
        board = self._render({"x-portfolio": portfolio_body(
            ["| N7 | 服务探针未实现 | P2 | — | [ ] ⏸ 待 Captain 排期 |"])})
        captain = self._captain(board)
        self.assertIn(
            "- [ ] ⏸ **N7**（测试项目组合 plan）服务探针未实现 — 🟡 ⏸ 待 Captain 排期",
            captain)
        # 分流互斥：⏸ 行不留在 Backlog 表格（一处显示，不两处记账）
        self.assertNotIn("| N7 |", board.split("## Backlog", 1)[1])

    def test_n_prefix_plain_row_stays_backlog(self):
        # S8-hotfix：N 前缀无 ⏸ 行进 Backlog（agent 可执行），不进待 Captain；C 前缀无 ⏸ 兼容不变
        board = self._render({"x-portfolio": portfolio_body(
            ["| N3 | 服务进程内存储改造 | P3 | — | — |",
             "| C1 | 决策一 | P0 | — | 待定 |"])})
        self.assertIn("| N3 | 服务进程内存储改造 | P3 | — | — |",
                      board.split("## Backlog", 1)[1])
        self.assertNotIn("**N3**", self._captain(board))
        self.assertIn("- [ ] ⏸ **C1** 决策一 — 待定（待 Captain）", self._captain(board))

    def test_t_prefix_pause_row_exits_backlog(self):
        # S8-hotfix 互斥语义：T+⏸ 进待 Captain（既有）且退出 Backlog 表格（新增口径）
        board = self._render({"x-portfolio": portfolio_body(
            ["| T9 | [⏸] 真实放行任务 | P1 | — | 待 C4 |"])})
        self.assertIn("- [ ] ⏸ **T9**（测试项目组合 plan）", self._captain(board))
        self.assertNotIn("| T9 |", board.split("## Backlog", 1)[1])

    def test_captain_block_before_stream_table(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": chain_body(SAMPLE_ROWS)})
        self.assertLess(board.index("## 待 Captain"), board.index("## 流表"))

    def test_empty_captain_shows_em_dash(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": plain_body()})
        self.assertIn("—", self._captain(board))


class OrderCompatTest(_TmpRepoTest):
    """契约④⑤：节排序、流表概览列+分节 todo、与 S7-b1 共存、selftest。"""

    def test_section_order(self):
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": chain_body(SAMPLE_ROWS)})
        seq = [board.index(s) for s in
               ["## 业务链总览", "## 待 Captain", "## 当前目标", "## 流表", "## 偏差明细", "## Backlog"]]
        self.assertEqual(seq, sorted(seq), f"节顺序不对：{seq}")

    def test_stream_table_overview_only_columns(self):
        # 流表只留概览列（流/当前阶段/阶段完成/健康），Next Step 不再塞格子
        body = chain_body(SAMPLE_ROWS, title="流 plan A",
                          next_step="- [x] 件一完成（已推送）\n- [ ] 件二进行中")
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": body})
        table = board.split("## 流表", 1)[1].split("## 偏差明细", 1)[0]
        self.assertIn("| 流 | 当前阶段 | 阶段完成 | 健康 |", table)
        self.assertIn("| 流A |", table)
        self.assertNotIn("件一完成", table.split("\n\n")[0])  # 表格段无 todo 细节

    def test_stream_todo_details_below_table_per_stream(self):
        # 各流 todo 细节：流表下方按流分节（#### 流名 + 行首 todo 列表，[⏸] 转标准格式）
        body = chain_body(
            SAMPLE_ROWS, title="流 plan A",
            next_step="- [x] 件一完成（已推送）\n- [ ] 件二进行中\n- 件三普通行\n- [⏸] 件四放行")
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": body})
        table = board.split("## 流表", 1)[1].split("## 偏差明细", 1)[0]
        self.assertIn("#### 流A", table)
        self.assertIn("- [x] 件一完成（已推送）", table)
        self.assertIn("- [ ] 件二进行中", table)
        self.assertIn("- 件三普通行", table)
        self.assertIn("- [ ] ⏸ 件四放行（待 Captain）", table)
        self.assertNotIn("[x] 件一完成", table.replace("- [x] 件一完成（已推送）", ""))  # 无残留旧形态
        self.assertNotIn("- [⏸] 件四放行", table)  # 非标 [⏸] checkbox 不原样透出

    def test_deviation_lifecycle_coexists(self):
        # S7-b1 已解决折叠（活跃行保留+尾注计数）与业务链总览共存
        pbody = portfolio_body(["| C1 | 决策一 | P0 | — | 待定 |"]).replace(
            "## Backlog",
            "## Deviation\n\n| 预期 | 实际 | 原因 | 待决策 |\n|---|---|---|---|\n"
            "| 预期甲 | 实际甲 | 原因甲 | — |\n"
            "| 预期乙 | 实际乙 | 原因乙 | 已解决（2026-10-29） |\n\n## Backlog")
        board = self._render({"x-portfolio": pbody, "stream-a": chain_body(SAMPLE_ROWS)})
        self.assertIn("## 业务链总览", board)
        self.assertIn("已解决 1 条（折叠）", board)
        self.assertIn("- [ ] ⏸ **C1** 决策一 — 待定（待 Captain）", board)

    def test_current_phase_derivation_coexists(self):
        # 流表「当前阶段」推导（S7-b1）不受业务链表影响
        body = chain_body(SAMPLE_ROWS, title="流 plan A",
                          phases=[("阶段一", "complete"), ("阶段二", "pending")])
        board = self._render({"x-portfolio": portfolio_body(), "stream-a": body})
        table = board.split("## 流表", 1)[1].split("## 偏差明细", 1)[0]
        self.assertIn("| 阶段二 |", table)
        self.assertNotIn("| 阶段一 |", table)

    def test_selftest_green_with_chain(self):
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body(["| C1 | 决策一 | P0 | — | 待定 |"]),
            "stream-a": chain_body(SAMPLE_ROWS),
        })
        self.assertEqual(ps.main(["--root", str(root), "--selftest"]), 0)

    def test_selftest_green_with_prose_next_steps(self):
        # S8-hotfix 回归守护：总舵 Next Step 散文 `-` 行 + 流分节 todo 存在时，
        # captain 守门切片须止于紧邻 ## 节（旧口径切到 ## Backlog 会误吞中间节报混入）
        pbody = portfolio_body(["| C1 | 决策一 | P0 | — | 待定 |"]).replace(
            "## Next Step\n推进流 A。",
            "## Next Step\n- ✅ 事一完成 - 🟡 ⏸ 事二待决（待 Captain）")
        root = make_repo(self.tmp, {
            "x-portfolio": pbody,
            "stream-a": chain_body(SAMPLE_ROWS,
                                   next_step="- [x] 件一完成\n- [ ] 件二待办"),
        })
        self.assertEqual(ps.main(["--root", str(root), "--selftest"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
