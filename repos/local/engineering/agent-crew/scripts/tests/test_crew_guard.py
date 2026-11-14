#!/usr/bin/env python3
"""crew-guard.py 值守逻辑回归测试。
（python3 scripts/tests/test_crew_guard.py 或 python3 -m unittest discover -s scripts/tests）"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "crew-guard.py"
_spec = importlib.util.spec_from_file_location("crew_guard", _SPEC_PATH)
cg = importlib.util.module_from_spec(_spec)
sys.modules["crew_guard"] = cg  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(cg)


STREAM_PLAN = """# Task Plan: Flow A

## Next Step

- [ ] 不在 Phase 里的待办

## Phases

### Phase 1: 进行中
- owner: builder
- [x] 已完成项
- [ ] 待办A
- [ ] ⏸ 待拍板
- [ ] 待 Captain 的项
- [ ] 留 Captain 的项
- **Status:** in_progress

### Phase 2: 已完成
- [x] 做完
- [ ] 残留待办B
- **Status:** complete
"""

PORTFOLIO_PLAN = """# Task Plan: 总览

## Phases

### Phase 1: 流 A
- plan: `.planning/flow-a/task_plan.md`
- owner: helm
- [ ] 流A首个切片
- **Status:** in_progress

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 可执行任务 | P1 | - | - |
| T2 | 待船长任务 | P1 | T1 | 待 C1 |
| T3 | 已阻塞任务 | P1 | - | blocked |
| T4 | 留船长任务 | P1 | - | 留 Captain |
| C1 | 待拍板 | P1 | - | - |
"""

BACKLOG_DONE_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 中文已完成 | P1 | - | 已完成 |
| T2 | 英文 done | P1 | - | done |
| T3 | 英文 Complete | P1 | - | Complete |
| T4 | 英文 completed | P1 | - | completed |
"""

BACKLOG_WAITING_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 待 Captain | P1 | - | 待 Captain |
| T2 | 留 Captain | P1 | - | 留 Captain |
| T3 | 待 C1 | P1 | - | 待 C1 |
"""

BACKLOG_MIXED_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 未完成主线 | P1 | - | - |
| T2 | 待拍板 | P1 | - | 待 C1 |
| T3 | 已完成任务 | P1 | - | 已完成 |
| T4 | 边界词 | P1 | - | incomplete |
| T5 | 中文未完成 | P1 | - | 未完成 |
"""

RESIDUAL_ONLY_STREAM = """# Task Plan: Done

## Phases

### Phase 1: 完成
- [x] 做完
- [ ] 残留待办B
- **Status:** complete
"""

EMPTY_STREAM = """# Task Plan: Empty

## Phases
"""

EMPTY_PORTFOLIO = """# Task Plan: Empty

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
"""


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


class GuardTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.crew = self.base / "crew"
        self.now = [0.0]
        self.agents: list[dict] = []
        self.prompt_calls: list[tuple[str, str]] = []
        self.notify_calls: list[tuple[str, str]] = []
        self.status_calls: list[str] = []
        self.rotate_calls: list[Path] = []
        self.rotate_result = (0, "条目：15 → 保留 10，移出 5")
        self.logs: list[str] = []
        self.prompt_ok = True
        self.status_result = (0, "看板新鲜")
        self.pane_outputs: dict[str, str] = {}
        self.pane_reads: list[tuple[str, int]] = []
        self.panes: list[dict] = []
        self.dispatch_calls: list[list[str]] = []
        self.dispatch_override = None

    def fake_pane_read(self, pane: str, lines: int) -> str:
        self.pane_reads.append((pane, lines))
        return self.pane_outputs.get(pane, "")

    @staticmethod
    def agent_entry(agent: dict) -> dict:
        """T37：聚焦守卫按 pane_id 查 pane list；夹具为缺省 agent 补稳定 pane_id。"""
        entry = dict(agent)
        entry.setdefault("pane_id", f"pane-{entry.get('name', 'agent')}")
        return entry

    def pane_entries(self) -> list[dict]:
        if self.panes:
            return self.panes
        return [
            {"pane_id": self.agent_entry(agent)["pane_id"],
             "focused": bool(agent.get("focused"))}
            for agent in self.agents
        ]

    def fake_dispatch(self, root: Path, args: list[str]) -> tuple[int, str, str]:
        self.dispatch_calls.append(list(args))
        if self.dispatch_override is not None:
            result = self.dispatch_override(args)
            if result is not None:
                return result
        if args[:2] == ["tab", "create"]:
            return 0, json.dumps({"tab": "wX:tNEW", "pane": "wX:pNEW"}), ""
        if args[:1] == ["start"]:
            return 0, json.dumps({"agent": args[args.index("--name") + 1]}), ""
        return 0, "", ""

    def handoff_fields(self) -> dict[str, str]:
        text = (self.crew / "guard.handoff").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def write_fixture(
        self,
        stream: str = STREAM_PLAN,
        portfolio: str = PORTFOLIO_PLAN,
        config: dict | None = None,
    ) -> None:
        write_file(self.root / ".planning" / "flow-a" / "task_plan.md", stream)
        write_file(self.root / ".planning" / "portfolio" / "task_plan.md", portfolio)
        if config is not None:
            write_file(
                self.root / ".planning" / "orchestration.json",
                json.dumps(config, ensure_ascii=False),
            )

    def make_guard(self, **overrides) -> "cg.Guard":
        kwargs = dict(
            root=self.root,
            crew_dir=self.crew,
            helm="helm-1",
            interval=120,
            clock=lambda: self.now[0],
            list_agents=lambda: [self.agent_entry(agent) for agent in self.agents],
            list_panes=self.pane_entries,
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or self.prompt_ok
            ),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True
            ),
            run_status=lambda root, flag: (
                self.status_calls.append(flag) or self.status_result
            ),
            run_rotate=lambda root: (
                self.rotate_calls.append(root) or self.rotate_result
            ),
            pane_read=self.fake_pane_read,
            dispatch=self.fake_dispatch,
            log_fn=self.logs.append,
            # 2.2.21：审批等待检测默认关闭真实日志扫描，单测各自注入。
            stuck_detect=lambda agents, now: [],
        )
        kwargs.update(overrides)
        return cg.Guard(**kwargs)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cg.main(argv)
        return code, out.getvalue(), err.getvalue()


class TodoScanTest(GuardTestBase):
    def test_phase_and_backlog_rules(self):
        self.write_fixture()
        pending, residual, _ = cg.scan_todos(self.root)
        texts = [todo.text for todo in pending]

        self.assertIn("待办A", texts)
        self.assertIn("流A首个切片", texts)
        self.assertIn("T1 可执行任务", texts)
        self.assertNotIn("不在 Phase 里的待办", texts)
        self.assertFalse(any("待拍板" in text for text in texts))
        self.assertFalse(any("待 Captain 的项" in text for text in texts))
        self.assertFalse(any("留 Captain 的项" in text for text in texts))
        self.assertFalse(any(text.startswith("T2") for text in texts))
        self.assertFalse(any(text.startswith("T3") for text in texts))
        self.assertFalse(any(text.startswith("T4") for text in texts))
        self.assertFalse(any(text.startswith("C1") for text in texts))
        self.assertEqual([todo.text for todo in residual], ["残留待办B"])

    def test_backlog_completed_rows_are_skipped(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=BACKLOG_DONE_PORTFOLIO)
        pending, residual, _ = cg.scan_todos(self.root)
        self.assertEqual(pending, [])
        self.assertEqual(residual, [])

    def test_backlog_waiting_captain_rows_are_skipped(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=BACKLOG_WAITING_PORTFOLIO)
        pending, residual, _ = cg.scan_todos(self.root)
        self.assertEqual(pending, [])
        self.assertEqual(residual, [])

    def test_comment_examples_are_ignored(self):
        self.write_fixture(
            stream=(
                EMPTY_STREAM
                + "\n<!--\n"
                + "### Phase 9: 注释示例\n- [ ] 注释里的待办\n- **Status:** pending\n"
                + "-->\n"
            )
        )
        pending, _, _ = cg.scan_todos(self.root)
        self.assertNotIn("注释里的待办", [todo.text for todo in pending])


