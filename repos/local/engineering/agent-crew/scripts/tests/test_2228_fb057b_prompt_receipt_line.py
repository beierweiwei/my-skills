#!/usr/bin/env python3
"""2.2.28 FB-057②：dispatch prompt 自动追加回执要求行（--no-require-receipt 可关）。

背景（FB-057）：派单 prompt 漏写回执要求 → worker 完成后静默、helm 核收不
可见（FB-057 worker 完成静默不可见）。notify 已有 RECEIPT_LINE 自动追加
（T22），prompt 是派单主通道却没有同等保险丝。

修复契约：
1. `dispatch.py prompt` 默认在 message 尾部追加 RECEIPT_LINE（已含则不重复）；
2. `--no-require-receipt` 显式关闭；
3. agent-log 记录 receipt_required 字段（与 notify 对齐）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_fb057b", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_fb057b"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)


class Fb057bPromptReceiptLineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        registry = dispatch_mod.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps(
            {"t33-builder": {"role": "builder", "runtime": "claude"}}),
            encoding="utf-8")
        self.calls: list[list[str]] = []

    def fake_herdr(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return json.dumps({"result": {"agents": [
                {"name": "t33-builder", "pane_id": "wX:p2"}]}})
        if argv[:3] == ["herdr", "pane", "list"]:
            return json.dumps({"result": {"panes": [
                {"pane_id": "wX:p2", "focused": False}]}})
        if argv[:3] == ["herdr", "pane", "process-info"]:
            return json.dumps({"result": {"approvals": []}})
        if argv[:3] == ["herdr", "pane", "read"]:
            return "ctx 100k/1M（T69 ctx 门夹具）"
        return ""

    def prompt_message(self) -> str:
        prompts = [call for call in self.calls
                   if call[:3] == ["herdr", "agent", "prompt"]]
        self.assertEqual(1, len(prompts))
        return prompts[0][4]

    def agent_log(self) -> list[dict]:
        path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def dispatch_main(self, *args: str) -> int:
        out = io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out):
                return dispatch_mod.main(["--root", str(self.root), *args])

    def _write_msg(self, text: str) -> str:
        """2.2.32/T76/B3：--message/--message-file 只收文件路径（夹具落盘）。"""
        import tempfile as _tf
        path = Path(_tf.mkstemp(suffix=".md")[1])
        path.write_text(text, encoding="utf-8")
        return str(path)

    def test_prompt_appends_receipt_line_by_default(self):
        code = self.dispatch_main(
            "prompt", "--agent", "t33-builder",
            "--message-file", self._write_msg("做 T33 切片"), "--json")
        self.assertEqual(0, code)
        self.assertEqual("做 T33 切片\n" + dispatch_mod.RECEIPT_LINE,
                         self.prompt_message())
        row = self.agent_log()[-1]
        self.assertEqual("prompt", row["action"])
        self.assertTrue(row["receipt_required"])

    def test_prompt_opt_out_with_no_require_receipt(self):
        code = self.dispatch_main(
            "prompt", "--agent", "t33-builder",
            "--message-file", self._write_msg("纯查询"), "--no-require-receipt", "--json")
        self.assertEqual(0, code)
        self.assertEqual("纯查询", self.prompt_message())
        self.assertFalse(self.agent_log()[-1]["receipt_required"])

    def test_prompt_does_not_duplicate_receipt_line(self):
        code = self.dispatch_main(
            "prompt", "--agent", "t33-builder",
            "--message-file", self._write_msg("已含要求\n" + dispatch_mod.RECEIPT_LINE), "--json")
        self.assertEqual(0, code)
        self.assertEqual("已含要求\n" + dispatch_mod.RECEIPT_LINE,
                         self.prompt_message())

    def test_cli_help_lists_no_require_receipt(self):
        parser = dispatch_mod.build_parser()
        prompt_parser = parser._subparsers._group_actions[0].choices["prompt"]
        self.assertIn("--no-require-receipt", prompt_parser.format_help())


if __name__ == "__main__":
    unittest.main()
