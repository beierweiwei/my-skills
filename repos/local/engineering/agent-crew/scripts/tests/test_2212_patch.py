#!/usr/bin/env python3
"""2.2.12 模板 slice 防回退断言（T24/T25、FB-019–022、G1/G3/G4）。

覆盖：四份 helm/firstmate 说人话三句与自检、两 firstmate 写权与证据落盘、
rework-reset 剧本结构与索引、notify/projectExt 规则文档一致性、G1/G3/G4 词表清理、
角色模板行数预算。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROLES = (
    "templates/agents/qoder/helm.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/helm.md",
    "templates/agents/opencode/firstmate.md",
)
FIRSTMATE = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
)
PLAYBOOK = "templates/playbooks/rework-reset.md"
PLAYBOOK_PHASES = (
    "停手与事实归集",
    "根因归类",
    "补验收",
    "重开需求门",
    "对抗审查",
    "切片重排",
    "验收门",
)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def load_crew_lint():
    path = REPO_ROOT / "scripts" / "crew-lint.py"
    spec = importlib.util.spec_from_file_location("crew_lint_2212", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PlainLanguageTest(unittest.TestCase):
    """A. T25/FB-021：四份模板统一说人话三句 + 你问再给 + 正反例 + 自检。"""

    def test_four_templates_have_three_sentence_contract(self):
        for rel in ROLES:
            with self.subTest(rel=rel):
                text = read(rel)
                for keyword in (
                    "- 结论：",
                    "- 进展：",
                    "- 需要你：",
                    "你问再给",
                    "正例",
                    "反例",
                    "发送前自检",
                ):
                    self.assertIn(keyword, text, f"{rel} 缺少关键词：{keyword}")
                self.assertIn("A5.6", text, f"{rel} 未保留 A5.6 验收/阶段呈报口径")

    def test_firstmate_receipt_is_two_layer(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                text = read(rel)
                self.assertIn("首句人话结论", text)
                self.assertIn("changed_files", text)


class FirstMateBoundaryTest(unittest.TestCase):
    """B. FB-019：portfolio 写权；C. FB-022：证据落盘后才写完成状态。"""

    def test_portfolio_write_right_clarified(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                text = read(rel)
                self.assertIn("portfolio 三文件", text)
                self.assertIn("由总舵写", text)
                self.assertIn("不写 portfolio 台账", text)

    def test_completion_requires_existing_evidence(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                text = read(rel)
                self.assertIn("非空且本轮生成", text)
                self.assertIn("F6/A1.3", text)
                self.assertIn("test -f", text)
                self.assertIn("假完成", text)

    def test_feedback_relay_to_helm(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                text = read(rel)
                self.assertIn("预分诊", text)
                self.assertIn("代录", text)


class ReworkResetTest(unittest.TestCase):
    """D. T24/FB-020：返工重置剧本、README 索引与选板触发。"""

    def test_playbook_structure(self):
        path = REPO_ROOT / PLAYBOOK
        self.assertTrue(path.is_file(), f"缺少 {PLAYBOOK}")
        text = path.read_text(encoding="utf-8")
        self.assertIn("## Phases", text)
        self.assertIn("## 适用与退出条件", text)
        self.assertIn("## 跳过规则", text)
        for keyword in PLAYBOOK_PHASES:
            self.assertIn(keyword, text, f"{PLAYBOOK} 缺少 Phase 关键词：{keyword}")
        self.assertEqual(text.count("- **Status:** pending"), 7)

    def test_readme_indexes_playbook(self):
        self.assertIn("rework-reset.md", read("templates/playbooks/README.md"))

    def test_firstmate_selects_rework_reset_on_third_round(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                text = read(rel)
                self.assertIn("rework-reset", text)
                self.assertIn("回执 ≥3 或同一功能 ≥3 轮返工", text)

    def test_playbook_skip_list_includes_slice_reorder(self):
        text = read(PLAYBOOK)
        self.assertIn(
            "不可跳过：停手归集、根因归类、补验收、重开需求门、切片重排、验收门",
            text,
        )

    def test_playbook_lint_passes(self):
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "scripts" / "plan-status.py"),
                "--playbooks",
                str(REPO_ROOT / "templates" / "playbooks"),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


class RulesDocConsistencyTest(unittest.TestCase):
    """G. 规则文档：notify 新模式与 projectExt 配置指针。"""

    def test_notify_new_mode_documented(self):
        text = read("references/crew-rules.md")
        self.assertIn("--workspace <id> --role helm", text)
        self.assertIn("最新活跃代际", text)
        self.assertIn("默认要求回执", text)

    def test_project_ext_pointer_notes_config(self):
        text = read("templates/AGENTS-编排节.md")
        self.assertIn("默认路径", text)
        self.assertIn("以配置 `paths.projectExt` 为准", text)


class CleanupTest(unittest.TestCase):
    """E. G1/G3/G4：范围词表零命中。"""

    def test_no_case_or_scan_terms_in_scope(self):
        for base in (REPO_ROOT / "references", REPO_ROOT / "templates" / "agents"):
            for path in sorted(base.rglob("*.md")):
                rel = path.relative_to(REPO_ROOT).as_posix()
                text = path.read_text(encoding="utf-8")
                with self.subTest(rel=rel):
                    self.assertNotIn("CASE", text)
                    self.assertNotIn("扫码", text)

    def test_no_customer_repo_name_in_shell_scripts(self):
        # AMEND：scripts/*.sh 生产脚本不得残留客户仓库名
        for path in sorted((REPO_ROOT / "scripts").glob("*.sh")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertNotIn("CASE", text)


class BudgetTest(unittest.TestCase):
    """F. 四份角色模板行数预算（复用 crew-lint BUDGETS 常量）。"""

    def test_role_templates_within_budget(self):
        budgets = dict(load_crew_lint().BUDGETS)
        for rel in ROLES:
            with self.subTest(rel=rel):
                self.assertIn(rel, budgets)
                lines = len(read(rel).splitlines())
                self.assertLessEqual(lines, budgets[rel], f"{rel} {lines} 行 > {budgets[rel]}")


if __name__ == "__main__":
    unittest.main()
