#!/usr/bin/env python3
"""2.2.33 工单 01-A/A2：init/refresh-block 写前备份配置（FB-063 防复发）。

`init.py`/`--refresh-block` 真正写入 `docs/crew/manifest.json` 或
`.planning/orchestration.json` 前，用 `create_backup`（`<crew-dir>/backups/`，
含 sha256 清单，可 `upgrade.py --rollback` 回滚）生成时间戳备份；保留同 action
最近 5 份；无写入路径零备份；备份失败中止写入不静默。
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_2233_mod", _SCRIPTS / "init.py")
init_2233 = importlib.util.module_from_spec(_spec)
sys.modules["init_2233_mod"] = init_2233
_spec.loader.exec_module(init_2233)

UPGRADE = _SCRIPTS / "upgrade.py"
PYTHON = sys.executable


class InitBackupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)

    # ---------- 工具 ----------

    def run_init(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = init_2233.run(["--root", str(self.root), *extra])
        return code, out.getvalue() + err.getvalue()

    def crew(self) -> Path:
        return init_2233.crew_dir(self.root)

    def backups(self, action: str) -> list[str]:
        """按 backup-manifest.json 的 action 过滤，返回按目录名排序的 backup id。"""
        base = self.crew() / "backups"
        if not base.is_dir():
            return []
        ids = []
        for directory in base.iterdir():
            manifest = directory / "backup-manifest.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if data.get("action") == action:
                ids.append(directory.name)
        return sorted(ids)

    def bump_config_hash(self) -> None:
        """改 config 的非渲染键（helm.agentName），触发 configHash 变化。"""
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["helm"]["agentName"] = f"helm-{os.urandom(3).hex()}"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")

    # ---------- refresh-block：写前备份 ----------

    def test_refresh_block_confirm_backs_up_manifest(self):
        self.assertEqual(0, self.run_init("--yes")[0])
        manifest_path = self.root / "docs/crew/manifest.json"
        before = manifest_path.read_bytes()
        # 触发块变化：改 AGENTS.md 块内一字（与渲染不一致）
        agents_path = self.root / "AGENTS.md"
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8").replace("编排体系", "编排体系X"),
            encoding="utf-8")
        # 未确认：rc=2，零备份零写入
        code, out = self.run_init("--refresh-block")
        self.assertEqual(2, code, out)
        self.assertEqual([], self.backups("refresh-block"))
        self.assertEqual(before, manifest_path.read_bytes())
        # 确认写入：备份 = 写前字节，AGENTS 块被重渲染（改坏的块内容恢复）
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, code, out)
        ids = self.backups("refresh-block")
        self.assertEqual(1, len(ids), out)
        backed = self.crew() / "backups" / ids[0] / "docs/crew/manifest.json"
        self.assertEqual(before, backed.read_bytes())
        self.assertNotIn("编排体系X", agents_path.read_text(encoding="utf-8"))

    def test_refresh_block_config_hash_update_backs_up_manifest(self):
        """块未变但 configHash 变：重算路径同样写前备份。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        manifest_path = self.root / "docs/crew/manifest.json"
        before = manifest_path.read_bytes()
        self.bump_config_hash()
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, code, out)
        self.assertIn("configHash", out)
        ids = self.backups("refresh-block")
        self.assertEqual(1, len(ids), out)
        backed = self.crew() / "backups" / ids[0] / "docs/crew/manifest.json"
        self.assertEqual(before, backed.read_bytes())

    def test_refresh_block_no_change_zero_backup(self):
        """块内容与 configHash 均最新：零写入零备份。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        manifest_path = self.root / "docs/crew/manifest.json"
        before = manifest_path.read_bytes()
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, code, out)
        self.assertIn("无需重渲染", out)
        self.assertEqual([], self.backups("refresh-block"))
        self.assertEqual(before, manifest_path.read_bytes())

    # ---------- 保留窗口（同 action 最近 5 份） ----------

    def test_backup_retention_keeps_five(self):
        self.assertEqual(0, self.run_init("--yes")[0])
        for _ in range(6):
            self.bump_config_hash()
            code, out = self.run_init("--refresh-block", "--confirm")
            self.assertEqual(0, code, out)
        ids = self.backups("refresh-block")
        self.assertEqual(5, len(ids), f"应保留最近 5 份，实际 {len(ids)}：{ids}")

    def test_retention_scoped_to_action(self):
        """清理只作用于同 action 备份，不误删其他 action。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        manifest_path = self.root / "docs/crew/manifest.json"
        other = init_2233.create_backup(self.root, "upgrade", [manifest_path])
        self.assertIsNotNone(other)
        for _ in range(6):
            self.bump_config_hash()
            self.run_init("--refresh-block", "--confirm")
        self.assertEqual(5, len(self.backups("refresh-block")))
        self.assertIn(other, self.backups("upgrade"))

    # ---------- 回滚可用 ----------

    def test_backup_restorable_manually(self):
        """manifest 备份人工 cp 回滚（upgrade rollback 不还原 manifest）。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        manifest_path = self.root / "docs/crew/manifest.json"
        before = manifest_path.read_bytes()
        self.bump_config_hash()
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, code, out)
        self.assertNotEqual(before, manifest_path.read_bytes())
        backup_id = self.backups("refresh-block")[0]
        backed = self.crew() / "backups" / backup_id / "docs/crew/manifest.json"
        shutil.copy2(backed, manifest_path)
        self.assertEqual(before, manifest_path.read_bytes())

    def test_config_backup_restorable_via_upgrade_rollback(self):
        """orchestration.json 备份可走 upgrade.py --rollback 逐字节还原。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillVersion"] = "0.0.1"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        before = cfg_path.read_bytes()
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        backup_id = self.backups("init-write")[0]
        proc = subprocess.run(
            [PYTHON, str(UPGRADE), "--root", str(self.root),
             "--rollback", backup_id],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertEqual(before, cfg_path.read_bytes())

    # ---------- 常规 init：写前备份配置 ----------

    def test_init_yes_backs_up_existing_config(self):
        self.assertEqual(0, self.run_init("--yes")[0])
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillVersion"] = "0.0.1"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        before = cfg_path.read_bytes()
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        ids = self.backups("init-write")
        self.assertEqual(1, len(ids), out)
        backed = self.crew() / "backups" / ids[0] / ".planning/orchestration.json"
        self.assertEqual(before, backed.read_bytes())
        updated = json.loads(cfg_path.read_text(encoding="utf-8"))
        self.assertNotEqual("0.0.1", updated["skillVersion"])

    def test_init_fresh_project_zero_backup(self):
        """全新项目：配置/manifest 均新建（无既有文件可备份），零备份。"""
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        self.assertEqual([], self.backups("init-write"))

    def test_init_no_config_change_zero_backup(self):
        """配置无变化重跑 init：零写入零备份。"""
        self.assertEqual(0, self.run_init("--yes")[0])
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        self.assertEqual([], self.backups("init-write"))

    # ---------- 备份失败中止写入 ----------

    def test_backup_failure_aborts_refresh_block_write(self):
        self.assertEqual(0, self.run_init("--yes")[0])
        agents_path = self.root / "AGENTS.md"
        agents_path.write_text(
            agents_path.read_text(encoding="utf-8").replace("编排体系", "编排体系X"),
            encoding="utf-8")
        agents_before = (self.root / "AGENTS.md").read_bytes()
        manifest_before = (self.root / "docs/crew/manifest.json").read_bytes()
        # 让备份目录不可创建：backups 位置放一个文件
        backups = self.crew() / "backups"
        backups.parent.mkdir(parents=True, exist_ok=True)
        backups.write_text("not a directory\n", encoding="utf-8")
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(1, code, out)
        self.assertIn("备份", out)
        self.assertEqual(agents_before, (self.root / "AGENTS.md").read_bytes())
        self.assertEqual(manifest_before,
                         (self.root / "docs/crew/manifest.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
