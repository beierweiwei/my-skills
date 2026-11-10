#!/usr/bin/env python3
"""init.py 剧本库骨架：项目层 README 生成/AGENTS 契约字符串/幂等/不覆盖项目自有剧本。"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("init_playbooks_mod", _SCRIPTS / "init.py")
init_mod = importlib.util.module_from_spec(_spec)
sys.modules["init_playbooks_mod"] = init_mod
_spec.loader.exec_module(init_mod)

TEMPLATES = _SCRIPTS.parent / "templates"
SEEDS = ("autonomous-run.md", "babysit.md", "session-pickup.md", "investigation.md")


def build_templates(base: Path) -> Path:
    """复制真实模板；真实素材缺失时直接断言失败（防回退 S2，不做最小样本回退）。"""
    dest = base / "templates"
    shutil.copytree(TEMPLATES, dest)
    required = [dest / "project/playbooks-README.md", dest / "playbooks/README.md",
                *[dest / "playbooks" / name for name in SEEDS]]
    missing = [path.relative_to(dest).as_posix() for path in required if not path.is_file()]
    assert not missing, f"真实剧本库素材缺失（防回退 S2）：{missing}"
    return dest


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(root: Path, exclude: tuple[str, ...] = ()) -> dict[str, str]:
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or rel.startswith(".git/") or rel in exclude:
            continue
        out[rel] = sha(path)
    return out


class InitPlaybooksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.root = base / "proj"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        self.templates = build_templates(base)

    def run_init(self, *extra: str) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = init_mod.run(["--root", str(self.root),
                                 "--templates", str(self.templates), *extra])
        return code, out.getvalue()

    def test_project_readme_generated_with_contract(self):
        code, out = self.run_init("--yes")
        self.assertEqual(code, 0, out)
        readme = self.root / ".planning/playbooks/README.md"
        self.assertTrue(readme.is_file())
        text = readme.read_text(encoding="utf-8")
        self.assertIn("覆盖", text)
        self.assertIn(".planning/playbooks", text)

    def test_agents_block_contains_playbook_contract(self):
        code, out = self.run_init("--yes")
        self.assertEqual(code, 0, out)
        agents = (self.root / "AGENTS.md").read_text(encoding="utf-8")
        for token in ("剧本库（playbook 路由）", "templates/playbooks/",
                      ".planning/playbooks/", "F6–F12"):
            self.assertIn(token, agents)

    def test_second_init_is_idempotent(self):
        self.assertEqual(self.run_init("--yes")[0], 0)
        before = snapshot(self.root, exclude=("docs/crew/install-log.jsonl",))
        code, out = self.run_init("--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("[跳过] .planning/playbooks/README.md", out)
        after = snapshot(self.root, exclude=("docs/crew/install-log.jsonl",))
        self.assertEqual(after, before)

    def test_custom_readme_and_playbook_not_overwritten(self):
        playbooks = self.root / ".planning/playbooks"
        playbooks.mkdir(parents=True)
        custom_readme = "# 项目自有剧本库说明（勿覆盖）\n"
        (playbooks / "README.md").write_text(custom_readme, encoding="utf-8")
        (playbooks / "my-play.md").write_text(
            "# 剧本：自定义\n\n## Phases\n\n### Phase 1: 自定义\n**Status:** pending\n",
            encoding="utf-8")
        code, out = self.run_init("--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(
            (playbooks / "README.md").read_text(encoding="utf-8"), custom_readme)
        self.assertTrue((playbooks / "my-play.md").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
