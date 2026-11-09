#!/usr/bin/env python3
"""doctor 配置/技能源/版本检查（S9-17 A/C）：config.schema/config.paths/skill.source/install.version/manifest.config。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture


class DoctorConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_config_schema_paths_identity_pass(self):
        for item_id in ("config.schema", "config.paths", "config.identity"):
            rc, report = self.fx.doctor_json("--only", item_id)
            self.assertEqual(0, rc, (item_id, report))
            self.assertEqual("pass", self.fx.item(report, item_id)["status"], item_id)

    def test_config_unknown_key_fails(self):
        self.fx.config["bogusKey"] = 1
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "config.schema")
        self.assertEqual(1, rc, report)
        self.assertIn("bogusKey", self.fx.item(report, "config.schema")["detail"])

    def test_skill_source_rule_copy_fails_and_absolute_warns(self):
        copy = self.fx.root / "docs/crew-rules.md"
        copy.write_text("# rules copy\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(1, rc, report)
        self.assertIn("crew-rules.md", self.fx.item(report, "skill.source")["detail"])
        copy.unlink()

        # fixture skillRoot 是绝对路径：非 strict PASS(warn)，strict FAIL
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        self.assertEqual("warn", self.fx.item(report, "skill.source")["status"])
        rc, report = self.fx.doctor_json("--only", "skill.source", "--strict")
        self.assertEqual(1, rc, report)

    def test_install_version_mismatch_fails_then_restores(self):
        self.fx.config["skillVersion"] = "9.9.9"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "install.version")
        self.assertEqual(1, rc, report)
        self.assertIn("9.9.9", self.fx.item(report, "install.version")["detail"])

        self.fx.config["skillVersion"] = "2.1.0"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "install.version")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "install.version")["status"])

    def test_install_version_check_source_override(self):
        skill2 = self.fx.base / "skill2"
        shutil.copytree(self.fx.skill, skill2)
        (skill2 / "SKILL.md").write_text(
            "---\nname: agent-crew\nversion: 9.9.9\n---\n", encoding="utf-8")
        proc = self.fx.doctor("--only", "install.version", "--check-source", str(skill2),
                              "--json")
        self.assertEqual(1, proc.returncode, proc.stdout)
        report = json.loads(proc.stdout)
        self.assertIn("9.9.9", self.fx.item(report, "install.version")["detail"])

    def test_manifest_config_mismatch_warns_with_refresh_hint(self):
        self.fx.config["projectName"] = "Renamed Project"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "manifest.config")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "manifest.config")
        self.assertEqual("warn", item["status"])
        self.assertIn("refresh-block", item["next"])

        rc, report = self.fx.doctor_json("--only", "manifest.config", "--strict")
        self.assertEqual(1, rc, report)


if __name__ == "__main__":
    unittest.main()
