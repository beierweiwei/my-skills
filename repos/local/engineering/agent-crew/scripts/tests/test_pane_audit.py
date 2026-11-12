#!/usr/bin/env python3
"""pane-audit.py crew-v2 1.6/2.2.8 用例：巡检告警 / dry-run / --apply 安全关闭 / blocked SLA。

覆盖：
① 单 tab >6 pane、总舵 tab >1 pane、总舵空 tab / 孤儿 pane、pane 无 registry、registry 无 pane、空 tab 各自告警；
② 白名单（Captain·* / 流程优化·* / 总舵 tab 仅兜底报告 / helm agent / --helm-pane / --whitelist-prefix）口径；
③ dry-run 不关闭任何 pane/tab；--apply 只关白名单外的空 tab，working 与孤儿 pane 不动；
④ 输出含可执行下一步；CLI 自解释；fixture JSON 注入，不调用真实 herdr；
⑤ blocked-overdue：最新回执按 ts、29/30/31 分钟边界、done 覆盖旧 blocked、损坏回执、
   work_slot/名字匹配、helm/白名单跳过、--apply 不关、crew dir 缺失 fail-soft。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

_SPEC_PATH = Path(__file__).resolve().parent.parent / "pane-audit.py"
_spec = importlib.util.spec_from_file_location("pane_audit", _SPEC_PATH)
pa = importlib.util.module_from_spec(_spec)
sys.modules["pane_audit"] = pa  # dataclass 注解解析需要模块已注册
_spec.loader.exec_module(pa)


def tab(tab_id: str, label: str, workspace: str | None = None) -> dict:
    item = {"tab_id": tab_id, "label": label}
    if workspace:
        item["workspace_id"] = workspace
    return item


def pane(pane_id: str, tab_id: str, status: str | None = None,
         agent_name: str | None = None) -> dict:
    item = {"pane_id": pane_id, "tab_id": tab_id}
    if status:
        item["agent_status"] = status
    if agent_name:
        item["agent"] = agent_name
    return item


def agent(name: str, pane_id: str, tab_id: str = "t1", status: str = "working") -> dict:
    return {
        "name": name,
        "pane_id": pane_id,
        "tab_id": tab_id,
        "agent_status": status,
    }


def snapshot(tabs: list[dict], panes: list[dict], agents: list[dict]) -> dict:
    return {"tabs": tabs, "panes": panes, "agents": agents}


def kinds(findings: list[pa.Finding]) -> list[tuple[str, str]]:
    return [(finding.kind, finding.target) for finding in findings]


BASE_NOW = datetime(2026, 11, 10, 15, 0, 0, tzinfo=timezone.utc)


def receipt(
    receipt_id: str,
    status: str = "blocked",
    *,
    minutes_ago: float = 0,
    work_slot: str | None = "p1",
    sender: str = "a1",
    ts: str | None = None,
) -> dict:
    """构造 R2 字段回执；ts 默认相对 BASE_NOW 的 UTC ISO8601。"""
    if ts is None:
        ts = (BASE_NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "receipt_id": receipt_id,
        "from": sender,
        "runtime": "qoder",
        "task": "T-1",
        "status": status,
        "changed_files": [],
        "evidence": [],
        "work_slot": work_slot,
        "findings": [],
        "summary": "测试回执",
        "ts": ts,
    }


def iso_ago(minutes: float) -> str:
    """相对真实当前时间的 UTC ISO8601（CLI 级用例用，无法注入时钟）。"""
    moment = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class AuditRulesTest(unittest.TestCase):
    """契约①②：四类告警与白名单口径（纯函数）。"""

    def test_tab_over_six_panes_warned(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane(f"p{i}", "t1") for i in range(7)],
            [agent(f"a{i}", f"p{i}") for i in range(7)],
        )
        self.assertEqual(kinds(pa.audit(data)), [("tab-overflow", "t1")])

    def test_six_panes_is_within_limit(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane(f"p{i}", "t1") for i in range(6)],
            [agent(f"a{i}", f"p{i}") for i in range(6)],
        )
        self.assertEqual(pa.audit(data), [])

    def test_helm_tab_multi_pane_warned_report_only(self):
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p1", "t3"), pane("p2", "t3")],
            [agent("helm-1", "p1"), agent("helm-2", "p2")],
        )
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("helm-tab-multi-pane", "t3")])
        self.assertIsNone(findings[0].action)  # 只报告，不自动关
        self.assertIn("2 个 pane", findings[0].detail)

    def test_helm_tab_single_pane_not_flagged(self):
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p1", "t3")],
            [agent("helm-1", "p1")],
        )
        self.assertEqual(pa.audit(data), [])

    def test_helm_tab_empty_reported_without_action(self):
        data = snapshot([tab("t3", "crew总舵")], [], [])
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("empty-tab", "t3")])
        self.assertIsNone(findings[0].action)  # 总舵 tab 仅报告，不自动关

    def test_helm_tab_orphan_pane_reported(self):
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p2", "t3")],
            [],
        )
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("orphan-pane", "p2")])
        self.assertIsNone(findings[0].action)

    def test_orphan_pane_warned(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("a1", "p1")],
        )
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("orphan-pane", "p2")])
        self.assertIsNone(findings[0].action)  # 孤儿 pane 只报告，不自动关

    def test_orphan_registry_warned(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1")],
            [agent("a1", "p1"), agent("a9", "p9")],
        )
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("orphan-registry", "a9")])
        self.assertIn("p9", findings[0].detail)

    def test_empty_tab_warned_and_actionable(self):
        data = snapshot(
            [tab("t1", "需求·任务"), tab("t2", "杂活")],
            [pane("p1", "t1")],
            [agent("a1", "p1")],
        )
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("empty-tab", "t2")])
        self.assertEqual(findings[0].action, "tab")

    def test_whitelisted_labels_never_flagged(self):
        data = snapshot(
            [tab("t1", "Captain·甲"), tab("t2", "流程优化·乙"), tab("t3", "总舵"), tab("t4", "普通")],
            [pane(f"p{i}", "t1") for i in range(7)]
            + [pane(f"q{i}", "t2") for i in range(7)]
            + [pane("r1", "t3")]
            + [pane("z1", "t4")],
            [agent("helm-1", "r1", tab_id="t3")],
        )
        self.assertEqual(kinds(pa.audit(data)), [("orphan-pane", "z1")])

    def test_helm_pane_option_skips(self):
        data = snapshot(
            [tab("t1", "普通")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("a1", "p1")],
        )
        self.assertEqual(pa.audit(data, helm_panes=frozenset({"p2"})), [])

    def test_helm_agent_registry_not_flagged(self):
        data = snapshot(
            [tab("t1", "普通")],
            [pane("p1", "t1")],
            [agent("a1", "p1"), agent("helm-crew", "p9")],
        )
        self.assertEqual(pa.audit(data), [])

    def test_custom_whitelist_prefix(self):
        data = snapshot([tab("t1", "实验·甲")], [], [])
        prefixes = pa.DEFAULT_WHITELIST_PREFIXES + ("实验·",)
        self.assertEqual(pa.audit(data, prefixes), [])
        self.assertEqual(kinds(pa.audit(data)), [("empty-tab", "t1")])  # 未配置时仍告警

    def test_custom_helm_label_single_pane_not_flagged(self):
        data = snapshot(
            [tab("t3", "steering")],
            [pane("p1", "t3")],
            [agent("helm-1", "p1", tab_id="t3")],
        )
        self.assertEqual(pa.audit(data, helm_labels=("总舵", "steering")), [])

    def test_custom_helm_label_multi_pane_reported(self):
        data = snapshot(
            [tab("t3", "steering")],
            [pane("p1", "t3"), pane("p2", "t3")],
            [agent("helm-1", "p1", tab_id="t3"), agent("helm-2", "p2", tab_id="t3")],
        )
        findings = pa.audit(data, helm_labels=("总舵", "steering"))
        self.assertEqual(kinds(findings), [("helm-tab-multi-pane", "t3")])
        self.assertIsNone(findings[0].action)

    def test_helm_recognition_precedes_whitelist_prefix(self):
        # 标签同时命中总舵与白名单前缀：按总舵 tab 处理（仅单 pane 检查）
        data = snapshot(
            [tab("t3", "Captain·总舵支持")],
            [pane("p1", "t3"), pane("p2", "t3")],
            [agent("a1", "p1", tab_id="t3"), agent("a2", "p2", tab_id="t3")],
        )
        self.assertEqual(kinds(pa.audit(data)), [("helm-tab-multi-pane", "t3")])

    def test_working_orphan_is_report_only(self):
        data = snapshot([tab("t1", "普通")], [pane("p9", "t1", status="working")], [])
        findings = pa.audit(data)
        self.assertEqual(kinds(findings), [("orphan-pane", "p9")])
        self.assertIn("working 不关闭", findings[0].detail)
        self.assertIsNone(findings[0].action)

    def test_findings_include_next_step(self):
        data = snapshot([tab("t2", "杂活")], [], [])
        text = pa.format_finding(pa.audit(data)[0])
        self.assertIn("[告警] 空 tab", text)
        self.assertIn("下一步：", text)


class BlockedOverdueAuditTest(unittest.TestCase):
    """契约⑤：blocked 最新回执年龄口径（纯函数，注入时钟）。"""

    def _blocked_snapshot(self, name: str = "a1", pane_id: str = "p1",
                          tab_label: str = "普通") -> dict:
        return snapshot(
            [tab("t1", tab_label)],
            [pane(pane_id, "t1", status="blocked")],
            [agent(name, pane_id, tab_id="t1", status="blocked")],
        )

    def test_blocked_overdue_reported_report_only(self):
        data = self._blocked_snapshot()
        receipts = [receipt("r-blocked", minutes_ago=31)]
        findings = pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)
        self.assertEqual(kinds(findings), [("blocked-overdue", "p1")])
        finding = findings[0]
        self.assertIsNone(finding.action)  # 只报告，--apply 不关
        self.assertIn("blocked 工位超时未处置", pa.KIND_TEXT["blocked-overdue"])
        self.assertIn("r-blocked", finding.detail)
        self.assertIn("31 分钟", finding.detail)
        self.assertIn("grace 30", finding.detail)
        for token in ("R6", "re-task", "close", "escalate", "2 小时"):
            self.assertIn(token, finding.next_step)

    def test_age_boundary_strictly_greater_than_grace(self):
        data = self._blocked_snapshot()
        for minutes in (29, 30):
            with self.subTest(minutes=minutes):
                findings = pa.audit(
                    data, blocked_receipts=[receipt("r1", minutes_ago=minutes)],
                    now=BASE_NOW,
                )
                self.assertEqual(findings, [])  # age == grace 不报
        findings = pa.audit(
            data, blocked_receipts=[receipt("r1", minutes_ago=31)], now=BASE_NOW)
        self.assertEqual(kinds(findings), [("blocked-overdue", "p1")])

    def test_custom_grace_parameter(self):
        data = self._blocked_snapshot()
        receipts = [receipt("r1", minutes_ago=31)]
        self.assertEqual(
            pa.audit(data, blocked_receipts=receipts, now=BASE_NOW,
                     blocked_grace_minutes=60),
            [],
        )
        self.assertEqual(
            kinds(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW,
                           blocked_grace_minutes=30)),
            [("blocked-overdue", "p1")],
        )

    def test_done_overrides_older_blocked_regardless_of_order(self):
        data = self._blocked_snapshot()
        old_blocked = receipt("r-old", minutes_ago=60)
        new_done = receipt("r-new", "done", minutes_ago=5)
        for receipts in ([old_blocked, new_done], [new_done, old_blocked]):
            with self.subTest(order=[r["receipt_id"] for r in receipts]):
                self.assertEqual(
                    pa.audit(data, blocked_receipts=receipts, now=BASE_NOW), [])

    def test_newer_blocked_wins_over_older_done(self):
        data = self._blocked_snapshot()
        receipts = [
            receipt("r-old-done", "done", minutes_ago=60),
            receipt("r-new-blocked", minutes_ago=31),
        ]
        findings = pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)
        self.assertEqual(kinds(findings), [("blocked-overdue", "p1")])
        self.assertIn("r-new-blocked", findings[0].detail)

    def test_captain_receipt_also_suppresses(self):
        data = self._blocked_snapshot()
        receipts = [receipt("r-old", minutes_ago=60),
                    receipt("r-captain", "captain", minutes_ago=2)]
        self.assertEqual(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW), [])

    def test_corrupt_or_unparseable_receipts_skipped(self):
        data = self._blocked_snapshot()
        receipts = [
            "not-a-dict",                       # 非对象回执
            {"receipt_id": "no-ts", "from": "a1", "work_slot": "p1", "status": "blocked"},
            receipt("bad-ts", ts="2026-13-99T99:99:99Z"),
            receipt("numeric-ts", ts=123),
        ]
        findings = pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)
        self.assertEqual(findings, [])  # fail-soft：跳过损坏条目，不抛异常

    def test_match_by_work_slot_preferred(self):
        data = self._blocked_snapshot(name="a1", pane_id="p1")
        receipts = [receipt("r-slot", minutes_ago=45, work_slot="p1", sender="other")]
        findings = pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)
        self.assertEqual(kinds(findings), [("blocked-overdue", "p1")])
        self.assertIn("r-slot", findings[0].detail)

    def test_match_by_name_when_slot_absent(self):
        data = self._blocked_snapshot(name="a1", pane_id="p1")
        receipts = [receipt("r-name", minutes_ago=45, work_slot=None, sender="a1")]
        findings = pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)
        self.assertEqual(kinds(findings), [("blocked-overdue", "p1")])
        self.assertIn("r-name", findings[0].detail)

    def test_no_matching_receipt_not_reported(self):
        data = self._blocked_snapshot(name="a1", pane_id="p1")
        receipts = [receipt("r-other", minutes_ago=60, work_slot="p9", sender="z9")]
        self.assertEqual(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW), [])

    def test_non_blocked_agent_not_reported(self):
        data = snapshot(
            [tab("t1", "普通")], [pane("p1", "t1", status="working")],
            [agent("a1", "p1", tab_id="t1", status="working")],
        )
        receipts = [receipt("r1", minutes_ago=60)]
        self.assertEqual(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW), [])

    def test_helm_agent_and_whitelisted_tab_skipped(self):
        data = snapshot(
            [tab("t1", "普通"), tab("t2", "Captain·甲")],
            [pane("p1", "t1", status="blocked"), pane("p2", "t2", status="blocked")],
            [
                agent("helm-crew", "p1", tab_id="t1", status="blocked"),
                agent("a2", "p2", tab_id="t2", status="blocked"),
            ],
        )
        receipts = [
            receipt("r-helm", minutes_ago=90, work_slot="p1", sender="helm-crew"),
            receipt("r-white", minutes_ago=90, work_slot="p2", sender="a2"),
        ]
        self.assertEqual(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW), [])

    def test_no_receipts_parameter_keeps_legacy_behavior(self):
        data = self._blocked_snapshot()
        self.assertEqual(pa.audit(data), [])  # 未传 blocked_receipts 不做 blocked 巡检

    def test_naive_ts_treated_as_utc(self):
        data = self._blocked_snapshot()
        receipts = [receipt("r-naive", ts="2026-11-10T14:29:00")]  # 无时区 = UTC，31 分钟前
        self.assertEqual(
            kinds(pa.audit(data, blocked_receipts=receipts, now=BASE_NOW)),
            [("blocked-overdue", "p1")],
        )

    def test_inbox_load_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmp:
            crew = Path(tmp) / "crew"
            self.assertEqual(pa.load_receipts(crew), [])  # 目录缺失
            inbox = crew / "inbox"
            inbox.mkdir(parents=True)
            (inbox / "bad.json").write_text("{not json", encoding="utf-8")
            (inbox / "list.json").write_text("[1, 2]", encoding="utf-8")
            (inbox / "good.json").write_text(
                json.dumps(receipt("r1"), ensure_ascii=False), encoding="utf-8")
            loaded = pa.load_receipts(crew)
            self.assertEqual([item["receipt_id"] for item in loaded], ["r1"])
            self.assertEqual(pa.load_receipts(None), [])


class NormalizeSnapshotTest(unittest.TestCase):
    def test_accepts_plain_object(self):
        normalized = pa.normalize_snapshot({"tabs": [], "panes": [], "agents": []})
        self.assertEqual(normalized, {"tabs": [], "panes": [], "agents": []})

    def test_accepts_herdr_result_wrapper(self):
        payload = {"result": {"snapshot": {"tabs": [tab("t1", "甲")], "panes": [], "agents": []}}}
        self.assertEqual(len(pa.normalize_snapshot(payload)["tabs"]), 1)

    def test_rejects_non_list_sections(self):
        with self.assertRaises(pa.AuditError):
            pa.normalize_snapshot({"tabs": {"x": 1}})


class _MainTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, data: dict, extra: list[str] | None = None,
             close_tab_ok: bool = True,
             empty_panes: set[str] | None = None) -> tuple[int, str, str, dict]:
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        recorded = {"panes": [], "tabs": []}

        def fake_close_pane(pane_id: str) -> bool:
            recorded["panes"].append(pane_id)
            return True

        def fake_close_tab(tab_id: str) -> bool:
            recorded["tabs"].append(tab_id)
            return close_tab_ok

        def fake_probe(pane_id: str) -> bool | None:
            # 未声明的 pane 一律「无法判定」，保持既有用例的只报告口径
            return pane_id in empty_panes if empty_panes is not None else None

        extra = list(extra or [])
        argv = ["--snapshot", str(path)] + extra
        if "--apply" in extra:
            # S-1：--apply 与 --snapshot 互斥；apply 用例改以 live snapshot 注入夹具
            argv = extra
            live_patch = mock.patch.object(pa, "load_live_snapshot", lambda: data)
            live_patch.start()
            self.addCleanup(live_patch.stop)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_pane", fake_close_pane), \
                mock.patch.object(pa, "close_tab", fake_close_tab), \
                mock.patch.object(pa, "probe_empty_pane", fake_probe):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(argv)
        return rc, out.getvalue(), err.getvalue(), recorded

    def _apply_fixture(self) -> dict:
        return snapshot(
            [
                tab("t1", "需求·任务"),   # 7 pane → 超限（仅报告）
                tab("t2", "杂活"),        # 空 tab → 安全关闭
                tab("t3", "Captain·甲"),  # 白名单空 tab → 不关
                tab("t4", "普通"),        # 孤儿 pane → 仅报告
            ],
            [pane(f"p{i}", "t1", status="working") for i in range(1, 8)]
            + [pane("p9", "t4")],
            [agent(f"a{i}", f"p{i}") for i in range(1, 8)],
        )

    def test_dry_run_reports_but_closes_nothing(self):
        rc, out, err, recorded = self._run(self._apply_fixture())
        self.assertEqual(rc, 0, err)
        self.assertEqual(recorded, {"panes": [], "tabs": []})
        self.assertIn("dry-run", out)
        self.assertIn("可安全自动关闭 1 项", out)
        self.assertIn("[告警] 单 tab pane 超限", out)
        self.assertIn("[告警] 空 tab", out)
        self.assertIn("下一步：", out)

    def test_apply_closes_only_safe_tab(self):
        rc, out, err, recorded = self._run(self._apply_fixture(), ["--apply"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(recorded["tabs"], ["t2"])  # 仅白名单外空 tab
        self.assertEqual(recorded["panes"], [])     # 超限/孤儿 pane 均不自动关
        self.assertIn("已关闭 tab t2", out)

    def test_apply_never_closes_working_pane(self):
        data = snapshot([tab("t1", "普通")], [pane("p1", "t1", status="working")], [])
        rc, out, err, recorded = self._run(data, ["--apply"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(recorded["panes"], [])
        self.assertIn("working 不关闭", out)

    def test_apply_failure_returns_nonzero(self):
        data = snapshot([tab("t2", "杂活")], [], [])
        rc, _out, err, recorded = self._run(data, ["--apply"], close_tab_ok=False)
        self.assertEqual(rc, 1)
        self.assertEqual(recorded["tabs"], ["t2"])
        self.assertIn("[失败]", err)

    def test_clean_snapshot_low_output(self):
        data = snapshot([tab("t1", "普通")], [pane("p1", "t1")], [agent("a1", "p1")])
        rc, out, err, _recorded = self._run(data)
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertEqual(len(out.strip().splitlines()), 1)
        self.assertIn("巡检通过", out)

    def test_workspace_filter(self):
        data = snapshot(
            [tab("t1", "甲", workspace="wA"), tab("t2", "乙", workspace="wB")],
            [pane("p1", "t1")],
            [agent("a1", "p1")],
        )
        rc, out, _err, _recorded = self._run(data, ["--workspace", "wA"])
        self.assertEqual(rc, 0)
        self.assertIn("1 个 tab", out)

    def _write_config(self, payload: object) -> Path:
        project = self.tmp / "proj"
        (project / ".planning").mkdir(parents=True, exist_ok=True)
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        (project / ".planning/orchestration.json").write_text(text, encoding="utf-8")
        return project

    def test_config_custom_helm_label_recognized(self):
        project = self._write_config({"helm": {"tabLabel": "steering"}})
        data = snapshot(
            [tab("t3", "steering")],
            [pane("p1", "t3"), pane("p2", "t3")],
            [agent("helm-1", "p1", tab_id="t3"), agent("helm-2", "p2", tab_id="t3")],
        )
        rc, out, err, _recorded = self._run(data, ["--root", str(project)])
        self.assertEqual(rc, 0, err)
        self.assertIn("[告警] 总舵 tab 多 pane", out)

    def test_config_missing_or_corrupt_falls_back_to_default_label(self):
        data = snapshot([tab("t3", "总舵")], [pane("p1", "t3")],
                        [agent("helm-1", "p1", tab_id="t3")])
        for name, payload in (("missing", None), ("corrupt", "{not json"),
                              ("invalid", {"helm": {"tabLabel": 123}})):
            with self.subTest(config=name):
                extra = [] if payload is None else [
                    "--root", str(self._write_config(payload))]
                rc, out, err, _recorded = self._run(data, extra)
                self.assertEqual(rc, 0, err)
                self.assertIn("巡检通过", out)  # 默认「总舵」仍被识别，单 pane 无告警

    def test_unresolvable_tilde_root_does_not_crash(self):
        data = snapshot([tab("t3", "总舵")], [pane("p1", "t3")],
                        [agent("helm-1", "p1", tab_id="t3")])
        rc, out, err, _recorded = self._run(data, ["--root", "~nosuchuser4242/x"])
        self.assertEqual(rc, 0, err)
        self.assertIn("巡检通过", out)
        self.assertNotIn("Traceback", err)
        self.assertEqual(pa.load_helm_labels("~nosuchuser4242/x"), ("总舵",))

    def test_help_documents_non_helm_and_priority(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit):
                pa.main(["--help"])
        text = out.getvalue()
        self.assertIn("非总舵", text)
        self.assertIn("优先", text)
        self.assertIn("helm.tabLabel", text)
        self.assertIn("非总舵", pa.__doc__)
        self.assertIn("优先", pa.__doc__)


class BlockedOverdueMainTest(unittest.TestCase):
    """契约④⑤：CLI --blocked-grace/--crew-dir、--apply 不关、crew dir fail-soft。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _write_crew_dir(self, items: list[object]) -> Path:
        crew = self.tmp / "crew"
        inbox = crew / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        for index, item in enumerate(items):
            if isinstance(item, dict):
                name = str(item.get("receipt_id") or f"r{index}")
                text = json.dumps(item, ensure_ascii=False)
            else:
                name = f"corrupt{index}"
                text = str(item)
            (inbox / f"{name}.json").write_text(text, encoding="utf-8")
        return crew

    def _run(self, data: dict, extra: list[str] | None = None):
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        recorded = {"panes": [], "tabs": []}

        def fake_close_pane(pane_id: str) -> bool:
            recorded["panes"].append(pane_id)
            return True

        def fake_close_tab(tab_id: str) -> bool:
            recorded["tabs"].append(tab_id)
            return True

        extra = list(extra or [])
        argv = ["--snapshot", str(path)] + extra
        if "--apply" in extra:
            # S-1：--apply 与 --snapshot 互斥；apply 用例改以 live snapshot 注入夹具
            argv = extra
            live_patch = mock.patch.object(pa, "load_live_snapshot", lambda: data)
            live_patch.start()
            self.addCleanup(live_patch.stop)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_pane", fake_close_pane), \
                mock.patch.object(pa, "close_tab", fake_close_tab), \
                mock.patch.object(pa, "probe_empty_pane", lambda pane_id: None):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(argv)
        return rc, out.getvalue(), err.getvalue(), recorded

    def _blocked_fixture(self) -> dict:
        return snapshot(
            [tab("t1", "普通")],
            [pane("p1", "t1", status="blocked")],
            [agent("a1", "p1", tab_id="t1", status="blocked")],
        )

    def test_cli_reports_blocked_overdue(self):
        crew = self._write_crew_dir([receipt("r1", ts=iso_ago(45))])
        rc, out, err, recorded = self._run(
            self._blocked_fixture(), ["--crew-dir", str(crew)])
        self.assertEqual(rc, 0, err)
        self.assertIn("[告警] blocked 工位超时未处置", out)
        self.assertIn("r1", out)
        self.assertIn("R6", out)
        self.assertEqual(recorded, {"panes": [], "tabs": []})

    def test_apply_never_closes_blocked_overdue(self):
        crew = self._write_crew_dir([receipt("r1", ts=iso_ago(45))])
        rc, out, err, recorded = self._run(
            self._blocked_fixture(), ["--crew-dir", str(crew), "--apply"])
        self.assertEqual(rc, 0, err)
        self.assertEqual(recorded, {"panes": [], "tabs": []})  # 只报告，不关闭
        self.assertIn("其余 1 项仅报告", out)

    def test_custom_blocked_grace_cli(self):
        crew = self._write_crew_dir([receipt("r1", ts=iso_ago(45))])
        rc, out, err, _recorded = self._run(
            self._blocked_fixture(),
            ["--crew-dir", str(crew), "--blocked-grace", "60"])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("blocked 工位超时未处置", out)

    def test_invalid_blocked_grace_returns_nonzero(self):
        for value in ("0", "-5"):
            with self.subTest(value=value):
                rc, _out, err, recorded = self._run(
                    self._blocked_fixture(), ["--blocked-grace", value])
                self.assertNotEqual(rc, 0)
                self.assertIn("--blocked-grace", err)
                self.assertEqual(recorded, {"panes": [], "tabs": []})

    def test_missing_crew_dir_fail_soft(self):
        rc, out, err, _recorded = self._run(
            self._blocked_fixture(), ["--crew-dir", str(self.tmp / "nope")])
        self.assertEqual(rc, 0, err)
        self.assertNotIn("blocked 工位超时未处置", out)
        self.assertNotIn("Traceback", err)

    def test_corrupt_receipt_files_fail_soft(self):
        crew = self._write_crew_dir(["{broken", "[1,2]",
                                     receipt("r1", ts=iso_ago(45))])
        rc, out, err, _recorded = self._run(
            self._blocked_fixture(), ["--crew-dir", str(crew)])
        self.assertEqual(rc, 0, err)
        self.assertIn("r1", out)  # 损坏文件跳过，有效回执照常判定


