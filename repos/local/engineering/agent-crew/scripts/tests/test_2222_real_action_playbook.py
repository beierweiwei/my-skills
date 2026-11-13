#!/usr/bin/env python3
"""2.2.22 剧本 slice 防回退断言：real-action-verification。

覆盖工单验收 1–7：
- 剧本存在，`## Phases` 内 ≥4 个 `### Phase N: <中文阶段名>`，每 Phase 恰一行
  `- **Status:** pending`，Phase 条目只用 `- [ ]`/`- [x]`/`- owner:`/`- plan:`。
- 六个检查点齐全：前置检查、执行、取证时点、顶层验证、停止/熔断、证据清单。
- 只引 VR 编号与 `references/verification-redlines.md` 指针，不复制 VR 条款原句。
- 不含项目特有词（3000/QQ/req2./SempClaw/FDE）。
- README「现有剧本」表登记本剧本与适用类型。
- `plan-status.py --playbooks templates/playbooks` 全绿。
- 技能层剧本正文不出现模型/后端赋值（S-11）。
"""

from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOK = REPO_ROOT / "templates" / "playbooks" / "real-action-verification.md"
README = REPO_ROOT / "templates" / "playbooks" / "README.md"

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$", re.MULTILINE)
STATUS_LINE = "- **Status:** pending"
ALLOWED_ITEM_RE = re.compile(
    r"^-\s*(?:\[[ xX]\]\s*\S|owner:\s*\S|plan:\s*`[^`]+`)"
)

CHECKPOINTS = (
    "前置检查",
    "真动作只走产品路径",
    "取证时点",
    "顶层验证",
    "停止与熔断",
    "证据清单",
)

# VR 正文原句（`references/verification-redlines.md`）：剧本只准引编号，出现即视为复制。
VR_VERBATIM = (
    "外部可观察证据 > 日志/JSON > 程序自述",
    "日志、JSON、自述只作旁证",
    "脚本直调发送或业务接口",
    "旁路脚本模拟业务逻辑",
    "任何绕行当场记 Deviation，并需 Captain 书面批准",
    "立即停止全部真实动作与发送",
    "中间层、接口 200、单测、harness、单点探针只作旁证",
)

PROJECT_TERMS = ("3000", "QQ", "req2.", "SempClaw", "FDE")


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def phase_chunks(text: str) -> dict[str, str]:
    body = text.partition("## Phases")[2].partition("\n## ")[0]
    heads = list(PHASE_RE.finditer(body))
    chunks: dict[str, str] = {}
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(body)
        chunks[match.group(1)] = body[match.start():end]
    return chunks


class RealActionPlaybookStructureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PLAYBOOK.is_file():
            raise AssertionError(f"缺少剧本：{PLAYBOOK}")
        cls.text = read(PLAYBOOK)
        cls.phases = phase_chunks(cls.text)

    def test_phases_section_and_minimum_count(self):
        self.assertIn("## Phases", self.text)
        self.assertGreaterEqual(len(self.phases), 4, sorted(self.phases))
        self.assertGreaterEqual(len(self.phases), 6, sorted(self.phases))
        for title in self.phases:
            self.assertRegex(title, r"[\u4e00-\u9fff]", f"Phase 标题应为中文阶段名：{title}")

    def test_each_phase_has_exactly_one_status_and_legal_items(self):
        for title, chunk in self.phases.items():
            with self.subTest(phase=title):
                self.assertEqual(1, chunk.count(STATUS_LINE))
                for line in chunk.splitlines():
                    stripped = line.strip()
                    if not stripped or stripped.startswith("### Phase"):
                        continue
                    if stripped.startswith("- **Status:**"):
                        continue
                    self.assertRegex(
                        stripped, ALLOWED_ITEM_RE, f"{title} 出现非法条目：{stripped}"
                    )
                self.assertTrue(
                    any(line.startswith("- [ ] ") for line in chunk.splitlines()),
                    f"{title} 缺少 `- [ ]` 步骤",
                )

    def test_six_checkpoints_present(self):
        for keyword in CHECKPOINTS:
            self.assertIn(keyword, self.text, f"缺少检查点：{keyword}")

    def test_checkpoint_semantics(self):
        # 工单目标 1–6 的动作语义：前置不满足不开工、双时点、第一证据、顶层验证、熔断留证、报告+回执。
        for keyword in (
            "不满足不开工",
            "动作发生时",
            "结果页",
            "外部可观察",
            "旁证",
            "全量用户路径",
            "模拟 / 单点 / 全量用户路径",
            "Deviation",
            "Captain 书面批准",
            "留证复盘",
            "报告与回执成对",
        ):
            self.assertIn(keyword, self.text, f"缺少语义关键词：{keyword}")

    def test_only_vr_pointer_and_ids_no_verbatim_copy(self):
        self.assertIn("references/verification-redlines.md", self.text)
        for number in range(1, 7):
            self.assertIn(f"VR{number}", self.text, f"缺少 VR{number} 指针")
        for sentence in VR_VERBATIM:
            self.assertNotIn(sentence, self.text, f"复制了 VR 条款原句：{sentence}")

    def test_no_project_specific_terms(self):
        for term in PROJECT_TERMS:
            self.assertNotIn(term, self.text, f"剧本含项目特有词：{term}")

    def test_no_hardcoded_model_or_backend(self):
        for marker in ("--model", "--backend", "backend:", "model:"):
            self.assertNotIn(marker, self.text, f"剧本出现模型/后端赋值：{marker}")


class RealActionPlaybookIndexTest(unittest.TestCase):
    def test_readme_row_registered(self):
        text = read(README)
        self.assertIn("`real-action-verification.md`", text)
        row = re.search(
            r"^\| `real-action-verification\.md` \| (.+) \|$", text, flags=re.MULTILINE
        )
        self.assertIsNotNone(row, "README「现有剧本」表缺少 real-action-verification.md 行")
        for keyword in ("真实发送", "外部", "取证", "熔断"):
            self.assertIn(keyword, row.group(1), f"README 适用类型缺少：{keyword}")


class RealActionPlaybookLintTest(unittest.TestCase):
    def test_playbooks_lint_green(self):
        proc = subprocess.run(
            [sys.executable, "scripts/plan-status.py", "--playbooks", "templates/playbooks"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        match = re.search(r"剧本 lint ok：(\d+) 个剧本", proc.stdout)
        self.assertIsNotNone(match, proc.stdout)
        self.assertGreaterEqual(int(match.group(1)), 11, proc.stdout)


if __name__ == "__main__":
    unittest.main()
