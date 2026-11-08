#!/usr/bin/env python3
"""pane-audit.py crew-v2 1.6 用例：巡检告警 / dry-run / --apply 安全关闭。

覆盖：
① 单 tab >3 pane、pane 无 registry、registry 无 pane、空 tab 各自告警；
② 白名单（Captain·* / 流程优化·* / 总舵 tab / helm agent / --helm-pane / --whitelist-prefix）跳过；
③ dry-run 不关闭任何 pane/tab；--apply 只关白名单外的空 tab，working 与孤儿 pane 不动；
④ 输出含可执行下一步；CLI 自解释；fixture JSON 注入，不调用真实 herdr。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
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


class AuditRulesTest(unittest.TestCase):
    """契约①②：四类告警与白名单口径（纯函数）。"""

    def test_tab_over_three_panes_warned(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane(f"p{i}", "t1") for i in range(4)],
            [agent(f"a{i}", f"p{i}") for i in range(4)],
        )
        self.assertEqual(kinds(pa.audit(data)), [("tab-overflow", "t1")])

    def test_three_panes_is_within_limit(self):
        data = snapshot(
            [tab("t1", "需求·任务")],
            [pane(f"p{i}", "t1") for i in range(3)],
            [agent(f"a{i}", f"p{i}") for i in range(3)],
        )
        self.assertEqual(pa.audit(data), [])

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
            [pane(f"p{i}", "t1") for i in range(4)]
            + [pane(f"q{i}", "t2") for i in range(4)]
            + [pane(f"r{i}", "t3") for i in range(4)]
            + [pane("z1", "t4")],
            [],
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
                tab("t1", "需求·任务"),   # 4 pane → 超限（仅报告）
                tab("t2", "杂活"),        # 空 tab → 安全关闭
                tab("t3", "Captain·甲"),  # 白名单空 tab → 不关
                tab("t4", "普通"),        # 孤儿 pane → 仅报告
            ],
            [pane(f"p{i}", "t1", status="working") for i in range(1, 5)]
            + [pane("p9", "t4")],
            [agent(f"a{i}", f"p{i}") for i in range(1, 5)],
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


class CliTest(unittest.TestCase):
    def test_help_is_self_explanatory(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                pa.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        text = out.getvalue()
        for token in ("--snapshot", "--apply", "--whitelist-prefix", "--helm-pane",
                      "白名单", "working", "dry-run"):
            self.assertIn(token, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