class CliTest(unittest.TestCase):
    def test_help_is_self_explanatory(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                pa.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("--snapshot", "--apply", "--whitelist-prefix", "--helm-pane",
                      "--blocked-grace", "--crew-dir", "blocked", "R6",
                      "白名单", "working", "dry-run", "≤6", "总舵"):
            self.assertIn(token, text)


class T45EmptyPaneTest(unittest.TestCase):
    """T45①③：多余空 pane 自动关（含总舵 tab / FB-038）；working 永不关；白名单跳过。"""

    def test_crew_tab_empty_pane_is_actionable(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("a1", "p1", tab_id="t1")],
        )
        findings = pa.audit(data, empty_probe=lambda pane_id: pane_id == "p2")
        self.assertIn(("empty-pane", "p2"), kinds(findings))
        finding = next(f for f in findings if f.kind == "empty-pane")
        self.assertEqual("pane", finding.action)
        self.assertIn("空闲 shell", finding.detail)
        self.assertNotIn(("orphan-pane", "p2"), kinds(findings))

    def test_working_pane_never_closed(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1"), pane("p2", "t1", status="working")],
            [agent("a1", "p1", tab_id="t1")],
        )
        findings = pa.audit(data, empty_probe=lambda _pane_id: True)
        self.assertEqual([], [f for f in findings if f.action])
        self.assertIn(("orphan-pane", "p2"), kinds(findings))

    def test_tab_without_registry_agent_report_only(self):
        # 纯人工 tab（无 registry 工位）：不接管，只报告
        data = snapshot([tab("t1", "普通")], [pane("p1", "t1")], [])
        findings = pa.audit(data, empty_probe=lambda _pane_id: True)
        self.assertIn(("orphan-pane", "p1"), kinds(findings))
        self.assertIsNone(findings[0].action)

    def test_whitelisted_tab_empty_pane_skipped(self):
        for label in ("Captain·甲", "流程优化·乙"):
            with self.subTest(label=label):
                data = snapshot([tab("t1", label)], [pane("p1", "t1")], [])
                self.assertEqual([], pa.audit(data, empty_probe=lambda _p: True))

    def test_helm_tab_extra_empty_pane_actionable_fb038(self):
        # FB-038：交接后总舵 tab 2 pane（继任总舵 + 空 shell）
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p1", "t3", status="idle"), pane("p2", "t3")],
            [agent("helm-1", "p1", tab_id="t3", status="idle")],
        )
        findings = pa.audit(data, empty_probe=lambda pane_id: pane_id == "p2")
        self.assertIn(("empty-pane", "p2"), kinds(findings))
        self.assertIn(("helm-tab-multi-pane", "t3"), kinds(findings))
        empty = next(f for f in findings if f.kind == "empty-pane")
        self.assertEqual("pane", empty.action)
        multi = next(f for f in findings if f.kind == "helm-tab-multi-pane")
        self.assertIsNone(multi.action)  # 多 pane 报告口径保持

    def test_helm_tab_without_identifiable_helm_pane_report_only(self):
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p1", "t3"), pane("p2", "t3")],
            [],
        )
        findings = pa.audit(data, empty_probe=lambda _pane_id: True)
        self.assertEqual([], [f for f in findings if f.action])
        self.assertIn(("helm-tab-multi-pane", "t3"), kinds(findings))

    def test_helm_pane_option_never_closed(self):
        data = snapshot(
            [tab("t3", "总舵")],
            [pane("p1", "t3", status="idle"), pane("p2", "t3")],
            [agent("helm-1", "p1", tab_id="t3", status="idle")],
        )
        findings = pa.audit(
            data, helm_panes=frozenset({"p2"}), empty_probe=lambda _pane_id: True)
        self.assertEqual([], [f for f in findings if f.action])

    def test_probe_unknown_report_only(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("a1", "p1", tab_id="t1")],
        )
        findings = pa.audit(data, empty_probe=lambda _pane_id: None)
        self.assertIn(("orphan-pane", "p2"), kinds(findings))
        self.assertEqual([], [f for f in findings if f.action])

    def test_unnamed_runtime_tui_tab_report_only(self):
        # 个人 tab：无名 runtime TUI（claude/qodercli）不算工位，空 pane 不自动关
        data = snapshot(
            [tab("t1", "个人")],
            [pane("p1", "t1", agent_name="claude"), pane("p2", "t1")],
            [{"name": None, "pane_id": "p1", "tab_id": "t1", "agent_status": "idle"}],
        )
        findings = pa.audit(data, empty_probe=lambda pane_id: pane_id == "p2")
        self.assertEqual([], [f for f in findings if f.action])
        self.assertIn(("orphan-pane", "p2"), kinds(findings))

    def test_agent_tui_pane_not_treated_as_empty(self):
        # pane 自带 agent 字段（未进 registry 的 TUI）→ 非空，不自动关
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane("p1", "t1"), pane("p2", "t1", agent_name="claude")],
            [agent("a1", "p1", tab_id="t1")],
        )
        findings = pa.audit(data, empty_probe=lambda _pane_id: True)
        self.assertEqual([], [f for f in findings if f.action])


