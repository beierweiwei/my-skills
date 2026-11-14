#!/usr/bin/env python3
"""2.2.32 工单 C/T76/C2：生命周期操作脚本化（dispatch 收窄）。

覆盖：
- start：拒绝手传 --model / --permission-mode（rc=2 + 配置指引）；模型按
  agents.roles.<role> > agents.default 级联解析（registry/agent-log 记
  modelSource，三栈通用）。
- prompt：--message/--message-file 只接受已存在文件路径；内联文本拒绝
  rc=1 + 指引；缺文件拒绝（失败侧安全）。
- close：working/blocked 拒关（rc=2）、--force --reason 放行留痕、
  awaiting_approval 恒拒；agent-log close 行补 sender/reason/status。
fixture 全部为中性占位内容。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent

_spec = importlib.util.spec_from_file_location("dispatch_t76c2", _SCRIPTS / "dispatch.py")
dm = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_t76c2"] = dm
_spec.loader.exec_module(dm)


def fake_herdr_factory(calls: list[list[str]], agents: list[dict]):
    def fake_run(argv, **_kwargs):
        calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=json.dumps({"result": {"agents": agents}}), stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="{}", stderr="")
    return fake_run


class StartConfigResolutionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        cfg = self.root / ".planning/orchestration.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        (self.root / ".planning/portfolio").mkdir(exist_ok=True)
        cfg.write_text(json.dumps({"skillVersion": "test"}), encoding="utf-8")

    def test_resolve_model_from_config_cascade(self):
        cfg = {"agents": {"default": {"backend": "claude", "model": "default-model"},
                          "roles": {"builder": {"backend": "claude", "model": "role-model"}}}}
        self.assertEqual(("role-model", "roles.builder"),
                         dm.resolve_model_from_config(cfg, "builder"))
        self.assertEqual(("default-model", "default"),
                         dm.resolve_model_from_config(cfg, "scout"))
        self.assertEqual(("", "default"),
                         dm.resolve_model_from_config({}, "builder"))
        # 角色条目空模型回落 default（不传 --model 语义）
        cfg2 = {"agents": {"default": {"model": "d"},
                           "roles": {"builder": {"model": ""}}}}
        self.assertEqual(("d", "default"), dm.resolve_model_from_config(cfg2, "builder"))

    def test_start_rejects_manual_model(self):
        code = dm.cmd_start(type("A", (), {
            "json": False, "role": "builder", "runtime": "qoder", "pane": "wX:p1",
            "model": "glm-5.3", "permission_mode": "", "name": None,
            "instance": None, "force": False})(), self.root)
        self.assertEqual(2, code)

    def test_start_rejects_manual_permission_mode(self):
        code = dm.cmd_start(type("A", (), {
            "json": False, "role": "builder", "runtime": "qoder", "pane": "wX:p1",
            "model": "", "permission_mode": "bypass_permissions", "name": None,
            "instance": None, "force": False})(), self.root)
        self.assertEqual(2, code)


class MessageFileArgumentTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.msg = self.base / "msg.md"
        self.msg.write_text("你好，按工单执行。\n", encoding="utf-8")

    def _args(self, **kw):
        return type("A", (), {"message_file": None, "message": None, **kw})()

    def test_inline_message_rejected_with_guidance(self):
        with self.assertRaises(dm.DispatchError) as ctx:
            dm.read_message_argument(self._args(message="内联文本"))
        self.assertEqual(1, ctx.exception.code)
        self.assertIn("文件路径", str(ctx.exception))

    def test_message_file_accepted(self):
        text = dm.read_message_argument(self._args(message_file=str(self.msg)))
        self.assertIn("你好，按工单执行。", text)

    def test_missing_file_rejected(self):
        with self.assertRaises(dm.DispatchError) as ctx:
            dm.read_message_argument(self._args(message_file="/nonexistent/x.md"))
        self.assertEqual(1, ctx.exception.code)

    def test_empty_rejected(self):
        with self.assertRaises(dm.DispatchError) as ctx:
            dm.read_message_argument(self._args())
        self.assertEqual(1, ctx.exception.code)


class CloseReconcileTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        registry = dm.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "proj-worker": {"role": "builder", "runtime": "qoder",
                            "pane": "wX:p9"},
        }), encoding="utf-8")
        self.calls: list[list[str]] = []
        self.agents: list[dict] = [{"name": "proj-worker", "pane_id": "wX:p9",
                                    "cwd": str(self.root)}]
        patch_target = "dispatch_t76c2.run_herdr"

        import unittest.mock as mock
        self._mock = mock.patch(patch_target, side_effect=self._herdr)
        self._mock.start()
        self.addCleanup(self._mock.stop)

    def _herdr(self, argv, **_kw):
        """run_herdr 语义：返回 stdout 字符串（json.loads 消费）。"""
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return json.dumps({"result": {"agents": self.agents}})
        return "{}"

    def _close(self, status: str, *extra: str) -> int:
        self.agents[0]["agent_status"] = status
        return dm.cmd_close(type("A", (), {
            "agent": "proj-worker", "json": False,
            "force": "--force" in extra, "reason": ""})(), self.root)

    def _log(self) -> list[dict]:
        path = dm.crew_dir(self.root) / "agent-log.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_close_working_rejected_rc2(self):
        self.assertEqual(2, self._close("working"))
        row = self._log()[-1]
        self.assertEqual("refused", row["result"])
        self.assertEqual("working", row["status"])
        self.assertIn("sender", row)

    def test_close_blocked_rejected_but_force_with_reason_passes(self):
        self.assertEqual(2, self._close("blocked"))
        self.agents[0]["agent_status"] = "blocked"
        code = dm.cmd_close(type("A", (), {
            "agent": "proj-worker", "json": False, "force": True,
            "reason": "人工确认：任务已由他人接管"})(), self.root)
        self.assertEqual(0, code)
        row = self._log()[-1]
        self.assertEqual("ok", row["result"])
        self.assertEqual("人工确认：任务已由他人接管", row["reason"])
        self.assertEqual("blocked", row["status"])

    def test_close_awaiting_approval_always_rejected(self):
        self.assertEqual(2, self._close("awaiting_approval"))

    def test_close_idle_ok_with_status_fields(self):
        self.assertEqual(0, self._close("idle"))
        row = self._log()[-1]
        self.assertEqual("ok", row["result"])
        self.assertEqual("idle", row["status"])
        self.assertIn("sender", row)


if __name__ == "__main__":
    unittest.main(verbosity=2)
