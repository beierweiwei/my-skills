#!/usr/bin/env python3
"""2.2.21 修法 B：审批等待（awaiting_approval）卡死检测与安全矩阵回归。

覆盖：
- crew_approvals：segment→run 日志映射、pid 回退、ask/settle 按 callId 配对、
  年龄计算、事故日志片段回放、缺会话/坏日志 fail-soft；
- crew-guard：stuck 来源、阈值/重复/grace/max 阶梯、ask 消失清年龄、
  helm 自身有 ask 时只通知不注入、stuck worker pane 零注入、config 夹取、
  --once --json 输出 stuck；
- 注入降级：crew-msg qoder 直推、dispatch prompt 遇未闭合 ask 不注入；
- init SCRIPT_MANIFEST 登记。

运行：uv run --no-project --with pytest python3 -m pytest scripts/tests/test_2221_stuck_detect.py -q
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent


def load_script(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ca = load_script("crew_approvals", "crew_approvals.py")
cg = load_script("crew_guard_2221", "crew-guard.py")
cm = load_script("crew_msg_2221", "crew-msg.py")
dispatch = load_script("dispatch_2221", "dispatch.py")
init_mod = load_script("init_2221", "init.py")

EMPTY_STREAM = """# Task Plan: Empty

## Phases
"""

STREAM_PLAN = """# Task Plan: Flow A

## Phases

### Phase 1: 进行中
- owner: builder
- [ ] 待办A
- **Status:** in_progress
"""

EMPTY_PORTFOLIO = """# Task Plan: Empty

## Backlog