def process_info(pgid: int | None, shell_pid: int | None,
                 processes: list[dict]) -> dict:
    """herdr `pane process-info` 真实结构包装（result.process_info）。"""
    info: dict = {"foreground_processes": processes}
    if pgid is not None:
        info["foreground_process_group_id"] = pgid
    if shell_pid is not None:
        info["shell_pid"] = shell_pid
    return {"result": {"process_info": info, "type": "pane_process_info"}}


def proc(pid: int, name: str, *argv: str) -> dict:
    return {"pid": pid, "name": name, "argv": list(argv),
            "cmdline": " ".join((name, *argv))}


class ProbeEmptyPaneRealStructureTest(unittest.TestCase):
    """X-1/S-1/S-4：真实 process-info 结构驱动 probe_empty_pane（含跑脚本的 shell）。"""

    def _probe(self, payload: dict) -> bool | None:
        with mock.patch.object(pa, "herdr_json", lambda _args: payload):
            return pa.probe_empty_pane("p1")

    def test_idle_interactive_zsh_is_empty(self):
        payload = process_info(100, 100, [proc(100, "zsh", "/usr/bin/zsh")])
        self.assertIs(True, self._probe(payload))

    def test_idle_bash_login_shell_is_empty(self):
        payload = process_info(100, 100, [proc(100, "bash", "bash", "-l")])
        self.assertIs(True, self._probe(payload))

    def test_bash_running_script_is_not_empty(self):
        payload = process_info(200, 100, [proc(200, "bash", "bash", "build.sh")])
        self.assertIs(False, self._probe(payload))

    def test_bash_wizard_waiting_read_is_not_empty(self):
        payload = process_info(200, 100, [proc(200, "bash", "bash", "wizard.sh")])
        self.assertIs(False, self._probe(payload))

    def test_bash_command_string_is_not_empty(self):
        payload = process_info(200, 100, [proc(200, "bash", "bash", "-lc", "make")])
        self.assertIs(False, self._probe(payload))

    def test_non_shell_foreground_is_not_empty(self):
        payload = process_info(300, 100, [proc(300, "yazi", "yazi")])
        self.assertIs(False, self._probe(payload))

    def test_non_shell_group_leader_with_shell_in_chain_is_not_empty(self):
        payload = process_info(
            300, 100,
            [proc(100, "sh", "sh", "-c", "vi notes.md"),
             proc(300, "yazi", "yazi")],
        )
        self.assertIs(False, self._probe(payload))

    def test_key_drift_returns_unknown(self):
        payload = {"result": {"process_info": {"foreground_procs": []}}}
        self.assertIsNone(self._probe(payload))

    def test_missing_argv_returns_unknown(self):
        payload = process_info(100, 100, [{"pid": 100, "name": "zsh"}])
        self.assertIsNone(self._probe(payload))

    def test_group_leader_absent_returns_unknown(self):
        payload = process_info(999, 100, [proc(100, "zsh", "zsh")])
        self.assertIsNone(self._probe(payload))


