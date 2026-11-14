#!/usr/bin/env python3
"""crew-reaper.py 2.2.20 用例：窗口对账回收（总舵 tab / 旧 helm）。

覆盖：
① CASE 残留夹具（两总舵 tab：旧 idle / 现役 working）→ dry-run 报告、apply 关旧保现役；
② 负向：working/blocked/聚焦/白名单/空 pane 无法确认 → 不关；
③ 无法判定现役（无 handoff + registry 无 helm / registry 并列 / 两源矛盾）→ 零动作；
④ 幂等：--apply 第二遍无动作；
⑤ 旧 helm 残留回收（idle/done）与 working 不回收；
⑥ close 只经 dispatch stub（pane close / tab close），无裸 herdr 生命周期。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "crew-reaper.py"
_spec = importlib.util.spec_from_file_location("crew_reaper", _SPEC_PATH)
r = importlib.util.module_from_spec(_spec)
sys.modules["crew_reaper"] = r
_spec.loader.exec_module(r)

_INIT_PATH = _SPEC_PATH.with_name("init.py")
_init_spec = importlib.util.spec_from_file_location("init_mod_reaper", _INIT_PATH)
init_mod = importlib.util.module_from_spec(_init_spec)
sys.modules["init_mod_reaper"] = init_mod
_init_spec.loader.exec_module(init_mod)


TEST_WORKSPACE = "wTest"


def tab(tab_id: str, label: str, focused: bool = False,
        workspace: str = TEST_WORKSPACE) -> dict:
    return {"tab_id": tab_id, "label": label, "focused": focused,
            "workspace_id": workspace}


def pane(pane_id: str, tab_id: str, status: str | None = None,
         agent_name: str | None = None,
         workspace: str = TEST_WORKSPACE) -> dict:
    item = {"pane_id": pane_id, "tab_id": tab_id, "workspace_id": workspace}
    if status is not None:
        item["agent_status"] = status
    if agent_name:
        item["agent"] = agent_name
    return item


def agent(name: str, pane_id: str, tab_id: str, status: str,
          workspace: str = TEST_WORKSPACE) -> dict:
    return {
        "name": name,
        "pane_id": pane_id,
        "tab_id": tab_id,
        "agent_status": status,
        "workspace_id": workspace,
    }


def snapshot(tabs: list[dict], panes: list[dict], agents: list[dict]) -> dict:
    return {"tabs": tabs, "panes": panes, "agents": agents}


def registry_entry(pane_id: str, started_at: str, role: str = "helm") -> dict:
    return {
        "role": role,
        "runtime": "qoder",
        "pane": pane_id,
        "startedAt": started_at,
    }


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def handoff_text(fields: dict) -> str:
    return "\n".join(f"{key}={value}" for key, value in fields.items()) + "\n"


CASE_TABS = [tab("tGS", "总舵"), tab("tGY", "总舵")]
CASE_PANES = [pane("w12:p1", "tGS"), pane("w12:p3", "tGY")]
CASE_AGENTS = [
    agent("helm-old", "w12:p1", "tGS", "idle"),
    agent("helm-new", "w12:p3", "tGY", "working"),
]
CASE_REGISTRY = {
    "helm-old": registry_entry("w12:p1", "2026-11-10T08:00:00Z"),
    "helm-new": registry_entry("w12:p3", "2026-11-12T09:00:00Z"),
}
CASE_HANDOFF = {
    "phase": "done",
    "result": "ok",
    "old": "helm-old",
    "oldPane": "w12:p1",
    "oldTab": "tGS",
    "new": "helm-new",
    "newPane": "w12:p3",
    "newTab": "tGY",
    "time": "2026-11-12T09:00:00Z",
    "reason": "ctx 交接阈值",
}


class ReaperCliBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def run_cli(
        self,
        data: dict,
        *,
        handoff: dict | None = None,
        registry: dict | None = None,
        extra: list[str] | None = None,
        json_output: bool = True,
        dispatch_fail: object = None,
        helm_label: str = "总舵",
    ) -> tuple[int, str, str, list[list[str]], dict | None]:
        project = self.tmp / "project"
        crew = self.tmp / "crew"
        project.mkdir(parents=True, exist_ok=True)
        crew.mkdir(parents=True, exist_ok=True)
        if helm_label != "总舵":
            write_json(project / ".planning" / "orchestration.json",
                       {"helm": {"tabLabel": helm_label}})
        snap = self.tmp / "snapshot.json"
        write_json(snap, data)
        if registry is not None:
            write_json(crew / "dispatch-agents.json", registry)
        if handoff is not None:
            (crew / "guard.handoff").write_text(handoff_text(handoff), encoding="utf-8")

        calls: list[list[str]] = []
        live_calls: list[bool] = []

        def fake_dispatch(root: Path, argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            if dispatch_fail is not None and dispatch_fail(argv):
                return 1, "", "stub dispatch failure"
            return 0, "", ""

        def fake_live_snapshot() -> dict:
            live_calls.append(True)
            return r.pa.normalize_snapshot(data)

        argv = [
            "--crew-dir", str(crew),
            "--root", str(project),
        ]
        if json_output:
            argv.append("--json")
        argv.extend(extra or [])
        patches = [
            mock.patch.object(r, "run_dispatch", fake_dispatch),
            mock.patch.object(r.subprocess, "run",
                              side_effect=AssertionError("不得调用裸 herdr 命令")),
        ]
        if "--apply" in (extra or []):
            # S-1：--snapshot 与 --apply 互斥；apply 用例改为 live 快照注入，
            # 仍走完整 main()→reconcile→dispatch 路径；空 pane 探针按「无法确认」。
            patches.extend([
                mock.patch.object(r, "load_live_snapshot", fake_live_snapshot),
                mock.patch.object(r.pa, "probe_empty_pane", return_value=None),
            ])
        else:
            argv[:0] = ["--snapshot", str(snap)]
        self.live_snapshot_calls = live_calls
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            for patch in patches:
                stack.enter_context(patch)
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = r.main(argv)
        stdout = out.getvalue()
        payload = json.loads(stdout) if json_output and stdout.strip() else None
        return rc, stdout, err.getvalue(), calls, payload


class CaseResidualTest(ReaperCliBase):
    """① CASE 夹具：旧总舵 tab 回收、现役保留。"""

    def test_dry_run_reports_without_closing(self):
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY,
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual("handoff:new", payload["source"])
        self.assertEqual("helm-new", payload["current_helm"]["agent"])
        self.assertEqual("tGY", payload["current_helm"]["tab"])
        decisions = {
            finding["target"]: finding["decision"]
            for finding in payload["findings"] if finding["kind"] == "helm-tab"
        }
        self.assertEqual("close", decisions["tGS"])
        self.assertEqual("keep", decisions["tGY"])
        self.assertTrue(payload["actions"])
        self.assertTrue(all(a["status"] == "would_close" for a in payload["actions"]))

    def test_apply_closes_old_tab_and_keeps_current(self):
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([True], self.live_snapshot_calls)
        self.assertEqual(
            [["pane", "close", "--pane", "w12:p1", "--caller", "crew-reaper"], ["tab", "close", "--tab", "tGS", "--caller", "crew-reaper"]],
            calls,
        )
        self.assertTrue(all(call[:2] in (["pane", "close"], ["tab", "close"])
                            for call in calls))
        self.assertEqual(["closed", "closed"],
                         [action["status"] for action in payload["actions"]])
        self.assertNotIn("tGY", [call[-1] for call in calls])

    def test_report_lists_tab_panes_agents_and_status(self):
        rc, _out, err, _calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY,
        )
        self.assertEqual(rc, 0, err)
        tabs = {
            finding["target"]: finding
            for finding in payload["findings"] if finding["kind"] == "helm-tab"
        }
        self.assertEqual(1, tabs["tGS"]["panes"])
        self.assertEqual("总舵", tabs["tGS"]["label"])
        self.assertEqual(
            [{"name": "helm-old", "pane": "w12:p1", "agent_status": "idle"}],
            tabs["tGS"]["agents"],
        )

    def test_empty_stale_helm_tab_closed(self):
        rc, _out, err, calls, payload = self.run_cli(
            snapshot([tab("tEmpty", "总舵"), tab("tGY", "总舵")],
                     [pane("w12:p3", "tGY")],
                     [agent("helm-new", "w12:p3", "tGY", "working")]),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([["tab", "close", "--tab", "tEmpty", "--caller", "crew-reaper"]], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tEmpty")
        self.assertEqual("close", finding["decision"])


class WorkspaceScopeTest(ReaperCliBase):
    """跨 workspace 的 tab/agent 一律不触碰（herdr 列表是全局的）。"""

    def test_other_workspace_helm_tab_not_closed(self):
        tabs = [
            {"tab_id": "w1:t1", "label": "总舵", "workspace_id": "w1"},
            {"tab_id": "w2:t9", "label": "总舵", "workspace_id": "w2"},
        ]
        panes = [
            {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1"},
            {"pane_id": "w2:p9", "tab_id": "w2:t9", "workspace_id": "w2"},
        ]
        agents = [
            {"name": "helm-new", "pane_id": "w1:p1", "tab_id": "w1:t1",
             "workspace_id": "w1", "agent_status": "working"},
            {"name": "helm-other-project", "pane_id": "w2:p9", "tab_id": "w2:t9",
             "workspace_id": "w2", "agent_status": "idle"},
        ]
        registry = {"helm-new": registry_entry("w1:p1", "2026-11-12T09:00:00Z")}
        handoff = {
            "phase": "done", "result": "ok",
            "new": "helm-new", "newPane": "w1:p1", "newTab": "w1:t1",
        }
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=handoff, registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        targets = [finding["target"] for finding in payload["findings"]
                   if finding["kind"] == "helm-tab"]
        self.assertEqual(["w1:t1"], targets)


class WorkspaceUnknownTest(ReaperCliBase):
    """S-3：workspace 无法定位时不得全局对账（只报告、零关闭）。"""

    def test_missing_workspace_ids_report_only(self):
        tabs = [
            {"tab_id": "tCUR", "label": "总舵"},
            {"tab_id": "w12:t9", "label": "总舵"},  # 另一项目旧总舵（idle）
        ]
        panes = [
            {"pane_id": "p3", "tab_id": "tCUR"},
            {"pane_id": "w12:p9", "tab_id": "w12:t9"},
        ]
        agents = [
            {"name": "helm-cur", "pane_id": "p3", "tab_id": "tCUR",
             "agent_status": "working"},
            {"name": "other-proj-helm", "pane_id": "w12:p9", "tab_id": "w12:t9",
             "agent_status": "idle"},
        ]
        registry = {"helm-cur": registry_entry("p3", "2026-11-12T09:00:00Z")}
        handoff = {
            "phase": "done", "result": "ok",
            "old": "helm-old", "oldPane": "p1", "oldTab": "tGS",
            "new": "helm-cur", "newPane": "p3", "newTab": "tCUR",
        }
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=handoff, registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual([], payload["actions"])
        notes = [finding for finding in payload["findings"]
                 if finding["kind"] == "workspace-unknown"]
        self.assertEqual(1, len(notes))
        self.assertIn("workspace=unknown", notes[0]["reason"])
        decisions = [finding["decision"] for finding in payload["findings"]
                     if finding["kind"] == "helm-tab"]
        # 现役 tab 仍标 keep（永不关闭）；其余因 workspace 无法判定只报告。
        self.assertEqual(["keep", "report"], decisions)


class SafetyNegativeTest(ReaperCliBase):
    """② working/blocked/聚焦/白名单/空 pane 无法确认 → 不关。"""

    def _run_with_old_status(self, status: str):
        agents = [agent("helm-old", "w12:p1", "tGS", status),
                  agent("helm-new", "w12:p3", "tGY", "working")]
        return self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, agents),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )

    def test_working_old_agent_not_closed(self):
        rc, _out, err, calls, payload = self._run_with_old_status("working")
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("report", finding["decision"])
        self.assertIn("working", finding["reason"])

    def test_blocked_old_agent_not_closed(self):
        rc, _out, err, calls, payload = self._run_with_old_status("blocked")
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("report", finding["decision"])
        self.assertIn("blocked", finding["reason"])

    def test_focused_tab_not_closed(self):
        tabs = [tab("tGS", "总舵", focused=True), tab("tGY", "总舵")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("report", finding["decision"])
        self.assertIn("聚焦", finding["reason"])

    def test_whitelist_prefix_skipped(self):
        tabs = [tab("tGS", "Captain·总舵"), tab("tGY", "总舵")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("skip", finding["decision"])

    def test_extra_whitelist_prefix(self):
        tabs = [tab("tGS", "实验·总舵"), tab("tGY", "总舵")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY,
            extra=["--apply", "--whitelist-prefix", "实验·"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("skip", finding["decision"])

    def test_unconfirmed_empty_pane_report_only(self):
        panes = [pane("w12:p9", "tGS", status="idle"), pane("w12:p3", "tGY")]
        agents = [agent("helm-new", "w12:p3", "tGY", "working")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, panes, agents),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"]
                       if f["kind"] == "helm-tab" and f["target"] == "tGS")
        self.assertEqual("report", finding["decision"])
        self.assertIn("process-info", finding["reason"])


class UnknownCurrentHelmTest(ReaperCliBase):
    """③ 无法判定现役 → 只报告、零关闭。"""

    def test_no_handoff_and_no_registry_helm(self):
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS), extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual("unknown", payload["source"])
        self.assertEqual("unknown", payload["current_helm"]["source"])
        kinds = [finding["kind"] for finding in payload["findings"]]
        self.assertIn("current-helm-unknown", kinds)

    def test_registry_helm_tie_unknown(self):
        registry = {
            "helm-a": registry_entry("w12:p1", "2026-11-10T08:00:00Z"),
            "helm-b": registry_entry("w12:p3", "2026-11-10T08:00:00Z"),
        }
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual("unknown", payload["source"])
        self.assertIn("并列", payload["current_helm"]["reason"])

    def test_handoff_registry_contradiction_unknown(self):
        registry = {
            "helm-old": registry_entry("w12:p1", "2026-11-10T08:00:00Z"),
            "helm-other": registry_entry("w12:p3", "2026-11-12T10:00:00Z"),
        }
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual("unknown", payload["source"])
        self.assertIn("矛盾", payload["current_helm"]["reason"])

    def test_failed_handoff_unknown_not_registry_successor(self):
        registry = {
            "helm-old": registry_entry("w12:p1", "2026-11-10T08:00:00Z"),
            "helm-new": registry_entry("w12:p3", "2026-11-12T09:00:00Z"),
        }
        handoff = {**CASE_HANDOFF, "result": "failed", "error": "pane close"}
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=handoff, registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual("unknown", payload["source"])
        self.assertIn("失败", payload["current_helm"]["reason"])

    def test_registry_only_source_resolved(self):
        registry = {"helm-new": registry_entry("w12:p3", "2026-11-12T09:00:00Z")}
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            registry=registry, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual("registry:helm-new", payload["source"])
        self.assertIn(["pane", "close", "--pane", "w12:p1", "--caller", "crew-reaper"], calls)
        self.assertIn(["tab", "close", "--tab", "tGS", "--caller", "crew-reaper"], calls)


class IdempotencyTest(ReaperCliBase):
    """④ 第二次 --apply（herdr 已刷新）无动作。"""

    def test_second_apply_no_actions(self):
        _rc, _out, _err, first_calls, _payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertTrue(first_calls)
        rc, _out, err, calls, payload = self.run_cli(
            snapshot([tab("tGY", "总舵")], [pane("w12:p3", "tGY")],
                     [agent("helm-new", "w12:p3", "tGY", "working")]),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertEqual([], payload["actions"])


class OldHelmResidualTest(ReaperCliBase):
    """⑤ 旧 helm agent 残留：idle/done 回收，working 不回收。"""

    def test_idle_old_helm_in_plain_tab_reclaimed(self):
        tabs = [tab("tGY", "总舵"), tab("tX", "旧舵工作区")]
        panes = [pane("w12:p3", "tGY"), pane("w12:p1", "tX")]
        agents = [agent("helm-new", "w12:p3", "tGY", "working"),
                  agent("helm-old", "w12:p1", "tX", "idle")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([["pane", "close", "--pane", "w12:p1", "--caller", "crew-reaper"],
                          ["tab", "close", "--tab", "tX", "--caller", "crew-reaper"]], calls)
        finding = next(f for f in payload["findings"] if f["kind"] == "old-helm")
        self.assertEqual("close", finding["decision"])

    def test_done_old_helm_reclaimed(self):
        tabs = [tab("tGY", "总舵"), tab("tX", "旧舵工作区")]
        panes = [pane("w12:p3", "tGY"), pane("w12:p1", "tX")]
        agents = [agent("helm-new", "w12:p3", "tGY", "working"),
                  agent("helm-old", "w12:p1", "tX", "done")]
        rc, _out, err, calls, _payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertIn(["pane", "close", "--pane", "w12:p1", "--caller", "crew-reaper"], calls)

    def test_working_old_helm_not_reclaimed(self):
        tabs = [tab("tGY", "总舵"), tab("tX", "旧舵工作区")]
        panes = [pane("w12:p3", "tGY"), pane("w12:p1", "tX")]
        agents = [agent("helm-new", "w12:p3", "tGY", "working"),
                  agent("helm-old", "w12:p1", "tX", "working")]
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, extra=["--apply"],
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        finding = next(f for f in payload["findings"] if f["kind"] == "old-helm")
        self.assertEqual("report", finding["decision"])


class DispatchContractTest(ReaperCliBase):
    """⑥ 关闭只经 dispatch；失败不中断。"""

    def test_dispatch_failure_recorded_and_other_items_continue(self):
        tabs = [tab("tGS1", "总舵"), tab("tGS2", "总舵"), tab("tGY", "总舵")]
        panes = [pane("p1", "tGS1"), pane("p2", "tGS2"), pane("p3", "tGY")]
        agents = [agent("helm-old1", "p1", "tGS1", "idle"),
                  agent("helm-old2", "p2", "tGS2", "idle"),
                  agent("helm-new", "p3", "tGY", "working")]
        registry = {
            "helm-old1": registry_entry("p1", "2026-11-10T08:00:00Z"),
            "helm-old2": registry_entry("p2", "2026-11-11T08:00:00Z"),
            "helm-new": registry_entry("p3", "2026-11-12T09:00:00Z"),
        }
        handoff = {
            **CASE_HANDOFF,
            "oldPane": "p1", "oldTab": "tGS1",
            "newPane": "p3", "newTab": "tGY",
        }
        rc, _out, err, calls, payload = self.run_cli(
            snapshot(tabs, panes, agents),
            handoff=handoff, registry=registry, extra=["--apply"],
            dispatch_fail=lambda argv: "p1" in argv,  # caller 追加在 argv 尾部（FB-061）
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            [["pane", "close", "--pane", "p1", "--caller", "crew-reaper"],
             ["pane", "close", "--pane", "p2", "--caller", "crew-reaper"],
             ["tab", "close", "--tab", "tGS2", "--caller", "crew-reaper"]],
            calls,
        )
        statuses = {(a["action"], a["target"]): a["status"]
                    for a in payload["actions"]}
        self.assertEqual("failed", statuses[("pane close", "p1")])
        self.assertEqual("skipped", statuses[("tab close", "tGS1")])
        self.assertEqual("closed", statuses[("pane close", "p2")])
        self.assertEqual("closed", statuses[("tab close", "tGS2")])


class CliContractTest(ReaperCliBase):
    """CLI：--help / --json 契约 / 参数与数据错误 rc=1。"""

    def test_help_documents_contract(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                r.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("dry-run", "--apply", "--json", "handoff", "registry",
                      "dispatch", "pane-audit", "working", "白名单",
                      "无法判定", "guard"):
            self.assertIn(token, text)

    def test_json_payload_contract(self):
        rc, _out, err, _calls, payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY,
        )
        self.assertEqual(rc, 0, err)
        for key in ("findings", "actions", "current_helm", "source", "applied"):
            self.assertIn(key, payload)
        self.assertFalse(payload["applied"])
        self.assertEqual({"agent", "pane", "tab", "source"},
                         set(payload["current_helm"]))

    def test_missing_snapshot_rc1(self):
        rc = r.main(["--snapshot", str(self.tmp / "missing.json"),
                     "--crew-dir", str(self.tmp), "--root", str(self.tmp)])
        self.assertEqual(1, rc)

    def test_bad_argument_rc1(self):
        rc = r.main(["--bogus"])
        self.assertEqual(1, rc)

    def test_corrupt_registry_rc1(self):
        project = self.tmp / "project"
        crew = self.tmp / "crew"
        project.mkdir()
        crew.mkdir()
        (crew / "dispatch-agents.json").write_text("{bad json", encoding="utf-8")
        snap = self.tmp / "snapshot.json"
        write_json(snap, snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS))
        rc = r.main(["--snapshot", str(snap), "--crew-dir", str(crew),
                     "--root", str(project), "--json"])
        self.assertEqual(1, rc)

    def test_human_dry_run_output(self):
        rc, out, err, calls, _payload = self.run_cli(
            snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS),
            handoff=CASE_HANDOFF, registry=CASE_REGISTRY, json_output=False,
        )
        self.assertEqual(rc, 0, err)
        self.assertEqual([], calls)
        self.assertIn("handoff:new", out)
        self.assertIn("would_close", out)
        self.assertIn("tGS", out)
        self.assertIn("dry-run", out)


class ManifestRegistrationTest(unittest.TestCase):
    """R-1：crew-reaper.py 必须进 SCRIPT_MANIFEST，init 安装与 manifest 条目同时覆盖。"""

    def test_reaper_in_script_manifest(self):
        self.assertIn("crew-reaper.py", init_mod.SCRIPT_MANIFEST)

    def test_init_installs_reaper_and_manifest_entry(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / "proj"
            root.mkdir()
            subprocess.run(["git", "init", "-q", str(root)], check=True,
                           capture_output=True)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = init_mod.run(["--root", str(root), "--yes"])
            self.assertEqual(0, rc, out.getvalue() + err.getvalue())
            self.assertTrue((root / "scripts" / "crew-reaper.py").is_file())
            manifest = json.loads(
                (root / "docs/crew/manifest.json").read_text(encoding="utf-8"))
            script_paths = {
                entry.get("path") for entry in manifest.get("entries", [])
                if isinstance(entry, dict) and entry.get("kind") == "file"
            }
            self.assertIn("scripts/crew-reaper.py", script_paths)


class SnapshotApplyMutexTest(ReaperCliBase):
    """S-1：--snapshot 与 --apply 互斥（rc=2），fixture 过期时不得真实关闭。"""

    def test_snapshot_with_apply_rejected_rc2(self):
        project = self.tmp / "project"
        crew = self.tmp / "crew"
        project.mkdir()
        crew.mkdir()
        write_json(crew / "dispatch-agents.json", CASE_REGISTRY)
        (crew / "guard.handoff").write_text(
            handoff_text(CASE_HANDOFF), encoding="utf-8")
        snap = self.tmp / "snapshot.json"
        write_json(snap, snapshot(CASE_TABS, CASE_PANES, CASE_AGENTS))
        calls: list[list[str]] = []

        def fake_dispatch(root: Path, argv: list[str]) -> tuple[int, str, str]:
            calls.append(list(argv))
            return 0, "", ""

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(r, "run_dispatch", fake_dispatch), \
                mock.patch.object(r.subprocess, "run",
                                  side_effect=AssertionError("互斥拒绝时不得调用 herdr")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = r.main(["--snapshot", str(snap), "--apply", "--json",
                         "--crew-dir", str(crew), "--root", str(project)])
        self.assertEqual(2, rc)
        self.assertEqual([], calls)
        message = err.getvalue()
        self.assertIn("互斥", message)
        self.assertIn("--snapshot", message)
        self.assertIn("--apply", message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
