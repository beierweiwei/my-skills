#!/usr/bin/env python3
"""2.2.20 工单 C / FB-045：交接残留根因回归。

覆盖：
① guard `_continue_handoff` 以 oldPane 反查 oldTab：写错/缺失纠正并告警，反查不到
   （pane/agent 消失）不按未验证 oldTab 操作；
② 手工 handoff 字段校验函数（`validate_handoff_fields`）可独立测试；
③ `phase=done` 后残留旧 helm 一次有界回收（idle/done 且非现役、非聚焦、非白名单）；
   working/blocked/无法判定只记日志；幂等（reclaim 标记）；
④ pane-audit 新增「多个总舵 tab / 旧 helm 残留」finding：dry-run 只报告、--apply
   按安全规则经 dispatch 回收；现役解析 handoff → registry → unknown；
⑤ guard 周期调用 crew-reaper（轮次间隔 + 失败不阻断，stub 验证）。
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

_GUARD_PATH = Path(__file__).resolve().parent.parent / "crew-guard.py"
_guard_spec = importlib.util.spec_from_file_location("crew_guard_2220", _GUARD_PATH)
cg = importlib.util.module_from_spec(_guard_spec)
sys.modules["crew_guard_2220"] = cg  # dataclass 注解解析需要模块已注册
_guard_spec.loader.exec_module(cg)

_PA_PATH = Path(__file__).resolve().parent.parent / "pane-audit.py"
_pa_spec = importlib.util.spec_from_file_location("pane_audit_2220", _PA_PATH)
pa = importlib.util.module_from_spec(_pa_spec)
sys.modules["pane_audit_2220"] = pa
_pa_spec.loader.exec_module(pa)

# S-2：跨实现一致性只读 import reaper（不改该文件）。
_REAPER_PATH = Path(__file__).resolve().parent.parent / "crew-reaper.py"
_rr_spec = importlib.util.spec_from_file_location("crew_reaper_2220", _REAPER_PATH)
rr = importlib.util.module_from_spec(_rr_spec)
sys.modules["crew_reaper_2220"] = rr
_rr_spec.loader.exec_module(rr)


EMPTY_STREAM = "# Task Plan: Empty\n\n## Phases\n"
EMPTY_PORTFOLIO = (
    "# Task Plan: Empty\n\n## Backlog\n\n"
    "| # | 目标 | P | 依赖 | 阻塞 |\n|---|---|---|---|---|\n"
)


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# guard 测试夹具
# ---------------------------------------------------------------------------

class GuardFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.crew = self.base / "crew"
        self.crew.mkdir(parents=True)
        self.now = [0.0]
        self.agents: list[dict] = []
        self.panes: list[dict] = []
        self.tabs: list[dict] = []
        self.logs: list[str] = []
        self.dispatch_calls: list[list[str]] = []
        self.dispatch_override = None
        self.pane_outputs: dict[str, str] = {}
        self.reaper_calls: list[tuple[Path, Path]] = []
        write_file(self.root / ".planning" / "flow-a" / "task_plan.md", EMPTY_STREAM)
        write_file(
            self.root / ".planning" / "portfolio" / "task_plan.md", EMPTY_PORTFOLIO)

    # -- fixtures ---------------------------------------------------------
    def write_handoff(self, **fields: str) -> None:
        order = ("phase", "old", "oldPane", "oldTab", "new", "newPane", "newTab",
                 "time", "reason", "result", "error", "successorWasWorking")
        lines = [f"{key}={fields[key]}" for key in order if key in fields]
        lines.extend(f"{key}={value}" for key, value in fields.items()
                     if key not in order)
        write_file(self.crew / "guard.handoff", "\n".join(lines) + "\n")

    def handoff_fields(self) -> dict[str, str]:
        text = (self.crew / "guard.handoff").read_text(encoding="utf-8")
        return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)

    def pane_of(self, pane_id: str) -> dict | None:
        return next(
            (pane for pane in self.panes if pane.get("pane_id") == pane_id), None)

    def fake_dispatch(self, root: Path, args: list[str]) -> tuple[int, str, str]:
        self.dispatch_calls.append(list(args))
        if self.dispatch_override is not None:
            result = self.dispatch_override(args)
            if result is not None:
                return result
        if args[:2] == ["pane", "close"]:
            pane_id = args[args.index("--pane") + 1]
            self.panes = [pane for pane in self.panes
                          if pane.get("pane_id") != pane_id]
        if args[:2] == ["tab", "create"]:
            return 0, json.dumps({"tab": "wX:tNEW", "pane": "wX:pNEW"}), ""
        if args[:1] == ["start"]:
            return 0, json.dumps(
                {"agent": args[args.index("--name") + 1]}), ""
        return 0, "", ""

    def make_guard(self, **overrides) -> "cg.Guard":
        kwargs = dict(
            root=self.root,
            crew_dir=self.crew,
            helm="helm-1",
            interval=120,
            clock=lambda: self.now[0],
            list_agents=lambda: [dict(agent) for agent in self.agents],
            list_panes=lambda: [dict(pane) for pane in self.panes],
            list_tabs=lambda: [dict(tab) for tab in self.tabs],
            prompt=lambda name, message: True,
            notify=lambda title, body: True,
            pane_read=lambda pane, lines: self.pane_outputs.get(pane, ""),
            dispatch=self.fake_dispatch,
            run_status=lambda root, flag: (0, "看板新鲜"),
            run_rotate=lambda root: (0, "无需轮转"),
            run_reaper=self.stub_reaper,
            log_fn=self.logs.append,
        )
        kwargs.update(overrides)
        return cg.Guard(**kwargs)

    def stub_reaper(self, root: Path, crew: Path) -> tuple[int, str]:
        self.reaper_calls.append((root, crew))
        return 0, '{"actions":[]}'

    def close_calls(self) -> list[list[str]]:
        return [call for call in self.dispatch_calls
                if call[:2] in (["pane", "close"], ["tab", "close"])]


STARTED_HANDOFF = dict(
    phase="started",
    old="helm-1",
    oldPane="wQ:pRQ",
    oldTab="wQ:tG7",
    new="helm-1-2",
    newPane="wQ:pNEW",
    newTab="wQ:tGY",
    result="ok",
    successorWasWorking="1",
)


class HandoffValidationTest(unittest.TestCase):
    """验收断言 3：校验函数可独立测试。"""

    def fields(self, old_tab: str = "wQ:tGS") -> dict[str, str]:
        return {
            "phase": "started", "old": "helm-1", "oldPane": "wQ:pRQ",
            "oldTab": old_tab, "new": "helm-1-2", "newPane": "wQ:pNEW",
            "newTab": "wQ:tGY",
        }

    def test_matching_tab_is_ok(self):
        agents = [{"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        panes = [{"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        check = cg.validate_handoff_fields(self.fields("wQ:tGS"), agents, panes)
        self.assertEqual("ok", check.status)
        self.assertEqual("wQ:tGS", check.old_tab)
        self.assertEqual("wQ:tGS", check.resolved_tab)

    def test_mismatch_prefers_reverse_lookup(self):
        agents = [{"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        panes = [{"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        check = cg.validate_handoff_fields(self.fields("wQ:tG7"), agents, panes)
        self.assertEqual("mismatch", check.status)
        self.assertEqual("wQ:tGS", check.old_tab)
        self.assertEqual("wQ:tGS", check.resolved_tab)
        self.assertIn("wQ:tG7", check.note)

    def test_missing_handoff_tab_is_corrected(self):
        agents = [{"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        panes = [{"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        check = cg.validate_handoff_fields(self.fields(""), agents, panes)
        self.assertEqual("missing_handoff_tab", check.status)
        self.assertEqual("wQ:tGS", check.old_tab)

    def test_pane_gone_is_fail_soft(self):
        check = cg.validate_handoff_fields(
            self.fields("wQ:tG7"), [], [{"pane_id": "wQ:pNEW", "tab_id": "wQ:tGY"}])
        self.assertEqual("pane_gone", check.status)
        self.assertFalse(check.pane_exists)

    def test_pane_present_but_tab_unknown_falls_back(self):
        # 旧 pane 仍在（agent list 有）但两路都没有 tab 信息：沿用 handoff oldTab
        agents = [{"name": "helm-1", "pane_id": "wQ:pRQ"}]
        panes = [{"pane_id": "wQ:pRQ", "focused": False}]
        check = cg.validate_handoff_fields(self.fields("wQ:tG7"), agents, panes)
        self.assertEqual("unresolved", check.status)
        self.assertTrue(check.pane_exists)
        self.assertEqual("wQ:tG7", check.old_tab)

    def test_pane_list_unavailable_is_unresolved_data(self):
        check = cg.validate_handoff_fields(self.fields("wQ:tG7"), [], None)
        self.assertEqual("unresolved_data", check.status)
        self.assertEqual("wQ:tG7", check.old_tab)

    def test_pane_list_authoritative_over_agent_list(self):
        agents = [{"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tOLDSTALE"}]
        panes = [{"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS"}]
        check = cg.validate_handoff_fields(self.fields("wQ:tG7"), agents, panes)
        self.assertEqual("mismatch", check.status)
        self.assertEqual("wQ:tGS", check.resolved_tab)
        self.assertEqual("panes", check.source)


class HandoffContinueReverseLookupTest(GuardFixture):
    """验收断言 1：交接操作前 oldPane 反查 oldTab，以反查为准。"""

    def prepare(self) -> None:
        self.write_handoff(**STARTED_HANDOFF)
        self.agents = [
            {"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tGS",
             "agent_status": "idle"},
            {"name": "helm-1-2", "pane_id": "wQ:pNEW", "tab_id": "wQ:tGY",
             "agent_status": "working"},
        ]
        self.panes = [
            {"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS", "focused": False,
             "agent_status": "idle"},
            {"pane_id": "wQ:pNEW", "tab_id": "wQ:tGY", "focused": False,
             "agent_status": "working"},
        ]
        self.tabs = [
            {"tab_id": "wQ:tGS", "label": "总舵", "focused": False},
            {"tab_id": "wQ:tGY", "label": "总舵", "focused": False},
        ]

    def test_wrong_old_tab_closes_reversed_tab_and_corrects_field(self):
        self.prepare()
        guard = self.make_guard()
        first = guard.run_once()
        self.assertEqual("wait", first["handoff"]["action"])

        self.agents[1]["agent_status"] = "idle"
        second = guard.run_once()
        self.assertEqual("done", second["handoff"]["action"])
        self.assertEqual(
            [["pane", "close", "--pane", "wQ:pRQ"],
             ["tab", "close", "--tab", "wQ:tGS"]],
            self.close_calls(),
        )
        fields = self.handoff_fields()
        self.assertEqual("wQ:tGS", fields["oldTab"])
        self.assertIn("mismatch", fields.get("tabCheck", ""))
        self.assertTrue(any("不一致" in line and "反查" in line for line in self.logs),
                        self.logs)

    def test_missing_old_tab_is_filled_from_reverse_lookup(self):
        self.prepare()
        self.write_handoff(**{**STARTED_HANDOFF, "oldTab": ""})
        guard = self.make_guard()
        guard.run_once()
        self.agents[1]["agent_status"] = "idle"
        guard.run_once()
        self.assertEqual(
            [["pane", "close", "--pane", "wQ:pRQ"],
             ["tab", "close", "--tab", "wQ:tGS"]],
            self.close_calls(),
        )
        fields = self.handoff_fields()
        self.assertEqual("wQ:tGS", fields["oldTab"])
        self.assertIn("missing_handoff_tab", fields.get("tabCheck", ""))

    def test_pane_gone_does_not_touch_unverified_tab(self):
        self.prepare()
        # 旧舵 agent/pane 都已消失；handoff oldTab 是过期值
        self.agents = [
            {"name": "helm-1-2", "pane_id": "wQ:pNEW", "tab_id": "wQ:tGY",
             "agent_status": "idle"},
        ]
        self.panes = [
            {"pane_id": "wQ:pNEW", "tab_id": "wQ:tGY", "focused": False,
             "agent_status": "idle"},
        ]
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual("done", result["handoff"]["action"])
        self.assertEqual([], self.close_calls())
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])
        self.assertIn("pane_gone", fields.get("tabCheck", ""))
        self.assertTrue(any("未" in line and "关闭" in line for line in self.logs),
                        self.logs)

    def test_pane_list_failure_keeps_legacy_pane_only_close(self):
        self.prepare()
        self.write_handoff(**STARTED_HANDOFF)

        def broken_panes() -> list[dict]:
            raise RuntimeError("pane list boom")

        guard = self.make_guard(list_panes=broken_panes)
        guard.run_once()
        self.agents[1]["agent_status"] = "idle"
        guard.run_once()
        self.assertEqual([["pane", "close", "--pane", "wQ:pRQ"]], self.close_calls())
        fields = self.handoff_fields()
        self.assertEqual("done", fields["phase"])
        self.assertEqual("ok", fields["result"])


class DoneReclaimTest(GuardFixture):
    """验收断言 2：phase=done 后残留旧 helm 一次有界回收。"""

    def prepare(self, old_status: str = "idle", focused: bool = False,
                label: str = "总舵") -> None:
        self.write_handoff(
            phase="done", result="ok", old="helm-1", oldPane="wQ:pRQ",
            oldTab="wQ:tGS", new="helm-1-2", newPane="wQ:pNEW",
            newTab="wQ:tGY", successorWasWorking="1",
        )
        self.agents = [
            {"name": "helm-1", "pane_id": "wQ:pRQ", "tab_id": "wQ:tGS",
             "agent_status": old_status},
            {"name": "helm-1-2", "pane_id": "wQ:pNEW", "tab_id": "wQ:tGY",
             "agent_status": "working"},
        ]
        self.panes = [
            {"pane_id": "wQ:pRQ", "tab_id": "wQ:tGS", "focused": focused,
             "agent_status": old_status},
            {"pane_id": "wQ:pNEW", "tab_id": "wQ:tGY", "focused": True,
             "agent_status": "working"},
        ]
        self.tabs = [
            {"tab_id": "wQ:tGS", "label": label, "focused": focused},
            {"tab_id": "wQ:tGY", "label": "总舵", "focused": True},
        ]

    def test_idle_old_helm_reclaimed_once(self):
        self.prepare()
        guard = self.make_guard()
        guard.run_once()
        self.assertEqual(
            [["pane", "close", "--pane", "wQ:pRQ"],
             ["tab", "close", "--tab", "wQ:tGS"]],
            self.close_calls(),
        )
        fields = self.handoff_fields()
        self.assertTrue(fields.get("reclaim", "").startswith("done"))
        self.assertTrue(any("残留" in line and "helm-1" in line for line in self.logs),
                        self.logs)

        before = len(self.dispatch_calls)
        guard.run_once()
        self.assertEqual(before, len(self.dispatch_calls))
        self.assertEqual("done", self.handoff_fields()["reclaim"].split(":")[0])

    def test_working_old_helm_not_closed_and_marked_once(self):
        self.prepare(old_status="working")
        guard = self.make_guard()
        guard.run_once()
        self.assertEqual([], self.close_calls())
        fields = self.handoff_fields()
        self.assertIn("working", fields.get("reclaim", ""))
        self.assertTrue(any("不关闭" in line for line in self.logs), self.logs)

        # 一次性标记：即使随后变 idle 也不在旧 handoff 上重复动作
        self.agents[0]["agent_status"] = "idle"
        self.panes[0]["agent_status"] = "idle"
        guard.run_once()
        self.assertEqual([], self.close_calls())

    def test_out_of_project_old_agent_not_closed(self):
        self.prepare()
        self.agents[0]["cwd"] = "/home/other/project"
        self.make_guard(project_roots={self.root}).run_once()
        self.assertEqual([], self.close_calls())
        self.assertTrue(any("不在本仓" in line for line in self.logs), self.logs)

    def test_focused_pane_not_closed(self):
        self.prepare(focused=True)
        self.make_guard().run_once()
        self.assertEqual([], self.close_calls())
        self.assertTrue(any("聚焦" in line for line in self.logs), self.logs)

    def test_whitelisted_tab_not_closed(self):
        self.prepare(label="Captain·总舵旧窗口")
        self.make_guard().run_once()
        self.assertEqual([], self.close_calls())
        self.assertTrue(any("白名单" in line for line in self.logs), self.logs)

    def test_unresolvable_tab_not_closed(self):
        self.prepare()
        for pane in self.panes:
            pane.pop("tab_id", None)
        for agent in self.agents:
            agent.pop("tab_id", None)
        self.make_guard().run_once()
        self.assertEqual([], self.close_calls())
        self.assertIn("unresolved", self.handoff_fields().get("reclaim", ""))

    def test_tab_list_failure_not_closed(self):
        self.prepare()

        def broken_tabs() -> list[dict]:
            raise RuntimeError("tab list boom")

        self.make_guard(list_tabs=broken_tabs).run_once()
        self.assertEqual([], self.close_calls())
        self.assertTrue(any("未关闭" in line for line in self.logs), self.logs)


class StaleMarkerTest(GuardFixture):
    """新一轮交接必须清掉上一轮 done handoff 的 tabCheck/reclaim 标记。"""

    def test_new_handoff_clears_previous_markers(self):
        self.write_handoff(
            phase="done", result="ok", old="helm-0", oldPane="wQ:pOLD0",
            oldTab="wQ:tOLD0", new="helm-1", newPane="wQ:pCUR",
            newTab="wQ:tCUR", successorWasWorking="1",
            tabCheck="mismatch:stale", reclaim="done",
        )
        self.agents = [
            {"name": "helm-1", "pane_id": "wQ:pCUR", "tab_id": "wQ:tCUR",
             "workspace_id": "wQ", "agent_status": "working"},
        ]
        self.panes = [
            {"pane_id": "wQ:pCUR", "tab_id": "wQ:tCUR", "focused": False,
             "agent_status": "working"},
        ]
        self.pane_outputs["wQ:pCUR"] = "status line ctx (650k/1M) tail"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual("started", result["handoff"]["action"])
        fields = self.handoff_fields()
        self.assertEqual("started", fields["phase"])
        self.assertEqual("", fields.get("tabCheck", ""))
        self.assertEqual("", fields.get("reclaim", ""))


class ReaperPeriodicTest(GuardFixture):
    """验收断言 5：guard 周期调用 crew-reaper（间隔 + 失败不阻断）。"""

    def test_reaper_called_on_interval(self):
        guard = self.make_guard()
        interval = cg.REAPER_EVERY_ROUNDS
        for _ in range(interval - 1):
            guard.run_once()
        self.assertEqual([], self.reaper_calls)
        guard.run_once()
        self.assertEqual([(self.root, self.crew)], self.reaper_calls)
        for _ in range(interval - 1):
            guard.run_once()
        self.assertEqual(1, len(self.reaper_calls))
        guard.run_once()
        self.assertEqual(2, len(self.reaper_calls))

    def test_reaper_failure_does_not_block_guard(self):
        calls = {"count": 0}

        def failing_reaper(root: Path, crew: Path) -> tuple[int, str]:
            calls["count"] += 1
            return 1, "boom"

        guard = self.make_guard(run_reaper=failing_reaper)
        for _ in range(cg.REAPER_EVERY_ROUNDS):
            result = guard.run_once()
            self.assertNotIn("error", result)
        self.assertEqual(1, calls["count"])
        self.assertTrue(any("周期回收失败" in line and "boom" in line
                            for line in self.logs), self.logs)

    def test_reaper_exception_does_not_block_guard(self):
        def broken_reaper(root: Path, crew: Path) -> tuple[int, str]:
            raise RuntimeError("reaper boom")

        guard = self.make_guard(run_reaper=broken_reaper)
        for _ in range(cg.REAPER_EVERY_ROUNDS):
            guard.run_once()
        self.assertTrue(any("周期回收调用异常" in line and "reaper boom" in line
                            for line in self.logs), self.logs)


# ---------------------------------------------------------------------------
# pane-audit：「多个总舵 tab / 旧 helm 残留」
# ---------------------------------------------------------------------------

def tab_item(tab_id: str, label: str, focused: bool = False,
             workspace: str = "wQ") -> dict:
    item = {"tab_id": tab_id, "label": label, "focused": focused}
    if workspace:
        item["workspace_id"] = workspace
    return item


def pane_item(pane_id: str, tab_id: str, status: str = "idle",
              focused: bool = False, workspace: str = "wQ") -> dict:
    item = {"pane_id": pane_id, "tab_id": tab_id,
            "agent_status": status, "focused": focused}
    if workspace:
        item["workspace_id"] = workspace
    return item


def agent_item(name: str, pane_id: str, tab_id: str, status: str = "idle",
               workspace: str = "wQ") -> dict:
    item = {"name": name, "pane_id": pane_id, "tab_id": tab_id,
            "agent_status": status}
    if workspace:
        item["workspace_id"] = workspace
    return item


def snapshot(tabs: list[dict], panes: list[dict], agents: list[dict]) -> dict:
    return {"tabs": tabs, "panes": panes, "agents": agents}


class PaneAuditHelmReclaimTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.project = self.tmp / "proj"
        self.crew = self.tmp / "crew"
        self.crew.mkdir(parents=True)

    def write_handoff(self, **fields: str) -> None:
        order = ("phase", "old", "oldPane", "oldTab", "new", "newPane", "newTab",
                 "time", "result")
        lines = [f"{key}={fields[key]}" for key in order if key in fields]
        lines.extend(f"{key}={value}" for key, value in fields.items()
                     if key not in order)
        write_file(self.crew / "guard.handoff", "\n".join(lines) + "\n")

    def write_registry(self, entries: dict) -> None:
        write_file(self.crew / "dispatch-agents.json",
                   json.dumps(entries, ensure_ascii=False))

    def two_helm_snapshot(self, old_status: str = "idle",
                          old_focused: bool = False,
                          old_label: str = "总舵") -> dict:
        return snapshot(
            [tab_item("wQ:tGS", old_label, focused=old_focused),
             tab_item("wQ:tGY", "总舵", focused=False)],
            [pane_item("wQ:pRQ", "wQ:tGS", old_status, focused=old_focused),
             pane_item("wQ:pNEW", "wQ:tGY", "working", focused=False)],
            [agent_item("helm-old", "wQ:pRQ", "wQ:tGS", old_status),
             agent_item("helm-new", "wQ:pNEW", "wQ:tGY", "working")],
        )

    def run_cli(self, data: dict, extra: list[str] | None = None):
        extra = list(extra or [])
        recorded = {"calls": []}

        def fake_dispatch_runner(root: Path):
            def runner(kind: str, target: str) -> bool:
                recorded["calls"].append((str(root), kind, target))
                return True
            return runner

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "make_dispatch_runner", fake_dispatch_runner), \
                mock.patch.object(pa, "probe_empty_pane", lambda pane_id: None):
            if "--apply" in extra:
                # S-1：--apply 与 --snapshot 互斥；用 live snapshot stub 注入同一夹具
                argv = ["--root", str(self.project),
                        "--crew-dir", str(self.crew)] + extra
                with mock.patch.object(pa, "load_live_snapshot", lambda: data):
                    with redirect_stdout(out), redirect_stderr(err):
                        rc = pa.main(argv)
            else:
                path = self.tmp / "snapshot.json"
                path.write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8")
                argv = ["--snapshot", str(path), "--root", str(self.project),
                        "--crew-dir", str(self.crew)] + extra
                with redirect_stdout(out), redirect_stderr(err):
                    rc = pa.main(argv)
        return rc, out.getvalue(), err.getvalue(), recorded["calls"]

    def test_help_documents_snapshot_exclusion_and_best_effort(self):
        # S-1 / S-7：help 写明互斥与回收 best-effort 边界
        out = io.StringIO()
        with redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                pa.main(["--help"])
        self.assertEqual(0, cm.exception.code)
        text = out.getvalue()
        for token in ("互斥", "--snapshot", "best-effort", "二次复检", "无法确认"):
            self.assertIn(token, text)

    def test_snapshot_and_apply_are_mutually_exclusive(self):
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(self.two_helm_snapshot()), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = pa.main(["--snapshot", str(path), "--apply",
                          "--root", str(self.project), "--crew-dir", str(self.crew)])
        self.assertNotEqual(0, rc)
        self.assertIn("互斥", err.getvalue())
        self.assertIn("--snapshot", err.getvalue())

    def done_handoff(self) -> None:
        self.write_handoff(
            phase="done", result="ok", old="helm-old", oldPane="wQ:pRQ",
            oldTab="wQ:tGS", new="helm-new", newPane="wQ:pNEW", newTab="wQ:tGY",
        )

    def test_dry_run_reports_both_tabs_without_closing(self):
        self.done_handoff()
        rc, out, err, calls = self.run_cli(self.two_helm_snapshot())
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("[告警] 多个总舵 tab", out)
        self.assertIn("wQ:tGS", out)
        self.assertIn("wQ:tGY", out)
        self.assertIn("helm-old", out)
        self.assertIn("helm-new", out)
        self.assertIn("dry-run", out)

    def test_apply_closes_only_old_helm_tab_via_dispatch(self):
        self.done_handoff()
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([("wQ:tGS")], [target for _root, _kind, target in calls])
        self.assertEqual(["tab"], [kind for _root, kind, _target in calls])
        self.assertNotIn("wQ:tGY", [target for _root, _kind, target in calls])
        self.assertIn("已关闭 tab wQ:tGS", out)

    def test_failed_handoff_reports_only_zero_actions(self):
        # B-1 / PoC 同数据：done+failed，失败继任 helm-new 已进 registry；
        # 真实现役是旧舵 helm-old，禁止把失败继任当现役后关旧舵 tab。
        self.write_handoff(
            phase="done", result="failed", old="helm-old", oldPane="wQ:pRQ",
            oldTab="wQ:tGS", new="helm-new", newPane="wQ:pNEW",
            newTab="wQ:tGY", error="successor no first report",
        )
        self.write_registry({
            "helm-old": {"role": "helm", "pane": "wQ:pRQ",
                         "startedAt": "2026-11-10T08:00:00Z"},
            "helm-new": {"role": "helm", "pane": "wQ:pNEW",
                         "startedAt": "2026-11-12T09:00:00Z"},
        })
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(old_status="idle"), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("无法确认现役", out)
        self.assertNotIn("判定：可回收", out)

    def test_working_old_helm_tab_not_closed(self):
        self.done_handoff()
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(old_status="working"), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("多个总舵 tab", out)

    def test_focused_old_helm_tab_not_closed(self):
        self.done_handoff()
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(old_focused=True), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)

    def test_whitelisted_old_helm_tab_not_closed(self):
        self.done_handoff()
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(old_label="Captain·旧总舵"), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)

    def test_unknown_current_helm_reports_only(self):
        # 无 handoff、无 registry：无法判定现役 → 只报告、零动作
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("多个总舵 tab", out)
        self.assertIn("无法确认现役", out)

    def test_registry_resolves_current_helm(self):
        self.write_registry({
            "helm-new": {"role": "helm", "agent": "helm-new", "pane": "wQ:pNEW",
                         "startedAt": "2026-11-12T10:00:00Z"},
        })
        rc, out, err, calls = self.run_cli(
            self.two_helm_snapshot(), ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([("wQ:tGS")], [target for _root, _kind, target in calls])
        self.assertIn("registry:helm-new", out)

    def test_cross_workspace_helm_tab_not_touched(self):
        # 其它项目的「总舵」tab 不归本仓巡检处置（herdr 列表全局）
        self.write_handoff(
            phase="done", result="ok", old="", oldPane="", oldTab="",
            new="helm-new", newPane="wA:pNEW", newTab="wA:tGY",
        )
        data = snapshot(
            [{"tab_id": "wA:tGY", "label": "总舵", "workspace_id": "wA",
              "focused": False},
             {"tab_id": "wB:tGS", "label": "总舵", "workspace_id": "wB",
              "focused": False}],
            [{"pane_id": "wA:pNEW", "tab_id": "wA:tGY", "workspace_id": "wA",
              "agent_status": "working", "focused": False},
             {"pane_id": "wB:pOLD", "tab_id": "wB:tGS", "workspace_id": "wB",
              "agent_status": "idle", "focused": False}],
            [{"name": "helm-new", "pane_id": "wA:pNEW", "tab_id": "wA:tGY",
              "workspace_id": "wA", "agent_status": "working"},
             {"name": "other-helm", "pane_id": "wB:pOLD", "tab_id": "wB:tGS",
              "workspace_id": "wB", "agent_status": "idle"}],
        )
        rc, out, err, calls = self.run_cli(data, ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertNotIn("多个总舵 tab", out)

    def test_missing_workspace_old_tab_only_reported(self):
        # S-3：能定位现役 workspace 时，缺 workspace_id 的旧 tab/pane/agent 不纳入
        # 可回收集合（只报告）。
        self.done_handoff()
        data = snapshot(
            [tab_item("wQ:tGS", "总舵", workspace=""),
             tab_item("wQ:tGY", "总舵", workspace="wQ")],
            [pane_item("wQ:pRQ", "wQ:tGS", "idle", workspace=""),
             pane_item("wQ:pNEW", "wQ:tGY", "working", workspace="wQ")],
            [agent_item("helm-old", "wQ:pRQ", "wQ:tGS", "idle", workspace=""),
             agent_item("helm-new", "wQ:pNEW", "wQ:tGY", "working",
                        workspace="wQ")],
        )
        rc, out, err, calls = self.run_cli(data, ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("缺 workspace_id", out)

    def test_unknown_workspace_zero_actions(self):
        # S-3：定位不到现役 workspace → 总舵/旧 helm 回收零动作、只报告
        self.done_handoff()
        data = snapshot(
            [tab_item("wQ:tGS", "总舵", workspace=""),
             tab_item("wQ:tGY", "总舵", workspace="")],
            [pane_item("wQ:pRQ", "wQ:tGS", "idle", workspace=""),
             pane_item("wQ:pNEW", "wQ:tGY", "working", workspace="")],
            [agent_item("helm-old", "wQ:pRQ", "wQ:tGS", "idle", workspace=""),
             agent_item("helm-new", "wQ:pNEW", "wQ:tGY", "working",
                        workspace="")],
        )
        rc, out, err, calls = self.run_cli(data, ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("无法确认现役总舵 tab", out)
        self.assertIn("无法定位现役 workspace", out)

    def test_residual_old_helm_in_normal_tab_closed_by_dispatch(self):
        self.write_handoff(
            phase="done", result="ok", old="helm-old", oldPane="wQ:pRQ",
            oldTab="wQ:tOLD", new="helm-new", newPane="wQ:pNEW", newTab="wQ:tGY",
        )
        data = snapshot(
            [tab_item("wQ:tGY", "总舵", focused=False),
             tab_item("wQ:tOLD", "杂活", focused=False)],
            [pane_item("wQ:pRQ", "wQ:tOLD", "idle", focused=False),
             pane_item("wQ:pNEW", "wQ:tGY", "working", focused=False)],
            [agent_item("helm-old", "wQ:pRQ", "wQ:tOLD", "idle"),
             agent_item("helm-new", "wQ:pNEW", "wQ:tGY", "working")],
        )
        rc, out, err, calls = self.run_cli(data, ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([("pane", "wQ:pRQ")], [
            (kind, target) for _root, kind, target in calls])
        self.assertIn("[告警] 旧 helm 残留", out)
        self.assertIn("helm-old", out)

    def test_residual_old_helm_working_not_closed(self):
        self.write_handoff(
            phase="done", result="ok", old="helm-old", oldPane="wQ:pRQ",
            oldTab="wQ:tOLD", new="helm-new", newPane="wQ:pNEW", newTab="wQ:tGY",
        )
        data = snapshot(
            [tab_item("wQ:tGY", "总舵", focused=False),
             tab_item("wQ:tOLD", "杂活", focused=False)],
            [pane_item("wQ:pRQ", "wQ:tOLD", "working", focused=False),
             pane_item("wQ:pNEW", "wQ:tGY", "working", focused=False)],
            [agent_item("helm-old", "wQ:pRQ", "wQ:tOLD", "working"),
             agent_item("helm-new", "wQ:pNEW", "wQ:tGY", "working")],
        )
        rc, out, err, calls = self.run_cli(data, ["--apply"])
        self.assertEqual(0, rc, err)
        self.assertEqual([], calls)
        self.assertIn("[告警] 旧 helm 残留", out)


class PaneAuditHelmContextTest(unittest.TestCase):
    """现役解析优先级：handoff:new → registry 最新 startedAt → unknown。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.crew = Path(self._tmp.name) / "crew"
        self.crew.mkdir(parents=True)

    def test_handoff_new_wins(self):
        write_file(
            self.crew / "guard.handoff",
            "phase=done\nold=helm-old\noldPane=p1\noldTab=t1\n"
            "new=helm-new\nnewPane=p2\nnewTab=t2\nresult=ok\n",
        )
        ctx = pa.resolve_helm_context(self.root, self.crew)
        self.assertEqual("helm-new", ctx.current)
        self.assertEqual("handoff:new", ctx.source)
        self.assertEqual("helm-old", ctx.old)

    def test_handoff_warn_uses_old_as_current(self):
        write_file(
            self.crew / "guard.handoff",
            "phase=warn\nold=helm-old\noldPane=p1\noldTab=t1\n"
            "new=\nnewPane=\nnewTab=\n",
        )
        ctx = pa.resolve_helm_context(self.root, self.crew)
        self.assertEqual("helm-old", ctx.current)
        self.assertEqual("handoff:old", ctx.source)

    def test_registry_latest_started_at(self):
        write_file(
            self.crew / "dispatch-agents.json",
            json.dumps({
                "helm-a": {"role": "helm", "pane": "pa",
                           "startedAt": "2026-11-10T00:00:00Z"},
                "helm-b": {"role": "helm", "pane": "pb",
                           "startedAt": "2026-11-12T00:00:00Z"},
            }),
        )
        ctx = pa.resolve_helm_context(self.root, self.crew)
        self.assertEqual("helm-b", ctx.current)
        self.assertEqual("registry:helm-b", ctx.source)

    def test_missing_inputs_are_unknown(self):
        ctx = pa.resolve_helm_context(self.root, self.crew)
        self.assertEqual("", ctx.current)
        self.assertEqual("unknown", ctx.source)

    def test_failed_handoff_is_unknown_without_registry_fallback(self):
        # B-1：done+failed（及 started+failed）不得回退 registry 把失败继任当现役。
        for phase in ("done", "started"):
            with self.subTest(phase=phase):
                write_file(
                    self.crew / "guard.handoff",
                    f"phase={phase}\nresult=failed\nold=helm-old\n"
                    "oldPane=p1\noldTab=t1\nnew=helm-new\nnewPane=p2\n"
                    "newTab=t2\nerror=successor no first report\n",
                )
                write_file(
                    self.crew / "dispatch-agents.json",
                    json.dumps({
                        "helm-old": {"role": "helm", "pane": "p1",
                                     "startedAt": "2026-11-10T08:00:00Z"},
                        "helm-new": {"role": "helm", "pane": "p2",
                                     "startedAt": "2026-11-12T09:00:00Z"},
                    }),
                )
                ctx = pa.resolve_helm_context(self.root, self.crew)
                self.assertEqual("", ctx.current)
                self.assertEqual("unknown", ctx.source)
                self.assertEqual("helm-old", ctx.old)


