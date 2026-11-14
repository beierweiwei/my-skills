#!/usr/bin/env python3
"""2.2.32 工单 F/T72：crew-model.py settings `agents` 键类型校验。

四态：agents=null / 字符串 / 列表 / agents.env 值非 str → rc=1 + 友好信息
（指明文件、期望类型、实际类型）+ 目标 settings 零写入（失败侧安全）。
正常路径（agents 缺省=旧行为）不回归。fake HOME 夹具，真实 settings 不动。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_SPEC = _TESTS_DIR.parent / "crew-model.py"
_spec = importlib.util.spec_from_file_location("crew_model_t72", _SPEC)
cm = importlib.util.module_from_spec(_spec)
sys.modules["crew_model_t72"] = cm
_spec.loader.exec_module(cm)

GOOD_OVERRIDES = {"role-builder": {"modelConfig": {"model": "old"}}}


class AgentsKeyTypeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.settings = self.home / ".qoder-cn" / "settings.json"

    def _write(self, payload) -> None:
        self.settings.parent.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(payload), encoding="utf-8")

    def _run(self) -> tuple[int, str, str]:
        argv = ["settings", "--runtime", "qoder", "--model", "glm-5.3", "--apply"]
        out, err = io.StringIO(), io.StringIO()
        env = {**os.environ, "HOME": str(self.home)}
        with mock.patch.dict(os.environ, env, clear=False), \
                redirect_stdout(out), redirect_stderr(err):
            code = cm.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _assert_rejected(self, needle: str) -> None:
        code, out, err = self._run()
        self.assertEqual(1, code, out + err)
        combined = out + err
        self.assertIn(needle, combined)
        self.assertIn(str(self.settings), combined)  # 指明文件
        # 零写入：内容与写入前逐字节一致
        self.assertEqual(
            json.dumps(json.loads(self.settings.read_text(encoding="utf-8")),
                       ensure_ascii=False, sort_keys=True),
            json.dumps(self._before, ensure_ascii=False, sort_keys=True))

    def test_agents_null_rejected_friendly(self):
        self._before = {"agents": None, "other": 1}
        self._write(self._before)
        self._assert_rejected("agents")

    def test_agents_string_rejected_with_actual_type(self):
        self._before = {"agents": "oops"}
        self._write(self._before)
        self._assert_rejected("str")

    def test_agents_list_rejected_with_actual_type(self):
        self._before = {"agents": ["oops"]}
        self._write(self._before)
        self._assert_rejected("list")

    def test_agents_env_non_str_value_rejected(self):
        self._before = {"agents": {"env": {"FOO": 123},
                                   "overrides": GOOD_OVERRIDES}}
        self._write(self._before)
        self._assert_rejected("env")

    def test_root_not_dict_rejected(self):
        self._before = ["not", "a", "dict"]
        self._write(self._before)
        code, out, err = self._run()
        self.assertEqual(1, code, out + err)
        self.assertIn("settings 根必须是 JSON 对象", out + err)

    def test_agents_absent_keeps_legacy_behavior(self):
        # agents 缺省（旧格式）：沿用既有「overrides 为空」口径而非新校验炸
        self._before = {"other": 1}
        self._write(self._before)
        code, out, err = self._run()
        self.assertEqual(1, code, out + err)
        self.assertIn("agents.overrides", out + err)


if __name__ == "__main__":
    unittest.main(verbosity=2)
