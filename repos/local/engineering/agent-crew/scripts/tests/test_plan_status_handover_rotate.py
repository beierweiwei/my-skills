#!/usr/bin/env python3
"""plan-status.py 交接瘦身用例（2.2.7 工单 01 A/B）。

覆盖：
A. --handover：≤40 行、区块齐全（Goal/Next Step/活跃流/待 Captain/顶部最新 3 条 progress/
   最近 3 条完成/证据入口/看板新鲜）、只读头部、零写入、空仓降级、超量截断仍 ≤40。
B. --rotate：默认 dry-run 零写入；--apply 保留最近 N 条 + 尾部非条目区块、归档头字段、
   指针、守恒、幂等、flock 冲突 rc=2、rename 失败 live 不变且临时文件清理、N<1 rc=1。
C. --write：progress ≥40960 字节或 ≥100 条时 stderr 告警「建议交接时 --rotate」，
   低于阈值零告警、stdout/看板内容不变；阈值常量与 help 可查。
fixture 全部为中性占位内容，不含任何业务词（通用性红线：渲染后无业务词残留）。
"""

from __future__ import annotations

import contextlib
import fcntl
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_handover_rotate", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_handover_rotate"] = ps  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(ps)


# —— 夹具 ——

def make_repo(tmp: Path, plans: dict[str, str], progress: str | None = None,
              config: dict | None = None) -> Path:
    root = tmp / "repo"
    planning = root / ".planning"
    planning.mkdir(parents=True)
    for name, body in plans.items():
        d = planning / name
        d.mkdir()
        (d / "task_plan.md").write_text(body, encoding="utf-8")
    if progress is not None:
        d = planning / "portfolio"
        d.mkdir(exist_ok=True)
        (d / "progress.md").write_text(progress, encoding="utf-8")
    if config is not None:
        (planning / "orchestration.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def stream_body(phases: list[tuple[str, str]], next_step: str = "推进下一条。") -> str:
    lines = ["# 流 plan", "", "## Goal", "流目标。", "", "## Next Step", next_step, "", "## Phases", ""]
    for i, (name, status) in enumerate(phases, 1):
        lines += [f"### Phase {i}: {name}", f"**Status:** {status}", ""]
    return "\n".join(lines) + "\n"


def portfolio_body(stream_refs: list[str], captain_rows: list[str] | None = None,
                   recent: list[str] | None = None) -> str:
    lines = [
        "# 测试项目组合 plan", "", "## Goal", "组合目标：推进多条流。", "",
        "## Next Step", "推进流 A 与流 B。", "", "## 最近完成", "",
    ]
    lines += recent or []
    lines += ["", "## Phases", ""]
    for i, ref in enumerate(stream_refs, 1):
        lines += [f"### Phase {i}: 流{i}", f"- plan: `{ref}`", "**Status:** pending", ""]
    lines += ["## Backlog", "", "| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
    lines += captain_rows or []
    lines += [""]
    return "\n".join(lines) + "\n"


def progress_body(entries: list[tuple[str, str]], digest: bool = True) -> str:
    """entries=[(日期, 正文)]；正文内的换行表示缩进续行（调用方自带缩进）。"""
    lines = ["# Progress（编排台账）", "", "> 合成夹具。", "", "## 自动驾驶里程", ""]
    for day, text in entries:
        first, *rest = text.split("\n")
        lines.append(f"- {day} {first}")
        lines += rest
    if digest:
        lines += ["", "## 交接摘要", "", "> 尾部 digest 原样保留（非条目区块）。"]
    return "\n".join(lines) + "\n"


def numbered_entries(n: int, start: int = 1, extra: str = "") -> list[tuple[str, str]]:
    return [(f"2026-11-{(i % 28) + 1:02d}", f"条目 {i:03d}{extra}")
            for i in range(start, start + n)]


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """文件内容 + mtime_ns 快照（证明零写入）。"""
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)


# —— A. --handover ——

class HandoverTest(_TmpRepoTest):
    def _repo(self, progress: str | None = None) -> Path:
        return make_repo(self.tmp, {
            "portfolio": portfolio_body(
                [".planning/stream-a/task_plan.md", ".planning/stream-b/task_plan.md"],
                captain_rows=["| C01 | 待拍板事项 | P0 | — | 需 Captain 决策 |",
                              "| T01 | 普通待办 | P1 | — | — |"],
                recent=["- [x] 完成 01", "- [x] 完成 02", "- [x] 完成 03", "- [x] 完成 04"],
            ),
            "stream-a": stream_body([("阶段一", "complete"), ("阶段二", "in_progress")],
                                    next_step="继续阶段二（长摘要不截断标记-STREAM-A）。"),
            "stream-b": stream_body([("阶段一", "complete")]),
        }, progress=progress)

    def _run(self, repo: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--handover"])
        return rc, out.getvalue(), err.getvalue()

    def test_summary_blocks_complete_and_bounded(self):
        entries = numbered_entries(5)
        entries[2] = ("2026-11-03", "条目 003\n  续行三甲\n  续行三乙")
        repo = self._repo(progress_body(entries))
        rc, out, err = self._run(repo)
        self.assertEqual(rc, 0, err)
        lines = out.splitlines()
        self.assertLessEqual(len(lines), ps.HANDOVER_MAX_LINES)
        self.assertIn("## Goal", lines)
        self.assertIn("组合目标：推进多条流。", out)
        self.assertIn("继续阶段二", out)
        # 活跃流：plan 引用 + 当前阶段 + Next Step；已完成流不出现
        self.assertIn("`.planning/stream-a/task_plan.md`", out)
        self.assertIn("当前阶段：阶段二", out)
        self.assertIn("长摘要不截断标记-STREAM-A", out)
        self.assertNotIn("stream-b", out)
        # 待 Captain（C 行）与普通 T 行不混入
        self.assertIn("C01", out)
        self.assertNotIn("T01", out)
        # 最近 3 条 progress：取 `## 自动驾驶里程` 顶部最新 3 条（progress 倒序），
        # 续行并入条目行，尾部旧条目不出现
        recent = out.split("## 最近 progress", 1)[1].split("## 最近完成", 1)[0]
        self.assertIn("条目 001", recent)
        self.assertIn("条目 002", recent)
        self.assertIn("条目 003", recent)
        self.assertNotIn("条目 004", recent)
        self.assertNotIn("条目 005", recent)
        joined = next(l for l in recent.splitlines() if "条目 003" in l)
        self.assertIn("续行三甲", recent)
        self.assertIn("续行三乙", joined)  # 续行碎片不单独成行
        # 最近 3 条完成（节内前 3 条，第 4 条不出现；标题口径「最近 3 条」）
        self.assertIn("## 最近完成（最近 3 条）", out)
        done = out.split("## 最近完成", 1)[1].split("## 证据入口", 1)[0]
        self.assertIn("完成 03", done)
        self.assertNotIn("完成 04", done)
        # 证据入口 + 看板状态
        self.assertIn(".planning/portfolio/task_plan.md", out)
        self.assertIn(".planning/portfolio/progress.md", out)
        self.assertIn(".planning/portfolio/feedback.md", out)
        self.assertIn(".planning/_archive/", out)
        self.assertIn("过期", out)  # 未生成看板

    def test_handover_writes_nothing(self):
        repo = self._repo(progress_body(numbered_entries(3)))
        before = snapshot(repo)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ps.main(["--root", str(repo), "--handover"]), 0)
        self.assertEqual(snapshot(repo), before)

    def test_handover_fresh_board_state(self):
        repo = self._repo(progress_body(numbered_entries(3)))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(ps.main(["--root", str(repo), "--write"]), 0)
        before = snapshot(repo)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        self.assertIn("新鲜", out)
        self.assertEqual(snapshot(repo), before)

    def test_no_active_streams_marks_none(self):
        repo = make_repo(self.tmp, {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body([("阶段一", "complete")]),
        }, progress=progress_body(numbered_entries(2)))
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        active = out.split("## 活跃流", 1)[1].split("## 待 Captain", 1)[0]
        self.assertIn("- 无", active)

    def test_empty_repo_degrades_readably(self):
        root = self.tmp / "empty"
        (root / ".planning").mkdir(parents=True)
        rc, out, err = self._run(root)
        self.assertEqual(rc, 0, err)
        self.assertIn("无（progress 缺失）", out)
        self.assertIn("—", out)
        self.assertIn("过期", out)  # 看板不存在
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_reads_progress_head_only(self):
        entries = numbered_entries(300, extra="·" * 100)
        entries[0] = ("2026-11-01", "最早条目-只读头部标记-EARLY")
        entries[-1] = ("2026-11-28", "最新条目-LATEST")
        repo = self._repo(progress_body(entries))
        self.assertGreater((repo / ".planning/portfolio/progress.md").stat().st_size,
                           ps.PROGRESS_TAIL_BYTES)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        # 顶部条目读得到；尾部条目不在最近 3 条内
        self.assertIn("EARLY", out)
        self.assertNotIn("LATEST", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_recent_progress_window_expands_for_huge_first_entry(self):
        entries = numbered_entries(4)
        entries[0] = ("2026-11-01", "条目 001 超长" + "长" * 20000)
        repo = self._repo(progress_body(entries))
        self.assertGreater((repo / ".planning/portfolio/progress.md").stat().st_size,
                           ps.PROGRESS_TAIL_BYTES)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)
        recent = out.split("## 最近 progress", 1)[1].split("## 最近完成", 1)[0]
        for needle in ("条目 001", "条目 002", "条目 003"):
            self.assertIn(needle, recent)
        self.assertNotIn("条目 004", recent)

    def test_recent_progress_window_expands_for_cross_boundary_continuation(self):
        # S3：头部恰 3 条、第 3 条续行跨 16KB 窗口边界 → 扩容重读，续行尾部不丢
        entries = numbered_entries(2)
        entries.append(("2026-11-03", "条目 003 跨边界\n  " + "续" * 6000 + "续行尾-TAIL-MARK"))
        repo = self._repo(progress_body(entries))
        self.assertGreater((repo / ".planning/portfolio/progress.md").stat().st_size,
                           ps.PROGRESS_TAIL_BYTES)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)
        recent = out.split("## 最近 progress", 1)[1].split("## 最近完成", 1)[0]
        for needle in ("条目 001", "条目 002", "条目 003", "续行尾-TAIL-MARK"):
            self.assertIn(needle, recent)

    def test_fallback_top_entries_stop_at_heading(self):
        # 无 `## 自动驾驶里程` 节：按日期前缀全文件兜底取顶部 3 条；
        # 兜底遇下一个 `## ` 标题停止，后续 digest 日期行不计入
        progress = (
            "# Progress\n\n"
            "- 2026-11-01 兜底甲\n  续行甲\n"
            "- 2026-11-02 兜底乙\n"
            "- 2026-11-03 兜底丙\n"
            "- 2026-11-04 兜底丁\n"
            "\n## 交接 digest\n\n- 2026-12-31 DIGEST-DATE\n"
        )
        repo = self._repo(progress)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        recent = out.split("## 最近 progress", 1)[1].split("## 最近完成", 1)[0]
        for needle in ("兜底甲", "兜底乙", "兜底丙", "续行甲"):
            self.assertIn(needle, recent)
        self.assertNotIn("兜底丁", recent)
        self.assertNotIn("DIGEST-DATE", recent)

    def test_digest_date_line_in_tail_not_counted_as_progress(self):
        entries = numbered_entries(100, extra="·" * 200)
        progress = progress_body(entries, digest=False) + (
            "\n\n## 交接摘要\n\n- 2026-12-31 DIGEST-DATE\n")
        repo = self._repo(progress)
        self.assertGreater((repo / ".planning/portfolio/progress.md").stat().st_size,
                           ps.PROGRESS_TAIL_BYTES)
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        recent = out.split("## 最近 progress", 1)[1].split("## 最近完成", 1)[0]
        self.assertIn("条目 001", recent)
        self.assertNotIn("DIGEST-DATE", recent)
        self.assertNotIn("DIGEST-DATE", out)
        out_s, err_s = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out_s), contextlib.redirect_stderr(err_s):
            self.assertEqual(ps.main(["--root", str(repo), "--write"]), 0)
        self.assertIn("100 条 ≥ 100", err_s.getvalue())
        self.assertNotIn("101 条", err_s.getvalue())

    def test_overflow_capped_and_still_bounded(self):
        refs = [f".planning/s{i:02d}/task_plan.md" for i in range(12)]
        plans = {f"s{i:02d}": stream_body([("阶段一", "in_progress")]) for i in range(12)}
        plans["portfolio"] = portfolio_body(
            refs, captain_rows=[f"| C{i:02d} | 事项{i:02d} | P0 | — | 待决策 |" for i in range(12)])
        repo = make_repo(self.tmp, plans, progress=progress_body(numbered_entries(4)))
        rc, out, _err = self._run(repo)
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)
        self.assertIn("另有", out)


