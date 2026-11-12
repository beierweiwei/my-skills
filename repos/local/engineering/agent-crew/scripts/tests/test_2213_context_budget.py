#!/usr/bin/env python3
"""2.2.13 H 切片：交接上下文预算（H1 handover 字节字段 / H2 覆盖层体积 / H3 基线 warn）。

行为断言（F8）：H1 直接驱动 plan-status --handover 读字段与提示；H2/H3 通过 doctor
--only --json 断言 status/detail/next/evidence，不锁实现细节。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SPEC_PATH = _TESTS_DIR.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_context_budget", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_context_budget"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

from doctor_fixtures import ProjectFixture, dispatch_mod  # noqa: E402

BUDGET = 32768


# —— H1 夹具（最小 plan 仓） ——

def make_repo(tmp: Path, plans: dict[str, str], progress: str | None = None) -> Path:
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
    return root


def stream_body(phases: list[tuple[str, str]], next_step: str = "推进下一条。") -> str:
    lines = ["# 流 plan", "", "## Goal", "流目标。", "", "## Next Step", next_step, "",
             "## Phases", ""]
    for index, (name, status) in enumerate(phases, 1):
        lines += [f"### Phase {index}: {name}", f"**Status:** {status}", ""]
    return "\n".join(lines) + "\n"


def portfolio_body(stream_refs: list[str]) -> str:
    lines = ["# 测试项目组合 plan", "", "## Goal", "组合目标：推进多条流。", "",
             "## Next Step", "推进流 A。", "", "## 最近完成", "", "## Phases", ""]
    for index, ref in enumerate(stream_refs, 1):
        lines += [f"### Phase {index}: 流{index}", f"- plan: `{ref}`",
                  "**Status:** pending", ""]
    return "\n".join(lines) + "\n"


def progress_body() -> str:
    return ("# Progress（编排台账）\n\n## 自动驾驶里程\n\n"
            "- 2026-11-01 夹具进度条目一\n- 2026-11-02 夹具进度条目二\n")


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


class HandoverBudgetTest(unittest.TestCase):
    """H1：--handover 输出本次必读字节与阈值；超限提示先摘要/grep；只读不回归。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _repo(self) -> Path:
        return make_repo(self.tmp, {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body([("阶段一", "complete"), ("阶段二", "in_progress")],
                                    next_step="推进阶段二。"),
        }, progress=progress_body())

    def _run(self, repo: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--handover"])
        return rc, out.getvalue(), err.getvalue()

    def _field(self, text: str) -> int:
        match = re.search(r"本次必读字节：(\d+)（阈值 32768", text)
        self.assertIsNotNone(match, text)
        return int(match.group(1))

    def _feedback(self, repo: Path) -> Path:
        path = repo / ".planning/portfolio/feedback.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def test_field_threshold_and_original_blocks_kept(self):
        repo = self._repo()
        rc, out, err = self._run(repo)
        self.assertEqual(rc, 0, err)
        self.assertLessEqual(self._field(out), BUDGET)
        self.assertNotIn("超预算", out)
        self.assertNotIn("先摘要", out)
        for block in ("## Goal", "## Next Step", "## 活跃流", "## 最近 progress",
                      "## 最近完成", "## 证据入口", "## 看板", "## 交接必读预算"):
            self.assertIn(block, out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_over_threshold_appends_summary_grep_hint(self):
        repo = self._repo()
        self._feedback(repo).write_text(
            "- 待复盘 " + "x" * 40000 + "\n", encoding="utf-8")
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        self.assertLessEqual(self._field(out), BUDGET)  # A1：必读 total 硬 ≤32KB
        self.assertIn("已封顶", out)
        self.assertIn("超预算", out)
        self.assertIn("先摘要", out)
        self.assertIn("grep", out)
        self.assertIn("写明只读段", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_feedback_byte_cap_keeps_total_under_budget(self):
        repo = self._repo()
        _rc, out, _err = self._run(repo)
        base = self._field(out)
        self.assertLessEqual(base, BUDGET)
        self.assertNotIn("已封顶", out)  # 正常夹具不封顶
        feedback = self._feedback(repo)
        feedback.write_text("- 待复盘 " + "y" * 40000 + "\n", encoding="utf-8")
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        capped = self._field(out)
        self.assertLessEqual(capped, BUDGET)  # A1：total 也硬 ≤32KB
        self.assertEqual(base + ps.HANDOVER_FEEDBACK_MAX_BYTES, capped)
        self.assertIn("已封顶", out)
        self.assertIn("超预算", out)

    def test_handover_still_writes_nothing(self):
        repo = self._repo()
        self._feedback(repo).write_text("- 待复盘 z\n", encoding="utf-8")
        board = repo / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"z" * 100)
        before = snapshot(repo)
        _rc, _out, _err = self._run(repo)
        self.assertEqual(snapshot(repo), before)


class DoctorBudgetTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.fx = ProjectFixture(self.base)

    def check(self, item_id: str) -> tuple[int, dict]:
        rc, report = self.fx.doctor_json("--only", item_id)
        return rc, self.fx.item(report, item_id)

    @staticmethod
    def pad_to(path: Path, total_bytes: int) -> None:
        data = path.read_bytes()
        if len(data) < total_bytes:
            path.write_bytes(data + b"x" * (total_bytes - len(data)))
        else:
            path.write_bytes(data[:total_bytes])


class RoleOverlayBudgetTest(DoctorBudgetTestBase):
    """H2：.qoder/agents/*.md >8KB 或组合产物 >20KB → warn（不阻断）。"""

    def test_default_fixture_passes(self):
        rc, item = self.check("role.overlay_budget")
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])

    def test_overlay_over_8kb_warns_with_actionable_next(self):
        self.fx.write_role("qoder", "builder", body="x" * 9000)
        rc, item = self.check("role.overlay_budget")
        self.assertEqual(0, rc)  # warn 不阻断
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertIn("8192", item["detail"])
        self.assertIn("项目文档", item["next"])

    def test_overlay_boundary_8192_passes_8193_warns(self):
        path = self.fx.write_role("qoder", "builder", body="base")
        self.pad_to(path, 8 * 1024)
        _rc, item = self.check("role.overlay_budget")
        self.assertEqual("pass", item["status"], item["detail"])
        self.pad_to(path, 8 * 1024 + 1)
        _rc, item = self.check("role.overlay_budget")
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertIn("8193", item["detail"])

    def test_composed_over_20kb_warns(self):
        base = self.fx.skill / "templates/agents/qoder/builder.md"
        # 组合产物可能对 tail 做归一化，校正两次落到 20481 边界
        for _ in range(2):
            outcome = dispatch_mod.build_compose(self.fx.root, "builder", "qoder")
            self.assertTrue(outcome.ok, outcome.errors)
            current = len(outcome.composed_body.encode("utf-8"))
            delta = 20 * 1024 + 1 - current
            if delta == 0:
                break
            data = base.read_bytes()
            base.write_bytes(data + b"x" * delta if delta > 0 else data[:len(data) + delta])
        overlay_size = self.fx.role_path("qoder", "builder").stat().st_size
        self.assertLessEqual(overlay_size, 8 * 1024)  # 只触发组合产物
        rc, item = self.check("role.overlay_budget")
        self.assertEqual(0, rc)
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertIn("20481", item["detail"])
        self.assertIn("项目文档", item["next"])

    def test_missing_overlay_dir_skips(self):
        shutil.rmtree(self.fx.root / ".qoder/agents")
        rc, item = self.check("role.overlay_budget")
        self.assertEqual(0, rc)
        self.assertEqual("skip", item["status"], item["detail"])


