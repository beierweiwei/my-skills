#!/usr/bin/env python3
"""2.2.28 FB-054：审批残留判定（S0 阻断 B1/B2 修法）+ G2 正文同步（S5）。

背景（FB-054）：guard 审批开口事件无闭合 + agent 已 idle 仍按「等待审批」
叫醒（假审批叫醒 16 分钟一次，狼来了效应淹真审批）。

S0 裁定修法（不得改数；抑制只发生在此处）：
1. B1：①「agent idle/done 即视为审批结束、不叫醒」只在 guard 叫醒判定层
   实施、只作用 worker（stuck 天然不含 helm；`_helm_has_pending_ask` 独立
   路径不动）；`crew_approvals.pending_approvals` 公共返回不动（注入前安全
   检查依赖它），本文件断言其返回与 agent_status 无关；
2. B2：③ callId 非 `call_N` 形态只标注不抑制（叫醒判定照常，消息/guard.log
   附「callId 形态异常，疑数据链缺陷」）；抑制 exclusively 由①承担；
3. S6：未闭合超龄 >30 分钟的被抑制项记一条按消息去重的「审批残留」guard.log
   （30 分钟=S0 权威裁定）；
4. S7：agent↔entry 匹配失配时 fail-open 照常叫醒，不静默抑制。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_2221_stuck_detect import (  # noqa: E402
    ACCIDENT_ASK,
    GuardHarness,
    approval_entry,
    ca,
    cg,
    write_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class ApprovalResidualTest(GuardHarness):
    def _with_approval(self, agents: list[dict], **entry_kwargs):
        self.write_fixture(pending=True)
        self.agents = agents
        guard = self.make_guard()
        guard.stuck_detect = lambda _agents, _now: [approval_entry(**entry_kwargs)]
        return guard

    def test_approval_suppressed_when_agent_done(self):
        """①：agent done → 审批视为结束，不叫醒（B/B 通道静默）。"""
        guard = self._with_approval(
            [{"name": "worker-1", "agent_status": "done"}], age_seconds=16 * 60)
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)
        self.assertNotIn("等待审批", result["reason"])

    def test_approval_suppressed_when_agent_idle(self):
        """①：agent idle → 同上（idle=待命≠等审批）。"""
        guard = self._with_approval(
            [{"name": "worker-1", "agent_status": "idle"}], age_seconds=16 * 60)
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertEqual([], self.prompt_calls)

    def test_approval_still_wakes_when_agent_working(self):
        """working + 超阈值 → 照常叫醒（①不削弱真审批可见性）。"""
        guard = self._with_approval(
            [{"name": "worker-1", "agent_status": "working"}],
            age_seconds=16 * 60)
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertIn("等待审批", result["reason"])

    def test_fail_open_when_agent_not_in_list(self):
        """S7：entry 的 agent 不在 herdr 名单（匹配失配）→ 照常叫醒。"""
        self.write_fixture(pending=True)
        guard = self.make_guard()
        ghost = approval_entry(agent="ghost-agent", age_seconds=16 * 60)
        guard.stuck_detect = lambda _agents, _now: [ghost]
        result = guard.run_once()
        self.assertTrue(result["woke"], "匹配失配必须 fail-open 照常叫醒")

    def test_residual_logged_over_30min_deduped(self):
        """4c①：时变 age 下残留日志 keyed（agent|callId）去重——只记一条。

        subA 实证旧按文本去重失效（消息含时变年龄，4 轮 4 条）；夹具改为
        每轮递增 age（31→33→35 分钟）模拟生产时变，断言仍只记 1 条。
        """
        self.write_fixture(pending=True)
        self.agents = [{"name": "worker-1", "agent_status": "done"}]
        guard = self.make_guard()
        ages = iter([31 * 60, 33 * 60, 35 * 60])

        def time_varying_stub(_agents, _now):
            return [approval_entry(age_seconds=next(ages, 37 * 60),
                                   call_id="call_09")]

        guard.stuck_detect = time_varying_stub
        for _ in range(3):
            guard.run_once()
        residual_lines = [line for line in self.logs if "审批残留" in line]
        self.assertEqual(1, len(residual_lines),
                         f"时变 age 下必须按 keyed 去重只记 1 条；logs={self.logs}")
        self.assertIn("call_09", residual_lines[0])
        # ask 消失（settle 补写）后键复位：再现超龄残留按新事件重记
        guard.residual_logged.clear()
        guard.run_once()
        residual_lines = [line for line in self.logs if "审批残留" in line]
        self.assertEqual(2, len(residual_lines), "键复位后应按新事件重记")

    def test_residual_not_logged_under_30min(self):
        """30 分钟内被抑制不记残留（正常完成场景）。"""
        guard = self._with_approval(
            [{"name": "worker-1", "agent_status": "done"}],
            age_seconds=10 * 60)
        guard.run_once()
        self.assertFalse(any("审批残留" in line for line in self.logs))

    def test_abnormal_callid_annotated_not_suppressed(self):
        """B2：callId 非 call_N 形态 + working → 照常叫醒，只标注数据链缺陷。"""
        guard = self._with_approval(
            [{"name": "worker-1", "agent_status": "working"}],
            age_seconds=16 * 60, call_id="call_x")
        result = guard.run_once()
        self.assertTrue(result["woke"], "畸形 callId 不得抑制叫醒（保守性不倒退）")
        message = self.prompt_calls[0][1]
        self.assertIn("callId 形态异常", message)
        self.assertIn("call_x", message)

    def test_helm_pending_ask_path_unaffected(self):
        """B1.2：helm 自身 ask 走 _helm_has_pending_ask 独立通道，不受①影响。"""
        self.write_fixture(pending=True)
        helm = {"name": "helm-1", "agent_status": "idle",
                "pane_id": "pane-helm-1"}
        self.agents = [helm]
        guard = self.make_guard()
        calls: list[list[dict]] = []

        def spy_detect(agents, now):
            calls.append(list(agents))
            if agents and agents[0].get("name") == "helm-1":
                return [approval_entry(agent="helm-1", age_seconds=60)]
            return []

        guard._detect_stuck = spy_detect
        guard.run_once()
        guard.run_once()  # G2 两轮防抖后叫醒 → _wake → helm ask 通道
        self.assertTrue(
            any(agents and agents[0].get("name") == "helm-1" for agents in calls),
            "helm ask 检测路径必须被走到")
        self.assertTrue(self.notify_calls, "helm 审批必须经通知通道可见")


class PendingApprovalsStatusAgnosticTest(unittest.TestCase):
    """B1 测试断言：pending_approvals 公共返回与 agent_status 无关。"""

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name) / "qoder-home"
        from test_2221_stuck_detect import RUN_ID, SESSION, SLUG
        self.session = SESSION
        segment = (self.home / "logs" / "sessions" / SLUG / SESSION
                   / "segments" / f"{RUN_ID}.jsonl")
        run_log = self.home / "logs" / "runs" / RUN_ID / "qodercli.log"
        write_file(segment, "{}\n")
        write_file(run_log, ACCIDENT_ASK + "\n")
        # ACCIDENT_ASK 时间戳 2026-11-12T22:05:51+08:00；now 取其后 10 分钟 → age≈600
        from datetime import datetime, timedelta, timezone
        ask_dt = datetime(2026, 11, 12, 22, 5, 51, tzinfo=timezone(timedelta(hours=8)))
        self.now = ask_dt.timestamp() + 600

    def test_return_ignores_agent_status(self):
        for status in ("idle", "done", "working", None):
            with self.subTest(status=status):
                agent = {"name": "worker-1", "pane_id": "wX:p1", "cwd": "/work",
                         "agent_session": {"agent": "qodercli",
                                           "value": self.session}}
                if status is not None:
                    agent["agent_status"] = status
                items = ca.pending_approvals([agent], self.now,
                                             qoder_home=self.home)
                self.assertEqual(1, len(items), "status 不得过滤审批返回")


class G2RulesTextTest(unittest.TestCase):
    """S5：crew-rules.md G2 正文同步（快速叫醒条款 + idleWakeRounds 漂移修正）。"""

    def read_g2(self) -> str:
        text = (REPO_ROOT / "references" / "crew-rules.md").read_text(
            encoding="utf-8")
        marker = "- **G2 唤醒条件**"
        begin = text.find(marker)
        if begin < 0:
            raise AssertionError("crew-rules.md 缺少 G2 唤醒条件条目")
        nxt = text.find("\n- **G", begin + len(marker))
        return text[begin:nxt if nxt > 0 else len(text)]

    def test_g2_has_diff_edge_wake_clause(self):
        g2 = self.read_g2()
        self.assertIn("差集", g2, "G2 须写明完成差集边沿语义")
        self.assertIn("无待办", g2, "G2 须写明无待办也触发（去「且有待办」前提）")

    def test_g2_idle_wake_rounds_default_aligned_with_code(self):
        g2 = self.read_g2()
        self.assertNotIn("默认 5", g2, "G2 的 idleWakeRounds 旧默认值（5≈10 分钟）已漂移，须为 30≈60")
        self.assertIn("30", g2, "G2 须写 idleWakeRounds 默认 30≈60 分钟（T64 口径）")


if __name__ == "__main__":
    unittest.main()
