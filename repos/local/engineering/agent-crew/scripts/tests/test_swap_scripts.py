#!/usr/bin/env python3
"""swap-to-v2.sh / rollback-v1.sh 替换包用例（crew-v2 3.4）。

覆盖：
① dry-run 零写入（项目与备份目录都不落盘）；
② 备份 manifest 含文件 sha256 与版本信息、备份路径正确；
③ 替换后受管文件更新、非受管文件（含自定义角色/脚本/文档）原样保留；
④ rollback 逐字节恢复受管文件（sha256 比对），新建文件只报告不删除；
⑤ init 失败自动回滚（--no-auto-rollback 保留现场）；备份被篡改时拒绝恢复；
⑥ 两脚本 --help 自解释。
全部 fixture 建在 tmp 目录，绝不触碰 CASE 仓。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
SWAP = SKILL_DIR / "scripts" / "swap-to-v2.sh"
ROLLBACK = SKILL_DIR / "scripts" / "rollback-v1.sh"

OLD_FILES = {
    ".qoder/agents/process-auditor.md": "# 自定义过程审计（非受管）\n",
    ".qoder/agents/builder.md": "# 旧版 builder\n",
    ".opencode/agent/firstmate.md": "# 旧版 firstmate\n",
    ".opencode/skills/firstmate-dispatch/SKILL.md": "# 旧版派活 SOP\n",
    "scripts/plan-status.py": "#!/usr/bin/env python3\nprint('old plan-status')\n",
    "scripts/start-helm.sh": "#!/usr/bin/env bash\necho old-helm\n",
    "scripts/my-tool.sh": "#!/usr/bin/env bash\necho custom-tool\n",
    "AGENTS.md": "# AGENTS\n\n自定义项目规则，保留。\n",
    "CONTEXT.md": "# CONTEXT\n\n自定义术语，保留。\n",
    "docs/crew-rules.md": "旧版规则正文\n",
    "docs/crew-acceptance.md": "旧版验收正文\n",
}
CONFIG = {"projectName": "case-proj", "portfolioDir": ".planning/portfolio"}
UNMANAGED = (
    ".qoder/agents/process-auditor.md",
    "scripts/my-tool.sh",
    "CONTEXT.md",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot_tree(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): sha256(path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class _SwapTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.root = self.tmp / "case-proj"
        self.backup = self.tmp / "backup"
        for rel, body in OLD_FILES.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body, encoding="utf-8")
        (self.root / ".planning").mkdir()
        (self.root / ".planning/orchestration.json").write_text(
            json.dumps(CONFIG, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self.origin = snapshot_tree(self.root)

    def run_script(self, script: Path, *args: object) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, "HOME": str(self.home)}
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            text=True, capture_output=True, env=env,
        )

    def swap(self, *args: object, script: Path = SWAP) -> subprocess.CompletedProcess[str]:
        return self.run_script(script, self.root, "--backup-dir", self.backup, *args)

    def manifest_rows(self) -> dict[str, str]:
        rows: dict[str, str] = {}
        for line in (self.backup / "manifest.txt").read_text(encoding="utf-8").splitlines():
            if line.startswith("FILE\t"):
                _, digest, rel = line.split("\t", 2)
                rows[rel] = digest
        return rows

    def _fake_failing_init(self) -> Path:
        fake = self.tmp / "fake-init.py"
        fake.write_text(
            'SCRIPT_MANIFEST = ["plan-status.py"]\n'
            'import sys\n'
            'from pathlib import Path\n'
            'root = Path(sys.argv[sys.argv.index("--root") + 1])\n'
            '(root / "scripts" / "plan-status.py").write_text("broken v2\\n", encoding="utf-8")\n'
            'print("fake init failure", file=sys.stderr)\n'
            'raise SystemExit(1)\n',
            encoding="utf-8",
        )
        return fake


class SwapTest(_SwapTest):
    def test_dry_run_zero_writes(self):
        before = snapshot_tree(self.root)
        result = self.swap("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertIn("备份路径（将创建）", result.stdout)
        # 已存在受管文件 → [更新]；缺失受管产物（角色/脚本）→ [新增]
        self.assertIn("[更新] scripts/plan-status.py", result.stdout)
        self.assertIn("[新增] .qoder/agents/helm.md", result.stdout)
        self.assertIn("[新增] scripts/crew-state.py", result.stdout)
        self.assertIn("[新增] scripts/dispatch.py", result.stdout)
        self.assertNotIn("scripts/crew-lint.py", result.stdout)
        self.assertIn("pending", result.stdout)
        self.assertFalse(self.backup.exists())
        self.assertEqual(snapshot_tree(self.root), before)

    def test_dry_run_reports_no_rule_copies(self):
        # v2.1 不再安装规则副本；缺 docs 也不应出现在变更清单
        for rel in ("docs/crew-rules.md", "docs/crew-acceptance.md"):
            (self.root / rel).unlink()
        before = snapshot_tree(self.root)
        result = self.swap("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        for rel in ("docs/crew-rules.md", "docs/crew-acceptance.md"):
            self.assertNotIn(f"[新增] {rel}", result.stdout)
        self.assertFalse(self.backup.exists())
        self.assertEqual(snapshot_tree(self.root), before)

    def test_default_backup_path_uses_home_and_utc_stamp(self):
        result = self.run_script(SWAP, self.root, "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"{self.home}/crew-backups/case-proj-", result.stdout)
        self.assertFalse((self.home / "crew-backups").exists())

    def test_backup_manifest_has_sha256_and_correct_paths(self):
        result = self.swap()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = self.backup / "manifest.txt"
        self.assertTrue(manifest.is_file())
        text = manifest.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("agent-crew swap manifest v1\n"))
        for token in ("swap_version=v2", "project=case-proj", "skill_rev=",
                      "created_utc=", "scope_dir=.qoder/agents"):
            self.assertIn(token, text)

        rows = self.manifest_rows()
        self.assertIn("scripts/plan-status.py", rows)
        self.assertEqual(rows["scripts/plan-status.py"],
                         self.origin["scripts/plan-status.py"])
        backup_file = self.backup / "files/scripts/plan-status.py"
        self.assertEqual(sha256(backup_file), rows["scripts/plan-status.py"])
        self.assertIn("scripts/start-helm.sh", rows)
        self.assertIn("docs/crew-rules.md", rows)
        self.assertIn("docs/crew-acceptance.md", rows)
        self.assertNotIn("scripts/my-tool.sh", rows)  # 非受管脚本不备份

    def test_swap_updates_managed_and_keeps_unmanaged(self):
        result = self.swap()
        self.assertEqual(result.returncode, 0, result.stderr)

        skill_source = (SKILL_DIR / "scripts/plan-status.py").read_text(encoding="utf-8")
        self.assertEqual(
            (self.root / "scripts/plan-status.py").read_text(encoding="utf-8"), skill_source
        )
        # 项目角色文件项目自有：v2.1 永不覆盖，既有 builder 保持原样
        self.assertEqual(
            (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8"),
            OLD_FILES[".qoder/agents/builder.md"],
        )
        self.assertTrue((self.root / ".qoder/agents/helm.md").is_file())  # 缺失才生成 stub
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertNotIn("agent-crew:orchestration:start", agents)  # 无标记 → pending 零写入
        self.assertIn("自定义项目规则", agents)
        pending = self.root / "docs/crew/AGENTS-block.pending.md"
        self.assertTrue(pending.is_file())
        self.assertIn("agent-crew:orchestration:start", pending.read_text(encoding="utf-8"))

        for rel in UNMANAGED:
            self.assertEqual(
                (self.root / rel).read_text(encoding="utf-8"), OLD_FILES[rel], rel
            )

        for token in ("替换完成", "备份路径：", "变更文件：", "回滚命令：",
                      "[更新] scripts/plan-status.py", "[新增] .qoder/agents/helm.md",
                      "[新增] scripts/crew-state.py"):
            self.assertIn(token, result.stdout)

    def test_rollback_restores_every_managed_file_bytewise(self):
        self.assertEqual(self.swap().returncode, 0)
        result = self.run_script(ROLLBACK, self.root, self.backup)
        self.assertEqual(result.returncode, 0, result.stderr)

        after = snapshot_tree(self.root)
        for rel, digest in self.origin.items():
            self.assertEqual(after[rel], digest, rel)  # 受管恢复 + 非受管原样

        new_file = self.root / ".qoder/agents/helm.md"
        self.assertTrue(new_file.is_file())  # 备份后新建：只报告，不删除
        self.assertIn("备份后新建且未回滚", result.stdout)
        self.assertIn(".qoder/agents/helm.md", result.stdout)

    def test_init_failure_triggers_auto_rollback(self):
        fake = self._fake_failing_init()
        result = self.swap("--init-py", fake)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("init.py 执行失败", result.stderr)
        self.assertIn("已自动回滚", result.stderr)
        self.assertTrue((self.backup / "manifest.txt").is_file())
        self.assertEqual(
            (self.root / "scripts/plan-status.py").read_text(encoding="utf-8"),
            OLD_FILES["scripts/plan-status.py"],
        )

    def test_no_auto_rollback_keeps_failed_state(self):
        fake = self._fake_failing_init()
        result = self.swap("--init-py", fake, "--no-auto-rollback")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--no-auto-rollback", result.stderr)
        self.assertIn("rollback-v1.sh", result.stderr)
        self.assertEqual(
            (self.root / "scripts/plan-status.py").read_text(encoding="utf-8"),
            "broken v2\n",
        )


class RollbackTest(_SwapTest):
    def test_tampered_backup_is_refused_without_writes(self):
        self.assertEqual(self.swap().returncode, 0)
        v2_hash = sha256(self.root / "scripts/plan-status.py")
        tampered = self.backup / "files/scripts/plan-status.py"
        tampered.write_text(tampered.read_text(encoding="utf-8") + "tamper",
                            encoding="utf-8")
        result = self.run_script(ROLLBACK, self.root, self.backup)
        self.assertEqual(result.returncode, 2)
        self.assertIn("校验失败", result.stderr)
        self.assertIn("拒绝恢复", result.stderr)
        self.assertEqual(sha256(self.root / "scripts/plan-status.py"), v2_hash)

    def test_missing_manifest_is_error(self):
        self.backup.mkdir()
        result = self.run_script(ROLLBACK, self.root, self.backup)
        self.assertEqual(result.returncode, 1)
        self.assertIn("manifest", result.stderr)


class HelpTest(_SwapTest):
    def test_swap_help_is_self_explanatory(self):
        result = self.run_script(SWAP, "--help")
        self.assertEqual(result.returncode, 0)
        for token in ("用法", "dry-run", "备份目录", "回滚", "退出码"):
            self.assertIn(token, result.stdout)

    def test_rollback_help_is_self_explanatory(self):
        result = self.run_script(ROLLBACK, "--help")
        self.assertEqual(result.returncode, 0)
        for token in ("用法", "manifest", "sha256", "回滚", "退出码"):
            self.assertIn(token, result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
