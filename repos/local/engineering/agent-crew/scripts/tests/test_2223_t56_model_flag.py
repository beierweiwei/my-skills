#!/usr/bin/env python3
"""2.2.23 T56：模型传参改 --model + refresh-block configHash 重算。

行为断言（红→绿）：
- crew-claude.py 不再注入 env ANTHROPIC_MODEL（静态防回退）；`--model` 追加口径在源码中。
- cheatsheet backend=claude 行不再写「经 ANTHROPIC_MODEL 传参」，改为 --model。
- init --refresh-block 块最新但 configHash 过期 → 重算落 manifest + install-log
  （files 为空、skillVersion 不动）；configHash 一致时保持零写入。
"""

from __future__ import annotations

import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent
SKILL = _SCRIPTS.parent

_init_spec = importlib.util.spec_from_file_location("init_t56", _SCRIPTS / "init.py")
init_lib = importlib.util.module_from_spec(_init_spec)
sys.modules["init_t56"] = init_lib
_init_spec.loader.exec_module(init_lib)


def make_skill(target: Path) -> Path:
    """复制当前技能为最小源（scripts+templates+SKILL.md）。"""
    target.mkdir(parents=True)
    for name in ("scripts", "templates"):
        shutil.copytree(SKILL / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "tests"))
    shutil.copy2(SKILL / "SKILL.md", target / "SKILL.md")
    return target


class ModelFlagStaticTest(unittest.TestCase):
    def test_crew_claude_uses_model_flag_not_env(self):
        text = (_SCRIPTS / "crew-claude.py").read_text(encoding="utf-8")
        self.assertIn('argv.extend(["--model", model])', text)
        self.assertNotIn('env["ANTHROPIC_MODEL"] = model', text)
        self.assertNotIn('"ANTHROPIC_MODEL"] = model', text)

    def test_cheatsheet_backend_claude_line_updated(self):
        text = (SKILL / "references/herdr-cheatsheet.md").read_text(encoding="utf-8")
        line = next(l for l in text.splitlines() if "**backend=claude**" in l)
        self.assertIn("--model", line)
        self.assertNotIn("ANTHROPIC_MODEL", line)


class RefreshBlockConfigHashTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls._tmp.cleanup)
        cls.base = Path(cls._tmp.name)
        cls.skill = make_skill(cls.base / "skill-copy")

    def setUp(self):
        self.root = self.base / f"proj-{self._testMethodName}"
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        proc = self.run_init("--yes")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    # ---------- 夹具工具 ----------

    def run_init(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.skill / "scripts/init.py"),
             "--root", str(self.root), *args],
            capture_output=True, text=True)

    def manifest(self) -> dict:
        return json.loads(
            (self.root / "docs/crew/manifest.json").read_text(encoding="utf-8"))

    def manifest_bytes(self) -> bytes:
        return (self.root / "docs/crew/manifest.json").read_bytes()

    def config(self) -> dict:
        return json.loads(
            (self.root / ".planning/orchestration.json").read_text(encoding="utf-8"))

    def write_config(self, data: dict) -> None:
        (self.root / ".planning/orchestration.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def install_log_lines(self) -> list[dict]:
        path = self.root / "docs/crew/install-log.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # ---------- 行为 ----------

    def test_stale_config_hash_recomputed_on_unchanged_block(self):
        config = self.config()
        config["agents"]["roles"]["builder"]["model"] = "glm-5.3-flash"
        self.write_config(config)
        self.assertNotEqual(self.manifest()["configHash"],
                            init_lib.config_hash(config))

        proc = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("块内容已是最新", proc.stdout)
        self.assertIn("已重算 manifest configHash", proc.stdout)
        self.assertEqual(init_lib.config_hash(self.config()),
                         self.manifest()["configHash"])
        # skillVersion 不动（T33 口径：版本写回仍走 upgrade/init 完整路径）
        self.assertEqual(self.manifest()["skillVersion"],
                         str(self.config().get("skillVersion") or ""))
        entries = [e for e in self.install_log_lines()
                   if e.get("action") == "refresh-block"]
        self.assertTrue(entries, "install-log 缺 refresh-block 记录")
        self.assertEqual([], entries[-1]["files"])
        self.assertEqual(init_lib.config_hash(self.config()),
                         entries[-1]["configHash"])

    def test_matching_config_hash_keeps_zero_write(self):
        before = self.manifest_bytes()
        proc = self.run_init("--refresh-block", "--confirm")
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("块内容已是最新，无需重渲染", proc.stdout)
        self.assertNotIn("已重算", proc.stdout)
        self.assertEqual(before, self.manifest_bytes())


if __name__ == "__main__":
    unittest.main()
