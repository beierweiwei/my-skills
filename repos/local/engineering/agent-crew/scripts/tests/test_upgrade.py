#!/usr/bin/env python3
"""upgrade.py v2.1：只替换受管脚本、备份/逐字节回滚、无 3-way/baseline。

夹具：把当前技能复制为 v1/v2 两个最小源（scripts+templates+SKILL.md），v2 只改
scripts/dispatch.py 与版本号；用 v1 安装、v2 升级与回滚。
"""

from __future__ import annotations

import hashlib
import json
import os
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
        return self.upgrade_from(self.v2, *args)

    def upgrade_from(self, skill: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(skill / "scripts/upgrade.py"), "--root", str(self.root), *args],
            capture_output=True, text=True)

    def make_version_only_skill(self, version: str) -> Path:
        """复制 v1 脚本（零脚本差异）只改 SKILL.md 版本，模拟版本-only 升级。"""
        target = self.base / f"skill-{version}"
        shutil.copytree(self.v1, target, dirs_exist_ok=True)
        text = (target / "SKILL.md").read_text(encoding="utf-8")
        (target / "SKILL.md").write_text(
            re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M),
            encoding="utf-8")
        return target

    def install_log_rows(self) -> list[dict]:
        return [
            json.loads(line) for line in
            (self.root / "docs/crew/install-log.jsonl").read_text(
                encoding="utf-8").splitlines()
        ]

    def manifest(self) -> dict:
        return json.loads(
            (self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))

    def config(self) -> dict:
        return json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))

    def apply_version_only(self, version: str = "2.0.1") -> subprocess.CompletedProcess:
        return self.upgrade_from(self.make_version_only_skill(version), "--apply", "--confirm")

    def backups(self) -> list[Path]:
        common = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        directory = Path(common) / "crew" / "backups"
        return sorted(directory.iterdir()) if directory.is_dir() else []

    def crew_dir(self) -> Path:
        common = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        return Path(common) / "crew"

    def test_apply_prints_guard_hint_when_absent(self):
        applied = self.apply_version_only()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn("upgrade 完成", applied.stdout)
        self.assertIn("提示：未检测到值守 guard", applied.stdout)

    def test_apply_guard_hint_silent_when_alive(self):
        crew = self.crew_dir()
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "guard.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
        applied = self.apply_version_only()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertIn("upgrade 完成", applied.stdout)
        self.assertNotIn("提示：未检测到值守 guard", applied.stdout)

    def test_apply_source_missing_script_fails_without_version_bump(self):
        # T33 覆盖集：源缺受管脚本 → rc=1、版本不变、零写入（不得早退假绿）
        broken = self.base / "skill-missing-script"
        shutil.copytree(self.v2, broken)
        (broken / "scripts/crew-msg.py").unlink()
        before_manifest = sha(self.root / "docs/crew/manifest.json")
        before_config = sha(self.root / ".planning/orchestration.json")
        proc = self.upgrade_from(broken, "--apply", "--confirm")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("安装源缺失受管脚本", proc.stderr)
        self.assertIn("scripts/crew-msg.py", proc.stderr)
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])
        self.assertEqual(before_manifest, sha(self.root / "docs/crew/manifest.json"))
        self.assertEqual(before_config, sha(self.root / ".planning/orchestration.json"))

    def test_apply_noop_prints_guard_hint_when_absent(self):
        applied = self.apply_version_only()
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        again = self.upgrade_from(
            self.make_version_only_skill("2.0.1"), "--apply", "--confirm")
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)
        self.assertIn("已是最新", again.stdout)
        self.assertIn("提示：未检测到值守 guard", again.stdout)

    def test_apply_noop_guard_hint_silent_when_alive(self):
        crew = self.crew_dir()
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "guard.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
        applied = self.apply_version_only()
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        again = self.upgrade_from(
            self.make_version_only_skill("2.0.1"), "--apply", "--confirm")
        self.assertEqual(0, again.returncode, again.stdout + again.stderr)
        self.assertIn("已是最新", again.stdout)
        self.assertNotIn("提示：未检测到值守 guard", again.stdout)

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

    def test_diff_report_follows_manifest_entries(self):
        # D：diff_report 以 manifest entries（kind:file 且 scripts/*.py|*.sh）为准，
        # 不再固定遍历 init_lib.SCRIPT_MANIFEST。
        manifest_path = self.root / "docs/crew/manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["entries"] = [
            entry for entry in manifest["entries"]
            if entry.get("path") == "scripts/dispatch.py"
        ]
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                                 encoding="utf-8")
        diff = self.upgrade("--diff", "--json")
        self.assertEqual(diff.returncode, 0, diff.stderr)
        report = json.loads(diff.stdout)
        self.assertEqual(["scripts/dispatch.py"],
                         [row["path"] for row in report["scripts"]])
        self.assertEqual(["scripts/dispatch.py"], report["changed"])

    def test_apply_install_log_next_not_stale(self):
        # D：--apply 已自动同步 config 的 skillVersion，install-log next 不再要求手改。
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(applied.returncode, 0, applied.stderr)
        rows = [
            json.loads(line) for line in
            (self.root / "docs/crew/install-log.jsonl").read_text(
                encoding="utf-8").splitlines()
        ]
        upgrades = [row for row in rows if row.get("action") == "upgrade"]
        self.assertTrue(upgrades, rows)
        next_text = upgrades[-1]["next"]
        self.assertNotIn("如需版本戳一致", next_text)
        self.assertIn("已自动同步", next_text)
        self.assertIn("--refresh-block", next_text)

    def test_docstring_matches_apply_config_scope(self):
        # D：docstring 明确 --apply 只同步 config 的 skillVersion，不再声称不改配置文件。
        text = (self.v2 / "scripts/upgrade.py").read_text(encoding="utf-8")
        doc = text.split('"""', 2)[1]
        self.assertIn("skillVersion", doc)
        self.assertIn("仅此一项", doc)
        self.assertNotIn("不做路径/配置迁移；不改 orchestration.json", doc)

    def test_apply_syncs_version_when_scripts_unchanged(self):
        # F1：零脚本差异但版本戳落后时，仍需锁内同步 config/manifest 版本并写 install-log。
        dispatch = self.root / "scripts/dispatch.py"
        h0 = sha(dispatch)
        before_backups = len(self.backups())

        applied = self.apply_version_only()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        self.assertEqual(sha(dispatch), h0)  # 脚本未动
        self.assertEqual(self.config()["skillVersion"], "2.0.1")
        self.assertEqual(self.manifest()["skillVersion"], "2.0.1")
        upgrades = [row for row in self.install_log_rows() if row.get("action") == "upgrade"]
        self.assertTrue(upgrades)
        self.assertEqual(upgrades[-1]["files"], [])
        self.assertIn("已自动同步", upgrades[-1]["next"])
        self.assertEqual(len(self.backups()), before_backups + 1)
        backup_manifest = json.loads(
            (self.backups()[-1] / "backup-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(backup_manifest["skillVersionBefore"], "2.0.0")
        self.assertIn(".planning/orchestration.json",
                      [entry["path"] for entry in backup_manifest["files"]])

    def test_apply_version_only_is_noop_when_versions_aligned(self):
        applied = self.apply_version_only()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        before_backups = len(self.backups())
        before_log = len(self.install_log_rows())
        before_cfg = sha(self.root / ".planning/orchestration.json")
        before_manifest = sha(self.root / "docs/crew/manifest.json")

        again = self.upgrade_from(
            self.make_version_only_skill("2.0.1"), "--apply", "--confirm")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("已是最新", again.stdout)
        self.assertEqual(len(self.backups()), before_backups)  # 不再备份
        self.assertEqual(len(self.install_log_rows()), before_log)  # 不再写日志
        self.assertEqual(sha(self.root / ".planning/orchestration.json"), before_cfg)
        self.assertEqual(sha(self.root / "docs/crew/manifest.json"), before_manifest)

    def test_rollback_restores_version_after_version_only_apply(self):
        # 版本-only 升级的备份可回滚版本戳（无脚本差异时仍可恢复）。
        applied = self.apply_version_only()
        self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
        latest = self.backups()[-1]

        rolled = self.upgrade("--rollback", latest.name)
        self.assertEqual(rolled.returncode, 0, rolled.stdout + rolled.stderr)
        self.assertEqual(self.config()["skillVersion"], "2.0.0")
        self.assertEqual(self.manifest()["skillVersion"], "2.0.0")

    def test_rollback_unknown_backup_fails(self):
        proc = self.upgrade("--rollback", "no-such-backup")
        self.assertEqual(proc.returncode, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
