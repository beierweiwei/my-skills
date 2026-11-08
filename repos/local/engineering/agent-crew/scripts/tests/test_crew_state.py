#!/usr/bin/env python3
"""crew-state.py 用例：python3 scripts/tests/test_crew_state.py

覆盖 decisions §8：
① 每条合法任务/工位迁移可执行且 status/history 可见；
② 非法迁移拒绝且状态不变；
③ 同一幂等键重复 transition 只产生一条事件；
④ 删除派生状态后 recover/rebuild 从 events.jsonl 还原一致状态；
⑤ 并发追加日志不损坏（多进程）；
⑥ recover 处理末尾半行/损坏行并给出明确结果；
⑦ 门禁（工单/回执/占用/评审/抽验/归档）按放行条件校验。
全部用临时目录 + --root，不触网、不调用 herdr。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crew-state.py"


class CrewStateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".git").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def cli(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
        )
        if check and proc.returncode != 0:
            self.fail(f"exit {proc.returncode}: {proc.stderr or proc.stdout}")
        return proc

    def status(self) -> dict:
        return json.loads(self.cli("status", "--json").stdout)

    def log_lines(self) -> list[dict]:
        path = self.root / ".git" / "crew" / "events.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def add_ticket(self, ident: str, **overrides) -> Path:
        path = self.root / ".git" / "crew" / "tickets" / f"{ident}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"goal": "测试目标", "acceptance": ["验收断言"], "boundary": ["边界规则"]}
        payload.update(overrides)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def add_receipt(self, receipt_id: str, **payload) -> Path:
        path = self.root / ".git" / "crew" / "inbox" / f"{receipt_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def add_file(self, name: str, text: str = "evidence") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def start_slot(self, slot: str = "s1") -> None:
        self.cli("transition", "slot", slot, "working", "--revision", "r1")

    def drive_task(self, ident: str, target: str, slot: str = "s1") -> None:
        """把任务从 backlog 推到 target（target 属于合法链上的状态）。"""
        self.add_ticket(ident)
        self.cli("transition", "task", ident, "ready", "--revision", "r1")
        if target == "ready":
            return
        self.cli("transition", "task", ident, "dispatched", "--revision", "r1", "--slot", slot)
        if target == "dispatched":
            return
        self.add_receipt(f"{ident}-ack", task=ident, status="ack")
        self.cli("transition", "task", ident, "running", "--revision", "r1")
        if target == "running":
            return
        if target == "blocked":
            self.add_receipt(f"{ident}-blocked", task=ident, status="blocked")
            self.cli("transition", "task", ident, "blocked", "--revision", "r1")
            return
        self.add_receipt(
            f"{ident}-done",
            task=ident,
            status="done",
            changed_files=["a.py"],
            evidence=["unit-test"],
        )
        self.cli("transition", "task", ident, "review", "--revision", "r1")
        if target == "review":
            return
        self.add_file(f"{ident}-review.md", "reviewer verdict: PASS\n")
        self.add_file(f"{ident}-spot.md", "node|entry|result\n")
        self.cli(
            "transition", "task", ident, "accepted", "--revision", "r1",
            "--review", f"{ident}-review.md",
            "--spot-check", f"{ident}-spot.md",
            "--plan-updated",
        )
        if target == "accepted":
            return
        self.add_file(f"{ident}-archive.md")
        self.cli(
            "transition", "task", ident, "archived", "--revision", "r1",
            "--evidence-archive", f"{ident}-archive.md",
            "--plan-closed",
        )

    # ---- ① 合法迁移可执行且可见 -------------------------------------------

    def test_task_full_path_and_blocked_cycle_visible(self):
        self.start_slot()
        self.add_ticket("T-1")
        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        self.cli("transition", "task", "T-1", "dispatched", "--revision", "r1", "--slot", "s1")
        self.add_receipt("T-1-ack", task="T-1", status="ack")
        self.cli("transition", "task", "T-1", "running", "--revision", "r1")
        self.add_receipt("T-1-blocked", task="T-1", status="blocked")
        self.cli("transition", "task", "T-1", "blocked", "--revision", "r1")
        self.cli("transition", "task", "T-1", "running", "--revision", "r2")
        self.add_receipt(
            "T-1-done", task="T-1", status="done",
            changed_files=["a.py"], evidence=["unit-test"],
        )
        self.cli("transition", "task", "T-1", "review", "--revision", "r1")
        self.add_file("review.md", "reviewer verdict: PASS\n")
        self.add_file("spot.md", "node|entry|result\n")
        self.cli(
            "transition", "task", "T-1", "accepted", "--revision", "r1",
            "--review", "review.md", "--spot-check", "spot.md", "--plan-updated",
        )
        self.add_file("archive.md")
        self.cli(
            "transition", "task", "T-1", "archived", "--revision", "r1",
            "--evidence-archive", "archive.md", "--plan-closed",
        )

        state = self.status()
        self.assertEqual(state["tasks"]["T-1"]["state"], "archived")
        history = self.cli("history", "--task", "T-1").stdout.strip().splitlines()
        self.assertEqual(len(history), 8)
        self.assertIn("backlog->ready", history[0])
        self.assertIn("running->blocked", "\n".join(history))
        self.assertIn("blocked->running", "\n".join(history))
        self.assertIn("accepted->archived", history[-1])

    def test_cancel_from_every_state(self):
        self.start_slot()
        states = [
            "backlog", "ready", "dispatched", "running",
            "blocked", "review", "accepted", "archived",
        ]
        for index, state in enumerate(states):
            ident = f"T-C{index}"
            if state != "backlog":
                self.drive_task(ident, state)
            self.cli(
                "transition", "task", ident, "cancelled",
                "--revision", "cancel", "--note", "测试取消",
            )
            current = self.status()["tasks"][ident]["state"]
            self.assertEqual(current, "cancelled", state)
            last = self.log_lines()[-1]
            self.assertEqual((last["id"], last["to"]), (ident, "cancelled"))

    def test_slot_lifecycle_and_registry_cleanup(self):
        self.cli("transition", "slot", "s1", "working", "--revision", "r1")
        self.cli("transition", "slot", "s1", "blocked", "--revision", "r1")
        self.cli("transition", "slot", "s1", "working", "--revision", "r2")

        rejected = self.cli("transition", "slot", "s1", "done", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(self.status()["slots"]["s1"]["state"], "working")

        self.add_receipt(
            "slot-done", task="T-9", status="done", work_slot="s1",
            changed_files=["x.py"], evidence=["e"],
        )
        self.cli("transition", "slot", "s1", "done", "--revision", "r1")

        registry = self.root / ".git" / "crew" / "registry.json"
        registry.write_text(
            json.dumps({"slots": {"s1": {"kind": "qoder"}, "s2": {}}}), encoding="utf-8"
        )
        self.cli("transition", "slot", "s1", "closed", "--revision", "r1")

        data = json.loads(registry.read_text(encoding="utf-8"))
        self.assertEqual(list(data["slots"]), ["s2"])
        self.assertEqual(self.status()["slots"]["s1"]["state"], "closed")
        history = self.cli("history", "--slot", "s1").stdout.strip().splitlines()
        self.assertEqual(len(history), 5)

    # ---- ② 非法迁移拒绝且状态不变 -----------------------------------------

    def test_illegal_transitions_rejected_state_unchanged(self):
        self.add_ticket("T-1")
        rejected = self.cli("transition", "task", "T-1", "accepted", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(len(rejected.stderr.strip().splitlines()), 1)
        self.assertIn("illegal transition", rejected.stderr)
        self.assertEqual(self.log_lines(), [])
        self.assertNotIn("T-1", self.status()["tasks"])

        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        rejected = self.cli("transition", "task", "T-1", "running", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(self.status()["tasks"]["T-1"]["state"], "ready")

    def test_duplicate_close_is_not_reopened(self):
        self.cli("transition", "slot", "s1", "working", "--revision", "r1")
        self.add_receipt("slot-done", task="T-1", status="done", work_slot="s1")
        self.cli("transition", "slot", "s1", "done", "--revision", "r1")
        self.cli("transition", "slot", "s1", "closed", "--revision", "r1")
        rejected = self.cli("transition", "slot", "s1", "working", "--revision", "r2", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertEqual(self.status()["slots"]["s1"]["state"], "closed")

    # ---- ③ 幂等键 ---------------------------------------------------------

    def test_idempotent_transition_single_event(self):
        self.add_ticket("T-1")
        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        again = self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        self.assertIn("duplicate", again.stdout)
        self.assertEqual(len(self.log_lines()), 1)

        self.cli("transition", "slot", "s1", "working", "--revision", "r1")
        again = self.cli("transition", "slot", "s1", "working", "--revision", "r1")
        self.assertIn("duplicate", again.stdout)
        self.assertEqual(len(self.log_lines()), 2)

    # ---- ④ 从日志重建 -----------------------------------------------------

    def test_recover_rebuilds_after_derived_state_removed(self):
        self.start_slot()
        self.drive_task("T-1", "running")
        before = self.status()

        crew_dir = self.root / ".git" / "crew"
        for path in sorted(crew_dir.iterdir()):
            if path.name == "events.jsonl":
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()

        recovered = self.cli("recover")
        self.assertIn("kept", recovered.stdout)
        self.assertIn("tasks=1", recovered.stdout)
        after = self.status()
        self.assertEqual(before["tasks"], after["tasks"])
        self.assertEqual(before["slots"], after["slots"])
        derived = json.loads(self.cli("derive").stdout)
        self.assertEqual(derived["tasks"], after["tasks"])

    # ---- ⑤ 并发追加 -------------------------------------------------------

    def test_concurrent_same_key_single_event(self):
        self.add_ticket("T-1")
        cmd = [
            sys.executable, str(SCRIPT), "--root", str(self.root),
            "transition", "task", "T-1", "ready", "--revision", "r1",
        ]
        procs = [
            subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(10)
        ]
        for proc in procs:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)
        self.assertEqual(len(self.log_lines()), 1)
        self.assertEqual(self.status()["tasks"]["T-1"]["state"], "ready")

    def test_concurrent_distinct_appends_not_corrupted(self):
        identifiers = [f"T-{i}" for i in range(10)]
        for ident in identifiers:
            self.add_ticket(ident)
        procs = [
            subprocess.Popen(
                [
                    sys.executable, str(SCRIPT), "--root", str(self.root),
                    "transition", "task", ident, "ready", "--revision", "r1",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for ident in identifiers
        ]
        for proc in procs:
            _, err = proc.communicate(timeout=60)
            self.assertEqual(proc.returncode, 0, err)

        events = self.log_lines()  # 任一行损坏都会在这里抛 JSONDecodeError
        self.assertEqual(len(events), 10)
        self.assertEqual({ev["id"] for ev in events}, set(identifiers))
        self.assertEqual(self.cli("validate").returncode, 0)

    # ---- ⑥ 半行/损坏行恢复 -------------------------------------------------

    def test_recover_handles_partial_and_corrupt_lines(self):
        self.add_ticket("T-1")
        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        log = self.root / ".git" / "crew" / "events.jsonl"
        with open(log, "a", encoding="utf-8") as fh:
            fh.write("this is not json\n")
            fh.write('{"seq": 99, "kind": "task"')

        broken = self.cli("status", "--json", check=False)
        self.assertEqual(broken.returncode, 3)
        self.assertIn("corrupt", broken.stderr)
        self.assertIn("recover", broken.stderr)

        recovered = self.cli("recover")
        self.assertIn("kept 1 event", recovered.stdout)
        self.assertIn("dropped 1 corrupt line", recovered.stdout)
        self.assertIn("truncated partial trailing line", recovered.stdout)

        self.assertEqual(self.status()["tasks"]["T-1"]["state"], "ready")
        self.assertEqual(len(self.log_lines()), 1)

        again = self.cli("recover")
        self.assertIn("no repairs needed", again.stdout)
        self.assertIn("kept 1 event", again.stdout)

    # ---- ⑦ 门禁 -----------------------------------------------------------

    def test_gates_reject_and_allow(self):
        self.start_slot()

        rejected = self.cli("transition", "task", "T-1", "ready", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("ticket", rejected.stderr)

        self.add_ticket("T-1", acceptance=[])
        rejected = self.cli("transition", "task", "T-1", "ready", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("acceptance", rejected.stderr)

        self.add_ticket("T-1")
        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")

        rejected = self.cli("transition", "task", "T-1", "dispatched", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("--slot", rejected.stderr)

        occupancy = self.root / ".git" / "crew" / "occupancy.json"
        occupancy.write_text(json.dumps({"files": {"shared.py": "other"}}), encoding="utf-8")
        rejected = self.cli(
            "transition", "task", "T-1", "dispatched", "--revision", "r1",
            "--slot", "s1", "--files", "shared.py", check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("occupancy conflict", rejected.stderr)

        self.cli(
            "transition", "task", "T-1", "dispatched", "--revision", "r1",
            "--slot", "s1", "--files", "free.py",
        )
        self.cli("transition", "task", "T-1", "running", "--revision", "r1", "--herdr-working")
        rejected = self.cli("transition", "task", "T-1", "blocked", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("receipt", rejected.stderr)

        self.add_receipt("blocked-1", task="T-1", status="captain")
        self.cli("transition", "task", "T-1", "blocked", "--revision", "r1")
        self.cli("transition", "task", "T-1", "running", "--revision", "r2")

        self.add_receipt("done-1", task="T-1", status="done", changed_files=["a.py"])
        rejected = self.cli("transition", "task", "T-1", "review", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("evidence", rejected.stderr)

        self.add_receipt(
            "done-2", task="T-1", status="done",
            changed_files=["a.py"], evidence=["proof"],
        )
        self.cli("transition", "task", "T-1", "review", "--revision", "r1")

        rejected = self.cli("transition", "task", "T-1", "accepted", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("--review", rejected.stderr)

        self.add_file("review.md", "verdict: FAIL\n")
        self.add_file("spot.md", "node|entry\n")
        rejected = self.cli(
            "transition", "task", "T-1", "accepted", "--revision", "r1",
            "--review", "review.md", "--spot-check", "spot.md", "--plan-updated", check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("PASS", rejected.stderr)

        self.add_file("review.md", "verdict: PASS\n")
        rejected = self.cli(
            "transition", "task", "T-1", "accepted", "--revision", "r1",
            "--review", "review.md", "--spot-check", "spot.md", check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("--plan-updated", rejected.stderr)

        self.cli(
            "transition", "task", "T-1", "accepted", "--revision", "r1",
            "--review", "review.md", "--spot-check", "spot.md", "--plan-updated",
        )
        rejected = self.cli("transition", "task", "T-1", "archived", "--revision", "r1", check=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("--evidence-archive", rejected.stderr)

        self.add_file("archive.md")
        rejected = self.cli(
            "transition", "task", "T-1", "archived", "--revision", "r1",
            "--evidence-archive", "archive.md", check=False,
        )
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("--plan-closed", rejected.stderr)

        self.cli(
            "transition", "task", "T-1", "archived", "--revision", "r1",
            "--evidence-archive", "archive.md", "--plan-closed",
        )
        self.assertEqual(self.status()["tasks"]["T-1"]["state"], "archived")

    def test_validate_flags_semantically_illegal_event(self):
        self.add_ticket("T-1")
        self.cli("transition", "task", "T-1", "ready", "--revision", "r1")
        log = self.root / ".git" / "crew" / "events.jsonl"
        bad_event = {
            "seq": 2, "ts": "2026-11-09T00:00:00Z", "kind": "task", "id": "T-1",
            "action": "running", "from": "backlog", "to": "running",
            "key": "task:T-1:running:bad", "rev": "bad", "actor": "", "note": "",
        }
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(bad_event) + "\n")
        checked = self.cli("validate", check=False)
        self.assertEqual(checked.returncode, 3)
        self.assertIn("invalid", checked.stderr)


if __name__ == "__main__":
    unittest.main()