def s2_snapshot(specs: list[tuple[str, str, str, str]]) -> dict:
    """(name, pane, tab, status) → 最小且一致的 herdr 快照（同一 helm 组）。"""
    tabs, panes, agents = [], [], []
    for name, pane_id, tab_id, status in specs:
        tabs.append({"tab_id": tab_id, "label": "总舵", "focused": False})
        panes.append({"pane_id": pane_id, "tab_id": tab_id,
                      "agent_status": status, "focused": False})
        agents.append({"name": name, "pane_id": pane_id, "tab_id": tab_id,
                       "agent_status": status})
    return {"tabs": tabs, "panes": panes, "agents": agents}


class HelmResolutionParityTest(unittest.TestCase):
    """S-2：pane-audit 与 crew-reaper 的现役判定口径一致性（同一组夹具）。

    约定口径：failed→unknown（不回退 registry）、warn→old、started/done(ok)→new、
    多候选并列→unknown、缺记录→unknown。只读调用 reaper，不改其文件。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.crew = Path(self._tmp.name) / "crew"
        self.crew.mkdir(parents=True)

    def _write_inputs(self, handoff_text: str | None, registry: dict):
        handoff_path = self.crew / "guard.handoff"
        if handoff_text is None:
            if handoff_path.exists():
                handoff_path.unlink()
        else:
            handoff_path.write_text(handoff_text, encoding="utf-8")
        (self.crew / "dispatch-agents.json").write_text(
            json.dumps(registry), encoding="utf-8")
        return rr.read_handoff(self.crew), rr.load_registry(self.crew)

    def _assert_parity(self, handoff_text, registry, specs,
                       expected_current: str, expected_source: str) -> None:
        handoff, reg = self._write_inputs(handoff_text, registry)
        reaper_ctx = rr.resolve_current_helm(handoff, reg, s2_snapshot(specs))
        audit_ctx = pa.resolve_helm_context(self.root, self.crew)
        self.assertEqual(expected_current, reaper_ctx.agent, "reaper current")
        self.assertEqual(expected_source, reaper_ctx.source, "reaper source")
        self.assertEqual(expected_current, audit_ctx.current, "pane-audit current")
        self.assertEqual(expected_source, audit_ctx.source, "pane-audit source")
        self.assertEqual(reaper_ctx.agent, audit_ctx.current)
        self.assertEqual(reaper_ctx.source, audit_ctx.source)

    @staticmethod
    def _handoff(phase: str, result: str = "ok") -> str:
        return (
            f"phase={phase}\nresult={result}\nold=helm-old\noldPane=p1\n"
            "oldTab=t1\nnew=helm-new\nnewPane=p2\nnewTab=t2\n"
        )

    def test_failed_is_unknown_without_registry_fallback(self):
        self._assert_parity(
            self._handoff("done", "failed"),
            {
                "helm-old": {"role": "helm", "pane": "p1",
                             "startedAt": "2026-11-10T08:00:00Z"},
                "helm-new": {"role": "helm", "pane": "p2",
                             "startedAt": "2026-11-12T09:00:00Z"},
            },
            [("helm-old", "p1", "t1", "idle"),
             ("helm-new", "p2", "t2", "idle")],
            "", "unknown",
        )

    def test_warn_uses_old(self):
        self._assert_parity(
            "phase=warn\nold=helm-old\noldPane=p1\noldTab=t1\n"
            "new=\nnewPane=\nnewTab=\n",
            {},
            [("helm-old", "p1", "t1", "idle")],
            "helm-old", "handoff:old",
        )

    def test_started_uses_new(self):
        self._assert_parity(
            self._handoff("started"),
            {"helm-new": {"role": "helm", "pane": "p2",
                          "startedAt": "2026-11-12T09:00:00Z"}},
            [("helm-old", "p1", "t1", "idle"),
             ("helm-new", "p2", "t2", "working")],
            "helm-new", "handoff:new",
        )

    def test_done_ok_uses_new(self):
        self._assert_parity(
            self._handoff("done", "ok"),
            {"helm-new": {"role": "helm", "pane": "p2",
                          "startedAt": "2026-11-12T09:00:00Z"}},
            [("helm-old", "p1", "t1", "idle"),
             ("helm-new", "p2", "t2", "idle")],
            "helm-new", "handoff:new",
        )

    def test_registry_tie_is_unknown(self):
        self._assert_parity(
            None,
            {
                "helm-a": {"role": "helm", "pane": "p1",
                           "startedAt": "2026-11-12T09:00:00Z"},
                "helm-b": {"role": "helm", "pane": "p2",
                           "startedAt": "2026-11-12T09:00:00Z"},
            },
            [("helm-a", "p1", "t1", "idle"),
             ("helm-b", "p2", "t2", "idle")],
            "", "unknown",
        )

    def test_missing_records_is_unknown(self):
        self._assert_parity(None, {}, [], "", "unknown")


if __name__ == "__main__":
    unittest.main()
