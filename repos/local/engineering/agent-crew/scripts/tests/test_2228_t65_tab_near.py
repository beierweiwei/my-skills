#!/usr/bin/env python3
"""2.2.28 T65：`dispatch.py tab create --near <agent>` 就近建 tab。

背景（T65，调查报告 `.scratch/herdr-tab-probe/报告.md`）：herdr CLI 无 tab
重排命令；socket API 原生 `tab.move`（NDJSON unix socket，insert_index 为
「按移动前列表的前插索引」，source 在 insert 右侧时最终位置=insert_index）。
新 tab 只能追加末尾，随后 move 定位（两步、非原子）。

契约：
1. `--near <agent>`：以目标 agent 所在 tab 为锚，新 tab 建在其后一位
   （anchor 末尾时新 tab 天然就位、不调 move）；
2. socket 层可注入（夹具 mock socket_call），编排逻辑可测；
3. agent 不在 list → rc=1；缺 tab_id/workspace_id → rc=3；
   socket 错误（tab_move_failed/tab_not_found）→ rc=3 显式报错；
4. move 前重取 tab.list（防 create 并发漂移，报告 3.4 建议）；
5. agent-log 记 op=create 与 near 字段。
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
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_t65", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_t65"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)


def tab_row(tab_id: str) -> dict:
    return {"tab_id": tab_id, "label": tab_id}


class TabNearTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        self.socket_calls: list[tuple[str, dict]] = []
        self.herdr_calls: list[list[str]] = []
        self.tabs = [tab_row("w1:tA"), tab_row("w1:tB"), tab_row("w1:tC")]
        self.agents = [{"name": "patch-2228-builder", "tab_id": "w1:tB",
                        "workspace_id": "w1", "pane_id": "w1:p1"}]
        self.create_output = json.dumps({"result": {"tab_id": "w1:tD",
                                                    "pane_id": "w1:p9"}})

    def fake_socket_call(self, method: str, params: dict, timeout: float = 5.0) -> dict:
        self.socket_calls.append((method, params))
        if method == "tab.list":
            return {"result": {"type": "tab_list", "tabs": list(self.tabs)}}
        if method == "tab.move":
            tab_id = params["tab_id"]
            insert = params["insert_index"]
            rows = [t for t in self.tabs if t.get("tab_id") != tab_id]
            rows.insert(min(insert, len(rows)), tab_row(tab_id))
            self.tabs = rows
            return {"result": {"type": "tab_list", "tabs": list(self.tabs)}}
        raise AssertionError(f"意外 socket 方法: {method}")

    def fake_run_herdr(self, argv: list[str]) -> str:
        self.herdr_calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return json.dumps({"result": {"agents": self.agents}})
        if argv[:3] == ["herdr", "tab", "create"]:
            created = json.loads(self.create_output)["result"]
            self.tabs.append(tab_row(created["tab_id"]))  # 服务端语义：create 追加末尾
            return self.create_output
        return ""

    def dispatch_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch_mod, "socket_call", self.fake_socket_call), \
                mock.patch.object(dispatch_mod, "run_herdr", self.fake_run_herdr), \
                redirect_stdout(out), redirect_stderr(err):
            code = dispatch_mod.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def test_creates_after_anchor_tab(self):
        """锚 w1:tB（下标 1）→ 新 tab 最终位置 2（其后一位）。"""
        code, out, err = self.dispatch_main(
            "tab", "create", "--near", "patch-2228-builder",
            "--label", "t65", "--no-focus", "--json")
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual("w1:tD", payload["tab"])
        self.assertEqual("patch-2228-builder", payload["near"])
        self.assertEqual("w1:tB", payload["anchorTab"])
        self.assertEqual(["w1:tA", "w1:tB", "w1:tD", "w1:tC"],
                         [t["tab_id"] for t in self.tabs])
        moves = [params for method, params in self.socket_calls if method == "tab.move"]
        self.assertEqual(1, len(moves))
        self.assertEqual({"tab_id": "w1:tD", "insert_index": 2}, moves[0])
        log_path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        row = json.loads(log_path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual("create", row["op"])
        self.assertEqual("patch-2228-builder", row["near"])

    def test_anchor_at_tail_skips_move(self):
        """锚在末尾：新 tab 天然就位，不调 tab.move。"""
        self.agents = [{"name": "patch-2228-builder", "tab_id": "w1:tC",
                        "workspace_id": "w1"}]
        code, out, err = self.dispatch_main(
            "tab", "create", "--near", "patch-2228-builder", "--json")
        self.assertEqual(0, code, err)
        moves = [params for method, params in self.socket_calls if method == "tab.move"]
        self.assertEqual([], moves, "锚在末尾不得调 move")
        self.assertEqual(["w1:tA", "w1:tB", "w1:tC", "w1:tD"],
                         [t["tab_id"] for t in self.tabs])

    def test_unknown_agent_rejected(self):
        code, _, err = self.dispatch_main(
            "tab", "create", "--near", "ghost-agent", "--json")
        self.assertEqual(1, code)
        self.assertIn("ghost-agent", err)

    def test_agent_missing_tab_id_rejected(self):
        self.agents = [{"name": "patch-2228-builder", "workspace_id": "w1"}]
        code, _, err = self.dispatch_main(
            "tab", "create", "--near", "patch-2228-builder", "--json")
        self.assertEqual(3, code)
        self.assertIn("tab_id", err)

    def test_socket_error_surfaces(self):
        def broken_socket(method, params, timeout=5.0):
            if method == "tab.move":
                raise dispatch_mod.DispatchError(
                    "herdr socket tab.move 失败：tab_move_failed", 3)
            return self.fake_socket_call(method, params, timeout)

        with mock.patch.object(dispatch_mod, "socket_call", broken_socket), \
                mock.patch.object(dispatch_mod, "run_herdr", self.fake_run_herdr):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main([
                    "--root", str(self.root), "tab", "create",
                    "--near", "patch-2228-builder", "--json"])
        self.assertEqual(3, code)
        self.assertIn("tab_move_failed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
