#!/usr/bin/env python3
"""plan-status.py --playbooks：剧本结构校验（正例/反例/README 跳过/路径缺失）。

解析契约与 templates/plans/stream-task_plan.md 一致：每剧本至少 1 个合法
`### Phase N:`；每 Phase 恰一行 `**Status:** pending|in_progress|complete`；
Phase 条目只允许 `- [ ]` / `- [x]` / `- owner:` / `- plan:`。
"""

from __future__ import annotations

import contextlib
import io
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "plan-status.py"
_spec = importlib.util.spec_from_file_location("plan_status_playbooks", _SPEC_PATH)
ps = importlib.util.module_from_spec(_spec)
sys.modules["plan_status_playbooks"] = ps
_spec.loader.exec_module(ps)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOKS_DIR = REPO_ROOT / "templates" / "playbooks"
SEEDS = ("autonomous-run.md", "babysit.md", "session-pickup.md", "investigation.md")


def run_cli(*args: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = ps.main(list(args))
    return rc, out.getvalue(), err.getvalue()


def playbook_body(status: str = "pending") -> str:
    """最小合法剧本：适用/Phases（owner+plan+todo+status）/跳过规则。"""
    return "\n".join([
        "# 剧本：fixture",
        "",
        "## 适用与退出条件",
        "",
        "- 适用：结构校验样本。",
        "",
        "## Phases",
        "",
        "### Phase 1: 准备",
        "- owner: fixture",
        "- plan: `.planning/fixture/task_plan.md`",
        "- [ ] 待办步骤",
        "- [x] 已完成步骤",
        f"**Status:** {status}",
        "",
        "## 跳过规则",
        "",
        "- 跳过步骤记 `- [x] <步骤>（跳过：<原因>）`。",
        "",
    ])


def line_of(text: str, needle: str) -> int:
    return text.splitlines().index(needle) + 1


class SyntheticPlaybookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "playbooks"
        self.dir.mkdir()

    def write(self, name: str, text: str) -> Path:
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def lint_dir(self) -> tuple[int, str, str]:
        return run_cli("--playbooks", str(self.dir))

    # ---- 正例 -----------------------------------------------------------

    def test_valid_playbook_passes_with_one_line_summary(self):
        self.write("demo.md", playbook_body())
        rc, out, err = self.lint_dir()
        self.assertEqual(rc, 0, err)
        self.assertEqual(err, "")
        self.assertIn("剧本 lint ok：1 个剧本", out)

    def test_valid_single_file_path_passes(self):
        path = self.write("demo.md", playbook_body("in_progress"))
        rc, out, err = run_cli("--playbooks", str(path))
        self.assertEqual(rc, 0, err)
        self.assertIn("1 个剧本", out)

    def test_all_three_status_values_pass(self):
        for status in ("pending", "in_progress", "complete"):
            with self.subTest(status=status):
                self.write("demo.md", playbook_body(status))
                rc, _out, err = self.lint_dir()
                self.assertEqual(rc, 0, err)

    def test_readme_skipped_case_insensitive(self):
        self.write("README.md", "无 Phase 无 Status 的说明文件。\n")
        self.write("readme.md", "同样应被跳过。\n")
        self.write("demo.md", playbook_body())
        rc, out, err = self.lint_dir()
        self.assertEqual(rc, 0, err)
        self.assertIn("1 个剧本", out)

    def test_direct_readme_file_path_is_linted(self):
        # 目录模式跳过 README.md；显式传文件路径仍按剧本校验
        path = self.write("README.md", "无 Phase 无 Status。\n")
        rc, _out, err = run_cli("--playbooks", str(path))
        self.assertEqual(rc, 1)
        self.assertIn("[Phase 缺失]", err)

    # ---- 反例 -----------------------------------------------------------

    def test_missing_status_line(self):
        body = playbook_body().replace("**Status:** pending\n", "")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '### Phase 1: 准备')}: [Status 行]", err)
        self.assertIn("缺少", err)

    def test_duplicate_status_line(self):
        body = playbook_body().replace(
            "**Status:** pending", "**Status:** pending\n**Status:** in_progress")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '**Status:** in_progress')}: [Status 行]", err)
        self.assertIn("2 个", err)

    def test_invalid_status_value(self):
        body = playbook_body().replace("**Status:** pending", "**Status:** done")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '**Status:** done')}: [Status 行]", err)
        self.assertIn("pending|in_progress|complete", err)

    def test_non_standard_todo_mark(self):
        body = playbook_body().replace("- [ ] 待办步骤", "* [ ] 待办步骤")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '* [ ] 待办步骤')}: [Phase 项格式]", err)

    def test_plain_bullet_in_phase(self):
        body = playbook_body().replace("- [ ] 待办步骤", "- 普通条目")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '- 普通条目')}: [Phase 项格式]", err)

    def test_no_phase_at_all(self):
        path = self.write("demo.md", "# 剧本：无阶段\n\n## 跳过规则\n\n- 无。\n")
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(f"{path.name}:1: [Phase 缺失]", err)

    def test_missing_path_returns_3(self):
        missing = Path(self._tmp.name) / "nope"
        rc, _out, err = run_cli("--playbooks", str(missing))
        self.assertEqual(rc, 3)
        self.assertIn("路径不存在", err)

    def test_help_documents_playbooks(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            with self.assertRaises(SystemExit) as cm:
                ps.main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--playbooks", out.getvalue())

    # ---- S4：Phase 必须位于 ## Phases 节内 -------------------------------

    def test_phase_under_other_section_fails(self):
        body = "\n".join([
            "# 剧本：Phase 错位",
            "",
            "## Phases",
            "",
            "## 跳过规则",
            "",
            "### Phase 1: 错位阶段",
            "**Status:** pending",
            "",
        ])
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '### Phase 1: 错位阶段')}: [Phase 位置]", err)
        self.assertIn("跳过规则", err)

    def test_missing_phases_section_fails(self):
        body = playbook_body().replace("## Phases\n", "")
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(f"{path.name}:1: [Phases 节]", err)
        self.assertIn("[Phase 位置]", err)  # Phase 现在落在「适用与退出条件」节下

    def test_empty_phases_section_fails(self):
        body = "\n".join([
            "# 剧本：空 Phases",
            "",
            "## Phases",
            "",
            "## 跳过规则",
            "",
            "- 无。",
            "",
        ])
        path = self.write("demo.md", body)
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 1)
        self.assertIn(
            f"{path.name}:{line_of(body, '## Phases')}: [Phases 节]", err)
        self.assertIn("[Phase 缺失]", err)

    def test_standard_playbook_with_phases_section_passes(self):
        # 正常剧本：说明节在前、## Phases 居中、Phase 位于其内 → pass（正例）
        self.write("demo.md", playbook_body())
        rc, _out, err = self.lint_dir()
        self.assertEqual(rc, 0, err)


class RepoSeedsTest(unittest.TestCase):
    """正例：技能层 4 个种子剧本全部 rc=0（真实素材缺失即失败，防回退 S2）。"""

    def test_seeds_present(self):
        self.assertTrue(PLAYBOOKS_DIR.is_dir(), f"缺少真实剧本库：{PLAYBOOKS_DIR}")
        names = {path.name for path in PLAYBOOKS_DIR.glob("*.md")}
        self.assertTrue(set(SEEDS) <= names, sorted(names))
        self.assertIn("README.md", names)

    def test_directory_passes(self):
        rc, out, err = run_cli("--playbooks", str(PLAYBOOKS_DIR))
        self.assertEqual(rc, 0, err)
        self.assertIn("剧本 lint ok", out)

    def test_each_seed_declares_phases_section(self):
        for name in SEEDS:
            with self.subTest(seed=name):
                text = (PLAYBOOKS_DIR / name).read_text(encoding="utf-8")
                self.assertIn("## Phases", text)
                rc, _out, err = run_cli("--playbooks", str(PLAYBOOKS_DIR / name))
                self.assertEqual(rc, 0, err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
