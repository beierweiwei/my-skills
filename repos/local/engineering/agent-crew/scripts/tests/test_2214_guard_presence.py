#!/usr/bin/env python3
"""2.2.14 清单「必核 guard 存在」防回退断言（FB-028）。

覆盖两类落点：
- 技能源模板/指南（本轮随技能分发）：release 剧本、orchestration-guide §5.11、
  AGENTS 编排节「安装 / 升级 / 体检」、qoder/opencode 的 helm「自动交接」与
  firstmate「上下文与工位」、session-pickup。
- 项目侧（本仓开发副本布局下才存在）：`docs/crew/使用指南-新增技能与编排.md` 与
  `.qoder/skills/verify-crew/features/doctor-guard-presence.md`。

防回退口径：不只断言文件存在，逐文件断言「必核 guard 存在」与 `runtime.guard`
出现在正确章节/行，且关键语义（pass 带 pid、warn/fail 重启、不得报收口）仍在。
`skill-src` 被独立分发/安装时没有项目侧文件，项目侧断言整体 skip；发布 2.2.14 时
本文件的 CHANGELOG 断言随版本断言测试一并更新。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PHRASE = "必核 guard 存在"
COMMAND = "runtime.guard"

SKILL_CHECKLIST_FILES = (
    "templates/playbooks/release.md",
    "references/orchestration-guide.md",
    "templates/AGENTS-编排节.md",
    "templates/agents/qoder/helm.md",
    "templates/agents/opencode/helm.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
    "templates/playbooks/session-pickup.md",
)
HELM_FILES = (
    "templates/agents/qoder/helm.md",
    "templates/agents/opencode/helm.md",
)
FIRSTMATE_FILES = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
)
PROJECT_GUIDE = "docs/crew/使用指南-新增技能与编排.md"
VERIFY_FEATURE = ".qoder/skills/verify-crew/features/doctor-guard-presence.md"
VERIFY_README = ".qoder/skills/verify-crew/features/README.md"
FEATURE_HEADINGS = (
    "## Sub-features",
    "## How to get to it (user POV)",
    "## Driving it with the python fixture harness",
    "## Gotchas",
)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def section(text: str, start: str, end: str) -> str:
    """取 [start, end) 之间的正文；缺 start/end 直接失败，避免静默空段。"""
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"缺少章节起点：{start}")
    tail = text.find(end, begin + len(start))
    if tail < 0:
        raise AssertionError(f"缺少章节终点：{end}")
    return text[begin:tail]


def lines_with(text: str, phrase: str) -> list[str]:
    return [line for line in text.splitlines() if phrase in line]


def project_root() -> Path | None:
    """定位本仓项目根；独立分发/安装布局返回 None（项目侧断言整体 skip）。"""
    override = os.environ.get("CREW_PROJECT_ROOT")
    if override:
        return Path(override)
    candidate = REPO_ROOT.parents[4]
    if (candidate / "AGENTS.md").is_file() and (candidate / ".planning").is_dir():
        return candidate
    return None


PROJECT_ROOT = project_root()


class SkillChecklistTest(unittest.TestCase):
    """技能源清单入口：同一短语与同一可执行判据。"""

    def test_each_entry_has_phrase_and_runtime_guard(self):
        for rel in SKILL_CHECKLIST_FILES:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn(PHRASE, text, f"{rel} 缺少：{PHRASE}")
                self.assertIn(COMMAND, text, f"{rel} 缺少：{COMMAND}")

    def test_release_phase6_requires_pass_with_pid_and_blocks_closure(self):
        text = read("templates/playbooks/release.md")
        chunk = section(text, "### Phase 6:", "\n### Phase 7:")
        for phrase in (
            PHRASE,
            "python3 scripts/doctor.py --only runtime.guard",
            "status=pass 且 detail 带 pid",
            "crew-guard.py --helm <当前舵> --interval 120",
            "不得报发布收口",
        ):
            self.assertIn(phrase, chunk, f"release Phase 6 缺少：{phrase}")
        exit_line = next(
            line for line in text.splitlines() if line.startswith("- 退出：")
        )
        self.assertIn("guard 存在核验", exit_line)
        self.assertIn("pass 且 detail 带 pid", exit_line)

    def test_guide_section_5_11_has_verify_step_and_redline(self):
        chunk = section(read("references/orchestration-guide.md"), "### 5.11", "\n## 6.")
        for phrase in (
            PHRASE,
            "python3 scripts/doctor.py --only runtime.guard",
            "status=pass 且 detail 带 pid",
            "不得报发布收口",
        ):
            self.assertIn(phrase, chunk, f"§5.11 缺少：{phrase}")

    def test_agents_template_upgrade_line_command(self):
        lines = lines_with(read("templates/AGENTS-编排节.md"), PHRASE)
        self.assertTrue(lines, "AGENTS 编排节缺少必核行")
        for line in lines:
            with self.subTest(line=line):
                self.assertIn("升级", line)
                self.assertIn("换舵", line)
                self.assertIn("runtime.guard", line)
                self.assertIn("crew-guard.py --helm", line)

    def test_helm_templates_handover_checks_guard(self):
        for rel in HELM_FILES:
            lines = lines_with(read(rel), PHRASE)
            self.assertTrue(lines, f"{rel} 缺少必核行")
            for line in lines:
                with self.subTest(rel=rel):
                    self.assertIn("交接", line)
                    self.assertIn("runtime.guard", line)
                    self.assertIn("crew-guard.py --helm", line)

    def test_firstmate_templates_rebuild_checks_guard(self):
        for rel in FIRSTMATE_FILES:
            lines = lines_with(read(rel), PHRASE)
            self.assertTrue(lines, f"{rel} 缺少必核行")
            for line in lines:
                with self.subTest(rel=rel):
                    self.assertIn("换舵", line)
                    self.assertIn("重建", line)
                    self.assertIn("runtime.guard", line)
                    self.assertIn("crew-guard.py --helm", line)

    def test_session_pickup_rebuild_phase_checks_guard(self):
        text = read("templates/playbooks/session-pickup.md")
        chunk = section(text, "### Phase 2:", "\n### Phase 3:")
        for phrase in (PHRASE, "runtime.guard", "crew-guard.py --helm"):
            self.assertIn(phrase, chunk, f"session-pickup Phase 2 缺少：{phrase}")

    def test_playbooks_lint_green(self):
        proc = subprocess.run(
            [sys.executable, "scripts/plan-status.py", "--playbooks", "templates/playbooks"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertRegex(proc.stdout, r"剧本 lint ok：\d+ 个剧本")


class ChangelogVersionSectionTest(unittest.TestCase):
    """发布 2.2.20：CHANGELOG 顶部含 2.2.20 发布段，2.2.17 基线段保留。"""

    def test_changelog_has_2218_section(self):
        text = read("CHANGELOG.md")
        top = re.search(r"^## (2\.2\.\d+)(?:\s|$)", text, flags=re.MULTILINE)
        self.assertIsNotNone(top, "CHANGELOG 缺少发布段")
        self.assertEqual("2.2.20", top.group(1), "CHANGELOG 顶部发布段应为 2.2.20")
        self.assertIsNotNone(
            re.search(r"^## 2\.2\.17(?:\s|$)", text, flags=re.MULTILINE),
            "CHANGELOG 缺少 2.2.17 基线段",
        )


@unittest.skipIf(
    PROJECT_ROOT is None,
    "skill 独立分发/安装布局：无项目侧文件，跳过项目清单断言",
)
class ProjectChecklistTest(unittest.TestCase):
    """项目侧使用指南与 verify-crew feature 同口径。"""

    def read_project(self, rel: str) -> str:
        path = PROJECT_ROOT / rel
        self.assertTrue(path.is_file(), f"缺少项目侧文件：{path}")
        return path.read_text(encoding="utf-8")

    def test_user_guide_mentions_guard_presence(self):
        text = self.read_project(PROJECT_GUIDE)
        for phrase in (PHRASE, "runtime.guard", "crew-guard.py --helm"):
            self.assertIn(phrase, text, f"使用指南缺少：{phrase}")
        self.assertIn("值守叫醒", text)
        self.assertIn("release", text)

    def test_verify_feature_matches_four_h2_contract(self):
        text = self.read_project(VERIFY_FEATURE)
        lines = text.splitlines()
        self.assertTrue(lines and lines[0].startswith("# "), "feature 缺 H1 标题")
        headings = [line for line in lines if line.startswith("## ")]
        self.assertEqual(list(FEATURE_HEADINGS), headings, "feature H2 顺序/名称不符契约")

    def test_verify_feature_recipes_cover_required_states(self):
        text = self.read_project(VERIFY_FEATURE)
        for phrase in (
            PHRASE,
            "runtime.guard",
            "无锁",
            "死 pid",
            "活 pid",
            "pass",
            "detail",
            "--strict",
            "rc=1",
            "/tmp",
            "scripts/doctor.py",
            "技能源",
            "upgrade",
            "旧证据不算数",
        ):
            self.assertIn(phrase, text, f"verify feature 缺少：{phrase}")

    def test_verify_readme_indexes_feature(self):
        text = self.read_project(VERIFY_README)
        self.assertIn("doctor-guard-presence.md", text)
        self.assertIn("doctor runtime.guard", text)


if __name__ == "__main__":
    unittest.main()
