#!/usr/bin/env python3
"""2.2.15 / T28：跨项目消息落收件方 inbox + 换舵 digest 未处理通知。

覆盖：
① dispatch notify --agent / --workspace+--role：解析目标 cwd/git common dir，
   通知原子写入收件方 `<git-common-dir>/crew/inbox/`，字段可追溯；
② 同内容重复发送按 notification_id 幂等去重；
③ 解析失败（目标不在本机/非 git/无 cwd）降级 pane prompt，输出与日志显式
   「未落收件方 inbox」；
④ `notify --ack` 把通知标记 processed（未处理口径终结）；
⑤ crew-msg 跨项目回执投递同时落收件方 inbox，失败显式告知；
⑥ plan-status --handover「未处理通知」段（数量/路径/无则「无」/溢出行）；
⑦ 规则与角色模板同步（通知落收件方 inbox、换舵 digest 必查未处理通知）。

运行：python3 -m unittest discover -s scripts/tests -p 'test_2215_t28*.py'
"""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
CREW_MSG = SKILL_DIR / "scripts" / "crew-msg.py"
PLAN_STATUS = SKILL_DIR / "scripts" / "plan-status.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


dispatch_mod = load_module("dispatch_t28", DISPATCH)
cm = load_module("crew_msg_t28", CREW_MSG)
ps = load_module("plan_status_t28", PLAN_STATUS)


