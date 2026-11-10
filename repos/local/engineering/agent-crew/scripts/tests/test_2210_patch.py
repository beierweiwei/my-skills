#!/usr/bin/env python3
"""2.2.10 T20 B/E：R6 处置动作映射与规则编号防回退。

文档断言：`crew-rules.md` R6 把 blocked 处置动作（re-task/close/escalate）
映射到合法回执 `status`（done|blocked|captain）；`ticket.md` 回执要求引用
R1–R6 并含映射要点；`references/*.md` 与 `templates/**/*.md` 无 F1–F12 残留，
guide 与两 helm 模板引用 F1–F16。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CREW_RULES = REPO_ROOT / "references" / "crew-rules.md"
GUIDE = REPO_ROOT / "references" / "orchestration-guide.md"
TICKET_TPL = REPO_ROOT / "templates" / "_templates" / "ticket.md"
HELM_QODER = REPO_ROOT / "templates" / "agents" / "qoder" / "helm.md"
HELM_OPENCODE = REPO_ROOT / "templates" / "agents" / "opencode" / "helm.md"
CREW_GUARD = REPO_ROOT / "scripts" / "crew-guard.py"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def r6_line() -> str:
    rules = read(CREW_RULES)
    return next(line for line in rules.splitlines() if line.startswith("- **R6 "))


class R6DispositionMappingTest(unittest.TestCase):
    def test_disposition_is_action_not_receipt_status(self):
        line = r6_line()
        self.assertIn("处置动作", line)
        self.assertIn("不是回执 `status`", line)

    def test_re_task_and_close_map_to_done(self):
        line = r6_line()
        self.assertIn("`re-task` → `status=done`", line)
        self.assertIn("已转出：<新工单/工位/接手人>", line)
        self.assertIn("`close` → `status=done`", line)
        self.assertIn("已闭环：<原因/动作>", line)

    def test_escalate_maps_to_captain(self):
        line = r6_line()
        self.assertIn("`escalate` → `status=captain`", line)
        self.assertIn("待 Captain：<决策项>", line)

    def test_unresolved_stays_blocked(self):
        line = r6_line()
        self.assertIn("未处置保持 `status=blocked`", line)
        self.assertIn("不得用 `done`/`captain` 掩盖", line)


class TicketTemplateTest(unittest.TestCase):
    def test_receipt_section_references_r1_r6_and_mapping(self):
        text = read(TICKET_TPL)
        self.assertIn("按 R1–R6", text)
        for phrase in (
            "re-task",
            "close",
            "escalate",
            "已转出",
            "已闭环",
            "待 Captain",
            "保持 `blocked`",
        ):
            self.assertIn(phrase, text)
        self.assertNotIn("R1–R4", text)


class RuleNumberRegressionTest(unittest.TestCase):
    def test_no_stale_f1_f12_in_references_and_templates(self):
        paths = sorted((REPO_ROOT / "references").glob("*.md"))
        paths += sorted((REPO_ROOT / "templates").rglob("*.md"))
        self.assertTrue(paths)
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in paths
            if "F1–F12" in read(path)
        ]
        self.assertEqual([], offenders)

    def test_guide_and_helm_templates_reference_f1_f16(self):
        for path in (GUIDE, HELM_QODER, HELM_OPENCODE):
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertIn("F1–F16", read(path))


class Fb008CtxThresholdRegressionTest(unittest.TestCase):
    """FB-008 AMEND1：guard 交接阈值 0.50 与文案防回退（29/30/49/50/51 由 guard 测试覆盖）。"""

    def test_references_and_templates_have_no_60_handoff_wording(self):
        paths = sorted((REPO_ROOT / "references").glob("*.md"))
        paths += sorted((REPO_ROOT / "templates").rglob("*.md"))
        self.assertTrue(paths)
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in paths
            if "30% 预警 / 60% 交接" in read(path)
            or "ctxHandoff` 默认 0.60" in read(path)
        ]
        self.assertEqual([], offenders)

    def test_helm_templates_declare_30_warn_50_handoff(self):
        for path in (HELM_QODER, HELM_OPENCODE):
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = read(path)
                self.assertIn("30% 预警 / 50% 交接", text)
                self.assertIn("`helm.ctxHandoff` 默认 0.50", text)

    def test_helm_templates_use_only_50_immediate_handoff(self):
        """D1：helm 自身上下文行只写 ≥50% 立即交接；worker 级 ≥60% 立即停不在此断言。"""
        for path in (HELM_QODER, HELM_OPENCODE):
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = read(path)
                self.assertNotIn("≥60% 立即交接", text)
                self.assertIn("≥50% 立即交接", text)

    def test_guard_default_thresholds_and_help(self):
        text = read(CREW_GUARD)
        self.assertIn("DEFAULT_CTX_WARN = 0.30", text)
        self.assertIn("DEFAULT_CTX_HANDOFF = 0.50", text)
        self.assertNotIn("DEFAULT_CTX_HANDOFF = 0.60", text)
        self.assertIn("ctxHandoff（默认 50%）", text)
        self.assertNotIn("ctxHandoff（默认 60%）", text)


if __name__ == "__main__":
    unittest.main()
