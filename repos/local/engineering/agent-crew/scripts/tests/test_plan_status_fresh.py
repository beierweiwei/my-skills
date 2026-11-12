#!/usr/bin/env python3
"""plan-status.py 新鲜度/结构 lint/最近完成用例（crew-v2 1.7）。

覆盖：
① --check-fresh：--write 后新鲜（低输出、退出 0）；plan 内容变化 / 新增 plan / 配置变化 /
   看板缺失 / 旧版看板（无源指纹）→ 明确列出来源、提示重跑 --write 并非零退出。
② create-time lint：Phase 标题格式、每 Phase 恰一行 Status、Next Step ≤10、
   Phase 项 checkbox 形式、裸编号；错误含 文件:行号；HTML 注释整段跳过；本仓模板不误伤。
③ 看板「最近完成」：最多渲染 10 条（新的在上），空节显示「—」。
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
_spec = importlib.util.spec_from_file_location("plan_status_fresh", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_fresh"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


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


def stream_body(next_step: str = "做下一条。", phases: list[tuple[str, str]] | None = None) -> str:
    lines = ["# 流 plan", "", "## Goal", "目标。", "", "## Next Step", next_step, "", "## Phases", ""]
    for i, (name, status) in enumerate(phases or [("阶段一", "pending")], 1):
        lines += [f"### Phase {i}: {name}", f"**Status:** {status}", ""]
    return "\n".join(lines) + "\n"


def portfolio_body(recent: list[str] | None = None) -> str:
    lines = [
        "# 测试项目组合 plan", "", "## Goal", "合成验证用目标。", "",
        "## Next Step", "推进流 A。", "", "## 最近完成", "",
    ]
    lines += recent or []
    lines += [
        "", "## Phases", "", "### Phase 1: 流A",
        "- plan: `.planning/stream-a/task_plan.md`", "**Status:** pending", "",
        "## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|", "",
    ]
    return "\n".join(lines) + "\n"


def line_of(body: str, needle: str) -> int:
    for no, line in enumerate(body.splitlines(), 1):
        if needle in line:
            return no
    raise AssertionError(f"fixture 中找不到 {needle!r}")


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class CheckFreshTest(_TmpRepoTest):
    """契约①：--check-fresh 以内容指纹判定新鲜度，过期列明来源并非零退出。"""

    CONFIG = {"board": {"title": "自定义看板"}}

    def _write(self, root: Path) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ps.main(["--root", str(root), "--write"]), 0)

    def _check(self, root: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(root), "--check-fresh"])
        return rc, out.getvalue(), err.getvalue()

    def _repo(self) -> Path:
        return make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(), "stream-a": stream_body()},
            config=self.CONFIG,
        )

    def test_fresh_after_write_low_output(self):
        root = self._repo()
        self._write(root)
        board = (root / "docs/进度追踪.md").read_text(encoding="utf-8")
        self.assertIsNotNone(ps.parse_fingerprint(board))  # --write 落盘源指纹
        rc, out, err = self._check(root)
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(len(out.strip().splitlines()), 1)  # 新鲜路径低输出
        self.assertIn("看板新鲜", out)

    def test_stale_when_plan_updated(self):
        root = self._repo()
        self._write(root)
        plan = root / ".planning/stream-a/task_plan.md"
        plan.write_text(plan.read_text(encoding="utf-8") + "\n补充一行。\n", encoding="utf-8")
        rc, out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn(".planning/stream-a/task_plan.md（plan 已更新）", err)
        self.assertIn("--write", err)

    def test_stale_when_plan_added(self):
        root = self._repo()
        self._write(root)
        d = root / ".planning/stream-b"
        d.mkdir()
        (d / "task_plan.md").write_text(stream_body(), encoding="utf-8")
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn(".planning/stream-b/task_plan.md（新增 plan）", err)

    def test_stale_when_plan_removed(self):
        root = self._repo()
        self._write(root)
        (root / ".planning/stream-a/task_plan.md").unlink()
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn(".planning/stream-a/task_plan.md（plan 已移除）", err)

    def test_stale_when_config_changed(self):
        root = self._repo()
        self._write(root)
        (root / ".planning/orchestration.json").write_text(
            '{"board": {"title": "改过的标题"}}', encoding="utf-8"
        )
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn("配置已更新：.planning/orchestration.json", err)

    def test_missing_board_is_stale(self):
        root = self._repo()
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn("看板不存在", err)
        self.assertIn("--write", err)

    def test_legacy_board_without_fingerprint_is_stale(self):
        root = self._repo()
        board = root / "docs/进度追踪.md"
        board.parent.mkdir(parents=True)
        board.write_text("# 旧看板\n", encoding="utf-8")
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn("源指纹", err)
        self.assertIn("--write", err)

    def test_old_generator_version_is_stale_then_write_refreshes(self):
        # S3：看板结构升级（BOARD_VERSION）后，旧版本指纹的看板显式过期；
        # --write 重渲后恢复新鲜。v1 为 2.2.18 及更早的结构版本。
        root = self._repo()
        self._write(root)
        board = root / "docs/进度追踪.md"
        text = board.read_text(encoding="utf-8")
        self.assertIn(f"plan-sources: {ps.BOARD_VERSION} ", text)
        self.assertNotEqual("v1", ps.BOARD_VERSION)  # 结构升级后不得仍为 v1
        board.write_text(text.replace(f"plan-sources: {ps.BOARD_VERSION} ",
                                      "plan-sources: v1 "), encoding="utf-8")
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 1)
        self.assertIn(f"看板生成器版本变化（v1 → {ps.BOARD_VERSION}）", err)
        self._write(root)
        rc, _out, err = self._check(root)
        self.assertEqual(rc, 0, err)


class LintTest(_TmpRepoTest):
    """契约②：create-time lint 规则与 文件:行号 报错格式。"""

    def _lint(self, root: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(root), "--lint"])
        return rc, out.getvalue(), err.getvalue()

    def _lint_one(self, body: str) -> tuple[int, str]:
        root = make_repo(self.tmp, {"stream-a": body})
        rc, _out, err = self._lint(root)
        return rc, err

    def test_lint_green_on_synthetic_repo(self):
        root = make_repo(
            self.tmp, {"x-portfolio": portfolio_body(), "stream-a": stream_body()}
        )
        rc, out, err = self._lint(root)
        self.assertEqual(rc, 0, err)
        self.assertIn("lint ok", out)
        self.assertEqual(err, "")

    def test_lint_green_on_repo_templates(self):
        # 本仓模板不得误伤（含 HTML 注释里的示例 Phase/Status 字样）
        tpl = REPO_ROOT / "templates" / "plans"
        root = make_repo(self.tmp, {
            "x-portfolio": (tpl / "portfolio-task_plan.md").read_text(encoding="utf-8"),
            "stream-a": (tpl / "stream-task_plan.md").read_text(encoding="utf-8"),
        })
        rc, _out, err = self._lint(root)
        self.assertEqual(rc, 0, err)

    def test_bad_phase_title(self):
        body = stream_body().replace("### Phase 1: 阶段一", "### Phase 1 阶段一")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '### Phase 1 阶段一')}: [Phase 标题]",
            err,
        )

    def test_duplicate_status_line(self):
        body = stream_body().replace(
            "**Status:** pending", "**Status:** pending\n**Status:** in_progress"
        )
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '**Status:** in_progress')}: [Status 行]",
            err,
        )
        self.assertIn("2 个", err)

    def test_missing_status_line(self):
        body = stream_body().replace("**Status:** pending\n", "")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '### Phase 1: 阶段一')}: [Status 行]",
            err,
        )
        self.assertIn("缺少", err)

    def test_next_step_over_limit(self):
        items = "\n".join(f"- [ ] 事项{i:02d}" for i in range(1, 12))
        body = stream_body(next_step=items)
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '## Next Step')}: [Next Step 条数]",
            err,
        )
        self.assertIn("11 条", err)
        self.assertIn("第 11 条在第", err)

    def test_bare_id_in_phase_item(self):
        body = stream_body().replace("**Status:** pending", "- [ ] T1\n**Status:** pending")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '- [ ] T1')}: [裸编号]", err
        )

    def test_bare_number_in_next_step(self):
        body = stream_body(next_step="- 1.7")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '- 1.7')}: [裸编号]", err
        )

    def test_plain_bullet_in_phase(self):
        body = stream_body().replace("**Status:** pending", "- 普通条目\n**Status:** pending")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '- 普通条目')}: [Phase 项格式]",
            err,
        )

    def test_pause_checkbox_in_phase_is_not_todo_mark(self):
        body = stream_body().replace("**Status:** pending", "- [⏸] 待定\n**Status:** pending")
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 1)
        self.assertIn("[Phase 项格式]", err)

    def test_metadata_and_checkbox_items_allowed(self):
        body = stream_body().replace(
            "**Status:** pending",
            "- owner: tester\n- [x] 完成项\n- [ ] 待办项（含 T9 描述）\n**Status:** pending",
        )
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 0, err)

    def test_html_comments_skipped(self):
        body = stream_body().replace(
            "**Status:** pending",
            "**Status:** pending\n<!--\n**Status:** complete\n### Phase 9 无冒号\n- 裸行\n-->",
        )
        rc, err = self._lint_one(body)
        self.assertEqual(rc, 0, err)


class RecentCompletedTest(_TmpRepoTest):
    """契约③（2.2.19 B）：看板「最近完成」最多 5 条一行摘要、标准 todo、空节收口态。"""

    def _board(self, recent: list[str] | None) -> str:
        root = make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(recent), "stream-a": stream_body()},
        )
        return ps.render(plans=ps.load_plans(root), cfg=ps.load_config(root))

    def test_recent_truncated_to_5(self):
        recent = [f"- [x] 事项 {i:02d}" for i in range(1, 13)]
        section = self._board(recent).split("## 最近完成", 1)[1].split("## 偏差", 1)[0]
        self.assertIn("- [x] 事项 01", section)
        self.assertIn("- [x] 事项 05", section)
        self.assertNotIn("事项 06", section)
        self.assertNotIn("事项 12", section)
        self.assertIn("另有 7 条", section)

    def test_recent_standard_todo_and_order(self):
        board = self._board(["- [x] 事项甲", "- [x] 事项乙"])
        section = board.split("## 最近完成", 1)[1].split("## 偏差", 1)[0]
        self.assertIn("- [x] 事项甲", section)
        self.assertLess(section.index("事项甲"), section.index("事项乙"))
        self.assertLess(board.index("## 流表"), board.index("## 最近完成"))

    def test_empty_recent_closed_state(self):
        section = self._board(None).split("## 最近完成", 1)[1].split("## 偏差", 1)[0]
        self.assertIn("—", section)


class HelpTest(unittest.TestCase):
    """契约④：--help 写清收口/guard 的刷新调用方式与新开关。"""

    def test_help_documents_refresh_and_flags(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                ps.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("--check-fresh", "--lint", "收口回执", "guard", "交接第"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
