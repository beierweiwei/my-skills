#!/usr/bin/env python3
"""2.2.28 FB-052：notify 对 done 目标不再判「目标不可用」拒投。

背景（FB-052）：`dispatch.py notify --workspace/--role` 解析到 done 状态的
目标时按「目标不可用」rc=1 拒投。done/idle=待命≠不可达：one-shot worker
收口转 done 后 helm 核收类通知投不过去，收件方 inbox 也没落盘。

修复契约：
1. resolve_notify_target 接受 done 为可选目标（与 idle/working 同列活跃态；
   blocked 与缺失/空白/unknown 仍拒绝）；
2. 目标 done 时跳过 pane 注入（herdr agent prompt 不调用），通知至少落
   收件方 inbox 兜底（delivery=inbox_fallback，prompt_skipped=done）；
3. 落盘失败时 result=degraded（既有口径不变）。
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
_spec = importlib.util.spec_from_file_location("dispatch_fb052", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_fb052"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)


class Fb052NotifyDoneTargetTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj-a"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
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

    def dispatch_main(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out):
                code = dispatch_mod.main(["--root", str(self.root), *args])
        return code, out.getvalue()

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls if call[:3] == ["herdr", "agent", "prompt"]]

    def agent_log(self) -> list[dict]:
        path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        if not path.exists():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_done_target_resolved_and_inbox_fallback_without_prompt(self):
        """role 解析到 done 目标：rc=0、落收件方 inbox、不注入 pane。"""
        self.agents = [{"name": "helm-x-4", "workspace_id": "w1",
                        "agent_status": "done", "cwd": str(self.root)}]
        code, out = self.dispatch_main(
            "notify", "--workspace", "w1", "--role", "helm",
            "--message", "切片完成，请核收", "--json")
        self.assertEqual(0, code)
        self.assertEqual([], self.prompt_calls(), "done 目标不得 pane 注入")
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertEqual("done", payload["prompt_skipped"])
        self.assertEqual("ok", payload["result"])
        self.assertEqual("written", payload["inbox"]["status"])
        row = self.agent_log()[-1]
        self.assertEqual("notify", row["action"])
        self.assertEqual("helm-x-4", row["target"])
        self.assertEqual("done", row["prompt_skipped"])
        # 通知正文落收件方 inbox 文件（R1 文件真源）
        inbox = self.root / ".git" / "crew" / "inbox"
        files = list(inbox.glob("notify-*.json"))
        self.assertEqual(1, len(files))
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertIn("切片完成", data["message"])

    def test_done_literal_agent_skips_prompt_and_falls_to_inbox(self):
        """--agent 直发 done 目标：同样跳过注入、落 inbox 兜底。"""
        self.agents = [{"name": "t57-builder", "workspace_id": "w1",
                        "agent_status": "done", "cwd": str(self.root)}]
        code, out = self.dispatch_main(
            "notify", "--agent", "t57-builder",
            "--message", "请核收", "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        self.assertEqual([], self.prompt_calls())
        payload = json.loads(out)
        self.assertEqual("inbox_fallback", payload["delivery"])
        self.assertEqual("done", payload["prompt_skipped"])
        self.assertEqual("written", payload["inbox"]["status"])

    def test_done_status_accepted_by_resolver(self):
        """resolve_notify_target 接受 done（与 idle/working 同列）。"""
        self.agents = [{"name": "helm-x-4", "workspace_id": "w1",
                        "agent_status": "Done"}]
        target = dispatch_mod.resolve_notify_target(self.agents, "w1", "helm")
        self.assertEqual("helm-x-4", target)


if __name__ == "__main__":
    unittest.main()
