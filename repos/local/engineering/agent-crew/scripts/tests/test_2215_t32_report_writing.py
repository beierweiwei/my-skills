#!/usr/bin/env python3
"""2.2.15 T32 报告落盘方式防回退断言（FB-029）。

统一口径：报告/运行记录落盘禁止无引号 heredoc 与命令替换；用单引号
heredoc 写 `/tmp` 再 `cp`，或直接使用 Write 工具。新会话按此口径落盘
不触发 Qoder 权限弹窗、不卡住 verifier/会话。

覆盖落点：
- `templates/playbooks/release.md`：退出条件 + Phase 8 收口步骤；
- `templates/playbooks/maintain-verification.md`：运行记录段落；
- `templates/_templates/ticket.md`：回执要求段落（派单写明落盘方式）；
- `templates/agents/{qoder,opencode}/{firstmate,reviewer,runner}.md`：动作清单
  （reviewer 只读、无 Write，口径额外给出分段写/helper 代写退路）。

反向断言：目标文档不得引入示例性无引号 heredoc（含注释），只允许
单引号 heredoc 口径的文字描述，不提供代码示例。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

CLAUSE = (
    "禁止无引号 heredoc/命令替换；单引号 heredoc 写 `/tmp` 再 `cp`，或 Write 工具"
)

TARGETS = (
    "templates/playbooks/release.md",
    "templates/playbooks/maintain-verification.md",
    "templates/_templates/ticket.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/qoder/reviewer.md",
    "templates/agents/qoder/runner.md",
    "templates/agents/opencode/firstmate.md",
    "templates/agents/opencode/reviewer.md",
    "templates/agents/opencode/runner.md",
)

ROLE_TARGETS = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/qoder/reviewer.md",
    "templates/agents/qoder/runner.md",
    "templates/agents/opencode/firstmate.md",
    "templates/agents/opencode/reviewer.md",
    "templates/agents/opencode/runner.md",
)

# 无引号 heredoc 模式（定界符未加引号）；加引号定界符属允许口径，不匹配。
UNQUOTED_HEREDOC = re.compile("<" + r"<-?\s*([A-Za-z_][A-Za-z0-9_]*)")


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def section(text: str, start: str, end: str | None) -> str:
    """取 [start, end) 正文；缺 start 直接失败，避免静默空段。"""
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"缺少章节起点：{start}")
    if end is None:
        return text[begin:]
    tail = text.find(end, begin + len(start))
    if tail < 0:
        raise AssertionError(f"缺少章节终点：{end}")
    return text[begin:tail]


class ReportWritingClauseTest(unittest.TestCase):
    """统一落盘口径存在于全部收口/终验落点。"""

    def test_each_target_has_clause(self):
        for rel in TARGETS:
            with self.subTest(rel=rel):
                self.assertIn(CLAUSE, read(rel), f"{rel} 缺少落盘口径")

    def test_release_exit_condition_has_clause(self):
        lines = [
            line
            for line in read("templates/playbooks/release.md").splitlines()
            if line.startswith("- 退出：")
        ]
        self.assertTrue(lines, "release 缺少「- 退出：」行")
        for line in lines:
            with self.subTest(line=line):
                self.assertIn(CLAUSE, line)

    def test_release_phase8_has_clause(self):
        text = read("templates/playbooks/release.md")
        chunk = section(text, "### Phase 8:", "\n## 跳过规则")
        self.assertIn(CLAUSE, chunk, "release Phase 8 收口步骤缺少落盘口径")

    def test_maintain_verification_record_section_has_clause(self):
        text = read("templates/playbooks/maintain-verification.md")
        chunk = section(text, "## 运行记录", "\n## Phases")
        self.assertIn(CLAUSE, chunk, "maintain-verification 运行记录缺少落盘口径")

    def test_ticket_requirement_section_has_clause(self):
        text = read("templates/_templates/ticket.md")
        chunk = section(text, "## 回执要求", None)
        self.assertIn(CLAUSE, chunk, "工单模板回执要求缺少落盘口径")

    def test_role_templates_clause_in_action_list(self):
        for rel in ROLE_TARGETS:
            text = read(rel)
            chunk = section(text, "## 动作清单", "## 上下文与工位" if "## 上下文与工位" in text else None)
            with self.subTest(rel=rel):
                self.assertIn(CLAUSE, chunk, f"{rel} 动作清单缺少落盘口径")

    def test_no_unquoted_heredoc_examples(self):
        for rel in TARGETS:
            for lineno, line in enumerate(read(rel).splitlines(), 1):
                with self.subTest(rel=rel, lineno=lineno):
                    self.assertIsNone(
                        UNQUOTED_HEREDOC.search(line),
                        f"{rel}:{lineno} 引入示例性无引号 heredoc：{line.strip()}",
                    )


if __name__ == "__main__":
    unittest.main()
