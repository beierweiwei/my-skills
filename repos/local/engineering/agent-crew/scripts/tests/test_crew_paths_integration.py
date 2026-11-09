#!/usr/bin/env python3
"""crew_paths 集成：state/ticket/guard/msg 在 linked worktree 共享 <common>/crew。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent


def run(script: Path, *args: str, cwd: Path | None = None,
        check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, str(script), *args], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise AssertionError(f"{script.name} rc={proc.returncode}: {proc.stderr or proc.stdout}")
    return proc


class CrewPathsIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.main = self.base / "main"
        self.main.mkdir()
        subprocess.run(["git", "init", "-q", str(self.main)], check=True, capture_output=True)
        proc = subprocess.run(
            [sys.executable, str(SKILL / "scripts/init.py"), "--root", str(self.main), "--yes"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        plan = self.main / ".planning/flow/task_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# Plan\n\n## Phases\n\n### Phase 1: x\n- [ ] todoA\n- **Status:** in_progress\n",
            encoding="utf-8")
        subprocess.run(["git", "-C", str(self.main), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(
            ["git", "-C", str(self.main), "-c", "user.email=a@b", "-c", "user.name=t",
             "commit", "-qm", "init"], check=True, capture_output=True)
        self.worktree = self.base / "wt"
        subprocess.run(["git", "-C", str(self.main), "worktree", "add", "-q",
                        str(self.worktree), "-b", "wtb"], check=True, capture_output=True)
        self.common = Path(subprocess.run(
            ["git", "-C", str(self.worktree), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True,
            check=True).stdout.strip())
        self.crew = self.common / "crew"

    def test_worktree_state_msg_ticket_share_common_crew(self):
        run(self.worktree / "scripts/crew-state.py", "--root", str(self.worktree), "status")
        run(self.worktree / "scripts/crew-state.py", "--root", str(self.worktree),
            "transition", "slot", "s1", "working", "--revision", "r1")
        run(self.worktree / "scripts/crew-msg.py", "write", "--from", "builder-1",
            "--runtime", "qoder", "--task", "T1", "--status", "done",
            "--summary", "完成", cwd=self.worktree)
        run(self.worktree / "scripts/crew-ticket.py", "--root", str(self.worktree),
            "close", "--task", "T1")
        self.assertTrue((self.crew / "events.jsonl").is_file())
        self.assertTrue((self.crew / "inbox").is_dir())
        self.assertFalse((self.worktree / ".git/crew").exists())

    def test_guard_once_json_reads_worktree_plan(self):
        proc = run(self.worktree / "scripts/crew-guard.py", "--root", str(self.worktree),
                   "--once", "--json")
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["crewDir"], str(self.crew))
        self.assertEqual(payload["planPath"], ".planning/flow/task_plan.md")
        self.assertGreaterEqual(payload["pending"], 1)
        self.assertIn("wake", payload)
        self.assertFalse((self.worktree / ".git/crew").exists())

    def test_non_git_falls_back_to_dot_crew(self):
        plain = self.base / "plain"
        plain.mkdir()
        run(SKILL / "scripts/crew-state.py", "--root", str(plain), "status")
        self.assertTrue((plain / ".crew").is_dir())
        self.assertFalse((plain / ".git").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
