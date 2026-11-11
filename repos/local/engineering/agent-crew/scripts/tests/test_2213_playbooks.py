#!/usr/bin/env python3
"""2.2.13 剧本 slice 防回退断言（C 维护循环 + release 交付门 + T27-S1/S2/S4）。

覆盖：
- `maintain-verification.md`：三 Outcome、Edit scope、Pass 0–6 关键词、经济档串行、
  运行记录落 `.scratch/` 不提交、角色能力对齐（可写阶段 owner ∈ {builder, runner}，
  只读阶段 owner ∈ {scout, firstmate}）。
- `README.md` 登记新剧本与统一触发/下限口径。
- `release.md`：交付门位于项目升级之后、维护 Phase 先于收口回执、Phase 6④
  证据清单含 `verify-skill.txt` 与维护 outcome、S1/S2/S4 口径；原有 7 Phase
  标题与关键步骤行不删不改写。
- `plan-status.py --playbooks templates/playbooks` 全绿。

S6-2 取舍：原 7 Phase 使用「关键步骤片段快照 + M1/M2/M3/M7 反向实验覆盖」，
不采用整行哈希（版本措辞微调即红、维护成本高）；Phase 6④ 证据清单行例外做
整行断言（rereview M8 缺口）。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLAYBOOKS = REPO_ROOT / "templates" / "playbooks"
MAINTAIN = PLAYBOOKS / "maintain-verification.md"
README = PLAYBOOKS / "README.md"
RELEASE = PLAYBOOKS / "release.md"

ORIGINAL_RELEASE_PHASES = (
    "版本五处一致",
    "全量测试与静态检查",
    "同步与 push",
    "用户目录刷新",
    "项目升级与体检",
    "升级后重启与可交接检查",
    "收口回执",
)

# Phase 6④ 证据清单整行快照：删 `verify-skill.txt` 即红（rereview M8）。
PHASE6_EVIDENCE_LINE = (
    "- [ ] ④ 证据落 `.planning/<task>/evidence/release/`（`myskills-push.txt`、"
    "`user-install.txt`、`upgrade-apply.txt`、`init-check.txt`、`doctor-after.json`、"
    "`guard-once.json`、`guard-restart.txt`、`handover-readiness.txt`、`verify-skill.txt`、"
    "`maintain-verification.txt`（Phase 7 维护 outcome，产出后补入））；"
    "任一未完成或无证据不得报发布收口"
)

# 原有 7 Phase 的关键步骤行快照：防只保留标题、步骤被整体改写。
ORIGINAL_STEP_SNAPSHOTS = {
    "版本五处一致": ("更新版本五处", "用版本断言测试与 grep 复核五处一致"),
    "全量测试与静态检查": ("uv run --with pytest pytest scripts/tests -q", "crew-lint.py --purity"),
    "同步与 push": ("git push --dry-run", "origin/master"),
    "用户目录刷新": ("update-all.sh --skill agent-crew", "用户目录 SKILL.md"),
    "项目升级与体检": (
        "upgrade.py --apply --confirm",
        "init.py --check",
        "doctor.py --root . --profile release --strict --json",
    ),
    "升级后重启与可交接检查": (
        "先无锁取证、再启动 guard",
        "compose 在启动时快照",
        "herdr pane read",
        "证据落 `.planning/<task>/evidence/release/`",
    ),
    "收口回执": ("按 R2 写回执", "plan-status.py --write"),
}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def phase_chunks(text: str) -> dict[str, str]:
    """取 `## Phases` 到下一个 `## ` 之间的 Phase 文本块，键为「Phase N」。"""
    body = text.partition("## Phases")[2].partition("\n## ")[0]
    heads = list(re.finditer(r"^### Phase (\d+): (.*)$", body, flags=re.MULTILINE))
    chunks: dict[str, str] = {}
    for index, match in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(body)
        chunks[f"Phase {match.group(1)}"] = body[match.start():end]
    return chunks


def phase_index(text: str, title: str) -> int:
    match = re.search(rf"^### Phase (\d+): {re.escape(title)}", text, flags=re.MULTILINE)
    if not match:
        raise AssertionError(f"缺少 Phase：{title}")
    return int(match.group(1))


def phase_by_title(text: str, title: str) -> str:
    chunks = phase_chunks(text)
    for chunk in chunks.values():
        if chunk.splitlines() and title in chunk.splitlines()[0]:
            return chunk
    raise AssertionError(f"缺少 Phase：{title}")


def owner_of(chunk: str) -> str:
    match = re.search(r"^- owner:\s*(\S+)", chunk, flags=re.MULTILINE)
    if not match:
        raise AssertionError("Phase 缺少 owner")
    return match.group(1)


class MaintainVerificationPlaybookTest(unittest.TestCase):
    """C：维护循环剧本的契约、可执行步骤与角色能力对齐。"""

    @classmethod
    def setUpClass(cls):
        if not MAINTAIN.exists():
            raise AssertionError(f"缺少维护循环剧本：{MAINTAIN}")
        cls.text = read(MAINTAIN)
        cls.phases = phase_chunks(cls.text)

    def test_three_outcomes_are_explicit(self):
        for outcome in ("**clean**", "**changed**", "**blocked**"):
            self.assertIn(outcome, self.text)
        for phrase in ("不提交", "一次本地 commit", "不自行 push", "卡点"):
            self.assertIn(phrase, self.text)

    def test_edit_scope_is_verification_skill_only(self):
        for phrase in (
            "Edit scope",
            "只改验证技能自身目录",
            "绝不改产品代码",
            "doc drift",
            "product regression",
            "只报不修",
        ):
            self.assertIn(phrase, self.text)

    def test_pass_steps_cover_full_loop(self):
        self.assertEqual(6, len(self.phases), sorted(self.phases))
        joined = "\n".join(self.phases.values())
        for phrase in (
            "定位目标",
            "index hygiene",
            "source wave",
            "reconcile",
            "live pass",
            "triage",
            "ship or stop",
            "四 H2",
            "Sub-features",
            "How to get to it (user POV)",
            "Driving it with",
            "Gotchas",
            "verified-unreachable",
        ):
            self.assertIn(phrase, joined, f"缺少 Pass 关键词：{phrase}")

    def test_live_pass_invariants(self):
        for phrase in (
            "证据不变式",
            "清理后仍在",
            "失败先清理再重试",
            "重试一次",
            "最终 teardown",
        ):
            self.assertIn(phrase, self.text)

    def test_economy_mode_is_serial_but_coverage_unchanged(self):
        for phrase in ("默认串行", "不强制并行子代理", "覆盖率要求不变", "副舵按预算增派只读子代理"):
            self.assertIn(phrase, self.text)

    def test_run_notes_land_in_scratch_and_stay_uncommitted(self):
        for phrase in (".scratch/", "covered", "unreachable", "drift", "outcome", "不提交"):
            self.assertIn(phrase, self.text)

    def test_each_phase_has_one_status_and_steps(self):
        self.assertEqual(6, len(self.phases))
        for name, chunk in self.phases.items():
            self.assertEqual(1, chunk.count("**Status:** pending"), name)
            self.assertTrue(any(line.startswith("- [ ] ") for line in chunk.splitlines()), name)

    def test_owner_matches_role_capability(self):
        # 可写阶段（编辑/驱动/commit）必须由 builder 或 runner 执行；只读阶段用 scout/firstmate。
        for phase in ("Phase 1", "Phase 3", "Phase 4", "Phase 5", "Phase 6"):
            self.assertIn(owner_of(self.phases[phase]), {"builder", "runner"}, phase)
        self.assertIn(owner_of(self.phases["Phase 2"]), {"scout", "firstmate"})
        for phrase in (
            "firstmate：选板、派工、收尾与 outcome 写入回执",
            "不直接修 map/harness，不 commit",
            "builder：Phase 1/3/4/5/6 的落盘、驱动与 commit",
        ):
            self.assertIn(phrase, self.text)

    def test_trigger_matches_release_and_readme(self):
        for phrase in (
            "发布执行完成（最终收口回执前）",
            "最迟下一个工作会话",
            "每周复盘兜底",
            "outcome 写入最终收口回执",
        ):
            self.assertIn(phrase, self.text)

    def test_create_verification_skill_is_external(self):
        for phrase in ("外部工程技能", "需环境已安装", "⏸ 上抛 Captain", "记录缺口"):
            self.assertIn(phrase, self.text)

    def test_each_create_verification_reference_has_external_skill_note(self):
        for path in (MAINTAIN, RELEASE):
            lines = [
                line
                for line in read(path).splitlines()
                if "create-verification-skill" in line
            ]
            self.assertTrue(lines, f"{path.name} 未找到 create-verification-skill 引用")
            for line in lines:
                with self.subTest(path=path.name):
                    self.assertTrue(
                        "外部工程技能" in line or "外部技能" in line, line
                    )
                    self.assertIn("⏸ 上抛", line)


class PlaybookIndexTest(unittest.TestCase):
    """README：登记维护循环剧本与统一触发/下限口径。"""

    def test_readme_registers_playbook_and_trigger(self):
        text = read(README)
        self.assertIn("`maintain-verification.md`", text)
        self.assertIn("发布执行完成（最终收口回执前）", text)
        self.assertIn("最迟下一个工作会话", text)
        self.assertIn("每周复盘兜底", text)
        self.assertIn("完整执行维护剧本 Phase 1–6", text)
        self.assertIn("不替代不可跳过项", text)
        self.assertNotIn("至少 source wave + live pass", text)
        self.assertNotIn("发布收口后当轮启动", text)

    def test_readme_release_row_mentions_gate_and_order(self):
        text = read(README)
        self.assertIn("安装后验证技能交付门", text)
        self.assertIn("发布后维护循环先于收口回执", text)


class ReleaseDeliveryGateTest(unittest.TestCase):
    """release.md：交付门时序、维护/收口顺序与 T27-S1/S2/S4 口径。"""

    @classmethod
    def setUpClass(cls):
        cls.text = read(RELEASE)
        cls.phases = phase_chunks(cls.text)

    def test_original_seven_phases_preserved(self):
        for title in ORIGINAL_RELEASE_PHASES:
            self.assertIn(f": {title}", self.text)
        self.assertEqual(8, len(self.phases), sorted(self.phases))

    def test_original_phase_steps_not_rewritten(self):
        for title, phrases in ORIGINAL_STEP_SNAPSHOTS.items():
            chunk = phase_by_title(self.text, title)
            for phrase in phrases:
                self.assertIn(phrase, chunk, f"Phase「{title}」缺少原步骤片段：{phrase}")

    def test_delivery_gate_runs_after_project_upgrade(self):
        gate = phase_index(self.text, "升级后重启与可交接检查")
        upgrade = phase_index(self.text, "项目升级与体检")
        self.assertGreater(gate, upgrade)
        gate_chunk = phase_by_title(self.text, "升级后重启与可交接检查")
        for phrase in (
            "验证技能交付门（安装后",
            "项目升级/安装完成后必须重跑",
            "以新证据为准",
        ):
            self.assertIn(phrase, gate_chunk)
        self.assertNotIn("验证技能交付门", self.phases["Phase 2"])

    def test_delivery_gate_covers_two_change_classes(self):
        gate_chunk = phase_by_title(self.text, "升级后重启与可交接检查")
        for phrase in (
            "由 builder/runner 执行",
            "行为/脚本改动",
            "真实驱动",
            "规则/模板/提示词文本改动",
            "grep/lint/schema 测试为主证据",
            "smoke feature",
            "feature 名、命令、退出码、证据路径",
            "<skills>/verify-<app>/",
            "项目层安装、不随技能分发",
            "verify-skill.txt",
            "test -f",
            "create-verification-skill",
            "外部工程技能",
            "需环境已安装",
            "未安装",
            "⏸ 上抛 Captain",
            "记录缺口",
            "不得跳过或用人肉清单替代",
        ):
            self.assertIn(phrase, gate_chunk)
        # 防拆分绕过：本仓路径与技能名分别断言（rereview S5-1）。
        self.assertNotIn(".qoder/skills/", self.text)
        self.assertNotIn("verify-crew/", self.text)

    def test_phase6_evidence_list_has_gate_and_maintenance_outcome(self):
        lines = [line for line in self.text.splitlines() if line.startswith("- [ ] ④ 证据落")]
        self.assertEqual([PHASE6_EVIDENCE_LINE], lines)

    def test_maintenance_phase_precedes_receipt(self):
        maintenance = phase_index(self.text, "发布后维护循环（验证技能）")
        receipt = phase_index(self.text, "收口回执")
        self.assertLess(maintenance, receipt)
        self.assertLess(phase_index(self.text, "升级后重启与可交接检查"), maintenance)
        maintenance_chunk = phase_by_title(self.text, "发布后维护循环（验证技能）")
        for phrase in (
            "完整执行 `maintain-verification.md` Phase 1–6",
            "不替代该剧本的不可跳过项",
            "Phase 8 收口回执",
            "clean/changed/blocked",
            "不自行 push",
            "builder/runner",
        ):
            self.assertIn(phrase, maintenance_chunk)
        receipt_chunk = phase_by_title(self.text, "收口回执")
        for phrase in ("Phase 6 验证技能交付门证据", "Phase 7 维护 outcome"):
            self.assertIn(phrase, receipt_chunk)

    def test_trigger_wording_matches_readme_and_maintain(self):
        maintenance_chunk = phase_by_title(self.text, "发布后维护循环（验证技能）")
        for phrase in (
            "发布执行完成（最终收口回执前）",
            "最迟下一个工作会话",
            "每周复盘兜底",
            "outcome 写入 Phase 8 回执",
        ):
            self.assertIn(phrase, maintenance_chunk)
        self.assertNotIn("发布收口后当轮启动", self.text)
        self.assertNotIn("至少 source wave + live pass", self.text)

    def test_no_source_chain_residue_in_playbooks(self):
        for path in (RELEASE, MAINTAIN, README):
            text = read(path)
            with self.subTest(path=path.name):
                self.assertNotIn("真源链", text)
                self.assertNotIn("人肉核对清单", text)

    def test_phase5_strict_warn_semantics(self):
        phase5 = self.phases["Phase 5"]
        for phrase in (
            "--strict",
            "warn 也会使 rc=1",
            "判定以 fail=0 为准",
            "warn 计数记录",
            "H2/H3",
            "不得因 warn 停发",
            "仅有未解释的 fail 才停",
        ):
            self.assertIn(phrase, phase5)

    def test_phase2_grep_uses_word_boundary_and_production_dirs(self):
        phase2 = self.phases["Phase 2"]
        for phrase in ("grep -rnw", "生产目录", "反向断言"):
            self.assertIn(phrase, phase2)

    def test_install_diff_excludes_caches(self):
        phase4 = self.phases["Phase 4"]
        for phrase in ("__pycache__", ".pytest_cache", "diff -rq -x"):
            self.assertIn(phrase, phase4)

    def test_external_snapshot_has_timestamp_and_head(self):
        phase6 = self.phases["Phase 6"]
        for phrase in ("date -Iseconds", "git rev-parse HEAD", "外部并发提交", "只读"):
            self.assertIn(phrase, phase6)

    def test_maintenance_is_not_skippable(self):
        tail = self.text.partition("## 跳过规则")[2]
        for phrase in ("发布后维护循环", "完整执行 `maintain-verification.md` Phase 1–6", "不替代"):
            self.assertIn(phrase, tail)


class PlaybooksLintTest(unittest.TestCase):
    """剧本结构可被 plan-status 解析（每 Phase 一行 Status、标准 `- [ ]`）。"""

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
        self.assertGreaterEqual(int(match.group(1)), 9, proc.stdout)


if __name__ == "__main__":
    unittest.main()
