#!/usr/bin/env python3
"""crew-msg.py 回执通道回归测试。
（python3 scripts/tests/test_crew_msg.py 或 python3 -m unittest discover -s scripts/tests）"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import tempfile
import unittest
import urllib.parse
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "crew-msg.py"
_spec = importlib.util.spec_from_file_location("crew_msg", _SPEC_PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="builder-1",
        runtime="opencode",
        task="T-1",
        status="done",
        summary="切片完成",
        commit="abc123",
        changed_files=["scripts/foo.py"],
        evidence=["python3 -m unittest"],
        work_slot="builder·T-1",
        findings=[],
        receipt_id="r-1",
        ts="2026-11-09T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class CrewDirTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"

    def write_registry(self, routes: dict) -> None:
        self.crew.mkdir(parents=True, exist_ok=True)
        cm.registry_path(self.crew).write_text(
            json.dumps({"version": 1, "routes": routes}), encoding="utf-8"
        )


class ReceiptWriteTest(CrewDirTestBase):
    def test_receipt_fields_complete_and_atomic_write(self):
        receipt = make_receipt()
        result = cm.write_receipt(self.crew, receipt, push=False)

        data = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(set(data), set(cm.RECEIPT_FIELDS))
        for field in cm.RECEIPT_FIELDS:
            self.assertEqual(data[field], receipt[field])
        self.assertFalse(result["duplicate"])
        self.assertIsNone(result["push"])
        self.assertEqual([p.name for p in cm.inbox_dir(self.crew).iterdir()], ["r-1.json"])

    def test_interrupted_atomic_write_leaves_no_partial_file(self):
        target = cm.inbox_dir(self.crew) / "r-1.json"
        with mock.patch.object(cm.os, "replace", side_effect=OSError("simulated crash")):
            with self.assertRaises(OSError):
                cm.write_receipt(self.crew, make_receipt(), push=False)

        self.assertFalse(target.exists())
        self.assertEqual(list(cm.inbox_dir(self.crew).iterdir()), [])

    def test_duplicate_receipt_id_is_idempotent(self):
        self.write_registry(
            {"lead": {"runtime": "opencode", "server": "http://127.0.0.1:1", "session": "ses_1"}}
        )
        calls = []

        def fake_post(url, payload, timeout=10.0):
            calls.append((url, payload))
            return 200, b"{}"

        with mock.patch.object(cm, "http_post_json", fake_post):
            first = cm.write_receipt(self.crew, make_receipt(), to="lead")
            second = cm.write_receipt(self.crew, make_receipt(), to="lead")

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertIsNone(second["push"])
        self.assertEqual(len(calls), 1)
        self.assertEqual([p.name for p in cm.inbox_dir(self.crew).iterdir()], ["r-1.json"])

    def test_push_failure_keeps_receipt_readable(self):
        self.write_registry(
            {"lead": {"runtime": "opencode", "server": "http://127.0.0.1:1", "session": "ses_1"}}
        )

        def refuse(url, payload, timeout=10.0):
            raise OSError("connection refused")

        with mock.patch.object(cm, "http_post_json", refuse):
            result = cm.write_receipt(self.crew, make_receipt(status="blocked"), to="lead")

        self.assertFalse(result["push"]["ok"])
        self.assertIn("connection refused", result["push"]["error"])
        self.assertEqual(cm.read_receipt(self.crew, "r-1")["status"], "blocked")
        self.assertEqual(
            [item["receipt_id"] for item in cm.list_receipts(self.crew)], ["r-1"]
        )

    def test_http_error_status_is_push_failure(self):
        self.write_registry(
            {"lead": {"runtime": "opencode", "server": "http://127.0.0.1:1", "session": "ses_1"}}
        )

        with mock.patch.object(cm, "http_post_json", lambda *a, **k: (500, b"nope")):
            result = cm.write_receipt(self.crew, make_receipt(), to="lead")

        self.assertFalse(result["push"]["ok"])
        self.assertIn("500", result["push"]["error"])

    def test_receipt_id_rejects_path_traversal(self):
        with self.assertRaises(cm.CrewMsgError):
            make_receipt(receipt_id="../evil")
        with self.assertRaises(cm.CrewMsgError):
            cm.read_receipt(self.crew, "../evil")


class OpencodeDeliveryTest(CrewDirTestBase):
    def test_request_method_path_and_queue_delivery(self):
        self.write_registry(
            {
                "lead": {
                    "runtime": "opencode",
                    "server": "http://127.0.0.1:4096/",
                    "session": "ses_abc",
                }
            }
        )
        captured = {}

        class FakeResponse:
            status = 200

            def read(self):
                return b'{"ok":true}'

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

        def fake_urlopen(request, timeout=None):
            captured["method"] = request.get_method()
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse()

        with mock.patch.object(cm.urllib.request, "urlopen", fake_urlopen):
            result = cm.deliver_receipt(make_receipt(), self.crew, to="lead")

        self.assertTrue(result["ok"])
        self.assertEqual(result["delivery"], "http")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(
            urllib.parse.urlsplit(captured["url"]).path, "/api/session/ses_abc/prompt"
        )
        self.assertEqual(captured["body"]["delivery"], "queue")
        self.assertEqual(captured["body"]["parts"][0]["type"], "text")


class RegistryTest(CrewDirTestBase):
    def test_missing_registry_is_empty_and_not_created(self):
        registry = cm.load_registry(self.crew)
        self.assertEqual(registry["routes"], {})
        self.assertFalse(cm.registry_path(self.crew).exists())
        self.assertFalse(self.crew.exists())

    def test_invalid_route_reports_clear_error(self):
        self.crew.mkdir(parents=True)
        cm.registry_path(self.crew).write_text(
            json.dumps(
                {
                    "routes": {
                        "late": {
                            "runtime": "opencode",
                            "server": "ftp://127.0.0.1:1",
                            "session": "",
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(cm.RegistryError) as ctx:
            cm.load_registry(self.crew)
        message = str(ctx.exception)
        for token in ("late", "session", "server"):
            self.assertIn(token, message)

    def test_unknown_runtime_and_fields_rejected(self):
        self.crew.mkdir(parents=True)
        cm.registry_path(self.crew).write_text(
            json.dumps({"routes": {"x": {"runtime": "qoder", "pane": "w1:p1"}}}),
            encoding="utf-8",
        )
        with self.assertRaises(cm.RegistryError) as ctx:
            cm.load_registry(self.crew)
        self.assertIn("qoder", str(ctx.exception))

    def test_delivery_without_target_does_not_create_registry(self):
        result = cm.deliver_receipt(make_receipt(), self.crew, to=None)
        self.assertEqual(result["delivery"], "none")
        self.assertFalse(self.crew.exists())

    def test_invalid_registry_does_not_prevent_receipt_file(self):
        self.crew.mkdir(parents=True)
        cm.registry_path(self.crew).write_text('{"routes": []}', encoding="utf-8")
        with self.assertRaises(cm.RegistryError):
            cm.write_receipt(self.crew, make_receipt(), to="lead")
        self.assertTrue((cm.inbox_dir(self.crew) / "r-1.json").exists())


class DeliveryBoundaryTest(CrewDirTestBase):
    def test_qoder_sendmessage_returns_delegate_with_warning(self):
        receipt = make_receipt(runtime="qoder")
        result = cm.deliver_receipt(
            receipt, self.crew, to="helm", target_runtime="qoder", via="sendmessage"
        )
        self.assertEqual(result["delivery"], "delegate")
        self.assertTrue(result["ok"])
        self.assertIn(receipt["receipt_id"], result["message"])
        self.assertIn(receipt["summary"], result["message"])
        self.assertIn("需接收方人工确认", result["warning"])
        self.assertIn("inbox 为真源", result["warning"])

    def test_qoder_auto_uses_herdr_prompt_without_sendmessage(self):
        receipt = make_receipt(runtime="qoder")
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(cm.subprocess, "run", return_value=completed) as run:
            result = cm.deliver_receipt(
                receipt, self.crew, to="helm-qoder", target_runtime="qoder", via="auto"
            )

        self.assertEqual(result["delivery"], "herdr")
        self.assertTrue(result["ok"])
        self.assertEqual(result["target"], "helm-qoder")
        self.assertNotIn("SendMessage", result.get("note", ""))
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["herdr", "agent", "prompt", "helm-qoder"])
        self.assertIn(receipt["receipt_id"], argv[4])
        self.assertIn(receipt["summary"], argv[4])

    def test_qoder_herdr_failure_falls_back_to_delegate_with_error(self):
        receipt = make_receipt(runtime="qoder")
        completed = mock.Mock(returncode=1, stdout="", stderr="agent not found")
        with mock.patch.object(cm.subprocess, "run", return_value=completed):
            result = cm.deliver_receipt(
                receipt, self.crew, to="helm-qoder", target_runtime="qoder", via="herdr"
            )

        self.assertEqual(result["delivery"], "delegate")
        self.assertFalse(result["ok"])
        self.assertIn("退出码 1", result["error"])
        self.assertIn("agent not found", result["error"])
        self.assertIn(receipt["receipt_id"], result["message"])
        self.assertIn("inbox 为真源", result["note"])

    def test_qoder_auto_without_herdr_name_falls_back_to_delegate(self):
        receipt = make_receipt(runtime="qoder")
        with mock.patch.object(cm.subprocess, "run") as run:
            result = cm.deliver_receipt(
                receipt, self.crew, to="session:ses_123", target_runtime="qoder", via="auto"
            )

        self.assertEqual(result["delivery"], "delegate")
        self.assertTrue(result["ok"])
        self.assertIn("无可用 herdr 名", result["note"])
        self.assertIn(receipt["receipt_id"], result["message"])
        run.assert_not_called()

    def test_via_is_ignored_for_non_qoder_paths(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            cm, "notify", return_value=False
        ) as notify:
            herdr_result = cm.deliver_receipt(
                make_receipt(runtime="qoder"), self.crew, to="herdr-peer", via="herdr"
            )
        self.assertEqual(herdr_result["delivery"], "notification")
        self.assertEqual(notify.call_count, 1)

        self.write_registry(
            {"lead": {"runtime": "opencode", "server": "http://127.0.0.1:1", "session": "ses_1"}}
        )
        with mock.patch.object(cm, "http_post_json", return_value=(200, b"{}")) as post:
            opencode_result = cm.deliver_receipt(
                make_receipt(), self.crew, to="lead", via="sendmessage"
            )
        self.assertEqual(opencode_result["delivery"], "http")
        post.assert_called_once()

        with mock.patch.dict(os.environ, {}, clear=True):
            cross_result = cm.deliver_receipt(
                make_receipt(runtime="qoder"),
                self.crew,
                to="claude-side",
                target_runtime="claude",
                via="herdr",
            )
        self.assertEqual(cross_result["delivery"], "notification")

    def test_cross_product_only_notifies(self):
        self.write_registry(
            {"claude-side": {"runtime": "claude", "socket": "/tmp/claude-inbox.sock"}}
        )
        with mock.patch.dict(os.environ, {}, clear=True):
            result = cm.deliver_receipt(make_receipt(), self.crew, to="claude-side")
        self.assertEqual(result["delivery"], "notification")
        self.assertFalse(result["notified"])
        self.assertIn("跨产品", result["note"])

    def test_claude_route_is_placeholder(self):
        self.write_registry(
            {"claude-side": {"runtime": "claude", "socket": "/tmp/claude-inbox.sock"}}
        )
        result = cm.deliver_receipt(
            make_receipt(runtime="claude"), self.crew, to="claude-side"
        )
        self.assertEqual(result["delivery"], "placeholder")
        self.assertTrue(result["ok"])

    def test_unknown_peer_falls_back_to_notification(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            result = cm.deliver_receipt(make_receipt(), self.crew, to="herdr-peer")
        self.assertEqual(result["delivery"], "notification")
        self.assertEqual(result["runtime"], "herdr")
        self.assertFalse(result["notified"])


class CliTest(CrewDirTestBase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cm.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_write_no_push_json(self):
        code, out, _ = self._run(
            [
                "write",
                "--crew-dir",
                str(self.crew),
                "--from",
                "builder-1",
                "--runtime",
                "opencode",
                "--task",
                "T-1",
                "--status",
                "done",
                "--summary",
                "ok",
                "--no-push",
                "--json",
            ]
        )
        self.assertEqual(code, 0)
        payload = json.loads(out)
        self.assertTrue(Path(payload["path"]).exists())
        self.assertFalse(payload["duplicate"])
        self.assertFalse(cm.registry_path(self.crew).exists())

    def test_write_push_failure_exits_3(self):
        self.write_registry(
            {"lead": {"runtime": "opencode", "server": "http://127.0.0.1:1", "session": "ses_1"}}
        )

        def refuse(url, payload, timeout=10.0):
            raise OSError("refused")

        with mock.patch.object(cm, "http_post_json", refuse):
            code, _, err = self._run(
                [
                    "write",
                    "--crew-dir",
                    str(self.crew),
                    "--from",
                    "builder-1",
                    "--runtime",
                    "opencode",
                    "--task",
                    "T-1",
                    "--status",
                    "blocked",
                    "--summary",
                    "卡住",
                    "--to",
                    "lead",
                    "--json",
                    "--receipt-id",
                    "r-cli",
                ]
            )
        self.assertEqual(code, cm.EXIT_PUSH_FAILED)
        self.assertIn("推送失败", err)
        self.assertTrue((cm.inbox_dir(self.crew) / "r-cli.json").exists())

    def test_ls_and_read(self):
        cm.write_receipt(self.crew, make_receipt(status="captain"), push=False)
        code, out, _ = self._run(["ls", "--crew-dir", str(self.crew), "--json"])
        self.assertEqual(code, 0)
        self.assertEqual([item["status"] for item in json.loads(out)], ["captain"])
        code, out, _ = self._run(
            ["read", "--crew-dir", str(self.crew), "r-1"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["receipt_id"], "r-1")

    def test_push_via_sendmessage_prints_warning(self):
        cm.write_receipt(self.crew, make_receipt(runtime="qoder"), push=False)
        code, out, _ = self._run(
            [
                "push",
                "--crew-dir",
                str(self.crew),
                "r-1",
                "--to",
                "helm-qoder",
                "--target-runtime",
                "qoder",
                "--via",
                "sendmessage",
            ]
        )
        self.assertEqual(code, 0)
        self.assertIn("warning:", out)
        self.assertIn("需接收方人工确认", out)
        self.assertIn("DELEGATE", out)

    def test_help_is_self_describing(self):
        text = cm.build_parser().format_help()
        for token in (
            "write",
            "ls",
            "read",
            "push",
            "delivery=queue",
            "SendMessage",
            "registry.json",
            "--via",
            "herdr agent prompt",
        ):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
