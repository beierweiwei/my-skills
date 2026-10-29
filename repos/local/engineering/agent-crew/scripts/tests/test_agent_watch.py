#!/usr/bin/env python3
"""agent-watch.py 状态 diff 回归测试：python3 scripts/tests/test_agent_watch.py
（或 python3 -m unittest discover -s scripts/tests）。"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

# 随迁：脚本在 skills 布局下位于本 tests 目录的上一级 scripts/。
_SPEC_PATH = Path(__file__).resolve().parent.parent / "agent-watch.py"
_spec = importlib.util.spec_from_file_location("agent_watch", _SPEC_PATH)
aw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(aw)


def agent(workspace: str, name: str, status: str = "working", tab: str = "t1") -> dict:
    return {
        "workspace_id": workspace,
        "name": name,
        "agent_status": status,
        "tab_id": f"{workspace}:{tab}",
        "pane_id": f"{workspace}:p1",
    }


class StateKeyTest(unittest.TestCase):
    def test_same_name_different_workspace_gets_distinct_key(self):
        a = aw.state_key(agent("wA", "p1"))
        b = aw.state_key(agent("wB", "p1"))
        self.assertEqual(a, "wA/p1")
        self.assertEqual(b, "wB/p1")
        self.assertNotEqual(a, b)

    def test_falls_back_to_pane_id_without_name(self):
        self.assertEqual(aw.state_key({"workspace_id": "wA", "pane_id": "wA:pR"}), "wA/wA:pR")


class DiffScopeTest(unittest.TestCase):
    def setUp(self):
        self.current_wq = {"wA/p1": "working", "wA/misc-runner": "working"}
        self.current_ws = {"wB/demo-p1": "working"}
        self.baseline = dict(self.current_wq)

    def test_alternating_workspaces_does_not_report_disappeared(self):
        # wA 建立基线 → wB 运行：不得把 wA 的 agent 报成已消失
        changes = aw.diff_states(self.current_ws, self.baseline, "wB", set())
        self.assertEqual(changes, [])

    def test_alternating_workspaces_keeps_each_baseline(self):
        merged = aw.merge_state(self.baseline, self.current_ws, "wB", set())
        self.assertEqual(
            merged, {"wA/p1": "working", "wA/misc-runner": "working", "wB/demo-p1": "working"}
        )
        # 回到 wA 仍无变化
        self.assertEqual(aw.diff_states(self.current_wq, merged, "wA", set()), [])

    def test_real_disappearance_in_scope_is_reported(self):
        previous = {"wA/p1": "working", "wA/gate-demo": "working"}
        changes = aw.diff_states({"wA/p1": "working"}, previous, "wA", set())
        self.assertEqual(changes, [("wA/gate-demo", "working", None)])
        merged = aw.merge_state(previous, {"wA/p1": "working"}, "wA", set())
        self.assertEqual(merged, {"wA/p1": "working"})

    def test_status_change_is_reported(self):
        previous = {"wA/p1": "idle"}
        changes = aw.diff_states({"wA/p1": "working"}, previous, "wA", set())
        self.assertEqual(changes, [("wA/p1", "idle", "working")])

    def test_initial_silent_by_default(self):
        self.assertEqual(aw.diff_states({"wA/p1": "working"}, {}, "wA", set()), [])
        self.assertEqual(
            aw.diff_states({"wA/p1": "working"}, {}, "wA", set(), notify_initial=True),
            [("wA/p1", None, "working")],
        )

    def test_agent_filter_scopes_disappearance(self):
        previous = {"wA/p1": "working", "wA/p2": "idle"}
        changes = aw.diff_states({"wA/p1": "working"}, previous, "wA", {"p1"})
        self.assertEqual(changes, [])

    def test_unfiltered_run_covers_all_workspaces(self):
        previous = {"wA/p1": "working", "wB/demo-p1": "working"}
        current = {"wA/p1": "working"}
        self.assertEqual(
            aw.diff_states(current, previous, None, set()), [("wB/demo-p1", "working", None)]
        )

    def test_legacy_keys_are_ignored_then_dropped(self):
        # 升级前旧格式（无 workspace 前缀）：不误报，写回时淘汰
        previous = {"p1": "working"}
        self.assertEqual(aw.diff_states({"wA/p1": "working"}, previous, "wA", set()), [])
        self.assertEqual(aw.merge_state(previous, {"wA/p1": "working"}, "wA", set()), {"wA/p1": "working"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
