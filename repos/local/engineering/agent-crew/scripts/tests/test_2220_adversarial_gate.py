#!/usr/bin/env python3
"""2.2.20-A：关键决策对抗审查硬门防回退断言（先红后绿）。

覆盖工单 A 验收断言：
① 四份角色模板（qoder/opencode × helm/firstmate）动作清单含硬门检查点，且一句话
   覆盖触发面（主数据/架构口径/验收口径/数据安全/范围变更）、实施前、独立 Reviewer
   对抗立场、Captain 直令不豁免（取证可并行）、阻断清零、证据落点、收口门缺证据阻断。
② 技能层剧本 `templates/playbooks/adversarial-review.md` 存在且符合解析契约
   （`## Phases` + 每 Phase 恰一行 Status），含前置输入/挑战清单/输出契约/记录落点，
   不出现 backend/model 赋值或具体模型 ID；README 登记；playbooks lint 全绿。
③ 规则正文：crew-rules D7 硬门 + F1 收口证据门；crew-acceptance A3.3 验收门证据。
④ AGENTS 编排节含硬门接线（技能根相对名指针，不用绝对 token）。

grep 断言只落在生产目录 `templates/`、`references/`，不扫 `scripts/tests/` 反向断言。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ROLE_FILES = (
    "templates/agents/qoder/helm.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/helm.md",
    "templates/agents/opencode/firstmate.md",
)
PLAYBOOK = "templates/playbooks/adversarial-review.md"
PLAYBOOK_README = "templates/playbooks/README.md"
AGENTS_BLOCK = "templates/AGENTS-编排节.md"
RULES = "references/crew-rules.md"
ACCEPTANCE = "references/crew-acceptance.md"

GATE_PHRASES = (
    "关键决策",
    "主数据",
    "架构口径",
    "验收口径",
    "数据安全",
    "范围变更",
    "实施前",
    "独立 Reviewer",
    "对抗",
    "Captain 直令不豁免",
    "取证可并行",
    "阻断清零",
    "plan Decisions",
    "需求目录",
    "收口/评审门缺证据即阻断",
)
CHALLENGE_PHRASES = (
    "假设",
    "边界与异常",
    "失败模式",
    "与真源冲突",
    "过度与欠设计",
    "回滚与监控",
)
PLAYBOOK_PATH = REPO_ROOT / PLAYBOOK


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def section(text: str, start: str, end: str | None = None) -> str:
    begin = text.find(start)
    if begin < 0:
        raise AssertionError(f"缺少章节：{start}")
    rest = text[begin + len(start):]
    if end is None:
        return rest
    tail = rest.find(end)
    if tail < 0:
        raise AssertionError(f"章节「{start}」缺少终点：{end}")
    return rest[:tail]


class RoleTemplateGateTest(unittest.TestCase):
    """① 四份角色模板：动作清单中的硬门检查点。"""

    def test_gate_line_covers_all_required_elements(self):
        for rel in ROLE_FILES:
            text = read(rel)
            actions = section(text, "## 动作清单", "\n## ")
            lines = [line for line in actions.splitlines() if "Captain 直令不豁免" in line]
            with self.subTest(rel=rel):
                self.assertTrue(lines, f"{rel} 动作清单缺少关键决策硬门检查点")
                checkpoint = "\n".join(lines)
                for phrase in GATE_PHRASES:
                    self.assertIn(phrase, checkpoint, f"{rel} 硬门检查点缺少：{phrase}")

    def test_gate_references_hard_rule_id(self):
        for rel in ROLE_FILES:
            with self.subTest(rel=rel):
                self.assertIn("D7", read(rel), f"{rel} 未引用硬门规则 D7")

    def test_closure_area_rechecks_adversarial_evidence(self):
        for rel in ROLE_FILES:
            lines = [line for line in read(rel).splitlines() if "D7" in line and "PASS" in line]
            with self.subTest(rel=rel):
                self.assertTrue(lines, f"{rel} 收口动作区未写 D7 证据门（PASS/阻断清零）")

    def test_self_check_runs_crew_reaper(self):
        for rel in ROLE_FILES:
            lines = [
                line for line in read(rel).splitlines()
                if "pane-audit.py --apply" in line and "crew-reaper.py --apply --json" in line
            ]
            with self.subTest(rel=rel):
                self.assertTrue(lines, f"{rel} 定时自检未接入 crew-reaper 对账回收")


class G7IndexSyncTest(unittest.TestCase):
    """A3/S-4：C2 新增 G7 后，编号表与四份角色模板引用同步为 G1–G7。"""

    def test_rules_index_lists_g1_g7_and_g7_entry(self):
        rules = read(RULES)
        self.assertIn("| G | 值守 | G1–G7 |", rules, "crew-rules 编号表未同步 G1–G7")
        line = next(
            (line for line in rules.splitlines() if line.startswith("- **G7 ")),
            None,
        )
        self.assertIsNotNone(line, "crew-rules 缺少 G7 条目")
        self.assertIn("guard 自动交接口径", line)

    def test_role_templates_reference_g1_g7_only(self):
        for rel in ROLE_FILES:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("G1–G7", text, f"{rel} 引用未同步 G1–G7")
                self.assertNotIn("G1–G6", text, f"{rel} 残留旧范围 G1–G6")


class PlaybookGateTest(unittest.TestCase):
    """② 剧本：存在、契约、挑战清单、输出契约与记录落点、README 登记。"""

    @classmethod
    def setUpClass(cls):
        if not PLAYBOOK_PATH.is_file():
            raise AssertionError(f"缺少剧本：{PLAYBOOK}")
        cls.text = read(PLAYBOOK)

    def test_phases_contract(self):
        self.assertIn("## Phases", self.text)
        chunk = section(self.text, "## Phases", "\n## ")
        heads = re.findall(r"^### Phase \d+:", chunk, flags=re.MULTILINE)
        self.assertGreaterEqual(len(heads), 1, "剧本至少需要 1 个 Phase")
        blocks = re.split(r"^### Phase \d+:.*$", chunk, flags=re.MULTILINE)[1:]
        for index, block in enumerate(blocks, 1):
            status = re.findall(r"\*\*Status:\*\*.*", block)
            self.assertEqual(1, len(status), f"Phase {index} 应恰有一行 Status")
            self.assertRegex(status[0], r"\*\*Status:\*\*\s+pending")

    def test_trigger_categories(self):
        for phrase in ("主数据", "架构口径", "验收口径", "数据安全", "范围变更"):
            self.assertIn(phrase, self.text, f"剧本缺少触发类别：{phrase}")

    def test_pre_inputs(self):
        chunk = section(self.text, "## 前置输入", "\n## ")
        for phrase in ("决策说明", "假设", "证据", "影响面"):
            self.assertIn(phrase, chunk, f"前置输入缺少：{phrase}")

    def test_challenge_checklist(self):
        for phrase in CHALLENGE_PHRASES:
            self.assertIn(phrase, self.text, f"挑战清单缺少：{phrase}")

    def test_output_contract_and_record_locations(self):
        output = section(self.text, "## 输出契约", "\n## ")
        for phrase in ("阻断", "建议", "阻断清零", "放行", "报告", "回执", "PASS", "BLOCK"):
            self.assertIn(phrase, output, f"输出契约缺少：{phrase}")
        record = section(self.text, "## 记录落点", "\n## ")
        for phrase in ("plan Decisions", "需求目录", "evidence"):
            self.assertIn(phrase, record, f"记录落点缺少：{phrase}")

    def test_readonly_stance(self):
        for phrase in ("只读", "不改实现"):
            self.assertIn(phrase, self.text, f"剧本缺少只读立场：{phrase}")

    def test_no_role_model_hardcoding(self):
        self.assertNotRegex(self.text, r"(?m)^\s*-?\s*(?:backend|model)\s*[:：]")
        self.assertNotRegex(self.text, r"(?i)\b(?:glm|gpt|claude|deepseek|qwen)[-_ ]?\d")

    def test_readme_registers_playbook(self):
        readme = read(PLAYBOOK_README)
        self.assertIn("adversarial-review.md", readme)
        for phrase in ("主数据", "架构口径", "验收口径", "数据安全", "范围变更", "Captain 直令不豁免"):
            self.assertIn(phrase, readme, f"README 登记缺少：{phrase}")

    def test_playbooks_lint_green(self):
        proc = subprocess.run(
            [sys.executable, "scripts/plan-status.py", "--playbooks", "templates/playbooks"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        match = re.search(r"剧本 lint ok：(\d+) 个剧本", proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        self.assertGreaterEqual(int(match.group(1)), 10, proc.stdout)


class RulesGateTest(unittest.TestCase):
    """③ 规则正文：D7 硬门 / F1 收口证据门 / A3.3 验收门证据。"""

    def test_d7_defined_and_indexed(self):
        rules = read(RULES)
        self.assertIn("D1–D9", rules)  # 2.2.28 FB-055 起 D 节扩至 D9
        line = next(
            (line for line in rules.splitlines() if line.startswith("- **D7 ")),
            None,
        )
        self.assertIsNotNone(line, "crew-rules 缺少 D7 硬门")
        for phrase in (
            "关键决策",
            "主数据",
            "架构口径",
            "验收口径",
            "数据安全",
            "范围变更",
            "实施前",
            "独立 Reviewer",
            "Captain 直令不豁免",
            "取证可并行",
            "阻断项清零",
            "plan Decisions",
            "需求目录",
            "PASS/阻断清零",
        ):
            self.assertIn(phrase, line, f"D7 缺少：{phrase}")

    def test_f1_closure_requires_adversarial_evidence(self):
        rules = read(RULES)
        line = next(
            (line for line in rules.splitlines() if line.startswith("- **F1 ")),
            None,
        )
        self.assertIsNotNone(line, "crew-rules 缺少 F1")
        for phrase in ("D7", "对抗审查", "报告路径", "PASS/阻断清零", "不收口"):
            self.assertIn(phrase, line, f"F1 收口门缺少：{phrase}")

    def test_acceptance_a33_requires_evidence(self):
        acceptance = read(ACCEPTANCE)
        line = next(
            (line for line in acceptance.splitlines() if line.startswith("- **A3.3 ")),
            None,
        )
        self.assertIsNotNone(line, "crew-acceptance 缺少 A3.3")
        for phrase in ("关键决策", "对抗审查", "PASS", "阻断清零", "D7"):
            self.assertIn(phrase, line, f"A3.3 缺少：{phrase}")


class AgentsBlockWiringTest(unittest.TestCase):
    """④ AGENTS 编排节：硬门接线或明确指针（技能根相对名）。"""

    def test_agents_block_has_hard_gate_pointer(self):
        text = read(AGENTS_BLOCK)
        self.assertIn("关键决策对抗审查", text)
        self.assertIn("Captain 直令不豁免", text)
        self.assertIn("references/crew-rules.md", text)
        self.assertIn("adversarial-review.md", text)
        self.assertIn("D7", text)

    def test_agents_block_keeps_relative_pointer_form(self):
        text = read(AGENTS_BLOCK)
        self.assertNotIn("{{crew_rules}}", text)
        self.assertNotIn("{{skill_root}}", text)


if __name__ == "__main__":
    unittest.main()
