#!/usr/bin/env python3
"""2.2.28 FB-053：push --to 传错目标名不再静默「成功」。

背景（FB-053）：`crew-msg.py push --to <项目名>`（非 agent 名、也不在
registry 路由）时，mirror 落盘 skipped、默认走 herdr notification 分支返回
ok=True、rc=0——发送方以为投递成功，实际没有任何 agent 收到。

修复契约：
1. 目标与已知 agent 名（本机 herdr agent list）和 registry 路由都不匹配 →
   delivery=unrouted、ok=False、rc=3，stderr 告警并提示可用目标；
2. herdr agent list 调用失败（环境问题，无法断言名单）→ 维持 notification
   兜底、rc=0（fail-safe 不误伤）；
3. registry 路由命中 → 正常投递，不受 unrouted 影响；
4. write --push 同契约（回执仍成立，rc=3）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "crew-msg.py"
_spec = importlib.util.spec_from_file_location("crew_msg_fb053", _SPEC_PATH)
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


def fake_herdr_agents(names: list[str]):
    """agent list 固定返回给定名单；git 调用按原样放行。"""

    def fake(argv, **kwargs):
        if argv[:3] == ["herdr", "agent", "list"]:
            agents = [{"name": name, "pane_id": f"pane-{name}"} for name in names]
            return mock.Mock(
                returncode=0,
                stdout=json.dumps({"result": {"agents": agents}}),
                stderr="")
        raise AssertionError(f"夹具不应走到其他命令: {argv[:3]}")

    return fake


class Fb053PushUnroutedTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"
        self.crew.mkdir(parents=True, exist_ok=True)
        receipt = make_receipt()
        cm.write_receipt(self.crew, receipt, push=False)

    def _push(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cm.main(["push", "--crew-dir", str(self.crew), "r-1", *extra])
        return code, out.getvalue(), err.getvalue()

    def test_push_to_unknown_name_is_unrouted_with_targets_hint(self):
        """--to 与本机 agent 名和 registry 都不匹配 → rc=3 + unrouted + 可用目标。"""
        with mock.patch.object(cm.subprocess, "run",
                               side_effect=fake_herdr_agents(["helm-qoder", "fb-builder"])):
            code, out, err = self._push("--to", "some-project-name", "--json")
        self.assertEqual(3, code, "unrouted 必须 rc≠0")
        payload = json.loads(out)
        self.assertEqual("unrouted", payload["delivery"])
        # 4b②：结构化原因码（enum 优先，reason 子串匹配仅降级兜底）
        self.assertEqual(cm.UNROUTED_NOT_IN_LIST, payload["unrouted_code"])
        self.assertIn("some-project-name", err)
        self.assertIn("helm-qoder", err, "stderr 必须提示可用目标")
        self.assertIn("fb-builder", err)
        # 回执文件仍成立（推送失败不影响回执真源）
        self.assertTrue((self.crew / "inbox" / "r-1.json").exists())

    def test_push_herdr_list_down_falls_back_to_notification(self):
        """agent list 调用失败（环境问题）无法断言名单 → 维持 notification rc=0。"""
        with mock.patch.object(cm.subprocess, "run",
                               side_effect=OSError("herdr down")), \
                mock.patch.object(cm, "notify", return_value=False):
            code, out, err = self._push("--to", "some-project-name")
        self.assertEqual(0, code)
        self.assertIn("推送：notification", out)

    def test_registry_route_not_affected_by_unrouted(self):
        """registry 路由命中（opencode）→ 正常 http 投递，不判 unrouted。"""
        (self.crew / "registry.json").write_text(json.dumps({
            "version": 1,
            "routes": {"lead": {"runtime": "opencode",
                                "server": "http://127.0.0.1:1",
                                "session": "ses_1"}},
        }), encoding="utf-8")
        with mock.patch.object(cm, "http_post_json", return_value=(200, b"{}")):
            code, out, err = self._push("--to", "lead")
        self.assertEqual(0, code)
        self.assertIn("推送：http", out)
        self.assertNotIn("unrouted", out)

    def test_write_with_bad_target_keeps_receipt_and_exits_3(self):
        """write --to 传错名：回执仍成立，推送失败 rc=3。"""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cm.subprocess, "run",
                               side_effect=fake_herdr_agents(["helm-qoder"])):
            with redirect_stdout(out), redirect_stderr(err):
                code = cm.main([
                    "write", "--crew-dir", str(self.crew),
                    "--from", "builder-1", "--runtime", "opencode",
                    "--task", "T-2", "--status", "done",
                    "--summary", "第二片", "--receipt-id", "r-2",
                    "--to", "wrong-name"])
        self.assertEqual(3, code)
        self.assertTrue((self.crew / "inbox" / "r-2.json").exists(), "回执仍成立")
        self.assertIn("wrong-name", err.getvalue())


if __name__ == "__main__":
    unittest.main()
