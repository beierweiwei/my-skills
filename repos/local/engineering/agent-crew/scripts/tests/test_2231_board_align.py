#!/usr/bin/env python3
"""2.2.31 board-align（工单 03）：plan-status Phase 结构化字段 `- 依赖:`/`- 在途:`。

lint 白名单放行 + 错误文案同步；`--brief`/看板流表加「依赖」「预计结束」两列
（预计结束从 `- 在途:` 解析 `MM-DD`，容忍 `MM-DD HH:MM`；格式异常走卫生提醒，
默认不阻断）；旧 plan（无新行）兼容显示「—」。/tmp fixture，禁触真实 .planning。
（python3 scripts/tests/test_2231_board_align.py）
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parent.parent / "plan-status.py"
_SPEC = importlib.util.spec_from_file_location("plan_status_board", _SCRIPT)
ps = importlib.util.module_from_spec(_SPEC)
sys.modules["plan_status_board"] = ps
_SPEC.loader.exec_module(ps)

PORTFOLIO_WITH_FIELDS = """# Task Plan: portfolio

## Phases

### Phase 39: board-align
- plan: `.planning/flow-a/task_plan.md`
- owner: builder
- 依赖: patch-2230 发布收口 + D7 快审 PASS
- 在途: 开始 11-14，更新 11-14，预计结束 11-14
- [ ] 实现
- **Status:** in_progress
"""

PORTFOLIO_ETA_WITH_TIME = PORTFOLIO_WITH_FIELDS.replace(
    "预计结束 11-14", "预计结束 11-14 15:30")

PORTFOLIO_ETA_BAD_FORMAT = PORTFOLIO_WITH_FIELDS.replace(
    "预计结束 11-14", "预计结束 明天")

PORTFOLIO_NO_FIELDS = """# Task Plan: portfolio

## Phases

### Phase 39: legacy
- plan: `.planning/flow-a/task_plan.md`
- owner: builder
- [ ] 实现
- **Status:** in_progress
"""

PORTFOLIO_ILLEGAL_FIELD = PORTFOLIO_WITH_FIELDS.replace(
    "- 依赖: patch-2230 发布收口 + D7 快审 PASS", "- 随便: 非法行")

FLOW_PLAN = """# Task Plan: Flow A

## Phases