class WakeConditionTest(GuardTestBase):
    def test_backlog_unfinished_rows_still_wake(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=BACKLOG_MIXED_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        self.assertFalse(guard.run_once()["woke"])
        self.assertTrue(guard.run_once()["woke"])
        message = self.prompt_calls[0][1]
        self.assertIn("T1 未完成主线", message)
        self.assertIn("T4 边界词", message)
        self.assertIn("T5 中文未完成", message)
        self.assertNotIn("T2", message)
        self.assertNotIn("T3", message)

    def test_working_to_idle_with_pending_wakes_immediately(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard = self.make_guard()

        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual(first["consecutive"], 0)
        self.assertEqual(guard.prev_working, {"worker-1"})

        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertEqual(second["consecutive"], 1)
        self.assertIn("worker 完成待核收：worker-1", self.prompt_calls[0][1])
        self.assertIn("T1 可执行任务", self.prompt_calls[0][1])

        # 冷却内不再重复叫醒
        third = guard.run_once()
        self.assertFalse(third["woke"])
        self.assertEqual(len(self.prompt_calls), 1)
        self.assertTrue(any("冷却中" in line for line in self.logs))

    def test_working_to_idle_without_pending_wakes_receipt_check(self):
        """FB-057①：无待办的完成边沿也叫醒核收（旧契约「不叫醒」已废除）。"""
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard = self.make_guard()
        self.assertFalse(guard.run_once()["woke"])

        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertIn("worker 完成待核收：worker-1", self.prompt_calls[0][1])
        self.assertIn("回执", self.prompt_calls[0][1])

    def test_consecutive_idle_with_pending_keeps_two_round_debounce(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual(first["consecutive"], 1)
        self.assertEqual(guard.prev_working, set())

        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertEqual(second["consecutive"], 2)
        self.assertIn("连续 2 轮", self.prompt_calls[0][1])

    def test_pending_and_no_running_needs_two_consecutive_rounds(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual(first["consecutive"], 1)
        self.assertEqual(self.prompt_calls, [])
        self.assertEqual(self.status_calls, ["--write"])

        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertEqual(len(self.prompt_calls), 1)
        name, message = self.prompt_calls[0]
        self.assertEqual(name, "helm-1")
        self.assertIn("待办A", message)
        self.assertIn("T1 可执行任务", message)
        self.assertEqual(self.status_calls, ["--write", "--check-fresh"])

    def test_running_worker_blocks_wake(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        guard = self.make_guard()
        for _ in range(3):
            result = guard.run_once()
            self.assertFalse(result["woke"])
        self.assertEqual(guard.consecutive, 0)
        self.assertEqual(self.prompt_calls, [])

    def test_cooldown_suppresses_repeat_wake_for_600s(self):
        self.write_fixture()
        # 2.2.21：pending 重复叫醒另受 pendingHeartbeatMinutes 约束；本用例只测
        # 10 分钟冷却边界，显式把心跳对齐到 10 分钟。
        write_file(self.root / ".planning" / "orchestration.json",
                   json.dumps({"guard": {"pendingHeartbeatMinutes": 10}}))
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        guard.run_once()
        guard.run_once()
        self.assertEqual(len(self.prompt_calls), 1)

        self.now[0] = 100
        guard.run_once()
        self.assertEqual(len(self.prompt_calls), 1)

        self.now[0] = 601
        guard.run_once()
        self.assertEqual(len(self.prompt_calls), 2)

    def test_blocked_worker_wakes_alone(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "blocked"}]
        guard = self.make_guard()

        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(len(self.prompt_calls), 1)
        self.assertIn("blocked", self.prompt_calls[0][1])
        self.assertIn("worker-1", self.prompt_calls[0][1])

    def test_excludes_helm_and_captain_from_running(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "working", "focused": False},
            {"name": "Captain-1", "agent_status": "working"},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        guard = self.make_guard()
        guard.run_once()
        self.assertFalse(self.prompt_calls)
        guard.run_once()
        self.assertEqual(len(self.prompt_calls), 1)

    def test_probe_names_blocked_do_not_wake(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        for name in ("s9-probe", "S9-UPPER", "probe-alpha", "PROBE-1",
                     "test-builder", "Test-2"):
            with self.subTest(name=name):
                self.agents = [{"name": name, "agent_status": "blocked",
                                "cwd": str(self.root)}]
                self.prompt_calls.clear()
                guard = self.make_guard()
                result = guard.run_once()
                self.assertFalse(result["woke"])
                self.assertEqual(result["blocked"], 0)
                self.assertEqual(self.prompt_calls, [])

    def test_probe_cwd_tmp_blocked_does_not_wake(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        for cwd in ("/tmp", "/tmp/s9-probe/session"):
            with self.subTest(cwd=cwd):
                self.agents = [{"name": "worker-1", "agent_status": "blocked",
                                "cwd": cwd}]
                self.prompt_calls.clear()
                guard = self.make_guard()
                result = guard.run_once()
                self.assertFalse(result["woke"])
                self.assertEqual(result["blocked"], 0)
                self.assertEqual(self.prompt_calls, [])

    def test_external_cwd_blocked_is_ignored(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "blocked",
                        "cwd": "/tmpfoo/project"}]
        guard = self.make_guard(project_roots={self.root})
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(result["blocked"], 0)
        self.assertEqual(self.prompt_calls, [])

    def test_real_blocked_worker_still_wakes(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        project = Path("/home/worker/project")
        self.agents = [{"name": "builder-1", "agent_status": "blocked",
                        "cwd": str(project)}]
        guard = self.make_guard(project_roots={project})
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(len(self.prompt_calls), 1)
        self.assertIn("builder-1", self.prompt_calls[0][1])

    def test_probe_working_still_counts_as_running(self):
        self.write_fixture()
        self.agents = [{"name": "s9-probe", "agent_status": "working",
                        "cwd": str(self.root)}]
        guard = self.make_guard(project_roots={self.root})
        for _ in range(3):
            result = guard.run_once()
            self.assertFalse(result["woke"])
        self.assertEqual(guard.consecutive, 0)
        self.assertEqual(self.prompt_calls, [])

    def test_captain_blocked_does_not_wake(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "Captain-1", "agent_status": "blocked"}]
        guard = self.make_guard()
        for _ in range(2):
            result = guard.run_once()
            self.assertFalse(result["woke"])
        self.assertEqual(self.prompt_calls, [])

    def test_residual_todo_in_complete_phase_only_logs(self):
        self.write_fixture(stream=RESIDUAL_ONLY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = []
        guard = self.make_guard()
        for _ in range(2):
            result = guard.run_once()
            self.assertFalse(result["woke"])
        self.assertEqual(self.prompt_calls, [])
        self.assertTrue(any("残留待办B" in line for line in self.logs))


class BlockedSlaTest(GuardTestBase):
    """2.2.8 工单 02：blocked 年龄 / 重复叫醒 / grace 提示。"""

    def setUp(self):
        super().setUp()
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "builder-1", "agent_status": "blocked"}]

    def write_guard_config(self, **guard) -> None:
        write_file(
            self.root / ".planning" / "orchestration.json",
            json.dumps({"guard": guard}, ensure_ascii=False),
        )

    def test_first_blocked_wake_still_respects_global_cooldown(self):
        guard = self.make_guard()
        guard.last_wake_at = 0.0
        self.now[0] = 300
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("冷却中" in line for line in self.logs))

        self.now[0] = 600
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

    def test_repeat_wake_boundary_14_59_vs_15_00(self):
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        self.now[0] = 14 * 60 + 59
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertTrue(any("未到重复叫醒间隔" in line for line in self.logs))

        self.now[0] = 15 * 60
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(2, len(self.prompt_calls))
        self.assertEqual({"builder-1": 15}, result["blockedAges"])
        self.assertIn("已 blocked 15 分钟", self.prompt_calls[1][1])

    def test_repeat_interval_respects_guard_config(self):
        self.write_guard_config(blockedRepeatMinutes=2)
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 119
        self.assertFalse(guard.run_once()["woke"])
        self.now[0] = 120
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

    def test_repeat_wake_bypasses_global_cooldown(self):
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 850
        guard.last_wake_at = 850.0  # 50 秒前有其它叫醒，全局冷却中
        self.now[0] = 900
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

    def test_grace_prompt_at_30_minutes(self):
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 15 * 60
        self.assertTrue(guard.run_once()["woke"])
        message_15 = self.prompt_calls[1][1]
        self.assertIn("已 blocked 15 分钟", message_15)
        self.assertNotIn("应关闭工位或升级 Captain", message_15)

        self.now[0] = 30 * 60
        self.assertTrue(guard.run_once()["woke"])
        message_30 = self.prompt_calls[2][1]
        self.assertIn("已 blocked 30 分钟", message_30)
        for token in ("按 R6 处置", "re-task / close / escalate",
                      "应关闭工位或升级 Captain", "guard 不自动关闭/杀 agent"):
            self.assertIn(token, message_30)

    def test_grace_threshold_respects_guard_config(self):
        self.write_guard_config(blockedRepeatMinutes=2, blockedGraceMinutes=4)
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 2 * 60
        self.assertTrue(guard.run_once()["woke"])
        self.assertNotIn("应关闭工位或升级 Captain", self.prompt_calls[1][1])

        self.now[0] = 4 * 60
        self.assertTrue(guard.run_once()["woke"])
        message = self.prompt_calls[2][1]
        self.assertIn("已 blocked 4 分钟", message)
        self.assertIn("应关闭工位或升级 Captain", message)

    def test_unblock_clears_age_and_reblock_wakes_fresh(self):
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 300
        self.agents = [{"name": "builder-1", "agent_status": "idle"}]
        guard.run_once()
        self.assertEqual({}, guard.blocked_since)
        self.assertEqual({}, guard.blocked_last_wake)

        self.now[0] = 700
        self.agents = [{"name": "builder-1", "agent_status": "blocked"}]
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual({"builder-1": 0}, result["blockedAges"])
        self.assertNotIn("应关闭工位或升级 Captain", self.prompt_calls[1][1])

    def test_disappeared_agent_clears_age(self):
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])

        self.now[0] = 300
        self.agents = []
        guard.run_once()
        self.assertEqual({}, guard.blocked_since)
        self.assertEqual({}, guard.blocked_last_wake)

    def test_multiple_blocked_merged_into_one_wake(self):
        self.agents = [
            {"name": "builder-1", "agent_status": "blocked"},
            {"name": "builder-2", "agent_status": "blocked"},
        ]
        guard = self.make_guard()
        first = guard.run_once()
        self.assertTrue(first["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertEqual({"builder-1": 0, "builder-2": 0}, first["blockedAges"])
        for name in ("builder-1", "builder-2"):
            self.assertIn(name, self.prompt_calls[0][1])

        self.now[0] = 15 * 60
        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertEqual(2, len(self.prompt_calls))
        self.assertEqual({"builder-1": 15, "builder-2": 15}, second["blockedAges"])

    def test_pane_id_identity_when_name_missing(self):
        self.agents = [{"name": "", "pane_id": "w1:p9", "agent_status": "blocked"}]
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])
        self.assertIn("w1:p9", guard.blocked_since)

        self.now[0] = 15 * 60
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))
        self.assertIn("w1:p9", self.prompt_calls[1][1])

    def test_probe_blocked_over_60_minutes_never_wakes_or_counts(self):
        self.agents = [{"name": "probe-alpha", "agent_status": "blocked",
                        "cwd": "/tmp/probe"}]
        guard = self.make_guard(project_roots={self.root})
        for minute in range(61):
            if minute:
                self.now[0] = minute * 60
            result = guard.run_once()
            self.assertFalse(result["woke"])
            self.assertEqual(0, result["blocked"])
        self.assertEqual([], self.prompt_calls)
        self.assertEqual({}, guard.blocked_since)
        self.assertEqual({}, guard.blocked_last_wake)

    def test_excluded_agents_never_enter_age_tracking(self):
        self.agents = [
            {"name": "builder-1", "agent_status": "blocked"},
            {"name": "helm-1", "agent_status": "blocked"},
            {"name": "Captain-1", "agent_status": "blocked"},
            {"name": "external-1", "agent_status": "blocked", "cwd": "/tmpfoo/x"},
        ]
        guard = self.make_guard(project_roots={self.root})
        result = guard.run_once()
        self.assertEqual({"builder-1": 0}, result["blockedAges"])
        self.assertEqual({"builder-1"}, set(guard.blocked_since))

    def test_blocked_ages_accumulate_during_handoff_then_wake(self):
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "guard.handoff").write_text(
            "phase=started\nold=helm-1\noldPane=wX:pOLD\noldTab=wX:tOLD\n"
            "new=helm-1-2\nnewPane=wX:pNEW\nnewTab=wX:tNEW\n"
            "time=2026-11-10T00:00:00Z\nreason=test\n",
            encoding="utf-8",
        )
        self.agents = [
            {"name": "helm-1", "pane_id": "wX:pOLD", "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
            {"name": "builder-1", "agent_status": "blocked"},
        ]
        guard = self.make_guard()

        first = guard.run_once()
        self.assertEqual("wait", first["handoff"]["action"])
        self.assertFalse(first["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertEqual({"builder-1": 0}, first["blockedAges"])

        self.now[0] = 30 * 60
        self.agents = [
            {"name": "helm-1", "pane_id": "wX:pOLD", "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
            {"name": "builder-1", "agent_status": "blocked"},
        ]
        second = guard.run_once()
        self.assertEqual("done", second["handoff"]["action"])
        self.assertFalse(second["woke"])
        self.assertEqual({"builder-1": 30}, second["blockedAges"])

        third = guard.run_once()
        self.assertTrue(third["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        message = self.prompt_calls[0][1]
        self.assertIn("已 blocked 30 分钟", message)
        self.assertIn("应关闭工位或升级 Captain", message)

    def test_load_config_guard_defaults_and_clamps(self):
        self.assertEqual(
            {"blockedRepeatMinutes": 15, "blockedGraceMinutes": 30,
             "approvalWaitMinutes": 15, "approvalRepeatMinutes": 30,
             "approvalGraceMinutes": 60, "approvalMaxAgeMinutes": 240,
             "wakeCooldownMinutes": 10, "pendingHeartbeatMinutes": 15,
             "idleWakeRounds": 0},
            cg.load_config(self.root)["guard"],
        )
        cases = (
            ({"blockedRepeatMinutes": 240}, 240),
            ({"blockedRepeatMinutes": 0}, 1),
            ({"blockedRepeatMinutes": 999}, 240),
            ({"blockedRepeatMinutes": "x"}, 15),
            ({"blockedRepeatMinutes": True}, 15),
            ({"blockedRepeatMinutes": 15.5}, 15),
        )
        for guard, expected in cases:
            with self.subTest(guard=guard):
                self.write_guard_config(**guard)
                self.assertEqual(
                    expected,
                    cg.load_config(self.root)["guard"]["blockedRepeatMinutes"],
                )
        self.write_guard_config(blockedGraceMinutes=0)
        self.assertEqual(1, cg.load_config(self.root)["guard"]["blockedGraceMinutes"])
        self.write_guard_config(blockedGraceMinutes="30")
        self.assertEqual(30, cg.load_config(self.root)["guard"]["blockedGraceMinutes"])


class WakeChannelTest(GuardTestBase):
    def test_focused_helm_uses_wake_file_and_notify(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "focused": True},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        guard = self.make_guard()
        guard.run_once()
        guard.run_once()

        self.assertEqual(self.prompt_calls, [])
        self.assertEqual(len(self.notify_calls), 1)
        wake = (self.crew / "guard.wake").read_text(encoding="utf-8")
        self.assertIn("guard 叫醒", wake)
        self.assertIn("待办A", wake)

    def test_unknown_pane_state_uses_wake_file(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "pane_id": "wX:p1"},
            {"name": "worker-1", "agent_status": "idle"},
        ]

        def broken():
            raise RuntimeError("pane list down")

        guard = self.make_guard(list_panes=broken)
        guard.run_once()
        guard.run_once()

        self.assertEqual(self.prompt_calls, [])
        self.assertEqual(len(self.notify_calls), 1)
        self.assertTrue((self.crew / "guard.wake").exists())
        self.assertTrue(any("聚焦状态无法判定" in line for line in self.logs))

    def test_missing_focused_field_uses_wake_file(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "pane_id": "wX:p1"},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        self.panes = [{"pane_id": "wX:p1"}]
        guard = self.make_guard()
        guard.run_once()
        guard.run_once()

        self.assertEqual(self.prompt_calls, [])
        self.assertTrue((self.crew / "guard.wake").exists())
        self.assertTrue(any("缺 focused 字段" in line for line in self.logs))

    def test_unfocused_pane_prompts(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "pane_id": "wX:p1"},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        self.panes = [{"pane_id": "wX:p1", "focused": False}]
        guard = self.make_guard()
        guard.run_once()
        guard.run_once()

        self.assertEqual(len(self.prompt_calls), 1)
        self.assertFalse((self.crew / "guard.wake").exists())

    def test_prompt_failure_falls_back_to_wake_file(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        self.prompt_ok = False
        guard = self.make_guard()
        guard.run_once()
        guard.run_once()

        self.assertEqual(len(self.prompt_calls), 1)
        self.assertTrue((self.crew / "guard.wake").exists())
        self.assertEqual(len(self.notify_calls), 1)

    def test_herdr_failure_does_not_wake_or_crash(self):
        self.write_fixture()

        def broken():
            raise RuntimeError("herdr down")

        guard = self.make_guard(list_agents=broken)
        out = io.StringIO()
        with redirect_stdout(out):
            result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(self.prompt_calls, [])
        self.assertTrue(any("herdr" in line for line in self.logs))


class WakeExpiryTest(GuardTestBase):
    """FB-011：guard.wake 首行带 generated_at；>2h 或不可解析按过期消费（fail-safe）。"""

    def setUp(self):
        super().setUp()
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.now[0] = 1_800_000_000.0

    def iso(self, epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def write_wake(self, head: str) -> Path:
        self.crew.mkdir(parents=True, exist_ok=True)
        path = self.crew / "guard.wake"
        path.write_text(f"{head}\nguard 叫醒：待办A\n", encoding="utf-8")
        return path

    def test_written_wake_has_generated_at_first_line(self):
        guard = self.make_guard()
        guard._write_wake_file("guard 叫醒：待办A")
        text = (self.crew / "guard.wake").read_text(encoding="utf-8")
        first, rest = text.split("\n", 1)
        self.assertTrue(first.startswith("generated_at: "), first)
        stamp = first.split(": ", 1)[1]
        self.assertTrue(stamp.endswith("Z"), stamp)
        self.assertIsNotNone(cg.parse_wake_generated_at(text))
        self.assertIn("guard 叫醒：待办A", rest)

    def test_fresh_wake_is_kept(self):
        path = self.write_wake(f"generated_at: {self.iso(self.now[0] - 60)}")
        before = path.read_text(encoding="utf-8")
        self.make_guard().run_once()
        self.assertTrue(path.exists())
        self.assertEqual(before, path.read_text(encoding="utf-8"))
        self.assertFalse(any("已过期" in line for line in self.logs))

    def test_wake_within_two_hours_is_kept(self):
        path = self.write_wake(f"generated_at: {self.iso(self.now[0] - 7199)}")
        self.make_guard().run_once()
        self.assertTrue(path.exists())

    def test_wake_at_exactly_two_hours_is_kept(self):
        # 语义为「超过 2h」才过期：恰好 7200s 保留（边界锁）。
        path = self.write_wake(f"generated_at: {self.iso(self.now[0] - 7200)}")
        self.make_guard().run_once()
        self.assertTrue(path.exists())
        self.assertFalse(any("已过期" in line for line in self.logs))

    def test_wake_just_over_two_hours_is_deleted(self):
        stamp = datetime.fromtimestamp(
            self.now[0] - 7200.001, timezone.utc
        ).isoformat(timespec="milliseconds")
        path = self.write_wake(f"generated_at: {stamp}")
        self.make_guard().run_once()
        self.assertFalse(path.exists())
        self.assertTrue(any("已过期/已忽略" in line for line in self.logs))

    def test_wake_older_than_two_hours_is_deleted_and_logged(self):
        stamp = self.iso(self.now[0] - 7201)
        path = self.write_wake(f"generated_at: {stamp}")
        self.make_guard().run_once()
        self.assertFalse(path.exists())
        logs = "\n".join(self.logs)
        self.assertIn("已过期/已忽略", logs)
        self.assertIn(stamp, logs)

    def test_unparseable_wake_is_deleted_and_logged(self):
        path = self.write_wake("guard 叫醒：没有时间戳")
        self.make_guard().run_once()
        self.assertFalse(path.exists())
        logs = "\n".join(self.logs)
        self.assertIn("已过期/已忽略", logs)
        self.assertIn("无法解析", logs)

    def test_legacy_bare_iso_first_line_is_supported(self):
        fresh = self.write_wake(self.iso(self.now[0] - 60))
        self.make_guard().run_once()
        self.assertTrue(fresh.exists())

        expired = self.write_wake(self.iso(self.now[0] - 7201))
        self.make_guard().run_once()
        self.assertFalse(expired.exists())

    def test_no_wake_file_is_silent(self):
        self.make_guard().run_once()
        self.assertFalse((self.crew / "guard.wake").exists())
        self.assertFalse(any("guard.wake" in line for line in self.logs))

    def test_wake_read_error_does_not_break_once(self):
        (self.crew / "guard.wake").mkdir(parents=True)
        guard = self.make_guard()
        with redirect_stdout(io.StringIO()):
            result = guard.run_once()
        self.assertFalse(result.get("error"))
        self.assertTrue(any("guard.wake 读取失败" in line for line in self.logs))

        with mock.patch.object(cg, "herdr_agents", return_value=[]), \
                mock.patch.object(cg, "run_plan_status", return_value=(0, "看板新鲜")), \
                mock.patch.object(cg, "herdr_prompt"), \
                mock.patch.object(cg, "herdr_notify"):
            code, out, err = self.run_main(
                ["--once", "--root", str(self.root), "--crew-dir", str(self.crew),
                 "--helm", "helm-1"]
            )
        self.assertEqual(0, code)
        self.assertEqual("", out)
        self.assertEqual("", err)

    def test_wake_write_error_does_not_crash_round(self):
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "focused": True},
        ]
        write_file(self.root / ".planning" / "flow-a" / "task_plan.md", STREAM_PLAN)
        guard = self.make_guard()
        with mock.patch.object(guard, "_write_wake_file", side_effect=OSError("disk full")):
            guard.run_once()
            guard.run_once()
        self.assertTrue(any("叫醒失败" in line for line in self.logs))


class SchedulingTest(GuardTestBase):
    def test_wake_runs_check_fresh_and_write_every_5min(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()

        guard.run_once()
        self.assertEqual(self.status_calls, ["--write"])

        self.now[0] = 301
        guard.run_once()
        self.assertEqual(self.status_calls, ["--write", "--write", "--check-fresh"])
        self.assertIn("看板新鲜", self.prompt_calls[0][1])


class LockAndOutputTest(GuardTestBase):
    def test_single_instance_lock_is_silent(self):
        self.write_fixture()
        handle = cg.acquire_lock(self.crew)
        self.assertIsNotNone(handle)
        try:
            self.assertIsNone(cg.acquire_lock(self.crew))
            code, out, err = self.run_main(
                ["--once", "--root", str(self.root), "--crew-dir", str(self.crew)]
            )
            self.assertEqual(code, 0)
            self.assertEqual(out, "")
            self.assertEqual(err, "")
        finally:
            handle.close()

    def test_lock_file_records_current_pid(self):
        handle = cg.acquire_lock(self.crew)
        self.assertIsNotNone(handle)
        try:
            lock = self.crew / "guard.lock"
            self.assertEqual(f"{os.getpid()}\n", lock.read_text(encoding="utf-8"))
            # 第二实例拿不到锁，不改写持有者 pid
            self.assertIsNone(cg.acquire_lock(self.crew))
            self.assertEqual(f"{os.getpid()}\n", lock.read_text(encoding="utf-8"))
        finally:
            handle.close()

    def test_normal_round_has_zero_stdout(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()
        out = io.StringIO()
        with redirect_stdout(out):
            result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(out.getvalue(), "")

    def test_cli_once_wires_injected_backends_with_zero_stdout(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        with mock.patch.object(
            cg, "herdr_agents", return_value=[{"name": "w", "agent_status": "idle"}]
        ), mock.patch.object(
            cg, "run_plan_status", return_value=(0, "看板新鲜")
        ) as status, mock.patch.object(
            cg, "herdr_prompt"
        ) as prompt, mock.patch.object(
            cg, "herdr_notify"
        ) as notify:
            code, out, err = self.run_main(
                [
                    "--once",
                    "--root",
                    str(self.root),
                    "--crew-dir",
                    str(self.crew),
                    "--helm",
                    "helm-1",
                ]
            )
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertEqual(err, "")
        status.assert_called_once()
        prompt.assert_not_called()
        notify.assert_not_called()

    def test_help_documents_guard_contract(self):
        text = cg.build_parser().format_help()
        for token in (
            "guard.wake",
            "--check-fresh",
            "--write",
            "blocked",
            "Captain*",
            "cron",
            "tab create",
            "oldTab",
            "newTab",
            "blockedRepeatMinutes",
            "blockedGraceMinutes",
            "approvalWaitMinutes",
            "approvalRepeatMinutes",
            "approvalGraceMinutes",
            "approvalMaxAgeMinutes",
            "wakeCooldownMinutes",
            "pendingHeartbeatMinutes",
            "awaiting_approval",
            "stuck",
            "R6",
            "不自动关闭",
            "rework",
        ):
            self.assertIn(token, text)


class PhaseStatusParseTest(GuardTestBase):
    """Status 行需同时识别 `in_progress` 与 `in progress`（真实 plan 两种写法并存）。"""

    IN_PROGRESS_PLAN = """# Task Plan: Flow A

## Phases

### Phase 6: 进行中
- owner: builder
- [ ] 待办A
- **Status:** {status}
"""

    def test_status_regex_matches_in_progress_spellings(self):
        for line in (
            "**Status:** in_progress",
            "**Status:** in progress",
            "**Status:**    in progress",
            "**Status:**   in_progress",
        ):
            with self.subTest(line=line):
                match = cg.STATUS_RE.search(line)
                self.assertIsNotNone(match, line)
                self.assertIn(match.group(1), ("in_progress", "in progress"))

    def test_status_regex_keeps_pending_and_complete(self):
        for line, expected in (
            ("**Status:** pending", "pending"),
            ("**Status:** complete", "complete"),
        ):
            with self.subTest(line=line):
                self.assertEqual(cg.STATUS_RE.search(line).group(1), expected)

    def test_both_in_progress_spellings_keep_todos_active(self):
        for status in ("in_progress", "in progress"):
            with self.subTest(status=status):
                self.write_fixture(
                    stream=self.IN_PROGRESS_PLAN.format(status=status),
                    portfolio=EMPTY_PORTFOLIO,
                )
                pending, residual, _ = cg.scan_todos(self.root)
                self.assertEqual([todo.text for todo in pending], ["待办A"])
                self.assertEqual(residual, [])

    def test_pending_and_complete_buckets_unchanged(self):
        self.write_fixture(
            stream=(
                "# Task Plan: Flow A\n\n## Phases\n\n"
                "### Phase 1: 待开工\n- [ ] 待办P\n- **Status:** pending\n\n"
                "### Phase 2: 已完成\n- [ ] 残留C\n- **Status:** complete\n"
            ),
            portfolio=EMPTY_PORTFOLIO,
        )
        pending, residual, _ = cg.scan_todos(self.root)
        self.assertEqual([todo.text for todo in pending], ["待办P"])
        self.assertEqual([todo.text for todo in residual], ["残留C"])

    def test_space_spelling_still_wakes_guard(self):
        self.write_fixture(
            stream=self.IN_PROGRESS_PLAN.format(status="in progress"),
            portfolio=EMPTY_PORTFOLIO,
        )
        self.agents = [{"name": "worker-1", "agent_status": "idle"}]
        guard = self.make_guard()
        first = guard.run_once()
        self.assertFalse(first["woke"])
        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertIn("待办A", self.prompt_calls[0][1])


class CwdIsolationTest(GuardTestBase):
    def test_parse_worktree_paths(self):
        roots = cg.parse_worktree_paths(
            "worktree /tmp/main\nHEAD abc\n\nworktree /tmp/linked\nHEAD def\n"
        )
        self.assertEqual(
            {Path("/tmp/main").resolve(), Path("/tmp/linked").resolve()}, roots
        )

    def test_git_worktree_roots_uses_porcelain_output(self):
        fake = mock.Mock(returncode=0, stdout=(
            "worktree /home/project/main\nHEAD a\n\n"
            "worktree /home/project/linked\nHEAD b\n"
        ))
        with mock.patch.object(cg.subprocess, "run", return_value=fake) as run:
            roots = cg.git_worktree_roots(Path("/home/project/main"))
        self.assertEqual(
            {Path("/home/project/main").resolve(), Path("/home/project/linked").resolve()},
            roots,
        )
        self.assertEqual(
            ["git", "-C", "/home/project/main", "worktree", "list", "--porcelain"],
            run.call_args.args[0],
        )

    def test_external_cwd_working_does_not_block_wake(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working",
                        "cwd": "/home/other/project"}]
        guard = self.make_guard(project_roots={self.root})
        first = guard.run_once()
        self.assertEqual(first["running"], 0)
        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertIn("待办A", self.prompt_calls[0][1])

    def test_linked_worktree_cwd_blocked_wakes(self):
        main = Path("/home/project/main")
        linked = Path("/home/project/linked")
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "builder-1", "agent_status": "blocked",
                        "cwd": str(linked)}]
        guard = self.make_guard(project_roots={main, linked})
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(result["blocked"], 1)

    def test_missing_cwd_falls_back_to_include(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "builder-1", "agent_status": "blocked"}]
        guard = self.make_guard(project_roots={self.root})
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertIn("builder-1", self.prompt_calls[0][1])


class HandoffTest(GuardTestBase):
    OLD_PANE = "wX:pOLD"

    def prepare_handoff(self, ctx_k: int = 650, config: dict | None = None) -> None:
        self.write_fixture(
            stream=EMPTY_STREAM,
            portfolio=EMPTY_PORTFOLIO,
            config=config,
        )
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"}
        ]
        self.pane_outputs[self.OLD_PANE] = f"status line ctx ({ctx_k}k/1M) tail"

    def reset_handoff_artifacts(self) -> None:
        for name in ("guard.handoff", "guard.wake"):
            path = self.crew / name
            if path.exists():
                path.unlink()
        self.pane_reads.clear()
        self.dispatch_calls.clear()
        self.notify_calls.clear()
        self.logs.clear()

    def test_parse_ctx_success_and_failure(self):
        self.assertEqual(412, cg.parse_ctx_k("ctx (412k/1M)"))
        self.assertEqual(412, cg.parse_ctx_k("... ctx ( 412k / 1M ) ..."))
        self.assertIsNone(cg.parse_ctx_k("ctx unknown"))
        self.assertIsNone(cg.parse_ctx_k(""))

    def test_warn_threshold_marks_and_notifies_once(self):
        self.prepare_handoff(ctx_k=320)
        guard = self.make_guard()

        first = guard.run_once()
        self.assertFalse(first["woke"])
        fields = self.handoff_fields()
        self.assertEqual("warn", fields["phase"])
        self.assertEqual("helm-1", fields["old"])
        self.assertEqual(self.OLD_PANE, fields["oldPane"])
        self.assertEqual("wX:tOLD", fields["oldTab"])
        self.assertIn("30%", fields["reason"])
        self.assertEqual([], self.dispatch_calls)
        self.assertTrue(any("准备交接" in body for _, body in self.notify_calls))

        count = len(self.notify_calls)
        guard.run_once()
        self.assertEqual(count, len(self.notify_calls))
        self.assertEqual("warn", self.handoff_fields()["phase"])

    def test_handoff_threshold_starts_successor_once(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()

        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(
            [["tab", "create", "--workspace", "wX", "--cwd", str(self.root),
              "--label", "总舵", "--no-focus"],
             ["start", "--role", "helm", "--pane", "wX:pNEW",
              "--permission-mode", "bypass_permissions",
              "--name", "helm-1-2", "--json"],
             ["prompt", "--agent", "helm-1-2", "--message"]],
            [call[:4] if call[:1] == ["prompt"] else call
             for call in self.dispatch_calls],
        )
        self.assertEqual(
            [], [c for c in self.dispatch_calls if c[:2] == ["pane", "split"]])
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertEqual("helm-1", fields["old"])
        self.assertEqual("helm-1-2", fields["new"])
        self.assertEqual("wX:pNEW", fields["newPane"])
        self.assertEqual("wX:tOLD", fields["oldTab"])
        self.assertEqual("wX:tNEW", fields["newTab"])
        self.assertEqual([], [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]])
        self.assertTrue(any("交接" in body for _, body in self.notify_calls))

        before = len(self.dispatch_calls)
        guard.run_once()
        self.assertEqual(before, len(self.dispatch_calls))
        self.assertEqual("started", self.handoff_fields()["phase"])

    def test_rotate_runs_before_tab_create(self):
        self.prepare_handoff(ctx_k=650)
        order: list[str] = []

        def rotate(root):
            order.append("rotate")
            return 0, "ok"

        original = self.fake_dispatch

        def dispatch(root, args):
            order.append("dispatch:" + " ".join(args[:2]))
            return original(root, args)

        self.make_guard(run_rotate=rotate, dispatch=dispatch).run_once()
        self.assertEqual("rotate", order[0])
        self.assertLess(order.index("rotate"), order.index("dispatch:tab create"))

    def test_rotate_success_logged_and_handoff_continues(self):
        self.prepare_handoff(ctx_k=650)
        self.make_guard().run_once()
        self.assertEqual([self.root], self.rotate_calls)
        self.assertTrue(
            any("已归档" in log and "rotate" in log for log in self.logs), self.logs)
        self.assertEqual("started", self.handoff_fields()["phase"])

    def test_rotate_failure_logs_warning_and_continues(self):
        self.prepare_handoff(ctx_k=650)
        self.rotate_result = (2, "已有 rotate 在运行（锁被占用）")
        self.make_guard().run_once()
        self.assertTrue(
            any("rotate 失败" in log and "退出码 2" in log for log in self.logs), self.logs)
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertNotEqual("failed", fields.get("result"))
        self.assertEqual(1, len([c for c in self.dispatch_calls if c[:2] == ["tab", "create"]]))
        self.assertEqual(1, len([c for c in self.dispatch_calls if c[:1] == ["prompt"]]))

    def test_rotate_exception_logs_warning_and_continues(self):
        self.prepare_handoff(ctx_k=650)

        def boom(root):
            raise OSError("模拟异常")

        self.make_guard(run_rotate=boom).run_once()
        self.assertTrue(any("rotate 异常" in log for log in self.logs), self.logs)
        self.assertEqual("started", self.handoff_fields()["phase"])

    def test_default_rotate_uses_production_args(self):
        self.prepare_handoff(ctx_k=650)
        with mock.patch.object(cg, "run_plan_status_args", return_value=(0, "ok")) as run:
            self.make_guard(run_rotate=None).run_once()
        run.assert_called_once_with(
            self.root, ["--rotate", "--apply", "--keep", "10"])
        self.assertEqual("started", self.handoff_fields()["phase"])

    def test_handoff_prompt_uses_handover_read_set(self):
        # FB-044 补口：自动交接 prompt 与 helm 模板同一白名单——brief 一屏优先，
        # 整看板不整读、仅按关键词 grep。
        self.prepare_handoff(ctx_k=650)
        self.make_guard().run_once()
        prompts = [c for c in self.dispatch_calls if c[:1] == ["prompt"]]
        self.assertEqual(1, len(prompts))
        message = prompts[0][-1]
        for token in ("--brief", "--handover", "feedback.md", "Next Step/Status",
                      "guard.handoff", "禁整读 看板", "32KB", "20KB",
                      "看板仅按关键词 grep", "写明只读段", "首报"):
            self.assertIn(token, message)
        self.assertNotIn("派生看板（docs/进度追踪.md）+", message)
        self.assertNotIn("先读", message)
        self.assertNotIn("findings.md", message)
        self.assertNotIn("含 progress 尾部", message)

    def test_rotate_noop_logs_zero_write(self):
        self.prepare_handoff(ctx_k=650)
        self.rotate_result = (0, "progress 条目数 ≤ keep 10，无需轮转（零写入）。")
        self.make_guard().run_once()
        self.assertTrue(
            any("无需轮转（零写入）" in log for log in self.logs), self.logs)
        self.assertFalse(any("已归档" in log for log in self.logs), self.logs)

    def test_rotate_once_across_two_rounds(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()
        guard.run_once()
        guard.run_once()
        self.assertEqual(1, len(self.rotate_calls))
        self.assertEqual("started", self.handoff_fields()["phase"])

    def test_warn_round_does_not_rotate(self):
        self.prepare_handoff(ctx_k=320)
        self.make_guard().run_once()
        self.assertEqual([], self.rotate_calls)
        self.assertEqual("warn", self.handoff_fields()["phase"])

    def test_tab_create_workspace_falls_back_to_pane_prefix(self):
        self.prepare_handoff(ctx_k=650)
        self.agents[0].pop("workspace_id")
        self.make_guard().run_once()
        tab_calls = [c for c in self.dispatch_calls if c[:2] == ["tab", "create"]]
        self.assertEqual(1, len(tab_calls))
        self.assertEqual("wX", tab_calls[0][tab_calls[0].index("--workspace") + 1])

    def test_no_workspace_fails_closed_before_tab_create(self):
        self.prepare_handoff(ctx_k=650)
        self.agents[0]["pane_id"] = "pOLD"  # 无 workspace 冒号前缀
        self.agents[0].pop("workspace_id")
        self.pane_outputs["pOLD"] = "status line ctx (650k/1M) tail"
        self.make_guard().run_once()
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("workspace", fields["error"])
        self.assertEqual([], self.dispatch_calls)

    def test_tab_create_without_tab_id_fails_closed(self):
        self.prepare_handoff(ctx_k=650)

        def override(args):
            if args[:2] == ["tab", "create"]:
                return 0, json.dumps({"pane": "wX:pNEW"}), ""
            return None

        self.dispatch_override = override
        self.make_guard().run_once()
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("未返回 tab", fields["error"])
        self.assertFalse([c for c in self.dispatch_calls if c[:1] == ["start"]])

    def test_custom_helm_tab_label_passed_to_tab_create(self):
        self.prepare_handoff(
            ctx_k=650,
            config={"portfolioDir": ".planning/portfolio", "helm": {"tabLabel": "总舵·主"}},
        )
        self.make_guard().run_once()
        tab_calls = [c for c in self.dispatch_calls if c[:2] == ["tab", "create"]]
        self.assertEqual(1, len(tab_calls))
        self.assertEqual("总舵·主", tab_calls[0][tab_calls[0].index("--label") + 1])

    def test_name_conflict_increments_successor_suffix(self):
        self.prepare_handoff(ctx_k=650)
        self.agents.extend([
            {"name": "helm-1-2", "agent_status": "idle"},
            {"name": "helm-1-3", "agent_status": "idle"},
        ])
        guard = self.make_guard()
        guard.run_once()
        starts = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(starts))
        self.assertEqual("helm-1-4", starts[0][starts[0].index("--name") + 1])

    def test_restart_resolver_uses_handoff_successor(self):
        self.write_fixture(
            stream=EMPTY_STREAM,
            portfolio=EMPTY_PORTFOLIO,
            config={
                "portfolioDir": ".planning/portfolio",
                "helm": {"agentName": "helm-1"},
            },
        )
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "guard.handoff").write_text(
            "phase=done\nold=helm-1\nnew=helm-1-2\n", encoding="utf-8",
        )
        self.assertEqual(
            "helm-1-2", cg.resolve_guard_helm(self.root, self.crew, None)
        )
        self.assertEqual(
            "helm-explicit", cg.resolve_guard_helm(self.root, self.crew, "helm-explicit")
        )

    def test_successor_start_reuses_registered_helm_runtime(self):
        self.prepare_handoff(ctx_k=650)
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "dispatch-agents.json").write_text(
            json.dumps({"helm-1": {"role": "helm", "runtime": "qoder"}}),
            encoding="utf-8",
        )
        self.make_guard().run_once()
        starts = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(starts))
        self.assertEqual("qoder", starts[0][starts[0].index("--runtime") + 1])

    def test_first_report_then_close_old_pane(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()
        guard.run_once()

        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()
        self.assertEqual([], [c for c in self.dispatch_calls
                              if c[:2] in (["pane", "close"], ["tab", "close"])])

        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]
        guard.run_once()
        closes = [c for c in self.dispatch_calls
                  if c[:2] in (["pane", "close"], ["tab", "close"])]
        self.assertEqual(
            [["pane", "close", "--pane", self.OLD_PANE, "--caller", "guard"],
             ["tab", "close", "--tab", "wX:tOLD", "--caller", "guard"]],
            closes,
        )
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])
        self.assertEqual("wX:tOLD", fields["oldTab"])
        self.assertEqual("wX:tNEW", fields["newTab"])

        before = len(self.dispatch_calls)
        guard.run_once()
        self.assertEqual(before, len(self.dispatch_calls))

    def test_old_handoff_without_old_tab_closes_only_pane(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()

        path = self.crew / "guard.handoff"
        lines = [line for line in path.read_text(encoding="utf-8").splitlines()
                 if not line.startswith(("oldTab=", "newTab="))]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]
        guard.run_once()
        closes = [c for c in self.dispatch_calls
                  if c[:2] in (["pane", "close"], ["tab", "close"])]
        self.assertEqual([["pane", "close", "--pane", self.OLD_PANE, "--caller", "guard"]], closes)
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])

    def test_tab_close_failure_notes_and_finishes_without_retry(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]

        def override(args):
            if args[:2] == ["tab", "close"]:
                return 1, "", "tab close boom"
            return None

        self.dispatch_override = override
        guard.dispatch = self.fake_dispatch  # type: ignore[method-assign]
        guard.run_once()
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])
        self.assertTrue(any("旧 tab" in line and "关闭失败" in line for line in self.logs))
        self.assertTrue(any("关闭失败" in body for _, body in self.notify_calls))

        before = len(self.dispatch_calls)
        guard.run_once()
        self.assertEqual(before, len(self.dispatch_calls))

    def test_residual_panes_in_old_tab_skip_tab_close(self):
        self.prepare_handoff(ctx_k=650)
        self.panes = [{"pane_id": "wX:pEXTRA", "tab_id": "wX:tOLD"}]
        guard = self.make_guard()
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]
        guard.run_once()
        closes = [c for c in self.dispatch_calls
                  if c[:2] in (["pane", "close"], ["tab", "close"])]
        self.assertEqual([["pane", "close", "--pane", self.OLD_PANE, "--caller", "guard"]], closes)
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])
        self.assertTrue(any("仍有 1 个 pane" in line for line in self.logs))
        self.assertTrue(any("仍有 1 个 pane" in body for _, body in self.notify_calls))

    def test_pane_list_failure_skips_old_tab_close(self):
        self.prepare_handoff(ctx_k=650)

        def broken():
            raise RuntimeError("pane list boom")

        self.panes = [{"pane_id": "wX:pEXTRA", "tab_id": "wX:tOLD"}]
        guard = self.make_guard(list_panes=broken)
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]
        guard.run_once()
        closes = [c for c in self.dispatch_calls
                  if c[:2] in (["pane", "close"], ["tab", "close"])]
        self.assertEqual([["pane", "close", "--pane", self.OLD_PANE, "--caller", "guard"]], closes)
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])
        self.assertTrue(any("剩余 pane 检查失败" in line for line in self.logs))
        self.assertTrue(any("无法确认剩余 pane" in body for _, body in self.notify_calls))

    def test_custom_thresholds_are_read_from_config(self):
        self.prepare_handoff(
            ctx_k=250,
            config={
                "portfolioDir": ".planning/portfolio",
                "helm": {"ctxWarn": 0.20, "ctxHandoff": 0.50},
            },
        )
        self.make_guard().run_once()
        self.assertEqual("warn", self.handoff_fields()["phase"])

    def test_parse_failure_logs_but_leaves_normal_wake_working(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        self.pane_outputs[self.OLD_PANE] = "no context line"
        guard = self.make_guard()

        self.assertFalse(guard.run_once()["woke"])
        self.assertTrue(guard.run_once()["woke"])
        self.assertFalse((self.crew / "guard.handoff").exists())
        self.assertEqual([], self.dispatch_calls)
        self.assertTrue(any("ctx 解析失败" in line for line in self.logs))
        self.assertIn("待办A", self.prompt_calls[0][1])

    def test_tab_create_start_prompt_failure_notifies_once_and_does_not_loop(self):
        for step in ("tab", "start", "prompt"):
            with self.subTest(step=step):
                self.reset_handoff_artifacts()
                self.prepare_handoff(ctx_k=650)

                def override(args, step=step):
                    if step == "tab" and args[:2] == ["tab", "create"]:
                        return 1, "", "tab boom"
                    if step == "start" and args[:1] == ["start"]:
                        return 1, "", "start boom"
                    if step == "prompt" and args[:1] == ["prompt"]:
                        return 1, "", "prompt boom"
                    return None

                self.dispatch_override = override
                guard = self.make_guard()
                guard.run_once()
                fields = self.handoff_fields()
                self.assertEqual("done", fields["phase"])
                self.assertEqual("failed", fields["result"])
                self.assertIn(step, fields["error"])
                self.assertTrue(any("失败" in body for _, body in self.notify_calls))
                self.assertEqual([], [c for c in self.dispatch_calls
                                      if c[:2] in (["pane", "split"], ["pane", "close"],
                                                   ["tab", "close"])])
                if step == "tab":
                    self.assertFalse([c for c in self.dispatch_calls
                                      if c[:1] == ["start"]])

                before = len(self.dispatch_calls)
                guard.run_once()
                self.assertEqual(before, len(self.dispatch_calls))

    def test_close_failure_notifies_once_and_does_not_retry(self):
        self.prepare_handoff(ctx_k=650)
        guard = self.make_guard()
        guard.run_once()

        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "working"},
        ]
        guard.run_once()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wX:pNEW", "agent_status": "idle"},
        ]

        def override(args):
            if args[:2] == ["pane", "close"]:
                return 1, "", "close boom"
            return None

        self.dispatch_override = override
        guard.dispatch = self.fake_dispatch  # type: ignore[method-assign]
        guard.run_once()
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("close", fields["error"])

        before = len(self.dispatch_calls)
        guard.run_once()
        self.assertEqual(before, len(self.dispatch_calls))


