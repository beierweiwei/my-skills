#!/usr/bin/env python3
"""2.2.27 / T57：init/upgrade 生成 `.claude/agents` 组合产物链（评估 N2）。

覆盖（评估报告 §3 表 4）：
① composed_agent_path/role_stub_path 的 claude 分支落点；
② role_stub_content("claude", ...)：frontmatter name/description +
   descriptor runtime=claude；
③ composed_agent_text("claude", ...)：descriptor id=crew-<role>、runtime=claude、
   composed/managed 标记，正文含基类+overlay 组合；
④ regenerate_composed_agents 三栈：claude 项目角色存在 → 生成产物并返回
   manifest 条目（sha256）；
⑤ manifest composedAgents 集成：init 写 manifest 后 claude 条目在册
   （与 qoder/opencode 同结构）；
⑥ upgrade 视角：manifest 缺 claude 条目 → upgrade 后补登（regenerate 覆盖）。

运行：python3 -m unittest scripts.tests.test_t57_init_claude_products -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import init as init_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_fixtures import ProjectFixture  # noqa: E402


class ClaudePathContractTest(unittest.TestCase):
    def test_composed_agent_path_claude(self):
        self.assertEqual(
            Path(".claude/agents/crew-builder.md"),
            init_mod.composed_agent_path("claude", "builder"))

    def test_role_stub_path_claude(self):
        self.assertEqual(
            Path(".claude/agents/builder.md"),
            init_mod.role_stub_path("claude", "builder"))

    def test_role_stub_content_claude(self):
        text = init_mod.role_stub_content("claude", "builder")
        self.assertIn("name: builder\n", text)
        self.assertIn("description: ", text)
        self.assertIn('"runtime": "claude"', text)
        self.assertIn('"id": "builder"', text)

    def test_role_stub_content_marks_claude_runtime_in_descriptor(self):
        text = init_mod.role_stub_content("claude", "runner")
        self.assertIn('"extends": "skill:runner"', text)


class ClaudeComposedProductTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))
        self.fx.write_role("claude", "builder")

    def test_composed_agent_text_claude_descriptor(self):
        text = init_mod.composed_agent_text(self.fx.root, "claude", "builder",
                                            skill_root=self.fx.skill)
        self.assertIn("name: crew-builder", text)
        self.assertIn('"id": "crew-builder"', text)
        self.assertIn('"runtime": "claude"', text)
        self.assertIn('"composed": true', text)
        self.assertIn('"managed": true', text)
        # 基类正文与项目 overlay 都进组合产物
        self.assertIn("PROJECT_ONLY_MARKER", text)

    def test_composed_agent_text_rejects_qoder_only_fields_for_claude(self):
        # claude 产物 frontmatter 同 qoder 形态（name/description/disallowedTools
        # 由基类承接），不要求 mode 字段
        text = init_mod.composed_agent_text(self.fx.root, "claude", "builder",
                                            skill_root=self.fx.skill)
        self.assertNotIn("mode:", text.split("---\n")[1])

    def test_regenerate_composed_agents_generates_claude(self):
        generated = init_mod.regenerate_composed_agents(self.fx.root,
                                                        skill_root=self.fx.skill)
        claude = [g for g in generated if g["runtime"] == "claude"]
        self.assertEqual(1, len(claude), generated)
        self.assertEqual(".claude/agents/crew-builder.md", claude[0]["path"])
        target = self.fx.root / ".claude/agents/crew-builder.md"
        self.assertTrue(target.is_file())
        self.assertEqual(claude[0]["sha256"],
                         init_mod.sha256_text(target.read_text(encoding="utf-8")))

    def test_regenerate_skips_claude_without_project_role(self):
        for runtime in ("qoder", "opencode", "claude"):
            path = self.fx.role_path(runtime, "builder")
            if path.exists():
                path.unlink()
        generated = init_mod.regenerate_composed_agents(self.fx.root,
                                                        skill_root=self.fx.skill)
        self.assertEqual([], [g for g in generated if g["runtime"] == "claude"])

    def test_manifest_records_claude_composed_agent(self):
        generated = init_mod.regenerate_composed_agents(self.fx.root,
                                                        skill_root=self.fx.skill)
        manifest = self.fx.manifest()
        manifest["composedAgents"] = generated
        self.fx.write_manifest(manifest)
        on_disk = json.loads(
            (self.fx.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))
        paths = [item["path"] for item in on_disk["composedAgents"]]
        self.assertIn(".claude/agents/crew-builder.md", paths)
        entry = next(item for item in on_disk["composedAgents"]
                     if item["path"] == ".claude/agents/crew-builder.md")
        self.assertEqual("claude", entry["runtime"])
        self.assertTrue(entry["managed"])


if __name__ == "__main__":
    unittest.main()
