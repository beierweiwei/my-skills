#!/usr/bin/env python3
"""2.2.4 自学习闭环：R5 / 复盘路由 / feedback 表结构 / inone 边界 / 骨架 / 版本。"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CREW_RULES = REPO_ROOT / "references" / "crew-rules.md"
PLAN_DISCIPLINE = REPO_ROOT / "references" / "plan-discipline.md"
GUIDE = REPO_ROOT / "references" / "orchestration-guide.md"
HELM_QODER = REPO_ROOT / "templates" / "agents" / "qoder" / "helm.md"
HELM_OPENCODE = REPO_ROOT / "templates" / "agents" / "opencode" / "helm.md"
FIRSTMATE_QODER = REPO_ROOT / "templates" / "agents" / "qoder" / "firstmate.md"
FIRSTMATE_OPENCODE = REPO_ROOT / "templates" / "agents" / "opencode" / "firstmate.md"
AGENTS_SECTION = REPO_ROOT / "templates" / "AGENTS-编排节.md"
DISPATCH_SOP = REPO_ROOT / "templates" / "skills" / "firstmate-dispatch" / "SKILL.md"
RETRO_TPL = REPO_ROOT / "templates" / "_templates" / "retro.md"
FEEDBACK_TPL = REPO_ROOT / "templates" / "project" / "portfolio-feedback.md"
INIT = REPO_ROOT / "scripts" / "init.py"
SKILL_MD = REPO_ROOT / "SKILL.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
OPENCODE_REVIEWER = REPO_ROOT / "templates" / "agents" / "opencode" / "reviewer.md"
WORKERS = tuple(
    REPO_ROOT / "templates" / "agents" / runtime / f"{role}.md"
    for runtime in ("qoder", "opencode")
    for role in ("builder", "planner", "reviewer", "runner", "scout")
)
FEEDBACK_HEADER = (
    "| ID | 场景/问题 | 影响 | 建议（文件+条目） | 预判去向 | 上报者 | 日期 | 状态 | 去向 |"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class RuleLayerTest(unittest.TestCase):
    def test_r5_defined_and_indexed(self):
        rules = read(CREW_RULES)
        self.assertIn("**R5 反馈分诊**", rules)
        self.assertIn("| R | 回执与反馈 | R1–R6 |", rules)
        for phrase in ("阻断级", "Errors Encountered", "inone", "预判去向", "待复盘未清零"):
            self.assertIn(phrase, rules)

    def test_guide_pointers_updated(self):
        guide = read(GUIDE)
        self.assertIn("**R1–R6**", guide)
        self.assertIn("（R1–R6）", guide)
        self.assertNotIn("R1–R4", guide)


class TemplateLayerTest(unittest.TestCase):
    def test_helm_templates_have_triage_and_retro(self):
        qoder = read(HELM_QODER)
        self.assertIn("R1–R6 回执与反馈", qoder)
        self.assertIn("\n8. 自学习分诊（R5）", qoder)
        self.assertIn("\n9. 复盘：大收口必跑", qoder)
        self.assertIn("\n10. 每轮开工先检查", qoder)
        for phrase in ("版本发布/升级", "验收门或里程碑签认", "≥3 条流或 ≥5 切片",
                       "最高优先级阻断解除", "四路归类", "retro-<YYYYMMDD>.md",
                       "INONE_AGENT_NAME=helm", "信箱只进不出"):
            self.assertIn(phrase, qoder)
        opencode = read(HELM_OPENCODE)
        self.assertIn("R1–R6 回执与反馈", opencode)
        self.assertIn("\n13. 自学习分诊与复盘（R5）", opencode)
        for phrase in ("四路归类", "retro-<YYYYMMDD>.md", "INONE_AGENT_NAME=helm",
                       "预判去向", "单 agent 读 feedback", "收口 gap",
                       "转_correct", "归档+原因", "待复盘未清零=未完成"):
            self.assertIn(phrase, opencode)

    def test_firstmate_templates_have_inone_boundary(self):
        for path in (FIRSTMATE_QODER, FIRSTMATE_OPENCODE):
            with self.subTest(path=path.name):
                text = read(path)
                self.assertIn("R1–R6", text)
                self.assertIn("INONE_AGENT_NAME=firstmate", text)
                self.assertIn("船员只报不推", text)
                self.assertIn("auto-memory", text)

    def test_workers_reference_r5_without_inone(self):
        for path in WORKERS:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = read(path)
                rule_line = next(
                    line for line in text.splitlines() if line.startswith("- 规则："))
                self.assertIn("R1–R6", rule_line)
                self.assertNotIn("R1–R4", rule_line)
                self.assertNotIn("inone", text)

    def test_opencode_reviewer_receipt_sentence_unchanged(self):
        self.assertIn("结论写回执（R1–R4）", read(OPENCODE_REVIEWER))

    def test_agents_section_feedback_and_retro(self):
        text = read(AGENTS_SECTION)
        for phrase in ("R 回执与反馈", "预判去向", "retro-<YYYYMMDD>.md",
                       "待复盘未清零", "inone", "auto-memory"):
            self.assertIn(phrase, text)

    def test_dispatch_sop_triage(self):
        text = read(DISPATCH_SOP)
        for phrase in ("INONE_AGENT_NAME=firstmate", "预判去向", "（R5）", "Errors"):
            self.assertIn(phrase, text)

    def test_plan_discipline_feedback_schema(self):
        text = read(PLAN_DISCIPLINE)
        self.assertIn(FEEDBACK_HEADER, text)
        for phrase in ("待复盘", "已批实施中", "已闭环", "已转出", "已归档",
                       "预判去向", "去向五值", "已批 →", "转_correct（待 T12）",
                       "已_inone #", "归档（原因）", "Bug 表冻结",
                       "retro-YYYYMMDD.md", "_archive"):
            self.assertIn(phrase, text)

    def test_report_and_feedback_skeletons(self):
        retro = read(RETRO_TPL)
        for phrase in ("# 复盘 <YYYY-MM-DD>", "- 触发：", "- 窗口：", "- 输入：",
                       "- 结论：", "## 四路归类", "## 闭环检查", "## 待 Captain"):
            self.assertIn(phrase, retro)
        feedback = read(FEEDBACK_TPL)
        self.assertIn("## 流程反馈（复盘信箱）", feedback)
        self.assertIn(FEEDBACK_HEADER, feedback)
        for phrase in ("FB-001", "2026-11-10 冻结", "去向五值",
                       "## 历史叙事（只读）"):
            self.assertIn(phrase, feedback)


class VersionTest(unittest.TestCase):
    def test_version_225_in_three_places(self):
        self.assertIn("version: 2.2.15", read(SKILL_MD))
        self.assertIn("v2.2.15", read(SKILL_MD))
        self.assertIn('SKILL_VERSION_FALLBACK = "2.2.15"', read(INIT))
        self.assertIn("## 2.2.15", read(CHANGELOG))


if __name__ == "__main__":
    unittest.main(verbosity=2)