class FlowStatusParsingTest(unittest.TestCase):
    """X-2：Status 按 Phase 分段、非法值/缺失默认 pending、注释剥离。"""

    def test_all_complete_flow_is_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["complete", "complete"])
            self.assertEqual({"flow-a": True}, pa.load_flow_plans(root))

    def test_invalid_status_done_makes_flow_incomplete_and_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["complete"] * 9 + ["done"])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                flows = pa.load_flow_plans(root)
            self.assertEqual({"flow-a": False}, flows)
            self.assertIn("非法 Status", err.getvalue())
            self.assertIn("done", err.getvalue())

    def test_capitalized_status_or_missing_space_is_invalid(self):
        for status in ("Complete", "complete"):
            with self.subTest(status=status):
                with tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    write_flow_plan(root, "flow-a", ["complete"] * 9 + [status])
                    if status == "complete":
                        plan = root / ".planning/flow-a/task_plan.md"
                        plan.write_text(
                            plan.read_text(encoding="utf-8").replace(
                                "**Status:** complete", "**Status:**complete"),
                            encoding="utf-8")
                    flows = pa.load_flow_plans(root)
                    self.assertEqual({"flow-a": False}, flows)

    def test_phase_without_status_defaults_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["complete", "complete"])
            plan = root / ".planning/flow-a/task_plan.md"
            plan.write_text(plan.read_text(encoding="utf-8").replace(
                "**Status:** complete", "", 1), encoding="utf-8")
            self.assertEqual({"flow-a": False}, pa.load_flow_plans(root))

    def test_status_with_trailing_annotation_is_valid(self):
        """真实 plan 的 `**Status:** complete（说明…）` 仍按 complete（与 guard 同口径）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["complete（收口说明）", "complete"])
            self.assertEqual({"flow-a": True}, pa.load_flow_plans(root))

    def test_status_in_html_comment_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["pending"])
            plan = root / ".planning/flow-a/task_plan.md"
            plan.write_text(
                plan.read_text(encoding="utf-8")
                + "<!--\n**Status:** complete\n-->\n",
                encoding="utf-8")
            self.assertEqual({"flow-a": False}, pa.load_flow_plans(root))

    def test_status_outside_phase_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_flow_plan(root, "flow-a", ["complete"])
            plan = root / ".planning/flow-a/task_plan.md"
            plan.write_text(
                plan.read_text(encoding="utf-8")
                + "## Notes\n\n**Status:** done\n",
                encoding="utf-8")
            self.assertEqual({"flow-a": True}, pa.load_flow_plans(root))

    def test_no_phase_plan_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plan = root / ".planning/flow-a/task_plan.md"
            plan.parent.mkdir(parents=True)
            plan.write_text("# Task Plan\n\n**Status:** complete\n", encoding="utf-8")
            self.assertEqual({"flow-a": False}, pa.load_flow_plans(root))


class FlowReclaimApplyGuardTest(unittest.TestCase):
    """X-2 端到端：非法 Status 的活跃流不得被 --apply 关 tab。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, project: Path, data: dict) -> tuple[int, str, str, list[str]]:
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        closed: list[str] = []
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_tab", lambda tab_id: closed.append(tab_id) or True), \
                mock.patch.object(pa, "close_pane", lambda _pane_id: True), \
                mock.patch.object(pa, "probe_empty_pane", lambda _pane_id: None), \
                mock.patch.object(pa, "load_live_snapshot", lambda: data):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(["--apply", "--root", str(project)])
        return rc, out.getvalue(), err.getvalue(), closed

    def _fixture(self) -> dict:
        return snapshot(
            [tab("t1", "副舵·flow-a")],
            [pane("p1", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status="idle")],
        )

    def test_nine_complete_plus_done_not_closed(self):
        project = self.tmp / "proj"
        write_flow_plan(project, "flow-a", ["complete"] * 9 + ["done"])
        rc, _out, err, closed = self._run(project, self._fixture())
        self.assertEqual(0, rc, err)
        self.assertEqual([], closed, "非法 Status 的流不得自动关 tab")
        self.assertIn("非法 Status", err)

    def test_all_complete_still_closed(self):
        project = self.tmp / "proj"
        write_flow_plan(project, "flow-a", ["complete", "complete"])
        rc, out, err, closed = self._run(project, self._fixture())
        self.assertEqual(0, rc, err)
        self.assertEqual(["t1"], closed)
        self.assertIn("已关闭 tab t1", out)


