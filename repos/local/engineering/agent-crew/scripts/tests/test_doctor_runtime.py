#!/usr/bin/env python3
"""doctor 运行时/发现/升级检查（S9-06/S9-07/S9-12/S9-14）：runtime.*/role.discovery.*/role.runtime-direct/upgrade.scripts。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from doctor_fixtures import DOCTOR, ProjectFixture, write_json


class DoctorRuntimeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.fx = ProjectFixture(self.base)

    def test_runtime_dir_normal_linked_worktree_and_non_git(self):
        rc, report = self.fx.doctor_json("--only", "runtime.dir")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "runtime.dir")["status"])

        main = self.base / "main"
        main.mkdir()
        subprocess.run(["git", "-C", str(main), "init", "-q"], check=True)
        (main / "README.md").write_text("probe\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(main), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(main), "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-qm", "init"], check=True)
        worktree = self.base / "wt"
        subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(worktree)],
                       check=True)
        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--root", str(worktree),
             "--only", "runtime.dir", "--json"],
            capture_output=True, text=True, env=self.fx.env(),
        )
        report = json.loads(proc.stdout)
        self.assertEqual(0, proc.returncode, report)
        self.assertIn(str(main / ".git/crew"), self.fx.item(report, "runtime.dir")["detail"])
        self.assertFalse((worktree / ".git/crew").exists())

        non_git = self.base / "plain"
        non_git.mkdir()
        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--root", str(non_git),
             "--only", "runtime.dir", "--json"],
            capture_output=True, text=True, env=self.fx.env(),
        )
        report = json.loads(proc.stdout)
        self.assertEqual(0, proc.returncode, report)
        self.assertEqual("warn", self.fx.item(report, "runtime.dir")["status"])

    def test_runtime_lock_active_fail_stale_warn(self):
        lock_dir = self.fx.root / ".git/crew"
        lock_dir.mkdir(parents=True)
        lock = lock_dir / "init.lock"
        write_json(lock, {"pid": os.getpid()})
        rc, report = self.fx.doctor_json("--only", "runtime.lock")
        self.assertEqual(1, rc, report)
        self.assertIn("活锁", self.fx.item(report, "runtime.lock")["detail"])

        write_json(lock, {"pid": 1 << 30})
        rc, report = self.fx.doctor_json("--only", "runtime.lock")
        self.assertEqual(0, rc, report)
        self.assertEqual("warn", self.fx.item(report, "runtime.lock")["status"])

    def test_runtime_lock_guard_pid_alive_pass_dead_or_missing_warn(self):
        lock_dir = self.fx.root / ".git/crew"
        lock_dir.mkdir(parents=True)
        guard = lock_dir / "guard.lock"
        guard.write_text(f"{os.getpid()}\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "runtime.lock")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "runtime.lock")
        self.assertEqual("pass", item["status"], item)
        self.assertIn(f"guard 运行中（pid={os.getpid()}）", item["detail"])

        guard.write_text(f"{1 << 30}\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "runtime.lock")
        self.assertEqual(0, rc, report)
        self.assertEqual("warn", self.fx.item(report, "runtime.lock")["status"])

        guard.write_text("", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "runtime.lock")
        self.assertEqual(0, rc, report)
        item = self.fx.item(report, "runtime.lock")
        self.assertEqual("warn", item["status"])
        self.assertIn("无法识别 pid", item["detail"])

    def test_runtime_worktree_clean_pass_dirty_fail(self):
        self.fx.commit()
        rc, report = self.fx.doctor_json("--only", "runtime.worktree")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "runtime.worktree")["status"])
        (self.fx.root / "dirty.txt").write_text("x\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "runtime.worktree")
        self.assertEqual(1, rc, report)

    def test_role_discovery_stub_pass_nested_fail(self):
        rc, report = self.fx.doctor_json("--only", "role.discovery.qoder")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.discovery.qoder")["status"])
        rc, report = self.fx.doctor_json("--only", "role.discovery.opencode")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.discovery.opencode")["status"])

        nested = self.fx.root / ".opencode/agent/custom/nested.md"
        nested.parent.mkdir(parents=True)
        nested.write_text("---\ndescription: nested\nmode: primary\n---\n# nested\n",
                          encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.discovery.opencode")
        self.assertEqual(1, rc, report)
        self.assertIn("非规范落点", self.fx.item(report, "role.discovery.opencode")["detail"])

    def test_role_discovery_qoder_parses_inherit_and_paren_formats(self):
        # 当前格式（fixture 默认）：`  builder · Inherit`
        rc, report = self.fx.doctor_json("--only", "role.discovery.qoder")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.discovery.qoder")["status"])

        # 历史括号格式：`builder (Project)`
        self.fx.stub_cli(
            "qodercli",
            "#!/usr/bin/env python3\nprint('2 active agents')\n\n"
            "print('Project:')\nprint('builder (Project)')\n"
            "print('reviewer (Project)')\n",
        )
        rc, report = self.fx.doctor_json("--only", "role.discovery.qoder")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.discovery.qoder")["status"])

        # 缺角色 → FAIL，且 section/总量行不被当成角色
        self.fx.stub_cli(
            "qodercli",
            "#!/usr/bin/env python3\nprint('12 active agents')\n\n"
            "print('Project:')\nprint('  builder · Inherit')\n\n"
            "print('Built-in:')\nprint('  Explore · Inherit')\n",
        )
        rc, report = self.fx.doctor_json("--only", "role.discovery.qoder")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "role.discovery.qoder")
        self.assertEqual("fail", item["status"])
        self.assertIn("reviewer", item["detail"])

    def test_role_discovery_missing_cli_is_env(self):
        (self.fx.bin / "qodercli").unlink()
        env = self.fx.env()
        env["PATH"] = str(self.fx.bin)
        proc = subprocess.run(
            [sys.executable, str(DOCTOR), "--root", str(self.fx.root),
             "--only", "role.discovery.qoder", "--json"],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(3, proc.returncode, proc.stderr)
        report = json.loads(proc.stdout)
        self.assertEqual("env", self.fx.item(report, "role.discovery.qoder")["status"])

    def test_role_runtime_direct_fail_and_dispatcher_exempt(self):
        self.fx.script.write_text(
            "#!/usr/bin/env python3\n# herdr agent start placeholder\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(0, rc, report)

        doc = self.fx.root / "docs/orchestration/topology.md"
        doc.write_text(doc.read_text(encoding="utf-8") + "\nherdr agent start foo\n",
                       encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(1, rc, report)
        self.assertIn("agent start", self.fx.item(report, "role.runtime-direct")["detail"])

    def test_role_runtime_direct_flags_agents(self):
        self.fx.write_agents(outside="# AGENTS\n\nherdr agent start foo\n")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "role.runtime-direct")
        self.assertEqual("fail", item["status"], item)
        self.assertIn("AGENTS.md", item["detail"])
        self.assertIn("agent start", item["detail"])

    def test_role_runtime_direct_ignores_unscoped_docs(self):
        for rel, command in (
            ("docs/编排系统.md", "herdr agent start foo"),
            ("docs/业务落地方案/需求0/需求.md", "herdr tab create foo"),
            ("docs/crew/只读示例.md", "herdr agent list"),
        ):
            path = self.fx.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# 文档\n\n{command}\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.runtime-direct")["status"])

    def test_role_runtime_direct_ignores_third_party_dirs(self):
        for rel in (
            "docs/orchestration/.venv/lib/site-packages/pkg/README.md",
            "docs/crew/node_modules/pkg/README.md",
            "scripts/custom/.venv/tool.py",
        ):
            path = self.fx.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("herdr agent prompt foo\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "role.runtime-direct")["status"])

    def test_role_runtime_direct_flags_role_file(self):
        path = self.fx.role_path("qoder", "builder")
        path.write_text(path.read_text(encoding="utf-8") + "\nherdr agent close crew-builder\n",
                        encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "role.runtime-direct")
        self.assertEqual("fail", item["status"], item)
        self.assertIn(".qoder/agents/builder.md", item["detail"])

    def test_role_runtime_direct_flags_custom_scripts(self):
        path = self.fx.root / "scripts/custom/ops.sh"
        path.parent.mkdir(parents=True)
        path.write_text("herdr pane split foo\n", encoding="utf-8")
        rc, report = self.fx.doctor_json("--only", "role.runtime-direct")
        self.assertEqual(1, rc, report)
        item = self.fx.item(report, "role.runtime-direct")
        self.assertEqual("fail", item["status"], item)
        self.assertIn("scripts/custom/ops.sh", item["detail"])

    def test_upgrade_scripts_warn_then_pass(self):
        rc, report = self.fx.doctor_json("--only", "upgrade.scripts")
        self.assertEqual(0, rc, report)
        self.assertEqual("warn", self.fx.item(report, "upgrade.scripts")["status"])

        backup = self.fx.root / ".git/crew/backups/2026-11-09T00:00:00Z"
        backup.mkdir(parents=True)
        write_json(backup / "backup-manifest.json", {
            "backupId": "2026-11-09T00:00:00Z",
            "files": [{"path": "scripts/dispatch.py", "sha256": "0" * 64}],
        })
        rc, report = self.fx.doctor_json("--only", "upgrade.scripts")
        self.assertEqual(0, rc, report)
        self.assertEqual("pass", self.fx.item(report, "upgrade.scripts")["status"])


if __name__ == "__main__":
    unittest.main()
