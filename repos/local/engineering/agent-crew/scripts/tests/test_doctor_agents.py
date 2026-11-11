#!/usr/bin/env python3
"""doctor AGENTS/指针/重复句检查（S9-13/S9-17 A/D）：agents.block/docs.pointers/agents.markers/docs.duplicate。"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import MARK_END, MARK_START, ProjectFixture


class DoctorAgentsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_agents_block_and_pointers_pass(self):
        for item_id in ("agents.block", "docs.pointers", "agents.markers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

    def test_skill_root_stale_fails_block_and_pointers(self):
        skill2 = self.fx.base / "skill2"
        shutil.copytree(self.fx.skill, skill2)
        self.fx.config["skillRoot"] = str(skill2)
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "agents.block")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "agents.block")["detail"])
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "docs.pointers")["detail"])

    def test_descriptive_block_pass_anchor_missing_and_skill_root_change(self):
        block = "\n".join([
            "## 协作编排",
            "- 技能根：`.planning/orchestration.json` 的 `skillRoot`。",
            "- 规则：`references/crew-rules.md`（D1–D6）。",
            "- 验收：`references/crew-acceptance.md`（A1–A5）。",
        ])
        self.fx.write_agents(block=block)
        self.fx.write_manifest()
        for item_id in ("agents.block", "docs.pointers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

        # 改 skillRoot 后不重渲染：描述性块不 stale
        skill2 = self.fx.base / "skill2"
        shutil.copytree(self.fx.skill, skill2)
        self.fx.config["skillRoot"] = str(skill2)
        self.fx.write_config()
        for item_id in ("agents.block", "docs.pointers"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

        # 缺技能根锚点 → stale，并给可执行 next
        self.fx.write_agents(block="## 协作编排\n- 规则：`references/crew-rules.md`（D1–D6）。\n")
        self.fx.write_manifest()
        rc, report = self.fx.doctor_json("--only", "agents.block")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "agents.block")
        self.assertIn("锚点", item["detail"])
        self.assertIn("refresh-block", item["next"])
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("stale", self.fx.item(report, "docs.pointers")["detail"])

    def test_unresolved_token_in_role_fails_pointers(self):
        path = self.fx.role_path("qoder", "builder")
        path.write_text(path.read_text(encoding="utf-8") + "\n{{doc_req}}\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.pointers")
        self.assertEqual(1, rc, report)
        self.assertIn("{{doc_req}}", self.fx.item(report, "docs.pointers")["detail"])

    def test_agents_markers_pending_and_duplicate(self):
        (self.fx.root / "docs/crew/AGENTS-block.pending.md").write_text(
            MARK_START + "\n## 编排\n" + MARK_END + "\n", encoding="utf-8")
        (self.fx.root / "AGENTS.md").write_text("# AGENTS\n\n无标记项目。\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "agents.markers")
        self.assertEqual(2, rc, report)
        self.assertEqual("pending", self.fx.item(report, "agents.markers")["status"])

        self.fx.write_agents(outside="# AGENTS\n\n## 协作编排\n\n## 协作编排\n")
        rc, report = self.fx.doctor_json("--only", "agents.duplicate")
        self.assertEqual(1, rc, report)
        self.assertIn("协作编排", self.fx.item(report, "agents.duplicate")["detail"])

    def test_docs_duplicate_rule_line_fails(self):
        line = "- 这是一条长度超过三十个字符的项目规则句，用来验证跨文档重复检测不会漏报。"
        self.fx.write_agents(block="## 协作编排\n" + line)
        ext = self.fx.root / "docs/crew/project-ext/builder.md"
        ext.write_text("# Builder 扩展\n\n" + line + "\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "docs.duplicate")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "docs.duplicate")
        self.assertEqual("fail", item["status"])
        self.assertIn("project-ext", item["detail"])


if __name__ == "__main__":
    unittest.main()