def git_init(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def inbox_files(repo: Path) -> list[Path]:
    folder = repo / ".git" / "crew" / "inbox"
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


class DispatchNotifyInboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.sender = git_init(self.base / "sender-proj")
        self.receiver = git_init(self.base / "receiver-proj")
        self.calls: list[list[str]] = []
        self.agents: list[dict] = []

    def fake_herdr(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            agents = []
            for agent in self.agents:
                entry = dict(agent)
                entry.setdefault("pane_id", f"pane-{entry.get('name', 'agent')}")
                agents.append(entry)
            return json.dumps({"result": {"agents": agents}})
        if argv[:3] == ["herdr", "pane", "list"]:
            panes = [
                {"pane_id": agent.get("pane_id") or f"pane-{agent.get('name', 'agent')}",
                 "focused": bool(agent.get("focused"))}
                for agent in self.agents
            ]
            return json.dumps({"result": {"panes": panes}})
        return ""

    def main(self, *args: str, root: Path | None = None) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out):
                code = dispatch_mod.main(
                    ["--root", str(root or self.sender), *args])
        return code, out.getvalue()

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["herdr", "agent", "prompt"]]

    def log_rows(self) -> list[dict]:
        path = self.sender / ".git" / "crew" / "agent-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def target_agent(self, *, cwd: Path | None = None, name: str = "case-helm-1",
                     status: str = "idle") -> dict:
        return {"name": name, "workspace_id": "wQ", "agent_status": status,
                "cwd": str(cwd or self.receiver)}

    def test_agent_direct_writes_receiver_inbox_with_traceable_fields(self):
        self.agents = [self.target_agent()]
        code, out = self.main(
            "notify", "--agent", "case-helm-1", "--message", "T28 通知正文", "--json")
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("written", payload["inbox"]["status"])
        path = Path(payload["inbox"]["path"])
        self.assertEqual(self.receiver / ".git" / "crew" / "inbox", path.parent)
        data = json.loads(path.read_text(encoding="utf-8"))
        for field in ("kind", "notification_id", "from", "from_project", "from_agent",
                      "target", "target_project", "ts", "message",
                      "receipt_required", "status"):
            self.assertIn(field, data)
        self.assertEqual("notification", data["kind"])
        self.assertEqual("case-helm-1", data["target"])
        self.assertEqual("sender-proj", data["from_project"])
        self.assertEqual(str(self.receiver), data["target_project"])
        self.assertEqual("pending", data["status"])
        self.assertTrue(data["receipt_required"])
        self.assertTrue(data["message"].endswith(dispatch_mod.RECEIPT_LINE))
        self.assertEqual(
            [["herdr", "agent", "prompt", "case-helm-1", data["message"]]],
            self.prompt_calls())
        row = self.log_rows()[-1]
        self.assertEqual("written", row["inbox_status"])
        self.assertEqual(str(path), row["inbox_path"])

    def test_same_content_is_idempotent_dedup(self):
        self.agents = [self.target_agent()]
        argv = ("notify", "--agent", "case-helm-1", "--message", "重复通知",
                "--no-require-receipt", "--json")
        code1, out1 = self.main(*argv)
        code2, out2 = self.main(*argv)
        self.assertEqual((0, 0), (code1, code2))
        self.assertEqual("written", json.loads(out1)["inbox"]["status"])
        self.assertEqual("dedup", json.loads(out2)["inbox"]["status"])
        self.assertEqual(1, len(inbox_files(self.receiver)))
        self.assertEqual("dedup", self.log_rows()[-1]["inbox_status"])
        self.assertEqual(2, len(self.prompt_calls()))

    def test_workspace_role_mode_writes_receiver_inbox(self):
        self.agents = [
            self.target_agent(name="case-helm-1", status="done"),
            self.target_agent(name="case-helm-2"),
        ]
        code, out = self.main(
            "notify", "--workspace", "wQ", "--role", "helm",
            "--message", "换舵通知", "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("herdr", payload["delivery"])
        self.assertEqual("resolved", payload["target_resolution"])
        self.assertEqual("case-helm-2", payload["target"])
        self.assertEqual("written", payload["inbox"]["status"])
        data = json.loads(Path(payload["inbox"]["path"]).read_text(encoding="utf-8"))
        self.assertEqual("case-helm-2", data["target"])

    def test_target_not_on_machine_degrades_with_explicit_note(self):
        self.agents = []
        code, out = self.main(
            "notify", "--agent", "ghost-helm", "--message", "hi",
            "--no-require-receipt")
        self.assertEqual(0, code)
        self.assertIn("未落收件方 inbox", out)
        self.assertIn("已降级为 pane prompt", out)
        self.assertEqual([["herdr", "agent", "prompt", "ghost-helm", "hi"]],
                         self.prompt_calls())
        self.assertEqual([], inbox_files(self.receiver))
        row = self.log_rows()[-1]
        self.assertEqual("skipped", row["inbox_status"])
        self.assertIn("不在本机 herdr agent list", row["inbox_reason"])
        self.assertEqual("ok", row["result"])

    def test_target_not_git_degrades_with_explicit_note(self):
        plain = self.base / "plain-dir"
        plain.mkdir()
        self.agents = [self.target_agent(cwd=plain)]
        code, out = self.main(
            "notify", "--agent", "case-helm-1", "--message", "hi", "--json")
        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("skipped", payload["inbox"]["status"])
        self.assertIn("不是 git 仓库", payload["inbox"]["reason"])
        self.assertEqual(1, len(self.prompt_calls()))

    def test_ack_marks_processed_and_is_idempotent(self):
        self.agents = [self.target_agent()]
        code, out = self.main(
            "notify", "--agent", "case-helm-1", "--message", "待处置", "--json")
        self.assertEqual(0, code)
        ident = json.loads(out)["inbox"]["path"]
        path = Path(ident)
        notification_id = json.loads(path.read_text(encoding="utf-8"))["notification_id"]

        # ack 由收件方在自己项目根执行
        code, out = self.main("notify", "--ack", notification_id, root=self.receiver)
        self.assertEqual(0, code)
        self.assertIn("已标记 processed", out)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual("processed", data["status"])
        self.assertIn("processed_ts", data)
        receiver_log = self.receiver / ".git" / "crew" / "agent-log.jsonl"
        self.assertEqual("notify-ack",
                         json.loads(receiver_log.read_text(encoding="utf-8").splitlines()[-1])["action"])

        before = path.read_bytes()
        code, out = self.main("notify", "--ack", notification_id, root=self.receiver)
        self.assertEqual(0, code)
        self.assertIn("no-op", out)
        self.assertEqual(before, path.read_bytes())

    def test_ack_unknown_or_mixed_usage_rejected(self):
        code, _ = self.main("notify", "--ack", "notify-not-exist")
        self.assertEqual(1, code)
        code, _ = self.main("notify", "--ack", "notify-not-exist", "--message", "x")
        self.assertEqual(1, code)


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="builder-1",
        runtime="qoder",
        task="T28",
        status="done",
        summary="切片完成",
        commit="",
        changed_files=["scripts/dispatch.py"],
        evidence=["unit"],
        work_slot="builder·T28",
        findings=[],
        receipt_id="r-t28-1",
        ts="2026-11-11T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class CrewMsgPeerInboxTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.sender_crew = self.base / "sender" / ".git" / "crew"
        self.receiver = git_init(self.base / "receiver-proj")

    def fake_run(self, argv, **kwargs):
        if argv[:3] == ["herdr", "agent", "list"]:
            agents = [{"name": "case-helm-1", "cwd": str(self.receiver),
                       "pane_id": "wQ:p1"}]
            return mock.Mock(returncode=0, stdout=json.dumps(
                {"result": {"agents": agents}}), stderr="")
        if argv[:3] == ["herdr", "pane", "list"]:
            return mock.Mock(returncode=0, stdout=json.dumps(
                {"result": {"panes": [{"pane_id": "wQ:p1", "focused": False}]}}),
                stderr="")
        if argv[:2] == ["git", "-C"]:
            return mock.Mock(returncode=0, stdout=".git\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def test_cross_project_receipt_copied_to_receiver_inbox(self):
        receipt = make_receipt()
        with mock.patch.object(cm.subprocess, "run", side_effect=self.fake_run):
            result = cm.deliver_receipt(
                receipt, self.sender_crew, to="case-helm-1", target_runtime="qoder")
        self.assertEqual("herdr", result["delivery"])
        self.assertTrue(result["ok"])
        self.assertEqual("written", result["peer_inbox"]["status"])
        path = Path(result["peer_inbox"]["path"])
        self.assertEqual(self.receiver / ".git" / "crew" / "inbox", path.parent)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(receipt, data)
        # 收件方 inbox 落盘先于 prompt（fake_run 顺序：list → git → prompt）
        self.assertFalse((self.sender_crew / "inbox" / "r-t28-1.json").exists())

    def test_same_project_inbox_reports_same_without_duplicate(self):
        receipt = make_receipt()
        same_crew = self.receiver / ".git" / "crew"
        with mock.patch.object(cm.subprocess, "run", side_effect=self.fake_run):
            result = cm.deliver_receipt(
                receipt, same_crew, to="case-helm-1", target_runtime="qoder")
        self.assertEqual("same", result["peer_inbox"]["status"])
        self.assertEqual([], inbox_files(self.receiver))

    def test_resolution_failure_reports_not_written_and_keeps_prompt(self):
        receipt = make_receipt()
        plain = self.base / "not-git"
        plain.mkdir()

        def fake_run(argv, **kwargs):
            if argv[:3] == ["herdr", "agent", "list"]:
                return mock.Mock(returncode=0, stdout=json.dumps(
                    {"result": {"agents": [{"name": "case-helm-1", "cwd": str(plain),
                                            "pane_id": "wQ:p1"}]}}), stderr="")
            if argv[:3] == ["herdr", "pane", "list"]:
                return mock.Mock(returncode=0, stdout=json.dumps(
                    {"result": {"panes": [{"pane_id": "wQ:p1", "focused": False}]}}),
                    stderr="")
            if argv[:2] == ["git", "-C"]:
                return mock.Mock(returncode=1, stdout="", stderr="not a git repository")
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(cm.subprocess, "run", side_effect=fake_run):
            result = cm.deliver_receipt(
                receipt, self.sender_crew, to="case-helm-1", target_runtime="qoder")
        self.assertEqual("herdr", result["delivery"])
        self.assertTrue(result["ok"])
        self.assertEqual("skipped", result["peer_inbox"]["status"])
        self.assertIn("不是 git 仓库", result["peer_inbox"]["reason"])
        out = io.StringIO()
        with redirect_stdout(out):
            cm._print_push(result)
        self.assertIn("未落收件方 inbox", out.getvalue())


class HandoverNotificationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.repo = self.base / "repo"
        git_init(self.repo)
        (self.repo / ".planning").mkdir()

    def write_notification(self, ident: str, *, status: str = "pending",
                           ts: str = "2026-11-11T00:00:00Z",
                           message: str = "请接管") -> Path:
        path = self.repo / ".git" / "crew" / "inbox" / f"{ident}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "kind": "notification",
            "notification_id": ident,
            "from": "sender-proj:dispatch",
            "target": "case-helm-1",
            "ts": ts,
            "message": message,
            "receipt_required": True,
            "status": status,
        }, ensure_ascii=False), encoding="utf-8")
        return path

    def handover(self) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            code = ps.main(["--root", str(self.repo), "--handover"])
        self.assertEqual(0, code)
        return out.getvalue()

    def test_lists_only_pending_notifications_with_paths(self):
        pending = self.write_notification("notify-aaaa")
        processed = self.write_notification("notify-bbbb", status="processed")
        receipt = self.repo / ".git" / "crew" / "inbox" / "r-1.json"
        receipt.write_text(json.dumps({"receipt_id": "r-1", "status": "done"}),
                           encoding="utf-8")
        out = self.handover()
        self.assertIn("## 未处理通知（1 条", out)
        self.assertIn("--ack", out)
        display = pending.relative_to(self.repo).as_posix()
        self.assertIn(display, out)
        self.assertNotIn(processed.name, out)
        self.assertNotIn("r-1.json", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)

    def test_no_pending_writes_none(self):
        out = self.handover()
        section = out.split("## 未处理通知", 1)[1].split("## 最近 progress", 1)[0]
        self.assertIn("- 无", section)

    def test_overflow_line_when_more_than_cap(self):
        for index in range(5):
            self.write_notification(f"notify-{index:04d}")
        out = self.handover()
        self.assertIn("## 未处理通知（5 条", out)
        self.assertIn("另有 2 条", out)
        self.assertLessEqual(len(out.splitlines()), ps.HANDOVER_MAX_LINES)


class RulesAndTemplatesSyncTest(unittest.TestCase):
    def test_crew_rules_cross_project_section(self):
        text = (SKILL_DIR / "references" / "crew-rules.md").read_text(encoding="utf-8")
        for needle in ("原子写入收件方", "未落收件方 inbox", "--ack",
                       "未处理通知", "跨项目回执落点"):
            self.assertIn(needle, text)

    def test_helm_and_firstmate_templates(self):
        for runtime in ("qoder", "opencode"):
            for role in ("helm", "firstmate"):
                path = SKILL_DIR / "templates" / "agents" / runtime / f"{role}.md"
                text = path.read_text(encoding="utf-8")
                self.assertIn("落收件方 inbox", text, str(path))
                self.assertIn("未处理通知", text, str(path))
                self.assertIn("notify --ack", text, str(path))


if __name__ == "__main__":
    unittest.main()
