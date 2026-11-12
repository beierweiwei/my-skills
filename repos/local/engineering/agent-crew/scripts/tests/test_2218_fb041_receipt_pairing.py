#!/usr/bin/env python3
"""2.2.18 FB-041 评审/终验「报告 + 回执」成对防回退断言。

背景：2.2.17 两次实测中 Reviewer/Verifier 写出报告却未发回执（firstmate 追要
才补）。根因是「报告落盘后必须发回执」未设为完成门槛，firstmate 收口只核回执、
不核成对。本测试钉死三类落点，防措辞漂移/删段回退：

1. qoder/opencode reviewer.md 动作清单：硬完成门槛——报告落盘后立即发回执
   （crew-msg.py write），回执含报告路径 + 结论（PASS/BLOCK/阻断+建议数）+
   工位处置，缺回执视为评审/终验未完成；
2. qoder/opencode firstmate.md 收口条目：评审/终验类核「报告 + 回执」成对，
   缺回执退回原工位补发，不得以报告文件替代回执；
3. `templates/_templates/ticket.md` 回执要求段：评审/终验类成对要求；
4. opencode reviewer frontmatter：`python3 scripts/crew-msg.py write*` 为 allow
   （S-8：否则发回执每条弹权限确认，无人值守卡住），且 edit deny / task deny /
   SendMessage false 与 `"*": ask` 底限不变。

位置约束：reviewer 断言钉在「## 动作清单」段内；firstmate 断言钉在收口条目
（「收口：核 F1 三件」行到下一个顶层编号条目之间），防文本漂移。
`templates/playbooks/release.md` 不在本单范围（Phase 8 成对由 FB-043 补），
本测试不对其做断言。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

REVIEWERS = (
    "templates/agents/qoder/reviewer.md",
    "templates/agents/opencode/reviewer.md",
)
FIRSTMATE = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
)
TICKET = "templates/_templates/ticket.md"
OPENCODE_REVIEWER = "templates/agents/opencode/reviewer.md"

GATE_NEEDLE = "报告落盘后立即发回执"
CLOSE_NEEDLE = "收口：核 F1 三件"
NUMBERED_ITEM = re.compile(r"^\d+\. ")
FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def section(text: str, start: str, end: str | None) -> str:
    """取 [start, end) 正文；缺 start/end 直接失败，避免静默空段。"""
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"缺少章节起点：{start}")
    if end is None:
        return text[begin:]
    tail = text.find(end, begin + len(start))
    if tail < 0:
        raise AssertionError(f"缺少章节终点：{end}")
    return text[begin:tail]


def line_with(text: str, needle: str, rel: str) -> str:
    """定位唯一含 needle 的行；0 行或多行都失败，防止断言漂到错误段落。"""
    hits = [line for line in text.splitlines() if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{rel} 应恰有 1 行含 {needle!r}，实际 {len(hits)} 行")
    return hits[0]


def close_block(rel: str) -> list[str]:
    """收口条目块：从「收口：核 F1 三件」行到下一个顶层编号条目（不含）。"""
    lines = read(rel).splitlines()
    starts = [i for i, line in enumerate(lines) if CLOSE_NEEDLE in line]
    if len(starts) != 1:
        raise AssertionError(
            f"{rel} 应恰有 1 行含 {CLOSE_NEEDLE!r}，实际 {len(starts)} 行")
    begin = starts[0]
    for i in range(begin + 1, len(lines)):
        if NUMBERED_ITEM.match(lines[i]):
            return lines[begin:i]
    return lines[begin:]


class ReviewerHardGateTest(unittest.TestCase):
    """验收 1：reviewer 动作清单含「报告落盘后立即发回执」硬门槛。"""

    def test_action_list_has_receipt_pairing_gate(self):
        for rel in REVIEWERS:
            text = read(rel)
            chunk = section(
                text,
                "## 动作清单",
                "## 上下文与工位" if "## 上下文与工位" in text else None,
            )
            with self.subTest(rel=rel):
                line = line_with(chunk, GATE_NEEDLE, rel)
                for phrase in (
                    "crew-msg.py write",
                    "报告路径",
                    "PASS/BLOCK",
                    "阻断+建议数",
                    "工位处置",
                    "缺回执视为评审/终验未完成",
                ):
                    self.assertIn(phrase, line, f"{rel} 回执硬门槛缺少：{phrase}")


class FirstmateClosurePairingTest(unittest.TestCase):
    """验收 2：firstmate 收口条目核「报告 + 回执」成对。"""

    def test_closure_block_requires_pairing(self):
        for rel in FIRSTMATE:
            with self.subTest(rel=rel):
                block = close_block(rel)
                hits = [
                    line for line in block
                    if "评审/终验类" in line and "报告 + 回执" in line
                ]
                self.assertEqual(
                    len(hits), 1,
                    f"{rel} 收口条目内应恰有 1 行评审/终验成对核对，实际 {len(hits)} 行",
                )
                line = hits[0]
                for phrase in (
                    "「报告 + 回执」成对",
                    "缺回执",
                    "退回原工位补发",
                    "不得以报告文件替代回执",
                    "报告路径",
                    "结论",
                    "工位处置",
                ):
                    self.assertIn(phrase, line, f"{rel} 收口成对核对缺少：{phrase}")


class TicketReceiptPairingTest(unittest.TestCase):
    """验收 3：工单模板回执要求段含评审/终验类成对要求。"""

    def test_receipt_section_requires_pairing(self):
        chunk = section(read(TICKET), "## 回执要求", None)
        hits = [line for line in chunk.splitlines() if "评审/终验类" in line]
        self.assertEqual(
            len(hits), 1, f"{TICKET} 回执要求段应恰有 1 行评审/终验成对要求")
        line = hits[0]
        for phrase in (
            "报告路径",
            "结论",
            "PASS/BLOCK",
            "阻断+建议数",
            "工位处置",
            "缺回执",
            "退回补发",
        ):
            self.assertIn(phrase, line, f"{TICKET} 成对要求缺少：{phrase}")


class OpencodeReviewerReceiptPermissionTest(unittest.TestCase):
    """补口 S-8：回执硬门槛可执行——crew-msg.py write 免确认，安全底限不变。"""

    def frontmatter(self) -> str:
        match = FRONTMATTER.match(read(OPENCODE_REVIEWER))
        if not match:
            raise AssertionError(f"{OPENCODE_REVIEWER} 缺少 frontmatter")
        return match.group(1)

    def test_receipt_write_allowed_without_prompt(self):
        bash_lines = [
            line for line in self.frontmatter().splitlines()
            if line.strip().startswith("bash:")
        ]
        self.assertEqual(
            len(bash_lines), 1, f"{OPENCODE_REVIEWER} 应恰有 1 行 bash 权限")
        line = bash_lines[0]
        self.assertIn(
            '"python3 scripts/crew-msg.py write*": allow', line,
            "回执命令未设为 allow：无人值守发回执会逐条弹确认",
        )
        self.assertIn('"*": ask', line, "bash 底限不再是 ask")

    def test_safety_guards_unchanged(self):
        front = self.frontmatter()
        for phrase in ("edit: deny", "task: deny", "SendMessage: false"):
            self.assertIn(phrase, front, f"{OPENCODE_REVIEWER} 安全底限被改：{phrase}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