| # | 目标 | P | 依赖 | 阻塞 |
|---|---|---|---|---|
"""

RUN_ID = "2026-11-12T21-48-20-426+08-00-2io65l-p646296"
SESSION = "a4e2f779-3a22-460f-8098-91ebccf00263"
SLUG = "-home-wjj-projects-demo"
ACCIDENT_CALL = "call_00_dAuKJM8b6pWuhqxeqaqy4005"
ACCIDENT_ASK = (
    "2026-11-12T22:05:51.279+08:00 INFO  debug.message [permission-ask] prompt "
    f"tool=Bash id={ACCIDENT_CALL} "
    'message="This command contains 203 command substitutions — too many to analyze '
    'for catastrophic removals. This requires explicit approval and cannot be '
    'auto-allowed by permission rules." attribution=other'
)
ACCIDENT_SETTLE = (
    "2026-11-13T07:41:31.415+08:00 INFO  debug.message [permission-ask] settle "
    f"tool=Bash id={ACCIDENT_CALL} result=approved"
)


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ask_line(ts: str, call_id: str = "call_01", tool: str = "Bash",
             message: str = "Allow Bash to run: echo hi?") -> str:
    return (f"{ts} INFO  debug.message [permission-ask] prompt "
            f'tool={tool} id={call_id} message="{message}" attribution=other')


def settle_line(ts: str, call_id: str = "call_01", tool: str = "Bash") -> str:
    return (f"{ts} INFO  debug.message [permission-ask] settle "
            f"tool={tool} id={call_id} result=approved")


def approval_entry(*, agent: str = "worker-1", pane: str = "wX:p1",
                   call_id: str = "call_01", age_seconds: int = 16 * 60,
                   tool: str = "Bash",
                   command_preview: str = "Allow Bash to run: echo hi?") -> dict:
    return {
        "agent": agent,
        "pane": pane,
        "call_id": call_id,
        "asked_at": "2026-11-13T00:00:00Z",
        "age_seconds": age_seconds,
        "tool": tool,
        "command_preview": command_preview,
    }


class ApprovalDetectorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.home = self.base / "qoder-home"
        self.run_log = self.home / "logs" / "runs" / RUN_ID / "qodercli.log"
        self.segment = (self.home / "logs" / "sessions" / SLUG / SESSION
                        / "segments" / f"{RUN_ID}.jsonl")
        write_file(self.segment, "{}\n")
        write_file(self.run_log, "")
        self.now = datetime(2026, 11, 13, 7, 0, 0, tzinfo=timezone.utc).timestamp()

    def agent(self, *, name: str = "worker-1", pane: str = "wX:p1",
              session: str = SESSION) -> dict:
        entry = {"name": name, "pane_id": pane, "cwd": "/work",
                 "agent": "qodercli"}
        if session:
            entry["agent_session"] = {"agent": "qodercli", "value": session}
        return entry

    def test_agent_run_log_maps_session_segment_to_run_log(self):
        self.assertEqual(self.run_log, ca.agent_run_log(
            self.agent(), qoder_home=self.home))

    def test_missing_segment_falls_back_to_pid_manifest(self):
        fallback_run = self.home / "logs" / "runs" / "2026-11-13T01-00-00-000+08-00-abc-p424242"
        fallback_log = fallback_run / "qodercli.log"
        write_file(fallback_log, "")
        write_file(fallback_run / "manifest.json",
                   json.dumps({"run_id": fallback_run.name, "pid": 424242}))
        found = ca.agent_run_log(
            self.agent(session="missing-session"), qoder_home=self.home,
            now=self.now, process_info=lambda pane: 424242)
        self.assertEqual(fallback_log, found)

    def test_pid_manifest_scan_ignores_runs_older_than_scan_days(self):
        old_run = self.home / "logs" / "runs" / "2026-10-01T01-00-00-000+08-00-old-p555"
        write_file(old_run / "qodercli.log", "")
        write_file(old_run / "manifest.json", json.dumps({"pid": 555}))
        old = self.now - 8 * 86400
        os.utime(old_run, (old, old))
        found = ca.agent_run_log(
            self.agent(session="missing-session"), qoder_home=self.home,
            now=self.now, process_info=lambda pane: 555)
        self.assertIsNone(found)

    def test_unclosed_ask_has_call_id_age_and_preview(self):
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        write_file(self.run_log, ask_line(asked.isoformat()) + "\n")
        items = ca.pending_approvals([self.agent()], self.now, qoder_home=self.home)
        self.assertEqual(1, len(items))
        self.assertEqual("call_01", items[0]["call_id"])
        self.assertEqual(int((self.now - asked.timestamp()) // 1), items[0]["age_seconds"])
        self.assertEqual("Bash", items[0]["tool"])
        self.assertIn("echo hi", items[0]["command_preview"])
        self.assertEqual("worker-1", items[0]["agent"])
        self.assertEqual("wX:p1", items[0]["pane"])

    def test_ask_and_settle_is_not_pending(self):
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        settled = asked + timedelta(seconds=120)
        write_file(self.run_log,
                   ask_line(asked.isoformat()) + "\n" + settle_line(settled.isoformat()) + "\n")
        self.assertEqual([], ca.pending_approvals(
            [self.agent()], self.now, qoder_home=self.home))

    def test_dialog_line_alone_does_not_close_ask(self):
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        dialog = (f"{asked.isoformat()} INFO  debug.message [permission-ask] dialog "
                  "tool=Bash id=call_01 outcome=proceed_once payloadKeys=[]")
        write_file(self.run_log, ask_line(asked.isoformat()) + "\n" + dialog + "\n")
        self.assertEqual(1, len(ca.pending_approvals(
            [self.agent()], self.now, qoder_home=self.home)))

    def test_accident_fragment_hit_with_expected_call_id_and_age(self):
        write_file(self.run_log, ACCIDENT_ASK + "\n")
        asked = datetime.fromisoformat("2026-11-12T22:05:51.279+08:00")
        now = asked.timestamp() + 34540.1
        items = ca.pending_approvals([self.agent()], now, qoder_home=self.home)
        self.assertEqual(1, len(items))
        self.assertEqual(ACCIDENT_CALL, items[0]["call_id"])
        self.assertEqual(34540, items[0]["age_seconds"])
        self.assertIn("203 command substitutions", items[0]["command_preview"])

    def test_accident_ask_plus_settle_is_closed(self):
        write_file(self.run_log, ACCIDENT_ASK + "\n" + ACCIDENT_SETTLE + "\n")
        self.assertEqual([], ca.pending_approvals(
            [self.agent()], self.now, qoder_home=self.home))

    def test_missing_session_and_pane_fail_soft(self):
        logs: list[str] = []
        items = ca.pending_approvals(
            [self.agent(session="", pane="")], self.now,
            qoder_home=self.home, log=logs.append)
        self.assertEqual([], items)
        self.assertTrue(any("无可用 run 日志" in line for line in logs))

    def test_unreadable_log_fail_soft(self):
        entries, reason = ca.read_tail_approvals(self.base, self.now)
        self.assertEqual([], entries)
        self.assertTrue(reason)
        logs: list[str] = []
        with mock.patch.object(ca, "agent_run_log", return_value=self.base):
            self.assertEqual([], ca.pending_approvals(
                [self.agent()], self.now, qoder_home=self.home, log=logs.append))
        self.assertTrue(any("不可读" in line for line in logs))

    def test_garbage_log_has_no_pending(self):
        write_file(self.run_log, "noise\n[permission-ask] something else\nbroken\n")
        self.assertEqual([], ca.pending_approvals(
            [self.agent()], self.now, qoder_home=self.home))

    def test_preview_truncates_and_redacts(self):
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        long_message = "Allow Bash token=abcdef123456 " + "x" * 300
        write_file(self.run_log, ask_line(asked.isoformat(), message=long_message) + "\n")
        item = ca.pending_approvals([self.agent()], self.now, qoder_home=self.home)[0]
        preview = item["command_preview"]
        self.assertLessEqual(len(preview), ca.PREVIEW_LIMIT)
        self.assertNotIn("abcdef123456", preview)
        self.assertIn("token=<redacted>", preview)

    def test_read_tail_only_ignores_ask_outside_tail(self):
        """性能边界：只解析日志尾部，头部超窗 ask 不可见；尾部 ask 可见。"""
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        head_ask = ask_line(asked.isoformat(), call_id="call_head")
        filler = "".join(f"noise {index:06d} " + "x" * 1000 + "\n"
                         for index in range(1100))
        write_file(self.run_log, head_ask + "\n" + filler)
        self.assertGreater(self.run_log.stat().st_size, ca.DEFAULT_TAIL_BYTES)

        entries, reason = ca.read_tail_approvals(self.run_log, self.now)
        self.assertEqual("", reason)
        self.assertEqual([], entries)  # 头部 ask 超出默认 tail 窗口，不被读取

        tail_ask = ask_line(asked.isoformat(), call_id="call_tail")
        with self.run_log.open("a", encoding="utf-8") as handle:
            handle.write(tail_ask + "\n")
        entries, reason = ca.read_tail_approvals(self.run_log, self.now)
        self.assertEqual("", reason)
        self.assertEqual(1, len(entries))
        self.assertEqual("call_tail", entries[0]["call_id"])

    def test_pending_approvals_20_agents_bounded(self):
        """冒烟级性能护栏：20 个 agent 单轮检测只读各自 tail，且有宽松耗时上限。

        这不是精确 benchmark（CI 负载会抖动），只拦「尾读失效、退化成全日志
        扫描」这类量级问题，因此上限取 5 秒而非毫秒级。
        """
        asked = datetime(2026, 11, 13, 6, 30, 0, tzinfo=timezone.utc)
        closed_at = asked + timedelta(minutes=5)
        agents: list[dict] = []
        for index in range(20):
            session = f"{SESSION}-{index:02d}"
            run_id = f"2026-11-12T21-48-20-426+08-00-perf{index:02d}"
            write_file(self.home / "logs" / "sessions" / SLUG / session
                       / "segments" / f"{run_id}.jsonl", "{}\n")
            call_id = f"call_{index:02d}"
            lines = ask_line(asked.isoformat(), call_id=call_id) + "\n"
            if index % 2 == 1:  # 10 个未闭合；另 10 个 ask+settle 已闭合
                lines += settle_line(closed_at.isoformat(), call_id=call_id) + "\n"
            write_file(self.home / "logs" / "runs" / run_id / "qodercli.log", lines)
            agents.append(self.agent(name=f"worker-{index:02d}",
                                     pane=f"wX:p{index:02d}", session=session))

        tail_sizes: list[int] = []
        real_read = ca.read_tail_approvals

        def spy_read(path, now, tail_bytes=ca.DEFAULT_TAIL_BYTES):
            tail_sizes.append(int(tail_bytes))
            return real_read(path, now, tail_bytes=tail_bytes)

        started = time.monotonic()
        with mock.patch.object(ca, "read_tail_approvals", side_effect=spy_read):
            items = ca.pending_approvals(agents, self.now, qoder_home=self.home)
        elapsed = time.monotonic() - started

        self.assertEqual(10, len(items))
        self.assertEqual({f"call_{index:02d}" for index in range(0, 20, 2)},
                         {item["call_id"] for item in items})
        self.assertEqual(len(agents), len(tail_sizes))
        self.assertTrue(all(size <= ca.DEFAULT_TAIL_BYTES for size in tail_sizes))
        self.assertLess(elapsed, 5.0)


class GuardHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.crew = self.base / "crew"
        self.root.mkdir(parents=True)
        self.now = [1_700_000_000.0]
        self.agents: list[dict] = []
        self.prompt_calls: list[tuple[str, str]] = []
        self.notify_calls: list[tuple[str, str]] = []
        self.status_calls: list[str] = []
        self.logs: list[str] = []
        self.panes: list[dict] = []
        self.prompt_ok = True
        self.approvals: list[dict] = []
        self.approval_fn = None

    @staticmethod
    def agent_entry(agent: dict) -> dict:
        entry = dict(agent)
        entry.setdefault("pane_id", f"pane-{entry.get('name', 'agent')}")
        return entry

    def pane_entries(self) -> list[dict]:
        if self.panes:
            return self.panes
        return [{"pane_id": self.agent_entry(agent)["pane_id"],
                 "focused": bool(agent.get("focused"))}
                for agent in self.agents]

    def fake_stuck(self, agents: list[dict], now: float) -> list[dict]:
        names = {str(agent.get("name") or "") for agent in agents}
        if self.approval_fn is not None:
            return [item for item in self.approval_fn(agents, now)
                    if str(item.get("agent") or "") in names]
        return [dict(item) for item in self.approvals
                if str(item.get("agent") or "") in names]

    def write_fixture(self, pending: bool = False) -> None:
        write_file(self.root / ".planning" / "flow-a" / "task_plan.md",
                   STREAM_PLAN if pending else EMPTY_STREAM)
        write_file(self.root / ".planning" / "portfolio" / "task_plan.md",
                   EMPTY_PORTFOLIO)

    def write_guard_config(self, **guard) -> None:
        write_file(self.root / ".planning" / "orchestration.json",
                   json.dumps({"guard": guard}, ensure_ascii=False))

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
                self.status_calls.append(flag) or (0, "看板新鲜")
            ),
            run_rotate=lambda root: (0, ""),
            run_reaper=lambda root, crew: (0, "ok"),
            pane_read=lambda pane, lines: "",
            dispatch=lambda root, args: (0, "", ""),
            log_fn=self.logs.append,
            stuck_detect=self.fake_stuck,
        )
        kwargs.update(overrides)
        return cg.Guard(**kwargs)

    def run_main(self, argv: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cg.main(argv)
        return code, out.getvalue(), err.getvalue()


class StuckGuardTest(GuardHarness):
    def test_stuck_over_threshold_wakes_with_age_and_call_id(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.approvals = [approval_entry(age_seconds=16 * 60)]
        guard = self.make_guard()

        result = guard.run_once()

        self.assertTrue(result["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertEqual("helm-1", self.prompt_calls[0][0])
        message = self.prompt_calls[0][1]
        self.assertIn("等待审批", message)
        self.assertIn("worker-1", message)
        self.assertIn("16 分钟", message)
        self.assertIn("call_01", message)
        self.assertIn("不会向等待审批的 pane 注入", message)
        self.assertEqual(1, len(result["stuck"]))
        self.assertEqual(16, result["stuck"][0]["ageMinutes"])
        self.assertEqual("call_01", result["stuck"][0]["callId"])

    def test_stuck_under_threshold_does_not_wake(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.approvals = [approval_entry(age_seconds=14 * 60)]
        guard = self.make_guard()

        result = guard.run_once()

        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertEqual(1, len(result["stuck"]))
        self.assertEqual(14, result["stuck"][0]["ageMinutes"])

    def test_first_stuck_wake_respects_global_cooldown(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.approvals = [approval_entry(age_seconds=16 * 60)]
        guard = self.make_guard()
        guard.last_wake_at = self.now[0] - 60

        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("冷却中" in line for line in self.logs))

        self.now[0] += 600
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

    def test_repeat_wake_bypasses_global_cooldown(self):
        self.write_fixture()
        self.write_guard_config(wakeCooldownMinutes=60, approvalRepeatMinutes=5,
                                approvalGraceMinutes=60, approvalMaxAgeMinutes=240)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        asked = self.now[0] - 16 * 60
        self.approval_fn = lambda agents, now: [
            approval_entry(age_seconds=int(now - asked))]
        guard = self.make_guard()

        self.assertTrue(guard.run_once()["woke"])
        self.now[0] += 6 * 60  # 全局冷却 60 分钟未过，但 repeat 5 分钟已过
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

    def test_repeat_grace_and_max_ladder(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        asked = self.now[0] - 16 * 60
        self.approval_fn = lambda agents, now: [
            approval_entry(age_seconds=int(now - asked))]
        guard = self.make_guard()

        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        # repeat 默认 30 分钟：+20 分钟只累计年龄，不重复叫醒
        self.now[0] += 20 * 60
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))

        # 距上次叫醒 ≥30 分钟：重复叫醒
        self.now[0] += 11 * 60
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(2, len(self.prompt_calls))

        # ≥grace（默认 60 分钟）：叫醒消息带 A/B/C 建议
        self.now[0] += 31 * 60
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(3, len(self.prompt_calls))
        message = self.prompt_calls[-1][1]
        self.assertIn("A 人工批准", message)
        self.assertIn("B 关闭重派", message)
        self.assertIn("C 继续等待", message)

    def test_over_max_age_stops_repeat_and_logs(self):
        self.write_fixture()
        self.write_guard_config(approvalWaitMinutes=15, approvalRepeatMinutes=5,
                                approvalGraceMinutes=60, approvalMaxAgeMinutes=30)
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        asked = self.now[0] - 16 * 60
        self.approval_fn = lambda agents, now: [
            approval_entry(age_seconds=int(now - asked))]
        guard = self.make_guard()

        self.assertTrue(guard.run_once()["woke"])
        self.now[0] += 16 * 60  # age 32 分钟，超过 max=30
        self.assertFalse(guard.run_once()["woke"])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertTrue(any("超过上限" in line for line in self.logs))

    def test_ask_disappears_clears_age_tracking(self):
        self.write_fixture()
        self.agents = [{"name": "worker-1", "agent_status": "working"}]
        self.approvals = [approval_entry(age_seconds=16 * 60)]
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])
        self.assertEqual(1, len(guard.approval_last_wake))

        self.approvals = []
        await_result = guard.run_once()
        self.assertEqual([], await_result["stuck"])
        self.assertEqual({}, guard.approval_last_wake)

    def test_helm_own_pending_ask_skips_injection(self):
        self.write_fixture(pending=True)
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "focused": False},
            {"name": "worker-1", "agent_status": "idle"},
        ]
        self.approvals = [approval_entry(agent="helm-1", pane="pane-helm-1",
                                         call_id="call_helm", age_seconds=60)]
        guard = self.make_guard()

        result = guard.run_once()

        self.assertFalse(result["woke"])  # 首轮 G2 防抖
        self.assertEqual([], self.prompt_calls)
        guard.run_once()
        self.assertEqual([], self.prompt_calls)
        self.assertTrue((self.crew / "guard.wake").is_file())
        self.assertEqual(1, len(self.notify_calls))
        self.assertTrue(any("helm 自身" in line for line in self.logs))

    def test_stuck_worker_pane_is_never_prompted(self):
        self.write_fixture()
        self.agents = [
            {"name": "helm-1", "agent_status": "idle", "focused": False},
            {"name": "worker-1", "agent_status": "working", "pane_id": "wX:pWORK"},
        ]
        self.approvals = [approval_entry(pane="wX:pWORK", age_seconds=16 * 60)]
        guard = self.make_guard()

        self.assertTrue(guard.run_once()["woke"])

        targets = [name for name, _ in self.prompt_calls]
        self.assertEqual(["helm-1"], targets)
        self.assertNotIn("worker-1", targets)
        self.assertNotIn("wX:pWORK", targets)

    def test_config_defaults_and_clamps(self):
        defaults = cg.load_config(self.root)["guard"]
        self.assertEqual(15, defaults["approvalWaitMinutes"])
        self.assertEqual(30, defaults["approvalRepeatMinutes"])
        self.assertEqual(60, defaults["approvalGraceMinutes"])
        self.assertEqual(240, defaults["approvalMaxAgeMinutes"])
        self.assertEqual(10, defaults["wakeCooldownMinutes"])
        self.assertEqual(15, defaults["pendingHeartbeatMinutes"])

        cases = (
            ({"approvalWaitMinutes": 0}, "approvalWaitMinutes", 1),
            ({"approvalWaitMinutes": 999}, "approvalWaitMinutes", 240),
            ({"approvalRepeatMinutes": "x"}, "approvalRepeatMinutes", 30),
            ({"approvalGraceMinutes": True}, "approvalGraceMinutes", 60),
            ({"approvalMaxAgeMinutes": 0}, "approvalMaxAgeMinutes", 1),
            ({"approvalMaxAgeMinutes": 9999}, "approvalMaxAgeMinutes", 1440),
            ({"wakeCooldownMinutes": 0}, "wakeCooldownMinutes", 1),
            ({"wakeCooldownMinutes": 999}, "wakeCooldownMinutes", 240),
            ({"pendingHeartbeatMinutes": 0}, "pendingHeartbeatMinutes", 1),
            ({"pendingHeartbeatMinutes": 999}, "pendingHeartbeatMinutes", 240),
        )
        for guard, key, expected in cases:
            with self.subTest(guard=guard):
                self.write_guard_config(**guard)
                self.assertEqual(expected, cg.load_config(self.root)["guard"][key])

    def test_once_json_includes_stuck(self):
        self.write_fixture()
        payload = {"pending": 0, "workers": [], "claudeRuns": [], "woke": False,
                   "handoff": {}, "rework": [],
                   "stuck": [{"agent": "worker-1", "callId": "call_01",
                              "ageMinutes": 16}]}
        with mock.patch.object(cg.Guard, "run_once", return_value=payload):
            code, out, err = self.run_main(
                ["--once", "--json", "--root", str(self.root),
                 "--crew-dir", str(self.crew), "--helm", "helm-1"])
        self.assertEqual(0, code, err)
        self.assertEqual("call_01", json.loads(out)["stuck"][0]["callId"])


class InjectionFallbackTest(GuardHarness):
    def test_crew_msg_pending_approval_skips_herdr_prompt(self):
        receipt = cm.build_receipt(
            from_="builder-1", runtime="qoder", task="patch-2221/BC",
            status="done", summary="完成", receipt_id="r-2221",
            ts="2026-11-13T00:00:00Z")
        prompt_calls: list[list[str]] = []

        def fake_run(argv, **kwargs):
            if argv[:3] == ["herdr", "agent", "list"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"result": {"agents": [
                    {"name": "helm-qoder", "pane_id": "wX:p1",
                     "agent_session": {"value": SESSION}}]}}), stderr="")
            if argv[:3] == ["herdr", "pane", "list"]:
                return mock.Mock(returncode=0, stdout=json.dumps({"result": {"panes": [
                    {"pane_id": "wX:p1", "focused": False}]}}), stderr="")
            prompt_calls.append(list(argv))
            return mock.Mock(returncode=0, stdout="", stderr="")

        with mock.patch.object(cm.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(cm, "notify", return_value=True), \
                mock.patch.object(cm, "pending_approvals",
                                  return_value=[approval_entry(agent="helm-qoder")]):
            result = cm.deliver_qoder(
                receipt, to="helm-qoder", via="auto", crew_dir=self.crew)

        self.assertEqual("inbox_fallback", result["delivery"])
        self.assertTrue(result["ok"])
        self.assertEqual([], prompt_calls)
        self.assertIn("未闭合审批", result["note"])

    def test_dispatch_prompt_pending_approval_refuses_injection(self):
        root = self.base / "dispatch-proj"
        root.mkdir()
        subprocess.run(["git", "init", "-q", str(root)], check=True,
                       capture_output=True)
        registry = dispatch.registry_path(root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "worker-1": {"role": "builder", "runtime": "qoder"}}), encoding="utf-8")
        entry = {"name": "worker-1", "pane_id": "wX:p1",
                 "agent_session": {"value": SESSION}, "agent_status": "working"}
        calls: list[list[str]] = []

        def fake_run_herdr(argv):
            calls.append(list(argv))
            return ""

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch, "pane_focus_state",
                               return_value=(False, "", True)), \
                mock.patch.object(dispatch, "herdr_agents", return_value=[entry]), \
                mock.patch.object(dispatch, "pending_approvals",
                                  return_value=[approval_entry()]), \
                mock.patch.object(dispatch, "herdr_notify", return_value=True), \
                mock.patch.object(dispatch, "run_herdr",
                                  side_effect=lambda argv: (
                                      "ctx 100k/1M" if argv[:3] == ["herdr", "pane", "read"]
                                      else fake_run_herdr(argv))), \
                redirect_stdout(out), redirect_stderr(err):
            code = dispatch.main(["--root", str(root), "prompt",
                                  "--agent", "worker-1", "--message", "hi"])

        self.assertEqual(2, code)
        self.assertEqual([], calls)
        log_path = dispatch.crew_dir(root) / "agent-log.jsonl"
        rows = [json.loads(line) for line in
                log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual("inbox_fallback", rows[-1]["delivery"])
        self.assertEqual("refused", rows[-1]["result"])
        self.assertIn("未闭合审批", err.getvalue())


class ManifestRegistrationTest(unittest.TestCase):
    def test_script_manifest_contains_both_new_scripts(self):
        self.assertIn("crew-write.py", init_mod.SCRIPT_MANIFEST)
        self.assertIn("crew_approvals.py", init_mod.SCRIPT_MANIFEST)

    def test_init_installs_crew_approvals_and_manifest_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "proj"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True)
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                rc = init_mod.run(["--root", str(root), "--yes"])
            self.assertEqual(0, rc, out.getvalue() + err.getvalue())
            self.assertTrue((root / "scripts" / "crew_approvals.py").is_file())
            manifest = json.loads(
                (root / "docs/crew/manifest.json").read_text(encoding="utf-8"))
            script_paths = {entry.get("path") for entry in manifest.get("entries", [])
                            if isinstance(entry, dict) and entry.get("kind") == "file"}
            self.assertIn("scripts/crew_approvals.py", script_paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
