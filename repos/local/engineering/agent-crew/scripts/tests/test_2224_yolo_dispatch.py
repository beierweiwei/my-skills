#!/usr/bin/env python3
"""2.2.24 T59：YOLO 派单口径补齐（CASE req2-docalign-scout-1 缺参事故）。

静态断言（红→绿）：
- crew-rules D 节新增 D8 YOLO 派单（规则正文，不再只在 cheatsheet）。
- firstmate 双栈模板派单段含 YOLO 硬口径（qoder: bypass_permissions / opencode: --auto）。
- dispatch.py start 的 registry/log 记 permissionMode 字段。
- pane-audit 有 yolo-missing 检查：KIND_TEXT、纯函数判定、audit() 注入告警。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import unittest

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent
SKILL = _SCRIPTS.parent

_pa_spec = importlib.util.spec_from_file_location(
    "pane_audit_t59", _SCRIPTS / "pane-audit.py")
pane_audit = importlib.util.module_from_spec(_pa_spec)
sys.modules["pane_audit_t59"] = pane_audit
_pa_spec.loader.exec_module(pane_audit)


def read(rel: str) -> str:
    return (SKILL / rel).read_text(encoding="utf-8")


class YoloDocsStaticTest(unittest.TestCase):
    def test_rules_have_d8(self):
        rules = read("references/crew-rules.md")
        self.assertIn("**D8 YOLO 派单**", rules)
        self.assertIn("bypass_permissions", rules)
        self.assertIn("yolo-missing", rules)

    def test_firstmate_qoder_template_has_yolo(self):
        text = read("templates/agents/qoder/firstmate.md")
        self.assertIn("D8 YOLO 派单", text)
        self.assertIn("--permission-mode bypass_permissions", text)
        self.assertIn("scout/runner/planner", text)

    def test_firstmate_opencode_template_has_yolo(self):
        text = read("templates/agents/opencode/firstmate.md")
        self.assertIn("D8 YOLO 派单", text)
        self.assertIn("`--auto`", text)
        self.assertIn("scout/runner/planner", text)

    def test_dispatch_records_permission_mode(self):
        text = read("scripts/dispatch.py")
        self.assertIn('"permissionMode": (args.permission_mode or "").strip()',
                      text)


class YoloAuditTest(unittest.TestCase):
    def test_kind_text_registered(self):
        self.assertIn("yolo-missing", pane_audit.KIND_TEXT)

    def test_yolo_missing_entries_detection(self):
        entries = [
            ("ok-builder",
             "qoderclicn --agent crew-builder --permission-mode "
             "bypass_permissions --name ok-builder"),
            ("bad-scout",
             "qoderclicn --agent crew-scout --name bad-scout"),
            ("half-mode",
             "qoderclicn --agent crew-runner --permission-mode default "
             "--name half-mode"),
        ]
        missing = pane_audit.yolo_missing_entries(entries)
        self.assertEqual(
            [("bad-scout", entries[1][1]), ("half-mode", entries[2][1])],
            missing)

    def test_collect_never_raises(self):
        # 本机采集失败（无 /proc、pgrep 缺失）返回空表，不抛异常
        self.assertIsInstance(pane_audit.collect_qoder_cmdlines(), list)

    def test_audit_emits_yolo_missing_finding(self):
        snapshot = {"tabs": [], "panes": [], "agents": []}
        entries = [
            ("bad-scout", "qoderclicn --agent crew-scout --name bad-scout"),
        ]
        findings = pane_audit.audit(snapshot, yolo_entries=entries)
        hits = [f for f in findings if f.kind == "yolo-missing"]
        self.assertEqual(1, len(hits), findings)
        self.assertEqual("bad-scout", hits[0].target)
        self.assertIn("bypass_permissions", hits[0].next_step)
        self.assertIsNone(hits[0].action)

    def test_audit_clean_when_yolo_present(self):
        snapshot = {"tabs": [], "panes": [], "agents": []}
        entries = [
            ("ok-builder",
             "qoderclicn --permission-mode bypass_permissions "
             "--name ok-builder"),
        ]
        findings = pane_audit.audit(snapshot, yolo_entries=entries)
        self.assertEqual(
            [], [f for f in findings if f.kind == "yolo-missing"])


if __name__ == "__main__":
    unittest.main()
