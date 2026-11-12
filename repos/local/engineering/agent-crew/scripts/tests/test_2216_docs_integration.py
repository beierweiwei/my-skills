#!/usr/bin/env python3
"""2.2.16 Phase 8 文档与模板接线防回退断言（T40/T41/T42/T44/T45/T43）。

覆盖 7 项接线（每个新工具至少一处在角色模板或规则/剧本中出现）：
1. `crew-preflight.py`：helm/firstmate 的每轮开工与交接接手动作；
2. `crew-msg.py` 已读游标：R1 与 helm/firstmate 的 inbox 读取动作改用
   `ls --unread` / `ls --mark-read`（inbox 文件仍是真源，游标不改不删回执）；
3. `crew-evidence.py`：runner/firstmate 模板与工单模板的证据采集口径；
4. `plan-status --brief` / `--lint`：plan-discipline 与 helm/firstmate 的看板/汇报动作；
5. `release-check.py`：release 剧本 Phase 5/6/8 的聚合核验门；
6. `pane-audit --apply`：helm/firstmate 定时自检默认动作；W5 空 pane 自动关与
   总舵 tab 单 pane；
7. `maintain-verification.md`：证据采集优先一次落盘，六段式结构不变。

反向断言：被替代的逐条手工检查句不得残留（防「禁止/改为」并存的双口径）。
组合渲染：开发副本布局下 dispatch compose helm/firstmate（qoder/opencode）rc=0。
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

Q_HELM = "templates/agents/qoder/helm.md"
Q_FIRSTMATE = "templates/agents/qoder/firstmate.md"
Q_RUNNER = "templates/agents/qoder/runner.md"
O_HELM = "templates/agents/opencode/helm.md"
O_FIRSTMATE = "templates/agents/opencode/firstmate.md"
O_RUNNER = "templates/agents/opencode/runner.md"
HELM = (Q_HELM, O_HELM)
FIRSTMATE = (Q_FIRSTMATE, O_FIRSTMATE)
RUNNER = (Q_RUNNER, O_RUNNER)
RULES = "references/crew-rules.md"
DISCIPLINE = "references/plan-discipline.md"
RELEASE = "templates/playbooks/release.md"
MAINTAIN = "templates/playbooks/maintain-verification.md"
TICKET = "templates/_templates/ticket.md"

PREFLIGHT_CMD = "python3 scripts/crew-preflight.py"
EVIDENCE_CMD = "python3 scripts/crew-evidence.py --out <file> -- <命令>"
EVIDENCE_FIELDS = "命令/rc/stdout/stderr/时间/git status"
EVIDENCE_FALLBACK = "缺该工具时手写落盘仍有效"
PANE_AUDIT_CMD = "python3 scripts/pane-audit.py --apply"


def read(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def lines_with(text: str, needle: str) -> list[str]:
    return [line for line in text.splitlines() if needle in line]


def release_phase(text: str, number: int) -> str:
    pattern = rf"^### Phase {number}: .*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise AssertionError(f"release.md 缺少 Phase {number}")
    rest = text[match.end():]
    following = re.search(r"^### Phase \d+: ", rest, flags=re.MULTILINE)
    return rest[: following.start()] if following else rest


class PreflightIntegrationTest(unittest.TestCase):
    """T41：一条预检替代逐条手工检查（每轮开工 + 交接接手）。"""

    def test_helm_startup_runs_preflight(self):
        for rel in HELM:
            text = read(rel)
            startup = [
                line for line in text.splitlines() if line.startswith("7. ") or line.startswith("10. ")
            ]
            self.assertTrue(
                any(PREFLIGHT_CMD in line for line in startup),
                f"{rel} 每轮开工动作未接 {PREFLIGHT_CMD}",
            )

    def test_firstmate_startup_runs_preflight(self):
        for rel in FIRSTMATE:
            text = read(rel)
            self.assertIn(PREFLIGHT_CMD, text, rel)
            startup = [
                line for line in text.splitlines() if line.startswith("8. ")
            ]
            self.assertTrue(startup, f"{rel} 缺少每轮开工动作")
            self.assertIn(PREFLIGHT_CMD, startup[0], f"{rel} 每轮开工未接预检")

    def test_handover_takeover_runs_preflight(self):
        for rel in HELM:
            handover = lines_with(read(rel), "交接读取范围（硬）")
            self.assertEqual(1, len(handover), rel)
            self.assertIn(PREFLIGHT_CMD, handover[0], f"{rel} 交接接手未接预检")
        for rel in FIRSTMATE:
            handover = lines_with(read(rel), "必核 guard 存在")
            self.assertTrue(handover, rel)
            self.assertTrue(
                any(PREFLIGHT_CMD in line for line in handover),
                f"{rel} 换舵/重建接手未接预检",
            )

    def test_old_piecemeal_checks_removed(self):
        for rel in HELM:
            self.assertNotIn(
                "每轮开工先检查 `<crew-dir>/guard.wake`",
                read(rel),
                f"{rel} 仍保留逐条手工 wake 检查（双口径）",
            )
        for rel in FIRSTMATE:
            self.assertNotIn(
                "每轮开工先查 `<crew-dir>/inbox/`（R1 文件真源）与 `<crew-dir>/guard.wake`",
                read(rel),
                f"{rel} 仍保留逐条手工 inbox/wake 检查（双口径）",
            )


class ReceiptCursorIntegrationTest(unittest.TestCase):
    """T42：R1 与角色模板改用已读游标，inbox 文件仍是唯一真源。"""

    def test_r1_documents_unread_cursor(self):
        line = next(line for line in read(RULES).splitlines() if "**R1 " in line)
        for phrase in (
            "python3 scripts/crew-msg.py ls --unread",
            "--mark-read <receipt_id>...",
            "inbox-read-cursor.json",
            "不改回执状态、不删回执文件",
            "唯一真源",
        ):
            self.assertIn(phrase, line, f"R1 缺少游标口径：{phrase}")

    def test_helm_firstmate_inbox_use_cursor(self):
        for rel in (*HELM, *FIRSTMATE):
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("ls --unread", text, f"{rel} 未用未读游标读取 inbox")
                self.assertIn("--mark-read", text, f"{rel} 未用游标标记已读")

    def test_r1_marks_read_is_not_disposed(self):
        """S-3：R1 明示游标只记读取进度，处置看回执 status + plan。"""
        line = next(line for line in read(RULES).splitlines() if "**R1 " in line)
        self.assertIn("已读≠已处置", line)
        self.assertIn("回执 status + plan", line)

    def test_helm_firstmate_document_cursor_initialization(self):
        """S-9：历史积压时一次性初始化游标的动作写进四个角色模板。"""
        for rel in (*HELM, *FIRSTMATE):
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("python3 scripts/crew-msg.py ls --mark-read --all", text)
                self.assertIn("初始化游标", text)
                self.assertIn("一次性", text)


class EvidenceToolIntegrationTest(unittest.TestCase):
    """T40：证据用 crew-evidence.py 一次落盘；缺工具退回手写口径。"""

    def test_runner_templates_use_evidence_tool(self):
        for rel in RUNNER:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn(EVIDENCE_CMD, text)
                self.assertIn(EVIDENCE_FIELDS, text)
                self.assertIn(EVIDENCE_FALLBACK, text)

    def test_firstmate_templates_use_evidence_tool(self):
        for rel in FIRSTMATE:
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn(EVIDENCE_CMD, text)
                self.assertIn(EVIDENCE_FIELDS, text)
                self.assertIn(EVIDENCE_FALLBACK, text)

    def test_ticket_template_requires_evidence_tool(self):
        text = read(TICKET)
        self.assertIn(EVIDENCE_CMD, text)
        self.assertIn(EVIDENCE_FIELDS, text)
        self.assertIn(EVIDENCE_FALLBACK, text)


class PlanStatusBriefLintIntegrationTest(unittest.TestCase):
    """T44：--brief 一屏摘要 + --lint 卫生提醒（默认不阻断，--strict 才失败）。"""

    def test_plan_discipline_documents_brief_and_lint(self):
        text = read(DISCIPLINE)
        for phrase in ("--brief", "--lint", "--strict", "rc=0", "不阻断"):
            self.assertIn(phrase, text, f"plan-discipline 缺少：{phrase}")

    def test_helm_firstmate_use_brief_and_lint(self):
        for rel in (*HELM, *FIRSTMATE):
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn("--brief", text, f"{rel} 汇报/看板未用一屏摘要")
                self.assertIn("--lint", text, f"{rel} 未提示看板卫生 lint")


class ReleaseCheckIntegrationTest(unittest.TestCase):
    """T43：release 剧本 Phase 5/6/8 加聚合核验门（不替换逐步命令）。"""

    def test_phase5_precheck_and_phase6_postcheck(self):
        text = read(RELEASE)
        for number in (5, 6, 8):
            chunk = release_phase(text, number)
            with self.subTest(phase=number):
                self.assertIn(
                    "release-check.py --dry-run", chunk,
                    f"release Phase {number} 缺少聚合核验",
                )
        self.assertIn("release-check-pre.txt", release_phase(text, 5))
        self.assertIn("全 pass", release_phase(text, 6))
        self.assertIn("release-check-post.txt", release_phase(text, 6))
        self.assertIn("全 pass", release_phase(text, 8))

    def test_stepwise_commands_kept(self):
        phase5 = release_phase(read(RELEASE), 5)
        for phrase in ("upgrade.py --apply --confirm", "init.py --check", "doctor.py"):
            self.assertIn(phrase, phase5, f"Phase 5 逐步命令被替换：{phrase}")


class PaneAuditIntegrationTest(unittest.TestCase):
    """T45：自检默认 --apply；W5 空 pane 自动关 + 总舵 tab 单 pane。"""

    def test_helm_firstmate_selftest_uses_apply(self):
        for rel in (*HELM, *FIRSTMATE):
            text = read(rel)
            with self.subTest(rel=rel):
                self.assertIn(PANE_AUDIT_CMD, text)
                self.assertIn("working 永不关", text)

    def test_w5_empty_pane_autoclose_and_helm_tab_single_pane(self):
        line = next(line for line in read(RULES).splitlines() if "**W5 " in line)
        for phrase in (
            "空 pane",
            "自动关",
            "working",
            "白名单",
            "总舵 tab 只允许 1 个 pane",
        ):
            self.assertIn(phrase, line, f"W5 缺少：{phrase}")


class MaintainEvidenceIntegrationTest(unittest.TestCase):
    """maintain-verification：证据优先一次落盘，六段式结构不变。"""

    def test_evidence_tool_preferred(self):
        text = read(MAINTAIN)
        self.assertIn(EVIDENCE_CMD, text)
        self.assertIn("缺该工具时", text)

    def test_six_phases_and_outcomes_unchanged(self):
        text = read(MAINTAIN)
        self.assertEqual(6, len(re.findall(r"^### Phase \d+: ", text, re.MULTILINE)))
        for phrase in ("**clean**", "**changed**", "**blocked**", ".scratch/", "不提交"):
            self.assertIn(phrase, text)


def project_root() -> Path | None:
    override = os.environ.get("CREW_PROJECT_ROOT")
    if override:
        return Path(override)
    candidate = REPO_ROOT.parents[4]
    if (candidate / "AGENTS.md").is_file() and (candidate / ".planning").is_dir():
        return candidate
    return None


PROJECT_ROOT = project_root()

_dm_spec = importlib.util.spec_from_file_location(
    "dispatch_2216_docs", REPO_ROOT / "scripts" / "dispatch.py")
dm = importlib.util.module_from_spec(_dm_spec)
sys.modules["dispatch_2216_docs"] = dm
_dm_spec.loader.exec_module(dm)


@unittest.skipUnless(PROJECT_ROOT is not None, "非开发副本布局，compose 断言跳过")
class ComposeRenderTest(unittest.TestCase):
    """模板渲染不断裂：helm/firstmate × qoder/opencode 组合 rc=0。"""

    def test_compose_all(self):
        for role in ("helm", "firstmate"):
            for runtime in ("qoder", "opencode"):
                with self.subTest(role=role, runtime=runtime):
                    outcome = dm.build_compose(
                        PROJECT_ROOT, role, runtime, skill_root=REPO_ROOT)
                    self.assertTrue(
                        outcome.ok, f"compose {role}/{runtime} 失败：{outcome.report.get('errors')}")
                    self.assertTrue(outcome.composed_body)
                    self.assertIn(PREFLIGHT_CMD, outcome.composed_body)


if __name__ == "__main__":
    unittest.main()