class HandoffPaneCountTest(GuardTestBase):
    """T45④ / FB-038：交接 tab create 后校验新 tab pane 数=1，多余空 pane 关闭。"""

    OLD_PANE = "wX:pOLD"

    def prepare_handoff(self, ctx_k: int = 650) -> None:
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"}
        ]
        self.pane_outputs[self.OLD_PANE] = f"status line ctx ({ctx_k}k/1M) tail"

    def set_new_tab_panes(self, *panes: dict) -> None:
        self.panes = [
            {"pane_id": self.OLD_PANE, "tab_id": "wX:tOLD", "focused": False},
            *panes,
        ]

    def test_extra_empty_pane_closed_before_start(self):
        self.prepare_handoff()
        self.set_new_tab_panes(
            {"pane_id": "wX:pNEW", "tab_id": "wX:tNEW", "agent_status": "unknown"},
            {"pane_id": "wX:pEXTRA", "tab_id": "wX:tNEW", "agent_status": "unknown"},
        )
        self.make_guard().run_once()

        close_calls = [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]]
        self.assertEqual([["pane", "close", "--pane", "wX:pEXTRA", "--caller", "guard"]], close_calls)
        close_index = self.dispatch_calls.index(
            ["pane", "close", "--pane", "wX:pEXTRA", "--caller", "guard"])
        start_index = next(
            index for index, call in enumerate(self.dispatch_calls)
            if call[:1] == ["start"]
        )
        self.assertLess(close_index, start_index)
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertTrue(any("多余 pane 已关闭" in line and "wX:pEXTRA" in line
                            for line in self.logs), self.logs)

    def test_working_extra_pane_not_closed(self):
        self.prepare_handoff()
        self.set_new_tab_panes(
            {"pane_id": "wX:pNEW", "tab_id": "wX:tNEW", "agent_status": "unknown"},
            {"pane_id": "wX:pBUSY", "tab_id": "wX:tNEW", "agent_status": "working"},
        )
        self.make_guard().run_once()

        self.assertEqual([], [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]])
        self.assertEqual("started", self.handoff_fields()["phase"])
        self.assertTrue(any("working" in line and "未关闭" in line for line in self.logs),
                        self.logs)

    def test_extra_agent_pane_not_closed(self):
        self.prepare_handoff()
        self.set_new_tab_panes(
            {"pane_id": "wX:pNEW", "tab_id": "wX:tNEW", "agent_status": "unknown"},
            {"pane_id": "wX:pTUI", "tab_id": "wX:tNEW",
             "agent": "claude", "agent_status": "idle"},
        )
        self.make_guard().run_once()

        self.assertEqual([], [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]])
        self.assertTrue(any("wX:pTUI" in line and "未关闭" in line for line in self.logs),
                        self.logs)

    def test_close_failure_warns_and_handoff_continues(self):
        self.prepare_handoff()
        self.set_new_tab_panes(
            {"pane_id": "wX:pNEW", "tab_id": "wX:tNEW", "agent_status": "unknown"},
            {"pane_id": "wX:pEXTRA", "tab_id": "wX:tNEW", "agent_status": "unknown"},
        )

        def override(args):
            if args[:2] == ["pane", "close"]:
                return 1, "", "close boom"
            return None

        self.dispatch_override = override
        self.make_guard().run_once()

        self.assertIn(["pane", "close", "--pane", "wX:pEXTRA", "--caller", "guard"],
                      self.dispatch_calls)
        self.assertIn("start", [c[0] for c in self.dispatch_calls])
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertNotEqual("failed", fields.get("result"))
        self.assertTrue(any("关闭失败" in line and "继续交接" in line for line in self.logs),
                        self.logs)

    def test_pane_list_failure_fails_safe(self):
        self.prepare_handoff()

        def broken_list() -> list[dict]:
            raise RuntimeError("pane list boom")

        self.make_guard(list_panes=broken_list).run_once()

        self.assertIn("start", [c[0] for c in self.dispatch_calls])
        self.assertEqual("started", self.handoff_fields()["phase"])
        self.assertTrue(any("pane 数校验失败" in line and "继续交接" in line
                            for line in self.logs), self.logs)

    def test_unidentified_new_tab_panes_warn_only(self):
        self.prepare_handoff()  # 默认 pane list 无新 tab 条目 → 无法确认 pane 数
        self.make_guard().run_once()

        self.assertEqual([], [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]])
        self.assertEqual("started", self.handoff_fields()["phase"])
        self.assertTrue(any("未返回" in line and "人工核查" in line for line in self.logs),
                        self.logs)

    def test_returned_pane_missing_from_pane_list_never_closes(self):
        # tab create 返回 pane 与 pane list 对不上：不关闭任何 pane（防误杀唯一 pane）
        self.prepare_handoff()
        self.set_new_tab_panes(
            {"pane_id": "wX:pOTHER", "tab_id": "wX:tNEW", "agent_status": "unknown"},
        )
        self.make_guard().run_once()

        self.assertEqual([], [c for c in self.dispatch_calls if c[:2] == ["pane", "close"]])
        self.assertEqual("started", self.handoff_fields()["phase"])
        self.assertTrue(any("未包含新 pane wX:pNEW" in line for line in self.logs),
                        self.logs)


