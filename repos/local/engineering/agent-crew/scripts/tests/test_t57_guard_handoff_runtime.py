#!/usr/bin/env python3
"""2.2.27 / T57：crew-guard 交接 runtime 继承（R2）+ T64 idle 兜底默认 60 分钟。

覆盖（评估报告 §3 表 2 + §4 R2/R4/R7）：
① effective_helm_runtime：runtime ∈ 三栈直读；旧过渡记录
   runtime=qoder+actualRuntime=claude → 读作 claude（R7 backwards）；
② handoff_start_args：按现役舵 runtime 组装（R2 实证——claude 舵交接
   start_args 含 `--runtime claude` + claude 形态 permission-mode）；
③ opencode 舵不再被塞 qoder 形态 --permission-mode（dispatch 会 rc=2 拒绝）；
④ 无 registry 记录时保持现状（bypass_permissions、无 --runtime）；
⑤ T64：DEFAULT_IDLE_WAKE_ROUNDS 默认 5→30（120s×30=60 分钟，Captain 口径
   每小时心跳；本仓 orchestration.json guard.idleWakeRounds=30 实证）；
⑥ 集成：ctx 达交接阈值 + 过渡记录 registry → dispatch start 实参含
   `--runtime claude`（红灯=错交接 qoder）。

运行：python3 -m unittest scripts.tests.test_t57_guard_handoff_runtime -v
"""

from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
_SPEC_PATH = SKILL_DIR / "scripts" / "crew-guard.py"
_spec = importlib.util.spec_from_file_location("crew_guard_t57", _SPEC_PATH)
cg = importlib.util.module_from_spec(_spec)
sys.modules["crew_guard_t57"] = cg
_spec.loader.exec_module(cg)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_crew_guard import EMPTY_PORTFOLIO, EMPTY_STREAM, GuardTestBase  # noqa: E402


class EffectiveHelmRuntimeTest(unittest.TestCase):
    """R7 backwards 读法（函数级）。"""

    def test_three_runtime_direct_read(self):
        self.assertEqual("qoder", cg.effective_helm_runtime({"runtime": "qoder"}))
        self.assertEqual("opencode", cg.effective_helm_runtime({"runtime": "opencode"}))
        self.assertEqual("claude", cg.effective_helm_runtime({"runtime": "claude"}))

    def test_legacy_transition_record_reads_as_claude(self):
        self.assertEqual(
            "claude",
            cg.effective_helm_runtime(
                {"runtime": "qoder", "actualRuntime": "claude"}))

    def test_non_runtime_fields_rejected(self):
        self.assertEqual("", cg.effective_helm_runtime({"runtime": "weird"}))
        self.assertEqual("", cg.effective_helm_runtime({}))
        self.assertEqual("", cg.effective_helm_runtime(None))
        self.assertEqual(
            "", cg.effective_helm_runtime({"actualRuntime": "not-a-runtime"}))

    def test_actual_runtime_only_accepted_in_three_stack(self):
        self.assertEqual(
            "qoder",
            cg.effective_helm_runtime(
                {"runtime": "weird", "actualRuntime": "qoder"}))


class HandoffStartArgsTest(unittest.TestCase):
    """R2：交接 start_args 继承现役舵 runtime，而非 qoder 单值假设。"""

    # 2.2.32/T76/C2：start 不再手传 --permission-mode（配置/模板解析），
    # 只保留 runtime 继承与消歧语义。

    def test_claude_runtime_claude_args(self):
        args = cg.handoff_start_args("claude", "wX:pNEW", "helm-1-2")
        self.assertEqual(
            ["start", "--role", "helm", "--pane", "wX:pNEW",
             "--runtime", "claude", "--name", "helm-1-2", "--json"],
            args)

    def test_qoder_runtime_qoder_args(self):
        args = cg.handoff_start_args("qoder", "wX:pNEW", "helm-1-2")
        self.assertIn("--runtime", args)
        self.assertEqual("qoder", args[args.index("--runtime") + 1])
        self.assertNotIn("--permission-mode", args)

    def test_opencode_runtime_no_qoder_permission_flag(self):
        args = cg.handoff_start_args("opencode", "wX:pNEW", "helm-1-2")
        self.assertNotIn("--permission-mode", args)
        self.assertIn("--runtime", args)
        self.assertEqual("opencode", args[args.index("--runtime") + 1])

    def test_missing_runtime_keeps_legacy_behavior(self):
        args = cg.handoff_start_args("", "wX:pNEW", "helm-1-2")
        self.assertNotIn("--runtime", args)
        self.assertNotIn("--permission-mode", args)


class IdleWakeDefaultTest(unittest.TestCase):
    """T64 口径沿革：2.2.26 默认 30（≈60 分钟报平安）；2.2.31（guard-noise 流）
    默认 0＝状态变化驱动接管（无变化零叫醒），>0 显式恢复旧兜底口径。"""

    def test_default_idle_wake_rounds_is_zero(self):
        self.assertEqual(0, cg.DEFAULT_IDLE_WAKE_ROUNDS)

    def test_max_clamp_still_240(self):
        self.assertEqual(240, cg.IDLE_WAKE_ROUNDS_MAX)


class GuardHandoffClaudeRuntimeTest(GuardTestBase):
    """R2 集成：ctx 达阈值 + 过渡记录 registry → start 实参 --runtime claude。"""

    OLD_PANE = "wX:pOLD"

    def prepare_handoff(self, ctx_k: int = 650, registry: dict | None = None) -> None:
        self.write_fixture(
            stream=EMPTY_STREAM,
            portfolio=EMPTY_PORTFOLIO,
            config={
                "portfolioDir": ".planning/portfolio",
                "helm": {"agentName": "helm-1"},
            },
        )
        self.agents = [
            {"name": "helm-1", "pane_id": self.OLD_PANE, "tab_id": "wX:tOLD",
             "workspace_id": "wX", "agent_status": "working"}
        ]
        self.pane_outputs[self.OLD_PANE] = f"status line ctx ({ctx_k}k/1M) tail"
        self.crew.mkdir(parents=True, exist_ok=True)
        if registry is not None:
            (self.crew / "dispatch-agents.json").write_text(
                json.dumps(registry), encoding="utf-8")

    def test_handoff_claude_transition_record_starts_claude_successor(self):
        self.prepare_handoff(registry={
            "helm-1": {"role": "helm", "runtime": "qoder", "actualRuntime": "claude"},
        })
        self.make_guard().run_once()
        starts = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(starts))
        self.assertIn("--runtime", starts[0])
        self.assertEqual("claude", starts[0][starts[0].index("--runtime") + 1])
        self.assertNotIn("qoder", starts[0])

    def test_handoff_claude_record_starts_claude_successor(self):
        self.prepare_handoff(registry={
            "helm-1": {"role": "helm", "runtime": "claude"},
        })
        self.make_guard().run_once()
        starts = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(starts))
        self.assertEqual("claude", starts[0][starts[0].index("--runtime") + 1])
        # 2.2.32/T76/C2：permission-mode 不再手传（配置解析）
        self.assertNotIn("--permission-mode", starts[0])

    def test_handoff_qoder_record_keeps_qoder_successor(self):
        self.prepare_handoff(registry={
            "helm-1": {"role": "helm", "runtime": "qoder"},
        })
        self.make_guard().run_once()
        starts = [c for c in self.dispatch_calls if c[:1] == ["start"]]
        self.assertEqual(1, len(starts))
        self.assertEqual("qoder", starts[0][starts[0].index("--runtime") + 1])


if __name__ == "__main__":
    unittest.main()
