#!/usr/bin/env python3
"""2.2.28 FB-057①：fast_wake 差集边沿（S0 阻断 A1 修法）+ 探针过滤。

背景：旧实现是总数边沿（`prev_working > 0 and current_working == 0 and
bool(pending)`）——只覆盖「全部 worker 完成」，部分完成（如 adversary 完成
而 builder 在跑）静躺；且「且有待办」前提让无待办完成永远走 60 分钟兜底。

S0 裁定修法（不得改数）：
1. 总数边沿 → 差集边沿：guard 记上一轮 working 名单集合（herdr + claude
   runs 口径），`finished = prev_set - cur_set` 非空即触发，同刻多完成一次
   叫醒带全名单；
2. 无待办也触发（去 `and bool(pending)` 前提）；
3. is_probe_agent 纳入 fast_wake 的 working 集合过滤（探针结束不叫醒）；
4. 冷却沿用全局 wakeCooldownMinutes（默认 10 分钟，非新参数）；被冷却吞掉
   的边沿记 guard.log 一行；
5. 消息带完成名单 + 核收指引（查 inbox 回执/pane）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_2221_stuck_detect import (  # noqa: E402
    GuardHarness,
    cg,
    write_file,
)


class FastWakeDiffEdgeTest(GuardHarness):
    def test_partial_completion_triggers_fast_wake(self):
        """部分完成（另一 worker 仍在跑）也触发——S0 A1 决定性场景。"""
        self.write_fixture(pending=True)
        self.agents = [
            {"name": "worker-a", "agent_status": "working"},
            {"name": "worker-b", "agent_status": "working"},
        ]
        guard = self.make_guard()
        self.assertFalse(guard.run_once()["woke"])  # 轮1：记录 prev 集合

        self.agents = [
            {"name": "worker-a", "agent_status": "idle"},
            {"name": "worker-b", "agent_status": "working"},
        ]
        result = guard.run_once()
        self.assertTrue(result["woke"], "部分完成也必须触发快速叫醒")
        self.assertEqual(1, len(self.prompt_calls))
        self.assertIn("worker-a", self.prompt_calls[0][1])
        self.assertNotIn("worker-b", self.prompt_calls[0][1].split("刚完成")[-1])

    def test_fast_wake_without_pending(self):
        """无待办的完成边沿也触发（去「且有待办」前提）。"""
        self.write_fixture(pending=False)
        self.agents = [{"name": "worker-a", "agent_status": "working"}]
        guard = self.make_guard()
        self.assertFalse(guard.run_once()["woke"])

        self.agents = [{"name": "worker-a", "agent_status": "idle"}]
        result = guard.run_once()
        self.assertTrue(result["woke"], "无待办完成边沿必须叫醒核收")
        message = self.prompt_calls[0][1]
        self.assertIn("worker-a", message)
        self.assertIn("回执", message, "消息须带核收指引（查 inbox 回执）")

    def test_multi_completion_wakes_once_with_full_list(self):
        """同刻多完成：一次叫醒带全名单。"""
        self.write_fixture(pending=True)
        self.agents = [
            {"name": "worker-a", "agent_status": "working"},
            {"name": "worker-b", "agent_status": "working"},
        ]
        guard = self.make_guard()
        guard.run_once()

        self.agents = [
            {"name": "worker-a", "agent_status": "idle"},
            {"name": "worker-b", "agent_status": "done"},
        ]
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertIn("worker-a", self.prompt_calls[0][1])
        self.assertIn("worker-b", self.prompt_calls[0][1])

    def test_probe_agent_completion_does_not_wake(self):
        """探针 agent（s9-/probe-/test- 前缀）完成边沿不叫醒（S0 A1 并入修法）。"""
        self.write_fixture(pending=True)
        self.agents = [{"name": "s9-probe-worker", "agent_status": "working"}]
        guard = self.make_guard()
        guard.run_once()

        self.agents = [{"name": "s9-probe-worker", "agent_status": "idle"}]
        result = guard.run_once()
        self.assertFalse(result["woke"], "探针完成不得触发快速叫醒")
        self.assertEqual([], self.prompt_calls)

    def test_swallowed_edge_logged(self):
        """冷却吞掉的完成边沿记 guard.log 一行（S0 S3 顺带落实）。"""
        self.write_fixture(pending=True)
        self.agents = [{"name": "worker-a", "agent_status": "working"}]
        guard = self.make_guard()
        guard.run_once()
        guard.last_wake_at = self.now[0] - 60  # 冷却（默认 10 分钟）内

        self.agents = [{"name": "worker-a", "agent_status": "idle"}]
        self.assertFalse(guard.run_once()["woke"])
        self.assertTrue(
            any("完成边沿" in line for line in self.logs),
            f"被吞边沿必须记日志；logs={self.logs}")

    def test_claude_run_completion_triggers_fast_wake(self):
        """claude runs 注册口径同样进差集（S0 A1：两口径统一成立）。"""
        self.write_fixture(pending=True)
        self.agents = []
        runs_dir = self.crew / "runs"
        registry = runs_dir / "claude-1.json"
        write_file(registry, json.dumps({
            "pid": 424242, "role": "scout", "started_at": "",
            "out_dir": "/tmp/out"}))
        fake_pid_procs = {424242: f"something {cg.CLAUDE_PROCESS_MARKER} x"}

        def fake_cmdline(pid):
            return fake_pid_procs.get(pid)

        guard = self.make_guard()
        with mock.patch.object(cg, "pid_alive", return_value=True), \
                mock.patch.object(cg, "proc_cmdline", side_effect=fake_cmdline):
            guard.run_once()  # 轮1：claude run 计入 prev 集合
        self.assertTrue(guard.prev_working, "claude run 必须计入 working 集合")

        registry.unlink()  # run 结束（注册清理）
        with mock.patch.object(cg, "pid_alive", return_value=True), \
                mock.patch.object(cg, "proc_cmdline", side_effect=fake_cmdline):
            result = guard.run_once()
        self.assertTrue(result["woke"], "claude run 完成边沿必须触发快速叫醒")

    def test_no_completion_still_quiet(self):
        """回归：无完成边沿（worker 持续 working/idle 不变）不叫醒。"""
        self.write_fixture(pending=False)
        self.agents = [{"name": "worker-a", "agent_status": "idle"}]
        guard = self.make_guard()
        for _ in range(3):
            self.assertFalse(guard.run_once()["woke"])
            self.now[0] += 3600
        self.assertEqual([], self.prompt_calls)


if __name__ == "__main__":
    unittest.main()