class Fb008CtxHandoffTest(GuardTestBase):
    """FB-008：ctx 解析（小数/百分比）、29/30/49/50 边界、done 重评估、pane 映射。"""

    OLD_PANE = "wX:pOLD"
    NEW_PANE = "wX:pNEW"

    def prepare(self, ctx: str = "ctx (650k/1M)", config: dict | None = None) -> None:
        self.write_fixture(
            stream=EMPTY_STREAM,
            portfolio=EMPTY_PORTFOLIO,
            config=config,
        )
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"}
        ]
        self.pane_outputs[self.OLD_PANE] = ctx

    def write_handoff(self, text: str) -> None:
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "guard.handoff").write_text(text, encoding="utf-8")

    def done_handoff(self, result: str = "ok", new: str = "helm-1-2") -> None:
        self.write_handoff(
            "phase=done\nold=helm-1\noldPane=wX:pOLD\noldTab=wX:tOLD\n"
            f"new={new}\nnewPane={self.NEW_PANE}\nnewTab=wX:tNEW\n"
            "time=2026-11-10T16:18:00Z\nreason=ctx 62.3% > 60%\n"
            f"result={result}\n"
        )

    def write_registry(self, registry: dict) -> None:
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "dispatch-agents.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    def test_parse_ctx_formats(self):
        cases = (
            ("ctx (412k/1M)", 412.0),
            ("ctx 75.4k/1M", 75.4),
            ("ctx 623k/1M", 623.0),
            ("ctx ( 75.4k / 1M )", 75.4),
            ("ctx 62.3%", 623.0),
            ("ctx 623k/1M", 623.0),
            ("first ctx 10k/1M\nlast ctx 75.8k/1M", 75.8),
            ("\x1b[32mctx 75.4k/1M\x1b[0m", 75.4),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertAlmostEqual(expected, cg.parse_ctx_k(text))

    def test_parse_ctx_invalid_returns_none(self):
        for text in ("ctx unknown", "", "ctx 75.4k", "ctx 75.4k/",
                     "ctx 75.4k/0M", "no context", None, 123):
            with self.subTest(text=text):
                self.assertIsNone(cg.parse_ctx_k(text))

    def test_threshold_boundaries_29_30_49_50(self):
        self.prepare(ctx="ctx 290k/1M")
        guard = self.make_guard()

        none = guard.run_once()
        self.assertEqual("none", none["handoff"]["action"])
        self.assertFalse((self.crew / "guard.handoff").exists())
        self.assertEqual([], self.dispatch_calls)
        self.assertEqual([], self.notify_calls)

        self.pane_outputs[self.OLD_PANE] = "ctx 300k/1M"
        warn = guard.run_once()
        self.assertEqual("warn", warn["handoff"]["action"])
        self.assertEqual("warn", self.handoff_fields()["phase"])
        self.assertEqual(1, len(self.notify_calls))
        self.assertIn("30%", self.handoff_fields()["reason"])

        self.pane_outputs[self.OLD_PANE] = "ctx 490k/1M"
        still = guard.run_once()
        self.assertEqual("none", still["handoff"]["action"])
        self.assertEqual("warn", self.handoff_fields()["phase"])
        self.assertEqual(1, len(self.notify_calls))
        self.assertEqual([], self.dispatch_calls)

        self.pane_outputs[self.OLD_PANE] = "ctx 500k/1M"
        started = guard.run_once()
        self.assertEqual("started", started["handoff"]["action"])
        self.assertEqual("started", self.handoff_fields()["phase"])
        self.assertIn("50%", self.handoff_fields()["reason"])
        self.assertEqual(
            1, len([c for c in self.dispatch_calls if c[:2] == ["tab", "create"]]))
        self.assertEqual(
            [], [c for c in self.dispatch_calls if c[:2] == ["pane", "split"]])

    def test_threshold_510k_still_triggers(self):
        self.prepare(ctx="ctx 510k/1M")
        result = self.make_guard().run_once()
        self.assertEqual("started", result["handoff"]["action"])
        self.assertIn("50%", self.handoff_fields()["reason"])

    def test_done_phase_warns_on_successor_ctx(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.done_handoff()
        self.agents = [
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "workspace_id": "wX", "agent_status": "idle"},
        ]
        self.pane_outputs[self.NEW_PANE] = "ctx 320k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("warn", result["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("warn", fields["phase"])
        self.assertEqual("helm-1-2", fields["old"])
        self.assertEqual(self.NEW_PANE, fields["oldPane"])
        self.assertTrue(any("320k/1M" in body for _, body in self.notify_calls))
        self.assertEqual([], self.dispatch_calls)

        guard.run_once()
        self.assertEqual(1, len(self.notify_calls))

    def test_done_phase_can_start_new_handoff(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.done_handoff()
        self.agents = [
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "workspace_id": "wX", "agent_status": "idle"},
        ]
        self.pane_outputs[self.NEW_PANE] = "ctx 650k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("started", result["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertEqual("helm-1-2", fields["old"])
        self.assertEqual(self.NEW_PANE, fields["oldPane"])
        self.assertEqual(
            [], [c for c in self.dispatch_calls if c[:2] == ["pane", "split"]])

    def test_done_failed_does_not_retry(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.done_handoff(result="failed")
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "idle"},
        ]
        self.pane_outputs[self.OLD_PANE] = "ctx 650k/1M"
        guard = self.make_guard()

        for _ in range(3):
            result = guard.run_once()
            self.assertEqual("none", result["handoff"]["action"])
        self.assertEqual([], self.dispatch_calls)
        self.assertEqual([], self.notify_calls)
        self.assertTrue(any("不再自动重试" in line for line in self.logs))

    def test_done_phase_below_warn_is_none(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.done_handoff()
        self.agents = [
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "agent_status": "idle"},
        ]
        self.pane_outputs[self.NEW_PANE] = "ctx 75.8k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("none", result["handoff"]["action"])
        self.assertEqual("done", self.handoff_fields()["phase"])
        self.assertEqual([], self.notify_calls)
        self.assertEqual([], self.dispatch_calls)

    def test_registry_maps_drifted_agent_name_to_helm_pane(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_registry({"helm-1": {"role": "helm", "pane": self.OLD_PANE}})
        self.agents = [
            {"name": "helm-1-renamed", "pane_id": self.OLD_PANE,
             "tab_id": "wX:tOLD", "agent_status": "idle"},
        ]
        self.pane_outputs[self.OLD_PANE] = "ctx 320k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("warn", result["handoff"]["action"])
        # T69② 起 worker ctx 扫描也会读 pane（漂移名未排除、读同一 pane）；
        # 断言收紧为「只读该 pane、无其他 pane」。
        self.assertEqual({(self.OLD_PANE, 80)}, set(self.pane_reads))
        self.assertEqual("helm-1-renamed", guard.helm)
        self.assertEqual("helm-1-renamed", self.handoff_fields()["old"])
        self.assertTrue(any("漂移" in line for line in self.logs))

    def test_registry_latest_helm_entry_reverse_lookup(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_registry({
            "helm-1": {"role": "helm", "pane": self.OLD_PANE,
                       "startedAt": "2026-11-10T08:00:00Z"},
            "helm-2": {"role": "helm", "pane": self.NEW_PANE,
                       "startedAt": "2026-11-10T09:00:00Z"},
        })
        self.agents = [
            {"name": "helm-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "agent_status": "idle"},
        ]
        self.pane_outputs[self.NEW_PANE] = "ctx 320k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("warn", result["handoff"]["action"])
        self.assertEqual("helm-2", guard.helm)
        self.assertEqual("helm-2", self.handoff_fields()["old"])

    def test_unresolvable_helm_is_failsafe(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [
            {"name": "builder-1", "pane_id": "wX:pB", "agent_status": "idle"},
        ]
        guard = self.make_guard()

        for _ in range(2):
            result = guard.run_once()
            self.assertEqual("none", result["handoff"]["action"])
        self.assertFalse((self.crew / "guard.handoff").exists())
        # T69② 起 worker ctx 扫描合法读 worker pane（builder-1）；helm pane
        # 未解析 → 不得有任何 helm pane read，dispatch 交接零动作。
        self.assertTrue(all(pane == "wX:pB" for pane, _ in self.pane_reads))
        self.assertEqual([], self.dispatch_calls)
        self.assertTrue(any("ctx 检查跳过" in line for line in self.logs))

    def test_second_handoff_after_done_can_start(self):
        self.prepare(ctx="ctx 650k/1M")
        guard = self.make_guard()
        guard.run_once()
        self.assertEqual("started", self.handoff_fields()["phase"])

        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "workspace_id": "wX", "agent_status": "working"},
        ]
        guard.run_once()
        self.agents[1]["agent_status"] = "idle"
        done = guard.run_once()
        self.assertEqual("done", done["handoff"]["action"])
        self.assertEqual("helm-1-2", guard.helm)

        self.dispatch_calls.clear()
        self.pane_outputs[self.NEW_PANE] = "ctx 650k/1M"
        again = guard.run_once()
        self.assertEqual("started", again["handoff"]["action"])
        self.assertEqual(
            1, len([c for c in self.dispatch_calls if c[:2] == ["tab", "create"]]))
        self.assertEqual("helm-1-2", self.handoff_fields()["old"])

    def test_cli_once_json_includes_handoff_action(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        with mock.patch.object(cg, "herdr_agents", return_value=[]), \
                mock.patch.object(cg, "run_plan_status", return_value=(0, "看板新鲜")), \
                mock.patch.object(cg, "herdr_prompt"), \
                mock.patch.object(cg, "herdr_notify"):
            code, out, err = self.run_main([
                "--once", "--json", "--root", str(self.root),
                "--crew-dir", str(self.crew), "--helm", "helm-1",
            ])
        self.assertEqual(0, code)
        self.assertEqual("", err)
        payload = json.loads(out)
        self.assertEqual({"action": "none"}, payload["handoff"])


class Fb008HandoffEdgeCaseTest(GuardTestBase):
    """FB-008 AMEND1：B1 旧舵身份/有界等待、B2 继任停滞、B3 失败态解析、B4 资源通知、B5 反查收紧。"""

    OLD_PANE = "wX:pOLD"
    NEW_PANE = "wX:pNEW"

    def prepare(self, config: dict | None = None) -> None:
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO, config=config)
        self.crew.mkdir(parents=True, exist_ok=True)

    def write_handoff(self, text: str) -> None:
        (self.crew / "guard.handoff").write_text(text, encoding="utf-8")

    def write_registry(self, registry: dict) -> None:
        (self.crew / "dispatch-agents.json").write_text(
            json.dumps(registry, ensure_ascii=False), encoding="utf-8")

    def started_handoff(self, old: str = "helm-1", new: str = "helm-1-2",
                        successor_was_working: str = "1") -> None:
        self.write_handoff(
            f"phase=started\nold={old}\noldPane={self.OLD_PANE}\noldTab=wX:tOLD\n"
            f"new={new}\nnewPane={self.NEW_PANE}\nnewTab=wX:tNEW\n"
            "time=2026-11-10T16:18:00Z\nreason=ctx 650k/1M >= 50% 交接阈值\n"
            f"result=ok\nsuccessorWasWorking={successor_was_working}\n"
        )

    def closes(self) -> list[list[str]]:
        return [c for c in self.dispatch_calls
                if c[:2] in (["pane", "close"], ["tab", "close"])]

    def test_restart_does_not_kill_working_old_helm(self):
        """B1a：重启后 helm=继任名，旧舵按 handoff old 判定；working 不关，转 idle 后正常关闭。"""
        self.prepare()
        self.started_handoff()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "working"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "agent_status": "idle"},
        ]
        guard = self.make_guard(helm="helm-1-2")

        first = guard.run_once()
        self.assertEqual("wait", first["handoff"]["action"])
        self.assertEqual([], self.closes())
        self.assertEqual("started", self.handoff_fields()["phase"])

        self.agents[0]["agent_status"] = "idle"
        done = guard.run_once()
        self.assertEqual("done", done["handoff"]["action"])
        self.assertEqual(
            [["pane", "close", "--pane", self.OLD_PANE, "--caller", "guard"],
             ["tab", "close", "--tab", "wX:tOLD", "--caller", "guard"]],
            self.closes(),
        )
        self.assertEqual("done", self.handoff_fields()["phase"])
        self.assertEqual("ok", self.handoff_fields()["result"])

    def test_old_helm_working_fails_after_bounded_wait(self):
        """B1b：旧舵持续 working 不杀；连续 HANDOFF_STALL_ROUNDS 轮后 failed 通知，不自动重试。"""
        self.prepare()
        self.started_handoff()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "working"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "agent_status": "idle"},
        ]
        guard = self.make_guard()

        for _ in range(cg.HANDOFF_STALL_ROUNDS - 1):
            result = guard.run_once()
            self.assertEqual("wait", result["handoff"]["action"])
            self.assertEqual("started", self.handoff_fields()["phase"])
        failed = guard.run_once()
        self.assertEqual("failed", failed["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("旧舵收口", fields["error"])
        self.assertEqual([], self.closes())
        body = self.notify_calls[0][1]
        self.assertIn("旧舵", body)
        self.assertIn("wX:pNEW", body)
        self.assertIn("保留", body)
        self.assertNotIn("清理", body)

        before = len(self.notify_calls)
        self.assertEqual("none", guard.run_once()["handoff"]["action"])
        self.assertEqual(before, len(self.notify_calls))
        self.assertEqual([], self.closes())

    def test_warn_restart_adopts_handoff_old_as_current_helm(self):
        """B1c：warn 期重启 + 残留旧舵，resolver 采用 handoff old（现役），不认回残留旧舵。"""
        self.prepare(config={
            "portfolioDir": ".planning/portfolio",
            "helm": {"agentName": "helm-1"},
        })
        self.write_handoff(
            "phase=warn\nold=helm-1-2\noldPane=wX:pNEW\noldTab=wX:tNEW\n"
            "new=helm-1-2\nnewPane=wX:pNEW\nnewTab=wX:tNEW\n"
            "time=2026-11-10T16:40:00Z\nreason=ctx 320k/1M >= 30% 预警阈值\n"
            "result=ok\nsuccessorWasWorking=1\n"
        )
        self.write_registry({
            "helm-1": {"role": "helm", "pane": self.OLD_PANE,
                       "startedAt": "2026-11-10T08:00:00Z"},
            "helm-1-2": {"role": "helm", "pane": self.NEW_PANE,
                         "startedAt": "2026-11-10T16:30:00Z"},
        })
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "workspace_id": "wX", "agent_status": "idle"},
        ]
        self.pane_outputs[self.OLD_PANE] = "ctx 650k/1M"
        self.pane_outputs[self.NEW_PANE] = "ctx 320k/1M"

        resolved = cg.resolve_guard_helm(self.root, self.crew, None)
        self.assertEqual("helm-1-2", resolved)
        guard = self.make_guard(helm=resolved)
        result = guard.run_once()
        self.assertEqual("none", result["handoff"]["action"])
        self.assertEqual("helm-1-2", guard.helm)
        self.assertEqual([], self.dispatch_calls)
        self.assertEqual([], self.notify_calls)

    def test_restart_resolver_prefers_current_helm_by_phase(self):
        """B3：warn→old、started/done(ok)→new、done(failed)→配置 helm、显式 --helm 优先。"""
        config = {"portfolioDir": ".planning/portfolio",
                  "helm": {"agentName": "helm-1"}}
        self.prepare(config=config)
        self.write_handoff("phase=started\nold=helm-1\nnew=helm-1-2\n")
        self.assertEqual(
            "helm-1-2", cg.resolve_guard_helm(self.root, self.crew, None))
        self.write_handoff("phase=warn\nold=helm-1-2\noldPane=wX:pNEW\n")
        self.assertEqual(
            "helm-1-2", cg.resolve_guard_helm(self.root, self.crew, None))
        self.write_handoff("phase=done\nold=helm-1\nnew=helm-1-2\nresult=failed\n")
        self.assertEqual(
            "helm-1", cg.resolve_guard_helm(self.root, self.crew, None))
        self.write_handoff("phase=done\nold=helm-1\nnew=helm-1-2\n")
        self.assertEqual(
            "helm-1-2", cg.resolve_guard_helm(self.root, self.crew, None))
        self.assertEqual(
            "helm-x", cg.resolve_guard_helm(self.root, self.crew, "helm-x"))

    def test_missing_successor_fails_after_three_rounds(self):
        """B2：继任缺失连续 3 轮 → failed + 通知，不自动重试、不关旧舵。"""
        self.prepare()
        self.started_handoff()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "idle"},
        ]
        guard = self.make_guard()
        for _ in range(cg.HANDOFF_STALL_ROUNDS - 1):
            result = guard.run_once()
            self.assertEqual("wait", result["handoff"]["action"])
            self.assertEqual("started", self.handoff_fields()["phase"])
        failed = guard.run_once()
        self.assertEqual("failed", failed["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("首报等待", fields["error"])
        self.assertEqual([], self.closes())
        body = self.notify_calls[0][1]
        self.assertIn("不存在", body)
        self.assertIn("保留", body)
        self.assertNotIn("清理", body)
        self.assertEqual("none", guard.run_once()["handoff"]["action"])

    def test_blocked_successor_fails_after_three_rounds(self):
        """B2：继任 blocked 连续 3 轮 → failed + 通知。"""
        self.prepare()
        self.started_handoff()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "agent_status": "blocked"},
        ]
        guard = self.make_guard()
        for _ in range(cg.HANDOFF_STALL_ROUNDS - 1):
            self.assertEqual("wait", guard.run_once()["handoff"]["action"])
        failed = guard.run_once()
        self.assertEqual("failed", failed["handoff"]["action"])
        self.assertEqual("failed", self.handoff_fields()["result"])
        self.assertTrue(any("blocked" in body for _, body in self.notify_calls))
        self.assertEqual([], self.closes())

    def test_idle_successor_without_first_report_fails_after_three_rounds(self):
        """B2r：重启窗口内继任已 idle 但 successorWasWorking≠1 → 有界等待后 failed。"""
        self.prepare()
        self.started_handoff(successor_was_working="0")
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": self.NEW_PANE, "tab_id": "wX:tNEW",
             "agent_status": "idle"},
        ]
        guard = self.make_guard()
        for _ in range(cg.HANDOFF_STALL_ROUNDS - 1):
            result = guard.run_once()
            self.assertEqual("wait", result["handoff"]["action"])
            self.assertEqual("started", self.handoff_fields()["phase"])
        failed = guard.run_once()
        self.assertEqual("failed", failed["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        self.assertIn("idle", fields["error"])
        self.assertIn("首报等待", fields["error"])
        self.assertEqual([], self.closes())
        body = self.notify_calls[0][1]
        self.assertIn("保留", body)
        self.assertNotIn("清理", body)
        self.assertEqual("none", guard.run_once()["handoff"]["action"])

    def test_done_failed_restart_keeps_configured_helm(self):
        """B3：done(failed) 重启后叫醒真实旧舵，不指向失败继任名。"""
        config = {"portfolioDir": ".planning/portfolio",
                  "helm": {"agentName": "helm-1"}}
        self.prepare(config=config)
        self.write_handoff(
            "phase=done\nold=helm-1\noldPane=wX:pOLD\noldTab=wX:tOLD\n"
            "new=helm-1-2\nnewPane=wX:pNEW\nnewTab=wX:tNEW\n"
            "time=2026-11-10T16:18:00Z\nresult=failed\nerror=dispatch start: boom\n"
        )
        resolved = cg.resolve_guard_helm(self.root, self.crew, None)
        self.assertEqual("helm-1", resolved)

        self.write_fixture(stream=STREAM_PLAN, portfolio=EMPTY_PORTFOLIO, config=config)
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "agent_status": "idle"},
        ]
        self.pane_outputs[self.OLD_PANE] = "ctx 650k/1M"
        guard = self.make_guard(helm=resolved)
        guard.run_once()
        guard.run_once()
        self.assertEqual("helm-1", self.prompt_calls[0][0])
        self.assertEqual([], self.dispatch_calls)

    def test_failure_notify_includes_created_pane_and_tab(self):
        """B4：失败通知带已创建的 newPane/newTab，便于人工清理；不自动回滚。"""
        self.prepare()
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"},
        ]
        self.pane_outputs[self.OLD_PANE] = "ctx 650k/1M"

        def override(args):
            if args[:1] == ["start"]:
                return 1, "", "start boom"
            return None

        self.dispatch_override = override
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual("failed", result["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("failed", fields["result"])
        body = self.notify_calls[0][1]
        self.assertIn("wX:pNEW", body)
        self.assertIn("wX:tNEW", body)
        self.assertIn("清理", body)
        self.assertNotIn("保留", body)
        self.assertEqual([], self.closes())

    def test_stale_registry_worker_pane_is_failsafe(self):
        """B5：registry 陈旧条目指向 worker pane → 关系不可解释，fail-safe 不动作。"""
        self.prepare()
        self.write_registry({"helm-1": {"role": "helm", "pane": "wX:pB"}})
        self.agents = [
            {"name": "builder-1", "pane_id": "wX:pB", "tab_id": "wX:tBUILD",
             "agent_status": "idle"},
        ]
        self.pane_outputs["wX:pB"] = "ctx 650k/1M"
        guard = self.make_guard()

        result = guard.run_once()
        self.assertEqual("none", result["handoff"]["action"])
        self.assertEqual("helm-1", guard.helm)
        # T69② 起 worker ctx 扫描合法读 worker pane；helm pane 不得被读
        self.assertTrue(all(pane == "wX:pB" for pane, _ in self.pane_reads))
        self.assertEqual([], self.dispatch_calls)
        self.assertFalse((self.crew / "guard.handoff").exists())
        self.assertTrue(any("不可解释" in line for line in self.logs))


RETRO_FEEDBACK_ONE_OPEN = """# 编排体系自学习反馈

## 流程反馈（复盘信箱）

| ID | 场景/问题 | 影响 | 建议（文件+条目） | 预判去向 | 上报者 | 日期 | 状态 | 去向 |
|---|---|---|---|---|---|---|---|---|
| FB-001 | 流程 A | 影响 A | `references/crew-rules.md` 改一句 | 改规则 R5 | builder | 2026-11-01 | 待复盘 | - |
| FB-002 | 流程 B | 影响 B | 改 SOP 步骤 | 改 SOP 步骤 | runner | 2026-11-02 | 已闭环 | 已改 |
"""

RETRO_FEEDBACK_NONE_OPEN = """# 编排体系自学习反馈

## 流程反馈（复盘信箱）

| ID | 场景/问题 | 影响 | 建议（文件+条目） | 预判去向 | 上报者 | 日期 | 状态 | 去向 |
|---|---|---|---|---|---|---|---|---|
| FB-002 | 流程 B | 影响 B | 改 SOP 步骤 | 改 SOP 步骤 | runner | 2026-11-02 | 已闭环 | 已改 |
"""

RETRO_FEEDBACK_BROKEN = """# 编排体系自学习反馈

## 流程反馈（复盘信箱）

| ID | 场景/问题 | 影响 | 建议（文件+条目） | 预判去向 | 上报者 | 日期 | 状态 | 去向 |
|---|---|---|---|---|---|---|---|---|
| FB-001 | 少了列 | 影响 A |
"""


class RetroDueTest(GuardTestBase):
    """2.2.4 guard 周复盘兜底：到期条件 / 每日一次 / fail-safe / 文案。"""

    def setUp(self):
        super().setUp()
        self.now[0] = datetime(2026, 11, 10, 12, 0).timestamp()

    def prepare(self, feedback: str | None = RETRO_FEEDBACK_ONE_OPEN,
                retro_days: tuple[str, ...] = ()) -> None:
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        if feedback is not None:
            write_file(self.root / ".planning" / "portfolio" / "feedback.md", feedback)
        for day in retro_days:
            write_file(self.root / ".planning" / "portfolio" / f"retro-{day}.md", "# 复盘\n")

    def test_no_feedback_file_no_retro_wake(self):
        self.prepare(feedback=None)
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("读取失败" in line for line in self.logs))

    def test_no_open_rows_no_retro_wake(self):
        self.prepare(feedback=RETRO_FEEDBACK_NONE_OPEN)
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)

    def test_open_without_retro_report_wakes(self):
        self.prepare()
        result = self.make_guard().run_once()
        self.assertTrue(result["woke"])
        self.assertTrue(result.get("retro"))
        message = self.prompt_calls[0][1]
        self.assertIn("feedback 待复盘 1 条", message)
        self.assertIn("最近复盘 无", message)
        self.assertIn(".planning/portfolio/retro-20261110.md", message)
        self.assertEqual("2026-11-10",
                         (self.crew / "retro.wake").read_text(encoding="utf-8").strip())

    def test_recent_report_no_wake(self):
        self.prepare(retro_days=("20261108",))
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)

    def test_overdue_wakes_once_per_day_then_next_day(self):
        self.prepare(retro_days=("20261001",))
        guard = self.make_guard()
        first = guard.run_once()
        self.assertTrue(first["woke"])
        message = self.prompt_calls[0][1]
        self.assertIn("最近复盘 20261001", message)
        guard.run_once()
        self.assertEqual(1, len(self.prompt_calls))

        self.now[0] += 86400
        guard.run_once()
        self.assertEqual(2, len(self.prompt_calls))
        self.assertEqual("2026-11-11",
                         (self.crew / "retro.wake").read_text(encoding="utf-8").strip())

    def test_broken_table_logs_only(self):
        self.prepare(feedback=RETRO_FEEDBACK_BROKEN)
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("行格式损坏" in line for line in self.logs))

    def test_working_worker_suppresses_retro_wake(self):
        self.prepare(retro_days=("20261001",))
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)

    def test_blocked_takes_priority_over_retro(self):
        self.prepare(retro_days=("20261001",))
        self.agents = [{"name": "builder-1", "agent_status": "blocked"}]
        result = self.make_guard().run_once()
        self.assertTrue(result["woke"])
        self.assertIn("worker 卡住", self.prompt_calls[0][1])
        self.assertNotIn("周复盘到期", self.prompt_calls[0][1])

    def test_invalid_utf8_feedback_fails_safe(self):
        self.prepare(feedback=None)
        (self.root / ".planning" / "portfolio" / "feedback.md").write_bytes(
            b"\xff\xfe\x00broken")
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("跳过复盘提醒" in line for line in self.logs))

    def test_invalid_utf8_marker_fails_safe(self):
        self.prepare()
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "retro.wake").write_bytes(b"\xff\xfe\x00broken")
        result = self.make_guard().run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("跳过复盘提醒" in line for line in self.logs))

    def test_wake_exception_leaves_no_marker_and_retries(self):
        self.prepare()
        guard = self.make_guard()
        calls = {"count": 0}

        def flaky(message: str, agents: list[dict], now: float | None = None) -> str:
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("wake boom")
            return "prompt"

        guard._wake = flaky  # type: ignore[method-assign]
        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertFalse((self.crew / "retro.wake").exists())
        self.assertTrue(any("复盘叫醒失败" in line for line in self.logs))

        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertTrue((self.crew / "retro.wake").exists())

    def test_broken_feedback_does_not_block_ordinary_wake(self):
        self.write_fixture(stream=STREAM_PLAN, portfolio=EMPTY_PORTFOLIO)
        write_file(self.root / ".planning" / "portfolio" / "feedback.md",
                   RETRO_FEEDBACK_BROKEN)
        guard = self.make_guard()
        guard.run_once()
        self.assertEqual([], self.prompt_calls)
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        message = self.prompt_calls[0][1]
        self.assertIn("guard 叫醒：有待办", message)
        self.assertNotIn("周复盘到期", message)

    def test_broken_marker_does_not_block_ordinary_wake(self):
        self.write_fixture(stream=STREAM_PLAN, portfolio=EMPTY_PORTFOLIO)
        write_file(self.root / ".planning" / "portfolio" / "feedback.md",
                   RETRO_FEEDBACK_ONE_OPEN)
        self.crew.mkdir(parents=True, exist_ok=True)
        (self.crew / "retro.wake").write_bytes(b"\xff\xfe\x00broken")
        guard = self.make_guard()
        guard.run_once()
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        message = self.prompt_calls[0][1]
        self.assertIn("guard 叫醒：有待办", message)
        self.assertNotIn("周复盘到期", message)


