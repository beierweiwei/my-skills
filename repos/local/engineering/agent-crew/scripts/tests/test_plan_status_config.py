#!/usr/bin/env python3
"""plan-status.py 配置化用例：python3 scripts/tests/test_plan_status_config.py

覆盖设计 §2.4：board.path / board.title / board.archive / backlog.captainBlocks /
portfolioMatch / 前缀配置化；无配置文件时内置默认 = 源仓现值（本仓零破坏）。
fixture 全部为中性占位内容，不含任何业务词（通用性红线：渲染后无业务词残留）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)


def make_repo(tmp: Path, plans: dict[str, str], config: dict | None = None) -> Path:
    """建一个最小目标仓：.planning/<name>/task_plan.md [+ orchestration.json]。"""
    root = tmp / "repo"
    planning = root / ".planning"
    planning.mkdir(parents=True)
    for name, body in plans.items():
        d = planning / name
        d.mkdir()
        (d / "task_plan.md").write_text(body, encoding="utf-8")
    if config is not None:
        (planning / "orchestration.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return root


PORTFOLIO_BODY = """# 测试项目组合 plan

## Goal
合成验证用目标。

## Next Step
推进流 A。

## Phases

### Phase 1: 流A
- plan: `.planning/stream-a/task_plan.md`
- owner: tester
- [x] 条目一
**Status:** complete

### Phase 2: 流B
- plan: `.planning/missing-stream/task_plan.md`
- owner: tester
- [ ] 条目二
**Status:** pending

## Decisions Made
| Decision | Rationale |
|----------|-----------|

## Errors Encountered
| Error | Resolution |
|-------|------------|

## Backlog
| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 任务一 | P1 | — | — |
| T7 | 任务七 | P1 | — | — |
| C5 | 决策五 | P0 | — | — |

## Deviation
| 预期 | 实际 | 原因 | 待决策 |
|---|---|---|---|
"""

STREAM_BODY = """# 流A plan

## Goal
流 A 目标。

## Next Step
做下一条。

## Phases

### Phase 1: 阶段一
- [x] 完成
**Status:** complete

## Decisions Made
| Decision | Rationale |
|----------|-----------|

