#!/usr/bin/env python3
"""2.2.33 工单 02-B：FB-064 派单写法文档漂移断言。

dispatch prompt/notify 自 2.2.32/T76 起只收文件路径；dispatch start 自
2.2.32/T76/C2 起拒绝手传 --permission-mode。模板与规则文档不得再教旧写法：
- 三栈 firstmate/helm 模板：派单命令示例只用 --message-file，不含可用的
  `--message "$(cat` 内联写法；firstmate 各含「先落临时文件」动作；
- qoder/claude firstmate 与 crew-rules D8：不教手传 --permission-mode；
- CHANGELOG 2.2.33 段标注两处不兼容变更。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent.parent
AGENTS = SKILL / "templates" / "agents"
STACKS = ("qoder", "claude", "opencode")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class DispatchMessageFileTest(unittest.TestCase):
    # ---------- --message-file 统一 ----------

    def test_firstmate_templates_promote_message_file(self):
        for stack in STACKS:
            text = read(AGENTS / stack / "firstmate.md")
            self.assertIn("--message-file", text, f"{stack}/firstmate.md 缺 --message-file")
            self.assertIn("先落临时文件", text,
                          f"{stack}/firstmate.md 缺「先落临时文件」动作")

    def test_helm_templates_keep_message_file(self):
        for stack in STACKS:
            text = read(AGENTS / stack / "helm.md")
            self.assertIn("--message-file", text, f"{stack}/helm.md 缺 --message-file")

    def test_no_inline_cat_dispatch_writing(self):
        """`--message "$(cat ...)` 可用写法清零（反例说明句不含该精确序列）。"""
        for stack in STACKS:
            for role in ("helm.md", "firstmate.md"):
                text = read(AGENTS / stack / role)
                self.assertNotIn('--message "$(cat', text,
                                 f"{stack}/{role} 残留可用 $(cat) 内联写法")

    def test_dispatch_skill_uses_message_file(self):
        text = read(SKILL / "templates/skills/firstmate-dispatch/SKILL.md")
        self.assertIn("--message-file", text)

    # ---------- D8 权限口径：不教手传 ----------

    def test_firstmate_templates_do_not_teach_permission_flag(self):
        """模板命令口径不得再教手传 --permission-mode bypass_permissions。"""
        for stack in STACKS:
            for role in ("firstmate.md", "helm.md"):
                text = read(AGENTS / stack / role)
                self.assertNotIn("--permission-mode bypass_permissions", text,
                                 f"{stack}/{role} 教手传 --permission-mode")

    def test_crew_rules_d8_no_manual_permission_flag(self):
        text = read(SKILL / "references/crew-rules.md")
        match = re.search(r"^- \*\*D8 YOLO 派单\*\*：(.*?)(?=^- \*\*D9|\Z)",
                          text, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(match, "crew-rules.md 缺 D8 条目")
        body = match.group(1)
        self.assertIn("不手传", body, "D8 未写明不手传口径")
        self.assertNotIn("一律带 `--permission-mode", body, "D8 残留手传祈使句")

    def test_cheatsheet_no_manual_permission_teaching(self):
        text = read(SKILL / "references/herdr-cheatsheet.md")
        for line in text.splitlines():
            if "dispatch.py start" in line and "permission-mode" in line:
                self.assertNotIn("一律带", line,
                                 f"cheatsheet 教手传：{line[:60]}")

    # ---------- CHANGELOG 不兼容标注 ----------

    def test_changelog_2233_marks_breaking_changes(self):
        text = read(SKILL / "CHANGELOG.md")
        match = re.search(r"^## 2\.2\.33\b", text, re.MULTILINE)
        self.assertIsNotNone(match, "CHANGELOG 缺 2.2.33 段")
        section = text[match.start():]
        nxt = re.search(r"^## \d", section[1:], re.MULTILINE)
        if nxt:
            section = section[:nxt.start() + 1]
        self.assertIn("不兼容", section, "2.2.33 段未标注不兼容变更")
        self.assertIn("--message-file", section)
        self.assertIn("--permission-mode", section)


if __name__ == "__main__":
    unittest.main()