class PeriodicWriteWarningTest(GuardTestBase):
    """A-F3：周期 --write 成功但含 rotate 建议时写 guard.log 告警。"""

    def test_rotate_hint_logged_without_changing_flow(self):
        self.write_fixture()
        self.status_result = (
            0, "[警告] portfolio progress 体量偏大（100 条 ≥ 100），"
               "建议交接时 --rotate --apply --keep 10 归档。")
        result = self.make_guard().run_once()
        self.assertTrue(any("体量告警" in log and "建议交接时 --rotate" in log
                            for log in self.logs), self.logs)
        self.assertEqual(["--write"], self.status_calls)
        self.assertIn("woke", result)


class PlanStatusArgsTest(GuardTestBase):
    """工单 02 断言 8：生产 run_plan_status 按 argv 列表传 rotate 多参数。"""

    def test_rotate_argv_passed_as_separate_elements(self):
        completed = mock.Mock(returncode=0, stdout="ok", stderr="")
        with mock.patch.object(cg.subprocess, "run", return_value=completed) as run:
            code, output = cg.run_plan_status_args(
                self.root, ["--rotate", "--apply", "--keep", "10"])
        self.assertEqual((0, "ok"), (code, output))
        argv = run.call_args[0][0]
        self.assertEqual(["--rotate", "--apply", "--keep", "10"], argv[2:6])
        self.assertEqual("--root", argv[6])
        self.assertEqual(str(self.root), argv[7])
        self.assertNotIn("--rotate --apply --keep 10", argv)

    def test_single_flag_helper_still_passes_one_arg(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(cg.subprocess, "run", return_value=completed) as run:
            cg.run_plan_status(self.root, "--write")
        argv = run.call_args[0][0]
        self.assertEqual(["--write"], argv[2:3])
        self.assertEqual("--root", argv[3])


class ReworkReminderTest(GuardTestBase):
    """T24/FB-020：同一 task 回执 ≥3 的非阻断提醒（不叫醒、不改 plan、不删回执）。"""

    def write_receipts(self, task: str | None, count: int, prefix: str = "r") -> None:
        for index in range(count):
            data = {"receipt_id": f"{prefix}{index}"}
            if task is not None:
                data["task"] = task
            write_file(self.crew / "inbox" / f"{prefix}{index}.json",
                       json.dumps(data, ensure_ascii=False))

    def write_plan(self, task: str, status: str) -> None:
        write_file(
            self.root / ".planning" / task / "task_plan.md",
            f"# Task Plan: {task}\n\n## Phases\n\n"
            f"### Phase 1: 夹具\n- **Status:** {status}\n",
        )

    def rework_logs(self) -> list[str]:
        return [log for log in self.logs if "返工提醒" in log]

    def test_two_receipts_do_not_remind(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 2)
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())
        self.assertEqual([], self.prompt_calls)

    def test_three_receipts_remind_once_without_wake(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        self.write_receipts("T23", 1, prefix="other")
        self.write_plan("T22", "in_progress")
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual([{"task": "T22", "receipts": 3}], result["rework"])
        self.assertEqual([], self.prompt_calls)
        self.assertFalse(result["woke"])
        logs = self.rework_logs()
        self.assertEqual(1, len(logs), logs)
        self.assertIn("task=T22", logs[0])
        self.assertIn("回执 3 条", logs[0])
        self.assertIn("rework-reset", logs[0])
        self.assertIn("需人签", logs[0])

        # 同一进程下一轮：JSON 仍报告，但日志不重复
        result = guard.run_once()
        self.assertEqual([{"task": "T22", "receipts": 3}], result["rework"])
        self.assertEqual(1, len(self.rework_logs()))

    def test_invalid_json_and_missing_task_ignored(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 2)
        write_file(self.crew / "inbox" / "broken.json", "{not json")
        self.write_receipts(None, 3, prefix="notask")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])

    def test_duplicate_receipt_id_counted_once(self):
        # S4：按 receipt_id 去重（R3），重复投递/复制不得触发返工提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_plan("T-X", "in_progress")
        for index in range(3):
            write_file(self.crew / "inbox" / f"dup{index}.json",
                       json.dumps({"receipt_id": "SAME-ID", "task": "T-X"}))
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())

    def test_missing_receipt_id_falls_back_to_filename(self):
        # S4：receipt_id 缺失时回退文件名去重
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_plan("T-X", "in_progress")
        for index in range(3):
            write_file(self.crew / "inbox" / f"noid{index}.json",
                       json.dumps({"task": "T-X"}))
        result = self.make_guard().run_once()
        self.assertEqual([{"task": "T-X", "receipts": 3}], result["rework"])

    def test_completed_task_plan_not_reminded(self):
        # S3/A-S5：task 的 plan 全部 Status=complete（已收口）→ 不提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        self.write_plan("T22", "complete")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())

    def test_completed_plan_with_skeleton_comment_not_reminded(self):
        # S3/A-S5：plan 头部 HTML 骨架注释里的示例 Status 字样不得当作活跃状态
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        write_file(
            self.root / ".planning" / "T22" / "task_plan.md",
            "# Task Plan: T22\n\n"
            "<!-- 约定：每个 Phase 下恰好一行 "
            "`**Status:** pending|in_progress|complete`；勿手改结构 -->\n\n"
            "## Phases\n\n### Phase 1: 夹具\n- **Status:** complete\n",
        )
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())

    def test_active_task_plan_still_reminded(self):
        # S3/A-S5：活跃 plan（Status≠complete）仍按阈值提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        self.write_plan("T22", "pending")
        result = self.make_guard().run_once()
        self.assertEqual([{"task": "T22", "receipts": 3}], result["rework"])
        self.assertEqual(1, len(self.rework_logs()))

    def test_mixed_phase_status_counts_as_active(self):
        # S3/A-S5：多 Phase 中仍有非 complete → 视为活跃
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        write_file(
            self.root / ".planning" / "T22" / "task_plan.md",
            "# Task Plan: T22\n\n## Phases\n\n"
            "### Phase 1: 已完成\n- **Status:** complete\n\n"
            "### Phase 2: 进行中\n- **Status:** in_progress\n",
        )
        result = self.make_guard().run_once()
        self.assertEqual([{"task": "T22", "receipts": 3}], result["rework"])

    def test_task_without_plan_not_reminded(self):
        # S3/A-S5：无 .planning/<task>/task_plan.md 的历史/编号 task 不提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2210", 3, prefix="old")
        self.write_receipts("T21", 3, prefix="hist")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())

    def test_slice_task_maps_to_active_stream_plan(self):
        # B1：切片式 task（patch-2213-code）按最长前缀映射到活跃流 plan（patch-2213）
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213-code", 3)
        self.write_plan("patch-2213", "in_progress")
        result = self.make_guard().run_once()
        self.assertEqual([{"task": "patch-2213-code", "receipts": 3}], result["rework"])
        self.assertEqual(1, len(self.rework_logs()))

    def test_slice_task_maps_to_completed_stream_plan(self):
        # B1：切片映射到的流 plan 已全部 complete → 仍不提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213-code", 3)
        self.write_plan("patch-2213", "complete")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])

    def test_slash_suffix_task_maps_to_stream_plan(self):
        # B1：历史切片名（流/切片）同样按前缀映射
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        task = "patch-2210/03-release（发布）"
        self.write_receipts(task, 3)
        self.write_plan("patch-2210", "in_progress")
        result = self.make_guard().run_once()
        self.assertEqual([{"task": task, "receipts": 3}], result["rework"])

    def test_longest_prefix_wins(self):
        # B1：最长匹配优先——短流活跃、长切片已收口 → 不提醒
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213-code", 3)
        self.write_plan("patch-2213", "in_progress")
        self.write_plan("patch-2213-code", "complete")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])

    def test_invalid_task_names_do_not_crash_or_escape(self):
        # S1：../../escape 不得越界读取；NUL 不得抛异常；整轮正常返回
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        write_file(
            self.base / "escape" / "task_plan.md",
            "# Task Plan: outside\n\n## Phases\n\n### Phase 1\n- **Status:** in_progress\n",
        )
        self.write_receipts("../../escape", 3, prefix="esc")
        self.write_receipts("bad\x00name", 3, prefix="nul")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        joined = "\n".join(self.logs)
        self.assertIn("返工提醒跳过", joined)
        self.assertIn("../../escape", joined)

    def test_non_utf8_plan_skipped_without_crash(self):
        # S1：坏 UTF-8 plan 记日志跳过（每 task 一次），不拖垮整轮
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213", 3)
        path = self.root / ".planning" / "patch-2213" / "task_plan.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xfe invalid utf-8")
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        skipped = [log for log in self.logs if "返工提醒跳过" in log]
        self.assertEqual(1, len(skipped))
        self.assertIn("plan 读取失败", skipped[0])

    def test_notes_status_does_not_activate_completed_plan(self):
        # S2：非 Phase 区域（## Notes）的 Status 不计入
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213", 3)
        write_file(
            self.root / ".planning" / "patch-2213" / "task_plan.md",
            "# Task Plan: patch-2213\n\n## Phases\n\n### Phase 1: A\n"
            "- **Status:** complete\n\n## Notes\n\n**Status:** pending\n",
        )
        result = self.make_guard().run_once()
        self.assertEqual([], result["rework"])
        self.assertEqual([], self.rework_logs())

    def test_phase_missing_status_counts_as_pending(self):
        # S2：缺 Status 的 Phase 视为 pending → 活跃
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("patch-2213", 3)
        write_file(
            self.root / ".planning" / "patch-2213" / "task_plan.md",
            "# Task Plan: patch-2213\n\n## Phases\n\n### Phase 1: A\n"
            "- **Status:** complete\n\n### Phase 2: B\n- 无状态行\n",
        )
        result = self.make_guard().run_once()
        self.assertEqual([{"task": "patch-2213", "receipts": 3}], result["rework"])

    def test_once_json_reports_rework_and_keeps_receipts(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.write_receipts("T22", 3)
        self.write_plan("T22", "in_progress")
        before = sorted(p.name for p in (self.crew / "inbox").iterdir())
        with mock.patch.object(
            cg, "herdr_agents", return_value=[{"name": "w", "agent_status": "idle"}]
        ), mock.patch.object(
            cg, "run_plan_status", return_value=(0, "看板新鲜")
        ), mock.patch.object(cg, "herdr_prompt"), mock.patch.object(cg, "herdr_notify"):
            code, out, err = self.run_main(
                ["--once", "--json", "--root", str(self.root),
                 "--crew-dir", str(self.crew), "--helm", "helm-1"]
            )
        self.assertEqual(0, code, err)
        payload = json.loads(out)
        self.assertEqual([{"task": "T22", "receipts": 3}], payload["rework"])
        self.assertEqual(before,
                         sorted(p.name for p in (self.crew / "inbox").iterdir()))


# —— 2.2.26 T63：Backlog ⏸ 两态与 idle 兜底叫醒 ——
# AMEND：⏸ 排除规则细化——纯排队 ⏸（下批/batch/评估中）入排队桶（不计 pending、
# 不触发即时叫醒、由 idle 兜底承载）；⏸+外部条件语义（待 Captain 等）不计待办。
BACKLOG_PAUSED_QUEUE_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 下批技能小修 | P1 | - | ⏸ 下批 |
| T2 | 等评估收尾 | P2 | - | ⏸ 评估中 |
"""

BACKLOG_PAUSED_CAPTAIN_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T3 | 待拍板事项 | P1 | - | ⏸ 待 Captain |
"""

BACKLOG_PAUSED_MIXED_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 下批技能小修 | P1 | - | ⏸ 下批 |
| T3 | 待拍板事项 | P1 | - | ⏸ 待 Captain |
"""

# —— 2.2.31：已完成判定先于 ⏸/外部标记匹配（guard-noise 流根因 1）——
# 真实形态（本仓 portfolio T40–T49）：「✅ 已完成（…发布…；待 Captain 签认）」。
# 已完成行带「待 Captain 签认」「⏸」后缀时不得误入暂停桶；「✅」单独出现也算完成。
BACKLOG_DONE_VARIANTS_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 完成带签认 | P1 | - | ✅ 已完成（2.2.16 本地发布；待 Captain 签认） |
| T2 | 完成带暂停 | P1 | - | 已完成 ⏸ |
| T3 | 完成勾号 | P1 | - | ✅ |
| T4 | 完成英文 | P1 | - | done（待 Captain 签认） |
| T5 | 未完成待Captain | P1 | - | 待 Captain |
| T6 | 未完成暂停 | P1 | - | ⏸ 下批 |
"""

HELM_IDLE = {"name": "helm-1", "agent_status": "idle"}


class BacklogPausedScanTest(GuardTestBase):
    """AMEND·T63：scan_todos 三桶——⏸ 排队与 ⏸ 外部条件分桶，均不计 pending。"""

    def test_paused_queue_rows_go_to_paused_bucket(self):
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_QUEUE_PORTFOLIO)
        pending, residual, paused = cg.scan_todos(self.root)
        self.assertEqual(pending, [])
        self.assertEqual(residual, [])
        texts = [todo.text for todo in paused]
        self.assertEqual(len(texts), 2)
        self.assertTrue(all(text.startswith("⏸") for text in texts))
        self.assertTrue(all("排队" in text for text in texts))
        self.assertTrue(any("T1 下批技能小修" in text for text in texts))
        self.assertTrue(any("T2 等评估收尾" in text for text in texts))

    def test_paused_captain_rows_marked_external(self):
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_CAPTAIN_PORTFOLIO)
        pending, residual, paused = cg.scan_todos(self.root)
        self.assertEqual(pending, [])
        self.assertEqual(len(paused), 1)
        self.assertIn("待外部", paused[0].text)
        self.assertIn("T3 待拍板事项", paused[0].text)

    def test_mixed_paused_rows_split_correctly(self):
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_MIXED_PORTFOLIO)
        pending, _, paused = cg.scan_todos(self.root)
        self.assertEqual(pending, [])
        self.assertEqual(len(paused), 2)
        self.assertTrue(any("待外部" in todo.text for todo in paused))
        self.assertTrue(any("排队" in todo.text for todo in paused))

    def test_unpaused_backlog_rows_still_go_to_pending(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=BACKLOG_MIXED_PORTFOLIO)
        pending, _, paused = cg.scan_todos(self.root)
        self.assertEqual([todo.text for todo in pending],
                         ["T1 未完成主线", "T4 边界词", "T5 中文未完成"])
        # T2「待 C1」属外部条件语义：入暂停桶（⏸ 待外部），不计 pending（AMEND）
        self.assertEqual([todo.text for todo in paused],
                         ["⏸ 待外部：T2 待拍板"])

    def test_blocked_backlog_rows_go_to_paused_bucket(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=PORTFOLIO_PLAN)
        pending, _, paused = cg.scan_todos(self.root)
        self.assertEqual([todo.text for todo in pending],
                         ["流A首个切片", "T1 可执行任务"])
        self.assertIn("⛔ 阻塞：T3 已阻塞任务", [todo.text for todo in paused])

    def test_paused_rows_do_not_trigger_immediate_wake(self):
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_QUEUE_PORTFOLIO)
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual(first["backlogPaused"], 2)
        second = guard.run_once()
        self.assertFalse(second["woke"], "排队 ⏸ 不触发两轮 debounce 即时叫醒")
        self.assertEqual(self.prompt_calls, [])

    def test_done_rows_with_pause_suffix_never_enter_any_bucket(self):
        """2.2.31 根因 1：已完成判定先于 ⏸/外部标记匹配——已完成行不进任何桶。"""
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_DONE_VARIANTS_PORTFOLIO)
        pending, residual, paused = cg.scan_todos(self.root)
        # T1「✅ 已完成（…；待 Captain 签认）」/ T2「已完成 ⏸」/ T3「✅」/
        # T4「done（待 Captain 签认）」：不进 pending、不进 ⏸ 待外部/排队、不进 ⛔。
        self.assertEqual(pending, [], "已完成行不得进 pending")
        self.assertEqual(residual, [])
        # 对照：未完成行不受影响——「待 Captain」仍归 ⏸ 待外部、「⏸ 下批」仍归排队。
        self.assertEqual([todo.text for todo in paused],
                         ["⏸ 待外部：T5 未完成待Captain",
                          "⏸ 排队：T6 未完成暂停（⏸ 下批）"],
                         "暂停桶只含未完成对照行，已完成行不得混入")


