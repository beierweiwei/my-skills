#!/usr/bin/env python3
"""2.2.26 T62：plan-status 看板可用性三改（CASE helm6332-board-hygiene-20261113-1 回执建议）。

a) 流表表头「完成数/健康」→「阶段完成数/健康」（口径为 Phase 完成数，防与勾选数混淆）；
b) plan section 标题支持尾部括注（`## 最近完成（最近 5 条）` 归一为「最近完成」）；
c) 卫生 lint 三类（hygiene 层，默认提醒 --strict 阻断）：
   progress-date（progress 条目缺日期前缀）/ plan-unregistered（有 plan 未登记
   portfolio Phase）/ phase-status-mismatch（portfolio Phase complete 但引用
   plan 内 Phase 未 complete）。

CASE 侧实证根因=plan 数据维护缺口而非解析 bug：lint 提醒是维护缺口的自动探针。
"""

from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_t62", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_t62"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

PORTFOLIO = """# 测试项目组合 plan

## Next Step

推进流 A。

## Phases

### Phase 1: 流A
- plan: `.planning/stream-a/task_plan.md`
**Status:** pending

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
"""

STREAM = """# 流 plan A

## Next Step

干活。

## Phases

### Phase 1: 阶段一
**Status:** pending
"""

STREAM_DONE_BODY = """# 流 plan B

## Next Step

收口。

## Phases

### Phase 1: 阶段一
**Status:** complete
"""

PORTFOLIO_PAREN_TITLE = """# 测试项目组合 plan

## Next Step

推进流 A。

## Phases

### Phase 1: 流A
- plan: `.planning/stream-a/task_plan.md`
**Status:** complete

## 最近完成（最近 5 条）

- [x] 完成条目X（2026-11-13）
"""

PROGRESS_OK = """# Progress: 测试项目组合

## 自动驾驶里程

- 2026-11-13 有日期前缀条目。
"""

PROGRESS_BAD = """# Progress: 测试项目组合

## 自动驾驶里程

- 2026-11-13 有日期前缀条目。
- 缺日期前缀条目。
"""


def make_repo(tmp: Path, plans: dict[str, str], progress: str | None = None) -> Path:
    root = tmp / "repo"
    planning = root / ".planning"
    planning.mkdir(parents=True)
    for name, body in plans.items():
        d = planning / name
        d.mkdir(exist_ok=True)
        (d / "task_plan.md").write_text(body, encoding="utf-8")
    if progress is not None:
        (planning / "portfolio").mkdir(exist_ok=True)
        (planning / "portfolio" / "progress.md").write_text(progress, encoding="utf-8")
    return root


class T62TableHeaderTest(unittest.TestCase):
    """a) 流表表头口径：完成数指 Phase 完成数，表头必须写明。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_brief_table_header_says_phase_completion(self):
        self.assertIn("阶段完成数/健康", ps.BRIEF_TABLE_HEADER)
        self.assertNotIn("| 完成数/健康", ps.BRIEF_TABLE_HEADER)

    def test_rendered_board_uses_new_header(self):
        root = make_repo(self.tmp, {"portfolio": PORTFOLIO, "stream-a": STREAM})
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("阶段完成数/健康", board)
        self.assertNotIn("| 完成数/健康", board)


class T62ParenTitleTest(unittest.TestCase):
    """b) section 标题尾部括注归一：`## 最近完成（最近 5 条）` 命中「最近完成」。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_section_with_parenthetical_is_normalized(self):
        root = make_repo(self.tmp, {"portfolio": PORTFOLIO_PAREN_TITLE,
                                    "stream-a": STREAM})
        portfolio = ps.parse_plan(
            root / ".planning/portfolio/task_plan.md", root)
        self.assertIn("最近完成", portfolio.sections)
        self.assertIn("完成条目X", " ".join(portfolio.sections["最近完成"]))
        self.assertNotIn("最近完成（最近 5 条）", portfolio.sections)

    def test_board_recent_completed_hits_paren_title(self):
        root = make_repo(self.tmp, {"portfolio": PORTFOLIO_PAREN_TITLE,
                                    "stream-a": STREAM_DONE_BODY})
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("完成条目X", board)


class T62HygieneLintsTest(unittest.TestCase):
    """c) 三类卫生 lint 各一例（正/反）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _issues(self, plans_map: dict[str, str], progress: str | None = None):
        root = make_repo(self.tmp, plans_map, progress=progress)
        plans = ps.load_plans(root)
        return ps.hygiene_issues(plans), root

    # —— progress-date：progress 条目缺日期前缀 ——
    def test_progress_entry_without_date_prefix_flags(self):
        issues, _ = self._issues({"portfolio": PORTFOLIO, "stream-a": STREAM},
                                 progress=PROGRESS_BAD)
        hit = [i for i in issues if i.rule == "progress-date"]
        self.assertEqual(len(hit), 1, [i.format() for i in issues])
        self.assertIn("缺日期前缀", hit[0].message)

    def test_progress_entries_with_date_prefix_pass(self):
        issues, _ = self._issues({"portfolio": PORTFOLIO, "stream-a": STREAM},
                                 progress=PROGRESS_OK)
        self.assertFalse([i for i in issues if i.rule == "progress-date"],
                         [i.format() for i in issues])

    # —— plan-unregistered：有 plan 未登记 portfolio Phase ——
    def test_unregistered_plan_flags(self):
        issues, _ = self._issues({"portfolio": PORTFOLIO, "stream-a": STREAM,
                                  "stream-b": STREAM_DONE_BODY})
        hit = [i for i in issues if i.rule == "plan-unregistered"]
        self.assertEqual(len(hit), 1, [i.format() for i in issues])
        self.assertIn("stream-b", hit[0].message)

    def test_registered_plan_passes(self):
        issues, _ = self._issues({"portfolio": PORTFOLIO, "stream-a": STREAM})
        self.assertFalse([i for i in issues if i.rule == "plan-unregistered"],
                         [i.format() for i in issues])

    # —— phase-status-mismatch：portfolio Phase complete 但 plan 内未 complete ——
    def test_completed_phase_with_unfinished_plan_flags(self):
        portfolio = PORTFOLIO.replace("**Status:** pending", "**Status:** complete")
        issues, _ = self._issues({"portfolio": portfolio, "stream-a": STREAM})
        hit = [i for i in issues if i.rule == "phase-status-mismatch"]
        self.assertEqual(len(hit), 1, [i.format() for i in issues])
        self.assertIn("流A", hit[0].message)

    def test_completed_phase_with_done_plan_passes(self):
        portfolio = PORTFOLIO.replace("**Status:** pending", "**Status:** complete")
        issues, _ = self._issues({"portfolio": portfolio,
                                  "stream-a": STREAM_DONE_BODY})
        self.assertFalse([i for i in issues if i.rule == "phase-status-mismatch"],
                         [i.format() for i in issues])


if __name__ == "__main__":
    unittest.main(verbosity=2)
