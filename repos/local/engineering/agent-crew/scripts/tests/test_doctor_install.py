#!/usr/bin/env python3
"""doctor 安装/技能仓/输出契约（S9-17 A/D、impl-plan §4 断言 6/8）：install.log/skill.lint/skill.purity/strict/json/只读。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import ProjectFixture, SKILL_DIR

sys.path.insert(0, str(SKILL_DIR / "scripts"))
import init as init_lib  # noqa: E402


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

    def test_install_scripts_source_pass_warn_skip_and_strict(self):
        names = list(init_lib.SCRIPT_MANIFEST)

        # 源无 scripts/：覆盖集=SCRIPT_MANIFEST，全部源缺失 → warn（strict rc=1）
        rc, report = self.fx.doctor_json("--only", "install.scripts_source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "install.scripts_source")
        self.assertEqual("warn", item["status"], item)
        self.assertIn(f"源缺失 {len(names)} 项", item["detail"])
        self.assertIn("scripts/init.py", item["detail"])
        proc = self.fx.doctor("--only", "install.scripts_source", "--strict", "--json")
        self.assertEqual(1, proc.returncode, proc.stdout)

        # 源补齐、项目仍只有 dispatch.py（源有/项目无）→ warn 项目缺失
        origin_dir = self.fx.skill / "scripts"
        origin_dir.mkdir(parents=True, exist_ok=True)
        for name in names:
            text = (self.fx.script.read_text(encoding="utf-8") if name == "dispatch.py"
                    else f"#!/usr/bin/env python3\n# managed {name}\n")
            (origin_dir / name).write_text(text, encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "install.scripts_source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "install.scripts_source")
        self.assertEqual("warn", item["status"], item)
        self.assertIn(f"项目缺失 {len(names) - 1} 项", item["detail"])
        self.assertIn("scripts/init.py", item["detail"])

        # 项目补齐且全部一致 → pass，detail 含核对数量
        for name in names:
            if name == "dispatch.py":
                continue
            target = self.fx.root / "scripts" / name
            target.write_text(f"#!/usr/bin/env python3\n# managed {name}\n",
                              encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "install.scripts_source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "install.scripts_source")
        self.assertEqual("pass", item["status"], item)
        self.assertIn(f"核对 {len(names)} 项", item["detail"])

        # 源脚本不同 → warn（非 strict rc 不变）；strict → rc=1；next 指向 upgrade
        (origin_dir / "dispatch.py").write_text(
            "#!/usr/bin/env python3\n# source newer\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "install.scripts_source")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "install.scripts_source")
        self.assertEqual("warn", item["status"], item)
        self.assertIn(f"1/{len(names)}", item["detail"])
        self.assertIn("scripts/dispatch.py", item["detail"])
        self.assertIn("upgrade.py --apply --confirm", item["next"])
        proc = self.fx.doctor("--only", "install.scripts_source", "--strict", "--json")
        self.assertEqual(1, proc.returncode, proc.stdout)

    def test_install_scripts_source_skips_without_manifest_or_source(self):
        (self.fx.root / "docs/crew/manifest.json").unlink()
        rc, report = self.fx.doctor_json("--only", "install.scripts_source")
        self.assertEqual(0, rc, report)
        self.assertEqual("skip", self.fx.item(report, "install.scripts_source")["status"])

        self.fx.write_manifest()
        proc = self.fx.doctor("--only", "install.scripts_source",
                              "--check-source", str(self.fx.base / "no-skill"), "--json")
        self.assertEqual(0, proc.returncode, proc.stdout)
        item = self.fx.item(json.loads(proc.stdout), "install.scripts_source")
        self.assertEqual("skip", item["status"], item)

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
