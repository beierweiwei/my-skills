#!/usr/bin/env python3
"""2.2.27 / T57：doctor claude 三栈 discovery/composed/names + 旧过渡记录 warn。

覆盖（评估报告 §3 表 3 + §4 R7）：
① role_entries 认 `.claude/agents`（core / crew-* 组合产物跳过 / custom-*）；
② `role.discovery.claude`：非规范落点 fail、正常文件 pass（claude 无 CLI
   agent list 等价命令，只做落点规范检查）；
③ `role.composed` 三栈：claude 组合产物缺失 → fail，存在且 hash 一致 → pass；
④ `role.names`：claude 条目重复 id 检测；
⑤ `runtime.legacy-registry`：dispatch-agents.json 含 actualRuntime 旧过渡
   记录 → warn 一次性提示清理；无记录 → pass。

运行：python3 -m unittest scripts.tests.test_t57_doctor_claude -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_fixtures import ProjectFixture  # noqa: E402


class DoctorClaudeRoleEntriesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_role_entries_recognizes_claude_core(self):
        self.fx.write_role("claude", "builder")
        entries = self._entries()
        claude = [e for e in entries if e["runtime"] == "claude"]
        self.assertEqual(1, len(claude), entries)
        self.assertEqual("builder", claude[0]["id"])
        self.assertFalse(claude[0]["custom"])

    def test_role_entries_skips_claude_composed_and_marks_custom(self):
        self.fx.write_role("claude", "builder")
        custom = self.fx.role_path("claude", "acme", custom=True)
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_text(
            "---\nname: acme\ndescription: custom\n---\n"
            "<!-- crew-role\n" + json.dumps({
                "id": "acme", "runtime": "claude", "extends": "skill:builder",
                "managed": False, "capabilities": ["read"], "gates": [],
                "lifecycle": "keep-until",
                "prompt": custom.relative_to(self.fx.root).as_posix()}) + "\n-->\n"
            "# Acme\n## X\nY\n", encoding="utf-8")
        composed = self.fx.root / ".claude/agents/crew-builder.md"
        composed.parent.mkdir(parents=True, exist_ok=True)
        composed.write_text("---\nname: crew-builder\ndescription: managed\n---\nbody\n",
                            encoding="utf-8")
        entries = self._entries()
        claude = [e for e in entries if e["runtime"] == "claude"]
        ids = {e["path"].name for e in claude}
        self.assertIn("builder.md", ids)
        self.assertIn("custom-acme.md", ids)
        self.assertNotIn("crew-builder.md", ids)

    def _entries(self) -> list[dict]:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
        import doctor as doctor_mod
        return doctor_mod.Doctor(self.fx.root, "dev", False).role_entries()


class DoctorClaudeChecksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))
        self.fx.write_role("claude", "builder")

    def test_discovery_claude_passes_on_regular_layout(self):
        self.fx.composed_entries = self.fx.write_composed_agents()
        self.fx.write_manifest()
        rc, report = self.fx.doctor_json("--only", "role.discovery.claude")
        item = self.fx.item(report, "role.discovery.claude")
        self.assertEqual("pass", item["status"], item)

    def test_discovery_claude_fails_on_nested_role(self):
        nested = self.fx.root / ".claude/agents/custom/deep.md"
        nested.parent.mkdir(parents=True, exist_ok=True)
        nested.write_text("---\nname: deep\ndescription: x\n---\nbody\n",
                          encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.discovery.claude")
        item = self.fx.item(report, "role.discovery.claude")
        self.assertEqual("fail", item["status"], item)

    def test_role_composed_fails_when_claude_product_missing(self):
        rc, report = self.fx.doctor_json("--only", "role.composed")
        item = self.fx.item(report, "role.composed")
        self.assertEqual("fail", item["status"], item)
        self.assertIn(".claude/agents/crew-builder.md", item["detail"])

    def test_role_composed_passes_with_claude_product_registered(self):
        self.fx.composed_entries = self.fx.write_composed_agents()
        self.fx.write_manifest()
        rc, report = self.fx.doctor_json("--only", "role.composed")
        item = self.fx.item(report, "role.composed")
        self.assertEqual("pass", item["status"], item)

    def test_role_names_detects_claude_custom_core_name_clash(self):
        self.fx.write_role("claude", "helm", custom=True)
        rc, report = self.fx.doctor_json("--only", "role.names")
        item = self.fx.item(report, "role.names")
        self.assertEqual("fail", item["status"], item)
        self.assertIn("helm", item["detail"])

    def test_legacy_registry_warns_on_transition_record(self):
        registry = self.fx.root / ".git/crew/dispatch-agents.json"
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "proj-helm-9": {"role": "helm", "runtime": "qoder",
                            "actualRuntime": "claude"},
        }), encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "runtime.legacy-registry")
        item = self.fx.item(report, "runtime.legacy-registry")
        self.assertEqual("warn", item["status"], item)
        self.assertIn("actualRuntime", item["detail"])
        self.assertIn("proj-helm-9", item["detail"])

    def test_legacy_registry_passes_when_clean(self):
        rc, report = self.fx.doctor_json("--only", "runtime.legacy-registry")
        item = self.fx.item(report, "runtime.legacy-registry")
        self.assertEqual("pass", item["status"], item)


if __name__ == "__main__":
    unittest.main()
