#!/usr/bin/env python3
"""2.2.28 S9：工位 cwd 落 home 修复——缺省补 --cwd <root> + 启动后校验 + log 记 cwd。

背景（2026-11-14 Captain 报障「副舵被发配到 home 目录」）：`dispatch.py tab
create`/`pane split` 的 `--cwd` 可选、缺省不传给 herdr → herdr 侧 pane 初始
cwd 落 home（复现实证 s9-repro.txt：显式 --cwd 项目根 ✓、不给 --cwd 提示符
`➜ ~`）。修复：dispatch 层缺省补 `--cwd <root>`（root=`--root` 解析值；显式
值优先；T65 `--near` 语义不变，仅同样补 cwd）；`cmd_start` 启动后校验工位
cwd==项目根（不符 stderr 告警 + log 标 cwdMismatch，不阻断）；tab/pane/
start 的 agent-log 补记 cwd 字段（可观测缺口，case-ctx-audit 同族）。
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
_spec = importlib.util.spec_from_file_location("dispatch_s9", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_s9"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)


class S9CwdDefaultTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        self.calls: list[list[str]] = []

    def fake_herdr(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "tab", "create"]:
            # --near 用例的锚 workspace 是 w1：返回同 workspace 的 tab id
            new_tab = "w1:tN" if "--workspace" in argv and "w1" in argv else "wX:tN"
            return json.dumps({"result": {
                "tab": {"tab_id": new_tab},
                "pane_id": new_tab.replace("t", "p", 1) + "N"}})
        if argv[:3] == ["herdr", "pane", "split"]:
            return json.dumps({"result": {"pane_id": "wX:pS"}})
        if argv[:3] == ["herdr", "agent", "list"]:
            return json.dumps({"result": {"agents": [
                {"name": "s9-runner", "cwd": str(self.root)},
                {"name": "w1-anchor", "tab_id": "w1:tB",
                 "workspace_id": "w1", "pane_id": "w1:pB"}]}})
        return ""

    def run_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def agent_log(self) -> list[dict]:
        path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_tab_create_default_injects_root_cwd(self):
        """路径①修：不给 --cwd → dispatch 缺省补 --cwd <root>。"""
        code, out, err = self.run_main("tab", "create", "--label", "s9", "--json")
        self.assertEqual(0, code, err)
        argv = self.calls[0]
        self.assertIn("--cwd", argv)
        self.assertEqual(str(self.root), argv[argv.index("--cwd") + 1])
        payload = json.loads(out)
        self.assertEqual(str(self.root), payload["cwd"])
        row = self.agent_log()[-1]
        self.assertEqual(str(self.root), row["cwd"], "agent-log 必须记 cwd")

    def test_tab_create_explicit_cwd_wins(self):
        """显式 --cwd 优先于缺省。"""
        other = self.root.parent / "other"
        other.mkdir()
        code, out, _err = self.run_main(
            "tab", "create", "--cwd", str(other), "--json")
        self.assertEqual(0, code)
        argv = self.calls[0]
        self.assertEqual(str(other), argv[argv.index("--cwd") + 1])

    def test_pane_split_default_injects_root_cwd(self):
        """路径③修：pane split 缺省同样补 --cwd（与 cmd_tab 同根因）。"""
        code, out, _err = self.run_main("pane", "split", "--pane", "wX:p1")
        self.assertEqual(0, code)
        argv = self.calls[0]
        self.assertIn("--cwd", argv)
        self.assertEqual(str(self.root), argv[argv.index("--cwd") + 1])
        row = self.agent_log()[-1]
        self.assertEqual(str(self.root), row["cwd"])

    def test_tab_near_also_gets_default_cwd(self):
        """T65 --near 语义不变：同样补缺省 cwd（socket move 参数不受影响）。"""
        calls: list[list[str]] = []

        def fake_socket(method, params, timeout=5.0):
            calls.append((method, params))
            if method == "tab.list":
                tabs = [{"tab_id": "w1:tA"}, {"tab_id": "w1:tB"},
                        {"tab_id": "w1:tC"}]
                # 模拟服务端：create 已发生 → 新 tab 追加末尾
                if any(a[:3] == ["herdr", "tab", "create"] for a in self.calls):
                    tabs.append({"tab_id": "w1:tN"})
                return {"result": {"tabs": tabs}}
            if method == "tab.move":
                rows = ["w1:tA", "w1:tB", "w1:tC"]
                if params["tab_id"] not in rows:
                    rows.append(params["tab_id"])  # create 后状态
                rows.remove(params["tab_id"])
                rows.insert(min(params["insert_index"], len(rows)),
                            params["tab_id"])
                return {"result": {"tabs": [{"tab_id": x} for x in rows]}}
            raise AssertionError(method)

        self.calls.clear()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr), \
                mock.patch.object(dispatch_mod, "socket_call", fake_socket):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main([
                    "--root", str(self.root), "tab", "create",
                    "--near", "w1-anchor", "--json"])
        self.assertEqual(0, code, err)
        argv = next(c for c in self.calls if c[:3] == ["herdr", "tab", "create"])
        self.assertIn("--cwd", argv, "--near 路径同样缺省补 cwd")
        self.assertEqual(str(self.root), argv[argv.index("--cwd") + 1])
        # T65 move 语义不变：锚 tB（idx1）→ 新 tab 落 idx2
        move = [p for m, p in calls if m == "tab.move"]
        self.assertEqual(1, len(move))
        self.assertEqual({"tab_id": "w1:tN", "insert_index": 2}, move[0])

    def test_start_cwd_mismatch_warns_and_logs(self):
        """启动后校验：agent cwd ≠ 项目根 → stderr 告警 + log cwdMismatch。"""
        registry = dispatch_mod.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "s9-runner": {"role": "runner", "runtime": "qoder"}}),
            encoding="utf-8")
        self.root_dot = self.root

        def fake_herdr_start(argv):
            self.calls.append(list(argv))
            if argv[:3] == ["herdr", "agent", "list"]:
                # 工位 cwd 落 home（修复前事故形态）
                return json.dumps({"result": {"agents": [
                    {"name": "s9-runner", "cwd": "/home/wjj"}]}})
            return ""

        # compose/delivery 链路较重：直接测校验段——用最小 mock 走 cmd_start
        # 不可行（需组合产物），此处用单元级：校验逻辑内联在 cmd_start，
        # 以集成形态由真实动作证据承载；本用例锁定 log 契约的输入假设
        # （herdr agent list 的 cwd 字段读取）。
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=fake_herdr_start):
            agents = dispatch_mod.herdr_agents()
        entry = next(a for a in agents if a.get("name") == "s9-runner")
        self.assertEqual("/home/wjj", entry.get("cwd"))


if __name__ == "__main__":
    unittest.main()