### Phase 1: 切片
- owner: builder
- [ ] 做事
- **Status:** in_progress
"""


def write_plan(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class BoardAlignBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def seed(self, portfolio: str = PORTFOLIO_WITH_FIELDS) -> None:
        write_plan(self.root, ".planning/portfolio/task_plan.md", portfolio)
        write_plan(self.root, ".planning/flow-a/task_plan.md", FLOW_PLAN)

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT), "--root", str(self.root), *args],
            capture_output=True, text=True,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def brief_stream_rows(self, out: str) -> list[list[str]]:
        """--brief 流表数据行（表头后跳过分隔行）；夹具单流，取到即一行。"""
        lines = out.splitlines()
        self.assertIn(ps.BRIEF_TABLE_HEADER, lines, f"brief 缺流表表头：\n{out}")
        header_idx = lines.index(ps.BRIEF_TABLE_HEADER)
        rows = []
        for line in lines[header_idx + 2:]:
            if not line.startswith("|"):
                break
            rows.append([c.strip() for c in line.strip("|").split("|")])
        self.assertTrue(rows, f"流表无数据行：\n{out}")
        return rows


class LintAllowlistTest(BoardAlignBase):
    """验收 1：lint 白名单允许 `- 依赖:`/`- 在途:`，非法行仍拒绝，文案同步。"""

    def test_structured_lines_pass_lint(self):
        self.seed()
        rc, _, err = self.run_cli("--lint")
        self.assertEqual(0, rc, err)
        self.assertIn("lint ok", io.StringIO(err).getvalue() or err or "lint ok")

    def test_illegal_phase_line_rejected_with_new_message(self):
        self.seed(PORTFOLIO_ILLEGAL_FIELD)
        rc, _, err = self.run_cli("--lint")
        self.assertEqual(1, rc)
        self.assertIn("Phase 项格式", err)
        for token in ("- [ ]", "- [x]", "- owner:", "- plan:", "- 依赖:", "- 在途:"):
            self.assertIn(token, err, f"错误文案应含 {token}")

    def test_missing_colon_field_still_rejected(self):
        self.seed(PORTFOLIO_WITH_FIELDS.replace(
            "- 在途: 开始 11-14", "- 在途 开始 11-14"))
        rc, _, err = self.run_cli("--lint")
        self.assertEqual(1, rc, "缺冒号的 `- 在途 ` 仍是非法 Phase 项")


class BriefColumnsTest(BoardAlignBase):
    """验收 3：`--brief` 流表两列（表头/分隔线/数据行同步，看板同源）。"""

    def test_header_has_two_new_columns(self):
        self.seed()
        header_cells = [c.strip() for c in
                        ps.BRIEF_TABLE_HEADER.strip("|").split("|")]
        self.assertEqual(
            ["流", "阶段", "阶段完成数/健康", "下一步", "待 Captain", "依赖", "预计结束"],
            header_cells)

    def test_brief_row_carries_deps_and_eta(self):
        self.seed()
        rc, out, err = self.run_cli("--brief")
        self.assertEqual(0, rc, err)
        rows = self.brief_stream_rows(out)
        for cells in rows:
            self.assertEqual(7, len(cells), f"数据行应 7 列：{cells}")
        flow_row = rows[0]
        self.assertIn("patch-2230 发布收口 + D7 快审 PASS", flow_row[-2], "依赖列取值")
        self.assertIn("11-14", flow_row[-1], "预计结束列取值")

    def test_separator_matches_column_count(self):
        self.seed()
        rc, out, err = self.run_cli("--brief")
        self.assertEqual(0, rc, err)
        lines = out.splitlines()
        header_idx = lines.index(ps.BRIEF_TABLE_HEADER)
        self.assertEqual("|---|---|---|---|---|---|---|", lines[header_idx + 1])

    def test_write_board_uses_same_renderer(self):
        # 看板（--write）与 --brief 同表头同渲染（验收 3 同源断言）。
        self.seed()
        rc, _, err = self.run_cli("--write")
        self.assertEqual(0, rc, err)
        board = (self.root / "docs" / "进度追踪.md").read_text(encoding="utf-8")
        self.assertIn(ps.BRIEF_TABLE_HEADER, board)
        self.assertIn("|---|---|---|---|---|---|---|", board)


class EtaParseTest(BoardAlignBase):
    """验收 2：预计结束解析变体与格式异常卫生提醒（不阻断）。"""

    def test_eta_plain_date(self):
        self.seed()
        _, out, _ = self.run_cli("--brief")
        self.assertEqual("11-14", self.brief_stream_rows(out)[0][-1])

    def test_eta_with_time_tolerated(self):
        self.seed(PORTFOLIO_ETA_WITH_TIME)
        _, out, _ = self.run_cli("--brief")
        self.assertEqual("11-14", self.brief_stream_rows(out)[0][-1],
                         "容忍 MM-DD HH:MM 取日期")

    def test_eta_missing_or_bad_format_falls_back_to_dash(self):
        self.seed(PORTFOLIO_ETA_BAD_FORMAT)
        _, out, _ = self.run_cli("--brief")
        self.assertEqual("—", self.brief_stream_rows(out)[0][-1], "格式异常显示 —")

    def test_bad_eta_format_warns_without_blocking(self):
        self.seed(PORTFOLIO_ETA_BAD_FORMAT)
        rc, _, err = self.run_cli("--lint")
        self.assertEqual(0, rc, "格式异常只提醒不阻断")
        self.assertIn("[提醒]", err)
        self.assertIn("预计结束", err)

    def test_deps_missing_shows_dash(self):
        self.seed(PORTFOLIO_WITH_FIELDS.replace(
            "- 依赖: patch-2230 发布收口 + D7 快审 PASS\n", ""))
        _, out, _ = self.run_cli("--brief")
        cells = self.brief_stream_rows(out)[0]
        self.assertEqual("—", cells[-2], "无依赖行显示 —")
        self.assertEqual("11-14", cells[-1])


class LegacyPlanCompatTest(BoardAlignBase):
    """验收 4：旧 plan（无新行）兼容——两列「—」，lint 不报错，brief 不破界。"""

    def test_legacy_plan_renders_dashes_and_passes_lint(self):
        self.seed(PORTFOLIO_NO_FIELDS)
        rc, _, err = self.run_cli("--lint")
        self.assertEqual(0, rc, err)
        _, out, _ = self.run_cli("--brief")
        cells = self.brief_stream_rows(out)[0]
        self.assertEqual("—", cells[-2])
        self.assertEqual("—", cells[-1])
        self.assertLessEqual(
            len(out.splitlines()), ps.BRIEF_MAX_LINES,
            "--brief 行数不因加列破界")


class BoardSelftestTest(unittest.TestCase):
    """同源断言：selftest 里「流表须与 --brief 同表头」继续成立。"""

    def test_module_selftest_board_header_assert_present(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        self.assertIn('assert BRIEF_TABLE_HEADER in board', src)
        self.assertEqual(7, len(ps.BRIEF_TABLE_HEADER.strip("|").split("|")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
