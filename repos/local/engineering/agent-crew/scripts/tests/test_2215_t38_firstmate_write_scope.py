#!/usr/bin/env python3
"""2.2.15 T38 firstmate 写权口径防回退断言（FB-035）。

口径：portfolio 三文件（task_plan/progress/feedback）由总舵写，本舵不写
portfolio 台账；firstmate 只写本流 `.planning/<task>/progress.md`。

覆盖：
- 技能源 qoder/opencode `firstmate.md`：可写文件条保留「portfolio 由总舵写」，
  「会话与交接」「自动驾驶里程」指向本流 progress；模板不再含
  「更新/写入 `.planning/portfolio/progress.md`」这类 firstmate 写 portfolio 指令。
- 项目侧（技能源位于本仓开发副本布局时）：`build_compose` 用技能源模板 +
  项目角色覆盖组合，组合正文同样不含上述指令。

独立分发/安装布局没有项目侧角色覆盖，项目侧断言整体 skip。
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

FIRSTMATE_TEMPLATES = (
    "templates/agents/qoder/firstmate.md",
    "templates/agents/opencode/firstmate.md",
)

PORTFOLIO_OWNER_CLAUSE = (
    "portfolio 三文件（task_plan/progress/feedback）由总舵写，本舵不写 portfolio 台账"
)
FLOW_HANDOVER = "按本流 `.planning/<task>/progress.md` 尾部交接"
FLOW_MILEAGE = "里程逐条记本流 `.planning/<task>/progress.md`「自动驾驶里程」节"

FORBIDDEN_WRITES = (
    "更新 `.planning/portfolio/progress.md`",
    "写入 `.planning/portfolio/progress.md` 的「自动驾驶里程」节",
    "写入 portfolio progress",
)

_dm_spec = importlib.util.spec_from_file_location(
    "dispatch_t38", REPO_ROOT / "scripts" / "dispatch.py")
dm = importlib.util.module_from_spec(_dm_spec)
sys.modules["dispatch_t38"] = dm
_dm_spec.loader.exec_module(dm)


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def project_root() -> Path | None:
    """定位本仓项目根；独立分发/安装布局返回 None（项目侧断言整体 skip）。"""
    override = os.environ.get("CREW_PROJECT_ROOT")
    if override:
        return Path(override)
    candidate = REPO_ROOT.parents[4]
    if (candidate / "AGENTS.md").is_file() and (candidate / ".planning").is_dir():
        return candidate
    return None


PROJECT_ROOT = project_root()


def assert_write_scope(case: unittest.TestCase, body: str, label: str) -> None:
    case.assertIn(PORTFOLIO_OWNER_CLAUSE, body, f"{label} 缺少 portfolio 写权口径")
    case.assertIn(FLOW_HANDOVER, body, f"{label} 交接未指向本流 progress")
    case.assertIn(FLOW_MILEAGE, body, f"{label} 里程未指向本流 progress")
    for phrase in FORBIDDEN_WRITES:
        case.assertNotIn(phrase, body, f"{label} 含旧写权指令：{phrase}")
    for line in body.splitlines():
        if ".planning/portfolio/progress.md" in line:
            case.assertTrue(
                "总舵" in line or "不写" in line,
                f"{label} 在非「总舵写 / 本舵不写」语境提到 portfolio progress：{line}",
            )
        if "portfolio progress" in line:
            case.assertTrue(
                "总舵" in line or "不写" in line,
                f"{label} 在非「总舵写 / 本舵不写」语境提到 portfolio progress：{line}",
            )


class FirstmateTemplateWriteScopeTest(unittest.TestCase):
    """技能源模板：两版 firstmate 同一写权口径。"""

    def test_keeps_portfolio_owner_clause(self):
        for rel in FIRSTMATE_TEMPLATES:
            with self.subTest(rel=rel):
                self.assertIn(PORTFOLIO_OWNER_CLAUSE, read(rel), rel)

    def test_handover_and_mileage_point_to_flow_progress(self):
        for rel in FIRSTMATE_TEMPLATES:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn(FLOW_HANDOVER, text, rel)
                self.assertIn(FLOW_MILEAGE, text, rel)

    def test_no_portfolio_progress_write_instruction(self):
        for rel in FIRSTMATE_TEMPLATES:
            with self.subTest(rel=rel):
                assert_write_scope(self, read(rel), rel)


@unittest.skipUnless(PROJECT_ROOT is not None, "非开发副本布局，项目侧组合断言跳过")
class ComposedFirstmateWriteScopeTest(unittest.TestCase):
    """组合产物：技能源模板 + 项目角色覆盖 append-only 后仍只有一条写权口径。"""

    def compose(self, runtime: str) -> str:
        outcome = dm.build_compose(
            PROJECT_ROOT, "firstmate", runtime, skill_root=REPO_ROOT)
        self.assertTrue(outcome.ok, f"compose 失败：{outcome.report.get('errors')}")
        return outcome.composed_body

    def test_qoder_composed(self):
        assert_write_scope(self, self.compose("qoder"), "qoder composed")

    def test_opencode_composed(self):
        assert_write_scope(self, self.compose("opencode"), "opencode composed")


if __name__ == "__main__":
    unittest.main()
