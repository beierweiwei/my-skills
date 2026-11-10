#!/usr/bin/env python3
"""upgrade.py v2.1：只替换受管脚本、备份/逐字节回滚、无 3-way/baseline。

夹具：把当前技能复制为 v1/v2 两个最小源（scripts+templates+SKILL.md），v2 只改
scripts/dispatch.py 与版本号；用 v1 安装、v2 升级与回滚。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_fixture_skill(target: Path, version: str, change_dispatch: bool = False) -> Path:
    target.mkdir(parents=True)
    for name in ("scripts", "templates"):
        shutil.copytree(SKILL / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    (target / "SKILL.md").write_text(
        re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M),
        encoding="utf-8")
    if change_dispatch:
        dispatch = target / "scripts/dispatch.py"
        dispatch.write_text(dispatch.read_text(encoding="utf-8") + "\n# v2 change\n",
                            encoding="utf-8")
    return target


class UpgradeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.v1 = make_fixture_skill(self.base / "skill-v1", "2.0.0")
        self.v2 = make_fixture_skill(self.base / "skill-v2", "2.1.0", change_dispatch=True)
        self.root = self.base / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        proc = subprocess.run(
            [sys.executable, str(self.v1 / "scripts/init.py"), "--root", str(self.root), "--yes"],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def upgrade(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.v2 / "scripts/upgrade.py"), "--root", str(self.root), *args],
            capture_output=True, text=True)

    def backups(self) -> list[Path]:
        common = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        directory = Path(common) / "crew" / "backups"
        return sorted(directory.iterdir()) if directory.is_dir() else []

    def test_diff_apply_rollback(self):
        dispatch = self.root / "scripts/dispatch.py"
        h0 = sha(dispatch)
        agents = self.root / "AGENTS.md"
        role = self.root / ".qoder/agents/helm.md"
        agents_sha, role_sha = sha(agents), sha(role)

        diff = self.upgrade("--diff", "--json")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        report = json.loads(diff.stdout)
        self.assertIn("scripts/dispatch.py", report["changed"])
        self.assertEqual(report["skillVersion"]["installed"], "2.0.0")
        self.assertEqual(report["skillVersion"]["source"], "2.1.0")

        without_confirm = self.upgrade("--apply")
        self.assertEqual(without_confirm.returncode, 2)
        self.assertEqual(sha(dispatch), h0)

        before_backups = len(self.backups())
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        h1 = sha(dispatch)
        self.assertNotEqual(h1, h0)
        self.assertGreater(len(self.backups()), before_backups)
        latest = self.backups()[-1]
        self.assertTrue((latest / "backup-manifest.json").is_file())
        self.assertEqual(sha(agents), agents_sha)
        self.assertEqual(sha(role), role_sha)
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["skillVersion"], "2.1.0")
        self.assertFalse((self.root / "docs/crew-rules.md").exists())

        rolled = self.upgrade("--rollback", latest.name)
        self.assertEqual(rolled.returncode, 0, rolled.stderr)
        self.assertEqual(sha(dispatch), h0)
        cfg = json.loads((self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["skillVersion"], "2.0.0")
        doctor = subprocess.run(
            [sys.executable, str(self.root / "scripts/doctor.py"), "--root", str(self.root),
             "--only", "manifest.hash.file", "--json"],
            capture_output=True, text=True)
        self.assertEqual(doctor.returncode, 0, doctor.stdout)

    def test_rollback_unknown_backup_fails(self):
        proc = self.upgrade("--rollback", "no-such-backup")
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
