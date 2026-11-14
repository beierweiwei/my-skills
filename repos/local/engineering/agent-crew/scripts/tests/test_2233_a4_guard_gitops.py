#!/usr/bin/env python3
"""2.2.33 工单 01-A/A4：guard 破坏性 git 操作观测（D7 快审 PASS 条件放行）。

BLK-5：四类操作正/负样例离线矩阵（fixture JSONL + fixture 仓）。
BLK-6：首次基线=末尾不回溯、offset>size（truncate）重置并记日志、删除文件清键。
BLK-4：S1 reflog reset 告警文本必须含「疑似（git stash push 产生同款行）」。
已知假阳性（D7 §2.3 裁定，不抑制）：echo "git reset --hard" 等引号内文本按
「疑似+原文」告警——本套件将其断言为正例（与工单原文的「负例抑制」不同，
以 D7 PASS 结论为准，回执已说明）。
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("crew_gitops", _SCRIPTS / "crew_gitops.py")
gitops = importlib.util.module_from_spec(_spec)
sys.modules["crew_gitops"] = gitops
_spec.loader.exec_module(gitops)

GUARD = _SCRIPTS / "crew-guard.py"


def assistant_line(command: str, cwd: str, tool_id: str,
                   session: str = "sess1234") -> str:
    return json.dumps({
        "type": "assistant",
        "cwd": cwd,
        "sessionId": session,
        "message": {"content": [
            {"type": "tool_use", "id": tool_id, "name": "Bash",
             "input": {"command": command}},
        ]},
    }, ensure_ascii=False)


class FakeGuard:
    """observe() 最小 guard 形状：root/crew_dir/log。"""

    def __init__(self, root: Path, crew_dir: Path) -> None:
        self.root = root
        self.crew_dir = crew_dir
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)


class MatchOpsTest(unittest.TestCase):
    """BLK-5：匹配规则正/负样例矩阵（纯函数，离线）。"""

    POSITIVE = [
        ("git -C /tmp/x reset --hard", "reset --hard"),
        ("git reset --hard HEAD~1", "reset --hard"),
        ("git   reset    --hard", "reset --hard"),
        ("git checkout -- .", "checkout --"),
        ("git checkout -- src/", "checkout --"),
        ("git clean -fd", "clean -f"),
        ("git clean -f -d", "clean -f"),
        ("git stash drop", "stash drop"),
        ("git stash drop stash@{1}", "stash drop"),
        ("git --no-pager reset --hard origin/main", "reset --hard"),
        ("git checkout main 2>/dev/null; git checkout -- .", "checkout --"),
    ]

    NEGATIVE = [
        "git checkout main",
        "git stash list",
        "git stash",
        "git status",
        "git commit -m 'feat: x'",
        "git log --oneline | grep reset",
        "git restore src/foo.py",
        "git reset --soft HEAD~1",
        "git clean -n",  # 纯 dry-run 预览（无 -f）
    ]

    def test_positive_matrix(self):
        for command, expected_op in self.POSITIVE:
            ops = gitops.match_ops(command)
            self.assertIn(expected_op, ops, f"漏检：{command}")

    def test_negative_matrix(self):
        for command in self.NEGATIVE:
            ops = gitops.match_ops(command)
            self.assertEqual([], ops, f"误报：{command} → {ops}")

    def test_quoted_text_known_false_positive(self):
        """引号内文本为已知假阳性（D7 裁定不抑制，疑似+原文兜底）。"""
        self.assertIn("reset --hard", gitops.match_ops('echo "git reset --hard"'))


class ScanTranscriptsTest(unittest.TestCase):
    """S3 内核：fixture JSONL 增量（BLK-5/BLK-6）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.root.mkdir()
        self.projects = self.base / "projects"
        slug = gitops.slug_of(self.root)
        self.session_dir = self.projects / slug
        self.session_dir.mkdir(parents=True)
        self.jsonl = self.session_dir / "s1.jsonl"
        self.jsonl.write_text("", encoding="utf-8")
        self.roots = {self.root}
        self.offsets: dict = {}
        self.seen: dict = {}

    def scan(self) -> list[dict]:
        return gitops.scan_transcripts(
            [self.jsonl], self.offsets, self.seen, self.roots,
            now=1.0, source_dir=self.projects)

    def test_first_baseline_is_end_no_backtrack(self):
        """BLK-6①：首次基线=末尾——历史破坏命令不回溯（零事件）。"""
        self.jsonl.write_text(
            assistant_line("git reset --hard", str(self.root), "t1") + "\n",
            encoding="utf-8")
        self.assertEqual([], self.scan())
        self.assertEqual(self.jsonl.stat().st_size, self.offsets[str(self.jsonl)])

    def test_increment_emits_event(self):
        self.scan()  # 基线
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(assistant_line("git reset --hard", str(self.root), "t2") + "\n")
        events = self.scan()
        self.assertEqual(1, len(events))
        self.assertEqual("reset --hard", events[0]["op"])
        self.assertEqual("sess1234", events[0]["sessionId"])

    def test_foreign_cwd_skipped(self):
        self.scan()
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(assistant_line("git reset --hard", "/tmp/other", "t3") + "\n")
        self.assertEqual([], self.scan())

    def test_user_and_non_bash_rows_skipped(self):
        self.scan()
        user_row = json.dumps({"type": "user", "cwd": str(self.root),
                               "message": {"content": [
                                   {"type": "tool_result", "id": "t4"}]}})
        read_row = assistant_line("read file", str(self.root), "t5").replace(
            '"Bash"', '"Read"')
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(user_row + "\n" + read_row + "\n")
        self.assertEqual([], self.scan())

    def test_partial_tail_line_tolerated(self):
        self.scan()
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(assistant_line("git clean -fd", str(self.root), "t6") + "\n")
            handle.write('{"type": "assistant", "cwd": ')  # 尾部半行
        events = self.scan()
        self.assertEqual(1, len(events))

    def test_tool_use_id_dedup(self):
        self.scan()
        row = assistant_line("git stash drop", str(self.root), "t7")
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(row + "\n")
        self.assertEqual(1, len(self.scan()))
        # 崩溃窗口重读同段（offset 回退）：seen 按 tool_use id 拦截
        self.offsets[str(self.jsonl)] -= len(row.encode("utf-8")) + 1
        self.assertEqual([], self.scan())

    def test_deleted_file_clears_offset_key(self):
        """BLK-6③：删除文件清键。"""
        self.scan()
        self.assertIn(str(self.jsonl), self.offsets)
        self.jsonl.unlink()
        gitops.scan_transcripts([self.jsonl], self.offsets, self.seen,
                                self.roots, now=1.0, source_dir=self.projects)
        self.assertNotIn(str(self.jsonl), self.offsets)

    def test_truncated_file_resets_offset(self):
        """BLK-6②：offset>size（truncate）重置基线不误读。"""
        self.scan()
        self.offsets[str(self.jsonl)] = 4096
        events = self.scan()
        self.assertEqual([], events)
        self.assertEqual(self.jsonl.stat().st_size, self.offsets[str(self.jsonl)])