## Errors Encountered
| Error | Resolution |
|-------|------------|
"""

# Backlog 空表（init 全新产物仓形态：只有表头无数据行）
EMPTY_BACKLOG_PORTFOLIO = PORTFOLIO_BODY.replace(
    "| T1 | 任务一 | P1 | — | — |\n| T7 | 任务七 | P1 | — | — |\n| C5 | 决策五 | P0 | — | — |\n",
    "",
)

# Phase 内多行 HTML 注释，中段/尾行含 **Status:** 字样（建议5 探针）
COMMENTED_STREAM = STREAM_BODY.replace(
    "### Phase 1: 阶段一\n- [x] 完成\n**Status:** complete\n",
    "### Phase 1: 阶段一\n- [x] 完成\n**Status:** pending\n"
    "<!-- 提示注释开头\n**Status:** complete\n另一行 **Status:** in_progress\n-->\n",
)

DEFAULT_PLANS = {"x-portfolio": PORTFOLIO_BODY, "stream-a": STREAM_BODY}


class PlanStatusConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    # --- 默认行为：无配置文件 = 源仓现值（本仓零破坏） ---

    def test_default_no_config_uses_case_defaults(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("# 进度追踪（派生看板）", board)
        self.assertNotIn("历史归档", board)  # board.archive 默认空 → 不写归档链接行

    def test_default_portfolio_match_substring_glob(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        cfg = ps.load_config(root)
        self.assertEqual(cfg["board"]["path"], "docs/进度追踪.md")
        self.assertEqual(cfg["portfolioMatch"], "*portfolio*")

    # --- 配置读取 ---

    def test_config_board_path_and_title(self):
        root = make_repo(
            self.tmp,
            DEFAULT_PLANS,
            config={"board": {"path": "reports/board.md", "title": "自定义看板", "archive": ""}},
        )
        cfg = ps.load_config(root)
        self.assertEqual(cfg["board"]["path"], "reports/board.md")
        board = ps.render(plans=ps.load_plans(root), cfg=cfg)
        self.assertIn("# 自定义看板", board)

    def test_write_creates_parent_dirs(self):
        root = make_repo(
            self.tmp, DEFAULT_PLANS, config={"board": {"path": "deep/nested/board.md"}}
        )
        rc = ps.main(["--root", str(root), "--write"])
        self.assertEqual(rc, 0)
        self.assertTrue((root / "deep" / "nested" / "board.md").exists())

    def test_config_captain_blocks_rewrite_backlog(self):
        root = make_repo(
            self.tmp,
            DEFAULT_PLANS,
            config={"backlog": {"captainBlocks": {"T7": "C5"}}},
        )
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        backlog = board.split("## Backlog", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("待 C5", backlog)  # T7 的阻塞列被映射改写
        self.assertNotIn("| T7 | 任务七 | P1 | — | — |", backlog)

    def test_no_blocks_by_default(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        backlog = board.split("## Backlog", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("| T7 | 任务七 | P1 | — | — |", backlog)  # 默认 {} → 原样透出

    def test_config_portfolio_match_glob(self):
        plans = {"streams-home": PORTFOLIO_BODY, "stream-a": STREAM_BODY}
        root = make_repo(self.tmp, plans, config={"portfolioMatch": "*streams*"})
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertNotIn("未找到 portfolio", board)

    def test_config_prefixes_split_backlog_and_captain(self):
        plans = {"x-portfolio": PORTFOLIO_BODY.replace("T1", "A1").replace("T7", "A2").replace("C5", "B9")}
        root = make_repo(
            self.tmp,
            plans,
            config={"backlog": {"captainPrefix": "B", "agentPrefix": "A", "captainBlocks": {"A2": "B9"}}},
        )
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        backlog = board.split("## Backlog", 1)[1].split("## 待 Captain", 1)[0]
        captain = board.split("## 待 Captain", 1)[1]
        self.assertIn("**B9**", captain)
        self.assertIn("| A1 |", backlog)
        self.assertIn("待 B9", backlog)

    # --- 归档行为（§2.4 #2：一次性动作不内置） ---

    def test_archive_line_only_when_configured(self):
        root = make_repo(
            self.tmp,
            DEFAULT_PLANS,
            config={"board": {"archive": "docs/archive/board-snap.md"}},
        )
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("docs/archive/board-snap.md", board)

    def test_archive_cli_override(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        board = ps.render(
            plans=ps.load_plans(root), cfg=ps.load_config(root), archive="docs/old.md"
        )
        self.assertIn("docs/old.md", board)

    # --- selftest（合成 plan 上保持绿；前缀可配置时断言随配置） ---

    def test_selftest_green_on_synthetic_repo(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        rc = ps.main(["--root", str(root), "--selftest"])
        self.assertEqual(rc, 0)

    def test_selftest_green_with_custom_prefixes(self):
        plans = {"x-portfolio": PORTFOLIO_BODY.replace("T1", "A1").replace("T7", "A2").replace("C5", "B9")}
        root = make_repo(
            self.tmp, plans, config={"backlog": {"captainPrefix": "B", "agentPrefix": "A"}}
        )
        rc = ps.main(["--root", str(root), "--selftest"])
        self.assertEqual(rc, 0)

    # --- 阻断1：Backlog 空表（init 全新产物仓）selftest 首跑即绿 ---

    def test_selftest_green_with_empty_backlog(self):
        root = make_repo(self.tmp, {"x-portfolio": EMPTY_BACKLOG_PORTFOLIO, "stream-a": STREAM_BODY})
        rc = ps.main(["--root", str(root), "--selftest"])
        self.assertEqual(rc, 0)
        board = ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))
        self.assertIn("## Backlog", board)  # 渲染仍完整（空表 → 「—」）

    # --- 建议5：多行 HTML 注释区间内 **Status:** 不污染解析 ---

    def test_multiline_comment_does_not_pollute_status(self):
        root = make_repo(self.tmp, {"stream-a": COMMENTED_STREAM})
        plan = ps.parse_plan(root / ".planning/stream-a/task_plan.md", root)
        self.assertEqual(plan.phases, [("阶段一", "pending")])

    # --- 建议10：损坏 orchestration.json 显式警告并降级内置默认 ---

    def test_broken_config_warns_and_defaults(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        (root / ".planning/orchestration.json").write_text("{broken json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cfg = ps.load_config(root)
        self.assertEqual(cfg["board"]["path"], "docs/进度追踪.md")  # 内置默认生效
        self.assertIn("配置文件损坏", err.getvalue())
        self.assertIn(str(root / ".planning/orchestration.json"), err.getvalue())

    # --- ROOT 解析（安装布局 / workspace 布局均可用） ---

    def test_resolve_root_walks_up_from_cwd(self):
        root = make_repo(self.tmp, DEFAULT_PLANS)
        nested = root / "a" / "b"
        nested.mkdir(parents=True)
        import os

        old = os.getcwd()
        os.chdir(nested)
        try:
            self.assertEqual(ps.resolve_root(None), root)
        finally:
            os.chdir(old)


if __name__ == "__main__":
    unittest.main(verbosity=2)
