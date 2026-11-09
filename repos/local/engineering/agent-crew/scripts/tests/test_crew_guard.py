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
        self.logs: list[str] = []
        self.prompt_ok = True
        self.status_result = (0, "看板新鲜")

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
            list_agents=lambda: self.agents,
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or self.prompt_ok
            ),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True
            ),
            run_status=lambda root, flag: (
                self.status_calls.append(flag) or self.status_result
            ),
            log_fn=self.logs.append,
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
        pending, residual = cg.scan_todos(self.root)
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

    def test_comment_examples_are_ignored(self):
        self.write_fixture(
            stream=(
                EMPTY_STREAM
                + "\n<!--\n"
                + "### Phase 9: 注释示例\n- [ ] 注释里的待办\n- **Status:** pending\n"
                + "-->\n"
            )
        )
        pending, _ = cg.scan_todos(self.root)
        self.assertNotIn("注释里的待办", [todo.text for todo in pending])


class WakeConditionTest(GuardTestBase):
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

    def test_tmp_prefix_outside_tmp_still_wakes(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "worker-1", "agent_status": "blocked",
                        "cwd": "/tmpfoo/project"}]
        guard = self.make_guard()
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertIn("worker-1", self.prompt_calls[0][1])

    def test_real_blocked_worker_still_wakes(self):
        self.write_fixture(stream=EMPTY_STREAM, portfolio=EMPTY_PORTFOLIO)
        self.agents = [{"name": "builder-1", "agent_status": "blocked",
                        "cwd": "/home/worker/project"}]
        guard = self.make_guard()
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertEqual(len(self.prompt_calls), 1)
        self.assertIn("builder-1", self.prompt_calls[0][1])

    def test_probe_working_still_counts_as_running(self):
        self.write_fixture()
        self.agents = [{"name": "s9-probe", "agent_status": "working",
                        "cwd": "/tmp/s9-probe/session"}]
        guard = self.make_guard()
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
                pending, residual = cg.scan_todos(self.root)
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
        pending, residual = cg.scan_todos(self.root)
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
