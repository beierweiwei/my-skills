#!/usr/bin/env python3
"""doctor playbooks.library：两层剧本库发现 + 结构校验 + fail 时 next 可执行。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import PLAYBOOK_SEEDS, ProjectFixture


class DoctorPlaybooksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def seed_path(self, name: str) -> Path:
        return self.fx.skill / "templates" / "playbooks" / name

    def test_library_pass_with_counts(self):
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertEqual("pass", item["status"])
        self.assertIn("技能层", item["detail"])
        self.assertIn("项目层", item["detail"])
        self.assertEqual(item["next"], "")

    def test_missing_skill_seed_fails_with_next(self):
        self.seed_path("babysit.md").unlink()
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertEqual("fail", item["status"])
        self.assertIn("babysit.md", item["detail"])
        self.assertIn("templates/playbooks", item["next"])

    def test_missing_skill_layer_fails_with_next(self):
        for name in PLAYBOOK_SEEDS:
            self.seed_path(name).unlink()
        (self.fx.skill / "templates" / "playbooks" / "README.md").unlink()
        (self.fx.skill / "templates" / "playbooks").rmdir()
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertIn("技能层剧本库目录缺失", item["detail"])
        self.assertTrue(item["next"])

    def test_broken_project_playbook_fails_with_next(self):
        broken = self.fx.root / ".planning/playbooks/my-play.md"
        broken.write_text("# 剧本：坏结构\n\n## Phases\n\n### Phase 1: 缺 Status\n- [ ] 待办\n",
                          encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertEqual("fail", item["status"])
        self.assertIn("项目层剧本结构不合法", item["detail"])
        self.assertIn("my-play.md", item["detail"])
        self.assertIn("--playbooks", item["next"])

    def test_missing_project_readme_fails_with_next(self):
        (self.fx.root / ".planning/playbooks/README.md").unlink()
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertIn("项目层剧本库缺少", item["detail"])
        self.assertIn("init", item["next"])

    def test_missing_project_layer_fails_with_next(self):
        (self.fx.root / ".planning/playbooks/README.md").unlink()
        (self.fx.root / ".planning/playbooks").rmdir()
        rc, report = self.fx.doctor_json("--only", "playbooks.library")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "playbooks.library")
        self.assertIn("项目层剧本库目录缺失", item["detail"])
        self.assertIn("init", item["next"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
