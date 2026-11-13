#!/usr/bin/env python3
"""2.2.28 FB-055：Phase 0（原设计意图审查）升技能层防回退。

背景（FB-055）：救火模式跳对抗审查 + 行为参数拍脑袋进工单（T63「5 轮」无
依据）。已修：本仓项目层 `.planning/playbooks/dispatch-preflight.md` Phase 0；
retro 结论（2026-11-13）：Phase 0 升入技能层，CASE 同步受益。

钉死契约：
1. 技能层 `templates/playbooks/dispatch-preflight.md` 存在，Phase 0 含
   「原设计意图」与「行为参数须给依据」两要素；
2. `references/crew-rules.md` D 节含 D9 条目（行为参数给依据 + 改既有机制
   先答原设计意图），索引表同步 D1–D9；
3. 剧本 README「现有剧本」表登记 dispatch-preflight；
4. `plan-status.py --playbooks templates/playbooks` 对新剧本 lint 全绿
   （结构契约由该命令保证，此处只断言登记与要素）。
"""

from __future__ import annotations

import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PLAYBOOK = "templates/playbooks/dispatch-preflight.md"
RULES = "references/crew-rules.md"
README = "templates/playbooks/README.md"


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


class Fb055Phase0SkillLayerTest(unittest.TestCase):
    def test_skill_layer_playbook_has_phase0(self):
        text = read(PLAYBOOK)
        self.assertIn("### Phase 0", text)
        self.assertIn("原设计", text, "Phase 0 须含原设计意图审查")
        self.assertIn("行为参数", text, "Phase 0 须含行为参数给依据")
        self.assertIn("拍脑袋", text, "Phase 0 须显式禁止无依据数值")

    def test_crew_rules_d9_exists_and_indexed(self):
        rules = read(RULES)
        self.assertIn("**D9 ", rules, "crew-rules.md D 节须有 D9 条目")
        d9 = rules.split("**D9 ", 1)[1].split("\n- ", 1)[0]
        self.assertIn("行为参数", d9)
        self.assertIn("原设计", d9)
        self.assertIn("| D | 派单 | D1–D9 |", rules, "规则索引表须同步 D1–D9")

    def test_playbook_readme_registers_dispatch_preflight(self):
        readme = read(README)
        self.assertIn("dispatch-preflight.md", readme)
        self.assertIn("派单前置", readme)


if __name__ == "__main__":
    unittest.main()
