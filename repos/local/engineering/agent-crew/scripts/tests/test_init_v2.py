#!/usr/bin/env python3
"""init.py v2.1：配置保留/版本戳/--check 语义/install-log/角色项目修改/绝对路径 WARN。"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_v2_mod", _SCRIPTS / "init.py")
init_v2 = importlib.util.module_from_spec(_spec)
sys.modules["init_v2_mod"] = init_v2
_spec.loader.exec_module(init_v2)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class InitV2Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)

    def run_init(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = init_v2.run(["--root", str(self.root), *extra])
        return code, out.getvalue()

    def test_existing_config_values_preserved_version_stamped(self):
        self.assertEqual(self.run_init("--yes")[0], 0)
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["projectName"] = "自定义项目"
        cfg["helm"]["tabLabel"] = "自定义总舵"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        cfg["skillVersion"] = "0.0.1"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        code, _ = self.run_init("--yes")
        self.assertEqual(code, 0)
        updated = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["projectName"], "自定义项目")
        self.assertEqual(updated["helm"]["tabLabel"], "自定义总舵")
        self.assertEqual(updated["skillVersion"], init_v2.read_skill_version())

    def test_check_missing_config_returns_pending_without_writes(self):
        before = sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*"))
        code, out = self.run_init("--check")
        self.assertEqual(code, 2)
        self.assertIn("未安装", out)
        self.assertEqual(
            sorted(p.relative_to(self.root).as_posix() for p in self.root.rglob("*")), before)

    def test_check_detects_config_version_mismatch(self):
        self.run_init("--yes")
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillVersion"] = "9.9.9"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        code, out = self.run_init("--check")
        self.assertEqual(code, 1)
        self.assertIn("skillVersion", out)

    def test_role_project_edit_not_drift(self):
        self.run_init("--yes")
        role = self.root / ".qoder/agents/builder.md"
        role.write_text(role.read_text(encoding="utf-8") + "\n项目补充\n", encoding="utf-8")
        code, out = self.run_init("--check")
        self.assertEqual(code, 0, out)
        self.assertIn("项目修改", out)

    def test_install_log_fields_and_result(self):
        self.run_init("--yes")
        line = (self.root / "docs/crew/install-log.jsonl").read_text(
            encoding="utf-8").splitlines()[0]
        entry = json.loads(line)
        for key in ("ts", "action", "projectId", "skillVersion", "configHash",
                    "root", "files", "conflicts", "result"):
            self.assertIn(key, entry)
        self.assertEqual(entry["action"], "init")
        self.assertEqual(entry["result"], "ok")

    def test_absolute_skill_root_warns(self):
        self.run_init("--yes")
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertTrue(Path(cfg["skillRoot"]).is_absolute())
        proc = subprocess.run(
            [sys.executable, str(self.root / "scripts/doctor.py"), "--root", str(self.root),
             "--only", "skill.source", "--json"],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn('"status": "warn"', proc.stdout)
        self.assertIn('"warn": 1', proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
