#!/usr/bin/env python3
"""doctor 配置/技能源/版本检查（S9-17 A/C）：config.schema/config.paths/skill.source/install.version/manifest.config。"""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture, make_linked_worktree


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

    def test_retro_config_registered_and_validated(self):
        self.fx.config["retro"] = {"enabled": True, "periodDays": 30}
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "config.schema")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "config.schema")["status"])

        self.fx.config["retro"] = {"enabled": "yes", "periodDays": 0}
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "config.schema")
        self.assertEqual(1, rc, report)
        detail = self.fx.item(report, "config.schema")["detail"]
        self.assertIn("retro.enabled", detail)
        self.assertIn("retro.periodDays", detail)

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

    def test_skill_source_allows_project_skills_dirs(self):
        # 项目级 skills 目录内的 SKILL.md 属合法安装，不算技能源副本；
        # fixture skillRoot 仍是绝对路径 → 只保留 WARN，不因副本 FAIL
        skill_md = self.fx.root / ".opencode/skills/firstmate-dispatch/SKILL.md"
        skill_md.parent.mkdir(parents=True)
        skill_md.write_text("---\nname: firstmate-dispatch\n---\n# SOP\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("warn", item["status"])
        self.assertNotIn("副本", item["detail"])

        # 项目 scope 安装技能源（相对 skillRoot）：SKILL.md/references 均在 skills 目录内 → PASS
        installed = self.fx.root / ".qoder/skills/agent-crew"
        shutil.copytree(self.fx.skill, installed)
        self.fx.config["skillRoot"] = ".qoder/skills/agent-crew"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("pass", item["status"])
        self.assertNotIn("副本", item["detail"])

    def test_skill_source_ignores_third_party_skill_copies(self):
        paths = (
            "docs/业务/原型/.venv/lib/site-packages/playwright/SKILL.md",
            "docs/业务/原型/node_modules/pkg/crew-rules.md",
        )
        for rel in paths:
            path = self.fx.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# third-party\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("warn", item["status"], item)
        self.assertNotIn("副本", item["detail"])
        for rel in paths:
            self.assertNotIn(rel, item["detail"])

    def test_skill_source_skips_gitignored_external_sources_only(self):
        rel = "skill-src/repos/local/other-skill/SKILL.md"
        other = self.fx.root / rel
        other.parent.mkdir(parents=True)
        other.write_text("---\nname: other-skill\n---\n", encoding="utf-8")
        # 未被忽略：仍判项目内技能源副本
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(1, rc, report)
        self.assertIn(rel, self.fx.item(report, "skill.source")["detail"])

        # 被 .gitignore 忽略的外部技能源：不算副本
        (self.fx.root / ".gitignore").write_text("skill-src/\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("warn", item["status"], item)  # fixture skillRoot 是绝对路径
        self.assertNotIn("副本", item["detail"])
        self.assertNotIn(rel, item["detail"])

        # 非 git 仓：维持原判定（git 不可用/rc≠0 同路径）
        shutil.rmtree(self.fx.root / ".git")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(1, rc, report)
        self.assertIn(rel, self.fx.item(report, "skill.source")["detail"])

    def test_skill_source_still_flags_real_doc_rule_copy(self):
        (self.fx.root / "docs/crew-rules.md").write_text("# rules copy\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("fail", item["status"], item)
        self.assertIn("docs/crew-rules.md", item["detail"])

    def test_skill_source_clean_project_passes(self):
        self.fx.config["skillRoot"] = "../skill"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("pass", item["status"], item)
        self.assertNotIn("副本", item["detail"])

    def test_skill_source_dev_worktree_warns_without_false_positives(self):
        repo_skill, wt_skill = make_linked_worktree(self.fx.base)
        # 开发 worktree（仓外 + 祖先 .git 为文件）：warn + 漂移语义 + 可执行 next
        self.fx.config["skillRoot"] = "../" + wt_skill.relative_to(self.fx.base).as_posix()
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("warn", item["status"], item)
        self.assertIn("开发 worktree", item["detail"])
        self.assertIn("漂移", item["detail"])
        self.assertTrue(item["next"])
        self.assertIn("稳定分发副本", item["next"])

        # 稳定分发副本形态（主仓工作树，.git 为目录）：不误报
        self.fx.config["skillRoot"] = "../" + repo_skill.relative_to(self.fx.base).as_posix()
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "skill.source")
        self.assertEqual("pass", item["status"], item)
        self.assertNotIn("开发 worktree", item["detail"])

        # 项目内 skillRoot：不误报
        installed = self.fx.root / ".qoder/skills/agent-crew"
        shutil.copytree(self.fx.skill, installed)
        self.fx.config["skillRoot"] = ".qoder/skills/agent-crew"
        self.fx.write_config()
        rc, report = self.fx.doctor_json("--only", "skill.source")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "skill.source")["status"])

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
