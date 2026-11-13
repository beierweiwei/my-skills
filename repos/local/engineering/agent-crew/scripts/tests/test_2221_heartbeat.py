#!/usr/bin/env python3
"""2.2.21 修法 C：pending 真 idle 周期心跳（与 B/G2 去重）回归。

覆盖：
- pending + 真 idle 按 guard.pendingHeartbeatMinutes 重复叫醒（默认 15 分钟）；
- pending + working 不叫醒（不得引入「working 也催」）；
- pending + stuck 只走 B（awaiting_approval）一条，不叠加心跳；
- 无 pending 不叫醒；
- 快速叫醒仍受 guard.wakeCooldownMinutes 冷却；
- config 夹取/坏值回退。

运行：uv run --no-project --with pytest python3 -m pytest scripts/tests/test_2221_heartbeat.py -q
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_2221_stuck_detect import (  # noqa: E402
    GuardHarness,
    approval_entry,
    cg,
)


class HeartbeatTest(GuardHarness):
    def test_pending_idle_repeats_at_configured_heartbeat(self):
        self.write_fixture(pending=True)
        self.write_guard_config(wakeCooldownMinutes=1, pendingHeartbeatMinutes=2)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        self.assertFalse(guard.run_once()["woke"])  # G2 首轮防抖
        self.assertTrue(guard.run_once()["woke"])   # 连续 2 轮叫醒
        self.assertEqual(1, len(self.prompt_calls))

        self.now[0] += 61  # 冷却已过，心跳未到
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertTrue(any("心跳未到间隔" in line for line in self.logs))

        self.now[0] += 61  # 距上次心跳 ≥2 分钟
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

    def test_default_heartbeat_is_15_minutes(self):
        self.write_fixture(pending=True)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        self.assertFalse(guard.run_once()["woke"])
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        self.now[0] += 601  # 10 分钟冷却已过，但默认心跳 15 分钟未到
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        self.now[0] += 301  # 累计 902 秒
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

    def test_pending_working_never_wakes(self):
        self.write_fixture(pending=True)
        self.write_guard_config(wakeCooldownMinutes=1, pendingHeartbeatMinutes=1)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard = self.make_guard()

        for _ in range(3):
            self.assertFalse(guard.run_once()["woke"])
            self.now[0] += 3600
        self.assertEqual([], self.prompt_calls)

    def test_pending_and_stuck_wakes_only_via_b_channel(self):
        self.write_fixture(pending=True)
        self.write_guard_config(wakeCooldownMinutes=1, pendingHeartbeatMinutes=1)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.approvals = [approval_entry(age_seconds=16 * 60)]
        guard = self.make_guard()

        first = guard.run_once()
        self.assertTrue(first["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertIn("等待审批", first["reason"])
        self.assertNotIn("无在跑 worker", first["reason"])

        # 心跳 1 分钟已过，但 B 的 repeat 默认 30 分钟：本轮不得叠加心跳叫醒
        self.now[0] += 120
        second = guard.run_once()
        self.assertFalse(second["woke"])
        self.assertEqual(1, len(self.prompt_calls))

    def test_no_pending_never_wakes(self):
        self.write_fixture(pending=False)
        self.write_guard_config(wakeCooldownMinutes=1, pendingHeartbeatMinutes=1)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        for _ in range(4):
            self.assertFalse(guard.run_once()["woke"])
            self.now[0] += 3600
        self.assertEqual([], self.prompt_calls)

    def test_fast_wake_still_respects_wake_cooldown(self):
        self.write_fixture(pending=True)
        self.write_guard_config(wakeCooldownMinutes=10, pendingHeartbeatMinutes=15)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard = self.make_guard()
        self.assertFalse(guard.run_once()["woke"])  # 记录 prev_working=1
        guard.last_wake_at = self.now[0] - 60

        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("冷却中" in line for line in self.logs))

        # 冷却已过的新一轮：快速叫醒语义不回归
        self.now[0] += 1000
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard2 = self.make_guard()
        self.assertFalse(guard2.run_once()["woke"])  # 记录 prev_working=1
        guard2.last_wake_at = self.now[0] - 601
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        self.assertTrue(guard2.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertIn("上一轮 1 个 working 已转 idle", self.prompt_calls[0][1])

    def test_fast_wake_not_suppressed_by_heartbeat_interval(self):
        self.write_fixture(pending=True)
        self.write_guard_config(wakeCooldownMinutes=1, pendingHeartbeatMinutes=60)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        self.assertFalse(guard.run_once()["woke"])
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        # 心跳 60 分钟未到，但 worker 先 working 再 idle：快速叫醒只受冷却约束。
        self.now[0] += 120
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.assertFalse(guard.run_once()["woke"])
        self.now[0] += 120
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))
        self.assertIn("上一轮 1 个 working 已转 idle", self.prompt_calls[-1][1])

    def test_pending_heartbeat_config_clamps(self):
        defaults = cg.load_config(self.root)["guard"]
        self.assertEqual(15, defaults["pendingHeartbeatMinutes"])
        self.assertEqual(10, defaults["wakeCooldownMinutes"])
        for value, expected in ((0, 1), (999, 240), ("x", 15), (True, 15), (7, 7)):
            with self.subTest(value=value):
                self.write_guard_config(pendingHeartbeatMinutes=value)
                self.assertEqual(
                    expected,
                    cg.load_config(self.root)["guard"]["pendingHeartbeatMinutes"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