class IdleBackoffWakeTest(GuardTestBase):
    """2.2.26 T63：空闲兜底叫醒——安静期报平安 + Backlog 全量清单（含 ⏸ 条目）。"""

    def config(self, rounds: int) -> dict:
        return {"guard": {"idleWakeRounds": rounds}}

    def quiet_fixture(self, config: dict) -> None:
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_QUEUE_PORTFOLIO,
                           config=config)

    def test_idle_backoff_wakes_after_n_rounds(self):
        self.quiet_fixture(self.config(3))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for i in range(3):
            result = guard.run_once()
            if i < 2:
                self.assertFalse(result["woke"], f"第 {i + 1} 轮未达阈值不叫")
        self.assertTrue(result["woke"], "第 3 轮空闲兜底叫醒")
        self.assertTrue(result.get("idleWake"))
        message = self.prompt_calls[0][1]
        self.assertIn("报平安", message)
        self.assertIn("空闲", message)
        # AMEND：Backlog 全量清单含 ⏸ 条目
        self.assertIn("T1 下批技能小修", message)
        self.assertIn("T2 等评估收尾", message)
        self.assertIn("⏸ 排队", message)

    def test_idle_backoff_not_before_n_rounds_default(self):
        self.quiet_fixture({})  # 默认 idleWakeRounds=5
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for i in range(4):
            result = guard.run_once()
            self.assertFalse(result["woke"], f"第 {i + 1} 轮未达默认 5 轮不叫")
        self.assertEqual(self.prompt_calls, [])

    def test_idle_backoff_helm_working_no_wake(self):
        self.quiet_fixture(self.config(2))
        self.agents = [{"name": "helm-1", "agent_status": "working"}]
        guard = self.make_guard()
        for _ in range(3):
            result = guard.run_once()
            self.assertFalse(result["woke"], "总舵 working 期间不兜底叫醒")
        self.agents = [HELM_IDLE]
        result = guard.run_once()
        self.assertFalse(result["woke"], "转 idle 后第 1 轮计数从零起")
        result = guard.run_once()
        self.assertTrue(result["woke"])

    def test_idle_backoff_helm_blocked_no_wake(self):
        self.quiet_fixture(self.config(2))
        self.agents = [{"name": "helm-1", "agent_status": "blocked"}]
        guard = self.make_guard()
        for _ in range(3):
            result = guard.run_once()
            self.assertFalse(result["woke"], "总舵 blocked 期间不兜底叫醒")

    def test_idle_backoff_disabled_when_zero(self):
        self.quiet_fixture(self.config(0))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for _ in range(6):
            result = guard.run_once()
            self.assertFalse(result["woke"], "idleWakeRounds=0 关闭兜底")
        self.assertEqual(self.prompt_calls, [])

    def test_idle_backoff_respects_cooldown(self):
        self.quiet_fixture(self.config(2))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for _ in range(2):
            guard.run_once()
        self.assertTrue(self.prompt_calls, "首次兜底叫醒发生")
        # 触发后计数清零：重新积累 2 轮再触达兜底条件，但仍在冷却窗口内
        for i in range(2):
            result = guard.run_once()
            self.assertFalse(result["woke"], f"冷却窗口内第 {i + 1} 轮不重复叫醒")
        self.assertTrue(any("冷却中" in line for line in self.logs))
        self.assertEqual(len(self.prompt_calls), 1)

    def test_idle_backoff_counter_resets_on_working_worker(self):
        self.quiet_fixture(self.config(3))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for _ in range(2):
            guard.run_once()
        self.agents = [HELM_IDLE, {"name": "worker-1", "agent_status": "working"}]
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(result["idleQuietRounds"], 0, "出现 working 清零计数")
        self.agents = [HELM_IDLE]
        for i in range(3):
            result = guard.run_once()
            # FB-057①：worker 消失轮触发完成边沿叫醒（冷却计时起点），轮间推进
            # 跨过全局冷却，保证最后一段断言的是兜底通道而非冷却抑制。
            self.now[0] += 700
        self.assertTrue(result["woke"], "重新积累满 3 轮后兜底叫醒")

    def test_idle_backoff_counter_resets_when_pending_appears(self):
        self.quiet_fixture(self.config(3))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for _ in range(2):
            guard.run_once()
        # 有可派 pending 时交回既有 pending 通道，兜底计数清零
        self.write_fixture(portfolio=BACKLOG_MIXED_PORTFOLIO, config=self.config(3))
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual(result["idleQuietRounds"], 0)

    def test_idle_backoff_lists_external_and_queue_rows(self):
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_MIXED_PORTFOLIO,
                           config=self.config(1))
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        result = guard.run_once()
        self.assertTrue(result["woke"])
        message = self.prompt_calls[0][1]
        self.assertIn("⏸ 排队", message)
        self.assertIn("⏸ 待外部", message)
        self.assertIn("T3 待拍板事项", message)


