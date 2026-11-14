#!/usr/bin/env python3
"""2.2.32 工单 C/T76/C1：通知送达唤醒链（B1 同项目直连注入 + B2 兜底桶）。

B1（D7 验收断言，fake herdr 端到端）：
- claude→claude / claude→qoder 同项目回执到达即注入（delivery=herdr、
  runtime=same-project、delivery_runtime=herdr-prompt）；
- 收件方聚焦 → inbox_fallback 不注入（守卫不回归）；
- 跨产品/跨项目 → 仍 notification（不超放宽）。
to 字段（补强）：--to 落进回执留存；guard 归属优先 to 精确命中，无 to 回退
work_slot/task 正文；发送方自己排除。
B2 兜底桶（guard fake）：idle≥300s 工位名下未消费回执/被拒派单注入固定模板；
聚焦/审批延后；同条目只叫一次；跨重启恢复（guard.state.json unconsumedWake）；
无主回执跳过；working 不叫；helm 角色/前缀排除、firstmate 可叫。
fixture 全部为中性占位内容。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cm = _load("crew_msg_t76c1", _SCRIPTS / "crew-msg.py")
cg = _load("crew_guard_t76c1", _SCRIPTS / "crew-guard.py")

TARGET = "proj-worker-1"


def make_receipt(**overrides) -> dict:
    fields = dict(
        from_="patch-2232-crewmate",
        runtime="claude",
        task="patch-2232/03-C",
        status="done",
        summary="回执到达即唤醒",
        commit="",
        changed_files=[],
        evidence=["unit"],
        work_slot="patch-2232-crewmate（claude 工位）",
        findings=[],
        receipt_id="r-t76-1",
        ts="2026-11-14T00:00:00Z",
    )
    fields.update(overrides)
    return cm.build_receipt(**fields)


class FakeHerdr:
    """按 argv 模拟 herdr agent list / pane list / agent prompt / notification 与 git。"""

    def __init__(self, agents: list[dict], panes: list[dict] | None = None,
                 focused: bool | None = False) -> None:
        self.agents = agents
        self.panes = panes if panes is not None else [
            {"pane_id": "wQ:p1", "focused": focused}]
        self.calls: list[list[str]] = []
        self.notify_calls: list[tuple] = []

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return mock.Mock(returncode=0, stderr="", stdout=json.dumps(
                {"result": {"agents": self.agents}}))
        if argv[:3] == ["herdr", "pane", "list"]:
            return mock.Mock(returncode=0, stderr="", stdout=json.dumps(
                {"result": {"panes": self.panes}}))
        if argv[:2] == ["git", "-C"]:
            return mock.Mock(returncode=0, stdout=".git\n", stderr="")
        if argv[:3] == ["herdr", "notification", "show"]:
            self.notify_calls.append(tuple(argv[3:]))
            return mock.Mock(returncode=0, stdout="", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    def prompt_calls(self) -> list[list[str]]:
        return [call for call in self.calls
                if call[:3] == ["herdr", "agent", "prompt"]]


# —— B1：同项目直连注入链 ——

class SameProjectInjectTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.repo = self.base / "proj"
        (self.repo / ".git" / "crew").mkdir(parents=True)
        self.crew = self.repo / ".git" / "crew"

    def _agent(self, cwd: Path | None = None) -> dict:
        return {"name": TARGET, "cwd": str(cwd or self.repo), "pane_id": "wQ:p1"}

    def _push(self, fake: FakeHerdr, receipt: dict, *,
              to: str = TARGET, target_runtime: str = "auto") -> dict:
        with mock.patch.object(cm.subprocess, "run", side_effect=fake):
            result = cm.write_receipt(
                self.crew, receipt, to=to,
                target_runtime=target_runtime, push=True)
        return result["push"]  # 投递结果在 push 键下

    def test_claude_to_claude_same_project_injects(self):
        fake = FakeHerdr([self._agent()])
        result = self._push(fake, make_receipt(runtime="claude"))
        self.assertEqual("herdr", result["delivery"], result)
        self.assertEqual("same-project", result.get("runtime"))
        self.assertEqual("herdr-prompt", result.get("delivery_runtime"))
        self.assertEqual(1, len(fake.prompt_calls()), fake.calls)
        self.assertEqual(TARGET, fake.prompt_calls()[0][3])

    def test_claude_sender_to_qoder_target_same_project_injects(self):
        # 收件方是 qoder kind 工位（herdr agent list 不区分 kind；同项目即注入）
        fake = FakeHerdr([self._agent()])
        result = self._push(fake, make_receipt(runtime="claude"))
        self.assertEqual("herdr", result["delivery"])
        self.assertEqual(1, len(fake.prompt_calls()))

    def test_focused_target_falls_back_no_injection(self):
        fake = FakeHerdr([self._agent()], focused=True)
        result = self._push(fake, make_receipt())
        self.assertEqual("inbox_fallback", result["delivery"])
        self.assertEqual([], fake.prompt_calls())

    def test_cross_project_stays_notification(self):
        other = self.base / "other-proj"
        (other / ".git" / "crew").mkdir(parents=True)
        fake = FakeHerdr([self._agent(cwd=other)])
        result = self._push(fake, make_receipt())
        self.assertEqual("notification", result["delivery"])
        self.assertEqual([], fake.prompt_calls())

    def test_explicit_cross_runtime_stays_notification(self):
        fake = FakeHerdr([self._agent()])
        result = self._push(fake, make_receipt(runtime="claude"),
                            target_runtime="qoder")
        self.assertEqual("notification", result["delivery"], result)
        self.assertEqual([], fake.prompt_calls())

    def test_qoder_sender_same_project_still_injects(self):
        fake = FakeHerdr([self._agent()])
        result = self._push(fake, make_receipt(runtime="qoder"))
        self.assertEqual("herdr", result["delivery"])
        self.assertEqual(1, len(fake.prompt_calls()))


# —— to 字段：写入与归属 ——

class ReceiptToFieldTest(unittest.TestCase):
    def test_build_receipt_records_to_when_given(self):
        receipt = make_receipt(to=TARGET)
        self.assertEqual(TARGET, receipt.get("to"))
        receipt = make_receipt()
        self.assertNotIn("to", receipt)  # 可选：不给不落，旧口径不变

    def test_match_owner_prefers_exact_to(self):
        receipt = make_receipt(to=TARGET)
        self.assertTrue(cg.Guard._match_unconsumed_owner(receipt, TARGET))
        self.assertFalse(cg.Guard._match_unconsumed_owner(receipt, "other-worker"))

    def test_match_owner_falls_back_without_to(self):
        receipt = make_receipt(work_slot=f"{TARGET}（claude 工位，保留至 21:00）")
        self.assertNotIn("to", receipt)
        self.assertTrue(cg.Guard._match_unconsumed_owner(receipt, TARGET))
        self.assertFalse(cg.Guard._match_unconsumed_owner(receipt, "other-worker"))

    def test_match_owner_excludes_sender_self(self):
        receipt = make_receipt(from_=TARGET, to=TARGET)
        self.assertFalse(cg.Guard._match_unconsumed_owner(receipt, TARGET))


# —— B2：guard 未消费唤醒桶 ——

class UnconsumedWakeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "proj"
        self.crew = self.root / ".git" / "crew"
        self.inbox = self.crew / "inbox"
        self.inbox.mkdir(parents=True)
        self.now = [1_000_000.0]
        self.logs: list[str] = []
        self.prompt_calls: list[tuple[str, str]] = []
        self.notify_calls: list[tuple[str, str]] = []

    def make_guard(self, **overrides) -> cg.Guard:
        kwargs = dict(
            root=self.root,
            crew_dir=self.crew,
            helm="crew-helm-live",
            clock=lambda: self.now[0],
            list_agents=lambda: [],
            list_panes=lambda: [{"pane_id": "wQ:p1", "focused": False}],
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or True),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True),
            stuck_detect=lambda agents, now: [],
            log_fn=self.logs.append,
        )
        kwargs.update(overrides)
        return cg.Guard(**kwargs)

    def agent(self, name: str, status: str = "idle") -> dict:
        # cwd 用非 /tmp 路径：is_probe_agent 按 /tmp cwd 判探针（真机工位不在
        # /tmp；夹具 root 在 tempfile 下会被误判，故虚构路径）
        return {"name": name, "agent_status": status, "pane_id": "wQ:p1",
                "cwd": "/home/fixture/proj"}

    def write_receipt(self, rid: str, *, to: str = "", work_slot: str = "",
                      sender: str = "patch-2232-firstmate") -> None:
        receipt = {
            "receipt_id": rid, "from": sender, "runtime": "claude",
            "task": "patch-2232/03-C", "status": "done", "commit": "",
            "changed_files": [], "evidence": [], "work_slot": work_slot,
            "findings": [], "summary": "测试回执", "ts": "2026-11-14T00:00:00Z",
        }
        if to:
            receipt["to"] = to
        (self.inbox / f"{rid}.json").write_text(
            json.dumps(receipt, ensure_ascii=False), encoding="utf-8")

    def write_refused(self, target: str, ts: str = "2026-11-14T10:00:00Z") -> None:
        row = {"ts": ts, "action": "prompt", "agent": target, "result": "refused",
               "delivery": "none", "task": "patch-2232/03-C"}
        with open(self.crew / "agent-log.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    def settle_idle(self, guard: cg.Guard, agents: list[dict], rounds: int = 3):
        """推进轮次使工位 idle 时长 ≥300s（每轮 +120s）。"""
        for _ in range(rounds):
            guard._track_agent_idle(agents, self.now[0])
            self.now[0] += 120
        guard._track_agent_idle(agents, self.now[0])

    def test_unread_receipt_wakes_idle_worker_with_fixed_template(self):
        self.write_receipt("r-b2-1", to="proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual(1, len(self.prompt_calls))
        name, message = self.prompt_calls[0]
        self.assertEqual("proj-worker-1", name)
        self.assertIn("[guard 未消费唤醒]", message)      # 固定模板
        self.assertIn("r-b2-1", message)                  # 清单拼接
        self.assertIn("crew-msg.py ls --unread", message)  # 消费指引

    def test_refused_prompt_wakes_target_when_idle(self):
        self.write_refused("proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual(1, len(self.prompt_calls))
        self.assertIn("被拒派单", self.prompt_calls[0][1])

    def test_focused_worker_deferred_not_injected(self):
        self.write_receipt("r-b2-2", to="proj-worker-1")
        guard = self.make_guard(
            list_panes=lambda: [{"pane_id": "wQ:p1", "focused": True}])
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("延后" in line for line in self.logs), self.logs)

    def test_same_receipt_wakes_only_once(self):
        self.write_receipt("r-b2-3", to="proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.now[0] += 600
        guard._scan_unconsumed_wake(agents, self.now[0])  # 第二轮同条目不再叫
        self.assertEqual(1, len(self.prompt_calls))

    def test_state_survives_guard_restart(self):
        self.write_receipt("r-b2-4", to="proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual(1, len(self.prompt_calls))
        # 重启：新实例从 guard.state.json 恢复，已叫条目不重叫（挂起不丢）
        guard2 = self.make_guard()
        guard2._restore_unconsumed()
        guard2._track_agent_idle(agents, self.now[0])
        self.now[0] += 600
        guard2._track_agent_idle(agents, self.now[0])
        guard2._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual(1, len(self.prompt_calls))

    def test_unmatched_receipt_skipped_and_logged(self):
        self.write_receipt("r-b2-5")  # 无 to、work_slot 不含任何工位名
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual([], self.prompt_calls)
        self.assertTrue(any("无在册归属" in line for line in self.logs), self.logs)

    def test_working_worker_not_woken(self):
        self.write_receipt("r-b2-6", to="proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1", status="working")]
        guard._track_agent_idle(agents, self.now[0])
        self.now[0] += 600
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual([], self.prompt_calls)

    def test_helm_role_and_prefix_excluded_firstmate_wakeable(self):
        (self.crew / "dispatch-agents.json").write_text(json.dumps({
            "crew-helm-drift": {"role": "helm"},
            "proj-firstmate": {"role": "firstmate"},
        }), encoding="utf-8")
        self.write_receipt("r-b2-7", to="crew-helm-drift")
        self.write_receipt("r-b2-8", to="helm-legacy-name")   # 前缀兜底
        self.write_receipt("r-b2-9", to="proj-firstmate")     # firstmate 可叫
        guard = self.make_guard()
        agents = [self.agent("crew-helm-drift"), self.agent("helm-legacy-name"),
                  self.agent("proj-firstmate")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual([("proj-firstmate", self.prompt_calls[0][1])],
                         self.prompt_calls)

    def test_queue_max_bounds_candidates(self):
        for i in range(cg.UNCONSUMED_QUEUE_MAX + 5):
            self.write_receipt(f"r-b2-q{i}", to="proj-worker-1")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual(1, len(self.prompt_calls))
        # 清单行数受队列上限约束
        self.assertEqual(cg.UNCONSUMED_QUEUE_MAX,
                         self.prompt_calls[0][1].count("- 回执 ")
                         + self.prompt_calls[0][1].count("- 被拒派单"))

    def test_read_cursor_excludes_consumed_receipts(self):
        self.write_receipt("r-b2-c1", to="proj-worker-1")
        (self.crew / "inbox-read-cursor.json").write_text(json.dumps(
            {"read": {"r-b2-c1": "2026-11-14T00:00:00Z"}}), encoding="utf-8")
        guard = self.make_guard()
        agents = [self.agent("proj-worker-1")]
        self.settle_idle(guard, agents)
        guard._scan_unconsumed_wake(agents, self.now[0])
        self.assertEqual([], self.prompt_calls)  # 已标读不叫


if __name__ == "__main__":
    unittest.main(verbosity=2)
