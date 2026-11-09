#!/usr/bin/env python3
"""doctor manifest/hash 检查（S9-15/S9-17 A）：schema/hash.file/hash.block/orphan。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture, block_sha, text_sha


class DoctorManifestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_manifest_schema_file_and_block_hash_pass(self):
        for item_id in ("manifest.schema", "manifest.hash.file", "manifest.hash.block"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

    def test_manifest_hash_file_mismatch_fails(self):
        self.fx.script.write_text("#!/usr/bin/env python3\n# tampered\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "manifest.hash.file")
        self.assertEqual(1, rc, report)
        self.assertIn("dispatch.py", self.fx.item(report, "manifest.hash.file")["detail"])

    def test_manifest_hash_block_mismatch_fails(self):
        agents = self.fx.root / "AGENTS.md"
        agents.write_text(agents.read_text(encoding="utf-8").replace(
            "- 通用规则见技能源；项目决策只写本文件。",
            "- 通用规则见技能源；项目决策只写本文件（手改）。"), encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "manifest.hash.block")
        self.assertEqual(1, rc, report)
        self.assertIn("blockSha256", self.fx.item(report, "manifest.hash.block")["detail"])

    def test_manifest_registering_rule_copy_fails(self):
        manifest = self.fx.manifest()
        manifest["entries"].append({
            "kind": "file", "path": "docs/crew-rules.md",
            "sha256": "0" * 64, "templateVersion": "2.1.0",
        })
        self.fx.write_manifest(manifest)
        rc, report = self.fx.doctor_json("--only", "manifest.schema")
        self.assertEqual(1, rc, report)
        self.assertIn("crew-rules.md", self.fx.item(report, "manifest.schema")["detail"])

    def test_manifest_orphan_missing_target_fails(self):
        manifest = self.fx.manifest()
        manifest["entries"].append({
            "kind": "file", "path": "scripts/missing.py",
            "sha256": "0" * 64, "templateVersion": "2.1.0",
        })
        self.fx.write_manifest(manifest)
        rc, report = self.fx.doctor_json("--only", "manifest.orphan")
        self.assertEqual(1, rc, report)
        self.assertIn("missing.py", self.fx.item(report, "manifest.orphan")["detail"])

    def test_manifest_hash_block_accepts_fixture_normalization(self):
        agents = (self.fx.root / "AGENTS.md").read_text(encoding="utf-8")
        manifest = self.fx.manifest()
        self.assertEqual(block_sha(agents), manifest["entries"][1]["blockSha256"])
        self.assertEqual(text_sha(self.fx.script), manifest["entries"][0]["sha256"])


if __name__ == "__main__":
    unittest.main()
