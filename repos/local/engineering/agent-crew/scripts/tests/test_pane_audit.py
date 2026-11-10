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


def pane(pane_id: str, tab_id: str, status: str | None = None) -> dict:
    item = {"pane_id": pane_id, "tab_id": tab_id}
    if status:
        item["agent_status"] = status
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
             close_tab_ok: bool = True) -> tuple[int, str, str, dict]:
        path = self.tmp / "snapshot.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        recorded = {"panes": [], "tabs": []}

        def fake_close_pane(pane_id: str) -> bool:
            recorded["panes"].append(pane_id)
            return True

        def fake_close_tab(tab_id: str) -> bool:
            recorded["tabs"].append(tab_id)
            return close_tab_ok

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_pane", fake_close_pane), \
                mock.patch.object(pa, "close_tab", fake_close_tab):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(["--snapshot", str(path)] + (extra or []))
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

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(pa, "close_pane", fake_close_pane), \
                mock.patch.object(pa, "close_tab", fake_close_tab):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = pa.main(["--snapshot", str(path)] + (extra or []))
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
