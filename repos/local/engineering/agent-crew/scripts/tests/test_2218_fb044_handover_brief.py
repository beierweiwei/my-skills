#!/usr/bin/env python3
"""2.2.18 FB-044：交接一屏摘要（--brief）替代整看板（防回退）。

① 模板/规则 grep：helm 两版「交接读取范围（硬）」白名单以 `--brief` 一屏替代整看板，
   看板仅按关键词 grep；`plan-discipline.md` §5.1 第 4 条与 `crew-rules.md` F11 同步口径。
② --handover 预算分项：brief 字节计入且与 `--brief` 输出同口径复用；整看板不计入；
   超限提示保留；夹具激活流场景 ≤32KB。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SPEC_PATH = REPO_ROOT / "scripts" / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_fb044", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_fb044"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

HELM = (
    "templates/agents/qoder/helm.md",
    "templates/agents/opencode/helm.md",
)
DISCIPLINE = "references/plan-discipline.md"
RULES = "references/crew-rules.md"
BUDGET = 32768
BRIEF_FIRST_ITEM = "只读白名单＝`python3 scripts/plan-status.py --brief` 一屏"


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


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


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(["--root", str(root), *args])
    return rc, out.getvalue(), err.getvalue()


def budget_fields(text: str) -> dict[str, int]:
    match = re.search(
        r"本次必读字节：(\d+)（阈值 32768；摘要 (\d+) / brief (\d+) / "
        r"看板（不整读）(\d+) / 活跃流 (\d+) / feedback (\d+) / handoff (\d+)）",
        text,
    )
    if match is None:
        raise AssertionError(f"预算分项未按新口径输出：\n{text}")
    keys = ("total", "summary", "brief", "board", "streams", "feedback", "handoff")
    return {key: int(value) for key, value in zip(keys, match.groups())}


class TemplateRulesTest(unittest.TestCase):
    """① helm 白名单与规则文档口径：brief 在、整看板不在必读白名单。"""

    def test_helm_whitelist_uses_brief_not_whole_board(self):
        for rel in HELM:
            text = read(rel)
            with self.subTest(rel=rel):
                whitelist = next(
                    line for line in text.splitlines() if "交接读取范围（硬）" in line)
                self.assertIn(BRIEF_FIRST_ITEM, whitelist)
                self.assertNotIn("只读白名单＝看板", whitelist)
                # S-5：White list 的 Status grep 只是核对手段，不计入交接必读预算。
                self.assertIn("活跃流 plan 的 Next Step **未完成**首条", whitelist)
                self.assertIn("Status 仅核对", whitelist)
                self.assertIn("不计入", whitelist)
                self.assertIn("单文件 >20KB 不整读", whitelist)
                self.assertIn("看板仅按关键词 grep", whitelist)
                budget = next(
                    line for line in text.splitlines() if "读取量预算（硬）" in line)
                self.assertIn("交接必读 ≤32KB", budget)
                self.assertIn("超阈值先摘要/关键词 grep", budget)
                self.assertIn("写明只读段", budget)

    def test_plan_discipline_rule4_synced(self):
        text = read(DISCIPLINE)
        line = next(
            line for line in text.splitlines() if line.startswith("4. **"))
        self.assertNotIn("交接看板优先", line)
        self.assertIn("python3 scripts/plan-status.py --brief", line)
        self.assertIn("看板仅按关键词 grep", line)
        for keyword in ("白名单", "32KB", "Decisions", "Deviation", "Backlog", "不整读"):
            self.assertIn(keyword, line, f"§5.1 第 4 条缺少：{keyword}")

    def test_crew_rules_f11_synced(self):
        line = next(
            line for line in read(RULES).splitlines() if "**F11 " in line)
        self.assertIn("plan-status.py --brief", line)
        self.assertIn("看板仅按关键词 grep", line)
        for keyword in ("读取量预算", "32KB", "Decisions", "Deviation", "Backlog", "不整读"):
            self.assertIn(keyword, line, f"F11 缺少：{keyword}")


class HandoverBudgetTest(unittest.TestCase):
    """② --handover 预算分项：brief 计入、整看板不计入、超限提示、激活流场景。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _repo(self) -> Path:
        return make_repo(self.tmp, {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body(
                [("阶段一", "complete"), ("阶段二", "in_progress")],
                next_step="推进阶段二。"),
        }, progress=progress_body())

    def _feedback(self, repo: Path) -> Path:
        return repo / ".planning/portfolio/feedback.md"

    def test_brief_counted_and_board_not_counted(self):
        repo = self._repo()
        rc, out, err = run(repo, "--handover")
        self.assertEqual(rc, 0, err)
        base = budget_fields(out)
        self.assertGreater(base["brief"], 0)
        self.assertEqual(0, base["board"])
        self.assertEqual(base["total"], sum(base[key] for key in (
            "summary", "brief", "streams", "feedback", "handoff")))
        # brief 分项与 --brief 实际输出同口径（同一渲染复用，字节一致）。
        _rc, brief_out, _err = run(repo, "--brief")
        self.assertEqual(len(brief_out.encode("utf-8")), base["brief"])
        # 整看板写入 40KB 不推高必读字节（仅新鲜状态字符串可能有极少量差异）。
        board = repo / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"x" * 40000)
        rc, out, err = run(repo, "--handover")
        self.assertEqual(rc, 0, err)
        after = budget_fields(out)
        self.assertEqual(0, after["board"])
        self.assertLess(after["total"], base["total"] + 1024)
        self.assertLessEqual(after["total"], BUDGET)

    def test_over_threshold_hint_via_feedback(self):
        repo = self._repo()
        feedback = self._feedback(repo)
        feedback.parent.mkdir(parents=True, exist_ok=True)
        feedback.write_text("- 待复盘 " + "x" * 40000 + "\n", encoding="utf-8")
        rc, out, _err = run(repo, "--handover")
        self.assertEqual(rc, 0)
        fields = budget_fields(out)
        # A1：feedback 分项封顶后 total 硬 ≤32KB；原始必读仍超 → 保留摘要/grep 提示。
        self.assertLessEqual(fields["total"], BUDGET)
        self.assertIn("已封顶", out)
        self.assertIn("超预算", out)
        self.assertIn("先摘要", out)
        self.assertIn("grep", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_active_streams_fixture_stays_under_budget(self):
        refs = [f".planning/stream-{i}/task_plan.md" for i in range(1, 6)]
        plans = {"portfolio": portfolio_body(refs)}
        for i in range(1, 6):
            plans[f"stream-{i}"] = stream_body(
                [("阶段一", "complete"), ("阶段二", "in_progress")],
                next_step="推进阶段二（长行占位）。" * 6)
        repo = make_repo(self.tmp, plans, progress=progress_body())
        board = repo / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"b" * 60000)
        rc, out, err = run(repo, "--handover")
        self.assertEqual(rc, 0, err)
        fields = budget_fields(out)
        self.assertLessEqual(fields["total"], BUDGET)
        self.assertNotIn("超预算", out)


if __name__ == "__main__":
    unittest.main()