class ApplyRealProcessFixtureTest(unittest.TestCase):
    """X-1 负向断言：真实 process-info 结构下，跑脚本的 shell 不被 --apply 关闭。"""

    def test_apply_does_not_close_shell_running_script(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "snapshot.json"
            data = snapshot(
                [tab("t1", "需求·任务")],
                [pane("p1", "t1"), pane("p2", "t1")],
                [agent("a1", "p1", tab_id="t1")],
            )
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            payload = process_info(
                200, 100, [proc(200, "bash", "bash", "build.sh")])
            closed: list[str] = []
            out = io.StringIO()
            with mock.patch.object(pa, "herdr_json", lambda _args: payload), \
                    mock.patch.object(pa, "close_pane",
                                      lambda pane_id: closed.append(pane_id) or True), \
                    mock.patch.object(pa, "close_tab", lambda _tab_id: True), \
                    mock.patch.object(pa, "load_live_snapshot", lambda: data):
                with contextlib.redirect_stdout(out):
                    rc = pa.main(["--apply"])
            self.assertEqual(0, rc)
            self.assertEqual([], closed, "跑脚本的 shell pane 不得被 --apply 关闭")
            self.assertNotIn("已关闭 pane p2", out.getvalue())
            self.assertIn("前台进程非空闲 shell", out.getvalue())


class ApplyReprobeTest(unittest.TestCase):
    """S-2：pane 关闭前复检一次，复检不通过则跳过关闭。"""

    def test_reprobe_mismatch_skips_close(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            path = tmp_path / "snapshot.json"
            data = snapshot(
                [tab("t1", "需求·任务")],
                [pane("p1", "t1"), pane("p2", "t1")],
                [agent("a1", "p1", tab_id="t1")],
            )
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            closed: list[str] = []
            calls: list[str] = []

            def flaky_probe(pane_id: str) -> bool | None:
                if pane_id != "p2":
                    return None
                calls.append(pane_id)
                return True if len(calls) == 1 else False

            out = io.StringIO()
            with mock.patch.object(pa, "probe_empty_pane", flaky_probe), \
                    mock.patch.object(pa, "close_pane",
                                      lambda pane_id: closed.append(pane_id) or True), \
                    mock.patch.object(pa, "close_tab", lambda _tab_id: True), \
                    mock.patch.object(pa, "load_live_snapshot", lambda: data):
                with contextlib.redirect_stdout(out):
                    rc = pa.main(["--apply"])
            self.assertEqual(0, rc)
            self.assertEqual([], closed, "复检不通过不得关闭 pane")
            self.assertEqual(2, len(calls), "audit 一次 + 关闭前复检一次")
            self.assertIn("[跳过] pane p2", out.getvalue())


def write_flow_plan(root: Path, flow: str, statuses: list[str]) -> Path:
    lines = [f"# Task Plan: {flow}", ""]
    for index, status in enumerate(statuses):
        lines.extend([f"### Phase {index}", f"**Status:** {status}", ""])
    path = root / ".planning" / flow / "task_plan.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


class T45FlowCompleteTest(unittest.TestCase):
    """T45②：流 plan 全 complete 且 agent 全 idle/done → 提议/关闭 tab（纯函数）。"""

    def _fixture(self, agent_statuses=("idle", "done"), focused: bool = False) -> dict:
        t1 = tab("t1", "副舵·flow-a")
        t1["focused"] = focused
        return snapshot(
            [t1],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status=agent_statuses[0]),
             agent("flow-a-firstmate", "p2", tab_id="t1", status=agent_statuses[1])],
        )

    def test_complete_flow_tab_actionable(self):
        findings = pa.audit(self._fixture(), flows={"flow-a": True})
        self.assertIn(("flow-complete-tab", "t1"), kinds(findings))
        finding = next(f for f in findings if f.kind == "flow-complete-tab")
        self.assertEqual("tab", finding.action)

    def test_incomplete_flow_not_flagged(self):
        self.assertEqual(
            [], pa.audit(self._fixture(), flows={"flow-a": False}))

    def test_working_or_blocked_agent_blocks_reclaim(self):
        for status in ("working", "blocked"):
            with self.subTest(status=status):
                data = self._fixture(agent_statuses=("idle", status))
                self.assertEqual([], pa.audit(data, flows={"flow-a": True}))

    def test_agent_without_matching_flow_not_flagged(self):
        data = snapshot(
            [tab("t1", "副舵·无计划")],
            [pane("p1", "t1")],
            [agent("unmatched-agent", "p1", tab_id="t1", status="idle")],
        )
        self.assertEqual([], pa.audit(data, flows={"flow-a": True}))

    def test_mixed_complete_and_incomplete_flows_not_flagged(self):
        data = snapshot(
            [tab("t1", "副舵·混合")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status="idle"),
             agent("flow-b-builder", "p2", tab_id="t1", status="idle")],
        )
        findings = pa.audit(data, flows={"flow-a": True, "flow-b": False})
        self.assertEqual([], [f for f in findings if f.kind == "flow-complete-tab"])

    def test_focused_tab_report_only(self):
        findings = pa.audit(self._fixture(focused=True), flows={"flow-a": True})
        finding = next(f for f in findings if f.kind == "flow-complete-tab")
        self.assertIsNone(finding.action)
        self.assertIn("聚焦", finding.detail)

    def test_non_empty_unknown_pane_blocks_reclaim(self):
        data = self._fixture()
        data["panes"].append(pane("p3", "t1", agent_name="claude"))
        findings = pa.audit(
            data, flows={"flow-a": True},
            empty_probe=lambda pane_id: pane_id != "p3")
        self.assertEqual([], [f for f in findings if f.kind == "flow-complete-tab"])

    def test_whitelisted_and_helm_tabs_never_reclaimed(self):
        for label in ("Captain·甲", "总舵"):
            with self.subTest(label=label):
                data = snapshot(
                    [tab("t1", label)],
                    [pane("p1", "t1")],
                    [agent("flow-a-builder", "p1", tab_id="t1", status="idle")],
                )
                findings = pa.audit(data, flows={"flow-a": True})
                self.assertEqual(
                    [], [f for f in findings if f.kind == "flow-complete-tab"])


