#!/usr/bin/env python3
"""2.2.15 T35：AGENTS 受管块模板漂移检查（init --check + doctor）。

根因（FB-032）：漂移检测只覆盖「写入后项目块被改」，不覆盖「技能模板已升级、
项目块仍是旧渲染」——manifest blockSha256 与项目块一致时 init --check / doctor
全绿，2.2.14 模板新增「必核 guard 存在」靠人工 --refresh-block 才发现。

覆盖：stale 夹具真实报出（manifest 存值与项目实际一致）、一致场景不误报、
模板不可读 skip、既有 manifest.hash.block / agents.block 检查不回退。
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))
_spec = importlib.util.spec_from_file_location("init_2215_mod", SCRIPTS / "init.py")
init_lib = importlib.util.module_from_spec(_spec)
sys.modules["init_2215_mod"] = init_lib
_spec.loader.exec_module(init_lib)

DRIFT_MARK = "template-drift"
REFRESH_HINT = "--refresh-block --confirm"


def make_skill(target: Path) -> Path:
    """复制当前技能为最小源（scripts+templates+SKILL.md）。"""
    target.mkdir(parents=True)
    for name in ("scripts", "templates"):
        shutil.copytree(SKILL / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copy(SKILL / "SKILL.md", target / "SKILL.md")
    return target


class AgentsDriftTest(unittest.TestCase):
    """项目由当前技能模板 init；随后只改技能模板，项目块保持旧渲染。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.skill = make_skill(self.base / "skill")
        self.root = self.base / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        proc = self.run_script(self.skill / "scripts/init.py", "--yes")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # ---------- 夹具工具 ----------

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), *args],
            capture_output=True, text=True)

    def init_check(self) -> subprocess.CompletedProcess:
        return self.run_script(self.skill / "scripts/init.py", "--check")

    def doctor(self, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(self.skill / "scripts/doctor.py", *args)

    def doctor_report(self, *args: str) -> tuple[subprocess.CompletedProcess, dict]:
        proc = self.doctor(*(args + ("--json",)))
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"doctor stdout 非 JSON：rc={proc.returncode}\n"
                      f"{proc.stdout}\n{proc.stderr}")
        return proc, report

    def item(self, report: dict, item_id: str) -> dict:
        for row in report["items"]:
            if row["id"] == item_id:
                return row
        self.fail(f"缺少 {item_id}：{[row['id'] for row in report['items']]}")

    def upgrade_template(self) -> None:
        """模拟技能升级：当前模板新增内容，项目块保持旧渲染。"""
        path = self.skill / "templates/AGENTS-编排节.md"
        text = path.read_text(encoding="utf-8")
        marker = "<!-- agent-crew:orchestration:end -->"
        path.write_text(
            text.replace(marker, "- T35 模板新增行（模拟技能升级）\n" + marker, 1),
            encoding="utf-8")

    def manifest_block_sha(self) -> str:
        manifest = json.loads(
            (self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))
        return str([entry for entry in manifest["entries"]
                    if entry.get("kind") == "block"][0]["blockSha256"])

    def project_block_sha(self) -> str:
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        return init_lib.sha256_text(init_lib.marker_scan(text)["body"])

    # ---------- init --check ----------

    def test_stale_template_reports_drift(self):
        # 前置：manifest 存值与项目实际块一致（旧检测看不到漂移）
        self.assertEqual(self.manifest_block_sha(), self.project_block_sha())
        self.upgrade_template()
        proc = self.init_check()
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("[过期]", proc.stdout)
        self.assertIn(DRIFT_MARK, proc.stdout)
        self.assertIn(REFRESH_HINT, proc.stdout)
        # 仍是「manifest 与项目块一致」，只是渲染源已变
        self.assertEqual(self.manifest_block_sha(), self.project_block_sha())

    def test_consistent_block_not_reported(self):
        proc = self.init_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn(DRIFT_MARK, proc.stdout)
        self.assertIn("0 项过期", proc.stdout)

    def test_missing_template_skips_without_false_positive(self):
        (self.skill / "templates/AGENTS-编排节.md").unlink()
        proc = self.init_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn(DRIFT_MARK, proc.stdout)

    # ---------- doctor ----------

    def test_doctor_warns_on_drift_and_existing_checks_stay_pass(self):
        self.upgrade_template()
        proc, report = self.doctor_report("--only", "agents.drift")
        drift = self.item(report, "agents.drift")
        self.assertEqual("warn", drift["status"], drift)
        self.assertIn("模板", drift["detail"])
        self.assertIn(REFRESH_HINT, drift["next"])
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        strict = self.doctor("--only", "agents.drift", "--strict", "--json")
        self.assertEqual(1, strict.returncode, strict.stdout + strict.stderr)
        # 既有检查不回退：manifest 与项目块一致 → 两项仍 pass
        _, report = self.doctor_report(
            "--only", "manifest.hash.block", "--only", "agents.block")
        self.assertEqual("pass", self.item(report, "manifest.hash.block")["status"])
        self.assertEqual("pass", self.item(report, "agents.block")["status"])

    def test_doctor_consistent_passes(self):
        _, report = self.doctor_report("--only", "agents.drift")
        item = self.item(report, "agents.drift")
        self.assertEqual("pass", item["status"], item)
        self.assertEqual("", item["next"])

    def test_doctor_missing_template_skips(self):
        (self.skill / "templates/AGENTS-编排节.md").unlink()
        proc, report = self.doctor_report("--only", "agents.drift", "--strict")
        self.assertEqual("skip", self.item(report, "agents.drift")["status"])
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
