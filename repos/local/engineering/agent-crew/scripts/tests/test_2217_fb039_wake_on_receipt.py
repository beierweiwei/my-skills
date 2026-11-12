#!/usr/bin/env python3
"""2.2.17 / FB-039：回执到达即唤醒（同项目默认直推）+ S3 游标历史时间戳。

覆盖：
① 同项目 + 非聚焦 → `herdr agent prompt` 注入，delivery=herdr；
② 同项目 + 聚焦 → 不注入，delivery=inbox_fallback（T37 守卫保留）；
③ 同项目 + 聚焦无法判定（pane list 失败/缺 focused 字段）→ inbox_fallback；
④ 跨项目 → notification 且未调用 prompt（T28 语义不破）；
⑤ 目标不可达 → notification（ok=true，与显式 qoder 的 A-1 失败语义区分）；
⑥ 显式 `--target-runtime herdr` → 保持 notification（尊重显式选择）；
⑦ `--via sendmessage` → DELEGATE 不变，不注入；
⑧ S3：`ls --mark-read` 只补新增 id 的标记时间，既有 id 历史时间戳不变。

运行：python3 scripts/tests/test_2217_fb039_wake_on_receipt.py -v
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_cm_spec = importlib.util.spec_from_file_location(
    "crew_msg_2217", SKILL_DIR / "scripts" / "crew-msg.py")
cm = importlib.util.module_from_spec(_cm_spec)
sys.modules["crew_msg_2217"] = cm
_cm_spec.loader.exec_module(cm)

TARGET = "patch-2217-firstmate"
OLD_TS = "2026-01-01T00:00:00Z"


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="patch-2217-fb039-builder",
        runtime="qoder",
        task="patch-2217/FB-039",
        status="done",
        summary="回执到达即唤醒",
        commit="",
        changed_files=["scripts/crew-msg.py"],
        evidence=["unit"],
        work_slot="builder·FB-039",
        findings=[],
        receipt_id="r-fb039-1",
        ts="2026-11-12T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class FakeHerdr:
    """按 argv 模拟 herdr agent list / pane list / agent prompt / notification 与 git。"""

    def __init__(self, agents: list[dict], panes: list[dict] | None = None,
                 prompt_rc: int = 0, pane_mode: str = "ok",
                 agent_mode: str = "ok", notify_rc: int = 0) -> None:
        self.agents = agents
        self.panes = panes if panes is not None else []
        self.prompt_rc = prompt_rc
        self.pane_mode = pane_mode
        self.agent_mode = agent_mode
        self.notify_rc = notify_rc
        self.calls: list[list[str]] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            if self.agent_mode == "down":
                return mock.Mock(returncode=1, stderr="agent list down", stdout="")
            return mock.Mock(returncode=0, stderr="", stdout=json.dumps(
                {"result": {"agents": self.agents}}))
        if argv[:3] == ["herdr", "pane", "list"]:
            if self.pane_mode == "down":
                return mock.Mock(returncode=1, stderr="pane list down", stdout="")
            if self.pane_mode == "bad-json":
                return mock.Mock(returncode=0, stderr="", stdout="not-json")
            return mock.Mock(returncode=0, stderr="", stdout=json.dumps(
                {"result": {"panes": self.panes}}))
        if argv[:2] == ["git", "-C"]:
            return mock.Mock(returncode=0, stdout=".git\n", stderr="")
        if argv[:3] == ["herdr", "notification", "show"]:
            return mock.Mock(returncode=self.notify_rc, stdout="", stderr="")
        return mock.Mock(returncode=self.prompt_rc, stdout="",
                         stderr="prompt failed" if self.prompt_rc else "")

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls
                if call[:3] == ["herdr", "agent", "prompt"]]


class WakeOnReceiptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.repo = self.base / "proj"
        self.other = self.base / "other-proj"
        for root in (self.repo, self.other):
            (root / ".git").mkdir(parents=True)
        self.crew = self.repo / ".git" / "crew"
        self.notify_calls: list[tuple] = []

    def run_write(self, fake: FakeHerdr, *, receipt_id: str = "r-fb039-1",
                  to: str = TARGET, target_runtime: str = "auto",
                  via: str = "auto", runtime: str = "qoder"):
        receipt = make_receipt(receipt_id=receipt_id, runtime=runtime)
        with mock.patch.object(cm.subprocess, "run", side_effect=fake), \
                mock.patch.object(
                    cm, "notify",
                    side_effect=lambda *a, **k: (
                        self.notify_calls.append(a), True)[1]):
            result = cm.write_receipt(
                self.crew, receipt, to=to, target_runtime=target_runtime, via=via)
        data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        return result, data

    def target_agent(self, cwd: Path | None = None) -> dict:
        return {"name": TARGET, "cwd": str(cwd or self.repo), "pane_id": "wQ:p1"}

    def test_same_project_unfocused_injects_prompt(self):
        fake = FakeHerdr(
            [self.target_agent()],
            [{"pane_id": "wQ:p1", "focused": False}])
        result, data = self.run_write(fake)

        push = result["push"]
        self.assertEqual("herdr", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertEqual("same", push["peer_inbox"]["status"])
        calls = fake.prompt_calls()
        self.assertEqual(1, len(calls))
        self.assertEqual(calls[0][:4], ["herdr", "agent", "prompt", TARGET])
        self.assertIn("r-fb039-1", calls[0][4])
        self.assertEqual("herdr", data["delivery"])

    def test_same_project_focused_skips_injection(self):
        fake = FakeHerdr(
            [self.target_agent()],
            [{"pane_id": "wQ:p1", "focused": True}])
        result, data = self.run_write(fake)

        push = result["push"]
        self.assertEqual("inbox_fallback", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertTrue(push["focused"])
        self.assertIn("聚焦", push["note"])
        self.assertEqual([], fake.prompt_calls())
        self.assertEqual("inbox_fallback", data["delivery"])
        self.assertEqual(1, len(self.notify_calls))

    def test_same_project_unknown_focus_is_fail_safe(self):
        cases = {
            "pane-list-down": FakeHerdr([self.target_agent()], pane_mode="down"),
            "focused-field-missing": FakeHerdr(
                [self.target_agent()], [{"pane_id": "wQ:p1"}]),
        }
        for label, fake in cases.items():
            with self.subTest(case=label):
                result, data = self.run_write(fake, receipt_id=f"r-{label}")
                push = result["push"]
                self.assertEqual("inbox_fallback", push["delivery"])
                self.assertTrue(push["ok"])
                self.assertIsNone(push["focused"])
                self.assertIn("fail-safe", push["note"])
                self.assertEqual([], fake.prompt_calls())
                self.assertEqual("inbox_fallback", data["delivery"])

    def test_cross_project_keeps_notification_without_prompt(self):
        fake = FakeHerdr(
            [self.target_agent(cwd=self.other)],
            [{"pane_id": "wQ:p1", "focused": False}])
        result, data = self.run_write(fake)

        push = result["push"]
        self.assertEqual("notification", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertEqual("written", push["peer_inbox"]["status"])
        self.assertIn("未注入", push["note"])
        self.assertEqual([], fake.prompt_calls())
        self.assertEqual("notification", data["delivery"])
        # T28：回执副本落收件方 inbox
        peer_files = sorted(
            (self.other / ".git" / "crew" / "inbox").glob("*.json"))
        self.assertEqual(1, len(peer_files))

    def test_unreachable_target_keeps_notification_ok(self):
        fake = FakeHerdr([])
        result, data = self.run_write(fake)

        push = result["push"]
        self.assertEqual("notification", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertIn("未注入", push["note"])
        self.assertIn("不在本机 herdr agent list", push["note"])
        self.assertEqual([], fake.prompt_calls())
        self.assertEqual("notification", data["delivery"])

    def test_explicit_herdr_target_runtime_keeps_notification(self):
        fake = FakeHerdr(
            [self.target_agent()],
            [{"pane_id": "wQ:p1", "focused": False}])
        result, data = self.run_write(fake, target_runtime="herdr")

        push = result["push"]
        self.assertEqual("notification", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertEqual([], fake.prompt_calls())
        self.assertEqual("notification", data["delivery"])

    def test_auto_same_project_sendmessage_still_delegates(self):
        fake = FakeHerdr(
            [self.target_agent()],
            [{"pane_id": "wQ:p1", "focused": False}])
        result, data = self.run_write(fake, via="sendmessage")

        push = result["push"]
        self.assertEqual("delegate", push["delivery"])
        self.assertTrue(push["ok"])
        self.assertIn("需接收方人工确认", push["warning"])
        self.assertEqual([], fake.prompt_calls())
        self.assertEqual("sendmessage-delegate", data["delivery"])

    def test_cli_write_default_auto_wakes_same_project_target(self):
        """CLI 不传 --target-runtime（默认 auto）也直推同项目 herdr 目标。"""
        fake = FakeHerdr(
            [self.target_agent()],
            [{"pane_id": "wQ:p1", "focused": False}])
        argv = [
            "write", "--crew-dir", str(self.crew), "--json",
            "--from", "patch-2217-fb039-builder", "--runtime", "qoder",
            "--task", "patch-2217/FB-039", "--status", "done",
            "--summary", "回执到达即唤醒", "--receipt-id", "r-cli-auto",
            "--to", TARGET,
        ]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cm.subprocess, "run", side_effect=fake), \
                mock.patch.object(cm, "notify", return_value=True), \
                redirect_stdout(out), redirect_stderr(err):
            code = cm.main(argv)

        self.assertEqual(0, code, err.getvalue())
        push = json.loads(out.getvalue())["push"]
        self.assertEqual("herdr", push["delivery"])
        self.assertEqual(1, len(fake.prompt_calls()))


class CursorTimestampTest(unittest.TestCase):
    """S3：mark-read 重写游标不得刷新既有 id 的历史标记时间。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"
        self.cursor_file = self.crew / "inbox-read-cursor.json"

    def write_receipts(self, *receipt_ids: str) -> None:
        for index, receipt_id in enumerate(receipt_ids):
            cm.write_receipt(
                self.crew,
                make_receipt(receipt_id=receipt_id,
                             ts=f"2026-11-12T00:00:{index:02d}Z"),
                push=False,
            )

    def ls(self, *extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cm.main(["ls", "--crew-dir", str(self.crew), *extra])
        return code, out.getvalue(), err.getvalue()

    def cursor_read(self) -> dict:
        return json.loads(self.cursor_file.read_text(encoding="utf-8"))["read"]

    def test_mark_read_only_appends_new_ids_timestamps(self):
        self.write_receipts("r-1", "r-2")
        self.cursor_file.write_text(json.dumps(
            {"version": 1, "read": {"r-1": OLD_TS}}), encoding="utf-8")

        code, out, err = self.ls("--mark-read", "r-2", "--json")
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(["r-2"], payload["marked"])
        self.assertEqual([], payload["already_read"])

        read = self.cursor_read()
        self.assertEqual(OLD_TS, read["r-1"], "既有 id 的标记时间被刷新")
        self.assertIn("r-2", read)
        new_ts = read["r-2"]

        # 再标记已读 id：不得改动任何既有时间戳
        code, _out, err = self.ls("--mark-read", "r-1", "--json")
        self.assertEqual(0, code, err)
        read_again = self.cursor_read()
        self.assertEqual(OLD_TS, read_again["r-1"])
        self.assertEqual(new_ts, read_again["r-2"])

    def test_mark_all_preserves_existing_and_adds_rest(self):
        self.write_receipts("r-1", "r-2", "r-3")
        self.cursor_file.write_text(json.dumps(
            {"version": 1, "read": {"r-2": OLD_TS}}), encoding="utf-8")

        code, out, err = self.ls("--mark-read", "--all", "--json")
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual(["r-1", "r-3"], payload["marked"])
        self.assertEqual(["r-2"], payload["already_read"])

        read = self.cursor_read()
        self.assertEqual(OLD_TS, read["r-2"])
        self.assertEqual({"r-1", "r-2", "r-3"}, set(read))


if __name__ == "__main__":
    unittest.main(verbosity=2)
