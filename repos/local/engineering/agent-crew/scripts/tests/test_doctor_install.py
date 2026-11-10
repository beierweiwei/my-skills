#!/usr/bin/env python3
"""doctor 安装/技能仓/输出契约（S9-17 A/D、impl-plan §4 断言 6/8）：install.log/skill.lint/skill.purity/strict/json/只读。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture


class DoctorInstallTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fx = ProjectFixture(Path(self._tmp.name))

    def test_install_log_pass_missing_warn_corrupt_fail(self):
        rc, report = self.fx.doctor_json("--only", "install.log")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "install.log")["status"])

        log = self.fx.root / "docs/crew/install-log.jsonl"
        log.unlink()
        rc, report = self.fx.doctor_json("--only", "install.log")
        self.assertEqual(0, rc, report)
        self.assertEqual("warn", self.fx.item(report, "install.log")["status"])

        log.write_text("{not-json\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "install.log")
        self.assertEqual(1, rc, report)
        self.assertIn("install-log", self.fx.item(report, "install.log")["detail"])

    def test_skill_lint_passes_with_cli_and_purity_skips_before_d(self):
        rc, report = self.fx.doctor_json("--only", "skill.lint")
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "skill.lint")["status"])

        rc, report = self.fx.doctor_json("--only", "skill.purity")
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "skill.purity")["status"])

        lint = self.fx.skill / "scripts/crew-lint.py"
        lint.parent.mkdir()
        lint.write_text(
            "#!/usr/bin/env python3\nimport sys\nprint('crew-lint: ok')\nsys.exit(0)\n",
            encoding="utf-8",
        )
        rc, report = self.fx.doctor_json("--only", "skill.lint")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "skill.lint")["status"])
        # 无 --purity 支持 → 仍 SKIP（D 交付前）
        rc, report = self.fx.doctor_json("--only", "skill.purity")
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "skill.purity")["status"])

        lint.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "if '--help' in sys.argv:\n"
            "    print('--purity')\n"
            "print('crew-lint: ok')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )
        rc, report = self.fx.doctor_json("--only", "skill.purity")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "skill.purity")["status"])

    def test_strict_warn_fails_json_shape_and_out(self):
        self.fx.config["projectName"] = "Renamed Project"
        self.fx.write_config()
        out = self.fx.base / "audit/doctor.json"
        proc = self.fx.doctor("--only", "manifest.config", "--strict", "--json",
                              "--out", str(out))
        self.assertEqual(1, proc.returncode)
        report = json.loads(proc.stdout)
        for key in ("schemaVersion", "runId", "root", "profile", "skillVersion",
                    "configHash", "summary", "items"):
            self.assertIn(key, report)
        for key in ("pass", "warn", "fail", "skip"):
            self.assertIn(key, report["summary"])
        for item in report["items"]:
            for key in ("id", "status", "detail", "evidence", "next"):
                self.assertIn(key, item)
        self.assertTrue(out.is_file())
        self.assertEqual(report, json.loads(out.read_text(encoding="utf-8")))

    def test_readonly_full_run_zero_project_writes(self):
        self.fx.commit()
        before = self.fx.snapshot()
        proc = self.fx.doctor("--profile", "release", "--json")
        self.assertIn(proc.returncode, (0, 1, 2), proc.stderr)
        json.loads(proc.stdout)
        after = self.fx.snapshot()
        self.assertEqual(before, after)

    def test_profile_release_uses_release_label_and_json(self):
        rc, report = self.fx.doctor_json("--profile", "release", "--only", "config.schema")
        self.assertEqual(0, rc, report)
        self.assertEqual("release", report["profile"])
        self.assertEqual(str(self.fx.root.resolve()), report["root"])


if __name__ == "__main__":
    unittest.main()