# —— B. --rotate ——

class RotateTest(_TmpRepoTest):
    def _repo(self, entries: list[tuple[str, str]] | None = None) -> Path:
        return make_repo(
            self.tmp,
            {"portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
             "stream-a": stream_body([("阶段一", "pending")])},
            progress=progress_body(entries or numbered_entries(6)),
        )

    def _run(self, repo: Path, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--rotate", *args])
        return rc, out.getvalue(), err.getvalue()

    def _progress(self, repo: Path) -> str:
        return (repo / ".planning/portfolio/progress.md").read_text(encoding="utf-8")

    def test_dry_run_default_zero_write(self):
        repo = self._repo()
        before = snapshot(repo)
        rc, out, err = self._run(repo, "--keep", "3")
        self.assertEqual(rc, 0, err)
        self.assertIn("条目：6 → 保留 3，移出 3", out)
        self.assertIn("行数：", out)
        self.assertIn("字节：", out)
        self.assertIn("progress-", out)
        self.assertIn("dry-run", out)
        self.assertEqual(snapshot(repo), before)
        self.assertFalse((repo / ".planning/_archive").exists())

    def test_apply_without_rotate_rejected(self):
        repo = self._repo()
        before = snapshot(repo)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--apply"])
        self.assertEqual(rc, 1)
        self.assertEqual(out.getvalue(), "")
        self.assertIn("--rotate", err.getvalue())
        self.assertEqual(snapshot(repo), before)

    def test_apply_preserves_progress_and_archive_mode(self):
        repo = self._repo()
        progress = repo / ".planning/portfolio/progress.md"
        os.chmod(progress, 0o600)
        rc, _out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertEqual(stat.S_IMODE(progress.stat().st_mode), 0o600)
        archive = next((repo / ".planning/_archive").glob("progress-*.md"))
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)

    def test_retry_after_live_rename_failure_does_not_duplicate_archive(self):
        repo = self._repo()
        progress = repo / ".planning/portfolio/progress.md"
        real_replace = os.replace

        def fake_replace(src, dst):
            if Path(dst).name == "progress.md":
                raise OSError("模拟 rename 失败")
            return real_replace(src, dst)

        with mock.patch.object(ps.os, "replace", side_effect=fake_replace):
            rc, _out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("live progress 保持不变", err)
        archive = next((repo / ".planning/_archive").glob("progress-*.md"))
        first = archive.read_text(encoding="utf-8")
        self.assertEqual(first.count("源文件归档前 sha256："), 1)

        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn("已写入", out)
        second = archive.read_text(encoding="utf-8")
        self.assertEqual(first, second)  # 同一批重试不重复追加
        self.assertEqual(second.count("源文件归档前 sha256："), 1)
        live = self._progress(repo)
        self.assertEqual(len(ps._entry_blocks(live.splitlines())[0]), 3)
        body = second.split("\n\n", 1)[1]
        self.assertEqual(len(ps._entry_blocks(body.splitlines())[0]), 3)

    def test_default_keep_is_10(self):
        repo = self._repo(numbered_entries(15))
        rc, _out, err = self._run(repo, "--apply")
        self.assertEqual(rc, 0, err)
        live = ps._entry_blocks(self._progress(repo).splitlines())[0]
        self.assertEqual(len(live), 10)

    def test_apply_keeps_recent_archives_rest_with_header_and_pointer(self):
        entries = numbered_entries(6)
        entries[1] = ("2026-11-02", "条目 002\n  续行二甲")
        repo = self._repo(entries)
        source = self._progress(repo)
        source_sha = ps.hashlib.sha256(source.encode("utf-8")).hexdigest()
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        live = self._progress(repo)
        archives = sorted((repo / ".planning/_archive").glob("progress-*.md"))
        self.assertEqual(len(archives), 1)
        archive = archives[0]
        self.assertIn("已写入", out)

        # live：保留最近 3 条 + 指针；旧条目不出现；文件头/节标题/尾部 digest 原样
        self.assertIn("条目 004", live)
        self.assertIn("条目 006", live)
        self.assertNotIn("条目 003", live)
        self.assertIn("# Progress（编排台账）", live)
        self.assertIn("## 自动驾驶里程", live)
        self.assertIn("尾部 digest 原样保留", live)
        pointer = [l for l in live.splitlines() if l.startswith("> ") and "归档" in l]
        self.assertEqual(len(pointer), 1)
        self.assertIn(archive.name, pointer[0])
        self.assertIn("3 条", pointer[0])

        # 守恒：live 条目（不含 `> ` 指针）+ 归档条目 == 轮转前条目
        self.assertEqual(len(ps._entry_blocks(live.splitlines())[0]), 3)
        archive_text = archive.read_text(encoding="utf-8")
        archive_body = archive_text.split("\n\n", 1)[1]
        self.assertEqual(len(ps._entry_blocks(archive_body.splitlines())[0]), 3)
        self.assertEqual(3 + 3, len(ps._entry_blocks(source.splitlines())[0]))

        # 归档正文逐字一致（含缩进续行）
        self.assertIn("条目 001", archive_body)
        self.assertIn("条目 002", archive_body)
        self.assertIn("  续行二甲", archive_body)

        # 归档头字段
        self.assertIn("> 来源：`.planning/portfolio/progress.md`", archive_text)
        self.assertIn("> 归档日期：", archive_text)
        self.assertIn("> 截止边界：移出源文件第", archive_text)
        self.assertIn("> 行数：", archive_text)
        self.assertIn("字节：", archive_text)
        self.assertIn(f"> 源文件归档前 sha256：{source_sha}", archive_text)
        self.assertIn("> 操作角色：", archive_text)

    def test_apply_role_from_env(self):
        repo = self._repo()
        with mock.patch.dict(os.environ, {"CREW_ROLE": "tester"}):
            rc, _out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        archive = next((repo / ".planning/_archive").glob("progress-*.md"))
        self.assertIn("> 操作角色：tester", archive.read_text(encoding="utf-8"))

    def test_second_apply_idempotent_zero_write(self):
        repo = self._repo()
        self.assertEqual(self._run(repo, "--keep", "3", "--apply")[0], 0)
        before = snapshot(repo)
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn("无需轮转", out)
        self.assertEqual(snapshot(repo), before)
        archives = list((repo / ".planning/_archive").glob("progress-*.md"))
        self.assertEqual(len(archives), 1)

    def test_keep_below_one_rejected(self):
        repo = self._repo()
        before = snapshot(repo)
        for bad in ("0", "-1"):
            rc, _out, err = self._run(repo, "--keep", bad, "--apply")
            self.assertEqual(rc, 1)
            self.assertIn("--keep 必须 ≥1", err)
        self.assertEqual(snapshot(repo), before)

    def test_lock_busy_rc2_zero_write(self):
        repo = self._repo()
        lock_path = repo / ".planning/.plan-rotate.lock"
        before = snapshot(repo)
        with open(lock_path, "a", encoding="utf-8") as holder:
            fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
            rc, _out, err = self._run(repo, "--keep", "3", "--apply")
            fcntl.flock(holder, fcntl.LOCK_UN)
        self.assertEqual(rc, 2)
        self.assertIn("被占用", err)
        self.assertEqual(
            (repo / ".planning/portfolio/progress.md").read_bytes(),
            before[".planning/portfolio/progress.md"][0])
        self.assertFalse((repo / ".planning/_archive").exists())

    def test_rename_failure_keeps_live_and_cleans_temp(self):
        repo = self._repo()
        source = self._progress(repo)
        real_replace = os.replace

        def fake_replace(src, dst):
            if Path(dst).name == "progress.md":
                raise OSError("模拟 rename 失败")
            return real_replace(src, dst)

        with mock.patch.object(ps.os, "replace", side_effect=fake_replace):
            rc, _out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 1)
        self.assertIn("live progress 保持不变", err)
        self.assertEqual(self._progress(repo), source)
        leftovers = list((repo / ".planning/portfolio").glob(".*tmp*"))
        self.assertEqual(leftovers, [])

    def test_tail_digest_bullet_not_counted_as_entry(self):
        entries = numbered_entries(12)
        progress = progress_body(entries) + "\n## 交接 digest\n\n- 尾部区块保留（非日期条目）\n"
        repo = self._repo()
        (repo / ".planning/portfolio/progress.md").write_text(progress, encoding="utf-8")
        rc, out, err = self._run(repo, "--keep", "10")
        self.assertEqual(rc, 0, err)
        self.assertIn("条目：12 → 保留 10，移出 2", out)
        rc, _out, err = self._run(repo, "--keep", "10", "--apply")
        self.assertEqual(rc, 0, err)
        live = self._progress(repo)
        self.assertEqual(len(ps._entry_blocks(live.splitlines())[0]), 10)
        self.assertIn("尾部区块保留（非日期条目）", live)
        archive = next((repo / ".planning/_archive").glob("progress-*.md"))
        archive_text = archive.read_text(encoding="utf-8")
        self.assertNotIn("尾部区块保留", archive_text)
        archive_body = archive_text.split("\n\n", 1)[1]
        self.assertEqual(len(ps._entry_blocks(archive_body.splitlines())[0]), 2)

    def test_fallback_without_section_hinted_in_dry_run(self):
        progress = "# Progress\n\n- 2026-11-01 甲\n- 2026-11-02 乙\n- 2026-11-03 丙\n"
        repo = self._repo()
        (repo / ".planning/portfolio/progress.md").write_text(progress, encoding="utf-8")
        rc, out, err = self._run(repo, "--keep", "2")
        self.assertEqual(rc, 0, err)
        self.assertIn("兜底", out)
        self.assertIn("条目：3 → 保留 2，移出 1", out)
        self.assertEqual(self._run(repo, "--keep", "2", "--apply")[0], 0)
        self.assertEqual(len(ps._entry_blocks(self._progress(repo).splitlines())[0]), 2)

    def test_missing_progress_rejected(self):
        root = make_repo(self.tmp, {"portfolio": portfolio_body([])})
        rc, _out, err = self._run(root, "--keep", "3")
        self.assertEqual(rc, 1)
        self.assertIn("progress 不存在", err)


