#!/usr/bin/env python3
"""2.2.19-B：看板人类可读（`render`/`--write` → docs/进度追踪.md）。

行为断言（F8，按本流 plan V1–V4 与工单断言）：
① 结构：现在状态一句 + 健康图例一行（S1：🟢 完成/🟡 进行中/🔴 阻塞或偏差）+
   今日变化 ≤3 行 + 流表（流/阶段/完成数+健康/下一步一句话 ≤60/待 Captain 数）+
   待 Captain ≤5 条（≤120 字符）+ 溢出 + 最近完成 ≤5 条（≤160 字符）+ 偏差/Backlog
   计数 + 证据/plan 指针；不输出整段 Next Step/业务链逐节点/各流 todo/明细。
② 体量：≤120 行且 ≤24KB（D-4）；超限确定性截断并给指针；本仓实测另落证据。
③ 口径一致：看板与 `--brief` 同一表头、同一待 Captain 计数、同一今日变化来源。
④ 既有语义：`--selftest` 绿（待 Captain 在流表前/Backlog 不混 ⏸/条目语法）、
   `--check-fresh` 指纹机制与 `--lint` 行为不变；selftest 的 T/C 分流断言对
   portfolio 解析结果生效（回退检测不空转，S4）。
⑤ 排序（R-1）：流表按「活跃 → 待立项 → 已完成」；活跃/待立项不被已完成流挤出，
   已完成流折叠为一行计数 + 指针（回归夹具：25 条已完成在前 + 1 活跃 + 1 待立项）。
fixture 全部为中性占位内容，不含任何业务词。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC_PATH = REPO_ROOT / "scripts" / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_2219_board", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_2219_board"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

TODAY = datetime.now().strftime("%Y-%m-%d")
ACTIVE_STREAMS = 30
CAPTAIN_CAP = 5
RECENT_CAP = 5
TODAY_CAP = 3


def make_repo(tmp: Path, plans: dict[str, str], progress: str | None = None,
              config: dict | None = None) -> Path:
    root = tmp / "repo"
    planning = root / ".planning"
    planning.mkdir(parents=True)
    for name, body in plans.items():
        directory = planning / name
        directory.mkdir()
        (directory / "task_plan.md").write_text(body, encoding="utf-8")
    if progress is not None:
        directory = planning / "portfolio"
        directory.mkdir(exist_ok=True)
        (directory / "progress.md").write_text(progress, encoding="utf-8")
    if config is not None:
        (planning / "orchestration.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def stream_body(next_step: str, phase: str = "阶段二", extra: str = "") -> str:
    return "\n".join([
        "# 流 plan", "", "## Goal", "流目标。", "", "## Next Step", next_step, "",
        "## Phases", "",
        "### Phase 1: 阶段一", "**Status:** complete", "",
        f"### Phase 2: {phase}", "**Status:** in_progress", "",
    ]) + extra


def completed_body() -> str:
    return "\n".join([
        "# 已完成流 plan", "", "## Goal", "流目标。", "", "## Next Step", "无。", "",
        "## Phases", "",
        "### Phase 1: 阶段一", "**Status:** complete", "",
    ]) + "\n"


def portfolio_body(refs: list[str], c_rows: list[str], t_rows: list[str],
                   recent: list[str], deviation: list[str]) -> str:
    lines = [
        "# 测试项目组合 plan", "", "## Goal", "组合目标。", "",
        "## Next Step", "推进流 A。", "", "## 最近完成", "",
    ]
    lines += recent
    lines += ["", "## Phases", ""]
    for index, ref in enumerate(refs, 1):
        lines += [f"### Phase {index}: 流{index:02d}", f"- plan: `{ref}`",
                  "**Status:** pending", ""]
    lines += ["## Deviation", "", "| 预期 | 实际 | 原因 | 待决策 |", "|---|---|---|---|"]
    lines += deviation
    lines += ["", "## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
    lines += c_rows + t_rows
    lines += [""]
    return "\n".join(lines) + "\n"


def progress_body(entries: list[tuple[str, str]]) -> str:
    lines = ["# Progress（编排台账）", "", "## 自动驾驶里程", ""]
    lines += [f"- {day} {text}" for day, text in entries]
    return "\n".join(lines) + "\n"


def build_big_repo(tmp: Path) -> Path:
    refs = [f".planning/stream-{i:02d}/task_plan.md" for i in range(1, ACTIVE_STREAMS + 1)]
    plans = {"portfolio": portfolio_body(
        refs,
        c_rows=[f"| C{i:02d} | 待拍板事项 {i} " + "题" * 300 + " | P0 | — | 需 Captain 决策 |"
                for i in range(1, 21)],
        t_rows=[f"| T{i:02d} | 待办事项 {i} " + "办" * 300 + " | P1 | — | — |"
                for i in range(1, 31)],
        recent=[f"- [x] 最近完成 {i:02d} " + "成" * 300 for i in range(1, 31)],
        deviation=["| 预期甲 | 实际甲 | 原因甲 | — |",
                   "| 预期乙 | 实际乙 | 原因乙 | — |",
                   "| 预期丙 | 实际丙 | 原因丙 | — |",
                   "| 预期丁 | 实际丁 | 原因丁 | 已解决（2026-10-29） |",
                   "| 预期戊 | 实际戊 | 原因戊 | 已解决（2026-10-30） |"],
    )}
    for i in range(1, ACTIVE_STREAMS + 1):
        extra = ""
        if i == 1:
            extra += ("## Backlog\n\n| # | 目标 | P | 依赖 | 阻塞 |\n|---|---|---|---|---|\n"
                      "| N1 | 流内待拍板 | P1 | — | [ ] ⏸ 待 Captain |\n\n")
            extra += ("## 业务链\n\n| 节点 | 状态 | 说明 |\n|---|---|---|\n"
                      "| 节点甲 | [x] | 已打通 |\n"
                      "| 节点乙 | [⏸] | 待放行（待 Captain） |\n\n")
            extra += ("## Deviation\n\n| 预期 | 实际 | 原因 | 待决策 |\n|---|---|---|---|\n"
                      "| 预期甲 | 实际甲 | 原因甲 | — |\n\n")
        plans[f"stream-{i:02d}"] = stream_body(
            f"流{i:02d}下一步-START " + "推" * 5000 + f" TAIL-NEXT-{i:02d}",
            phase="阶段二 " + "阶" * 500,
            extra=extra,
        )
    today_entries = [(TODAY, f"今日条目 {i} " + "今" * 500) for i in range(1, 6)]
    old_entries = [("2026-11-01", "旧条目 " + "旧" * 500)]
    progress = progress_body(today_entries + old_entries)
    config = {"backlog": {"captainBlocks": {"T01": "C01", "T02": "C02"}}}
    return make_repo(tmp, plans, progress=progress, config=config)


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(["--root", str(root), *args])
    return rc, out.getvalue(), err.getvalue()


def section(board: str, header: str) -> str:
    return board.split(header, 1)[1].split("\n## ", 1)[0]


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class BoardStructureTest(_TmpRepoTest):
    """V1 结构 + V2 体量：多流/超长阶段名与 Next Step/20 条待 Captain/30 条待办。"""

    def _board(self) -> str:
        root = build_big_repo(self.tmp)
        rc, out, err = run(root, "--write")
        self.assertEqual(rc, 0, err)
        return (root / "docs/进度追踪.md").read_text(encoding="utf-8")

    def test_size_within_120_lines_and_24kb(self):
        board = self._board()
        self.assertLessEqual(len(board.splitlines()), ps.BOARD_MAX_LINES)
        self.assertLessEqual(len(board.encode("utf-8")), ps.BOARD_MAX_BYTES)

    def test_now_status_sentence(self):
        board = self._board()
        line = next(line for line in board.splitlines() if "**现在：**" in line)
        self.assertIn(f"{ACTIVE_STREAMS} 条活跃流", line)
        self.assertIn("0 条待立项", line)
        self.assertIn("最差健康 🔴", line)
        self.assertIn("今日有变化", line)

    def test_health_legend_line(self):
        # S1：现在句下附一行健康图例，🟢/🟡/🔴 含义可解释。
        board = self._board()
        lines = board.splitlines()
        legend = next(line for line in lines if line.startswith("> 图例"))
        for token in ("🟢 完成", "🟡 进行中", "🔴 阻塞或偏差"):
            self.assertIn(token, legend)
        now_idx = next(i for i, line in enumerate(lines) if "**现在：**" in line)
        self.assertEqual(lines[now_idx + 1], legend)

    def test_pending_streams_not_counted_as_active(self):
        # S-3：无 plan 的流（Backlog 承载）拆为待立项，不计入活跃；流表保留占位。
        root = make_repo(self.tmp, {
            "portfolio": portfolio_body(
                [".planning/stream-a/task_plan.md", ".planning/no-plan/task_plan.md"],
                c_rows=[], t_rows=[], recent=[], deviation=[]),
            "stream-a": stream_body("下一步。"),
        })
        rc, out, err = run(root)
        self.assertEqual(rc, 0, err)
        line = next(line for line in out.splitlines() if "**现在：**" in line)
        self.assertIn("1 条活跃流", line)
        self.assertIn("1 条待立项", line)
        self.assertIn("Backlog 承载", out)  # 流表仍以占位呈现待立项流

    def test_today_changes_at_most_three(self):
        board = self._board()
        today = section(board, "## 今日变化")
        entries = [line for line in today.splitlines() if line.startswith(f"- {TODAY}")]
        self.assertEqual(TODAY_CAP, len(entries))
        self.assertIn("另有 2 条", today)

    def test_captain_summary_capped_with_overflow(self):
        board = self._board()
        captain = section(board, "## 待 Captain")
        items = [line for line in captain.splitlines() if line.startswith("- [ ] ⏸")]
        self.assertEqual(CAPTAIN_CAP, len(items))
        for line in items:
            self.assertLessEqual(len(line), 120)
        self.assertIn("另有 17 条", captain)  # 20 C + 流内 ⏸ 1 + 业务链 ⏸ 1
        # S-2：溢出条目的真源在 portfolio Backlog 与各流 plan（流内 ⏸/业务链 ⏸）。
        overflow = next(line for line in captain.splitlines() if "另有 17 条" in line)
        self.assertIn("Backlog 与各流 plan", overflow)

    def test_stream_table_uses_brief_header_and_columns(self):
        board = self._board()
        table = section(board, "## 流表")
        self.assertIn(ps.BRIEF_TABLE_HEADER, table)
        rows = [line for line in table.splitlines()
                if line.startswith("| 流") and not line.startswith("| 流 |")]
        # R-1：活跃流不被体量上限截断（本夹具 30 条全部在跑）。
        self.assertEqual(ACTIVE_STREAMS, len(rows))
        self.assertIn("| 流01 |", table)
        self.assertIn("| 流30 |", table)
        self.assertNotIn("另有", table)  # 活跃流不再被旧体量上限截出溢出注
        self.assertNotIn("TAIL-NEXT-01", board)  # Next Step 一句话，不整段导出

    def test_recent_completed_capped_one_line_each(self):
        board = self._board()
        recent = section(board, "## 最近完成")
        items = [line for line in recent.splitlines() if line.startswith("- [x]")]
        self.assertEqual(RECENT_CAP, len(items))
        for line in items:
            self.assertLessEqual(len(line), 160)
        self.assertIn("另有 25 条", recent)

    def test_details_not_exported_with_pointers(self):
        board = self._board()
        for legacy in ("## 业务链总览", "## 当前目标", "## 总舵 Next Step",
                       "## 偏差明细", "#### "):
            self.assertNotIn(legacy, board)
        self.assertNotIn("| 预期 | 实际 |", board)
        self.assertNotIn("| T01 |", board)
        self.assertNotIn("**节点甲**", board)  # 业务链逐节点不再导出
        self.assertIn("## 证据/plan 指针", board)
        self.assertIn("task_plan: `.planning/portfolio/task_plan.md`", board)
        self.assertIn("progress: `.planning/portfolio/progress.md`", board)
        self.assertIn("详情 grep", board)

    def test_chain_captain_still_summarized_when_within_cap(self):
        # B 不导出业务链逐节点，但链上 ⏸ 节点仍进待 Captain 摘要（语义保留）。
        root = make_repo(self.tmp, {
            "portfolio": "\n".join([
                "# 组合", "", "## Goal", "目标。", "", "## Phases", "",
                "### Phase 1: 流A", "- plan: `.planning/stream-a/task_plan.md`",
                "**Status:** pending", "", "## Backlog", "",
                "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|", "",
            ]) + "\n",
            "stream-a": stream_body("下一步。", extra=(
                "## 业务链\n\n| 节点 | 状态 | 说明 |\n|---|---|---|\n"
                "| 节点甲 | [x] | 已打通 |\n"
                "| 节点乙 | [⏸] | 待放行（待 Captain） |\n")),
        })
        rc, out, err = run(root)
        self.assertEqual(rc, 0, err)
        self.assertNotIn("**节点甲**", out)
        self.assertIn("- [ ] ⏸ **节点乙**（流A）— 待放行（待 Captain）", out)

    def test_deviation_and_backlog_counts(self):
        board = self._board()
        deviation = section(board, "## 偏差")
        self.assertIn("活跃 3 条", deviation)
        self.assertIn("已解决 2 条（折叠）", deviation)
        backlog = section(board, "## Backlog")
        self.assertIn("agent 待办 30 条", backlog)
        self.assertIn("待 C 阻塞 2 条", backlog)


class StreamPriorityOrderTest(_TmpRepoTest):
    """R-1 回归：25 条已完成流排在 portfolio 前时，活跃/待立项必须仍出现在流表与现在句，
    已完成流折叠为一行计数 + 指针（不得挤掉活跃/待立项）。"""

    DONE_STREAMS = 25

    def _board(self) -> str:
        root = build_completed_first_repo(self.tmp, self.DONE_STREAMS)
        rc, _out, err = run(root, "--write")
        self.assertEqual(rc, 0, err)
        return (root / "docs/进度追踪.md").read_text(encoding="utf-8")

    def test_active_and_pending_survive_completed_flood(self):
        board = self._board()
        now = next(line for line in board.splitlines() if "**现在：**" in line)
        self.assertIn("1 条活跃流", now)
        self.assertIn("1 条待立项", now)
        table = section(board, "## 流表")
        rows = [line for line in table.splitlines()
                if line.startswith("| 流") and not line.startswith("| 流 |")]
        self.assertEqual(2, len(rows), "流表应只列活跃+待立项两条")
        active_row = next(line for line in rows if line.startswith("| 流26 |"))
        self.assertIn("阶段二", active_row)   # 阶段可读
        self.assertIn("1/2", active_row)     # 完成数可读
        self.assertIn("活跃流下一步", active_row)  # 下一步可读
        pending_row = next(line for line in rows if line.startswith("| 流27 |"))
        self.assertIn("Backlog 承载", pending_row)  # 待立项占位可读
        self.assertNotIn("| 流01 |", table)  # 已完成流折叠，不占流表行

    def test_completed_streams_folded_with_pointer(self):
        board = self._board()
        table = section(board, "## 流表")
        fold = next(line for line in table.splitlines() if "已完成" in line)
        self.assertIn(f"已完成 {self.DONE_STREAMS} 条", fold)
        self.assertIn("各流 plan", fold)  # 明细有指针可循

    def test_completed_first_board_within_limits(self):
        board = self._board()
        self.assertLessEqual(len(board.splitlines()), ps.BOARD_MAX_LINES)
        self.assertLessEqual(len(board.encode("utf-8")), ps.BOARD_MAX_BYTES)


def build_completed_first_repo(tmp: Path, done_count: int) -> Path:
    refs = [f".planning/done-{i:02d}/task_plan.md" for i in range(1, done_count + 1)]
    refs += [".planning/active/task_plan.md", ".planning/pending/task_plan.md"]
    plans = {"portfolio": portfolio_body(refs, c_rows=[], t_rows=[], recent=[],
                                         deviation=[])}
    for i in range(1, done_count + 1):
        plans[f"done-{i:02d}"] = completed_body()
    plans["active"] = stream_body("- [ ] 活跃流下一步。")
    return make_repo(tmp, plans)


class BriefConsistencyTest(_TmpRepoTest):
    """V3：看板与 --brief 同一表头/同一待 Captain 计数/同一今日变化来源。"""

    def test_same_header_counts_and_today_source(self):
        root = build_big_repo(self.tmp)
        rc, board_out, err = run(root)
        self.assertEqual(rc, 0, err)
        _rc, brief, _err = run(root, "--brief")
        self.assertIn(ps.BRIEF_TABLE_HEADER, board_out)
        self.assertIn(ps.BRIEF_TABLE_HEADER, brief)
        board_cap = section(board_out, "## 待 Captain")
        brief_cap = brief.split("## 待 Captain", 1)[1]
        board_total = int(board_cap.split("（", 1)[1].split(" 条", 1)[0])
        brief_total = int(brief_cap.split("（", 1)[1].split(" 条", 1)[0])
        self.assertEqual(board_total, brief_total)
        self.assertIn(f"## 今日变化（{TODAY}）", board_out)
        self.assertIn(f"## 今日变化（{TODAY}）", brief)
        self.assertIn(f"- {TODAY} 今日条目 1", board_out)
        self.assertIn(f"- {TODAY} 今日条目 1", brief)


class FitBoardTest(unittest.TestCase):
    """V2：确定性硬截断（行/字节）+ 指针，重复调用结果一致。"""

    def test_fit_board_deterministic_cap(self):
        lines = ["# 标题", "", "> 生成", f"<!-- plan-sources: {ps.BOARD_VERSION} x -->"] + [
            f"- 细节 {i:03d}" for i in range(300)]
        first = ps._fit_board(lines, ".planning/portfolio")
        second = ps._fit_board(lines, ".planning/portfolio")
        self.assertEqual(first, second)
        self.assertLessEqual(len(first), ps.BOARD_MAX_LINES)
        self.assertLessEqual(len(("\n".join(first) + "\n").encode("utf-8")),
                             ps.BOARD_MAX_BYTES)
        self.assertTrue(first[-1].startswith("> 已截断"))
        self.assertIn("<!-- plan-sources:", first[3])  # 指纹行保留，check-fresh 不受影响


class ExistingSemanticsTest(_TmpRepoTest):
    """V4：selftest 语义、check-fresh 指纹、lint 行为不回归。"""

    def test_selftest_fresh_lint_green(self):
        root = build_big_repo(self.tmp)
        rc, out, err = run(root, "--write")
        self.assertEqual(rc, 0, err)
        rc, out, err = run(root, "--check-fresh")
        self.assertEqual(rc, 0, err)
        self.assertIn("新鲜", out)
        rc, out, err = run(root, "--selftest")
        self.assertEqual(rc, 0, err)
        self.assertIn("selftest ok", out)
        rc, out, err = run(root, "--lint")
        self.assertEqual(rc, 0, err)

    def test_empty_portfolio_degrades(self):
        root = make_repo(self.tmp, {
            "portfolio": "\n".join(["# 空组合", "", "## Goal", "—", "", "## Phases", ""]) + "\n",
        })
        rc, out, err = run(root)
        self.assertEqual(rc, 0, err)
        self.assertIn("**现在：**", out)
        self.assertIn("## 流表", out)
        self.assertIn("## 待 Captain", out)
        self.assertIn("## 证据/plan 指针", out)


class SelftestRoutingGateTest(_TmpRepoTest):
    """S4：selftest 的「Backlog 混入 C 项/⏸ 行」断言改为对 portfolio 解析结果生效，
    不再依赖看板已不输出的明细行（守门不得空转）。"""

    def _repo(self) -> Path:
        return make_repo(self.tmp, {
            "portfolio": portfolio_body(
                [".planning/stream-a/task_plan.md"],
                c_rows=["| C1 | 决策一 | P0 | — | 待定 |"],
                t_rows=["| T1 | 待办一 | P1 | — | — |",
                        "| T2 | [⏸] 待放行 | P1 | — | 待 C1 |"],
                recent=[], deviation=[]),
            "stream-a": stream_body("下一步。"),
        })

    def test_selftest_green_on_routing_fixture(self):
        root = self._repo()
        self.assertEqual(ps.main(["--root", str(root), "--selftest"]), 0)

    def test_selftest_flags_backlog_count_mismatch(self):
        # 渲染把 C/⏸ 行误算进 agent 待办 → 计数与 portfolio 解析不一致，必须拦住。
        root = self._repo()
        real_render = ps.render

        def broken_render(plans, cfg, archive=None):
            return real_render(plans, cfg, archive).replace(
                "- agent 待办 1 条", "- agent 待办 3 条")

        ps.render = broken_render
        try:
            with self.assertRaises(AssertionError):
                ps.selftest(ps.load_plans(root), ps.load_config(root))
        finally:
            ps.render = real_render

    def test_selftest_flags_captain_row_missing(self):
        # C 前缀行未进待 Captain 汇总（分流丢失）→ 必须拦住。
        root = self._repo()
        real_captain = ps._captain_lines
        ps._captain_lines = lambda *args, **kwargs: []
        try:
            with self.assertRaises(AssertionError):
                ps.selftest(ps.load_plans(root), ps.load_config(root))
        finally:
            ps._captain_lines = real_captain


if __name__ == "__main__":
    unittest.main(verbosity=2)
