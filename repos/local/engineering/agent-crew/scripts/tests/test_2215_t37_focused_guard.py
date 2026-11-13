#!/usr/bin/env python3
"""T37 聚焦守卫回归：crew-msg / dispatch 注入前检查 pane focused 与 delivery 审计。

（python3 scripts/tests/test_2215_t37_focused_guard.py 或
  python3 -m unittest discover -s scripts/tests）"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent

_cm_spec = importlib.util.spec_from_file_location(
    "crew_msg_t37", SKILL_DIR / "scripts" / "crew-msg.py")
cm = importlib.util.module_from_spec(_cm_spec)
_cm_spec.loader.exec_module(cm)

_dm_spec = importlib.util.spec_from_file_location(
    "dispatch_t37", SKILL_DIR / "scripts" / "dispatch.py")
dm = importlib.util.module_from_spec(_dm_spec)
sys.modules["dispatch_t37"] = dm
_dm_spec.loader.exec_module(dm)

_cg_spec = importlib.util.spec_from_file_location(
    "crew_guard_t37", SKILL_DIR / "scripts" / "crew-guard.py")
cg = importlib.util.module_from_spec(_cg_spec)
sys.modules["crew_guard_t37"] = cg  # dataclass 注解解析需要模块已注册
_cg_spec.loader.exec_module(cg)


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="builder-1",
        runtime="qoder",
        task="T37",
        status="done",
        summary="聚焦守卫切片",
        commit="",
        changed_files=["scripts/crew-msg.py"],
        evidence=["unit"],
        work_slot="builder·T37",
        findings=[],
        receipt_id="r-t37-1",
        ts="2026-11-11T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class FakeHerdr:
    """按 argv 模拟 herdr agent list / pane list / agent prompt / notification show。"""

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
        if argv[:3] == ["herdr", "notification", "show"]:
            return mock.Mock(returncode=self.notify_rc, stdout="", stderr="")
        if argv[:3] == ["herdr", "pane", "read"]:
            # T69 ctx 门夹具：低 ctx 过门，焦点/审批守卫语义由本测试原有断言承载
            return mock.Mock(returncode=0, stdout="ctx 100k/1M", stderr="")
        return mock.Mock(returncode=self.prompt_rc, stdout="",
                         stderr="prompt failed" if self.prompt_rc else "")

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls
                if call[:3] == ["herdr", "agent", "prompt"]]


class CrewMsgFocusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"

    def run_write(self, fake: FakeHerdr, **overrides):
        via = overrides.pop("via", "auto")
        receipt = make_receipt(**overrides)
        with mock.patch.object(cm.subprocess, "run", side_effect=fake), \
                mock.patch.object(cm, "notify", return_value=True):
            result = cm.write_receipt(
                self.crew, receipt, to="helm-qoder", target_runtime="qoder",
                via=via)
        data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        return receipt, result, data

    def run_cli(self, fake: FakeHerdr, *, receipt_id: str, to: str,
                via: str = "auto") -> tuple[int, str, str]:
        """走真实 CLI 入口，验证退出码语义（A-1：不可达必须非 0）。"""
        argv = [
            "write", "--crew-dir", str(self.crew), "--json",
            "--from", "builder-1", "--runtime", "qoder",
            "--task", "T37", "--status", "done", "--summary", "聚焦守卫",
            "--receipt-id", receipt_id, "--to", to,
            "--target-runtime", "qoder", "--via", via,
        ]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cm.subprocess, "run", side_effect=fake), \
                mock.patch.object(cm, "notify", return_value=True), \
                redirect_stdout(out), redirect_stderr(err):
            code = cm.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_focused_pane_skips_injection_and_records_inbox_fallback(self):
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}],
            [{"pane_id": "wQ:p1", "focused": True}])
        _receipt, result, data = self.run_write(fake)

        self.assertEqual(result["push"]["delivery"], "inbox_fallback")
        self.assertTrue(result["push"]["ok"])
        self.assertTrue(result["push"]["focused"])
        self.assertEqual(fake.prompt_calls(), [])
        self.assertEqual(data["delivery"], "inbox_fallback")
        self.assertTrue(result["delivery_record"]["ok"])

    def test_unfocused_pane_injects_and_records_herdr(self):
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}],
            [{"pane_id": "wQ:p1", "focused": False}])
        _receipt, result, data = self.run_write(fake)

        self.assertEqual(result["push"]["delivery"], "herdr")
        self.assertEqual(len(fake.prompt_calls()), 1)
        self.assertEqual(data["delivery"], "herdr")

    def test_pane_list_unavailable_is_fail_safe(self):
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}], pane_mode="down")
        _receipt, result, data = self.run_write(fake, via="herdr")

        self.assertEqual(result["push"]["delivery"], "inbox_fallback")
        self.assertIsNone(result["push"]["focused"])
        self.assertIn("fail-safe", result["push"]["note"])
        self.assertEqual(fake.prompt_calls(), [])
        self.assertEqual(data["delivery"], "inbox_fallback")

    def test_pane_focused_field_missing_is_fail_safe(self):
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}],
            [{"pane_id": "wQ:p1"}])
        _receipt, result, _data = self.run_write(fake)

        self.assertEqual(result["push"]["delivery"], "inbox_fallback")
        self.assertIn("缺 focused 字段", result["push"]["note"])
        self.assertEqual(fake.prompt_calls(), [])

    def test_sendmessage_records_sendmessage_delegate_without_probe(self):
        fake = FakeHerdr([])
        receipt = make_receipt()
        with mock.patch.object(cm.subprocess, "run", side_effect=fake):
            result = cm.write_receipt(
                self.crew, receipt, to="helm-qoder", target_runtime="qoder",
                via="sendmessage")
        data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))

        self.assertEqual(result["push"]["delivery"], "delegate")
        self.assertEqual(data["delivery"], "sendmessage-delegate")
        self.assertEqual(fake.prompt_calls(), [])

    def test_ghost_target_is_unreachable_not_inbox_fallback(self):
        """A-1：目标不在 agent list → ok=False + rc=3，不得报 inbox_fallback 成功。"""
        fake = FakeHerdr([])
        code, out, err = self.run_cli(
            fake, receipt_id="r-ghost", to="ghost-helm", via="herdr")

        self.assertEqual(3, code)
        payload = json.loads(out)["push"]
        self.assertEqual("delegate", payload["delivery"])
        self.assertFalse(payload["ok"])
        self.assertIn("目标不可达", payload["error"])
        self.assertIn("不在本机 herdr agent list", payload["error"])
        self.assertEqual("skipped", payload["peer_inbox"]["status"])
        self.assertEqual([], fake.prompt_calls())
        self.assertNotIn("inbox_fallback", json.dumps(payload, ensure_ascii=False))
        self.assertIn("推送失败", err)
        data = json.loads((self.crew / "inbox" / "r-ghost.json").read_text(
            encoding="utf-8"))
        self.assertEqual("delegate", data["delivery"])

    def test_agent_list_failure_is_unreachable(self):
        """A-1：agent list 调用失败同样按不可达处理（非 0 退出）。"""
        fake = FakeHerdr([], agent_mode="down")
        code, out, _err = self.run_cli(
            fake, receipt_id="r-listdown", to="helm-qoder")

        self.assertEqual(3, code)
        payload = json.loads(out)["push"]
        self.assertFalse(payload["ok"])
        self.assertIn("agent list", payload["error"])
        self.assertEqual([], fake.prompt_calls())

    def test_target_present_without_pane_id_stays_inbox_fallback(self):
        """A-1 边界：目标存在但 pane_id 缺失 → 可达但不可判定，仍 fail-safe 不注入。"""
        fake = FakeHerdr([{"name": "helm-qoder"}])
        _receipt, result, _data = self.run_write(fake)

        self.assertEqual("inbox_fallback", result["push"]["delivery"])
        self.assertTrue(result["push"]["ok"])
        self.assertIsNone(result["push"]["focused"])
        self.assertEqual([], fake.prompt_calls())

    def test_via_herdr_focused_falls_back_with_explicit_note(self):
        """A-2：--via herdr 聚焦同样不注入，落 inbox_fallback 并在 note 明示。"""
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}],
            [{"pane_id": "wQ:p1", "focused": True}])
        _receipt, result, _data = self.run_write(fake, via="herdr")

        self.assertEqual("inbox_fallback", result["push"]["delivery"])
        self.assertTrue(result["push"]["ok"])
        self.assertIn("--via herdr", result["push"]["note"])
        self.assertEqual([], fake.prompt_calls())

    def test_notification_failure_records_notified_false(self):
        """A-3：herdr notification 非 0 退出 → notified=false，note 不虚报。"""
        fake = FakeHerdr(
            [{"name": "helm-qoder", "pane_id": "wQ:p1"}],
            [{"pane_id": "wQ:p1", "focused": True}], notify_rc=1)
        receipt = make_receipt(receipt_id="r-notifyfail")
        with mock.patch.object(cm.subprocess, "run", side_effect=fake), \
                mock.patch.dict(os.environ, {"HERDR_ENV": "1"}):
            result = cm.write_receipt(
                self.crew, receipt, to="helm-qoder", target_runtime="qoder")

        self.assertEqual("inbox_fallback", result["push"]["delivery"])
        self.assertFalse(result["push"]["notified"])
        self.assertIn("未发送", result["push"]["note"])
        self.assertEqual([], fake.prompt_calls())


class DispatchFocusTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        registry = dm.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "crew-builder": {"role": "builder", "runtime": "qoder",
                             "pane": "wX:p1"},
        }), encoding="utf-8")
        self.notify_calls: list[tuple[str, str]] = []

    def run_main(self, fake: FakeHerdr, *args: str, notify_ok: bool = True):
        def dispatch_herdr(argv):
            result = fake(argv)
            if result.returncode != 0:
                raise dm.DispatchError(
                    result.stderr or "herdr failed", result.returncode)
            return result.stdout

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dm, "run_herdr", side_effect=dispatch_herdr), \
                mock.patch.object(
                    dm, "herdr_notify",
                    side_effect=lambda title, body: (
                        self.notify_calls.append((title, body)) or notify_ok)):
            with redirect_stdout(out), redirect_stderr(err):
                code = dm.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def agent_log(self) -> list[dict]:
        path = dm.crew_dir(self.root) / "agent-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_prompt_focused_refuses_with_rc2_and_audit_row(self):
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1"}],
            [{"pane_id": "wX:p1", "focused": True}])
        code, _out, err = self.run_main(
            fake, "prompt", "--agent", "crew-builder", "--message", "hi")

        self.assertEqual(2, code)
        self.assertIn("聚焦", err)
        self.assertEqual(fake.prompt_calls(), [])
        row = self.agent_log()[-1]
        self.assertEqual("none", row["delivery"])
        self.assertEqual("focused", row["focus"])
        self.assertEqual("refused", row["result"])

    def test_prompt_unfocused_injects_and_records_herdr(self):
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1"}],
            [{"pane_id": "wX:p1", "focused": False}])
        code, out, _err = self.run_main(
            fake, "prompt", "--agent", "crew-builder", "--message", "hi")

        self.assertEqual(0, code)
        self.assertIn("已发送提示", out)
        self.assertEqual(len(fake.prompt_calls()), 1)
        row = self.agent_log()[-1]
        self.assertEqual("herdr", row["delivery"])
        self.assertEqual("unfocused", row["focus"])

    def test_prompt_unknown_pane_state_is_fail_safe(self):
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1"}], pane_mode="down")
        code, _out, err = self.run_main(
            fake, "prompt", "--agent", "crew-builder", "--message", "hi")

        self.assertEqual(2, code)
        self.assertIn("fail-safe", err)
        self.assertEqual(fake.prompt_calls(), [])
        row = self.agent_log()[-1]
        self.assertEqual("unknown", row["focus"])
        self.assertIn("pane list", row["focus_reason"])

    def test_notify_focused_keeps_inbox_and_skips_prompt(self):
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1",
              "cwd": str(self.root)}],
            [{"pane_id": "wX:p1", "focused": True}])
        code, out, _err = self.run_main(
            fake, "notify", "--agent", "crew-builder", "--message", "hi",
            "--no-require-receipt", "--json")

        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertEqual("focused", payload["prompt_skipped"])
        self.assertTrue(payload["notified"])
        self.assertEqual("written", payload["inbox"]["status"])
        self.assertEqual(fake.prompt_calls(), [])
        self.assertEqual(len(self.notify_calls), 1)
        row = self.agent_log()[-1]
        self.assertEqual("inbox_fallback", row["delivery"])
        self.assertEqual("focused", row["prompt_skipped"])
        self.assertEqual("ok", row["result"])

    def test_notify_unknown_pane_state_is_fail_safe(self):
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1",
              "cwd": str(self.root)}], pane_mode="down")
        code, out, _err = self.run_main(
            fake, "notify", "--agent", "crew-builder", "--message", "hi",
            "--no-require-receipt", "--json")

        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertEqual("unknown", payload["prompt_skipped"])
        self.assertEqual(fake.prompt_calls(), [])

    def test_notify_notification_failure_records_notified_false(self):
        """A-3：通知失败时 --json 与 agent-log 的 notified 都必须为 false。"""
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1",
              "cwd": str(self.root)}],
            [{"pane_id": "wX:p1", "focused": True}])
        code, out, _err = self.run_main(
            fake, "notify", "--agent", "crew-builder", "--message", "hi",
            "--no-require-receipt", "--json", notify_ok=False)

        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertFalse(payload["notified"])
        row = self.agent_log()[-1]
        self.assertFalse(row["notified"])
        self.assertEqual("inbox_fallback", row["delivery"])

    def test_notify_skipped_without_inbox_returns_rc2(self):
        plain = self.base / "plain"
        plain.mkdir()
        fake = FakeHerdr(
            [{"name": "crew-builder", "pane_id": "wX:p1", "cwd": str(plain)}],
            [{"pane_id": "wX:p1", "focused": True}])
        code, out, err = self.run_main(
            fake, "notify", "--agent", "crew-builder", "--message", "hi",
            "--no-require-receipt", "--json")

        self.assertEqual(2, code)
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertEqual("degraded", payload["result"])
        self.assertEqual("skipped", payload["inbox"]["status"])
        self.assertIn("通知未送达", err)
        self.assertEqual(fake.prompt_calls(), [])

    def test_notify_external_agent_without_pane_still_prompts(self):
        fake = FakeHerdr([])
        code, out, _err = self.run_main(
            fake, "notify", "--agent", "case-agent", "--message", "hi",
            "--no-require-receipt", "--json")

        self.assertEqual(0, code)
        payload = json.loads(out)
        self.assertEqual("herdr", payload["delivery"])
        self.assertIsNone(payload["prompt_skipped"])
        self.assertEqual(len(fake.prompt_calls()), 1)


class NotifyAuditTest(unittest.TestCase):
    """A-3：三处 herdr 通知封装都必须看 returncode，失败不得报 True。"""

    def notify_with_rc(self, func, rc: int) -> bool:
        with mock.patch.dict(os.environ, {"HERDR_ENV": "1"}), \
                mock.patch.object(func.__globals__["subprocess"], "run",
                                  return_value=mock.Mock(returncode=rc)):
            return func("标题", "正文")

    def test_crew_msg_notify_checks_returncode(self):
        self.assertFalse(self.notify_with_rc(cm.notify, 1))
        self.assertTrue(self.notify_with_rc(cm.notify, 0))

    def test_dispatch_herdr_notify_checks_returncode(self):
        self.assertFalse(self.notify_with_rc(dm.herdr_notify, 1))
        self.assertTrue(self.notify_with_rc(dm.herdr_notify, 0))

    def test_guard_herdr_notify_checks_returncode(self):
        self.assertFalse(self.notify_with_rc(cg.herdr_notify, 2))
        self.assertTrue(self.notify_with_rc(cg.herdr_notify, 0))


class TemplateSyncTest(unittest.TestCase):
    def test_role_templates_document_focus_guard(self):
        rels = [
            "templates/agents/qoder/helm.md",
            "templates/agents/qoder/firstmate.md",
            "templates/agents/qoder/runner.md",
            "templates/agents/opencode/helm.md",
            "templates/agents/opencode/firstmate.md",
            "templates/agents/opencode/runner.md",
        ]
        for rel in rels:
            with self.subTest(rel=rel):
                text = (SKILL_DIR / rel).read_text(encoding="utf-8")
                self.assertIn("聚焦守卫（T37）", text)
                self.assertIn("inbox_fallback", text)
                self.assertIn("含 `--via herdr`", text)

    def test_cheatsheet_documents_focus_guard(self):
        text = (SKILL_DIR / "references" / "herdr-cheatsheet.md").read_text(
            encoding="utf-8")
        self.assertIn("聚焦守卫（T37）", text)
        self.assertIn("focused", text)
        self.assertIn("inbox_fallback", text)
        self.assertIn("rc=2", text)
        self.assertIn("`--via herdr` 同此口径", text)
        self.assertIn("目标不可达 ≠ 聚焦回退", text)
        self.assertIn("已知限制（A-4）", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
