#!/usr/bin/env python3
"""2.2.3 最终定稿防回退：engineering-disciplines / reviewer 四条 / 经济档 / arena / BUG-023-024 / 版本。

纪律正文在 my-skills 分发仓，跨仓定位：优先 `ENGINEERING_DISCIPLINES_ROOT`，
否则按本仓同级 `my-skills/repos/local/engineering/engineering-disciplines` 查找；找不到跳过。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SKILL_MD = REPO_ROOT / "SKILL.md"
CREW_RULES = REPO_ROOT / "references" / "crew-rules.md"
REVIEWERS = (
    REPO_ROOT / "templates" / "agents" / "qoder" / "reviewer.md",
    REPO_ROOT / "templates" / "agents" / "opencode" / "reviewer.md",
)
FIRSTMATE = (
    REPO_ROOT / "templates" / "agents" / "qoder" / "firstmate.md",
    REPO_ROOT / "templates" / "agents" / "opencode" / "firstmate.md",
)
DISPATCH = REPO_ROOT / "templates" / "skills" / "firstmate-dispatch" / "SKILL.md"
ARENA = REPO_ROOT / "templates" / "playbooks" / "arena.md"
PLAYBOOKS = REPO_ROOT / "templates" / "playbooks"
CREW_STATE = REPO_ROOT / "scripts" / "crew-state.py"
CREW_MSG = REPO_ROOT / "scripts" / "crew-msg.py"
INIT = REPO_ROOT / "scripts" / "init.py"
PLAN_STATUS = REPO_ROOT / "scripts" / "plan-status.py"
TEST_CREW_LINT = REPO_ROOT / "scripts" / "tests" / "test_crew_lint.py"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def disciplines_dir() -> Path:
    env = os.environ.get("ENGINEERING_DISCIPLINES_ROOT")
    if env:
        return Path(env).expanduser()
    return (
        REPO_ROOT.parents[4].parent
        / "my-skills"
        / "repos"
        / "local"
        / "engineering"
        / "engineering-disciplines"
    )


class EngineeringDisciplinesTest(unittest.TestCase):
    def setUp(self):
        path = disciplines_dir() / "SKILL.md"
        if not path.is_file():
            self.skipTest(
                f"engineering-disciplines 不在默认位置：{path}；可设 ENGINEERING_DISCIPLINES_ROOT"
            )
        self.text = read(path)

    def test_six_sections_with_3_to_5_body_lines(self):
        sections = re.split(r"^## ", self.text, flags=re.MULTILINE)
        bodies = [s for s in sections[1:] if s.startswith("\u00a7")]
        self.assertEqual(len(bodies), 6, f"应有 6 节，实际 {len(bodies)}")
        for body in bodies:
            lines = body.splitlines()
            title, content = lines[0].strip(), lines[1:]
            content_lines = [line for line in content if line.strip()]
            self.assertGreaterEqual(len(content_lines), 3, f"节「{title}」正文不足 3 行")
            self.assertLessEqual(len(content_lines), 5, f"节「{title}」正文超过 5 行")

    def test_manual_trigger_only(self):
        self.assertIn("disable-model-invocation: true", self.text)
        self.assertIn("手动调用或主舵点名", self.text)

    def test_no_agent_crew_references(self):
        for token in ("agent-crew", "crew-rules", "crew_rules", "templates/agents"):
            self.assertNotIn(token, self.text, f"纪律正文不应引用 {token}")


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.rules = read(CREW_RULES)

    def test_f13_to_f16_defined_with_index(self):
        for number in range(13, 17):
            self.assertRegex(self.rules, rf"\*\*F{number} ", f"缺少 F{number}")
        self.assertIn("F1–F16", self.rules)
        self.assertIn("## F 收口\n", self.rules)
        self.assertNotIn("## F 收口与评审", self.rules)
        self.assertNotRegex(self.rules, r"\*\*F2[12] ")

    def test_reviewer_templates_embed_four_items(self):
        for path in REVIEWERS:
            text = read(path)
            for keyword in ("意图前置", "证据门槛", "防凑数", "盲审"):
                self.assertIn(keyword, text, f"{path.name} 缺少 {keyword}")
            self.assertIn("F1–F16", text, f"{path.name} 引用区间未更新")
            for verdict in ("PASS", "BLOCK", "可延后"):
                self.assertIn(verdict, text, f"{path.name} 缺少结论枚举 {verdict}")

    def test_reviewer_templates_have_no_section_pointers(self):
        for path in REVIEWERS:
            text = read(path)
            self.assertNotIn("\u00a7", text, f"{path.name} 不应有节名指针")
            self.assertNotIn("engineering-disciplines", text)


class EconomyTest(unittest.TestCase):
    def test_economy_table_in_firstmate_templates(self):
        for path in FIRSTMATE:
            text = read(path)
            for token in ("interrogate", "arena", "swarm", "升档判断权在大副"):
                self.assertIn(token, text, f"{path.name} 缺少经济档表要素 {token}")
            self.assertIn("F1–F16", text, f"{path.name} 引用区间未更新")

    def test_economy_table_in_dispatch_sop(self):
        text = read(DISPATCH)
        for token in ("interrogate", "arena", "swarm", "升档判断权在大副", "2.1 选板"):
            self.assertIn(token, text, f"firstmate-dispatch 缺少经济档要素 {token}")


class ArenaPlaybookTest(unittest.TestCase):
    def test_arena_playbook_structure(self):
        self.assertTrue(ARENA.is_file())
        text = read(ARENA)
        for phase in ("Frame", "Fan out", "Pick", "Graft", "Verify", "Output"):
            self.assertIn(phase, text)
        status_lines = [line for line in text.splitlines() if "**Status:**" in line]
        phases = [line for line in text.splitlines() if line.startswith("### Phase ")]
        self.assertEqual(len(status_lines), len(phases), "每 Phase 必须恰一行 Status")

    def test_arena_playbook_passes_lint(self):
        proc = subprocess.run(
            [sys.executable, str(PLAN_STATUS), "--playbooks", str(PLAYBOOKS)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


class BugRegressionTest(unittest.TestCase):
    def test_bug_023_dispatch_commands(self):
        text = read(DISPATCH)
        code_lines: list[str] = []
        in_code = False
        for line in text.splitlines():
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                code_lines.append(line)
        code = "\n".join(code_lines)
        for forbidden in ("herdr agent start", "herdr agent prompt", "herdr tab create"):
            self.assertNotIn(forbidden, code, f"示例仍直写 {forbidden}")
        for required in (
            "dispatch.py tab create",
            "start-agent.sh",
            "dispatch.py prompt",
            "仅人工恢复",
        ):
            self.assertIn(required, text, f"缺少 {required}")

    def test_bug_024_receipt_status_enum(self):
        state_doc = read(CREW_STATE)
        self.assertIn("status(done|blocked|captain)", state_doc)
        self.assertNotIn("status(done|blocked|captain|ack)", state_doc)
        self.assertIn("status(done|blocked|captain)", read(CREW_MSG))


class VersionTest(unittest.TestCase):
    def test_version_matches_in_three_places(self):
        self.assertIn("version: 2.2.29", read(SKILL_MD))
        self.assertIn("v2.2.29", read(SKILL_MD))
        self.assertIn('SKILL_VERSION_FALLBACK = "2.2.29"', read(INIT))
        self.assertIn("## 2.2.29", read(CHANGELOG))
        self.assertIn('"2.2.29"', read(TEST_CREW_LINT))
        self.assertIn("## 2.2.17", read(CHANGELOG))


class BoundaryTest(unittest.TestCase):
    def test_no_section_pointer_wiring(self):
        for path in REPO_ROOT.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py"}:
                continue
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for marker in ("\u00a7\u5148\u78e8\u540e\u5e72", "\u00a7\u6b62\u635f\u4e0d\u76f2\u8bd5",
                           "\u00a7\u8fd0\u884c\u8bc1\u5b9e"):
                self.assertNotIn(marker, text, f"{path.relative_to(REPO_ROOT)} 出现节名接线")


if __name__ == "__main__":
    unittest.main()