class T45MainApplyTest(unittest.TestCase):
    """T45 CLI：--apply 关闭明确空 pane / 完成流 tab；dry-run 只报告。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def _run(self, data: dict, extra: list[str] | None = None,
             empty_panes: set[str] | None = None) -> tuple[int, str, str, dict]:
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        recorded = {"panes": [], "tabs": []}

        def fake_close_pane(pane_id: str) -> bool:
            recorded["panes"].append(pane_id)
            return True

        def fake_close_tab(tab_id: str) -> bool:
            recorded["tabs"].append(tab_id)
            return True

        def fake_probe(pane_id: str) -> bool | None:
            return pane_id in empty_panes if empty_panes is not None else None

        extra = list(extra or [])
        argv = ["--snapshot", str(path)] + extra
        if "--apply" in extra:
            # S-1：--apply 与 --snapshot 互斥；apply 用例改以 live snapshot 注入夹具
            argv = extra
            live_patch = mock.patch.object(pa, "load_live_snapshot", lambda: data)
            live_patch.start()
            self.addCleanup(live_patch.stop)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_pane", fake_close_pane), \
                mock.patch.object(pa, "close_tab", fake_close_tab), \
                mock.patch.object(pa, "probe_empty_pane", fake_probe):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(argv)
        return rc, out.getvalue(), err.getvalue(), recorded

    def _empty_pane_fixture(self) -> dict:
        return snapshot(
            [tab("t1", "需求·任务"), tab("t3", "总舵")],
            [pane("p1", "t1"), pane("p2", "t1"),
             pane("h1", "t3", status="idle"), pane("h2", "t3")],
            [agent("a1", "p1", tab_id="t1"),
             agent("helm-1", "h1", tab_id="t3", status="idle")],
        )

    def test_apply_closes_extra_empty_panes_keeps_agent_panes(self):
        rc, out, err, recorded = self._run(
            self._empty_pane_fixture(), ["--apply"], empty_panes={"p2", "h2"})
        self.assertEqual(rc, 0, err)
        self.assertEqual(["h2", "p2"], sorted(recorded["panes"]))
        self.assertEqual([], recorded["tabs"])
        self.assertIn("已关闭 pane p2", out)
        self.assertIn("已关闭 pane h2", out)

    def test_dry_run_reports_empty_pane_without_closing(self):
        rc, out, err, recorded = self._run(
            self._empty_pane_fixture(), empty_panes={"p2", "h2"})
        self.assertEqual(rc, 0, err)
        self.assertEqual({"panes": [], "tabs": []}, recorded)
        self.assertIn("[告警] 多余空 pane", out)
        self.assertIn("dry-run", out)

    def test_apply_closes_complete_flow_tab(self):
        project = self.tmp / "proj"
        write_flow_plan(project, "flow-a", ["complete", "complete"])
        data = snapshot(
            [tab("t1", "副舵·flow-a")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status="idle"),
             agent("flow-a-firstmate", "p2", tab_id="t1", status="done")],
        )
        rc, out, err, recorded = self._run(
            data, ["--apply", "--root", str(project)])
        self.assertEqual(rc, 0, err)
        self.assertEqual(["t1"], recorded["tabs"])
        self.assertIn("已关闭 tab t1", out)

    def test_incomplete_flow_tab_not_closed(self):
        project = self.tmp / "proj"
        write_flow_plan(project, "flow-a", ["complete", "pending"])
        data = snapshot(
            [tab("t1", "副舵·flow-a")],
            [pane("p1", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status="idle")],
        )
        rc, _out, err, recorded = self._run(
            data, ["--apply", "--root", str(project)])
        self.assertEqual(rc, 0, err)
        self.assertEqual([], recorded["tabs"])

    def test_blocked_agent_tab_not_closed(self):
        project = self.tmp / "proj"
        write_flow_plan(project, "flow-a", ["complete"])
        data = snapshot(
            [tab("t1", "副舵·flow-a")],
            [pane("p1", "t1")],
            [agent("flow-a-builder", "p1", tab_id="t1", status="blocked")],
        )
        rc, _out, err, recorded = self._run(
            data, ["--apply", "--root", str(project)])
        self.assertEqual(rc, 0, err)
        self.assertEqual([], recorded["tabs"])

    def test_whitelisted_tab_empty_pane_not_closed(self):
        data = snapshot(
            [tab("t1", "Captain·甲")],
            [pane("p1", "t1"), pane("p2", "t1")],
            [agent("a1", "p1", tab_id="t1")],
        )
        rc, _out, err, recorded = self._run(
            data, ["--apply", "--root", str(self.tmp)], empty_panes={"p2"})
        self.assertEqual(rc, 0, err)
        self.assertEqual({"panes": [], "tabs": []}, recorded)


class T45HelpTest(unittest.TestCase):
    """T45⑤：帮助文本写明自检默认 --apply、working 永不关、白名单跳过。"""

    def test_help_documents_self_check_contract(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                pa.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("自检", "--apply", "working", "白名单", "空 pane",
                      "完成流", "总舵 tab", "FB-038"):
            self.assertIn(token, text)

    def test_help_documents_background_job_sighup(self):
        """S12：固化「pane 内后台 job 在 --apply 关 pane 时仍可能收 SIGHUP」语义。"""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                pa.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("SIGHUP", "后台 job", "不保证"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
