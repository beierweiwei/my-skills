#!/usr/bin/env python3
"""doctor 配置/技能源/版本检查（S9-17 A/C）：config.schema/config.paths/skill.source/install.version/manifest.config。"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture, make_linked_worktree

_SCRIPTS = Path(__file__).resolve().parent.parent
_SKILL_REL = Path("repos/local/engineering/agent-crew")


def _load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


DOCTOR_MOD = _load_module("doctor_config_under_test", "doctor.py")
INIT_MOD = _load_module("init_config_under_test", "init.py")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "-c", "protocol.file.allow=always", *args],
        check=True, capture_output=True,
    )


def _seed_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "README.md").write_text("# src repo\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")


def _write_skill_skeleton(root: Path) -> Path:
    skill = root / _SKILL_REL
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: agent-crew\nversion: 2.1.0\n---\n# skill fixture\n",
        encoding="utf-8")
    return skill


def make_submodule_checkout(base: Path) -> tuple[Path, Path]:
    """建「主仓 + 真实 git submodule 检出」，返回 (主仓技能源目录, submodule 技能源目录)。

    submodule 根的 .git 为文件、gitdir 指向 <super>/.git/modules/<name>，属版本固定副本。
    """
    repo = base / "src-repo"
    _seed_repo(repo)
    super_repo = base / "super"
    super_repo.mkdir()
    subprocess.run(["git", "init", "-q", str(super_repo)], check=True)
    _git(super_repo, "submodule", "add", "-q", str(repo), "skills-src")
    return _write_skill_skeleton(repo), _write_skill_skeleton(super_repo / "skills-src")


def make_separate_git_dir_clone(base: Path) -> tuple[Path, Path]:
    """建「主仓 + git clone --separate-git-dir 检出」，返回 (主仓, 检出) 技能源目录。

    检出根的 .git 为文件、gitdir 指向外部 git 目录，属版本固定副本。
    """
    repo = base / "src-repo"
    _seed_repo(repo)
    clone = base / "sep-clone"
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "clone", "-q",
         "--separate-git-dir", str(base / "sep-gitdir"), str(repo), str(clone)],
        check=True, capture_output=True,
    )
    return _write_skill_skeleton(repo), _write_skill_skeleton(clone)


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

        # F1 口径：strict 下既有语义把 warn 计为失败（rc=1），非 strict rc=0
        rc, report = self.fx.doctor_json("--only", "skill.source", "--strict")
        self.assertEqual(1, rc, report)
        self.assertEqual("warn", self.fx.item(report, "skill.source")["status"])

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

    def test_skill_source_fixed_checkouts_are_not_dev_worktrees(self):
        # F2：真实 git submodule / --separate-git-dir 检出是版本固定副本，不报开发 worktree
        cases = (
            ("submodule", make_submodule_checkout(self.fx.base / "subfx")),
            ("separate-git-dir", make_separate_git_dir_clone(self.fx.base / "sepfx")),
        )
        for name, (_stable, skill) in cases:
            with self.subTest(case=name):
                self.fx.config["skillRoot"] = os.path.relpath(
                    skill, self.fx.root).replace(os.sep, "/")
                self.fx.write_config()
                rc, report = self.fx.doctor_json("--only", "skill.source")
                self.assertEqual(0, rc, report)
                item = self.fx.item(report, "skill.source")
                self.assertEqual("pass", item["status"], item)
                self.assertNotIn("开发 worktree", item["detail"])
                self.assertNotIn("漂移", item["detail"])

    def test_linked_worktree_helper_semantics_align(self):
        for make in (make_linked_worktree, make_submodule_checkout,
                     make_separate_git_dir_clone):
            with self.subTest(fixture=make.__name__):
                _stable, skill = make(self.fx.base / f"align-{make.__name__}")
                doctor_result = DOCTOR_MOD.linked_worktree_root(skill)
                init_result = INIT_MOD.linked_worktree_root(skill)
                self.assertEqual(doctor_result, init_result)
        wt_root = make_linked_worktree(self.fx.base / "align-positive")[1].parents[3]
        self.assertEqual(DOCTOR_MOD.linked_worktree_root(wt_root), wt_root)
        self.assertEqual(INIT_MOD.linked_worktree_root(wt_root), wt_root)

    def test_linked_worktree_helper_fail_soft_on_malformed_gitfile(self):
        cases = {
            "non-gitdir": "not a gitdir line\n",
            "empty": "",
            "missing-target": f"gitdir: {self.fx.base / 'gone' / 'skill'}\n",
        }
        for name, content in cases.items():
            with self.subTest(case=name):
                root = self.fx.base / f"malformed-{name}"
                root.mkdir()
                (root / ".git").write_text(content, encoding="utf-8")
                self.assertIsNone(DOCTOR_MOD.linked_worktree_root(root))
                self.assertIsNone(INIT_MOD.linked_worktree_root(root))

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
