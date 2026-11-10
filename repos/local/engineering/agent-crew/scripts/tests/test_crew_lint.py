#!/usr/bin/env python3
"""crew-lint.py 用例：python3 scripts/tests/test_crew_lint.py

覆盖四类检查：行数预算、规则编号引用（含区间端点/中间号）、裸指针、跨文档重复句；
以及全绿低输出与 SKILL frontmatter 版本解析。全部在临时目录构造最小技能树。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "crew-lint.py"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent

BUDGET_CASES = (
    ("templates/AGENTS-编排节.md", 151, 150),
    ("templates/agents/qoder/helm.md", 81, 80),
    ("templates/agents/qoder/builder.md", 51, 50),
    ("templates/agents/opencode/helm.md", 81, 80),
    ("templates/agents/opencode/firstmate.md", 81, 80),
    ("templates/agents/opencode/builder.md", 51, 50),
    ("templates/_templates/ticket.md", 16, 15),
)
ROLE_FILES = (
    "templates/agents/qoder/helm.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/qoder/builder.md",
    "templates/agents/qoder/planner.md",
    "templates/agents/qoder/reviewer.md",
    "templates/agents/qoder/runner.md",
    "templates/agents/qoder/scout.md",
    "templates/agents/opencode/helm.md",
    "templates/agents/opencode/firstmate.md",
    "templates/agents/opencode/builder.md",
    "templates/agents/opencode/planner.md",
    "templates/agents/opencode/reviewer.md",
    "templates/agents/opencode/runner.md",
    "templates/agents/opencode/scout.md",
)


def rules_text() -> str:
    blocks = []
    counts = {"D": 6, "R": 4, "W": 5, "O": 3, "F": 12, "P": 3, "G": 5, "S": 4, "X": 5}
    for prefix, count in counts.items():
        blocks.append(f"## {prefix} 组")
        blocks.extend(f"- **{prefix}{i} 规则{i}**：说明。" for i in range(1, count + 1))
        blocks.append("")
    return "\n".join(blocks) + "\n"


def acceptance_text() -> str:
    return "\n".join(f"## A{i} 规则{i}\n" for i in range(1, 6)) + "\n"


class CrewLintTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "skill"
        self.root.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    # ---- helpers ---------------------------------------------------------

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def make_skill(self) -> None:
        self.write("references/crew-rules.md", rules_text())
        self.write("references/crew-acceptance.md", acceptance_text())
        self.write(
            "templates/AGENTS-编排节.md",
            "# AGENTS\n\n规则见 {{crew_rules}}（D1–D6）与 {{crew_acceptance}}（A1–A5）。\n",
        )
        for rel in ROLE_FILES:
            self.write(rel, "# 角色\n\n规则：{{crew_rules}}（D1–D6）。\n")
        self.write(
            "templates/_templates/ticket.md",
            "# 工单\n\n## 目标\n目标。\n## 验收断言\n- 断言。\n"
            "## 边界规则\nD3–D6。\n## 回执要求\nR1–R4。\n",
        )
        self.write(
            "SKILL.md",
            "---\nname: fixture\ndescription: fixture\nversion: 9.9.9\n---\n\n# fixture\n",
        )

    def lint(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(self.root), *args],
            capture_output=True,
            text=True,
        )

    # ---- 全绿 / 版本 -----------------------------------------------------

    def test_fixture_clean(self):
        self.make_skill()
        proc = self.lint()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ok", proc.stdout)

    def test_repo_clean_and_low_output(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT)], cwd=REPO_ROOT, capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        self.assertEqual(len(lines), 1, proc.stdout)
        self.assertIn("crew-lint: ok", lines[0])

    def test_version_flag_parses_frontmatter(self):
        self.make_skill()
        fixture = self.lint("--version")
        self.assertEqual(fixture.returncode, 0)
        self.assertEqual(fixture.stdout.strip(), "9.9.9")

        repo = subprocess.run(
            [sys.executable, str(SCRIPT), "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(repo.returncode, 0, repo.stderr)
        self.assertEqual(repo.stdout.strip(), "2.2.5")

    # ---- 行数预算 --------------------------------------------------------

    def test_budget_violations(self):
        for rel, size, limit in BUDGET_CASES:
            with self.subTest(rel=rel):
                self._tmp.cleanup()
                self._tmp = tempfile.TemporaryDirectory()
                self.root = Path(self._tmp.name) / "skill"
                self.root.mkdir()
                self.make_skill()
                self.write(rel, "\n".join(f"填充 {i}" for i in range(size)) + "\n")
                proc = self.lint()
                self.assertEqual(proc.returncode, 1)
                self.assertIn(rel, proc.stdout)
                self.assertIn("budget", proc.stdout)
                self.assertIn(f"> {limit}", proc.stdout)

    # ---- 规则编号引用 ----------------------------------------------------

    def test_rule_reference_missing_endpoint(self):
        self.make_skill()
        self.write(
            "templates/AGENTS-编排节.md",
            "# AGENTS\n\n规则见 {{crew_rules}}（D1–D9）与 {{crew_acceptance}}（A1–A5）。\n",
        )
        proc = self.lint()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("D9 not defined", proc.stdout)

    def test_rule_reference_missing_interior(self):
        self.make_skill()
        self.write("references/crew-rules.md", rules_text().replace("- **D4 规则4**：说明。\n", ""))
        self.write(
            "templates/AGENTS-编排节.md",
            "# AGENTS\n\n规则见 {{crew_rules}}（D1–D6）。\n",
        )
        proc = self.lint()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("D4 not defined", proc.stdout)

    def test_acceptance_reference_missing(self):
        self.make_skill()
        self.write(
            "templates/AGENTS-编排节.md",
            "# AGENTS\n\n验收见 {{crew_acceptance}}（A1–A6）。\n",
        )
        proc = self.lint()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("A6 not defined", proc.stdout)

    # ---- 指针写法 --------------------------------------------------------

    def test_bare_pointer_rejected(self):
        self.make_skill()
        self.write("templates/agents/qoder/builder.md", "# 角色\n\n规则见 references/crew-rules.md。\n")
        proc = self.lint()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("pointer", proc.stdout)
        self.assertIn("templates/agents/qoder/builder.md", proc.stdout)

    # ---- 重复句 ----------------------------------------------------------

    def test_duplicate_sentence_across_documents(self):
        self.make_skill()
        sentence = "跨文档重复句子检查需要至少三十个字符才能被脚本报出来用于验证。"
        self.write("templates/AGENTS-编排节.md", f"# AGENTS\n\n{sentence}\n")
        self.write("templates/_templates/ticket.md", f"# 工单\n\n{sentence}\n")
        proc = self.lint()
        self.assertEqual(proc.returncode, 1)
        self.assertIn("duplicate", proc.stdout)
        self.assertIn("templates/AGENTS-编排节.md", proc.stdout)
        self.assertIn("templates/_templates/ticket.md", proc.stdout)

    def test_role_pair_duplicate_ignored(self):
        self.make_skill()
        sentence = "角色文件之间共用同构骨架允许重复不参与跨文档重复句比较。"
        self.write("templates/agents/qoder/builder.md", f"# 角色\n\n{sentence}\n")
        self.write("templates/agents/qoder/planner.md", f"# 角色\n\n{sentence}\n")
        proc = self.lint()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_duplicate_exclusions_headings_tables_and_code(self):
        self.make_skill()
        long_heading = "# 相同标题相同标题相同标题相同标题相同标题相同标题"
        table = "|---|---|"
        code_line = "这是代码块内的长句子不会参与跨文档重复检查因为围栏内容被跳过。"
        for rel in ("templates/AGENTS-编排节.md", "templates/_templates/ticket.md"):
            self.write(rel, f"{long_heading}\n{table}\n```\n{code_line}\n```\n")
        proc = self.lint()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    # ---- purity ----------------------------------------------------------

    def test_purity_clean_fixture(self):
        self.make_skill()
        proc = self.lint("--purity")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("purity ok", proc.stdout)

    def test_purity_absolute_path_rejected(self):
        self.make_skill()
        self.write("templates/project/topology.md", "# 拓扑\ndata: /home/alice/project/data\n")
        proc = self.lint("--purity")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("absolute path", proc.stdout)
        self.assertIn("templates/project/topology.md", proc.stdout)

    def test_purity_windows_drive_rejected(self):
        self.make_skill()
        self.write("references/extra.md", "# 示例\nC:\\Users\\alice\\project\\data\n")
        proc = self.lint("--purity")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("absolute path", proc.stdout)

    def test_purity_denylist_terms(self):
        self.make_skill()
        self.write("purity.denylist", "# 维护者本地词表\nAcmeCorp\n")
        self.write("templates/project/topology.md", "# 拓扑\n项目：acmecorp 内部系统\n")
        proc = self.lint("--purity")
        self.assertEqual(proc.returncode, 1)
        self.assertIn("forbidden term", proc.stdout)

    def test_purity_skips_tests_and_pycache(self):
        self.make_skill()
        self.write("scripts/tests/test_dummy.py", "HOME = '/home/alice/project'\n")
        self.write("scripts/__pycache__/x.py", "HOME = '/home/alice/project'\n")
        proc = self.lint("--purity")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_help_mentions_purity(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--purity", proc.stdout)

    # ---- 缺规则正文 ------------------------------------------------------

    def test_missing_rule_source(self):
        self.make_skill()
        (self.root / "references" / "crew-rules.md").unlink()
        proc = self.lint()
        self.assertEqual(proc.returncode, 3)
        self.assertIn("missing rule source", proc.stderr)


if __name__ == "__main__":
    unittest.main()
