#!/usr/bin/env python3
"""2.2.14 T33：版本假绿防护（FB-031）。

CASE 场景：项目 manifest=v1 + 项目脚本旧 + 技能源 v2 且脚本内容不同。
- `init --check` / `init` 均 rc=1、零写入、提示先 upgrade；
- 脚本对齐后 init 正常打新版本、doctor `install.scripts_source` pass 不误报；
- 同版本本地差异仅提示（新检查不升 rc）；全新项目（无 manifest）不拦截；
- upgrade 复制后复核不一致 → rc=1 且不写新版本。
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))  # upgrade.py 以顶层名 import init
_spec = importlib.util.spec_from_file_location("upgrade_2214_mod", SCRIPTS / "upgrade.py")
upgrade_mod = importlib.util.module_from_spec(_spec)
sys.modules["upgrade_2214_mod"] = upgrade_mod
_spec.loader.exec_module(upgrade_mod)
init_lib = upgrade_mod.init_lib

UPGRADE_HINT = "请先运行 python3 scripts/upgrade.py --apply --confirm"
DRIFT_WARNING = "受管脚本与安装源存在本地差异"


def make_skill(target: Path, version: str, change: str = "") -> Path:
    """复制当前技能为最小源（scripts+templates+SKILL.md），可改一个受管脚本模拟 v2。"""
    target.mkdir(parents=True)
    for name in ("scripts", "templates"):
        shutil.copytree(SKILL / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    (target / "SKILL.md").write_text(
        re.sub(r"^version: .*$", f"version: {version}", text, count=1, flags=re.M),
        encoding="utf-8")
    if change:
        path = target / "scripts" / change
        path.write_text(path.read_text(encoding="utf-8") + "\n# v2 marker\n",
                        encoding="utf-8")
    return target


class ManifestGuardCaseTest(unittest.TestCase):
    """v1 安装 → v2 源（改 scripts/crew-state.py）。"""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.base = Path(cls._tmp.name)
        cls.v1 = make_skill(cls.base / "skill-v1", "2.0.0")
        cls.v2 = make_skill(cls.base / "skill-v2", "2.1.0", change="crew-state.py")

    def setUp(self):
        self.root = self.base / f"proj-{self._testMethodName}"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        proc = self.init_from(self.v1, "--yes")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])

    # ---------- 夹具工具 ----------

    def run_script(self, script: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(script), "--root", str(self.root), *args],
            capture_output=True, text=True)

    def init_from(self, skill: Path, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(skill / "scripts/init.py", *args)

    def upgrade_from(self, skill: Path, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(skill / "scripts/upgrade.py", *args)

    def refresh_from(self, skill: Path, *args: str) -> subprocess.CompletedProcess:
        return self.run_script(skill / "scripts/init.py", "--refresh-block", *args)

    def make_block_stale(self) -> None:
        """在受管块内插一行，制造「块需重渲染」且标记合法。"""
        agents = self.root / "AGENTS.md"
        text = agents.read_text(encoding="utf-8")
        agents.write_text(
            text.replace(init_lib.MARK_END, f"stale line\n{init_lib.MARK_END}", 1),
            encoding="utf-8")

    def doctor(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SKILL / "scripts/doctor.py"),
             "--root", str(self.root), *args],
            capture_output=True, text=True)

    def doctor_item(self, *args: str) -> tuple[int, dict]:
        proc = self.doctor(*(args + ("--json",)))
        try:
            report = json.loads(proc.stdout)
        except ValueError:
            self.fail(f"doctor stdout 非 JSON：rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}")
        for item in report["items"]:
            if item["id"] == "install.scripts_source":
                return proc.returncode, item
        self.fail(f"缺少 install.scripts_source：{report['items']}")

    def manifest(self) -> dict:
        return json.loads(
            (self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))

    def config(self) -> dict:
        return json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))

    def snapshot(self) -> dict[str, str]:
        rels = ["AGENTS.md", ".planning/orchestration.json",
                "docs/crew/manifest.json", "docs/crew/install-log.jsonl"]
        rels += [f"scripts/{path.name}" for path in (self.root / "scripts").iterdir()]
        return {
            rel: hashlib.sha256((self.root / rel).read_bytes()).hexdigest()
            for rel in rels if (self.root / rel).is_file()
        }

    def align_scripts(self) -> None:
        """模拟 upgrade：把 v2 受管脚本复制到项目（不动 manifest/版本）。"""
        for name in init_lib.SCRIPT_MANIFEST:
            origin = self.v2 / "scripts" / name
            if origin.is_file():
                shutil.copyfile(origin, self.root / "scripts" / name)

    def make_crlf_skill(self, name: str, version: str) -> Path:
        """复制 v1 为源并把 scripts/ 全部转 CRLF（模拟 Windows 源）。"""
        target = self.base / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(self.v1, target)
        for path in (target / "scripts").iterdir():
            if path.is_file():
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
        skill_md = target / "SKILL.md"
        skill_md.write_text(
            re.sub(r"^version: .*$", f"version: {version}",
                   skill_md.read_text(encoding="utf-8"), count=1, flags=re.M),
            encoding="utf-8")
        return target

    # ---------- CASE 1：旧脚本 + 新源被拦 ----------

    def test_init_check_blocks_and_points_to_upgrade(self):
        proc = self.init_from(self.v2, "--check")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("受管脚本与安装源不一致", proc.stdout)
        self.assertIn(UPGRADE_HINT, proc.stdout)

    def test_init_refuses_and_writes_nothing(self):
        before = self.snapshot()
        proc = self.init_from(self.v2)
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("本次零写入", proc.stderr)
        self.assertIn(UPGRADE_HINT, proc.stderr)
        self.assertIn("2.0.0→2.1.0", proc.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])

    def test_doctor_warns_before_alignment_and_strict_fails(self):
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(self.v2))
        self.assertEqual(0, rc, item)
        self.assertEqual("warn", item["status"], item)
        self.assertIn("scripts/crew-state.py", item["detail"])
        self.assertIn("upgrade.py --apply --confirm", item["next"])
        strict = self.doctor("--only", "install.scripts_source",
                             "--check-source", str(self.v2), "--strict", "--json")
        self.assertEqual(1, strict.returncode, strict.stdout)

    # ---------- CASE 1b：覆盖集缺口（源缺失 / 项目缺失） ----------

    def test_doctor_warns_when_source_missing_managed_script(self):
        broken = self.base / "skill-source-missing"
        if broken.exists():
            shutil.rmtree(broken)
        shutil.copytree(self.v2, broken)
        (broken / "scripts/crew-msg.py").unlink()
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(broken))
        self.assertEqual(0, rc, item)
        self.assertEqual("warn", item["status"], item)
        self.assertIn("源缺失 1 项", item["detail"])
        self.assertIn("scripts/crew-msg.py", item["detail"])
        strict = self.doctor("--only", "install.scripts_source",
                             "--check-source", str(broken), "--strict", "--json")
        self.assertEqual(1, strict.returncode, strict.stdout)

    def test_doctor_warns_when_project_missing_managed_script(self):
        self.align_scripts()
        missing = self.root / "scripts/crew-msg.py"
        missing.unlink()
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(self.v2))
        self.assertEqual(0, rc, item)
        self.assertEqual("warn", item["status"], item)
        self.assertIn("项目缺失 1 项", item["detail"])
        self.assertIn("scripts/crew-msg.py", item["detail"])
        strict = self.doctor("--only", "install.scripts_source",
                             "--check-source", str(self.v2), "--strict", "--json")
        self.assertEqual(1, strict.returncode, strict.stdout)

        shutil.copyfile(self.v2 / "scripts/crew-msg.py", missing)
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(self.v2))
        self.assertEqual(0, rc, item)
        self.assertEqual("pass", item["status"], item)
        self.assertIn(f"核对 {len(init_lib.SCRIPT_MANIFEST)} 项", item["detail"])

    # ---------- CASE 2：对齐后正常 ----------

    def test_init_after_alignment_stamps_new_version(self):
        self.align_scripts()
        proc = self.init_from(self.v2, "--yes")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn(UPGRADE_HINT, proc.stdout + proc.stderr)
        self.assertEqual("2.1.0", self.manifest()["skillVersion"])
        self.assertEqual("2.1.0", self.config()["skillVersion"])
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(self.v2))
        self.assertEqual(0, rc, item)
        self.assertEqual("pass", item["status"], item)
        self.assertIn(f"核对 {len(init_lib.SCRIPT_MANIFEST)} 项", item["detail"])

    def test_upgrade_after_alignment_is_green(self):
        proc = self.upgrade_from(self.v2, "--apply", "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("upgrade 完成", proc.stdout)
        self.assertEqual("2.1.0", self.manifest()["skillVersion"])
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(self.v2))
        self.assertEqual(0, rc, item)
        self.assertEqual("pass", item["status"], item)

    # ---------- CRLF 归一化（S1） ----------

    def test_crlf_source_line_endings_are_consistent(self):
        # 同版本 + 仅 CRLF 差异：check 不因版本过期/脚本差异升 rc
        crlf = self.make_crlf_skill("skill-crlf", "2.0.0")
        self.assertEqual([], init_lib.script_source_mismatches(self.root, crlf))
        check = self.init_from(crlf, "--check")
        self.assertEqual(0, check.returncode, check.stdout + check.stderr)
        self.assertNotIn("受管脚本与安装源不一致", check.stdout + check.stderr)
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(crlf))
        self.assertEqual(0, rc, item)
        self.assertEqual("pass", item["status"], item)
        self.assertIn(f"核对 {len(init_lib.SCRIPT_MANIFEST)} 项", item["detail"])

    def test_crlf_source_real_content_change_still_blocks(self):
        crlf = self.make_crlf_skill("skill-crlf-diff", "2.1.0")
        changed = crlf / "scripts/crew-state.py"
        changed.write_bytes(changed.read_bytes() + b"# real change\r\n")
        self.assertIn("scripts/crew-state.py",
                      init_lib.script_source_mismatches(self.root, crlf))
        check = self.init_from(crlf, "--check")
        self.assertEqual(1, check.returncode, check.stdout + check.stderr)
        self.assertIn("受管脚本与安装源不一致", check.stdout)
        rc, item = self.doctor_item(
            "--only", "install.scripts_source", "--check-source", str(crlf))
        self.assertEqual(0, rc, item)
        self.assertEqual("warn", item["status"], item)
        self.assertIn("scripts/crew-state.py", item["detail"])
        strict = self.doctor("--only", "install.scripts_source",
                             "--check-source", str(crlf), "--strict", "--json")
        self.assertEqual(1, strict.returncode, strict.stdout)

    # ---------- refresh-block 旁路（工单 09） ----------

    def test_refresh_block_blocked_by_script_mismatch(self):
        # CASE：旧脚本 + 新源 + 旧 manifest + 块需重渲染 → rc=1、零写入
        self.make_block_stale()
        before = self.snapshot()
        proc = self.refresh_from(self.v2, "--confirm")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("refresh-block 拒绝写入", proc.stderr)
        self.assertIn("受管脚本与安装源不一致", proc.stderr)
        self.assertIn(UPGRADE_HINT, proc.stderr)
        self.assertIn("再重跑 --refresh-block", proc.stderr)
        self.assertIn("2.0.0→2.1.0", proc.stderr)
        self.assertIn("本次零写入", proc.stderr)
        self.assertEqual(before, self.snapshot())
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])

    def test_refresh_block_without_confirm_keeps_rc2(self):
        self.make_block_stale()
        before = self.snapshot()
        proc = self.refresh_from(self.v2)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(before, self.snapshot())

    def test_refresh_block_latest_block_stays_rc0_with_mismatch(self):
        # 块已最新 → 不写盘，不改既有 rc=0 语义
        proc = self.refresh_from(self.v2, "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("块内容已是最新", proc.stdout)
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])

    def test_refresh_block_after_alignment_stamps_new_version(self):
        self.align_scripts()
        self.make_block_stale()
        proc = self.refresh_from(self.v2, "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn(UPGRADE_HINT, proc.stdout + proc.stderr)
        self.assertEqual("2.1.0", self.manifest()["skillVersion"])

    def test_refresh_block_same_version_drift_warns_and_writes(self):
        drifted = self.root / "scripts/crew-state.py"
        drifted.write_text(drifted.read_text(encoding="utf-8") + "\n# local drift\n",
                           encoding="utf-8")
        self.make_block_stale()
        proc = self.refresh_from(self.v1, "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn(f"[警告] {DRIFT_WARNING}", proc.stdout)
        self.assertNotIn(UPGRADE_HINT, proc.stdout + proc.stderr)
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])

    # ---------- CASE 3：同版本本地差异仅提示 ----------

    def test_same_version_local_drift_only_warns(self):
        self.align_scripts()
        self.assertEqual(0, self.init_from(self.v2, "--yes").returncode)
        drifted = self.root / "scripts/crew-state.py"
        drifted.write_text(drifted.read_text(encoding="utf-8") + "\n# local drift\n",
                           encoding="utf-8")

        dry = self.init_from(self.v2, "--dry-run")
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        self.assertIn(f"[警告] {DRIFT_WARNING}", dry.stdout)
        self.assertNotIn(UPGRADE_HINT, dry.stdout + dry.stderr)

        proc = self.init_from(self.v2)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)  # 既有冲突语义不变
        self.assertIn(f"[警告] {DRIFT_WARNING}", proc.stdout)
        self.assertNotIn(UPGRADE_HINT, proc.stdout + proc.stderr)

        check = self.init_from(self.v2, "--check")
        self.assertIn(f"[风险提示] {DRIFT_WARNING}", check.stdout)
        self.assertNotIn(UPGRADE_HINT, check.stdout)

    # ---------- CASE 4：全新项目不拦截 ----------

    def test_fresh_project_not_blocked(self):
        fresh = self.base / "fresh-proj"
        if fresh.exists():
            shutil.rmtree(fresh)
        fresh.mkdir()
        subprocess.run(["git", "init", "-q", str(fresh)], check=True,
                       capture_output=True)
        proc = subprocess.run(
            [sys.executable, str(self.v2 / "scripts/init.py"), "--root", str(fresh), "--yes"],
            capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        manifest = json.loads(
            (fresh / "docs/crew/manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("2.1.0", manifest["skillVersion"])

    # ---------- upgrade：复制后复核 ----------

    def test_upgrade_aborts_when_source_missing_managed_script(self):
        broken = self.base / "skill-upgrade-missing"
        if broken.exists():
            shutil.rmtree(broken)
        shutil.copytree(self.v2, broken)
        (broken / "scripts/crew-msg.py").unlink()
        proc = self.upgrade_from(broken, "--apply", "--confirm")
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("安装源缺失受管脚本", proc.stderr)
        self.assertIn("scripts/crew-msg.py", proc.stderr)
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])

    def test_upgrade_recheck_failure_keeps_old_version(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(init_lib, "script_source_mismatches",
                               return_value=["scripts/crew-state.py"]):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = upgrade_mod.apply_upgrade(self.root, True, "2.1.0", self.v2)
        self.assertEqual(1, rc, out.getvalue() + err.getvalue())
        self.assertIn("upgrade 复核失败", err.getvalue())
        self.assertEqual("2.0.0", self.manifest()["skillVersion"])
        self.assertEqual("2.0.0", self.config()["skillVersion"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