class ScanReflogTest(unittest.TestCase):
    """S1 内核：reflog 增量 + BLK-4 stash 假阳性标注。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.reflog = Path(self._tmp.name) / "HEAD"
        self.reflog.write_text("", encoding="utf-8")
        self.roots = set()

    def test_first_baseline_is_end(self):
        self.reflog.write_text(
            "0000 reset: moving to HEAD\n", encoding="utf-8")
        events, offset, baseline = gitops.scan_reflog(
            self.reflog, None, self.roots, now=1.0)
        self.assertTrue(baseline)
        self.assertEqual([], events)
        self.assertEqual(self.reflog.stat().st_size, offset)

    def test_reset_line_emits_with_stash_hint(self):
        _, offset, _ = gitops.scan_reflog(self.reflog, 0, self.roots, now=1.0)
        self.reflog.write_text(
            f"{'x' * 40} reset: moving to HEAD\n", encoding="utf-8")
        events, new_offset, _ = gitops.scan_reflog(
            self.reflog, offset, self.roots, now=2.0)
        self.assertEqual(1, len(events))
        self.assertIn(gitops.REFLOG_STASH_HINT, events[0]["command"])

    def test_commit_lines_silent(self):
        _, offset, _ = gitops.scan_reflog(self.reflog, 0, self.roots, now=1.0)
        self.reflog.write_text(
            f"{'x' * 40} commit: init\n", encoding="utf-8")
        events, _, _ = gitops.scan_reflog(
            self.reflog, offset, self.roots, now=2.0)
        self.assertEqual([], events)

    def test_missing_file_silent(self):
        events, offset, baseline = gitops.scan_reflog(
            self.reflog.with_name("nope"), 0, self.roots, now=1.0)
        self.assertEqual([], events)
        self.assertFalse(baseline)


class ObserveIntegrationTest(unittest.TestCase):
    """端到端：fixture 仓 + 注入 projects_dir（真实 subprocess git）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        self.projects = self.base / "projects"
        slug = gitops.slug_of(self.root)
        self.session_dir = self.projects / slug
        self.session_dir.mkdir(parents=True)
        self.jsonl = self.session_dir / "s1.jsonl"
        self.jsonl.write_text("", encoding="utf-8")
        self.crew = self.base / "crew"
        self.crew.mkdir()
        self.guard = FakeGuard(self.root, self.crew)

    def observe(self) -> None:
        gitops.observe(self.guard, projects_dir=self.projects)

    def test_full_cycle_alert_dedup_and_state(self):
        self.observe()  # 基线轮：无事件、零告警
        self.assertEqual([], [m for m in self.guard.logs if "观测：" in m])
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(assistant_line("git clean -fd", str(self.root), "t1") + "\n")
        self.observe()
        alerts = [m for m in self.guard.logs if "破坏性 git 操作" in m]
        self.assertEqual(1, len(alerts), self.guard.logs)
        self.assertIn("clean -f", alerts[0])
        events = [json.loads(line) for line in
                  (self.crew / "guard.gitops.jsonl").read_text(
                      encoding="utf-8").splitlines()]
        self.assertEqual(1, len(events))
        state = json.loads((self.crew / "guard.gitops.json").read_text(
            encoding="utf-8"))
        self.assertEqual(1, len(state["alerted"]))
        # 同签名重复命令（新 tool_use id）：jsonl 记事件、零新告警（终身一次）
        with self.jsonl.open("a", encoding="utf-8") as handle:
            handle.write(assistant_line("git clean -fd", str(self.root), "t2") + "\n")
        self.observe()
        alerts = [m for m in self.guard.logs if "破坏性 git 操作" in m]
        self.assertEqual(1, len(alerts))
        events = [json.loads(line) for line in
                  (self.crew / "guard.gitops.jsonl").read_text(
                      encoding="utf-8").splitlines()]
        self.assertEqual(2, len(events))

    def test_reflog_stash_resets_alerts_with_hint(self):
        """真实 git stash 产生的 reset reflog 行 → 告警含 BLK-4 疑似标注。"""
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.email=t@t",
             "-c", "user.name=t", "commit", "-qm", "init",
             "--allow-empty"], check=True, capture_output=True)
        self.observe()  # reflog 基线
        probe = self.root / "probe.txt"
        probe.write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "probe.txt"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(self.root), "stash", "-q"],
                       check=True, capture_output=True)
        self.observe()
        alerts = [m for m in self.guard.logs if "破坏性 git 操作" in m]
        self.assertEqual(1, len(alerts), self.guard.logs)
        self.assertIn(gitops.REFLOG_STASH_HINT, alerts[0])

    def test_no_candidate_dirs_logged(self):
        empty_projects = self.base / "empty-projects"
        empty_projects.mkdir()
        gitops.observe(self.guard, projects_dir=empty_projects)
        self.assertTrue(any("无候选 session 目录" in m for m in self.guard.logs))