class AtomicWriteTest(_TmpRepoTest):
    """B12 补充：os.replace 前 fsync；失败路径目标文件不变且不留 temp。"""

    def test_fsync_called_before_replace(self):
        target = self.tmp / "x.md"
        with mock.patch.object(ps.os, "fsync", wraps=os.fsync) as spy:
            ps._atomic_write(target, "内容\n")
        spy.assert_called_once()
        self.assertEqual(target.read_text(encoding="utf-8"), "内容\n")

    def test_failure_keeps_target_and_cleans_temp(self):
        target = self.tmp / "x.md"
        target.write_text("旧内容", encoding="utf-8")
        with mock.patch.object(ps.os, "replace", side_effect=OSError("模拟失败")):
            with self.assertRaises(OSError):
                ps._atomic_write(target, "新内容")
        self.assertEqual(target.read_text(encoding="utf-8"), "旧内容")
        self.assertEqual(list(self.tmp.glob(".*tmp*")), [])


# —— C. --write 体量告警 ——
class WriteWarningTest(_TmpRepoTest):
    def _repo(self, progress: str | None) -> Path:
        return make_repo(
            self.tmp,
            {"portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
             "stream-a": stream_body([("阶段一", "pending")])},
            progress=progress,
        )

    def _write(self, repo: Path) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--write"])
        return rc, out.getvalue(), err.getvalue()

    @staticmethod
    def _board_without_timestamp(repo: Path) -> str:
        text = (repo / "docs/进度追踪.md").read_text(encoding="utf-8")
        return "\n".join(l for l in text.splitlines() if "生成于" not in l)

    def test_warning_on_byte_threshold(self):
        padding = "x" * (ps.PROGRESS_WARN_BYTES + 100)
        repo = self._repo(progress_body([("2026-11-01", padding)]))
        base = make_repo(self.tmp / "base", {
            "portfolio": portfolio_body([".planning/stream-a/task_plan.md"]),
            "stream-a": stream_body([("阶段一", "pending")]),
        })  # 同内容另一仓：对照看板（无 progress → 无告警）
        rc, out, err = self._write(repo)
        self.assertEqual(rc, 0)
        self.assertIn("建议交接时 --rotate", err)
        self.assertIn(str(ps.PROGRESS_WARN_BYTES), err)
        self.assertEqual(len(out.strip().splitlines()), 1)  # stdout 只余写入行
        self.assertEqual(self._write(base)[0], 0)
        self.assertEqual(self._board_without_timestamp(repo),
                         self._board_without_timestamp(base))

    def test_warning_on_entry_threshold(self):
        repo = self._repo(progress_body(numbered_entries(ps.PROGRESS_WARN_ENTRIES)))
        self.assertLess((repo / ".planning/portfolio/progress.md").stat().st_size,
                        ps.PROGRESS_WARN_BYTES)
        rc, _out, err = self._write(repo)
        self.assertEqual(rc, 0)
        self.assertIn("建议交接时 --rotate", err)
        self.assertIn(f"{ps.PROGRESS_WARN_ENTRIES} 条", err)

    def test_no_warning_below_thresholds(self):
        repo = self._repo(progress_body(numbered_entries(ps.PROGRESS_WARN_ENTRIES - 1)))
        self.assertLess((repo / ".planning/portfolio/progress.md").stat().st_size,
                        ps.PROGRESS_WARN_BYTES)
        rc, _out, err = self._write(repo)
        self.assertEqual(rc, 0)
        self.assertEqual(err, "")

    def test_thresholds_documented(self):
        self.assertEqual(ps.PROGRESS_WARN_BYTES, 40960)
        self.assertEqual(ps.PROGRESS_WARN_ENTRIES, 100)
        self.assertIn("40960", ps.__doc__ or "")
        self.assertIn("PROGRESS_WARN_BYTES", ps.__doc__ or "")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                ps.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("--handover", "--rotate", "--keep", "--apply", "dry-run", "40960"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
