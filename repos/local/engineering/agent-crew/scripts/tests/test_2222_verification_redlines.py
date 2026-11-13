#!/usr/bin/env python3
"""2.2.22 VR：verification-redlines.md 与验收口径接线断言（先红后绿）。

覆盖工单 VR-doc 验收断言 1–8：
① VR 文件存在、VR1–VR6 六块齐全、每块含「映射：」且引用 A/F/X 编号；
② 不含 CASE 特有词（3000 / QQ / req2. / SempClaw / FDE / thread workspace / UI 100%）；
③ 凭据正文唯一在 X4：VR4 不含 X4 原句（0600 / install -m 600 / 平台 env）；
   X4 含句级 pattern「仓库、证据与对话」与「平台（含平台数据目录）」消歧；
④ A3.4 三分法措辞与 plan 一致；A3.1–A3.3 文案不变；
⑤ A4/A5 只新增指针行，六维表与 A5 模板不动；
⑥ F6 只加指针、G6/R5 无改动、VR 不复述 A3/F6 全文、VR5 末句与 A3.4 一致；
⑦ SKILL.md 第 11 行「读哪里」含 verification-redlines.md；
⑧ crew-lint 与 --purity 全绿。

grep 断言只落在生产目录 references/ 与 SKILL.md，不扫 scripts/tests/ 反向断言。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VR_FILE = "references/verification-redlines.md"
RULES = "references/crew-rules.md"
ACCEPTANCE = "references/crew-acceptance.md"
SKILL = "SKILL.md"

CASE_TERMS = ("3000", "QQ", "req2.", "SempClaw", "FDE", "thread workspace", "UI 100%")
X4_BANNED_IN_VR4 = ("0600", "install -m 600", "平台 env")
X4_LOCATIONS = "仓库、证据与对话"
X4_PLATFORM = "平台（含平台数据目录）"
MAPPING_RE = re.compile(r"^映射：(?P<refs>.+)$", re.MULTILINE)
AFX_REF_RE = re.compile(r"(?<![A-Za-z0-9])(?:A\d+(?:\.\d+)?|F\d+(?:\.\d+)?|X\d+(?:\.\d+)?)(?![A-Za-z0-9])")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def line_with(text: str, prefix: str) -> str:
    line = next((ln for ln in text.splitlines() if ln.startswith(prefix)), None)
    if line is None:
        raise AssertionError(f"缺少行：{prefix}")
    return line


def section(text: str, start: str, end: str | None = "\n## ") -> str:
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"缺少章节：{start}")
    rest = text[begin + len(start):]
    if end is None:
        return rest
    tail = rest.find(end)
    if tail < 0:
        raise AssertionError(f"章节「{start}」缺少终点：{end}")
    return rest[:tail]


class VerificationRedlinesDocTest(unittest.TestCase):
    """断言 1–3：VR 文档结构、无项目特有词、凭据正文唯一在 X4。"""

    @classmethod
    def setUpClass(cls):
        path = REPO_ROOT / VR_FILE
        if not path.is_file():
            raise AssertionError(f"缺少文件：{VR_FILE}")
        cls.text = read(VR_FILE)

    def test_vr1_to_vr6_blocks_with_afx_mapping(self):
        for number in range(1, 7):
            heading = f"## VR{number} "
            self.assertIn(heading, self.text, f"缺少 {heading.strip()} 块")
        for number in range(1, 7):
            end = "\n## " if number < 6 else None
            chunk = section(self.text, f"## VR{number} ", end)
            mappings = MAPPING_RE.findall(chunk)
            self.assertEqual(1, len(mappings), f"VR{number} 应恰有一行「映射：」")
            self.assertTrue(
                AFX_REF_RE.search(mappings[0]),
                f"VR{number} 映射缺少 A/F/X 编号：{mappings[0]}",
            )

    def test_no_case_specific_terms(self):
        for term in CASE_TERMS:
            self.assertNotIn(term, self.text, f"VR 正文混入 CASE 特有词：{term}")

    def test_credential_rule_lives_only_in_x4(self):
        vr4 = section(self.text, "## VR4 ", "\n## ")
        self.assertIn("X4", vr4, "VR4 未指向 X4")
        for banned in X4_BANNED_IN_VR4:
            self.assertNotIn(banned, vr4, f"VR4 复制了 X4 原句：{banned}")
        x4 = line_with(read(RULES), "- **X4 ")
        self.assertIn(X4_LOCATIONS, x4, "X4 缺少句级禁止位置「仓库、证据与对话」")
        self.assertIn(X4_PLATFORM, x4, "X4 缺少「平台」消歧（含平台数据目录）")


class AcceptanceWiringTest(unittest.TestCase):
    """断言 4–5：A3.4 三分法落位、A3.1–A3.3 不变、A4/A5 只加指针。"""

    def test_a34_three_tier_wording(self):
        acceptance = read(ACCEPTANCE)
        a34 = line_with(acceptance, "- **A3.4 ")
        for phrase in (
            "证据层级与完成判定",
            "①面向最终用户的业务功能交付（bug 修复/功能开发/原型）",
            "②技能/规则/模板/提示词/文档类文本交付",
            "③调研/评审/发布维护类交付",
            "未列类型按最近似档判定并说明理由",
            "按 F6 分级",
            "证据层级（模拟/单点/全量用户路径，VR1）",
            "收口/完成/停止",
            "VR5",
        ):
            self.assertIn(phrase, a34, f"A3.4 缺少：{phrase}")

    def test_a31_to_a33_unchanged(self):
        acceptance = read(ACCEPTANCE)
        a31 = section(acceptance, "- **A3.1 ", "- **A3.2 ")
        for phrase in ("四要素", "目标入口可访问", "版本一致", "证据齐备", "未竟项列出"):
            self.assertIn(phrase, a31, f"A3.1 文案被改动，缺少：{phrase}")
        a32 = line_with(acceptance, "- **A3.2 ")
        for phrase in ("四要素", "抽验表", "doctor 结果", "验收门"):
            self.assertIn(phrase, a32, f"A3.2 文案被改动，缺少：{phrase}")
        a33 = line_with(acceptance, "- **A3.3 ")
        for phrase in ("关键决策", "对抗审查", "PASS", "D7"):
            self.assertIn(phrase, a33, f"A3.3 文案被改动，缺少：{phrase}")

    def test_no_internal_review_numbering_in_production_references(self):
        offenders = []
        for path in sorted((REPO_ROOT / "references").glob("*.md")):
            for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if re.search(r"S-[A-Z]?\d", line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
        self.assertEqual([], offenders, f"生产 references 含内部审查编号 S-*：{offenders}")

    def test_a4_a5_pointer_only(self):
        acceptance = read(ACCEPTANCE)
        a4 = section(acceptance, "## A4 ", "## A5 ")
        a5 = acceptance.split("## A5 ", 1)[1]
        for chunk, label in ((a4, "A4"), (a5, "A5")):
            hits = [ln for ln in chunk.splitlines() if "verification-redlines.md" in ln]
            self.assertEqual(1, len(hits), f"{label} 应只有一行 VR 指针")
            self.assertTrue(hits[0].lstrip().startswith("- **A"), "指针行应为编号条目")
        self.assertIn("| ① 目标驱动+结果验证 |", a4, "A4 六维映射表被改动")
        self.assertIn("A5.6 模板", acceptance, "A5 模板条目丢失")
        self.assertIn("证据：<一个入口或路径>", a5, "A5 模板内容被改动")


class RulesWiringTest(unittest.TestCase):
    """断言 6：F6 指针、G6/R5 无改动、防三角引用、VR5 与 A3.4 一致。"""

    def test_f6_pointer_without_rewriting(self):
        rules = read(RULES)
        f6 = line_with(rules, "- **F6 ")
        for phrase in ("prove-it-works", "行为/脚本改动", "grep/lint/schema 测试为主证据"):
            self.assertIn(phrase, f6, f"F6 原正文被改动，缺少：{phrase}")
        pointers = [
            ln for ln in rules.splitlines()
            if "verification-redlines.md" in ln and "VR1" in ln and "VR5" in ln
        ]
        self.assertEqual(1, len(pointers), "F6 缺少唯一的 VR1/VR5 指针行")

    def test_g6_and_r5_untouched(self):
        rules = read(RULES)
        g6 = line_with(rules, "- **G6 ")
        self.assertIn("口头决策落盘", g6)
        self.assertNotIn("verification-redlines.md", g6, "G6 不应新增 VR 指针")
        r5 = line_with(rules, "- **R5 ")
        self.assertIn("反馈分诊", r5)
        self.assertNotIn("verification-redlines.md", r5, "R5 不应新增 VR 指针")

    def test_vr_body_does_not_restate_rules(self):
        text = read(VR_FILE)
        for phrase in ("prove-it-works", "pstack 原名", "create-verification-skill"):
            self.assertNotIn(phrase, text, f"VR 正文复述 F6 全文：{phrase}")
        for phrase in ("目标入口可访问", "未竟项列出", "版本一致："):
            self.assertNotIn(phrase, text, f"VR 正文复述 A3.1 四要素：{phrase}")

    def test_vr5_completion_clause_matches_a34(self):
        vr5 = section(read(VR_FILE), "## VR5 ", "\n## ")
        self.assertIn("其他交付类型按 A3.4 分档判定", vr5, "VR5 末句未按 A3.4 分档")


class SkillEntryTest(unittest.TestCase):
    """断言 7：SKILL.md 第 11 行「读哪里」含 VR 指针。"""

    def test_read_where_line_points_to_vr(self):
        lines = read(SKILL).splitlines()
        self.assertGreaterEqual(len(lines), 11)
        self.assertIn("verification-redlines.md", lines[10], "SKILL.md 第 11 行缺少 VR 指针")
        read_where = next(ln for ln in lines if ln.startswith("**读哪里**"))
        self.assertIn("verification-redlines.md", read_where)


class LintGreenTest(unittest.TestCase):
    """断言 8：crew-lint 与 --purity 全绿。"""

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
