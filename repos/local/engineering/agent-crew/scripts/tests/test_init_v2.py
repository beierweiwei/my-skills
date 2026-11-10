#!/usr/bin/env python3
"""init.py v2.1：配置保留/版本戳/--check 语义/install-log/角色项目修改/绝对路径 WARN。"""

from __future__ import annotations

import contextlib
import hashlib
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

from doctor_fixtures import ProjectFixture, make_linked_worktree

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

    def run_init_at(self, root: Path, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = init_v2.run(["--root", str(root), *extra])
        return code, out.getvalue()

    def run_init(self, *extra: str) -> tuple[int, str]:
        return self.run_init_at(self.root, *extra)

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

    def test_check_pending_agents_returns_2_without_failure_line(self):
        self.run_init("--yes")
        agents = self.root / "AGENTS.md"
        agents.write_text("# AGENTS\n\n项目自有内容。\n", encoding="utf-8")
        before = agents.read_bytes()
        code, out = self.run_init("--check")
        self.assertEqual(code, 2, out)
        self.assertIn("AGENTS.md 无受管标记（pending）", out)
        self.assertNotIn("[缺失/失败]", out)
        self.assertEqual(agents.read_bytes(), before)

    def test_check_pending_takes_precedence_over_composed_stale(self):
        self.run_init("--yes")
        (self.root / "AGENTS.md").write_text("# AGENTS\n\n项目自有内容。\n", encoding="utf-8")
        role = self.root / ".qoder/agents/builder.md"
        role.write_text(role.read_text(encoding="utf-8") + "\n项目补充\n", encoding="utf-8")
        code, out = self.run_init("--check")
        self.assertEqual(code, 2, out)
        self.assertIn("composedStale", out)
        self.assertIn("AGENTS.md 无受管标记（pending）", out)
        self.assertNotIn("[缺失/失败]", out)

    def test_check_real_problem_returns_1(self):
        self.run_init("--yes")
        (self.root / "scripts/crew-state.py").unlink()
        code, out = self.run_init("--check")
        self.assertEqual(code, 1, out)
        self.assertIn("[缺失/失败]", out)
        self.assertIn("crew-state.py", out)

    def test_check_consistent_returns_0(self):
        self.run_init("--yes")
        code, out = self.run_init("--check")
        self.assertEqual(code, 0, out)

    def test_role_project_edit_marks_composed_stale(self):
        self.run_init("--yes")
        role = self.root / ".qoder/agents/builder.md"
        role.write_text(role.read_text(encoding="utf-8") + "\n项目补充\n", encoding="utf-8")
        code, out = self.run_init("--check")
        self.assertEqual(code, 1, out)
        self.assertIn("composedStale", out)
        code, out = self.run_init("--yes")
        self.assertEqual(code, 0, out)
        code, out = self.run_init("--check")
        self.assertEqual(code, 0, out)

    def test_refresh_block_keeps_relative_skill_root(self):
        self.assertEqual(self.run_init("--yes")[0], 0)
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillRoot"] = ".qoder/skills/agent-crew"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        code, out = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, code, out)
        text = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        self.assertIn(".qoder/skills/agent-crew", text)
        self.assertNotIn(str(Path(init_v2.SKILL_DIR).resolve()), text)
        for check in ("agents.block", "docs.pointers"):
            proc = subprocess.run(
                [sys.executable, str(self.root / "scripts/doctor.py"), "--root",
                 str(self.root), "--only", check, "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

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

    def test_check_dev_worktree_skill_root_hints_without_failing(self):
        base = Path(self._tmp.name) / "fx"
        repo_skill, wt_skill = make_linked_worktree(base)
        fx = ProjectFixture(base)
        for skill in (repo_skill, wt_skill):  # --check 需组合基础代理模板
            shutil.copytree(fx.skill, skill, dirs_exist_ok=True)
        fx.config["skillVersion"] = init_v2.read_skill_version()

        def apply_skill_root(skill: Path) -> None:
            fx.config["skillRoot"] = os.path.relpath(skill, fx.root).replace(os.sep, "/")
            fx.write_config()
            fx.composed_entries = fx.write_composed_agents()
            fx.write_manifest()

        apply_skill_root(repo_skill)
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertNotIn("开发 worktree", out)

        apply_skill_root(wt_skill)
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)  # 提示不升级为失败
        self.assertIn("[风险提示]", out)
        self.assertIn("开发 worktree", out)
        self.assertIn("稳定分发副本", out)

        # 项目内 skillRoot：不误报
        installed = fx.root / ".qoder/skills/agent-crew"
        shutil.copytree(fx.skill, installed)
        apply_skill_root(installed)
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertNotIn("开发 worktree", out)

    def test_check_unresolvable_tilde_skill_root_skips_hint_without_traceback(self):
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.config["skillRoot"] = "~nosuchuser4242/agent-crew"
        fx.write_config()
        fx.write_manifest()
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(1, code, out)  # 报告问题但不崩溃（HEAD 行为）
        self.assertIn("检查结果", out)
        self.assertNotIn("Traceback", out)
        self.assertNotIn("开发 worktree", out)

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


class FeedbackSkeletonTest(unittest.TestCase):
    """2.2.4：portfolio feedback.md 骨架（缺失才写、不覆盖、幂等）。"""

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

    def feedback_path(self) -> Path:
        return self.root / ".planning" / "portfolio" / "feedback.md"

    def test_feedback_skeleton_created_and_retro_config_written(self):
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        text = self.feedback_path().read_text(encoding="utf-8")
        self.assertIn("## 流程反馈（复盘信箱）", text)
        self.assertIn("2026-11-10 冻结", text)
        cfg = json.loads(
            (self.root / ".planning" / "orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual({"enabled": True, "periodDays": 7}, cfg["retro"])

    def test_feedback_skeleton_not_overwritten(self):
        target = self.feedback_path()
        target.parent.mkdir(parents=True)
        target.write_text("# 项目自有反馈\n\n不覆盖。\n", encoding="utf-8")
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        self.assertEqual("# 项目自有反馈\n\n不覆盖。\n",
                         target.read_text(encoding="utf-8"))

    def test_feedback_skeleton_idempotent(self):
        self.assertEqual(0, self.run_init("--yes")[0])
        before = sha(self.feedback_path())
        code, out = self.run_init("--yes")
        self.assertEqual(0, code, out)
        self.assertEqual(before, sha(self.feedback_path()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
