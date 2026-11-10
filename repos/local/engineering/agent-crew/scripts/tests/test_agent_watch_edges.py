#!/usr/bin/env python3
"""agent-watch.py crew-v2 1.5 用例：pid 隔离状态文件 / flush / --edge-only。

覆盖：
① 未指定 --state-file 时默认路径含 pid（不同 pid 不同文件），显式 --state-file 原样生效；
② 所有输出 print(flush=True)（用记录 flush 的流验证无未刷新残留）；
③ --edge-only 只输出进入 working/done/blocked 或消失的边沿，过滤 working→idle 等噪声；
   默认模式输出保持原有形态（列表 + 状态变化）。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "agent-watch.py"
_spec = importlib.util.spec_from_file_location("agent_watch_edges", _SPEC_PATH)
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)


def agent(name: str = "p1", status: str = "working", workspace: str = "wA") -> dict:
    return {
        "workspace_id": workspace,
        "name": name,
        "agent_status": status,
        "tab_id": f"{workspace}:t1",
        "pane_id": f"{workspace}:p1",
    }


class FlushSpy(io.StringIO):
    """记录 write 后是否有未 flush 的残留（print(flush=True) 应每行刷新）。"""

    def __init__(self) -> None:
        super().__init__()
        self.pending = 0
        self.flushes = 0

    def write(self, text: str) -> int:
        self.pending += 1
        return super().write(text)

    def flush(self) -> None:
        if self.pending:
            self.pending = 0
            self.flushes += 1
        super().flush()


class StatePathTest(unittest.TestCase):
    """契约①：默认按 pid 隔离，显式路径优先。"""

    def test_default_state_path_contains_pid(self):
        path = aw.state_path_for("/repo/.git", None)
        self.assertEqual(path.parent, Path("/repo/.git"))
        self.assertIn(str(os.getpid()), path.name)

    def test_different_pids_get_different_state_files(self):
        p1 = aw.state_path_for("/repo/.git", None, pid=1111)
        p2 = aw.state_path_for("/repo/.git", None, pid=2222)
        self.assertNotEqual(p1, p2)
        self.assertIn("1111", p1.name)
        self.assertIn("2222", p2.name)

    def test_explicit_state_file_wins(self):
        path = aw.state_path_for("/repo/.git", "/tmp/aw-wX.state", pid=1234)
        self.assertEqual(path, Path("/tmp/aw-wX.state"))


class EdgeFilterTest(unittest.TestCase):
    """契约③：边沿过滤口径（纯函数）。"""

    def test_keeps_entering_edge_statuses_and_disappearance(self):
        changes = [
            ("a", "idle", "working"),
            ("b", "idle", "done"),
            ("c", "idle", "blocked"),
            ("d", "working", None),
        ]
        self.assertEqual(aw.filter_edge_changes(changes), changes)

    def test_filters_leaving_working_and_unknown_noise(self):
        changes = [
            ("a", "working", "idle"),
            ("b", "done", "unknown"),
            ("c", None, "idle"),
            ("d", "working", "working"),
        ]
        self.assertEqual(aw.filter_edge_changes(changes), [])

    def test_initial_edge_kept_when_visible(self):
        changes = aw.diff_states({"wA/p1": "working"}, {}, "wA", set(), notify_initial=True)
        self.assertEqual(aw.filter_edge_changes(changes), [("wA/p1", None, "working")])


class MainBehaviorTest(unittest.TestCase):
    """契约②③：main 集成（显式状态文件 / flush / edge-only 输出形态）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, argv: list[str], agents: list[dict]) -> tuple[int, FlushSpy]:
        spy = FlushSpy()
        err = io.StringIO()
        with mock.patch.object(aw, "herdr_agents", return_value=agents):
            with contextlib.redirect_stdout(spy), contextlib.redirect_stderr(err):
                rc = aw.main(argv)
        self.assertEqual(spy.pending, 0, "存在未 flush 的输出")
        return rc, spy

    def test_explicit_state_file_used_and_output_flushed(self):
        state = self.tmp / "custom.state"
        rc, spy = self._run(["--once", "--state-file", str(state)], [agent(status="working")])
        self.assertEqual(rc, 0)
        self.assertTrue(state.is_file())
        self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {"wA/p1": "working"})
        self.assertGreaterEqual(spy.flushes, 2)  # 摘要行 + 每 agent 行均 flush
        self.assertIn("agents=1", spy.getvalue())

    def test_default_state_file_is_pid_scoped(self):
        common = self.tmp / ".git"
        common.mkdir()

        class Proc:
            returncode = 0
            stdout = str(common)
            stderr = ""

        with mock.patch.object(aw.subprocess, "run", return_value=Proc()):
            rc, _spy = self._run(["--once"], [])
        self.assertEqual(rc, 0)
        self.assertTrue((common / f"agent-watch.{os.getpid()}.state").is_file())

    def test_edge_only_reports_entering_working(self):
        state = self.tmp / "edge.state"
        state.write_text(json.dumps({"wA/p1": "idle"}), encoding="utf-8")
        rc, spy = self._run(
            ["--once", "--edge-only", "--state-file", str(state)], [agent(status="working")]
        )
        self.assertEqual(rc, 0)
        self.assertIn("idle → working", spy.getvalue())
        self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {"wA/p1": "working"})

    def test_edge_only_suppresses_leaving_working_and_routine_listing(self):
        state = self.tmp / "edge.state"
        state.write_text(json.dumps({"wA/p1": "working"}), encoding="utf-8")
        rc, spy = self._run(
            ["--once", "--edge-only", "--state-file", str(state)], [agent(status="idle")]
        )
        self.assertEqual(rc, 0)
        self.assertEqual(spy.getvalue(), "")  # 无列表、无噪声变化

    def test_edge_only_reports_disappearance(self):
        state = self.tmp / "edge.state"
        rc, _spy = self._run(["--once", "--state-file", str(state)], [agent(status="working")])
        self.assertEqual(rc, 0)
        rc, spy = self._run(["--once", "--edge-only", "--state-file", str(state)], [])
        self.assertEqual(rc, 0)
        self.assertIn("已消失", spy.getvalue())

    def test_default_mode_keeps_listing_and_status_changes(self):
        state = self.tmp / "default.state"
        state.write_text(json.dumps({"wA/p1": "working"}), encoding="utf-8")
        rc, spy = self._run(["--once", "--state-file", str(state)], [agent(status="idle")])
        self.assertEqual(rc, 0)
        out = spy.getvalue()
        self.assertIn("agents=1", out)  # 默认保留例行列表
        self.assertIn("working → idle", out)  # 默认不过滤任何变化

    def test_default_initial_state_still_silent(self):
        state = self.tmp / "default.state"
        rc, spy = self._run(["--once", "--state-file", str(state)], [agent(status="working")])
        self.assertEqual(rc, 0)
        self.assertNotIn("状态变化", spy.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
