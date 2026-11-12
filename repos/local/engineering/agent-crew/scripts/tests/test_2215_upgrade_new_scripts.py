#!/usr/bin/env python3
"""2.2.15 T34/B-1：upgrade 安装源新增受管脚本（2.2.14 O1 闭环死路）。

夹具按真实发布顺序：项目由 v1 安装 → 技能源原地刷新为 v2（新增
scripts/new-tool.py，SCRIPT_MANIFEST 同步新增，版本 2.0.0→2.1.0）→ 用项目已安装的
scripts/upgrade.py 升级。项目内 upgrade.py 默认与当前源同版（新版）；「pre-T34 旧
入口首升」由 test_first_run_old_upgrade_... 显式把安装副本降级为旧语义模拟。
upgrade 必须读取「安装源 SCRIPT_MANIFEST ∪ manifest entries」而不是只看已安装清单。
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "upgrade_2215_mod", SKILL / "scripts" / "upgrade.py")
upgrade_mod = importlib.util.module_from_spec(_spec)
sys.modules["upgrade_2215_mod"] = upgrade_mod
_spec.loader.exec_module(upgrade_mod)
NEW_SCRIPT = "new-tool.py"
NEW_BODY = '#!/usr/bin/env python3\nprint("new-tool")\n'


def sha_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_skill(target: Path, version: str) -> Path:
    """复制当前技能为最小源（scripts+templates+SKILL.md），版本号改成 version。"""
    target.mkdir(parents=True)
    for name in ("scripts", "templates"):
        shutil.copytree(SKILL / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    (target / "SKILL.md").write_text(
        re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M),
        encoding="utf-8")
    return target


def set_version(skill: Path, version: str) -> None:
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    (skill / "SKILL.md").write_text(
        re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M),
        encoding="utf-8")


def activate_new_source(skill: Path, version: str = "2.1.0",
                        modify: str | None = None) -> None:
    """源原地刷新：新增脚本 + SCRIPT_MANIFEST 登记 + 版本 bump（可选改一个既有脚本）。"""
    set_version(skill, version)
    init_path = skill / "scripts/init.py"
    text = init_path.read_text(encoding="utf-8")
    init_path.write_text(
        text.replace('SCRIPT_MANIFEST = [\n',
                     f'SCRIPT_MANIFEST = [\n    "{NEW_SCRIPT}",\n', 1),
        encoding="utf-8")
    (skill / "scripts" / NEW_SCRIPT).write_text(NEW_BODY, encoding="utf-8")
    if modify:
        target = skill / "scripts" / modify
        target.write_text(target.read_text(encoding="utf-8") + "\n# v2 change\n",
                          encoding="utf-8")


class UpgradeNewScriptTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.skill = make_skill(self.base / "skill", "2.0.0")
        self.root = self.base / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)],
                       check=True, capture_output=True)
        proc = subprocess.run(
            [sys.executable, str(self.skill / "scripts/init.py"),
             "--root", str(self.root), "--yes"],
            capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # ---------- 夹具工具 ----------

    def upgrade(self, *args: str) -> subprocess.CompletedProcess:
        """用项目已安装的 upgrade.py（真实发布入口）操作当前技能源。"""
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/upgrade.py"),
             "--root", str(self.root), *args],
            capture_output=True, text=True)

    def manifest(self) -> dict:
        return json.loads(
            (self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))

    def config(self) -> dict:
        return json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))

    def file_entries(self) -> dict[str, dict]:
        return {
            str(entry.get("path")): entry
            for entry in self.manifest().get("entries", [])
            if isinstance(entry, dict) and entry.get("kind") == "file"
        }

    def install_log_rows(self) -> list[dict]:
        return [
            json.loads(line) for line in
            (self.root / "docs/crew/install-log.jsonl").read_text(
                encoding="utf-8").splitlines()
        ]

    def backups(self) -> list[Path]:
        common = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "--path-format=absolute",
             "--git-common-dir"], capture_output=True, text=True).stdout.strip()
        directory = Path(common) / "crew" / "backups"
        return sorted(directory.iterdir()) if directory.is_dir() else []

    def doctor_scripts_source(self, *extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.root / "scripts/doctor.py"),
             "--root", str(self.root), "--only", "install.scripts_source", *extra],
            capture_output=True, text=True)

    def make_installed_upgrade_legacy(self) -> None:
        """把项目内安装副本降级为 pre-T34 语义（只认本进程 init 清单）。

        真实旧二进制无法由新源码重建；这里只在已安装的真实脚本里插入旧语义
        返回，其余代码保持原样，复现「首升不识别源新增脚本」。
        """
        path = self.root / "scripts" / "upgrade.py"
        text = path.read_text(encoding="utf-8")
        marker = "def source_script_manifest(source_dir: Path) -> list[str]:"
        self.assertIn(marker, text)
        head, tail = text.split(marker, 1)
        path.write_text(
            head + marker + "\n    return list(init_lib.SCRIPT_MANIFEST)" + tail,
            encoding="utf-8")

    # ---------- 用例 ----------

    def test_apply_installs_new_script_and_registers_entry(self):
        activate_new_source(self.skill)
        proc = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        installed = self.root / "scripts" / NEW_SCRIPT
        self.assertTrue(installed.is_file(), proc.stdout + proc.stderr)
        self.assertEqual(sha_text(self.skill / "scripts" / NEW_SCRIPT),
                         sha_text(installed))
        entries = self.file_entries()
        self.assertIn(f"scripts/{NEW_SCRIPT}", entries)
        self.assertEqual(sha_text(installed), entries[f"scripts/{NEW_SCRIPT}"]["sha256"])
        self.assertEqual("2.1.0", self.manifest()["skillVersion"])
        self.assertEqual("2.1.0", self.config()["skillVersion"])
        self.assertIn("个受管脚本更新", proc.stdout)
        self.assertIn("补登 1 个受管脚本条目", proc.stdout)

    def test_doctor_scripts_source_passes_after_upgrade(self):
        activate_new_source(self.skill)
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        doctor = self.doctor_scripts_source("--strict", "--json")
        self.assertEqual(0, doctor.returncode, doctor.stdout + doctor.stderr)
        payload = json.loads(doctor.stdout)
        item = payload["items"][0]
        self.assertEqual("install.scripts_source", item["id"])
        self.assertEqual("pass", item["status"], item["detail"])

    def test_diff_lists_new_script_missing_and_writes_nothing(self):
        activate_new_source(self.skill)
        before_manifest = sha_file(self.root / "docs/crew/manifest.json")
        before_config = sha_file(self.root / ".planning/orchestration.json")
        proc = self.upgrade("--diff", "--json")
        self.assertEqual(0, proc.returncode, proc.stderr)
        report = json.loads(proc.stdout)
        rows = {row["path"]: row for row in report["scripts"]}
        self.assertIn(f"scripts/{NEW_SCRIPT}", rows)
        self.assertEqual("missing", rows[f"scripts/{NEW_SCRIPT}"]["status"])
        self.assertIn(f"scripts/{NEW_SCRIPT}", report["changed"])
        self.assertFalse((self.root / "scripts" / NEW_SCRIPT).exists())
        self.assertEqual(before_manifest, sha_file(self.root / "docs/crew/manifest.json"))
        self.assertEqual(before_config,
                         sha_file(self.root / ".planning/orchestration.json"))

    def test_rollback_removes_new_script_entry_and_restores_version(self):
        dispatch_before = sha_text(self.root / "scripts/dispatch.py")
        activate_new_source(self.skill)
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        latest = self.backups()[-1]
        backup_manifest = json.loads(
            (latest / "backup-manifest.json").read_text(encoding="utf-8"))
        added = [item.get("path") for item in backup_manifest.get("addedFiles", [])]
        self.assertEqual([f"scripts/{NEW_SCRIPT}"], added)

        rolled = self.upgrade("--rollback", latest.name)
        self.assertEqual(0, rolled.returncode, rolled.stdout + rolled.stderr)
        self.assertFalse((self.root / "scripts" / NEW_SCRIPT).exists())
        self.assertTrue((latest / "removed" / "scripts" / NEW_SCRIPT).is_file())
        self.assertNotIn(f"scripts/{NEW_SCRIPT}", self.file_entries())
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])
        self.assertEqual(dispatch_before, sha_text(self.root / "scripts/dispatch.py"))
        last = self.install_log_rows()[-1]
        self.assertEqual("rollback", last["action"])
        removed = [row["path"] for row in last["files"]
                   if row.get("status") == "removed"]
        self.assertEqual([f"scripts/{NEW_SCRIPT}"], removed)

    def test_rollback_refuses_when_new_script_modified(self):
        activate_new_source(self.skill)
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        latest = self.backups()[-1]
        installed = self.root / "scripts" / NEW_SCRIPT
        installed.write_text(installed.read_text(encoding="utf-8") + "\n# local edit\n",
                             encoding="utf-8")
        rolled = self.upgrade("--rollback", latest.name)
        self.assertEqual(1, rolled.returncode, rolled.stdout + rolled.stderr)
        self.assertIn("已被修改", rolled.stderr)
        self.assertTrue(installed.is_file())
        self.assertEqual("2.1.0", self.config()["skillVersion"])

    def test_first_run_old_upgrade_partially_installs_then_second_run_completes(self):
        # 真实 2.2.14→2.2.15 发布顺序：项目内是 pre-T34 upgrade（只认本进程清单），
        # 源新增 new-tool.py 且 upgrade.py 自身有更新。首跑复制旧清单内脚本后即写
        # 版本（新脚本漏装），项目内 upgrade 已被换成新版；第二次运行才补装。
        self.make_installed_upgrade_legacy()
        activate_new_source(self.skill, modify="upgrade.py")
        first = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        self.assertFalse((self.root / "scripts" / NEW_SCRIPT).exists(),
                         first.stdout + first.stderr)
        self.assertEqual("2.1.0", self.config()["skillVersion"])
        self.assertNotIn("补登", first.stdout)

        # 首升后项目内 doctor/init 已是新版：必须报「项目缺失」并提示再跑 upgrade
        doctor = self.doctor_scripts_source("--strict", "--json")
        self.assertEqual(1, doctor.returncode, doctor.stdout + doctor.stderr)
        item = json.loads(doctor.stdout)["items"][0]
        self.assertEqual("warn", item["status"], item)
        self.assertIn(f"项目缺失 1 项：scripts/{NEW_SCRIPT}", item["detail"])
        self.assertIn("再跑一次 upgrade", item["next"])

        # 第二次运行：项目 upgrade.py 已是新版，按源清单补装并补登 entries
        second = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertTrue((self.root / "scripts" / NEW_SCRIPT).is_file(),
                        second.stdout + second.stderr)
        self.assertIn("补登 1 个受管脚本条目", second.stdout)
        doctor = self.doctor_scripts_source("--strict", "--json")
        self.assertEqual(0, doctor.returncode, doctor.stdout + doctor.stderr)
        item = json.loads(doctor.stdout)["items"][0]
        self.assertEqual("pass", item["status"], item["detail"])
        # 受管脚本数随清单增长（2.2.16 新增受管脚本），按源清单动态核对，不写死数字
        expected = len(upgrade_mod.source_script_manifest(self.skill))
        self.assertIn(f"核对 {expected} 项", item["detail"])

    def test_doctor_sees_source_new_script_with_stale_project_init(self):
        # 未运行 upgrade：项目内 init 清单滞后一代；doctor 必须读源侧 AST 清单，
        # 否则只会报 init.py 内容不一致而漏报「项目缺失 new-tool.py」（B-1 复现 B）。
        activate_new_source(self.skill)
        doctor = self.doctor_scripts_source("--strict", "--json")
        self.assertEqual(1, doctor.returncode, doctor.stdout + doctor.stderr)
        item = json.loads(doctor.stdout)["items"][0]
        self.assertEqual("warn", item["status"], item)
        self.assertIn(f"项目缺失 1 项：scripts/{NEW_SCRIPT}", item["detail"])

    def test_recheck_covers_source_only_scripts(self):
        activate_new_source(self.skill)
        names = upgrade_mod.managed_script_names(
            self.manifest(),
            [*upgrade_mod.source_script_manifest(self.skill),
             *upgrade_mod.init_lib.SCRIPT_MANIFEST])
        self.assertIn(NEW_SCRIPT, names)
        self.assertEqual(
            [f"scripts/{NEW_SCRIPT}"],
            upgrade_mod.managed_script_mismatches(
                self.root, self.skill, [NEW_SCRIPT]))
        shutil.copyfile(self.skill / "scripts" / NEW_SCRIPT,
                        self.root / "scripts" / NEW_SCRIPT)
        self.assertEqual(
            [], upgrade_mod.managed_script_mismatches(
                self.root, self.skill, [NEW_SCRIPT]))

    def test_mixed_new_and_modified_scripts_install_and_rollback(self):
        dispatch_before = sha_text(self.root / "scripts/dispatch.py")
        activate_new_source(self.skill, modify="dispatch.py")
        applied = self.upgrade("--apply", "--confirm")
        self.assertEqual(0, applied.returncode, applied.stdout + applied.stderr)
        self.assertTrue((self.root / "scripts" / NEW_SCRIPT).is_file())
        self.assertNotEqual(dispatch_before, sha_text(self.root / "scripts/dispatch.py"))
        entries = self.file_entries()
        self.assertIn(f"scripts/{NEW_SCRIPT}", entries)
        self.assertEqual(sha_text(self.root / "scripts/dispatch.py"),
                         entries["scripts/dispatch.py"]["sha256"])

        latest = self.backups()[-1]
        rolled = self.upgrade("--rollback", latest.name)
        self.assertEqual(0, rolled.returncode, rolled.stdout + rolled.stderr)
        self.assertFalse((self.root / "scripts" / NEW_SCRIPT).exists())
        self.assertEqual(dispatch_before, sha_text(self.root / "scripts/dispatch.py"))
        self.assertEqual("2.0.0", self.config()["skillVersion"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
