"""plan-archive.py 单测（2.2.30 工单 B）：/tmp fixture，禁触真实 .planning。"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "plan-archive.py"
OWNER = "fixture-owner"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(text: str, day: str = "2026-11-14", i: int = 0) -> str:
    return f"- {day} 0{i}:0{i} **事件**：{text}"


def make_progress(entries: int, header: str = "## 自动驾驶里程") -> str:
    lines = [f"# Progress: fixture", "", header, ""]
    lines += [entry(f"条目 {n}", f"2026-11-{10 + n % 10:02d}", n) for n in range(entries)]
    lines += ["", "## 将来事项", "", "- 占位行"]
    return "\n".join(lines) + "\n"


def make_task_plan(done_ns: int = 12, undone_ns: int = 3, recent: int = 12) -> str:
    lines = ["# Task Plan: fixture", "", "## Goal", "", "- 目标", "",
             "## Next Step", ""]
    lines += [f"- [x] 已完成切片 {n}（证据 e{n}）" for n in range(done_ns)]
    lines += [f"- [ ] 未完成切片 {n}" for n in range(undone_ns)]
    lines += ["", "## 最近完成", ""]
    lines += [f"- 完成 {n}（commit abc{n}）" for n in range(recent)]
    lines += ["", "## Decisions Made", "", "| D | R |", "|---|---|",
              "| 决策 | 理由 |", ""]
    return "\n".join(lines) + "\n"


class PlanArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".planning" / "fixture").mkdir(parents=True)
        self.task_dir = self.root / ".planning" / "fixture"
        self.archive_dir = self.root / ".planning" / "_archive"

    # ---------- fixture / runner ----------

    def write_plans(self, progress: str | None = None, task_plan: str | None = None,
                    extra: dict[str, str] | None = None) -> None:
        if progress is not None:
            (self.task_dir / "progress.md").write_text(progress, encoding="utf-8")
        if task_plan is not None:
            (self.task_dir / "task_plan.md").write_text(task_plan, encoding="utf-8")
        for name, content in (extra or {}).items():
            (self.task_dir / name).write_text(content, encoding="utf-8")

    def run_cli(self, *argv: str, task: str = "fixture") -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--task", task, "--root", str(self.root), *argv],
            capture_output=True, text=True, timeout=60)

    def read(self, name: str) -> str:
        return (self.task_dir / name).read_text(encoding="utf-8")

    # ---------- 断言 2/3：默认 dry-run / apply 必须显式参数 ----------

    def test_dry_run_zero_write(self):
        self.write_plans(progress=make_progress(30), task_plan=make_task_plan())
        before = {p.name: sha256(p) for p in self.task_dir.iterdir()}
        proc = self.run_cli()
        self.assertEqual(0, proc.returncode, proc.stderr)
        after = {p.name: sha256(p) for p in self.task_dir.iterdir()}
        self.assertEqual(before, after, "dry-run 落盘")
        self.assertEqual([], list(self.archive_dir.iterdir())
                         if self.archive_dir.exists() else [], "dry-run 产生归档文件")
        self.assertIn("dry-run", proc.stdout)
        self.assertIn("将归档", proc.stdout)

    def test_apply_requires_task_and_owner(self):
        self.write_plans(progress=make_progress(30))
        proc = subprocess.run([sys.executable, str(SCRIPT), "--task", "fixture",
                               "--root", str(self.root), "--apply"],
                              capture_output=True, text=True)
        self.assertEqual(1, proc.returncode)
        self.assertIn("--owner", proc.stderr)
        proc = subprocess.run([sys.executable, str(SCRIPT), "--task", "",
                               "--root", str(self.root), "--apply", "--owner", "x"],
                              capture_output=True, text=True)
        self.assertNotEqual(0, proc.returncode)

    def test_apply_all_rejected(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), "--task", "all",
                               "--root", str(self.root), "--apply", "--owner", "x"],
                              capture_output=True, text=True)
        self.assertEqual(1, proc.returncode)
        self.assertIn("all", proc.stderr)

    # ---------- 断言 4：flock ----------

    def test_flock_conflict_rejected(self):
        import fcntl
        self.write_plans(progress=make_progress(30))
        lock = self.root / ".planning" / ".plan-archive.lock"
        lock.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock, "w")
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            proc = self.run_cli("--apply", "--owner", OWNER)
            self.assertEqual(1, proc.returncode)
            self.assertIn("锁", proc.stderr)
            self.assertIn("apply", proc.stderr)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()
        # 正常退出后锁不残留
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertFalse(lock.exists(), "锁文件残留")

    # ---------- 断言 5：守恒 / 归档头 / sha ----------

    def test_apply_conservation_and_header(self):
        self.write_plans(progress=make_progress(30), task_plan=make_task_plan())
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        # 守恒：live 行数下降，归档行数补齐
        live_prog = self.read("progress.md").splitlines()
        self.assertLess(len(live_prog), len(make_progress(30).splitlines()))
        archives = list(self.archive_dir.glob("fixture-progress-*.md"))
        self.assertEqual(1, len(archives), f"归档文件：{list(self.archive_dir.iterdir())}")
        header = archives[0].read_text(encoding="utf-8")
        for token in ("来源：", "操作者：", OWNER, "截止边界：", "before：", "sha256"):
            self.assertIn(token, header, f"归档头缺 {token}")
        # live 尾部指针
        self.assertIn("归档记录：", self.read("progress.md"))

    def test_external_modification_aborts(self):
        # TOCTOU：规划后源被外部改动 → abort，live 保持外部版本、归档不落盘
        import importlib.util
        from datetime import datetime
        self.write_plans(progress=make_progress(30))
        spec = importlib.util.spec_from_file_location("plan_archive_mod", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        path = self.task_dir / "progress.md"
        orig = mod.read_bytes
        calls = {"n": 0}

        def fake_read(p):
            if p == path:
                calls["n"] += 1
                if calls["n"] == 2:  # 第 2 次读 = 规划后复核点：模拟外部改动
                    path.write_text(make_progress(30) + "- 外部插入行\n",
                                    encoding="utf-8")
            return orig(p)

        mod.read_bytes = fake_read
        with self.assertRaises(ValueError):
            mod.process_file(self.root, "fixture", "progress.md", OWNER,
                             datetime.now(), 10, True)
        self.assertIn("- 外部插入行", path.read_text(encoding="utf-8"),
                      "abort 后 live 应保持外部改动版本（不得被脚本改写）")
        self.assertFalse(self.archive_dir.exists() and list(self.archive_dir.iterdir()),
                         "abort 时不得写归档文件")

    # ---------- 断言 6：幂等 / 同日追加分段 ----------

    def test_idempotent_and_same_day_append(self):
        self.write_plans(progress=make_progress(30))
        p1 = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, p1.returncode, p1.stderr)
        live1 = sha256(self.task_dir / "progress.md")
        # 重复执行：无可归档 rc=0 零改动
        p2 = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, p2.returncode, p2.stderr)
        self.assertIn("幂等", p2.stdout)
        self.assertEqual(live1, sha256(self.task_dir / "progress.md"))
        # 制造新超限再归档：同日文件追加分段不覆盖
        self.write_plans(progress=make_progress(30))
        p3 = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, p3.returncode, p3.stderr)
        archives = sorted(self.archive_dir.glob("fixture-progress-*.md"))
        self.assertEqual(1, len(archives), "同日多次运行须同一文件追加分段")
        text = archives[0].read_text(encoding="utf-8")
        self.assertEqual(text.count("---"), 1, f"缺追加分段：{text.count('---')}")
        self.assertGreaterEqual(text.count("## 20"), 2, "缺两段归档头")

    # ---------- 断言 7：冲突拒绝 ----------

    def test_corrupt_archive_rejected(self):
        self.write_plans(progress=make_progress(30))
        self.archive_dir.mkdir(parents=True, exist_ok=True)
        target = self.archive_dir / f"fixture-progress-{__import__('datetime').date.today():%Y%m%d}.md"
        target.write_text("# 手写归档（无 BODY 标记）\n", encoding="utf-8")
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(1, proc.returncode)
        self.assertIn("不可安全追加", proc.stderr)
        # live 未改
        self.assertEqual(make_progress(30), self.read("progress.md"))

    def test_task_plan_missing_sections_rejected(self):
        self.write_plans(task_plan="# Task Plan\n\n## Goal\n\n- 只有 Goal\n")
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(1, proc.returncode)
        self.assertIn("结构异常", proc.stderr)
        self.assertEqual("# Task Plan\n\n## Goal\n\n- 只有 Goal\n",
                         self.read("task_plan.md"), "拒绝后 live 不得改")

    def test_missing_task_dir(self):
        proc = self.run_cli("--apply", "--owner", OWNER, task="no-such")
        self.assertEqual(1, proc.returncode)
        self.assertIn("不存在", proc.stderr)

    # ---------- 断言 8：作用范围（诱饵文件零改动） ----------

    def test_decoy_files_untouched(self):
        self.write_plans(progress=make_progress(30), task_plan=make_task_plan(),
                         extra={"findings.md": "# findings\n\n- 不归档\n",
                                "decoy.txt": "诱饵内容\n"})
        decoy_sha = sha256(self.task_dir / "decoy.txt")
        findings_sha = sha256(self.task_dir / "findings.md")
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual(decoy_sha, sha256(self.task_dir / "decoy.txt"))
        self.assertEqual(findings_sha, sha256(self.task_dir / "findings.md"))
        self.assertNotIn("findings", proc.stdout)

    # ---------- 断言 9：task_plan 段级 + 大文件边沿 ----------

    def test_task_plan_next_step_and_recent_archive(self):
        self.write_plans(task_plan=make_task_plan(done_ns=12, undone_ns=3, recent=12))
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read("task_plan.md")
        # Next Step：已完成 12→保留 10，未完成 3 全留
        self.assertEqual(10, text.count("- [x]"))
        self.assertEqual(3, text.count("- [ ]"))
        # 最近完成：12→保留 10
        self.assertEqual(10, text.count("- 完成 "))
        # Decisions 不动
        self.assertIn("| 决策 | 理由 |", text)
        archives = list(self.archive_dir.glob("fixture-task_plan-*.md"))
        self.assertEqual(1, len(archives))
        body = archives[0].read_text(encoding="utf-8")
        self.assertIn("已完成切片 0", body, "最旧的被归档")
        self.assertIn("完成 0（commit", body)

    def test_progress_large_file_edge(self):
        # >40KB 边沿：40 条 × ~1.2KB 条目 ≈ 48KB
        big_entry = "字" * 1200
        lines = ["# Progress: fixture", "", "## 自动驾驶里程", ""]
        lines += [entry(f"{n} {big_entry}") for n in range(40)]
        self.write_plans(progress="\n".join(lines) + "\n")
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read("progress.md")
        self.assertLess(len(text.encode("utf-8")), 40960, "归档后须 <40KB")
        archives = list(self.archive_dir.glob("fixture-progress-*.md"))
        self.assertTrue(archives)
        arch = archives[0].read_text(encoding="utf-8")
        # 守恒：live 条目 + 归档条目 == 40
        live_entries = sum(1 for ln in text.splitlines() if ln.startswith("- 2026-"))
        arch_entries = sum(1 for ln in arch.splitlines() if ln.startswith("- 2026-"))
        self.assertEqual(40, live_entries + arch_entries, "条目对账失败")

    def test_progress_date_sections_form_b(self):
        # 旧格式：## YYYY-MM-DD 日期段；总量 >40KB 触发整段迁移（一轮一段），
        # 保护最近 2 日 + 交接段
        big = "字" * 900  # ~2.7KB/条 UTF-8
        lines = ["# Progress: old-form", ""]
        for day in ("2026-11-01", "2026-11-02", "2026-11-12", "2026-11-13"):
            lines += [f"## {day}", ""]
            lines += [entry(f"{big} {n}", day, n) for n in range(6)]
            lines += [""]
        lines += ["## 交接（待重建）", "", "- 交接内容", ""]
        self.write_plans(progress="\n".join(lines) + "\n")
        self.assertGreaterEqual(
            len("\n".join(lines).encode("utf-8")), 40960, "夹具须超 40KB")
        proc = self.run_cli("--apply", "--owner", OWNER)
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read("progress.md")
        self.assertIn("## 2026-11-12", text, "最近 2 日须保留")
        self.assertIn("## 2026-11-13", text)
        self.assertIn("## 交接", text, "交接段须保留")
        self.assertNotIn("## 2026-11-01", text, "最旧日期段须归档")
        archives = list(self.archive_dir.glob("fixture-progress-*.md"))
        body = archives[0].read_text(encoding="utf-8")
        self.assertIn("## 2026-11-01", body)

    def test_keep_flag(self):
        self.write_plans(progress=make_progress(15))
        proc = self.run_cli("--apply", "--owner", OWNER, "--keep", "5")
        self.assertEqual(0, proc.returncode, proc.stderr)
        text = self.read("progress.md")
        self.assertEqual(5, sum(1 for ln in text.splitlines() if ln.startswith("- 2026-")))


if __name__ == "__main__":
    unittest.main()
