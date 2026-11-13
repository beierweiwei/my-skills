#!/usr/bin/env python3
"""2.2.28 FB-051：已读游标两键不一致（文件名 stem vs 内容 receipt_id）防回退。

背景（FB-051）：CASE 侧回执文件名带时间戳前缀（如
`20261111T063200Z-case-1.json`），内容 receipt_id 是 `case-1`。旧逻辑
`--mark-read` 按 `<id>.json` 文件存在性判缺失、`--unread` 按内容 receipt_id
过滤，两键不一致 → 这类回执永远标不上已读（`--unread` 永久误报）。

修复契约：读 inbox 建 stem→content-id 映射，两键任一命中即可标读；游标
统一写内容 receipt_id（canonical），与 `--unread` 过滤键一致。

1. 显式 `--mark-read <content-id>` 命中带前缀文件名 → 标记成功，游标写
   content-id，`--unread` 不再列出；
2. 显式 `--mark-read <stem>` 同样命中 → 游标写 canonical（content-id）；
3. `--mark-read --all` 不再 skip 两键不一致项；
4. 两键都不命中（真不存在）仍整体拒绝、无副作用；
5. 回执文件只读不改写（R1 inbox 文件仍是真源）。
"""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

_SPEC_PATH = Path(__file__).resolve().parent.parent / "crew-msg.py"
_spec = importlib.util.spec_from_file_location("crew_msg_fb051", _SPEC_PATH)
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def _legacy_receipt(receipt_id: str) -> dict:
    return {
        "receipt_id": receipt_id, "from": "stub", "runtime": "qoder",
        "task": "T-legacy", "status": "done", "commit": "",
        "changed_files": [], "evidence": [], "work_slot": "stub",
        "findings": [], "summary": "legacy", "ts": "2026-11-11T06:32:00Z",
    }


class Fb051KeyMappingTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.crew = Path(self._tmp.name) / ".git" / "crew"
        self.inbox = cm.inbox_dir(self.crew)
        self.inbox.mkdir(parents=True, exist_ok=True)
        self.cursor_file = self.crew / "inbox-read-cursor.json"
        # 夹具：文件名带时间戳前缀，内容 receipt_id 无前缀（FB-051 场景）
        self.legacy_path = self.inbox / "20261111T063200Z-case-1.json"
        self.legacy_path.write_text(
            json.dumps(_legacy_receipt("case-1"), ensure_ascii=False),
            encoding="utf-8")

    def _ls(self, *extra):
        import io
        from contextlib import redirect_stderr, redirect_stdout
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cm.main(["ls", "--crew-dir", str(self.crew), *extra])
        return code, out.getvalue(), err.getvalue()

    def _unread_ids(self):
        code, out, err = self._ls("--unread", "--json")
        self.assertEqual(code, 0, err)
        return [item["receipt_id"] for item in json.loads(out)]

    def test_explicit_content_id_marks_prefixed_filename(self):
        """显式传内容 id：命中带前缀文件名 → 标记成功、--unread 不再误报。"""
        self.assertEqual(self._unread_ids(), ["case-1"])
        code, out, err = self._ls("--mark-read", "case-1", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["marked"], ["case-1"])
        self.assertEqual(payload["skipped"], [])
        cursor = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        self.assertEqual(set(cursor["read"]), {"case-1"})
        self.assertEqual(self._unread_ids(), [])
        # R1：回执文件内容不得改写
        data = json.loads(self.legacy_path.read_text(encoding="utf-8"))
        self.assertEqual(data["receipt_id"], "case-1")

    def test_explicit_stem_marks_canonical_content_id(self):
        """显式传文件名 stem：同样命中，游标写 canonical（内容 id）。"""
        code, out, err = self._ls(
            "--mark-read", "20261111T063200Z-case-1", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["marked"], ["case-1"])
        cursor = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        self.assertEqual(set(cursor["read"]), {"case-1"})
        self.assertEqual(self._unread_ids(), [])

    def test_mark_all_no_longer_skips_key_mismatch(self):
        """--all 对两键不一致项照常标记，不再 skip。"""
        code, out, err = self._ls("--mark-read", "--all", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["marked"], ["case-1"])
        self.assertEqual(payload["skipped"], [])
        self.assertEqual(self._unread_ids(), [])
        self.assertTrue(self.legacy_path.exists())

    def test_unknown_id_still_rejected_without_side_effect(self):
        """两键都不命中（真不存在）仍整体拒绝、不写游标。"""
        code, _, err = self._ls("--mark-read", "case-1", "missing")
        self.assertEqual(code, 1)
        self.assertIn("missing", err)
        self.assertFalse(self.cursor_file.exists())
        self.assertEqual(self._unread_ids(), ["case-1"])

    def test_stem_content_collision_marks_correct_object(self):
        """4b①：stem 碰撞（文件 mmm.json 内容 id=xxx + 文件 xxx.json 内容 id=yyy）。

        subB 实证旧单值 dict 后写覆盖先写：--mark-read xxx 标错为 yyy、A 永久
        未读。分表后内容 id 优先解析 → xxx 标 A（canonical=xxx），B 不受影响；
        冲突进 warning 提示。
        """
        self.legacy_path.write_text(
            json.dumps(_legacy_receipt("xxx"), ensure_ascii=False),
            encoding="utf-8")  # 20261111T063200Z-case-1.json 内容 id=xxx
        other = self.inbox / "xxx.json"
        other.write_text(
            json.dumps(_legacy_receipt("yyy"), ensure_ascii=False),
            encoding="utf-8")
        code, out, err = self._ls("--mark-read", "xxx", "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertEqual(payload["marked"], ["xxx"],
                         "内容 id 优先：xxx 必须标到内容 id=xxx 的回执（A）")
        self.assertIn("同时是内容 id",
                      payload["warning"] or "", "键碰撞必须进 warning")
        cursor = json.loads(self.cursor_file.read_text(encoding="utf-8"))
        self.assertEqual(set(cursor["read"]), {"xxx"})
        # B（内容 yyy）不受影响：仍未读、可按内容 id 标读
        self.assertEqual(self._unread_ids(), ["yyy"])
        code, out, _err = self._ls("--mark-read", "yyy", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["marked"], ["yyy"])


if __name__ == "__main__":
    unittest.main()
