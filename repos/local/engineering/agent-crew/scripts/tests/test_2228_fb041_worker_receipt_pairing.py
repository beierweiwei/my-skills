#!/usr/bin/env python3
"""2.2.28 FB-041/048(a)+FB-057③：worker（runner/scout）模板「报告+回执成对」防回退。

背景（FB-041/048(a) 复发 + FB-057③）：reviewer 模板 2.2.18 已加「报告落盘后
立即发回执」硬门槛（test_2218_fb041_receipt_pairing.py），但 runner/scout
只写「完成后按 R2 字段写回执」——报告落盘≠回执已发，scout/runner 仍出现
「报告落盘、无回执、firstmate 追要」的复发。FB-057③ 将 scout/runner 回执
纪律与 FB-041/048(a) 合并实施。

钉死契约（三栈 qoder/opencode/claude 同改）：
1. runner/scout 模板动作清单含「报告+回执成对」与「无回执不得结束回合」
   硬门槛措辞；
2. reviewer 三栈保持既有硬门槛（防回退）；
3. 位置约束：runner 断言钉在动作清单条目 4 内、scout 钉在条目 5 内，防文本
   漂移到无关段落。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

STACKS = ("qoder", "opencode", "claude")
PAIR_NEEDLE = "报告+回执成对"
GATE_NEEDLE = "无回执不得结束回合"
NUMBERED_ITEM = re.compile(r"^(\d+)\. ", re.MULTILINE)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def action_item(text: str, number: str) -> str:
    """取动作清单第 number 条正文（到下一个编号条目或文档尾），防漂移。"""
    begin = text.find("## 动作清单")
    if begin < 0:
        raise AssertionError("缺少「## 动作清单」章节")
    marker = re.search(rf"^{number}\. ", text[begin:], re.MULTILINE)
    if marker is None:
        raise AssertionError(f"动作清单缺少条目 {number}")
    start = begin + marker.start()
    rest = text[start + len(f"{number}. "):]
    nxt = NUMBERED_ITEM.search(rest)
    return rest[:nxt.start()] if nxt else rest


class Fb041WorkerReceiptPairingTest(unittest.TestCase):
    def test_runner_and_scout_have_pairing_gate_in_all_stacks(self):
        for stack in STACKS:
            for role, item_no in (("runner", "4"), ("scout", "5")):
                with self.subTest(stack=stack, role=role):
                    text = read(f"templates/agents/{stack}/{role}.md")
                    if stack == "opencode":
                        # opencode 清单编号不同：runner/scout 回执条目均取实际编号
                        item = None
                        for no in ("4", "5", "6"):
                            try:
                                candidate = action_item(text, no)
                            except AssertionError:
                                continue
                            if "R2" in candidate:
                                item = candidate
                                break
                        if item is None:
                            raise AssertionError(
                                f"opencode {role} 动作清单缺 R2 回执条目")
                    else:
                        item = action_item(text, item_no)
                    self.assertIn(PAIR_NEEDLE, item,
                                  f"{stack}/{role} 条目缺「{PAIR_NEEDLE}」")
                    self.assertIn(GATE_NEEDLE, item,
                                  f"{stack}/{role} 条目缺「{GATE_NEEDLE}」")

    def test_reviewer_keeps_existing_pairing_gate(self):
        for stack in STACKS:
            with self.subTest(stack=stack):
                text = read(f"templates/agents/{stack}/reviewer.md")
                self.assertIn("报告落盘后立即发回执", text)
                self.assertIn(PAIR_NEEDLE, text)
                self.assertIn(GATE_NEEDLE, text)

    def test_gates_live_in_action_list_not_prose(self):
        """硬门槛措辞必须在动作清单条目内，不得漂移到引用/边界等叙述段。"""
        for stack in STACKS:
            for role in ("runner", "scout"):
                with self.subTest(stack=stack, role=role):
                    text = read(f"templates/agents/{stack}/{role}.md")
                    begin = text.find("## 动作清单")
                    body = text[begin:] if begin >= 0 else ""
                    self.assertIn(PAIR_NEEDLE, body)
                    self.assertIn(GATE_NEEDLE, body)


if __name__ == "__main__":
    unittest.main()
