#!/usr/bin/env python3
"""helm_agent_name.py 回归测试：agent 名派生与配置读取（工单 D-helm-fix）。
运行：python3 scripts/tests/test_helm_agent_name.py（或 unittest discover）。"""

from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# 随迁：脚本在 skills 布局下位于本 tests 目录的上一级 scripts/。
_SPEC_PATH = Path(__file__).resolve().parent.parent / "helm_agent_name.py"
_spec = importlib.util.spec_from_file_location("helm_agent_name", _SPEC_PATH)
han = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(han)

import re  # noqa: E402


class SlugifyTest(unittest.TestCase):
    """slugify：小写、非法字符转 -、去首尾 -；herdr agent 名合法字符 [a-z0-9_-]。"""

    def test_lowercases(self):
        self.assertEqual(han.slugify("Demo-Project"), "demo-project")

    def test_spaces_become_single_dash(self):
        self.assertEqual(han.slugify("my  project"), "my-project")

    def test_illegal_chars_become_single_dash(self):
        self.assertEqual(han.slugify("foo.bar!baz"), "foo-bar-baz")

    def test_strips_leading_trailing_dashes(self):
        self.assertEqual(han.slugify("--foo--"), "foo")

    def test_cjk_becomes_empty(self):
        self.assertEqual(han.slugify("编排技能"), "")

    def test_mixed_cjk_ascii(self):
        self.assertEqual(han.slugify("示例demo"), "demo")

    def test_output_always_legal(self):
        for raw in ["Demo", "a b c", "!!", "示例", "_x_", "a.b-c_d"]:
            self.assertRegex(han.slugify(raw), re.compile(r"^[a-z0-9_-]*$"))


class ResolveAgentNameTest(unittest.TestCase):
    """resolve_agent_name：配置 helm.agentName 优先；默认 helm-<projectName slug>；
    projectName 空则用目录名；目录名 slugify 后仍空（纯中文等）兜底 project。"""

    def _root(self, dirname: str, cfg: dict | None = None, raw: str | None = None) -> Path:
        root = Path(tempfile.mkdtemp()) / dirname
        root.mkdir(parents=True)
        if raw is not None:
            (root / ".planning").mkdir()
            (root / ".planning" / "orchestration.json").write_text(raw, encoding="utf-8")
        elif cfg is not None:
            (root / ".planning").mkdir()
            (root / ".planning" / "orchestration.json").write_text(
                json.dumps(cfg), encoding="utf-8")
        return root

    def test_configured_name_wins(self):
        root = self._root("proj", cfg={"projectName": "proj", "helm": {"agentName": "my-helm"}})
        self.assertEqual(han.resolve_agent_name(root), "my-helm")

    def test_empty_agent_name_falls_back_to_derive(self):
        # 默认模板 helm.agentName=""（空串）：与不含该键同义，走派生
        root = self._root("proj", cfg={"projectName": "proj", "helm": {"agentName": ""}})
        self.assertEqual(han.resolve_agent_name(root), "helm-proj")

    def test_default_from_project_name(self):
        root = self._root("ignored_dir", cfg={"projectName": "My Proj"})
        self.assertEqual(han.resolve_agent_name(root), "helm-my-proj")

    def test_default_without_agent_name_key(self):
        # orchestration.json 默认模板不含 helm.agentName 键 = 走派生
        root = self._root("ignored_dir", cfg={"projectName": "demo", "helm": {"tabLabel": "总舵"}})
        self.assertEqual(han.resolve_agent_name(root), "helm-demo")

    def test_empty_project_name_uses_dir_name(self):
        root = self._root("demo_repo", cfg={"projectName": ""})
        self.assertEqual(han.resolve_agent_name(root), "helm-demo_repo")

    def test_no_config_file_uses_dir_name(self):
        root = self._root("bare-repo")
        self.assertEqual(han.resolve_agent_name(root), "helm-bare-repo")

    def test_broken_config_falls_back_to_dir_name(self):
        root = self._root("resilient", raw="{broken json")
        self.assertEqual(han.resolve_agent_name(root), "helm-resilient")

    def test_cjk_dir_name_falls_back_to_project(self):
        # 纯中文目录名 slugify 后为空：兜底固定词 project（撞名时由 start-helm 友好提示引导配置）
        root = self._root("编排技能")
        self.assertEqual(han.resolve_agent_name(root), "helm-project")

    def test_derived_name_is_legal(self):
        root = self._root("Some Repo.v2", cfg={"projectName": "Some Repo.v2"})
        self.assertRegex(han.resolve_agent_name(root), re.compile(r"^[a-z0-9_-]+$"))


class MainOutputTest(unittest.TestCase):
    """命令行输出为可直接 eval 的 KEY=value 赋值行（bash start-helm.sh 消费）。"""

    def test_eval_safe_assignments(self):
        root = Path(tempfile.mkdtemp()) / "proj-x"
        root.mkdir(parents=True)
        (root / ".planning").mkdir()
        (root / ".planning" / "orchestration.json").write_text(
            json.dumps({"projectName": "proj X", "helm": {"tabLabel": "总舵"}}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(_SPEC_PATH), str(root)],
            capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        values = {}
        for line in proc.stdout.strip().splitlines():
            key, _, quoted = line.partition("=")
            self.assertRegex(key, re.compile(r"^[A-Z_]+$"))
            values[key] = shlex.split(quoted)[0]
        for expected in ["AGENT_NAME", "TAB_LABEL", "EXPECTED_MODEL", "PORTFOLIO_DIR", "DOC_CONTEXT"]:
            self.assertIn(expected, values)
        self.assertEqual(values["AGENT_NAME"], "helm-proj-x")
        self.assertEqual(values["TAB_LABEL"], "总舵")


if __name__ == "__main__":
    unittest.main(verbosity=2)
