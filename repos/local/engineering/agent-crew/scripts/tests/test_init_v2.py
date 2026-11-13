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
from test_doctor_config import make_separate_git_dir_clone, make_submodule_checkout

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

    def write_aligned_manifest(self, fx: ProjectFixture) -> None:
        """ProjectFixture 默认 manifest=2.1.0；init 源为真实技能版本，对齐避免 T33 误判。"""
        manifest = fx.manifest()
        manifest["skillVersion"] = init_v2.read_skill_version()
        fx.write_manifest(manifest)

    def test_default_config_and_template_redlines_are_generic(self):
        # G10/G12：默认红线与模板同步，去掉平台专属项；Captain 决策保留通用 8 项
        template = json.loads(
            (init_v2.SKILL_DIR / "templates/project.config.json").read_text(encoding="utf-8"))
        self.assertEqual(init_v2.DEFAULT_CONFIG["redLines"], template["redLines"])
        self.assertEqual(2, len(template["redLines"]))
        self.assertFalse(any("平台通用能力分层" in item for item in template["redLines"]))
        self.assertEqual([
            "合并/推送/worktree 清理",
            "花钱采购",
            "客户数据与对接人",
            "法律合规与风险确认",
            "主数据/架构口径",
            "范围与验收口径变更",
            "三道人工门签认",
            "验收效果最终确认",
        ], template["captainDecisions"])

        self.run_init("--yes")
        cfg = json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))
        self.assertEqual(template["redLines"], cfg["redLines"])

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
        block = text.split(init_v2.MARK_START, 1)[1].split(init_v2.MARK_END, 1)[0]
        # 描述性块：0 处 skillRoot 路径，含锚点与全部相对名
        self.assertNotIn(".qoder/skills/agent-crew", block)
        self.assertNotIn(str(Path(init_v2.SKILL_DIR).resolve()), block)
        self.assertIn(".planning/orchestration.json", block)
        self.assertIn("skillRoot", block)
        for rel in ("references/crew-rules.md", "references/crew-acceptance.md",
                    "references/verification-redlines.md",
                    "references/orchestration-guide.md", "references/plan-discipline.md",
                    "references/herdr-cheatsheet.md", "templates/playbooks/",
                    "templates/playbooks/README.md", "SKILL.md", "CHANGELOG.md"):
            self.assertIn(rel, block)
        for check in ("agents.block", "docs.pointers"):
            proc = subprocess.run(
                [sys.executable, str(self.root / "scripts/doctor.py"), "--root",
                 str(self.root), "--only", check, "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_skill_root_change_without_refresh_not_stale(self):
        self.assertEqual(self.run_init("--yes")[0], 0)
        cfg_path = self.root / ".planning/orchestration.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["skillRoot"] = ".qoder/skills/agent-crew"
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
        for check in ("agents.block", "docs.pointers"):
            proc = subprocess.run(
                [sys.executable, str(self.root / "scripts/doctor.py"), "--root",
                 str(self.root), "--only", check, "--json"],
                capture_output=True, text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            self.assertNotIn("stale", proc.stdout)

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
            self.write_aligned_manifest(fx)

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

    def test_check_missing_skill_root_reports_missing_not_worktree(self):
        # F4：不存在路径先报「不存在」，不再归因为开发 worktree；rc 不因提示改变
        base = Path(self._tmp.name) / "fx"
        _repo_skill, wt_skill = make_linked_worktree(base)
        fx = ProjectFixture(base)
        gone = wt_skill.parent / "gone-skill"  # worktree 内、不存在
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.config["skillRoot"] = os.path.relpath(gone, fx.root).replace(os.sep, "/")
        fx.write_config()
        manifest = fx.manifest()
        manifest["composedAgents"] = []  # 隔离提示块，避免既有组合校验干扰 rc
        manifest["skillVersion"] = init_v2.read_skill_version()
        fx.write_manifest(manifest)
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertIn("skillRoot 不存在或不可读", out)
        self.assertIn("下一步", out)
        self.assertNotIn("开发 worktree", out)

    def test_check_guard_hint_absent_uses_configured_helm_name(self):
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.config["helm"] = {"agentName": "helm-demo"}
        fx.write_config()
        self.write_aligned_manifest(fx)
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertIn("提示：未检测到值守 guard", out)
        self.assertIn("--helm helm-demo", out)

    def test_check_guard_hint_silent_when_alive(self):
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.write_config()
        self.write_aligned_manifest(fx)
        crew = init_v2.crew_dir(fx.root)
        crew.mkdir(parents=True, exist_ok=True)
        (crew / "guard.lock").write_text(f"{os.getpid()}\n", encoding="utf-8")
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertNotIn("提示：未检测到值守 guard", out)

    def test_check_guard_hint_silent_for_non_git(self):
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.write_config()
        shutil.rmtree(fx.root / ".git")
        code, out = self.run_init_at(fx.root, "--check")
        self.assertNotIn("提示：未检测到值守 guard", out)

    def test_check_fixed_checkouts_do_not_hint_worktree(self):
        # F2：真实 submodule / --separate-git-dir 检出不再提示开发 worktree
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        cases = (
            ("submodule", make_submodule_checkout(base / "subfx")),
            ("separate-git-dir", make_separate_git_dir_clone(base / "sepfx")),
        )
        for name, (_stable, skill) in cases:
            with self.subTest(case=name):
                shutil.copytree(fx.skill, skill, dirs_exist_ok=True)
                fx.config["skillVersion"] = init_v2.read_skill_version()
                fx.config["skillRoot"] = os.path.relpath(
                    skill, fx.root).replace(os.sep, "/")
                fx.write_config()
                fx.composed_entries = fx.write_composed_agents()
                self.write_aligned_manifest(fx)
                code, out = self.run_init_at(fx.root, "--check")
                self.assertEqual(0, code, out)
                self.assertNotIn("开发 worktree", out)
                self.assertNotIn("不存在或不可读", out)

    def test_check_skips_gitignored_skill_source_copies(self):
        # A：项目内 gitignored 的外部技能源（如 skill-src/）不算规则/技能源副本；
        # 未被忽略的真实副本仍必须失败。
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.write_config()
        self.write_aligned_manifest(fx)
        copy_rel = "skill-src/repos/local/engineering/agent-crew/references/crew-rules.md"
        copy = fx.root / copy_rel
        copy.parent.mkdir(parents=True)
        copy.write_text("# rules copy\n", encoding="utf-8")

        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(1, code, out)
        self.assertIn(copy_rel, out)

        (fx.root / ".gitignore").write_text("skill-src/\n", encoding="utf-8")
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertNotIn(copy_rel, out)
        self.assertNotIn("规则/技能源副本", out)

        # 非 git 仓：git 不可用/rc≠0 时按未忽略处理，维持原判定
        shutil.rmtree(fx.root / ".git")
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(1, code, out)
        self.assertIn(copy_rel, out)

    def test_check_flags_verification_redlines_copy_only(self):
        # S-3：VR 正文同名副本判「规则/技能源副本」；不同名 CASE 扩展文件不误报
        base = Path(self._tmp.name) / "fx"
        fx = ProjectFixture(base)
        fx.config["skillVersion"] = init_v2.read_skill_version()
        fx.write_config()
        self.write_aligned_manifest(fx)
        copy = fx.root / "docs/crew/verification-redlines.md"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_text("# vr copy\n", encoding="utf-8")

        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(1, code, out)
        self.assertIn("docs/crew/verification-redlines.md", out)

        copy.unlink()
        other = fx.root / "docs/crew/verification-and-delivery.md"
        other.write_text("# project extension\n", encoding="utf-8")
        code, out = self.run_init_at(fx.root, "--check")
        self.assertEqual(0, code, out)
        self.assertNotIn("verification-and-delivery.md", out)

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
