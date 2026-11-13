#!/usr/bin/env python3
"""plan-status.py 一屏摘要（--brief）与内容卫生 lint 用例（2.2.16 T44）。

覆盖：
① --brief：活跃流一屏表（流｜阶段｜阶段完成数/健康｜下一步｜待 Captain）+ 今日变化 3 行；
   「待 Captain」含 portfolio Backlog ⏸/C 前缀项摘要；行数 ≤ BRIEF_MAX_LINES；
   无活跃流/无 portfolio/无看板时明确提示不崩；只读不落盘。
② 卫生 lint（--lint 附带，非阻断）：Next Step 超长（阈值 HYGIENE_NEXT_STEP_MAX）/
   Next Step 含 ✅ 或 [x] / 未完成 Phase 有 todo 项却无 owner / 样板占位残留；
   命中输出 file:line + [规则] + 建议；默认 rc=0，--lint --strict 才 rc=1；
   结构性 lint 契约不变；注释/fenced code 不误报。
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
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_brief_lint", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_brief_lint"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)

TODAY = datetime.now().strftime("%Y-%m-%d")
YESTERDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
BRIEF_HEADER = "| 流 | 阶段 | 阶段完成数/健康 | 下一步 | 待 Captain |"


def make_repo(
    tmp: Path,
    plans: dict[str, str],
    config: dict | None = None,
    progress: str | None = None,
    name: str = "repo",
) -> Path:
    """建一个最小目标仓：.planning/<name>/task_plan.md [+ orchestration.json + progress]。"""
    root = tmp / name
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
    if progress is not None:
        pdir = planning / "portfolio"
        pdir.mkdir(exist_ok=True)
        (pdir / "progress.md").write_text(progress, encoding="utf-8")
    return root


def portfolio_body(
    streams: tuple[str, ...] = ("stream-a",),
    backlog_rows: tuple[str, ...] = (),
    next_step: str = "推进流 A。",
) -> str:
    lines = ["# 总览 plan", "", "## Goal", "目标。", "", "## Next Step", next_step, "",
             "## Phases", ""]
    for i, name in enumerate(streams, 1):
        lines += [
            f"### Phase {i}: {name}",
            f"- plan: `.planning/{name}/task_plan.md`",
            "**Status:** pending",
            "",
        ]
    lines += ["## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
    lines += list(backlog_rows)
    return "\n".join(lines) + "\n"


def stream_body(
    title: str = "流 A",
    next_step: str = "推进下一步。",
    phases: tuple[tuple[str, str], ...] = (("阶段一", "pending"),),
    items: tuple[str, ...] = ("- [ ] 切片。",),
    owner: bool = True,
    backlog_rows: tuple[str, ...] = (),
    chain_rows: tuple[str, ...] = (),
) -> str:
    lines = [f"# {title}", "", "## Goal", "目标。", "", "## Next Step", next_step, "",
             "## Phases", ""]
    for i, (name, status) in enumerate(phases, 1):
        lines.append(f"### Phase {i}: {name}")
        if owner:
            lines.append("- owner: test-owner")
        lines.extend(items)
        lines += [f"**Status:** {status}", ""]
    if chain_rows:
        lines += ["## 业务链", "", "| 节点 | 状态 | 说明 |", "|---|---|---|"]
        lines += list(chain_rows)
        lines.append("")
    if backlog_rows:
        lines += ["## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
        lines += list(backlog_rows)
        lines.append("")
    return "\n".join(lines) + "\n"


def line_of(body: str, needle: str) -> int:
    for no, line in enumerate(body.splitlines(), 1):
        if needle in line:
            return no
    raise AssertionError(f"fixture 中找不到 {needle!r}")


def progress_body(entries: tuple[str, ...]) -> str:
    lines = ["# Progress", "", "## 自动驾驶里程", ""]
    lines += [f"- {entry}" for entry in entries]
    return "\n".join(lines) + "\n"


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(argv)
    return rc, out.getvalue(), err.getvalue()


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


class BriefTableTest(_TmpRepoTest):
    """契约①：--brief 一屏表结构与降级提示。"""

    def _repo(self, **kwargs) -> Path:
        plans = kwargs.pop("plans", None)
        if plans is None:
            plans = {
                "x-portfolio": portfolio_body(streams=("stream-a", "stream-b", "stream-c")),
                "stream-a": stream_body(
                    title="流 A",
                    next_step="推进 A 的下一步。",
                    phases=(("阶段一", "complete"), ("阶段二", "pending")),
                ),
                "stream-b": stream_body(title="流 B", next_step="推进 B 的下一步。"),
                "stream-c": stream_body(
                    title="流 C",
                    phases=(("阶段一", "complete"),),
                    items=("- [x] 已收口。",),
                ),
            }
        return make_repo(self.tmp, plans, **kwargs)

    def test_table_structure_and_active_only(self):
        root = self._repo()
        rc, out, err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertIn(BRIEF_HEADER, out)
        self.assertIn("| stream-a | 阶段二 | 1/2 🟡 | 推进 A 的下一步。 | 0 |", out)
        self.assertIn("| stream-b | 阶段一 | 0/1 🟡 | 推进 B 的下一步。 | 0 |", out)
        self.assertNotIn("| stream-c |", out)  # 全部 complete 的流不属活跃流
        self.assertLessEqual(len(out.splitlines()), ps.BRIEF_MAX_LINES)
        self.assertIn("## 待 Captain（0 条）", out)
        self.assertIn(f"## 今日变化（{TODAY}）", out)

    def test_next_step_cell_clipped(self):
        long_step = "动作" * (ps.BRIEF_NEXT_STEP_CELL_MAX)
        root = self._repo(plans={
            "x-portfolio": portfolio_body(streams=("stream-a",)),
            "stream-a": stream_body(next_step=long_step),
        })
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        cell = [l for l in out.splitlines() if l.startswith("| stream-a |")][0]
        self.assertIn("…", cell)
        self.assertLess(len(cell), len(long_step) + 60)
        self.assertLessEqual(len(out.splitlines()), ps.BRIEF_MAX_LINES)

    def test_stream_overflow_capped(self):
        names = [f"stream-{i}" for i in range(10)]
        plans = {"x-portfolio": portfolio_body(streams=tuple(names))}
        for name in names:
            plans[name] = stream_body(title=name)
        root = self._repo(plans=plans)
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.splitlines()), ps.BRIEF_MAX_LINES)
        self.assertIn(f"另有 {len(names) - ps.BRIEF_MAX_STREAMS} 个活跃流", out)

    def test_no_active_streams_explicit(self):
        root = self._repo(plans={
            "x-portfolio": portfolio_body(streams=("stream-a",)),
            "stream-a": stream_body(phases=(("阶段一", "complete"),),
                                    items=("- [x] 已收口。",)),
        })
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn("无活跃流", out)

    def test_no_portfolio_explicit_and_today_still_shown(self):
        root = make_repo(
            self.tmp,
            {"stream-a": stream_body()},
            progress=progress_body((f"{TODAY} 今日进展。",)),
        )
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn("未找到 portfolio plan", out)
        self.assertIn(f"- {TODAY} 今日进展。", out)

    def test_missing_board_explicit(self):
        root = self._repo()
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn("看板", out)
        self.assertIn("过期", out)  # 看板不存在时 freshness 明确标过期，不崩

    def test_brief_read_only(self):
        root = self._repo()
        rc, _out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertFalse((root / "docs").exists())  # --brief 不派生看板文件

    def test_over_limit_truncates_with_warning_rc0(self):
        """S5：异常数据超行数上限时显式截断 + 可读告警，仍 rc=0（不再 assert 崩溃）。"""
        root = self._repo()
        flood = [f"- 异常变化 {i}" for i in range(ps.BRIEF_MAX_LINES * 2)]
        with mock.patch.object(ps, "_today_changes", return_value=flood):
            rc, out, err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), ps.BRIEF_MAX_LINES)
        self.assertIn("已截断", lines[-1])
        self.assertIn("截断", err)
        self.assertIn("看板", err)


class BriefCaptainAndTodayTest(_TmpRepoTest):
    """契约②：待 Captain 摘要与今日变化。"""

    def test_captain_summary_and_per_stream_count(self):
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body(
                streams=("stream-a",),
                backlog_rows=(
                    "| C-1 | 定夺 X | P1 | - | 待 Captain 决定 |",
                    "| T9 | 待拍板事项 | P2 | - | ⏸ 等 Captain |",
                ),
            ),
            "stream-a": stream_body(
                backlog_rows=("| C-9 | 等拍板 | P1 | - | ⏸ 待 Captain |",),
                chain_rows=("| 节点X | ⏸ | 等拍板 |",),
            ),
        })
        rc, out, err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0, err)
        # 4 条 = portfolio C 行 + portfolio ⏸ 行 + 流 Backlog ⏸ 行 + 业务链 ⏸ 节点；
        # 摘要最多 3 行，第 4 条（portfolio T9 ⏸）按溢出注折叠，计数仍为 4。
        self.assertIn("## 待 Captain（4 条）", out)
        self.assertIn("**C-1**", out)
        self.assertIn("**C-9**", out)
        self.assertIn("**节点X**", out)
        self.assertIn("另有 1 条", out)
        self.assertIn("| stream-a | 阶段一 | 0/1 🟡 | 推进下一步。 | 2 |", out)

    def test_portfolio_pause_row_extracted(self):
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body(
                streams=("stream-a",),
                backlog_rows=("| T9 | 待拍板事项 | P2 | - | ⏸ 等 Captain |",),
            ),
            "stream-a": stream_body(),
        })
        rc, out, err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0, err)
        self.assertIn("## 待 Captain（1 条）", out)
        self.assertIn("**T9**", out)
        self.assertIn("待拍板事项", out)

    def test_captain_overflow_capped(self):
        rows = tuple(
            f"| C-{i} | 事项 {i} | P1 | - | 待 Captain |" for i in range(1, 6)
        )
        root = make_repo(self.tmp, {
            "x-portfolio": portfolio_body(streams=("stream-a",), backlog_rows=rows),
            "stream-a": stream_body(),
        })
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn(f"另有 {len(rows) - ps.BRIEF_MAX_CAPTAIN} 条", out)
        self.assertLessEqual(len(out.splitlines()), ps.BRIEF_MAX_LINES)

    def test_today_changes_top_three_with_overflow(self):
        entries = tuple(f"{TODAY} 今日第 {i} 件事" for i in range(1, 5)) + (
            f"{YESTERDAY} 昨天的事",
        )
        root = make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(), "stream-a": stream_body()},
            progress=progress_body(entries),
        )
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn(f"- {TODAY} 今日第 1 件事", out)
        self.assertIn(f"- {TODAY} 今日第 3 件事", out)
        self.assertNotIn(f"今日第 4 件事", out)
        self.assertNotIn("昨天的事", out)
        self.assertIn("另有 1 条", out)

    def test_today_changes_empty_explains_latest(self):
        root = make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(), "stream-a": stream_body()},
            progress=progress_body((f"{YESTERDAY} 昨天的事",)),
        )
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn(f"最近：{YESTERDAY}", out)

    def test_progress_missing_explicit(self):
        root = make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(), "stream-a": stream_body()},
        )
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn("progress 缺失", out)


class HygieneLintTest(_TmpRepoTest):
    """契约③：内容卫生 lint 命中/不误报与 rc 语义。"""

    def setUp(self):
        super().setUp()
        self._seq = 0

    def _lint(self, body: str, *extra: str) -> tuple[int, str, str, Path]:
        self._seq += 1
        root = make_repo(self.tmp, {"stream-a": body}, name=f"repo-{self._seq}")
        rc, out, err = run_main(["--root", str(root), "--lint", *extra])
        return rc, out, err, root

    def test_next_step_overlong_warns_rc0(self):
        long_step = "甲" * (ps.HYGIENE_NEXT_STEP_MAX + 1)
        body = stream_body(next_step=long_step)
        rc, out, err, _root = self._lint(body)
        self.assertEqual(rc, 0, err)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, long_step)}: [NextStep 超长]",
            err,
        )
        self.assertIn("lint ok", out)

    def test_next_step_overlong_skips_completed_flow(self):
        long_step = "甲" * (ps.HYGIENE_NEXT_STEP_MAX + 1)
        body = stream_body(
            next_step=long_step,
            phases=(("阶段一", "complete"),),
            items=("- [x] 已收口。",),
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("NextStep 超长", err)

    def test_completed_flow_exempt_from_done_marker_and_placeholder(self):
        """S-8：完成流整体豁免（✅/[x] 与样板残留不再提醒）。"""
        body = stream_body(
            next_step="收口 ✅ 完成 [x]",
            phases=(("<示例阶段>", "complete"),),
            items=("- [x] 已收口。", "- [ ] 示例残留。"),
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("NextStep 完成标记", err)
        self.assertNotIn("样板残留", err)

    def test_incomplete_flow_still_checks_done_marker_and_placeholder(self):
        body = stream_body(
            next_step="收口 ✅ 完成",
            phases=(("<示例阶段>", "pending"),),
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertIn("NextStep 完成标记", err)
        self.assertIn("样板残留", err)

    def test_next_step_done_marker_warns(self):
        for marker in ("- [x] 已完成的事项", "收口 ✅ 完成"):
            with self.subTest(marker=marker):
                body = stream_body(next_step=marker)
                rc, _out, err, _root = self._lint(body)
                self.assertEqual(rc, 0)
                self.assertIn("NextStep 完成标记", err)
                self.assertIn(f".planning/stream-a/task_plan.md:{line_of(body, marker)}", err)

    def test_next_step_done_marker_ok(self):
        body = stream_body(next_step="推进未完成的事项。")
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("NextStep 完成标记", err)

    def test_missing_owner_warns(self):
        body = stream_body(owner=False)
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '### Phase 1: 阶段一')}: "
            "[🟡 无 owner]",
            err,
        )

    def test_owner_present_ok(self):
        body = stream_body(owner=True)
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("无 owner", err)

    def test_complete_phase_without_owner_ok(self):
        body = stream_body(
            phases=(("阶段一", "complete"),), items=("- [x] 已完成。",), owner=False
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("无 owner", err)

    def test_phase_without_items_needs_no_owner(self):
        body = stream_body(items=(), owner=False)
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("无 owner", err)

    def test_placeholder_phase_title_warns(self):
        body = stream_body(phases=(("<阶段名称>", "pending"),))
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertIn(
            f".planning/stream-a/task_plan.md:{line_of(body, '<阶段名称>')}: [样板残留]",
            err,
        )

    def test_placeholder_todo_warns(self):
        body = stream_body(items=("- [ ] 示例任务待替换",))
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertIn(f":{line_of(body, '示例任务待替换')}: [样板残留]", err)

    def test_placeholder_mid_sentence_ok(self):
        for item in ("- [ ] 扫描扫码示例并清理", "- [ ] 改 <crew-dir>/agent-log.jsonl 落点"):
            with self.subTest(item=item):
                body = stream_body(items=(item,))
                rc, _out, err, _root = self._lint(body)
                self.assertEqual(rc, 0)
                self.assertNotIn("样板残留", err)

    def test_placeholder_in_comment_and_fence_skipped(self):
        body = stream_body().replace(
            "## Goal",
            "<!--\n### Phase 2: 示例阶段\n- [ ] 示例任务\n-->\n\n## Goal",
            1,
        ).replace(
            "## Phases",
            "```\n- [ ] 示例任务\n```\n\n## Phases",
            1,
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 0)
        self.assertNotIn("样板残留", err)

    def test_lint_strict_escalates_hygiene(self):
        body = stream_body(owner=False)
        rc, _out, err, _root = self._lint(body, "--strict")
        self.assertEqual(rc, 1)
        self.assertIn("严格模式失败", err)

    def test_strict_requires_lint(self):
        root = make_repo(self.tmp, {"stream-a": stream_body()})
        rc, _out, err = run_main(["--root", str(root), "--strict"])
        self.assertEqual(rc, 1)
        self.assertIn("--strict", err)

    def test_structural_lint_rc_unchanged_with_hygiene(self):
        body = stream_body(owner=False).replace(
            "**Status:** pending", "**Status:** pending\n**Status:** pending", 1
        )
        rc, _out, err, _root = self._lint(body)
        self.assertEqual(rc, 1)
        self.assertIn("[Status 行]", err)
        self.assertIn("[提醒]", err)
        self.assertIn("[🟡 无 owner]", err)

    def test_brief_attaches_lint_reminder(self):
        root = make_repo(
            self.tmp,
            {"x-portfolio": portfolio_body(), "stream-a": stream_body(owner=False)},
        )
        rc, out, _err = run_main(["--root", str(root), "--brief"])
        self.assertEqual(rc, 0)
        self.assertIn("lint 提醒 1 条", out)


if __name__ == "__main__":
    unittest.main()