class ContextBaselineTest(DoctorBudgetTestBase):
    """H3：角色组合 + AGENTS + CONTEXT + 交接必读估算 >60KB → warn；无来源优雅 skip。"""

    def test_default_fixture_passes(self):
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])
        self.assertIn("handoverBytes", item["evidence"])

    def test_oversized_agents_and_context_warn(self):
        agents = self.fx.root / "AGENTS.md"
        agents.write_bytes(agents.read_bytes() + b"x" * 40000)
        (self.fx.root / "CONTEXT.md").write_bytes(b"c" * 30000)
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)  # warn 不阻断
        self.assertEqual("warn", item["status"], item["detail"])
        self.assertGreater(item["evidence"]["totalBytes"], 60 * 1024)
        self.assertIn("摘要", item["next"])

    def test_large_feedback_capped_in_handover_bytes(self):
        # A1：feedback 分项封顶，交接必读硬 ≤32KB（不再由 feedback 推高到 60KB+）。
        feedback = self.fx.root / ".planning/portfolio/feedback.md"
        feedback.write_text("- 待复盘 " + "b" * 70000 + "\n", encoding="utf-8")
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertGreater(item["evidence"]["handoverBytes"], 0)
        self.assertLessEqual(item["evidence"]["handoverBytes"], 32768)

    def test_large_board_not_counted_in_handover_bytes(self):
        # FB-044：看板仅按关键词 grep，大看板不得单独推高交接必读估算。
        board = self.fx.root / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"b" * 70000)
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertEqual("pass", item["status"], item["detail"])
        self.assertLess(item["evidence"]["handoverBytes"], 60 * 1024)

    def test_custom_portfolio_dir_handover_bytes_counted(self):
        # S6：自定义 portfolioDir 时不得误报「无 .planning/portfolio」/handoverBytes=0
        self.fx.config["portfolioDir"] = ".planning/flows"
        self.fx.write_config()
        flows = self.fx.root / ".planning/flows"
        flows.mkdir(parents=True, exist_ok=True)
        (flows / "feedback.md").write_text(
            "- 待复盘 " + "b" * 40000 + "\n", encoding="utf-8")
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertGreater(item["evidence"]["handoverBytes"], 0)
        self.assertLessEqual(item["evidence"]["handoverBytes"], 32768)
        self.assertNotIn("无 .planning/portfolio", item["detail"])

    def test_missing_custom_portfolio_dir_reports_configured_path(self):
        # S6：自定义目录不存在时按配置路径说明，不误报默认目录
        self.fx.config["portfolioDir"] = ".planning/flows"
        self.fx.write_config()
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertIn("无 .planning/flows", item["detail"])

    def test_no_sources_skips(self):
        shutil.rmtree(self.fx.root / ".qoder/agents")
        (self.fx.root / "AGENTS.md").unlink()
        shutil.rmtree(self.fx.root / ".planning/portfolio")
        rc, item = self.check("context.baseline")
        self.assertEqual(0, rc)
        self.assertEqual("skip", item["status"], item["detail"])


if __name__ == "__main__":
    unittest.main()
