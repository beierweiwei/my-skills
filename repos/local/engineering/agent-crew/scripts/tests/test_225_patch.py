#!/usr/bin/env python3
"""2.2.5 防回退：F1 口径 / F4 提示 / NF-001/002/003 / 版本。

行为测试在 test_doctor_config.py / test_init_v2.py / test_pane_audit.py；
本文件承载文档与 grep 断言，防止修复被文案回退覆盖。
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / "SKILL.md"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
INIT = REPO_ROOT / "scripts" / "init.py"
DOCTOR = REPO_ROOT / "scripts" / "doctor.py"
HELM_QODER = REPO_ROOT / "templates" / "agents" / "qoder" / "helm.md"
HELM_OPENCODE = REPO_ROOT / "templates" / "agents" / "opencode" / "helm.md"
AUTONOMOUS = REPO_ROOT / "templates" / "playbooks" / "autonomous-run.md"
CHEATSHEET = REPO_ROOT / "references" / "herdr-cheatsheet.md"
TEST_CREW_LINT = REPO_ROOT / "scripts" / "tests" / "test_crew_lint.py"
TEST_220 = REPO_ROOT / "scripts" / "tests" / "test_220_final.py"

FORBIDDEN_RUNTIME = re.compile(
    r"herdr (?:agent (?:start|prompt|close|wait)|tab (?:create|close)|pane (?:split|close))\b"
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def changelog_section(version: str) -> str:
    text = read(CHANGELOG)
    # 版本号后带空白，避免 "2.2.1" 误命中 "## 2.2.10"
    marker = f"## {version} "
    start = text.index(marker)
    rest = text[start + len(marker):]
    match = re.search(r"^## ", rest, re.MULTILINE)
    return rest[:match.start()] if match else rest


def interrogate_skill() -> Path | None:
    env = os.environ.get("INTERROGATE_ROOT")
    if env:
        path = Path(env).expanduser() / "SKILL.md"
        return path if path.is_file() else None
    path = (REPO_ROOT.parents[5] / "my-skills" / "repos" / "local"
            / "engineering" / "interrogate" / "SKILL.md")
    return path if path.is_file() else None


class F1StrictRcWordingTest(unittest.TestCase):
    def test_changelog_221_documents_strict_semantics(self):
        section = changelog_section("2.2.1")
        self.assertIn("--strict", section)
        self.assertIn("rc=1", section)
        self.assertIn("默认 profile 不升级为失败", section)

    def test_strict_semantics_code_not_changed(self):
        # 只改文档口径：strict 既有语义仍在 doctor main 的退出码分支里
        text = read(DOCTOR)
        self.assertIn('elif args.strict and counts["warn"]:', text)
        self.assertIn("exit_code = 1", text)


class F4MissingSkillRootTest(unittest.TestCase):
    def test_missing_hint_guarded_before_worktree_attribution(self):
        text = read(INIT)
        guard = text.index("if not skill_root_path.is_dir():")
        missing = text.index("skillRoot 不存在或不可读")
        worktree = text.index("skillRoot 指向开发 worktree")
        self.assertLess(guard, missing)
        self.assertLess(missing, worktree)

    def test_missing_hint_has_actionable_next(self):
        text = read(INIT)
        self.assertIn("修正 .planning/orchestration.json 的 skillRoot", text)
        self.assertIn("指向实际存在的技能目录", text)


class Nf001HelmDispatchFileTest(unittest.TestCase):
    def test_both_helm_templates_require_file_first(self):
        for path in (HELM_QODER, HELM_OPENCODE):
            with self.subTest(template=path.name):
                text = read(path)
                self.assertIn("所有派单一律先落临时文件（不区分长短）", text)
                self.assertIn('"$(cat <file>)"', text)
                self.assertNotIn("长 prompt/含反引号等特殊字符的派单", text)
                self.assertLessEqual(len(text.splitlines()), 80)


class Nf002InterrogateCoverageTest(unittest.TestCase):
    def test_interrogate_timeout_and_coverage_gap(self):
        path = interrogate_skill()
        if path is None:
            self.skipTest("interrogate 不在本机旁路（可设 INTERROGATE_ROOT）")
        text = read(path)
        self.assertIn("timeout window", text)
        self.assertIn("coverage gap", text)
        self.assertIn("Coverage gaps", text)
        self.assertIn("Do not end the turn while waiting", text)

    def test_autonomous_phase2_checklist(self):
        text = read(AUTONOMOUS)
        self.assertIn("### Phase 2:", text)
        phase2 = text.split("### Phase 2:", 1)[1].split("### Phase 3:", 1)[0]
        self.assertIn("不结束回合", phase2)
        self.assertIn("覆盖缺口", phase2)


class Nf003CheatsheetDispatchTest(unittest.TestCase):
    def test_no_manual_lifecycle_examples(self):
        hits = FORBIDDEN_RUNTIME.findall(read(CHEATSHEET))
        self.assertEqual(hits, [])

    def test_dispatch_examples_and_readonly_commands_present(self):
        text = read(CHEATSHEET)
        self.assertIn("dispatch.py prompt", text)
        self.assertIn("dispatch.py tab create", text)
        for token in ("herdr agent list", "herdr agent get", "herdr agent read",
                      "herdr tab list", "herdr pane list", "send-keys", "仅人工恢复"):
            self.assertIn(token, text)


class Version225Test(unittest.TestCase):
    def test_version_synced(self):
        self.assertIn("version: 2.2.30", read(SKILL_MD))
        self.assertIn("v2.2.30", read(SKILL_MD))
        self.assertIn('SKILL_VERSION_FALLBACK = "2.2.30"', read(INIT))
        self.assertIn("## 2.2.30", read(CHANGELOG))
        self.assertIn('"2.2.30"', read(TEST_CREW_LINT))
        self.assertIn("2.2.30", read(TEST_220))


if __name__ == "__main__":
    unittest.main()
