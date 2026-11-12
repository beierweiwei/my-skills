#!/usr/bin/env python3
"""2.2.17 Phase 5 文档与模板整合防回退断言（FB-039 投递语义 + FB-040 在跑口径）。

位置约束：每条断言先定位指定文件的指定行（R1/G4/T37/guard/registry/通信/值守），
只在该行内断言关键词，不做全仓裸 grep。S6 的 opencode helm 反向断言由
test_2216_docs_integration.py::test_old_piecemeal_checks_removed 覆盖（Phase 3 已落）。

覆盖：
1. crew-rules R1 补投递语义（默认直推 / 聚焦回退 / 跨项目通知 / 显式 herdr 通知）；
2. crew-rules G4 在跑口径 = Herdr working + `<crew-dir>/runs/` 存活注册；
3. herdr-cheatsheet T37 段与 guard 段同步；
4. orchestration-guide registry 段与守卫段同步；
5. qoder/opencode 的 helm/firstmate/runner 通信行、helm/firstmate 值守行同步；
6. `crew-msg.py --help` / `crew-guard.py --help` 含新语义关键词（脚本侧由代码工单改）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

RULES = "references/crew-rules.md"
CHEATSHEET = "references/herdr-cheatsheet.md"
GUIDE = "references/orchestration-guide.md"
Q_HELM = "templates/agents/qoder/helm.md"
Q_FIRSTMATE = "templates/agents/qoder/firstmate.md"
Q_RUNNER = "templates/agents/qoder/runner.md"
O_HELM = "templates/agents/opencode/helm.md"
O_FIRSTMATE = "templates/agents/opencode/firstmate.md"
O_RUNNER = "templates/agents/opencode/runner.md"
HELM = (Q_HELM, O_HELM)
FIRSTMATE = (Q_FIRSTMATE, O_FIRSTMATE)
RUNNER = (Q_RUNNER, O_RUNNER)
ALL_ROLES = (*HELM, *FIRSTMATE, *RUNNER)

# 模板值守行定位标记（各文件现有措辞不同，逐文件钉死位置）。
GUARD_LINE_MARKERS = {
    Q_HELM: "值守：响应 guard 叫醒",
    O_HELM: "值守：后台",
    Q_FIRSTMATE: "值守：guard 为自动触发器",
    O_FIRSTMATE: "监听：派完立即返回",
}


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def line_with(text: str, needle: str, rel: str) -> str:
    """定位唯一含 needle 的行；0 行或多行都失败，防止断言漂到错误段落。"""
    hits = [line for line in text.splitlines() if needle in line]
    if len(hits) != 1:
        raise AssertionError(f"{rel} 应恰有 1 行含 {needle!r}，实际 {len(hits)} 行")
    return hits[0]


def help_text(script: str, *extra: str) -> str:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / script), *extra, "--help"],
        capture_output=True, text=True, env=env, timeout=60,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"{script} {' '.join(extra)} --help 失败 rc={proc.returncode}：{proc.stderr}")
    return proc.stdout + proc.stderr


def compact(text: str) -> str:
    """去掉所有空白，规避 argparse 按宽度折行把关键词拆开。"""
    return re.sub(r"\s+", "", text)


class RulesDeliveryAndRunsTest(unittest.TestCase):
    """crew-rules：R1 投递语义 + G4 在跑口径。"""

    def test_r1_documents_receipt_delivery_semantics(self):
        line = line_with(read(RULES), "**R1 ", RULES)
        for phrase in (
            "crew-msg.py write --to <同项目 herdr agent>",
            "默认直推收件方 pane",
            "delivery=herdr",
            "T37",
            "delivery=inbox_fallback",
            "跨项目",
            "delivery=notification",
            "--target-runtime herdr",
            "情形③",
            "delegate ok=false",
        ):
            self.assertIn(phrase, line, f"R1 缺少投递语义：{phrase}")

    def test_g4_running_scope_includes_claude_runs(self):
        line = line_with(read(RULES), "**G4 ", RULES)
        for phrase in (
            "Herdr `agent_status=working`",
            "<crew-dir>/runs/` 存活注册",
            "crew-claude.py run",
            "死 pid",
            "坏 JSON",
            "注册失败只告警",
            "blocked",
            "idle/done/unknown 不算在跑",
        ):
            self.assertIn(phrase, line, f"G4 缺少在跑口径：{phrase}")
        self.assertNotIn(
            "只有 working 算在跑", line,
            "G4 仍是旧口径（未合并 runs 存活注册）",
        )


class CheatsheetSyncTest(unittest.TestCase):
    """herdr-cheatsheet：T37 段补默认直推，guard 段补 runs 注册。"""

    def test_t37_section_documents_default_direct_push(self):
        line = line_with(read(CHEATSHEET), "聚焦守卫（T37）", CHEATSHEET)
        for phrase in (
            "默认直推",
            "delivery=herdr",
            "跨项目/不可达",
            "delivery=notification",
            "--target-runtime herdr",
        ):
            self.assertIn(phrase, line, f"T37 段缺少投递语义：{phrase}")

    def test_guard_section_documents_runs_registry(self):
        line = line_with(read(CHEATSHEET), "guard 值守（G1–G6）", CHEATSHEET)
        for phrase in (
            "runs/` 存活注册",
            "crew-claude.py run",
            "死 pid/坏 JSON 忽略",
            "claudeRuns",
        ):
            self.assertIn(phrase, line, f"cheatsheet guard 段缺少：{phrase}")

    def test_a1_unreachable_semantics_documents_three_cases(self):
        """建议-1：A-1 行须限定显式 qoder 例外，并覆盖 auto 的 notification / 后续失败分支。"""
        line = line_with(read(CHEATSHEET), "目标不可达 ≠ 聚焦回退（A-1）", CHEATSHEET)
        for phrase in (
            "显式 `--target-runtime qoder`",
            "ok=false",
            "rc=3",
            "默认 auto",
            "delivery=notification",
            "ok=true",
            "已判定同项目",
            "inbox_fallback",
            "delegate ok=false",
        ):
            self.assertIn(phrase, line, f"A-1 行缺少情形说明：{phrase}")

    def test_cursor_section_documents_legacy_disposition(self):
        """建议-4：legacy 文件名回执的处置路径写进 cheatsheet 回执游标处。"""
        line = line_with(read(CHEATSHEET), "回执游标（R1）", CHEATSHEET)
        for phrase in (
            "legacy 文件名回执",
            "--mark-read --all",
            "rc=0",
            "rc=1",
            "`<receipt_id>.json`",
            "重命名",
            "删除",
        ):
            self.assertIn(phrase, line, f"回执游标处置缺少：{phrase}")


class GuideSyncTest(unittest.TestCase):
    """orchestration-guide：registry 只存例外 + Herdr 目标直推；守卫段引用 G4。"""

    def test_registry_row_documents_herdr_direct_push(self):
        line = line_with(read(GUIDE), "只存 Herdr 不知道的路由", GUIDE)
        self.assertIn("默认直推收件方 pane", line, "registry 段未说明 Herdr 目标默认直推")

    def test_guard_section_references_g4_runs_scope(self):
        line = line_with(read(GUIDE), "待办/在跑口径见 G1–G6", GUIDE)
        for phrase in ("G4", "runs/` 存活注册", "crew-claude.py run"):
            self.assertIn(phrase, line, f"守卫段缺少在跑口径：{phrase}")


class RoleTemplateSyncTest(unittest.TestCase):
    """6 角色模板：通信行补投递语义；helm/firstmate 值守行补 runs 注册。"""

    def test_communication_line_documents_delivery(self):
        for rel in ALL_ROLES:
            text = read(rel)
            with self.subTest(rel=rel):
                line = line_with(text, "通信：", rel)
                for phrase in (
                    "同项目 herdr 目标回执默认直推收件方 pane",
                    "T37",
                    "聚焦/不可判定落 inbox+通知",
                    "跨项目/不可达只通知",
                ):
                    self.assertIn(phrase, line, f"{rel} 通信行缺少：{phrase}")

    def test_guard_line_documents_claude_runs(self):
        for rel, marker in GUARD_LINE_MARKERS.items():
            text = read(rel)
            with self.subTest(rel=rel):
                line = line_with(text, marker, rel)
                for phrase in (
                    "guard 在跑口径",
                    "runs/` 存活注册",
                    "crew-claude.py run",
                    "死 pid/坏 JSON 忽略",
                ):
                    self.assertIn(phrase, line, f"{rel} 值守行缺少：{phrase}")

    def test_runner_templates_document_delivery_only(self):
        """runner 无值守职责：只要求通信行同步，值守行不硬塞。"""
        for rel in RUNNER:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertNotIn("guard 在跑口径", text, f"{rel} 不应写入值守口径")


class HelpOutputTest(unittest.TestCase):
    """脚本侧语义已由代码工单落地，本工单只加防回退断言。"""

    def test_crew_msg_help_documents_delivery(self):
        main = compact(help_text("crew-msg.py"))
        for phrase in (
            "按qoder直推",
            "只写文件+Herdr通知",
            "delivery=notification",
            "target-runtimeherdr",
        ):
            self.assertIn(phrase, main, f"crew-msg.py --help 缺少：{phrase}")
        write = compact(help_text("crew-msg.py", "write"))
        self.assertIn("同项目herdr目标直推", write, "crew-msg.py write --help 缺少直推语义")

    def test_crew_guard_help_documents_runs(self):
        out = compact(help_text("crew-guard.py"))
        for phrase in (
            "runs/`存活注册",
            "crew-claude.pyrun",
            "死pid/坏JSON/缺pid忽略",
            "claudeRuns",
        ):
            self.assertIn(phrase, out, f"crew-guard.py --help 缺少：{phrase}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
