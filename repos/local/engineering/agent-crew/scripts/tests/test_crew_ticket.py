#!/usr/bin/env python3
"""crew-ticket.py 用例：python3 scripts/tests/test_crew_ticket.py

覆盖 decisions §4：
① claim 冲突拒绝且列出持有者；② 同 owner 重复 claim 幂等；
③ release 幂等；④ stage 只暂存清单内文件，清单外非零退出；
⑤ precommit 输出 numstat/未提交/越界/import 提示；
⑥ close 输出 numstat + git status + 证据清单；
⑦ 两进程并发 claim 同一文件只有一个成功。
测试全部在临时 git 仓（git init）内完成，不触网、不调用 herdr、不 commit。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crew-ticket.py"


class CrewTicketTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "tester@example.com")
        self.git("config", "user.name", "Tester")

    def tearDown(self):
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            self.fail(f"git {' '.join(args)}: {proc.stderr or proc.stdout}")
        return proc.stdout

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            self.fail(f"exit {proc.returncode}: {proc.stderr or proc.stdout}")
        return proc

    def write(self, name: str, text: str = "content\n") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def receipt(self, receipt_id: str, **payload) -> Path:
        path = self.root / ".git" / "crew" / "inbox" / f"{receipt_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload.setdefault("receipt_id", receipt_id)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def occupancy(self) -> dict:
        path = self.root / ".git" / "crew" / "occupancy.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}

    def staged_names(self) -> set[str]:
        return set(self.git("diff", "--cached", "--name-only").splitlines())

    # ---- ①②③ claim / release -------------------------------------------

    def test_claim_conflict_lists_holder(self):
        self.cli("claim", "--owner", "owner-a", "src/x.py")
        rejected = self.cli(
            "claim", "--owner", "owner-b", "src/x.py", "src/y.py", check=False
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("src/x.py", rejected.stderr)
        self.assertIn("held by owner-a", rejected.stderr)
        self.assertEqual(self.occupancy(), {"src/x.py": "owner-a"})

    def test_claim_same_owner_idempotent(self):
        self.cli("claim", "--owner", "owner-a", "a.py", "b.py")
        before = self.occupancy()
        again = self.cli("claim", "--owner", "owner-a", "a.py", "b.py")
        self.assertIn("no-op", again.stdout)
        self.assertEqual(self.occupancy(), before)
        self.assertEqual(before, {"a.py": "owner-a", "b.py": "owner-a"})

    def test_release_idempotent_and_other_owner(self):
        self.cli("claim", "--owner", "owner-a", "a.py")
        self.cli("release", "--owner", "owner-a", "a.py")
        self.assertEqual(self.occupancy(), {})
        again = self.cli("release", "--owner", "owner-a", "a.py")
        self.assertIn("no-op", again.stdout)
        self.assertEqual(self.occupancy(), {})

        self.cli("claim", "--owner", "owner-a", "a.py")
        rejected = self.cli("release", "--owner", "owner-b", "a.py", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("held by owner-a", rejected.stderr)
        self.assertEqual(self.occupancy(), {"a.py": "owner-a"})

    def test_path_safety_rejects_absolute_and_parent_escape(self):
        absolute = self.cli("claim", "--owner", "owner-a", str(self.root / "a.py"), check=False)
        self.assertEqual(absolute.returncode, 2)
        escape = self.cli("claim", "--owner", "owner-a", "../outside.py", check=False)
        self.assertEqual(escape.returncode, 2)
        self.assertEqual(self.occupancy(), {})

    # ---- ④ stage ---------------------------------------------------------

    def test_stage_only_declared_files_and_rejects_outside(self):
        self.write("a.py", "a = 1\n")
        self.write("b.py", "b = 2\n")
        self.write("pkg/new.py", "c = 3\n")
        self.write("outside.txt", "nope\n")
        path = self.receipt(
            "r1",
            task="T-1",
            status="done",
            changed_files=["a.py", "b.py", "pkg/new.py"],
        )

        rejected = self.cli("stage", "--receipt", str(path), check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("out-of-scope", rejected.stderr)
        self.assertIn("outside.txt", rejected.stderr)
        self.assertEqual(self.staged_names(), set())

        (self.root / "outside.txt").unlink()
        self.cli("stage", "--task", "T-1")
        self.assertEqual(self.staged_names(), {"a.py", "b.py", "pkg/new.py"})

    def test_stage_requires_declared_scope(self):
        self.write("a.py")
        rejected = self.cli("stage", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("requires --receipt/--task/--ticket", rejected.stderr)
        self.assertEqual(self.staged_names(), set())

    def test_stage_rejects_empty_receipt_list(self):
        self.write("a.py")
        self.receipt("r1", task="T-1", status="done", changed_files=[])
        rejected = self.cli("stage", "--task", "T-1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("empty", rejected.stderr)

    # ---- ⑤ precommit -----------------------------------------------------

    def test_precommit_reports_numstat_status_scope_and_import_hints(self):
        self.write("pkg/helper.py", "value = 1\n")
        self.write("a.py", "from pkg.helper import value\n")
        self.write("other.txt", "extra\n")
        self.receipt(
            "r1",
            task="T-1",
            status="done",
            changed_files=["a.py", "pkg/helper.py"],
            evidence=["unittest"],
        )
        self.git("add", "a.py", "pkg/helper.py")
        self.write("a.py", "from pkg.helper import value\nvalue += 1\n")

        out = self.cli("precommit", "--task", "T-1").stdout
        self.assertIn("numstat staged:", out)
        self.assertIn("a.py", out)
        self.assertIn("pkg/helper.py", out)
        self.assertIn("numstat unstaged:", out)
        self.assertIn("uncommitted:", out)
        self.assertIn("?? other.txt", out)
        self.assertIn("out-of-scope:", out)
        self.assertIn("other.txt", out)
        self.assertIn("import hints:", out)
        self.assertIn("a.py -> pkg/helper.py", out)

    # ---- ⑥ close ---------------------------------------------------------

    def test_close_reports_numstat_status_and_evidence(self):
        self.write("a.py", "a = 1\n")
        self.receipt(
            "r1",
            task="T-1",
            status="done",
            changed_files=["a.py"],
            evidence=["python3 -m unittest discover -s scripts/tests -v", "artifact/report.md"],
        )
        self.git("add", "a.py")

        out = self.cli("close", "--task", "T-1", "--evidence", "manual check").stdout
        self.assertIn("numstat staged:", out)
        self.assertIn("git status:", out)
        self.assertIn("A  a.py", out)
        self.assertIn("evidence:", out)
        self.assertIn("- python3 -m unittest discover -s scripts/tests -v", out)
        self.assertIn("- artifact/report.md", out)
        self.assertIn("- manual check", out)
        self.assertNotIn("ls-remote:", out)

    # ---- ⑦ 并发 claim -----------------------------------------------------

    def test_concurrent_claim_single_winner(self):
        owners = ["owner-a", "owner-b"]
        procs = [
            subprocess.Popen(
                [
                    sys.executable, str(SCRIPT), "--root", str(self.root),
                    "claim", "--owner", owner, "shared.py",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for owner in owners
        ]
        results = [(proc,) + proc.communicate(timeout=60) for proc in procs]
        codes = [proc.returncode for proc, _, _ in results]
        self.assertEqual(codes.count(0), 1, [err for _, _, err in results])
        self.assertEqual(codes.count(2), 1, [err for _, _, err in results])

        winner = owners[codes.index(0)]
        loser_err = results[codes.index(2)][2]
        self.assertIn(f"held by {winner}", loser_err)
        self.assertEqual(self.occupancy(), {"shared.py": winner})


if __name__ == "__main__":
    unittest.main()