class GuardHookContractTest(unittest.TestCase):
    """guard 挂载契约（静态）：独立 try/except、import 静默降级、改动 ≤15 行、不 wake。"""

    def test_guard_hook_block_within_budget(self):
        text = GUARD.read_text(encoding="utf-8")
        match = re.search(
            r"# 2\.2\.33/A4/FB-063[^\n]*\n(?:\s+# [^\n]*\n)*"
            r"(        try:\n            import crew_gitops\n"
            r"            crew_gitops\.observe_once\(self\)\n"
            r"        except Exception[^\n]*\n"
            r"            self\.log\(f\"gitops 观测异常[^\n]*\n)",
            text)
        self.assertIsNotNone(match, "guard 缺 A4 挂载块")
        self.assertLessEqual(len(match.group(1).splitlines()), 8,
                             "挂载块超出预算（≤15 行含注释）")
        self.assertNotIn("_wake", match.group(1))
        self.assertNotIn("reason_parts", match.group(1))

    def test_manifest_registers_crew_gitops(self):
        init_text = (_SCRIPTS / "init.py").read_text(encoding="utf-8")
        match = re.search(r"SCRIPT_MANIFEST = \[(.*?)\]", init_text, re.DOTALL)
        names = re.findall(r'"([^"]+)"', match.group(1))
        self.assertEqual(28, len(names), f"受管清单应 28 项，实际 {len(names)}")
        self.assertIn("crew_gitops.py", names)


if __name__ == "__main__":
    unittest.main()
