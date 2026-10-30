#!/usr/bin/env python3
"""plan-status.py 生命周期用例（S7-b1）：python3 scripts/tests/test_plan_status_lifecycle.py

覆盖两缺陷契约：
① Current Phase 自动推导——一律取 Phases 中首个 Status≠complete 的阶段名；
   全部 complete 回落手工 ## Current Phase（收口说明兜底），再无则显示收口态；
   活跃期手工字段被忽略（治手工滞后）。
② Deviation 行内已解决标记（任意列含「已解决」即命中）——看板活跃 Deviation
   过滤该行，节尾注「已解决 N 条（折叠）」；无标记行=活跃（兼容存量）；
   整节无活跃行显示收口态；health 偏差计数同步只数活跃行。
fixture 全部为中性占位内容，不含任何业务词（通用性红线：渲染后无业务词残留）。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_lifecycle", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_lifecycle"] = ps  # dataclass 注解解析需要模块已注册
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


def stream_body(phases: list[tuple[str, str]], declared: str | None = None) -> str:
    """生成一个流 plan 正文：phases=[(阶段名, status)]；declared=手工 Current Phase 值。"""
    head = ["# 流 plan", "", "## Goal", "目标。", "", "## Next Step", "下一步。", ""]
    declared_block = ["## Current Phase", declared, ""] if declared is not None else []
    lines = head + declared_block + ["## Phases", ""]
    for i, (name, status) in enumerate(phases, 1):
        lines += [f"### Phase {i}: {name}", f"**Status:** {status}", ""]
    lines += ["## Decisions Made", "", "| Decision | Rationale |", "|----------|-----------|", ""]
    return "\n".join(lines) + "\n"


def portfolio_body(deviation_rows: list[str] | None, has_section: bool = True) -> str:
    """生成 portfolio 正文：deviation_rows=None 表无 Deviation 节；[] 表空表；[...] 表数据行。"""
    lines = [
        "# 测试项目组合 plan",
        "",
        "## Goal",
        "合成验证用目标。",
        "",
        "## Next Step",
        "推进流 A。",
        "",
        "## Phases",
        "",
        "### Phase 1: 流A",
        "- plan: `.planning/stream-a/task_plan.md`",
        "**Status:** pending",
        "",
        "## Backlog",
        "",
        "| # | 目标 | P | 依赖 | 阻塞 |",
        "|---|---|---|---|---|",
        "",
    ]
    if has_section:
        lines += ["## Deviation", "", "| 预期 | 实际 | 原因 | 待决策 |", "|---|---|---|---|"]
        lines += deviation_rows or []
        lines += [""]
    return "\n".join(lines) + "\n"


class CurrentPhaseDerivationTest(unittest.TestCase):
    """契约①：看板/解析层 Current Phase 一律=首个 Status≠complete 的 Phase 名。"""

    def _plan(self, phases, declared=None) -> ps.Plan:
        sections = {"Current Phase": [declared]} if declared is not None else {}
        return ps.Plan(path=Path("x/task_plan.md"), root=Path("."),
                       phases=list(phases), sections=sections)

    def test_first_noncomplete_wins(self):
        plan = self._plan([("阶段一", "complete"), ("阶段二", "in_progress"), ("阶段三", "pending")])
        self.assertEqual(ps.current_phase(plan), "阶段二")

    def test_pending_after_complete_is_active(self):
        # 中间全 complete、后续 pending：旧逻辑只找 in_progress 会误报「—」
        plan = self._plan([("阶段一", "complete"), ("阶段二", "complete"), ("阶段三", "pending")])
        self.assertEqual(ps.current_phase(plan), "阶段三")

    def test_manual_ignored_while_active(self):
        # 活跃期手工 ## Current Phase 不参与显示（防手工滞后误导）
        plan = self._plan([("阶段一", "complete"), ("阶段二", "in_progress")], declared="阶段九（手工滞后）")
        self.assertEqual(ps.current_phase(plan), "阶段二")

    def test_all_complete_falls_back_to_declared(self):
        # 全部 complete：手工值作收口说明兜底
        plan = self._plan([("阶段一", "complete")], declared="已收口（全部阶段完成）")
        self.assertEqual(ps.current_phase(plan), "已收口（全部阶段完成）")

    def test_all_complete_no_declared_shows_done_mark(self):
        plan = self._plan([("阶段一", "complete"), ("阶段二", "complete")])
        self.assertEqual(ps.current_phase(plan), ps.ALL_DONE_MARK)

    def test_no_phases_em_dash(self):
        plan = self._plan([])
        self.assertEqual(ps.current_phase(plan), "—")


class DeviationLifecycleTest(unittest.TestCase):
    """契约②：行内「已解决」过滤 + 折叠计数 + 收口态 + 存量兼容。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _board_deviation(self, body: str) -> str:
        root = make_repo(self.tmp, {"x-portfolio": body, "stream-a": stream_body([("阶段一", "complete")])})
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        return board.split("## 偏差明细", 1)[1].split("## Backlog", 1)[0]

    def test_resolved_row_folded_with_count(self):
        body = portfolio_body([
            "| 预期甲 | 实际甲 | 原因甲 | — |",
            "| 预期乙 | 实际乙 | 原因乙 | 已解决（2026-10-29） |",
        ])
        dev = self._board_deviation(body)
        self.assertIn("| 预期甲 |", dev)
        self.assertNotIn("预期乙", dev)  # 已解决行被过滤
        self.assertIn("已解决 1 条（折叠）", dev)

    def test_resolved_mark_hits_any_column(self):
        # 行内任意列含「已解决」即命中（不限于待决策列）
        body = portfolio_body([
            "| 预期甲（已解决） | 实际甲 | 原因甲 | — |",
            "| 预期乙 | 实际乙 | 原因乙 | — |",
        ])
        dev = self._board_deviation(body)
        self.assertNotIn("预期甲（已解决）", dev)
        self.assertIn("| 预期乙 |", dev)
        self.assertIn("已解决 1 条（折叠）", dev)

    def test_all_resolved_closed_state(self):
        # 整节无活跃行：显示收口态，不渲染表头与数据行
        body = portfolio_body([
            "| 预期甲 | 实际甲 | 原因甲 | 已解决（2026-10-28） |",
            "| 预期乙 | 实际乙 | 原因乙 | 已解决（2026-10-29） |",
        ])
        dev = self._board_deviation(body)
        self.assertNotIn("| 预期 | 实际 |", dev)
        self.assertIn("2 条已解决", dev)

    def test_no_deviation_section_compat(self):
        # 无 Deviation 节：兼容存量，显示「—」
        dev = self._board_deviation(portfolio_body(None, has_section=False))
        self.assertIn("—", dev)
        self.assertNotIn("已解决", dev)

    def test_empty_deviation_table_compat(self):
        # 空表（只有表头）：同样显示「—」
        dev = self._board_deviation(portfolio_body([]))
        self.assertIn("—", dev)
        self.assertNotIn("已解决", dev)

    def test_health_counts_active_only(self):
        # health 偏差计数同步只数活跃行：仅剩已解决行时不再 🔴
        body = portfolio_body(["| 预期甲 | 实际甲 | 原因甲 | 已解决（2026-10-29） |"])
        root = make_repo(self.tmp, {"x-portfolio": body, "stream-a": stream_body([("阶段一", "complete")])})
        plan = ps.parse_plan(root / ".planning/x-portfolio/task_plan.md", root)
        self.assertEqual(plan.deviations, 0)
        self.assertNotEqual(plan.health, ps.HEALTH_BLOCKED)

    def test_active_deviation_still_blocks(self):
        body = portfolio_body(["| 预期甲 | 实际甲 | 原因甲 | — |"])
        root = make_repo(self.tmp, {"x-portfolio": body, "stream-a": stream_body([("阶段一", "complete")])})
        plan = ps.parse_plan(root / ".planning/x-portfolio/task_plan.md", root)
        self.assertEqual(plan.deviations, 1)
        self.assertEqual(plan.health, ps.HEALTH_BLOCKED)


class RenderIntegrationTest(unittest.TestCase):
    """流表「当前阶段」列取推导值（含手工滞后 plan 的实测形态）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_stream_table_shows_derived_phase(self):
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body([]),
            "stream-a": stream_body(
                [("阶段一", "complete"), ("阶段二", "complete"), ("阶段三", "pending")],
                declared="阶段二（手工滞后）"),
        })
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("| 阶段三 |", board)
        self.assertNotIn("阶段二（手工滞后）", board)

    def test_selftest_green_on_synthetic_repo(self):
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body(["| 预期甲 | 实际甲 | 原因甲 | 已解决（2026-10-29） |"]),
            "stream-a": stream_body([("阶段一", "complete")]),
        })
        self.assertEqual(ps.main(["--root", str(root), "--selftest"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
