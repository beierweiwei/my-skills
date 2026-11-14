#!/usr/bin/env python3
"""2.2.33 工单 01-A/A3：doctor 增 config.git_clean 未提交告警（FB-063 防复发）。

`orchestration.json`/`docs/crew/manifest.json` 有未提交改动 → warn（只提醒不阻断，
fail=0 发布口径不变）；全干净 → pass；非 git 仓库 → skip。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import DOCTOR, ProjectFixture

_SPEC = importlib.util.spec_from_file_location("doctor_2233_mod",
                                               DOCTOR.parent / "doctor.py")
doctor_2233 = importlib.util.module_from_spec(_SPEC)
sys.modules["doctor_2233_mod"] = doctor_2233
_SPEC.loader.exec_module(doctor_2233)

PYTHON = sys.executable
CONFIG_REL = ".planning/orchestration.json"
MANIFEST_REL = "docs/crew/manifest.json"


class ConfigGitCleanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.fx = ProjectFixture(self.base)

    def git(self, *args: str) -> None:
        subprocess.run(["git", "-C", str(self.fx.root), *args], check=True,
                       capture_output=True, text=True,
                       env={**self.fx.env(),
                            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"})

    # ---------- 注册 ----------

    def test_registered_in_checks_and_methods(self):
        self.assertIn("config.git_clean", doctor_2233.CHECKS)
        self.assertEqual("check_config_git_clean",
                         doctor_2233.METHODS["config.git_clean"])

    # ---------- 三态 ----------

    def test_uncommitted_config_warns(self):
        rc, report = self.fx.doctor_json("--only", "config.git_clean")
        item = self.fx.item(report, "config.git_clean")
        self.assertEqual("warn", item["status"], json.dumps(item, ensure_ascii=False))
        self.assertIn(CONFIG_REL, item["detail"])
        self.assertIn(MANIFEST_REL, item["detail"])
        self.assertIn("commit", item["next"])
        # warn 不阻断：非 strict rc=0
        self.assertEqual(0, rc)

    def test_clean_config_passes(self):
        self.git("add", CONFIG_REL, MANIFEST_REL)
        self.git("commit", "-qm", "configs")
        rc, report = self.fx.doctor_json("--only", "config.git_clean")
        item = self.fx.item(report, "config.git_clean")
        self.assertEqual("pass", item["status"], json.dumps(item, ensure_ascii=False))
        self.assertEqual(0, rc)

    def test_modified_after_commit_warns(self):
        self.git("add", CONFIG_REL, MANIFEST_REL)
        self.git("commit", "-qm", "configs")
        (self.fx.root / CONFIG_REL).write_text(
            (self.fx.root / CONFIG_REL).read_text(encoding="utf-8") + "\n",
            encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "config.git_clean")
        item = self.fx.item(report, "config.git_clean")
        self.assertEqual("warn", item["status"], json.dumps(item, ensure_ascii=False))
        self.assertIn(CONFIG_REL, item["detail"])
        self.assertEqual(0, rc)

    def test_non_git_directory_skips(self):
        plain = self.base / "plain"
        plain.mkdir()
        (plain / CONFIG_REL).parent.mkdir(parents=True)
        (plain / CONFIG_REL).write_text("{}", encoding="utf-8")
        (plain / MANIFEST_REL).parent.mkdir(parents=True)
        (plain / MANIFEST_REL).write_text("{}", encoding="utf-8")
        proc = subprocess.run(
            [PYTHON, str(DOCTOR), "--root", str(plain),
             "--only", "config.git_clean", "--json"],
            capture_output=True, text=True, timeout=60)
        report = json.loads(proc.stdout)
        item = next(entry for entry in report["items"]
                    if entry["id"] == "config.git_clean")
        self.assertEqual("skip", item["status"], json.dumps(item, ensure_ascii=False))

    # ---------- 既有 rc 语义不变 ----------

    def test_strict_warn_keeps_existing_rc_semantics(self):
        """--strict 下 warn 仍按既有语义使 rc=1（fail=0 发布口径由 release-check 负责）。"""
        proc = subprocess.run(
            [PYTHON, str(DOCTOR), "--root", str(self.fx.root),
             "--only", "config.git_clean", "--strict", "--json"],
            capture_output=True, text=True, timeout=60)
        report = json.loads(proc.stdout)
        item = next(entry for entry in report["items"]
                    if entry["id"] == "config.git_clean")
        self.assertEqual("warn", item["status"])
        self.assertEqual(1, proc.returncode)

    def test_doctor_full_run_includes_new_check(self):
        rc, report = self.fx.doctor_json()
        ids = {entry["id"] for entry in report["items"]}
        self.assertIn("config.git_clean", ids)


if __name__ == "__main__":
    unittest.main()
