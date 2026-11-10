#!/usr/bin/env python3
"""2.2.9 试点 C：大副收口 SOP 接入 interrogate 互检 + firstmate 基类动作行。

锁点（T9-A/B）：
- `firstmate-dispatch` §5 收口含「收口前互检」：触发/纯文档跳过留痕、经济档与升档、
  运行复现门槛、只报不修/同根因合并/无发现合法、证据落点与 plan Decisions、技能指针；
- qoder/opencode `firstmate.md` 各含「收口前按 interrogate 技能做互检（经济档/升档规则）」，
  且不超过 crew-lint 的 80 行预算；
- 不出现 engineering-disciplines 字样；版本号由发布切片（T9-D）单独断言。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DISPATCH = REPO_ROOT / "templates" / "skills" / "firstmate-dispatch" / "SKILL.md"
FIRSTMATE = (
    REPO_ROOT / "templates" / "agents" / "qoder" / "firstmate.md",
    REPO_ROOT / "templates" / "agents" / "opencode" / "firstmate.md",
)
INTERROGATE_LINE = "收口前按 interrogate 技能做互检（经济档/升档规则）"
FIRSTMATE_BUDGET = 80


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def close_section(text: str) -> str:
    start = text.index("## 5. 收口")
    end = text.index("## 6. ", start)
    return text[start:end]


class InterrogateSopTest(unittest.TestCase):
    def setUp(self):
        self.text = read(DISPATCH)
        self.section = close_section(self.text)

    def test_close_section_has_interrogate_intercheck(self):
        for token in (
            "收口前互检",
            "interrogate",
            "触发",
            "代码/配置/脚本",
            "纯文档",
            "跳过并留痕",
            "1 攻击者",
            "inherit-parent",
            "核心模块 / 对外 API / 数据安全红线",
            "升 2",
            "plan Decision",
            "运行复现",
            "失败测试",
            "repro",
            "错误输出",
            "纯静态推断",
            "「建议」",
            "只报不修",
            "同根因合并",
            "「无发现」是合法结果",
            ".planning/<流>/evidence/interrogate.md",
            "结论进该流 plan Decisions",
            "按 `interrogate` 技能执行",
        ):
            self.assertIn(token, self.section, f"§5 收口缺少关键词：{token}")

    def test_sop_does_not_reference_engineering_disciplines(self):
        self.assertNotIn("engineering-disciplines", self.text)


class FirstmateTemplateTest(unittest.TestCase):
    def test_each_template_has_interrogate_action_line(self):
        for path in FIRSTMATE:
            text = read(path)
            self.assertIn(INTERROGATE_LINE, text, f"{path.name} 缺少 interrogate 互检动作行")
            self.assertNotIn(
                "engineering-disciplines", text, f"{path.name} 不应引用通用纪律技能"
            )

    def test_firstmate_templates_within_line_budget(self):
        for path in FIRSTMATE:
            lines = len(read(path).splitlines())
            self.assertLessEqual(
                lines, FIRSTMATE_BUDGET, f"{path.name} {lines} 行超过预算 {FIRSTMATE_BUDGET}"
            )


if __name__ == "__main__":
    unittest.main()
