#!/usr/bin/env python3
"""init.py v2 行为契约测试：版本戳 / --check / --only-failed / 规则正文安装。

python3 scripts/tests/test_init_v2.py
（或 python3 -m unittest discover -s scripts/tests）
"""

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
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_v2_mod", _SCRIPTS / "init.py")
init_v2 = importlib.util.module_from_spec(_spec)
sys.modules["init_v2_mod"] = init_v2
_spec.loader.exec_module(init_v2)


def snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class InitV2Test(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.root.mkdir()

    def run_init(self, *extra: str) -> int:
        return init_v2.run(["--root", str(self.root), "--yes", *extra])

    def run_init_captured(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = self.run_init(*extra)
        return code, out.getvalue()

    def run_check_captured(self) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = init_v2.run(["--root", str(self.root), "--check"])
        return code, out.getvalue()

    # --- 验收 1：规则正文落 docs + token 渲染为项目相对路径 ---

    def test_first_run_installs_rule_docs_and_renders_tokens(self):
        code, out = self.run_init_captured()
        self.assertEqual(code, 0)

        rules = self.root / "docs" / "crew-rules.md"
        acceptance = self.root / "docs" / "crew-acceptance.md"
        self.assertEqual(
            rules.read_text(encoding="utf-8"),
            (init_v2.SKILL_DIR / "references" / "crew-rules.md").read_text(encoding="utf-8"),
        )
        self.assertEqual(
            acceptance.read_text(encoding="utf-8"),
            (init_v2.SKILL_DIR / "references" / "crew-acceptance.md").read_text(encoding="utf-8"),
        )
        self.assertIn("[新建] docs/crew-rules.md", out)
        self.assertIn("[新建] docs/crew-acceptance.md", out)

        builder = (self.root / ".qoder/agents/builder.md").read_text(encoding="utf-8")
        self.assertIn("docs/crew-rules.md", builder)
        self.assertIn("docs/crew-acceptance.md", builder)
        self.assertNotIn("{{crew_rules}}", builder)
        self.assertNotIn("{{crew_acceptance}}", builder)

        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn("`docs/crew-rules.md`", agents)
        self.assertIn("`docs/crew-acceptance.md`", agents)

        for path in [
            *sorted((self.root / ".qoder/agents").glob("*.md")),
            *sorted((self.root / ".opencode/agent").glob("*.md")),
            self.root / "AGENTS.md",
        ]:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("{{crew_rules}}", text, path)
            self.assertNotIn("{{crew_acceptance}}", text, path)

    def test_v2_scripts_are_installed_without_exec_bit(self):
        code, out = self.run_init_captured()
        self.assertEqual(code, 0)
        for name in (
            "crew-state.py",
            "crew-guard.py",
            "crew-ticket.py",
            "crew-msg.py",
            "pane-audit.py",
            "crew-lint.py",
        ):
            target = self.root / "scripts" / name
            self.assertIn(f"[新建] scripts/{name}", out)
            self.assertTrue(target.exists(), f"缺脚本 {name}")
            self.assertEqual(
                target.stat().st_mode & 0o111, 0, f"{name} 不应设置可执行位"
            )

    # --- 验收 2：二次 init 幂等 ---

    def test_second_run_is_idempotent(self):
        self.assertEqual(self.run_init(), 0)
        before = snapshot(self.root)
        code, out = self.run_init_captured()
        self.assertEqual(code, 0)
        self.assertEqual(snapshot(self.root), before, "二次 init 产生了写入")
        self.assertNotIn("[新建]", out)
        self.assertNotIn("[冲突]", out)
        self.assertNotIn("源已变更", out)

    # --- 验收 3：--check 干净退出 0；漂移非零并指出文件 ---

    def test_check_clean_then_reports_drift(self):
        self.run_init()
        code, out = self.run_check_captured()
        self.assertEqual(code, 0)
        self.assertIn("[一致] .qoder/agents/builder.md", out)
        for name in (
            "crew-state.py",
            "crew-guard.py",
            "crew-ticket.py",
            "crew-msg.py",
            "pane-audit.py",
            "crew-lint.py",
        ):
            self.assertIn(f"[一致] scripts/{name}", out)
        self.assertIn("30 项一致", out)
        self.assertIn("0 项缺失", out)
        self.assertIn("0 项过期", out)

        builder = self.root / ".qoder/agents/builder.md"
        builder.write_text("手工改动\n", encoding="utf-8")
        before = snapshot(self.root)
        code, out = self.run_check_captured()
        self.assertEqual(code, 1)
        self.assertIn("[过期] .qoder/agents/builder.md", out)
        self.assertEqual(snapshot(self.root), before, "--check 不得写盘")

    def test_check_missing_reports_without_writing(self):
        before = snapshot(self.root)
        code, out = self.run_check_captured()
        self.assertEqual(code, 1)
        self.assertIn("[缺失] .planning/orchestration.json", out)
        self.assertEqual(snapshot(self.root), before, "--check 不得创建任何文件")

    def test_check_detects_config_version_mismatch(self):
        self.run_init()
        path = self.root / ".planning/orchestration.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["skillVersion"] = "0.1.0"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        code, out = self.run_check_captured()
        self.assertEqual(code, 1)
        self.assertIn("[过期] .planning/orchestration.json", out)
        self.assertIn("源已变更", out)
        self.assertIn("update-all", out)

    # --- 验收 4：版本戳 + 源变更提示 ---

    def test_version_stamp_matches_skill_version(self):
        code, out = self.run_init_captured()
        self.assertEqual(code, 0)
        cfg = json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8")
        )
        self.assertEqual(cfg["skillVersion"], init_v2.read_skill_version())
        self.assertIn(init_v2.read_skill_version(), out)

    def test_old_installed_version_prints_source_change_notice(self):
        self.run_init()
        config_path = self.root / ".planning/orchestration.json"
        installed = json.loads(config_path.read_text(encoding="utf-8"))["skillVersion"]
        with mock.patch.object(init_v2, "read_skill_version", return_value="9.9.9"):
            code, out = self.run_init_captured()
        self.assertEqual(code, 0)
        self.assertIn("源已变更", out)
        self.assertIn(f"已安装 skillVersion={installed}", out)
        self.assertIn("源=9.9.9", out)
        self.assertIn("重跑 update-all", out)
        cfg = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(cfg["skillVersion"], "9.9.9")

    def test_read_skill_version_frontmatter(self):
        skill_dir = self.base / "skill"
        skill_dir.mkdir()
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("---\nname: x\nversion: 1.2.3\n---\n\n# body\n", encoding="utf-8")
        self.assertEqual(init_v2.read_skill_version(skill_dir), "1.2.3")
        skill_md.write_text('---\nname: x\nversion: "4.5.6"\n---\n', encoding="utf-8")
        self.assertEqual(init_v2.read_skill_version(skill_dir), "4.5.6")
        skill_md.write_text("---\nname: x\n---\n", encoding="utf-8")
        self.assertEqual(init_v2.read_skill_version(skill_dir), init_v2.SKILL_VERSION_FALLBACK)
        skill_md.write_text("# no frontmatter\n", encoding="utf-8")
        self.assertEqual(init_v2.read_skill_version(skill_dir), init_v2.SKILL_VERSION_FALLBACK)

    # --- 验收 5：失败项汇总 + --only-failed ---

    def test_only_failed_reruns_only_recorded_item(self):
        self.run_init()
        builder = self.root / ".qoder/agents/builder.md"
        builder.write_text("手工改动\n", encoding="utf-8")

        code, _ = self.run_init_captured()
        self.assertEqual(code, 2)
        report_path = self.root / ".planning" / ".agent-crew-init.json"
        self.assertTrue(report_path.exists(), "冲突后应写失败记录")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual([entry["path"] for entry in report["failed"]],
                         [".qoder/agents/builder.md"])

        before = snapshot(self.root)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = init_v2.run(["--root", str(self.root), "--only-failed"])
        text = out.getvalue()
        self.assertEqual(code, 2)
        self.assertIn("[冲突] .qoder/agents/builder.md", text)
        self.assertNotIn("[跳过] .planning/orchestration.json", text)
        self.assertNotIn("[新建]", text)
        self.assertEqual(snapshot(self.root), before, "--only-failed 不应触碰其他产物")

        code, out = self.run_init_captured("--only-failed", "--force")
        self.assertEqual(code, 0)
        self.assertNotIn("手工改动", builder.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["failed"], [])

    def test_only_failed_without_record_is_explicit(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = init_v2.run(["--root", str(self.root), "--only-failed"])
        self.assertEqual(code, 0)
        self.assertIn("未找到上次 init 失败记录", out.getvalue())
        self.assertFalse((self.root / ".planning").exists())

    def test_init_report_uses_git_dir_when_repo(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.run_init()
        (self.root / ".qoder/agents/builder.md").write_text("手工改动\n", encoding="utf-8")
        self.assertEqual(self.run_init(), 2)
        self.assertTrue((self.root / ".git/crew/init-report.json").exists())
        self.assertFalse((self.root / ".planning/.agent-crew-init.json").exists())

    # --- 规则正文冲突：默认不覆盖 / --force 覆盖 ---

    def test_rule_doc_conflict_respects_force(self):
        self.run_init()
        target = self.root / "docs" / "crew-rules.md"
        target.write_text("手工修改的规则正文\n", encoding="utf-8")

        code, _ = self.run_init_captured()
        self.assertEqual(code, 2)
        self.assertEqual(target.read_text(encoding="utf-8"), "手工修改的规则正文\n")

        code, _ = self.run_init_captured("--force")
        self.assertEqual(code, 0)
        self.assertEqual(
            target.read_text(encoding="utf-8"),
            (init_v2.SKILL_DIR / "references" / "crew-rules.md").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
