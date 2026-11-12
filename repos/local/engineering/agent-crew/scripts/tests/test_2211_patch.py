#!/usr/bin/env python3
"""2.2.11 T21 docs：FB-009/010/013/014 规则、模板与发布剧本防回退断言。

覆盖：
- FB-009：两 helm 口头决策当轮落盘 + 回显 + 交接 digest「未落盘决策」栏；
  crew-rules 新增 G6；索引/guide/两 helm/两 firstmate 的 G 范围更新为 G1–G7
  （A3/A4 随 C2 的 G7 同步）。
- FB-010：两 firstmate 回合末先查 inbox，未处置回执/待续派不得结束回合。
- FB-013：plan-discipline 与两 firstmate 的 Errors 当轮记录、空表写「无」禁「待记录」；
  10 个 worker 模板回执 findings 带出 Error/Resolution。
- FB-014：guide §5.11 发布后重启与可交接检查；release 剧本结构合法且覆盖步骤；
  README 剧本表列出 release。
- AMEND1：FB-011 两 helm 核对 guard.wake 首行 `generated_at:` 与过期 fail-safe；
  FB-012 crew-rules 唯一 dispatcher 含 `notify` 及跨项目通知边界。
- AMEND2：B-1 `--once --json` 无锁取证与非空 JSON 判定；B-2 firstmate digest
  「未落盘决策」、helm 落盘「含生效口径与时间」；B-3/B-4 release 证据文件与
  upgrade 自动同步；B-5 全仓编号防回退扫描（含 ASCII 连字符变体）与每 Phase 至少一个步骤。
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CREW_RULES = REPO_ROOT / "references" / "crew-rules.md"
PLAN_DISCIPLINE = REPO_ROOT / "references" / "plan-discipline.md"
GUIDE = REPO_ROOT / "references" / "orchestration-guide.md"
PLAYBOOK_README = REPO_ROOT / "templates" / "playbooks" / "README.md"
RELEASE_PLAYBOOK = REPO_ROOT / "templates" / "playbooks" / "release.md"

HELM = {
    "qoder": REPO_ROOT / "templates" / "agents" / "qoder" / "helm.md",
    "opencode": REPO_ROOT / "templates" / "agents" / "opencode" / "helm.md",
}
FIRSTMATE = {
    "qoder": REPO_ROOT / "templates" / "agents" / "qoder" / "firstmate.md",
    "opencode": REPO_ROOT / "templates" / "agents" / "opencode" / "firstmate.md",
}
WORKERS = tuple(
    REPO_ROOT / "templates" / "agents" / runtime / f"{role}.md"
    for runtime in ("qoder", "opencode")
    for role in ("builder", "planner", "reviewer", "runner", "scout")
)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class Fb009HelmDecisionPersistenceTest(unittest.TestCase):
    """FB-009：Captain 口头决策当轮落盘、回显，交接 digest 带未落盘决策栏。"""

    def test_helm_persists_verbal_decision_same_turn(self):
        for runtime, path in HELM.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                for phrase in (
                    "口头决策当轮落盘",
                    "Captain 口头/会话决策",
                    "当轮",
                    "progress.md",
                    "已落盘：<文件>/<条目>",
                    "生效口径与时间",
                    "未落盘不得当作已决执行",
                ):
                    self.assertIn(phrase, text)

    def test_helm_handover_digest_lists_unpersisted_decisions(self):
        for runtime, path in HELM.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                self.assertIn("digest ≤30 行", text)
                self.assertIn("未落盘决策", text)
                self.assertIn("无则写「无」", text)

    def test_firstmate_handover_digest_lists_unpersisted_decisions(self):
        for runtime, path in FIRSTMATE.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                self.assertIn("交接 digest", text)
                self.assertIn("未落盘决策", text)
                self.assertIn("无则写「无」", text)

    def test_crew_rules_adds_g6_and_indexes_g7(self):
        rules = read(CREW_RULES)
        self.assertIn("| G | 值守 | G1–G7 |", rules)
        g6_line = next(
            (line for line in rules.splitlines() if line.startswith("- **G6 ")),
            "",
        )
        self.assertIn("G6 口头决策落盘", g6_line)
        for phrase in (
            "当轮",
            "已落盘：",
            "未落盘不得当作已决执行",
            "未落盘决策",
        ):
            self.assertIn(phrase, g6_line)

    def test_no_stale_g1_g5_range(self):
        # 全仓防回退：排除 VCS/缓存目录；同时匹配 en dash 与 ASCII 连字符变体。
        excluded = {".git", "__pycache__", ".pytest_cache"}
        needles = ("G1" + "\u2013" + "G5", "G1" + "-" + "G5")
        offenders = []
        for path in sorted(REPO_ROOT.rglob("*")):
            if not path.is_file() or excluded & set(path.relative_to(REPO_ROOT).parts):
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if any(needle in text for needle in needles):
                offenders.append(str(path.relative_to(REPO_ROOT)))
        self.assertEqual([], offenders)

    def test_guide_helm_firstmate_reference_g1_g7(self):
        paths = [GUIDE, *HELM.values(), *FIRSTMATE.values()]
        for path in paths:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                self.assertIn("G1–G7", read(path))


class Fb010FirstmateTurnEndTest(unittest.TestCase):
    """FB-010：每轮结束前查 inbox，有未处置回执/待续派工作不得结束回合。"""

    def test_firstmate_checks_inbox_before_ending_turn(self):
        for runtime, path in FIRSTMATE.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                for phrase in (
                    "每轮结束前查",
                    "inbox",
                    "未处置回执",
                    "待续派",
                    "不得结束回合",
                    "R6",
                ):
                    self.assertIn(phrase, text)


class Fb013ErrorsTest(unittest.TestCase):
    """FB-013：Errors 当轮落表，空表写「无」禁「待记录」，worker 回执带出错误。"""

    def test_plan_discipline_records_errors_same_turn(self):
        text = read(PLAN_DISCIPLINE)
        for phrase in (
            "当轮",
            "Errors Encountered",
            "Error/Resolution",
            "空表写「无」",
            "不得留「待记录」",
        ):
            self.assertIn(phrase, text)

    def test_firstmate_checks_errors_on_close(self):
        for runtime, path in FIRSTMATE.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                for phrase in (
                    "Errors Encountered",
                    "当轮已记录",
                    "空表写「无」",
                    "不得留「待记录」",
                ):
                    self.assertIn(phrase, text)

    def test_worker_templates_carry_errors_in_findings(self):
        for path in WORKERS:
            with self.subTest(path=str(path.relative_to(REPO_ROOT))):
                text = read(path)
                self.assertIn("findings", text)
                self.assertIn("Error/Resolution", text)
                self.assertIn("Errors", text)


class Fb014ReleaseRestartTest(unittest.TestCase):
    """FB-014：发布后重启与可交接检查进入 guide 与 release 剧本。"""

    def test_guide_has_release_restart_section(self):
        text = read(GUIDE)
        for phrase in (
            "### 5.11 发布后重启与可交接检查",
            "先无锁取证、再启动 guard",
            "无 guard 持锁时跑",
            "非空 JSON",
            "不得作为证据",
            "guard.lock",
            "--once",
            "长驻角色",
            "compose 在启动时快照",
            "可交接性实测",
            "evidence/release/",
            "不得报发布",
        ):
            self.assertIn(phrase, text)

    def test_release_playbook_structure(self):
        text = read(RELEASE_PLAYBOOK)
        head, marker, rest = text.partition("## Phases")
        self.assertTrue(marker, "release.md 缺少 ## Phases")
        body, _, tail = rest.partition("\n## ")
        phase_count = len(re.findall(r"^### Phase \d+: ", body, re.MULTILINE))
        status_count = len(re.findall(r"^- \*\*Status:\*\* pending$", body, re.MULTILINE))
        self.assertGreaterEqual(phase_count, 5)
        self.assertEqual(phase_count, status_count)
        allowed = re.compile(r"^- (?:\[ \]|\[x\]|owner:|plan:|\*\*Status:\*\* pending$)")
        offenders = [
            line
            for line in body.splitlines()
            if line.startswith("- ") and not allowed.match(line)
        ]
        self.assertEqual([], offenders)
        phases = re.split(r"^### Phase \d+: .*$", body, flags=re.MULTILINE)[1:]
        self.assertEqual(phase_count, len(phases))
        for index, chunk in enumerate(phases, start=1):
            steps = [line for line in chunk.splitlines() if line.startswith("- [ ] ")]
            self.assertTrue(steps, f"Phase {index} 缺少 - [ ] 步骤")
        for outside in (head, tail):
            self.assertNotIn("### Phase", outside)
            self.assertNotIn("**Status:**", outside)

    def test_release_playbook_covers_release_steps(self):
        text = read(RELEASE_PLAYBOOK)
        for phrase in (
            "版本五处",
            "pytest",
            "crew-lint",
            "my-skills",
            "dry-run",
            "update-all.sh",
            "用户目录",
            "upgrade",
            "init",
            "doctor",
            "guard",
            "lock",
            "--once",
            "非空 JSON",
            "不得作为证据",
            "长驻角色",
            "可交接性实测",
            "evidence/release/",
            "myskills-push.txt",
            "user-install.txt",
            "upgrade-apply.txt",
            "init-check.txt",
            "doctor-after.json",
            "收口回执",
        ):
            self.assertIn(phrase, text)

    def test_readme_lists_release_playbook(self):
        text = read(PLAYBOOK_README)
        self.assertIn("`release.md`", text)
        self.assertIn("技能版本发布/升级", text)


class Fb011WakeTimestampTest(unittest.TestCase):
    """AMEND1/FB-011：helm 核对 guard.wake 时间戳，过期或无法解析按 fail-safe 忽略。"""

    def test_helm_checks_wake_generated_at(self):
        for runtime, path in HELM.items():
            with self.subTest(runtime=runtime):
                text = read(path)
                for phrase in (
                    "generated_at:",
                    "超过 2h",
                    "无法解析",
                    "fail-safe",
                    "不得按旧叫醒派活",
                    "guard 启动也会清理过期 wake",
                ):
                    self.assertIn(phrase, text)


class Fb012NotifyRuleTest(unittest.TestCase):
    """AMEND1/FB-012：跨项目通知只走 dispatch.py notify，禁止直写 herdr agent prompt。"""

    def test_dispatcher_list_includes_notify(self):
        rules = read(CREW_RULES)
        self.assertIn(
            "compose/start/prompt/close/notify/status/list/tab/pane",
            rules,
        )

    def test_cross_project_notify_boundary(self):
        rules = read(CREW_RULES)
        for phrase in (
            "跨项目通知（外部 agent）",
            "dispatch.py notify --agent <外部名> --message <文本>",
            "不查/不写 dispatch 注册表",
            "不 start/close",
            "禁止直写 `herdr agent prompt`",
        ):
            self.assertIn(phrase, rules)


if __name__ == "__main__":
    unittest.main()