# —— 2.2.31：idleWakeRounds=0 状态变化驱动兜底（guard-noise 流要件 1–7）——
# 首次基线不叫醒；变化（任一桶集合 diff）→ 下一轮叫醒一次只含增量；无变化完全
# 静默（guard.log 每 30 轮心跳行）；被冷却吞掉的增量持久化、冷却期满补发一次；
# ⏸ 排队条目「今天 ≥ 文本中 YYYY-MM-DD」视为到期变化；基线落盘跨重启可比较。
QUEUE_WITH_DATE_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 下批技能小修 | P1 | - | ⏸ 下批 |
| T9 | 到期复盘 | P2 | - | ⏸ 复盘 2026-11-20 或第 5 次先到者 |
"""

QUEUE_ADDED_ROW_PORTFOLIO = """# Task Plan: 总览

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
| T1 | 下批技能小修 | P1 | - | ⏸ 下批 |
| T2 | 等评估收尾 | P2 | - | ⏸ 评估中 |
| T7 | 新排队项 | P2 | - | ⏸ 下批 2026-12-01 |
"""


class IdleChangeWakeTest(GuardTestBase):
    """2.2.31 状态变化驱动：idleWakeRounds=0 新口径，行为断言（F8 真驱动 run_once）。"""

    def quiet0(self, portfolio: str = BACKLOG_PAUSED_QUEUE_PORTFOLIO) -> None:
        self.write_fixture(stream=EMPTY_STREAM, portfolio=portfolio,
                           config={"guard": {"idleWakeRounds": 0}})

    def settle(self, guard: "cg.Guard", rounds: int) -> None:
        """推进 N 轮安静期（每轮推进时钟避开冷却纠缠），断言全程零叫醒。"""
        for _ in range(rounds):
            self.now[0] += 700
            result = guard.run_once()
            self.assertFalse(result["woke"], "安静无变化轮不得叫醒")
        self.assertEqual(self.prompt_calls, [])

    def test_first_run_builds_baseline_silently_and_state_file_exists(self):
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        result = guard.run_once()
        self.assertFalse(result["woke"], "首轮建基线不叫醒（防升级即叫）")
        self.assertEqual(self.prompt_calls, [])
        state = json.loads((self.crew / "guard.state.json").read_text(encoding="utf-8"))
        self.assertTrue(state["buckets"]["paused"], "基线含 paused 桶")
        self.assertIn("digest", state)

    def test_quiet_rounds_stay_silent_and_log_heartbeat_every_30(self):
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        self.settle(guard, 31)
        heartbeats = [line for line in self.logs if "存活心跳" in line]
        self.assertEqual(len(heartbeats), 1, "每 30 轮写一行存活心跳（第 30 轮）")

    def test_paused_addition_wakes_once_with_delta_only(self):
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        self.settle(guard, 2)
        self.write_fixture(stream=EMPTY_STREAM, portfolio=QUEUE_ADDED_ROW_PORTFOLIO,
                           config={"guard": {"idleWakeRounds": 0}})
        self.now[0] += 700
        result = guard.run_once()
        self.assertTrue(result["woke"], "Backlog 排队新增 → 下一轮即叫醒")
        self.assertTrue(result.get("idleChangeWake"))
        message = self.prompt_calls[0][1]
        self.assertIn("T7 新排队项", message, "消息含变化增量")
        self.assertNotIn("报平安", message, "新口径不再发报平安全量格式")
        # 同一状态连续多轮不重复叫醒（含冷却过期后）
        for _ in range(3):
            self.now[0] += 700
            result = guard.run_once()
            self.assertFalse(result["woke"], "同状态不重复叫醒")
        self.assertEqual(len(self.prompt_calls), 1)

    def test_swallowed_change_replayed_after_cooldown(self):
        # 要件 7：变化只受全局冷却；冷却内再次变化被吞 → 持久化，冷却期满补发一次。
        # （桶收窄后 workers 进出不进指纹、非安静期兜底不介入——既有通道行为不变。）
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        self.settle(guard, 2)
        # 安静期 paused 新增 → 兜底叫醒（last_wake_at 计时起点）
        self.write_fixture(stream=EMPTY_STREAM, portfolio=QUEUE_ADDED_ROW_PORTFOLIO,
                           config={"guard": {"idleWakeRounds": 0}})
        self.now[0] += 700
        result = guard.run_once()
        self.assertTrue(result["woke"] and result.get("idleChangeWake"), "paused 新增叫醒")
        # 冷却（默认 10 分钟）内 paused 再变（T7 移除）→ 被吞、持久化
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_QUEUE_PORTFOLIO,
                           config={"guard": {"idleWakeRounds": 0}})
        self.now[0] += 60
        result = guard.run_once()
        self.assertFalse(result["woke"], "冷却中被吞")
        state = json.loads((self.crew / "guard.state.json").read_text(encoding="utf-8"))
        self.assertTrue(state.get("pendingNotify"), "被吞增量持久化在 state")
        self.assertTrue(any("留待补发" in line for line in self.logs), "被吞记日志")
        # 冷却期满：补发一次（含移除增量）
        self.now[0] += 700
        result = guard.run_once()
        self.assertTrue(result["woke"], "冷却期满补发一次")
        message = self.prompt_calls[-1][1]
        self.assertIn("T7 新排队项", message, "补发消息含移除增量")
        self.now[0] += 700
        self.assertFalse(guard.run_once()["woke"], "补发后不重复")

    def test_worker_change_leaves_wake_to_existing_channels(self):
        # 桶收窄（实现取舍）：pending/worker 变化打断安静期，由既有通道承载
        # （fast_wake/pending 主叫醒），新兜底不介入——2.2.30 已验收行为不改变。
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        self.settle(guard, 2)
        self.agents = [HELM_IDLE, {"name": "worker-1", "agent_status": "working"}]
        self.now[0] += 700
        result = guard.run_once()
        self.assertFalse(result["woke"], "worker 进入不触发状态变化兜底")
        self.assertFalse(result.get("idleChangeWake"))
        self.assertEqual(self.prompt_calls, [])
        state = json.loads((self.crew / "guard.state.json").read_text(encoding="utf-8"))
        self.assertNotIn("workers", state["buckets"], "指纹只含盲区两桶")
        self.agents = [HELM_IDLE]
        self.now[0] += 700
        result = guard.run_once()
        self.assertTrue(result["woke"], "worker 消失仍走完成边沿叫醒")
        self.assertFalse(result.get("idleChangeWake"), "该叫醒来自 fast_wake 通道")
        self.assertIn("worker 完成待核收", self.prompt_calls[-1][1])

    def test_due_queue_entry_wakes_once_on_date(self):
        # 要件 5：⏸ 排队条目「今天 ≥ 文本到期日」视为状态变化，一次性叫醒。
        self.quiet0(portfolio=QUEUE_WITH_DATE_PORTFOLIO)
        self.agents = [HELM_IDLE]
        self.now[0] = datetime(2026, 11, 13, 12, 0, 0).timestamp()
        guard = self.make_guard()
        self.settle(guard, 2)  # 到期前安静
        self.now[0] = datetime(2026, 11, 21, 12, 0, 0).timestamp()
        result = guard.run_once()
        self.assertTrue(result["woke"], "到期日已过 → 当轮叫醒")
        self.assertTrue(result.get("idleChangeWake"))
        message = self.prompt_calls[0][1]
        self.assertIn("T9 到期复盘", message)
        self.assertIn("到期", message)
        self.now[0] += 700
        self.assertFalse(guard.run_once()["woke"], "到期只触发一次")

    def test_restart_same_state_does_not_rewake(self):
        # 断言：基线在盘不在内存——重启后同状态不重复叫醒。
        self.quiet0()
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        self.settle(guard, 2)
        successor = self.make_guard()  # 模拟重启：全新实例，内存态为空
        result = successor.run_once()
        self.assertFalse(result["woke"], "重启后同状态不重复叫醒")
        self.assertEqual(self.prompt_calls, [])

    def test_legacy_rounds_keep_report_ping_behavior(self):
        # 兼容口径：idleWakeRounds>0 保留旧兜底（报平安全量清单），新机制不介入。
        self.write_fixture(stream=EMPTY_STREAM,
                           portfolio=BACKLOG_PAUSED_QUEUE_PORTFOLIO,
                           config={"guard": {"idleWakeRounds": 3}})
        self.agents = [HELM_IDLE]
        guard = self.make_guard()
        for _ in range(3):
            result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertTrue(result.get("idleWake"), "旧口径仍走 idleWake 兜底")
        self.assertFalse(result.get("idleChangeWake"))
        self.assertIn("报平安", self.prompt_calls[0][1])
        state = json.loads((self.crew / "guard.state.json").read_text(encoding="utf-8"))
        self.assertIsNone(state.get("pendingNotify"), "旧口径不维护补发队列")


if __name__ == "__main__":
    unittest.main(verbosity=2)
