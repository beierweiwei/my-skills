#!/usr/bin/env python3
"""2.2.22 角色模板指针与防副本静态断言（S-1/S-2/S-3/S-R2/S-4）。

覆盖工单验收断言：
① qoder/opencode × builder/reviewer/firstmate/runner/helm 各含
   `{{skill_root}}/references/verification-redlines.md`（VR1–VR6）指针，
   每份模板恰好一处（并入验收行或独立一行），不复制 VR 正文；
② builder/runner 另指真动作剧本 `templates/playbooks/real-action-verification.md`；
③ `templates/AGENTS-编排节.md`「规则与文档指针」含 verification-redlines.md；
④ firstmate/runner 双栈 4 处 X4 复述句含「仓库、证据、对话」三词。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNTIMES = ("qoder", "opencode")
POINTER_ROLES = ("builder", "reviewer", "firstmate", "runner", "helm")
PLAYBOOK_ROLES = ("builder", "runner")
VR_POINTER = "{{skill_root}}/references/verification-redlines.md"
PLAYBOOK_POINTER = "{{skill_root}}/templates/playbooks/real-action-verification.md"
X4_TEMPLATES = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/qoder/runner.md",
    "templates/agents/opencode/firstmate.md",
    "templates/agents/opencode/runner.md",
)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class TemplatePointerTest(unittest.TestCase):
    def test_affected_roles_point_to_verification_redlines(self):
        for runtime in RUNTIMES:
            for role in POINTER_ROLES:
                rel = f"templates/agents/{runtime}/{role}.md"
                text = read(rel)
                with self.subTest(rel=rel):
                    self.assertEqual(1, text.count(VR_POINTER), rel)
                    self.assertIn("VR1–VR6", text, rel)

    def test_builder_runner_point_to_real_action_playbook(self):
        for runtime in RUNTIMES:
            for role in PLAYBOOK_ROLES:
                rel = f"templates/agents/{runtime}/{role}.md"
                with self.subTest(rel=rel):
                    self.assertIn(PLAYBOOK_POINTER, read(rel), rel)

    def test_agents_block_pointer_section_mentions_verification_redlines(self):
        text = read("templates/AGENTS-编排节.md")
        self.assertIn("`references/verification-redlines.md`", text)
        self.assertIn("VR1–VR6", text)
        self.assertIn("`templates/playbooks/real-action-verification.md`", text)

    def test_x4_reproduction_lists_repo_evidence_dialog(self):
        """2.2.33/T51：模板不复述 X4 落点清单（X4 唯一正文），断言指针存在。"""
        for rel in X4_TEMPLATES:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("按 X4 执行", text, f"{rel} 缺少 X4 指针")
            for zone in ("沙箱", "共享目录", "线程工作区"):
                with self.subTest(rel=rel, zone=zone):
                    self.assertNotIn(zone, text, f"{rel} 残留 X4 落点复述：{zone}")


if __name__ == "__main__":
    unittest.main()
