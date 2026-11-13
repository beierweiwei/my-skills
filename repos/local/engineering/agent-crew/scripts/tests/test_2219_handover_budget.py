#!/usr/bin/env python3
"""2.2.19-A：交接摘要结构与硬预算（`plan-status.py --handover`）。

行为断言（F8，按本流 plan V1–V3 与工单断言）：
① 摘要结构：Goal ≤2 行；活跃流一行含 plan 路径/当前阶段/Next Step 首条（≤200 字符）/
   待 Captain 计数；待 Captain ≤3 条标题（≤120 字符）+ 溢出计数；最近 progress 3 条；
   未处理通知段保留。
② 预算口径：分项＝摘要（不含活跃流行）+ brief + 活跃流 Next Step 首条（每流 ≤200 字符）+
   feedback 待复盘命中（最多计 10 行）+ handoff；整看板/整 plan 不计入；输出「只读段」说明。
③ 硬上限：stdout ≤32,768B（60KB+ 看板、100KB plan、50 条待 Captain、>50KB Next Step、
   超长 progress 夹具）；超限确定性截断并标注「已截断，详情见 plan」；只读不写文件。
④ 模板/规则同步：helm qoder/opencode「交接读取范围（硬）」明确 digest + brief + 活跃流
   Next Step grep、不读整看板；plan-discipline §5.1 第 4 条与 crew-rules F11 同步预算新口径。
fixture 全部为中性占位内容，不含任何业务词。
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
_spec = importlib.util.spec_from_file_location("plan_status_2219_handover", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_2219_handover"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

OUTPUT_CAP = 8192      # S4：`--handover` stdout 硬上限（原 BUDGET 断言改 8192）
THRESHOLD = 32768      # 必读预算阈值（语义不变）
HELM = (
    "templates/agents/qoder/helm.md",
    "templates/agents/opencode/helm.md",
)
DISCIPLINE = "references/plan-discipline.md"
RULES = "references/crew-rules.md"
BUDGET_RE = re.compile(
    r"本次必读字节：(\d+)（阈值 32768；摘要 (\d+) / brief (\d+) / "
    r"看板（不整读）(\d+) / 活跃流 (\d+) / feedback (\d+) / handoff (\d+)）"
)
BUDGET_KEYS = ("total", "summary", "brief", "board", "streams", "feedback", "handoff")
# B-1：封顶/超预算时的封顶前原值行（raw_total + 各分项原值）。
RAW_RE = re.compile(
    r"封顶前实际必读：(\d+)（摘要 (\d+) / brief (\d+) / 活跃流 (\d+) / "
    r"feedback (\d+) / handoff (\d+)）"
)
RAW_KEYS = ("raw_total", "summary", "brief", "streams", "feedback", "handoff")


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


def stream_body(phases: list[tuple[str, str]], next_step: str = "推进下一条。",
                extra: str = "") -> str:
    lines = ["# 流 plan", "", "## Goal", "流目标。", "", "## Next Step", next_step, "",
             "## Phases", ""]
    for index, (name, status) in enumerate(phases, 1):
        lines += [f"### Phase {index}: {name}", "**Status:** {status}".format(status=status), ""]
    return "\n".join(lines) + "\n" + extra


def portfolio_body(stream_refs: list[str], captain_rows: list[str] | None = None,
                   goal: str = "组合目标：推进多条流。",
                   next_step: str = "推进流 A。",
                   recent: list[str] | None = None) -> str:
    lines = ["# 测试项目组合 plan", "", "## Goal", goal, "", "## Next Step", next_step, "",
             "## 最近完成", ""]
    lines += recent or []
    lines += ["", "## Phases", ""]
    for index, ref in enumerate(stream_refs, 1):
        lines += [f"### Phase {index}: 流{index}", f"- plan: `{ref}`",
                  "**Status:** pending", ""]
    lines += ["## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
    lines += captain_rows or []
    lines += [""]
    return "\n".join(lines) + "\n"


def progress_body(entries: list[tuple[str, str]]) -> str:
    lines = ["# Progress（编排台账）", "", "## 自动驾驶里程", ""]
    for day, text in entries:
        lines.append(f"- {day} {text}")
    return "\n".join(lines) + "\n"


def numbered_entries(n: int) -> list[tuple[str, str]]:
    return [(f"2026-11-{(i % 28) + 1:02d}", f"条目 {i:03d}") for i in range(1, n + 1)]


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """文件内容 + mtime_ns（证明 --handover 零写入）。"""
    return {
        path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def run(root: Path, *args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(["--root", str(root), *args])
    return rc, out.getvalue(), err.getvalue()


def budget_fields(text: str) -> dict[str, int]:
    match = BUDGET_RE.search(text)
    if match is None:
        raise AssertionError(f"预算分项未按新口径输出：\n{text}")
    return {key: int(value) for key, value in zip(BUDGET_KEYS, match.groups())}


def raw_budget_fields(text: str) -> dict[str, int]:
    match = RAW_RE.search(text)
    if match is None:
        raise AssertionError(f"封顶前原值未输出：\n{text}")
    return {key: int(value) for key, value in zip(RAW_KEYS, match.groups())}


def section(out: str, header: str, next_header: str) -> str:
    return out.split(header, 1)[1].split(next_header, 1)[0].strip("\n")


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class HandoverStructureTest(_TmpRepoTest):
    """V1 摘要结构 + V3 夹具硬上限（超长 Goal/Next Step/progress、50 条待 Captain）。"""

    def _repo(self) -> Path:
        big = "长" * 20000
        next_step = "超长下一步-STREAM-START " + "推" * 60000 + " TAIL-NEXT-END"
        plans = {
            "portfolio": portfolio_body(
                [".planning/stream-a/task_plan.md", ".planning/stream-b/task_plan.md"],
                captain_rows=[
                    f"| C{i:02d} | 待拍板事项 {i} " + "题" * 500 + " | P0 | — | 需 Captain 决策 |"
                    for i in range(1, 51)
                ],
                goal="组合目标-前缀-GOAL" + big + "\n第二行-GOAL-L2",
                next_step="组合下一步-NEXT" + big,
                recent=["- [x] 最近完成甲", "- [x] 最近完成乙",
                        "- [x] 最近完成丙", "- [x] 最近完成丁"],
            ),
            "stream-a": stream_body(
                [("阶段一", "complete"), ("阶段二", "in_progress")],
                next_step=next_step,
                # 100KB plan：未渲染段不整读、不计入必读量。
                extra="## 备注\n\n" + "注" * 100000 + "\n",
            ),
            "stream-b": stream_body(
                [("阶段一", "complete"), ("阶段二", "in_progress")],
                next_step="普通下一步。",
            ),
        }
        progress = progress_body([
            ("2026-11-01", "超长 Progress 前缀-PROGRESS " + "z" * 50000 + " PROGRESS-TAIL-END"),
            ("2026-11-02", "条目 002"),
            ("2026-11-03", "条目 003"),
            ("2026-11-04", "条目 004"),
        ])
        repo = make_repo(self.tmp, plans, progress=progress)
        board = repo / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"b" * 65000)
        return repo

    def test_stdout_hard_cap_required_fields_and_truncation_marker(self):
        repo = self._repo()
        rc, out, err = run(repo, "--handover")
        self.assertEqual(rc, 0, err)
        self.assertLessEqual(len(out.encode("utf-8")), OUTPUT_CAP)
        for header in ("## Goal", "## Next Step", "## 活跃流", "## 待 Captain",
                       "## 未处理通知", "## 最近 progress", "## 最近完成",
                       "## 证据入口", "## 看板", "## 交接必读预算"):
            self.assertIn(header, out)
        self.assertIn("已截断，详情见 plan", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)
        # 确定性：同仓两次输出逐字节一致。
        _rc, again, _err = run(repo, "--handover")
        self.assertEqual(out, again)

    def test_goal_max_two_lines_and_next_step_clipped(self):
        repo = self._repo()
        _rc, out, _err = run(repo, "--handover")
        goal = section(out, "## Goal\n", "## Next Step")
        self.assertLessEqual(len(goal.splitlines()), 2)
        self.assertIn("组合目标-前缀-GOAL", goal)
        active = section(out, "## 活跃流\n", "## 待 Captain")
        rows = [line for line in active.splitlines() if line.startswith("- **")]
        self.assertEqual(2, len(rows))  # 每活跃流一行（两条流各一行）
        row = rows[0]
        self.assertIn("`.planning/stream-a/task_plan.md`", row)
        self.assertIn("当前阶段：阶段二", row)
        self.assertIn("超长下一步-STREAM-START", row)
        self.assertIn("待 Captain：0", row)
        self.assertNotIn("TAIL-NEXT-END", out)  # >50KB Next Step 只留首条 ≤200 字符
        self.assertIn("普通下一步。", out)
        # portfolio Next Step 摘要同样受单行上限约束（100KB plan 不撑爆输出）。
        portfolio_next = section(out, "## Next Step\n", "## 活跃流")
        self.assertIn("组合下一步-NEXT", portfolio_next)
        self.assertLessEqual(len(portfolio_next.splitlines()), 1)

    def test_captain_max_three_titles_plus_overflow(self):
        repo = self._repo()
        _rc, out, _err = run(repo, "--handover")
        captain = section(out, "## 待 Captain\n", "## 未处理通知")
        titles = [line for line in captain.splitlines() if line.startswith("- [ ] ⏸")]
        self.assertEqual(3, len(titles))
        for line in titles:
            self.assertLessEqual(len(line), 120)
        self.assertIn("另有 47 条", captain)

    def test_recent_progress_three_and_truncated_huge_entry(self):
        repo = self._repo()
        _rc, out, _err = run(repo, "--handover")
        recent = section(out, "## 最近 progress（顶部最新 3 条）\n", "## 最近完成")
        lines = [line for line in recent.splitlines() if line.strip()]
        self.assertEqual(3, len(lines))
        self.assertIn("超长 Progress 前缀-PROGRESS", lines[0])
        self.assertNotIn("PROGRESS-TAIL-END", out)
        self.assertIn("条目 002", recent)
        self.assertIn("条目 003", recent)
        self.assertNotIn("条目 004", recent)

    def test_budget_counts_stream_rows_once_and_clipped_next_step(self):
        repo = self._repo()
        _rc, out, _err = run(repo, "--handover")
        fields = budget_fields(out)
        self.assertEqual(0, fields["board"])
        head = out.split("## 交接必读预算", 1)[0]
        lines = head.splitlines()
        start = lines.index("## 活跃流") + 1
        end = lines.index("## 待 Captain")
        # S-1：活跃流整行（流名/plan 路径/阶段/Next Step 首条/待 Captain 计数）计入
        # streams，一行只计一次（summary 已剔除该块，不重复）。
        stream_expected = sum(len(line.encode("utf-8")) + 1
                              for line in lines[start:end])
        self.assertEqual(stream_expected, fields["streams"])
        self.assertGreater(fields["streams"], 0)
        # 摘要分项不含活跃流行：按输出逐行复算（去掉活跃流内容与预算块）。
        expected = sum(len(line.encode("utf-8")) + 1
                       for index, line in enumerate(lines) if not start <= index < end)
        self.assertEqual(expected, fields["summary"])
        self.assertEqual(fields["total"], sum(fields[key] for key in (
            "summary", "brief", "streams", "feedback", "handoff")))
        self.assertIn("只读段", out)
        self.assertNotIn("已封顶", out)  # 正常夹具不被封顶，分项口径保持
        self.assertNotIn("封顶前实际必读", out)

    def test_handover_is_read_only(self):
        repo = self._repo()
        before = snapshot(repo)
        _rc, _out, _err = run(repo, "--handover")
        self.assertEqual(snapshot(repo), before)


class HandoverFeedbackCapTest(_TmpRepoTest):
    """V2 + A1：feedback 待复盘命中最多计 10 行且 ≤8KB；total 硬 ≤32,768B。"""

    def _repo(self, lines: int) -> Path:
        repo = make_repo(self.tmp / f"case-{lines}", {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body([("阶段一", "in_progress")]),
        }, progress=progress_body(numbered_entries(3)))
        feedback = repo / ".planning/portfolio/feedback.md"
        feedback.write_text(
            "".join(f"- 待复盘 {i:02d} " + "f" * 4000 + "\n" for i in range(lines)),
            encoding="utf-8")
        return repo

    def test_feedback_component_and_total_hard_capped(self):
        ten = self._repo(10)
        _rc, out_ten, _err = run(ten, "--handover")
        fields_ten = budget_fields(out_ten)
        self.assertEqual(ps.HANDOVER_FEEDBACK_MAX_BYTES, fields_ten["feedback"])
        self.assertLessEqual(fields_ten["total"], THRESHOLD)  # A1：total 也硬 ≤32KB
        self.assertIn("已封顶", out_ten)
        self.assertIn("其余按需 grep", out_ten)
        self.assertIn("超预算", out_ten)  # 原始必读仍超 → 摘要/grep 提示保留
        self.assertLessEqual(len(out_ten.encode("utf-8")), OUTPUT_CAP)

        # 20 行（前 10 行内容相同 + 追加 10 行）不得继续推高 feedback 分项。
        more = self._repo(20)
        _rc, out_more, _err = run(more, "--handover")
        fields_more = budget_fields(out_more)
        self.assertEqual(fields_ten["feedback"], fields_more["feedback"])
        self.assertLessEqual(fields_more["total"], THRESHOLD)
        self.assertLessEqual(len(out_more.encode("utf-8")), OUTPUT_CAP)


class CappedHonestyTest(_TmpRepoTest):
    """B-1：封顶不得低报——输出封顶前原值（raw_total + 被截分项），按实际被截
    分项归因，handoff 原值过大给可执行提示。"""

    def _repo(self, handoff_bytes: int = 0, feedback_lines: int = 0,
              feedback_line_bytes: int = 1000) -> Path:
        plans = {
            "portfolio": portfolio_body(
                [".planning/stream-a/task_plan.md"],
                goal="目标-GOAL " + "目" * 512,
                next_step="组合下一步-NEXT " + "步" * 512,
            ),
            "stream-a": stream_body(
                [("阶段一", "complete"), ("阶段二", "in_progress")],
                next_step="流下一步 " + "推" * 512),
        }
        repo = make_repo(self.tmp / f"capped-{handoff_bytes}-{feedback_lines}", plans,
                         progress=progress_body([
                             (f"2026-11-0{i}", "进度 " + "p" * 3000) for i in range(1, 4)]))
        if feedback_lines:
            feedback = repo / ".planning/portfolio/feedback.md"
            feedback.write_text(
                "".join(f"- 待复盘 {i:02d} " + "f" * (feedback_line_bytes - 16) + "\n"
                        for i in range(feedback_lines)), encoding="utf-8")
        if handoff_bytes:
            handoff = repo / ".crew/guard.handoff"
            handoff.parent.mkdir(parents=True, exist_ok=True)
            handoff.write_bytes(b"h" * handoff_bytes)
        return repo

    def test_capped_reports_raw_total_and_component_values(self):
        repo = self._repo(handoff_bytes=20480, feedback_lines=10)
        _rc, out, err = run(repo, "--handover")
        self.assertEqual(0, _rc, err)
        fields = budget_fields(out)
        raw = raw_budget_fields(out)
        self.assertLessEqual(fields["total"], THRESHOLD)
        self.assertLessEqual(len(out.encode("utf-8")), OUTPUT_CAP)
        self.assertEqual(20480, raw["handoff"])  # 被截分项原值 = 文件真实字节
        self.assertGreater(raw["raw_total"], fields["total"])  # 封顶前必读更高
        self.assertEqual(raw["raw_total"], sum(raw[key] for key in (
            "summary", "brief", "streams", "feedback", "handoff")))
        # 缺口必须对得上：raw_total - total == (raw_feedback - feedback)
        # + (raw_handoff - handoff)，证明没有其它分项被静默压数。
        self.assertEqual(raw["raw_total"] - fields["total"],
                         (raw["feedback"] - fields["feedback"])
                         + (raw["handoff"] - fields["handoff"]))
        capped = next(line for line in out.splitlines() if "已封顶" in line)
        self.assertIn("handoff", capped)  # 归因到实际被截分项，不再只归因 feedback
        self.assertIn("其余按需 grep", capped)
        self.assertIn("guard.handoff 过大（20480 B", out)
        self.assertIn("清理/精简", out)

    def test_handoff_only_capped_attributes_handoff(self):
        repo = self._repo(handoff_bytes=61440)
        _rc, out, _err = run(repo, "--handover")
        raw = raw_budget_fields(out)
        self.assertEqual(61440, raw["handoff"])
        self.assertGreater(raw["raw_total"], THRESHOLD)
        capped = next(line for line in out.splitlines() if "已封顶" in line)
        self.assertIn("handoff", capped)
        self.assertNotIn("feedback 待复盘命中", capped)
        self.assertIn("guard.handoff 过大（61440 B", out)

    def test_feedback_only_capped_attributes_feedback(self):
        repo = self._repo(feedback_lines=10)  # 10×1KB > 8KB 分项上限，无 handoff
        _rc, out, _err = run(repo, "--handover")
        raw = raw_budget_fields(out)
        self.assertEqual(0, raw["handoff"])
        self.assertGreater(raw["feedback"], ps.HANDOVER_FEEDBACK_MAX_BYTES)
        capped = next(line for line in out.splitlines() if "已封顶" in line)
        self.assertIn("feedback 待复盘命中", capped)
        self.assertNotIn("guard.handoff 过大", out)


class RawBudgetBoundaryTest(_TmpRepoTest):
    """S-4：over_budget 按 raw_total 精确判定——32,768 无提示、32,769 有提示。"""

    def _plans(self) -> dict[str, str]:
        return {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body([("阶段一", "in_progress")]),
        }

    def _repo(self, name: str, feedback_cost: int | None = None) -> Path:
        repo = make_repo(self.tmp / name, self._plans(),
                         progress=progress_body(numbered_entries(3)))
        if feedback_cost is not None:
            prefix = "- 待复盘 "
            line = prefix + "f" * (feedback_cost - 1 - len(prefix.encode("utf-8")))
            feedback = repo / ".planning/portfolio/feedback.md"
            feedback.write_text(line + "\n", encoding="utf-8")
        return repo

    def _base(self) -> int:
        _rc, out, _err = run(self._repo("base"), "--handover")
        fields = budget_fields(out)
        return fields["summary"] + fields["brief"] + fields["streams"] + fields["handoff"]

    def test_raw_total_exact_boundary(self):
        base = self._base()
        # 单行 feedback 使 raw_total 恰好落在阈值线上（cost 含行尾换行）。
        at_limit = self._repo("at-limit", feedback_cost=THRESHOLD - base)
        _rc, out_ok, _err = run(at_limit, "--handover")
        self.assertNotIn("超预算", out_ok)
        self.assertEqual(THRESHOLD, raw_budget_fields(out_ok)["raw_total"])
        one_over = self._repo("one-over", feedback_cost=THRESHOLD + 1 - base)
        _rc, out_over, _err = run(one_over, "--handover")
        self.assertIn("超预算", out_over)
        self.assertEqual(THRESHOLD + 1, raw_budget_fields(out_over)["raw_total"])


class HandoverExtremeTest(_TmpRepoTest):
    """V3 + A1：9 条活跃流 + 超长阶段名/C 行 + 3 条超长 progress/完成 +
    10 条数 KB feedback → stdout 与 total 均硬 ≤32KB 且必备段齐全。"""

    def test_extreme_fixture_stays_under_hard_cap(self):
        refs = [f".planning/stream-{i}/task_plan.md" for i in range(1, 10)]
        plans = {
            "portfolio": portfolio_body(
                refs,
                captain_rows=[
                    f"| C{i:02d} | 事项 " + "题" * 2000 + " | P0 | — | 需 Captain 决策 |"
                    for i in range(1, 21)
                ],
                goal="超长目标-GOAL " + "目" * 50000,
                next_step="超长组合下一步-NEXT " + "步" * 50000,
                recent=["- [x] 完成 " + "成" * 50000 for _ in range(3)],
            ),
        }
        for i in range(1, 10):
            # S4：输出硬 ≤8KB——流 1 保留超长 Next Step 验证 clip；流 2–9 短 Next Step
            # 使浅收缩档即可收敛（max_streams=8 语义与「另有 1 个活跃流」断言保持）。
            plans[f"stream-{i}"] = stream_body(
                [("阶段一", "complete"),
                 ("阶段二 " + ("阶" * 50000 if i == 1 else ""), "in_progress")],
                next_step=(f"流{i}下一步-START " + "推" * 50000 if i == 1
                           else f"流{i}推进下一条。"),
            )
        # S4：输出硬 ≤8KB——progress 渲染条目缩短使「预算已封顶」行在输出内可见；
        # raw_total 仍超 32KB 阈值（feedback 10 行 × 数 KB），封顶/超预算提示不受影响。
        progress = progress_body([
            (f"2026-11-0{i}", f"超长进度 {i} " + "进" * 150)
            for i in range(1, 4)
        ])
        repo = make_repo(self.tmp, plans, progress=progress)
        board = repo / "docs/进度追踪.md"
        board.parent.mkdir(parents=True, exist_ok=True)
        board.write_bytes(b"b" * 65000)
        feedback = repo / ".planning/portfolio/feedback.md"
        feedback.write_text(
            "".join(f"- 待复盘 {i:02d} " + "f" * 4000 + "\n" for i in range(10)),
            encoding="utf-8")

        _rc, out, err = run(repo, "--handover")
        self.assertEqual(0, _rc, err)
        self.assertLessEqual(len(out.encode("utf-8")), OUTPUT_CAP)
        fields = budget_fields(out)
        self.assertLessEqual(fields["total"], THRESHOLD)  # A1：病理夹具 total 也硬 ≤32KB
        self.assertIn("已封顶", out)
        self.assertIn("其余按需 grep", out)
        for header in ("## Goal", "## Next Step", "## 活跃流", "## 待 Captain",
                       "## 未处理通知", "## 最近 progress", "## 最近完成",
                       "## 证据入口", "## 看板", "## 交接必读预算"):
            self.assertIn(header, out)
        self.assertIn("已截断，详情见 plan", out)
        active = section(out, "## 活跃流\n", "## 待 Captain")
        self.assertIn("另有 1 个活跃流", active)
        self.assertLessEqual(len([l for l in active.splitlines() if l.startswith("- **")]),
                             ps.HANDOVER_MAX_STREAMS)
        captain = section(out, "## 待 Captain\n", "## 未处理通知")
        self.assertLessEqual(len([l for l in captain.splitlines()
                                  if l.startswith("- [ ] ⏸")]),
                             ps.HANDOVER_MAX_CAPTAIN)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)


class TemplateRulesSyncTest(unittest.TestCase):
    """V5：模板/规则同步新预算口径（grep 防回退；不削弱既有 FB-044 断言）。"""

    def test_helm_templates_state_digest_brief_next_step_not_whole_board(self):
        for rel in HELM:
            text = read(rel)
            whitelist = next(
                line for line in text.splitlines() if "交接读取范围（硬）" in line)
            with self.subTest(rel=rel):
                self.assertIn("digest", whitelist)
                self.assertIn("--brief", whitelist)
                self.assertIn("Next Step", whitelist)
                self.assertIn("不读整看板", whitelist)
                budget = next(
                    line for line in text.splitlines() if "读取量预算（硬）" in line)
                self.assertIn("实际要求读的段", budget)
                self.assertIn("整看板/整 plan 不计入", budget)
                self.assertIn("输出自身硬 ≤8KB", budget)
                self.assertIn("已截断，详情见 plan", budget)

    def test_plan_discipline_and_crew_rules_synced(self):
        discipline = next(line for line in read(DISCIPLINE).splitlines()
                          if line.startswith("4. **"))
        for keyword in ("实际要求读的段", "整看板", "输出硬 ≤32KB", "已截断，详情见 plan"):
            self.assertIn(keyword, discipline, f"§5.1.4 缺少：{keyword}")
        f11 = next(line for line in read(RULES).splitlines() if "**F11 " in line)
        for keyword in ("实际要求读的段", "整看板/整 plan 不计入",
                        "输出硬 ≤32KB", "已截断"):
            self.assertIn(keyword, f11, f"F11 缺少：{keyword}")

    def test_status_grep_not_billed_in_discipline_and_helm(self):
        # S-5：白名单 Status grep 只是核对手段，不计入交接必读预算；与 F11/模板预算行一致。
        discipline = next(line for line in read(DISCIPLINE).splitlines()
                          if line.startswith("4. **"))
        self.assertIn("Next Step 首条", discipline)
        self.assertIn("Status 仅核对", discipline)
        self.assertIn("不计入", discipline)
        for rel in HELM:
            whitelist = next(
                line for line in read(rel).splitlines() if "交接读取范围（硬）" in line)
            with self.subTest(rel=rel):
                self.assertIn("Next Step **未完成**首条", whitelist)
                self.assertIn("Status 仅核对", whitelist)
                self.assertIn("不计入", whitelist)


if __name__ == "__main__":
    unittest.main(verbosity=2)
