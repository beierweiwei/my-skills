#!/usr/bin/env python3
"""2.2.33 工单 01-A/A1：release-check 配置全文快照（FB-063 防复发）。

`release-check.py --snapshot-dir <dir>`：把项目编排配置（`.planning/orchestration.json`
与 `docs/crew/manifest.json`）按字节复制到证据目录并写 sha256/时间戳清单；
缺配置文件 skip 不崩；不传参数时输出与 rc 与现状一致（零副作用）。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
RELEASE = SKILL / "scripts" / "release-check.py"
PYTHON = sys.executable

INIT_STUB = '''#!/usr/bin/env python3
import os
import sys

SCRIPT_MANIFEST = ["init.py", "doctor.py"]
SKILL_VERSION_FALLBACK = "2.2.33"

if __name__ == "__main__":
    rc = int(os.environ.get("STUB_INIT_RC", "0"))
    print(f"init: stub rc={rc}")
    sys.exit(rc)
'''

DOCTOR_STUB = '''#!/usr/bin/env python3
import json
import sys

args = sys.argv[1:]
if "--only" in args:
    print(json.dumps({"summary": {"pass": 1}, "items": [
        {"id": "runtime.guard", "status": "pass", "detail": "值守 guard 运行中"}]}))
    sys.exit(0)
print(json.dumps({"summary": {"pass": 10, "warn": 0, "fail": 0, "skip": 0,
                              "env": 0, "pending": 0}, "items": []}))
sys.exit(0)
'''

CLEAN_ENV = ("STUB_INIT_RC", "STUB_DOCTOR_FAIL", "STUB_DOCTOR_WARN",
             "STUB_GUARD_STATUS", "STUB_GUARD_DETAIL", "CREW_MY_SKILLS_ROOT")


class ConfigSnapshotTest(unittest.TestCase):
    """夹具：源树 + 非 git 项目根（.crew 回退），init/doctor 为可编程桩。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.source = self.tmp / "skill-source"
        self.root = self.tmp / "proj"
        self.snapshot = self.tmp / "evidence" / "config-snapshot"
        self.make_source()
        self.make_project()

    # ---------- 夹具 ----------

    def write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def make_source(self) -> None:
        self.write(self.source / "SKILL.md", (
            "---\nname: agent-crew\nversion: 2.2.33\n---\n\n"
            "# agent-crew：多 Agent 编排体系（v2.2.33）\n"))
        self.write(self.source / "CHANGELOG.md",
                   "# Changelog\n\n## 2.2.33 — 2026-11-14\n\n- stub\n")
        self.write(self.source / "scripts/init.py", INIT_STUB)
        self.write(self.source / "scripts/doctor.py", DOCTOR_STUB)

    def make_project(self) -> None:
        config = {
            "projectId": "proj",
            "skillRoot": str(self.source),
            "skillVersion": "2.2.33",
            "helm": {"agentName": "crew-orchestrator-helm"},
        }
        self.write(self.root / ".planning/orchestration.json",
                   json.dumps(config, ensure_ascii=False, indent=2) + "\n")
        self.write(self.root / "docs/crew/manifest.json",
                   json.dumps({"skillVersion": "2.2.33", "entries": []},
                              ensure_ascii=False, indent=2) + "\n")
        shutil.copytree(self.source / "scripts", self.root / "scripts")
        self.write(self.root / ".crew/guard.lock", f"{os.getpid()}\n")

    def run_check(self, *args: str) -> subprocess.CompletedProcess:
        env = {key: value for key, value in os.environ.items()
               if key not in CLEAN_ENV}
        return subprocess.run(
            [PYTHON, str(RELEASE), "--root", str(self.root),
             "--source", str(self.source), "--dry-run", *args],
            capture_output=True, text=True, cwd=str(self.tmp), env=env)

    # ---------- 快照正确性 ----------

    def test_snapshot_copies_configs_bytewise(self):
        proc = self.run_check("--snapshot-dir", str(self.snapshot))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        for name in ("orchestration.json", "manifest.json"):
            copied = self.snapshot / name
            self.assertTrue(copied.is_file(), f"缺快照文件：{copied}")
        # 字节一致（cmp 语义）
        self.assertEqual(
            (self.root / ".planning/orchestration.json").read_bytes(),
            (self.snapshot / "orchestration.json").read_bytes())
        self.assertEqual(
            (self.root / "docs/crew/manifest.json").read_bytes(),
            (self.snapshot / "manifest.json").read_bytes())
        # 清单：ts + sha256 匹配
        listing = json.loads(
            (self.snapshot / "config-snapshot.json").read_text(encoding="utf-8"))
        self.assertIn("ts", listing)
        by_name = {entry["file"]: entry for entry in listing["files"]}
        for name, source in (
                ("orchestration.json", self.root / ".planning/orchestration.json"),
                ("manifest.json", self.root / "docs/crew/manifest.json")):
            entry = by_name[name]
            self.assertEqual("copied", entry["status"])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                             entry["sha256"])
            self.assertEqual(source.stat().st_size, entry["size"])
        # 表格输出含 snapshot 行与 sha256
        self.assertIn("snapshot:", proc.stdout)
        self.assertIn(hashlib.sha256(
            (self.root / ".planning/orchestration.json").read_bytes()
        ).hexdigest()[:12], proc.stdout)

    def test_snapshot_json_mode_includes_field(self):
        proc = self.run_check("--snapshot-dir", str(self.snapshot), "--json")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        report = json.loads(proc.stdout)
        snapshot = report["snapshot"]
        self.assertEqual(str(self.snapshot), snapshot["dir"])
        statuses = {entry["file"]: entry["status"] for entry in snapshot["files"]}
        self.assertEqual({"orchestration.json": "copied",
                          "manifest.json": "copied"}, statuses)

    def test_snapshot_creates_missing_directory(self):
        target = self.tmp / "deep" / "nested" / "config-snapshot"
        proc = self.run_check("--snapshot-dir", str(target))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue((target / "orchestration.json").is_file())

    # ---------- 缺配置：skip 不崩 ----------

    def test_snapshot_missing_configs_skip(self):
        (self.root / ".planning/orchestration.json").unlink()
        (self.root / "docs/crew/manifest.json").unlink()
        proc = self.run_check("--snapshot-dir", str(self.snapshot))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        listing = json.loads(
            (self.snapshot / "config-snapshot.json").read_text(encoding="utf-8"))
        statuses = {entry["file"]: entry["status"] for entry in listing["files"]}
        self.assertEqual({"orchestration.json": "missing",
                          "manifest.json": "missing"}, statuses)
        self.assertIn("missing", proc.stdout)

    def test_snapshot_partial_configs(self):
        (self.root / "docs/crew/manifest.json").unlink()
        proc = self.run_check("--snapshot-dir", str(self.snapshot))
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue((self.snapshot / "orchestration.json").is_file())
        self.assertFalse((self.snapshot / "manifest.json").exists())

    # ---------- 默认行为不变 ----------

    def test_default_run_has_no_snapshot_without_flag(self):
        proc = self.run_check()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertNotIn("snapshot:", proc.stdout)
        self.assertFalse(self.snapshot.exists())

    # ---------- 快照失败：环境错误 ----------

    def test_snapshot_target_is_file_env_error(self):
        blocker = self.tmp / "blocker"
        blocker.write_text("not a directory\n", encoding="utf-8")
        proc = self.run_check("--snapshot-dir", str(blocker / "sub"))
        self.assertEqual(3, proc.returncode)
        self.assertIn("快照", proc.stderr)


if __name__ == "__main__":
    unittest.main()
