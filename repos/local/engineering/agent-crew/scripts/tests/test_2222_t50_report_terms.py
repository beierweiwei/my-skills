#!/usr/bin/env python3
"""2.2.22 T50：汇报术语面向用户（首句业务术语）断言（先红后绿）。

覆盖工单 T50 验收断言 1–5：
① A5.4 含六要素：首句规则（UI/业务术语）、禁止清单、技术明细/证据段、
   正例、反例、自检；
② 7 角色 × 双栈 = 14 份模板的汇报/回执动作行含「首句」「业务术语」
   「技术明细」且指向 A5.4（并入现有行，不新增行）；
③ 模板行数/字节预算不破（crew-lint BUDGETS；role budget 测试在发布门复跑）；
④ 模板不复制 A5.4 全文，只写动作/指针；
⑤ crew-lint（含 purity）全绿。

grep 断言只落在 references/ 与 templates/ 生产目录。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ACCEPTANCE = "references/crew-acceptance.md"
RUNTIMES = ("qoder", "opencode")
ROLES = ("helm", "firstmate", "reviewer", "runner", "builder", "planner", "scout")
ROLE_FILES = tuple(f"templates/agents/{runtime}/{role}.md" for runtime in RUNTIMES for role in ROLES)
REPORT_KEYWORDS = ("汇报", "回执", "报告", "回报")
REQUIRED_TERMS = ("首句", "业务术语", "技术明细")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def a54_block() -> str:
    acceptance = read(ACCEPTANCE)
    start = acceptance.find("- **A5.4 ")
    if start < 0:
        raise AssertionError("crew-acceptance 缺少 A5.4")
    rest = acceptance[start:]
    end = rest.find("- **A5.5 ")
    if end < 0:
        raise AssertionError("A5.4 缺少终点 A5.5")
    return rest[:end]


class A54TermsTest(unittest.TestCase):
    """断言 1：A5.4 六要素齐全。"""

    def test_first_sentence_rule_and_ui_terms(self):
        block = a54_block()
        for phrase in (
            "首句",
            "现在能不能用、到哪一步",
            "UI/业务术语",
            "页面名",
            "按钮名",
            "状态名",
            "流程节点",
            "业务对象名",
        ):
            self.assertIn(phrase, block, f"A5.4 缺首句规则要素：{phrase}")

    def test_ban_list(self):
        block = a54_block()
        for phrase in (
            "禁止清单",
            "类/函数/脚本名",
            "commit hash",
            "日志",
            "表名",
            "接口路径",
            "内部编号",
            "T*/B-*/D-*",
            "角色黑话",
            "worker/adversary/guard/helm",
        ):
            self.assertIn(phrase, block, f"A5.4 禁止清单缺少：{phrase}")

    def test_technical_detail_and_evidence_section(self):
        block = a54_block()
        for phrase in ("技术明细", "证据", "默认不贴", "按需给", "一个入口或路径"):
            self.assertIn(phrase, block, f"A5.4 技术明细段缺少：{phrase}")

    def test_positive_and_negative_examples(self):
        block = a54_block()
        self.assertIn("正例", block, "A5.4 缺少正例")
        self.assertIn("反例", block, "A5.4 缺少反例")
        positive = block.split("正例", 1)[1].split("反例", 1)[0]
        self.assertRegex(positive, r"[\u4e00-\u9fff]{4,}", "正例需为业务语言句子")
        negative = block.split("反例", 1)[1]
        self.assertRegex(negative, r"[\u4e00-\u9fff]{4,}", "反例需为业务语言句子")

    def test_self_check_points_to_a57(self):
        block = a54_block()
        self.assertIn("自检", block, "A5.4 缺少自检")
        self.assertIn("A5.7", block, "A5.4 自检应指向 A5.7")


class RoleTemplateTermsTest(unittest.TestCase):
    """断言 2/4：14 模板汇报/回执行动作行含三词且只写动作/指针。"""

    def test_all_role_files_present(self):
        for rel in ROLE_FILES:
            self.assertTrue((REPO_ROOT / rel).is_file(), f"缺少模板：{rel}")

    def test_report_action_line_has_terms(self):
        for rel in ROLE_FILES:
            lines = [
                line for line in read(rel).splitlines()
                if all(term in line for term in REQUIRED_TERMS)
                and any(keyword in line for keyword in REPORT_KEYWORDS)
            ]
            with self.subTest(rel=rel):
                self.assertEqual(
                    1, len(lines), f"{rel} 应有且仅有一行汇报/回执动作同时含 {REQUIRED_TERMS}"
                )
                self.assertIn("A5.4", lines[0], f"{rel} 汇报行动作未指向 A5.4")

    def test_templates_do_not_copy_a54_body(self):
        for rel in ROLE_FILES:
            text = read(rel)
            with self.subTest(rel=rel):
                for phrase in ("禁止清单", "页面名", "按钮名", "角色黑话", "技术明细/证据段"):
                    self.assertNotIn(phrase, text, f"{rel} 复制了 A5.4 正文：{phrase}")


class LintGreenTest(unittest.TestCase):
    """断言 3/5：lint（含 BUDGETS 与 purity）全绿。"""

    def run_lint(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "scripts/crew-lint.py", *extra],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )

    def test_crew_lint_green(self):
        proc = self.run_lint()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("crew-lint: ok", proc.stdout)

    def test_crew_lint_purity_green(self):
        proc = self.run_lint("--purity")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("purity ok", proc.stdout)


if __name__ == "__main__":
    unittest.main()
