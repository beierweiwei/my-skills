#!/usr/bin/env python3
"""plan-status.py --rotate 按条目时间戳排序用例（2.2.32 工单 A / T77 / FB-058）。

覆盖三夹具：
① 条目倒序置顶（最新在顶、旧在下）——FB-058 现场：旧实现按块序切分把最新 N 条
   归档、源文件反留最旧；修复后源文件保留集恰为最新 N 条，其余归档。
② 同日多条与异日混排——同日条目按 HH:MM 分先后；无时刻按 00:00 参与排序；
   同日同时刻稳定 tie-break（文件内相对顺序，确定性）；保留集在源文件不重排。
③ 标题/空行/段头混排——`## 自动驾驶里程` 节前后有其它节/空行/非条目行，
   只归档节内条目块，其余字节原样，指针行恰一行。
断言口径：dry-run/apply 摘要 stdout 机读（保留/归档首末时间戳），archive 头与摘要一致；
守恒（live + archive == 轮转前条目数）；二次运行幂等。
fixture 全部为中性占位内容，不含业务词（通用性红线）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_rotate_order", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_rotate_order"] = ps
_spec.loader.exec_module(ps)


# —— 夹具 ——


def make_repo(tmp: Path, progress: str) -> Path:
    root = tmp / "repo"
    planning = root / ".planning"
    (planning / "portfolio").mkdir(parents=True)
    (planning / "portfolio" / "progress.md").write_text(progress, encoding="utf-8")
    return root


def progress_body(entries: list[str], section: str = "## 自动驾驶里程") -> str:
    """entries 为完整条目文本（首行 `- YYYY-MM-DD ...`，续行自带缩进）。"""
    return (f"# Progress（编排台账）\n\n> 合成夹具。\n\n{section}\n\n"
            + "\n".join(entries) + "\n")


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        p.relative_to(root).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
        for p in sorted(root.rglob("*")) if p.is_file()
    }


class _TmpRepoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, repo: Path, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = ps.main(["--root", str(repo), "--rotate", *args])
        return rc, out.getvalue(), err.getvalue()

    def _progress(self, repo: Path) -> str:
        return (repo / ".planning/portfolio/progress.md").read_text(encoding="utf-8")

    def _archive(self, repo: Path) -> Path:
        return next((repo / ".planning/_archive").glob("progress-*.md"))

    @staticmethod
    def _entry_count(text: str) -> int:
        return len(ps._entry_blocks(text.splitlines())[0])


# —— 夹具①：条目倒序置顶（最新在顶）—— FB-058 现场 ——

REVERSE_ENTRIES = [
    "- 2026-11-14 22:10 倒序条目A-最新",       # 时间最新，文件顶部
    "- 2026-11-14 08:05 倒序条目B",
    "- 2026-11-13 21:45 倒序条目C",
    "- 2026-11-13 09:30 倒序条目D",
    "- 2026-11-12 18:00 倒序条目E\n  续行E甲",
    "- 2026-11-12 08:00 倒序条目F",
    "- 2026-11-11 23:00 倒序条目G",
    "- 2026-11-11 07:30 倒序条目H-最旧",       # 时间最旧，文件底部
]


class RotateReverseOrderTest(_TmpRepoTest):
    """倒序置顶：源文件必须保留最新 N 条（顶部块），归档最旧（底部块）；
    旧实现按块序切分恰将其弄反（FB-058）。"""

    def _repo(self) -> Path:
        return make_repo(self.tmp, progress_body(REVERSE_ENTRIES))

    def test_apply_keeps_newest_in_source_and_archives_oldest(self):
        repo = self._repo()
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        live = self._progress(repo)
        # 源文件保留集恰为最新 3 条（A/B/C）；最旧 5 条（D–H）不出现
        for kept in ("倒序条目A-最新", "倒序条目B", "倒序条目C"):
            self.assertIn(kept, live)
        for removed in ("倒序条目D", "倒序条目E", "续行E甲", "倒序条目F",
                        "倒序条目G", "倒序条目H-最旧"):
            self.assertNotIn(removed, live)
        # 非条目字节原样；指针恰一行
        self.assertIn("# Progress（编排台账）", live)
        self.assertIn("## 自动驾驶里程", live)
        pointer = [l for l in live.splitlines() if l.startswith("> ") and "归档" in l]
        self.assertEqual(len(pointer), 1)
        self.assertIn("5 条", pointer[0])
        # 守恒：live 3 + archive 5 == 8
        self.assertEqual(self._entry_count(live), 3)
        archive_text = self._archive(repo).read_text(encoding="utf-8")
        archive_body = archive_text.split("\n\n", 1)[1]
        self.assertEqual(self._entry_count(archive_body), 5)
        self.assertEqual(3 + 5, self._entry_count(progress_body(REVERSE_ENTRIES)))
        for removed in ("倒序条目D", "倒序条目E", "续行E甲", "倒序条目F",
                        "倒序条目G", "倒序条目H-最旧"):
            self.assertIn(removed, archive_body)
        for kept in ("倒序条目A-最新", "倒序条目B", "倒序条目C"):
            self.assertNotIn(kept, archive_body)

    def test_summary_spans_match_archive_header(self):
        repo = self._repo()
        rc, out, err = self._run(repo, "--keep", "3")  # dry-run
        self.assertEqual(rc, 0, err)
        self.assertIn("条目：8 → 保留 3，移出 5", out)
        self.assertIn("保留集时间戳：2026-11-13 21:45 → 2026-11-14 22:10", out)
        self.assertIn("归档集时间戳：2026-11-11 07:30 → 2026-11-13 09:30", out)
        self.assertIn("行数：", out)
        self.assertIn("字节：", out)
        self.assertIn("progress-", out)
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        archive_text = self._archive(repo).read_text(encoding="utf-8")
        # archive 头与摘要一致：边界=归档集首末；「保留最近 N 条」与实际保留集一致
        self.assertIn("截止边界：归档 5 条（2026-11-11 07:30 → 2026-11-13 09:30）", archive_text)
        self.assertIn("保留最近 3 条（2026-11-13 21:45 → 2026-11-14 22:10）", archive_text)

    def test_second_apply_idempotent_zero_write(self):
        repo = self._repo()
        self.assertEqual(self._run(repo, "--keep", "3", "--apply")[0], 0)
        before = snapshot(repo)
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn("无需轮转", out)
        self.assertEqual(snapshot(repo), before)


# —— 夹具②：同日多条与异日混排（tie-break / 不重排 / 无时刻）——

SAMEDAY_ENTRIES = [
    "- 2026-11-13 09:30 同日早",          # 文件 1
    "- 2026-11-14 08:00 异日甲",          # 文件 2
    "- 2026-11-13 21:45 同日晚",          # 文件 3
    "- 2026-11-12 23:59 最旧",            # 文件 4
    "- 2026-11-13 09:30 同日同时刻乙",     # 文件 5（与文件 1 同日同时刻）
    "- 2026-11-13 同日无时刻",            # 文件 6（无 HH:MM，按 00:00 参与排序）
]


class RotateSameDayTieBreakTest(_TmpRepoTest):
    """时间排序：无时刻按 00:00；同日同时刻按文件相对顺序 tie-break（先出现者视为
    更旧）；保留集在源文件保持原有相对位置（不按时间重排）。"""

    def _repo(self) -> Path:
        return make_repo(self.tmp, progress_body(SAMEDAY_ENTRIES))

    def test_keep3_archives_oldest_by_timestamp_not_block_order(self):
        # 时间序（旧→新）：11-12 23:59 < 11-13(无时刻=00:00) < 11-13 09:30(文件1)
        #                < 11-13 09:30(文件5，tie-break) < 11-13 21:45 < 11-14 08:00
        # keep=3 → 归档最旧 3 条：最旧 / 同日无时刻 / 同日早（文件1）；
        #          保留：同日同时刻乙（文件5，tie-break 后进保留集）/ 同日晚 / 异日甲
        repo = self._repo()
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        live = self._progress(repo)
        self.assertIn("同日同时刻乙", live)
        self.assertIn("同日晚", live)
        self.assertIn("异日甲", live)
        for removed in ("同日早", "同日无时刻", "最旧"):
            self.assertNotIn(removed, live)
        archive_text = self._archive(repo).read_text(encoding="utf-8")
        self.assertIn("截止边界：归档 3 条（2026-11-12 23:59 → 2026-11-13 09:30）", archive_text)
        self.assertIn("保留最近 3 条（2026-11-13 09:30 → 2026-11-14 08:00）", archive_text)

    def test_kept_entries_keep_original_relative_order(self):
        # keep=2 → 保留 同日晚(文件3) / 异日甲(文件2)；两者在 live 中的相对顺序
        # 与原文件一致（异日甲在前），源文件不按时间重排
        repo = self._repo()
        rc, _out, err = self._run(repo, "--keep", "2", "--apply")
        self.assertEqual(rc, 0, err)
        live = self._progress(repo)
        pos_a, pos_b = live.index("异日甲"), live.index("同日晚")
        self.assertLess(pos_a, pos_b)
        self.assertEqual(self._entry_count(live), 2)

    def test_second_apply_idempotent_zero_write(self):
        repo = self._repo()
        self.assertEqual(self._run(repo, "--keep", "3", "--apply")[0], 0)
        before = snapshot(repo)
        rc, out, err = self._run(repo, "--keep", "3", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn("无需轮转", out)
        self.assertEqual(snapshot(repo), before)


# —— 夹具③：标题/空行/段头混排 ——


MIXED_PROGRESS = (
    "# Progress（编排台账）\n"
    "\n"
    "> 合成夹具。\n"
    "\n"
    "## 近三轮\n"
    "\n"
    "1. 2026-11-14 甲轮次描述（非条目行，原样保留）\n"
    "\n"
    "## 自动驾驶里程\n"
    "\n"
    "- 2026-11-13 08:00 条目一-最旧\n"
    "  续行一甲\n"
    "\n"
    "- 2026-11-14 09:00 条目二\n"
    "中间非条目说明行（顶格，原样保留）\n"
    "- 2026-11-14 10:30 条目三-最新\n"
    "\n"
    "## 交接摘要\n"
    "\n"
    "- 2026-12-31 DIGEST 日期行（节外不计条目）\n"
    "\n"
    "> 尾部落款原样保留\n"
)


class RotateMixedStructureTest(_TmpRepoTest):
    """只归档 `## 自动驾驶里程` 节内条目块；节前/节间/节后非条目字节原样；
    节外日期样 digest 行不计入、不归档；指针行恰一行。"""

    def _repo(self) -> Path:
        return make_repo(self.tmp, MIXED_PROGRESS)

    def test_only_entry_blocks_archived_other_bytes_kept(self):
        repo = self._repo()
        rc, out, err = self._run(repo, "--keep", "2", "--apply")
        self.assertEqual(rc, 0, err)
        self.assertIn("条目：3 → 保留 2，移出 1", out)
        live = self._progress(repo)
        # 归档的只有条目一（含续行）
        self.assertNotIn("条目一-最旧", live)
        self.assertNotIn("续行一甲", live)
        self.assertIn("条目二", live)
        self.assertIn("条目三-最新", live)
        # 其余字节原样：节前其它节、节间说明行、节后 digest 节与落款
        for original in ("## 近三轮", "甲轮次描述（非条目行，原样保留）",
                         "中间非条目说明行（顶格，原样保留）",
                         "## 交接摘要", "- 2026-12-31 DIGEST 日期行（节外不计条目）",
                         "> 尾部落款原样保留"):
            self.assertIn(original, live)
        pointer = [l for l in live.splitlines() if l.startswith("> ") and "归档" in l]
        self.assertEqual(len(pointer), 1)
        # archive 只含条目一；digest 行与说明行不入 archive
        archive_text = self._archive(repo).read_text(encoding="utf-8")
        self.assertIn("条目一-最旧", archive_text)
        self.assertIn("续行一甲", archive_text)
        self.assertNotIn("DIGEST", archive_text)
        self.assertNotIn("中间非条目说明行", archive_text)
        archive_body = archive_text.split("\n\n", 1)[1]
        self.assertEqual(self._entry_count(archive_body), 1)
        self.assertEqual(self._entry_count(live), 2)

    def test_boundary_and_keep_span_in_archive_header(self):
        repo = self._repo()
        rc, _out, err = self._run(repo, "--keep", "2", "--apply")
        self.assertEqual(rc, 0, err)
        archive_text = self._archive(repo).read_text(encoding="utf-8")
        self.assertIn("截止边界：归档 1 条（2026-11-13 08:00 → 2026-11-13 08:00）",
                      archive_text)
        self.assertIn("保留最近 2 条（2026-11-14 09:00 → 2026-11-14 10:30）",
                      archive_text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
