#!/usr/bin/env python3
"""2.2.32 工单 D/T79：guard 继任 helm 接手窗口 ctx 观测（只观测不叫醒）。

断言：done(ok)+doneAt 窗内逐轮采样（最小字段 ts/helm/ctx_k/phase）+
guard.log 摘要；零 wake/零通知；窗口外不采；缺 doneAt 旧文件不采；
ctx 解析失败跳过；JSONL 损坏行容错（追加继续）；result=failed 不采。
fixture 全部为中性占位内容。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent

_spec = importlib.util.spec_from_file_location("crew_guard_t79", _SCRIPTS / "crew-guard.py")
cg = importlib.util.module_from_spec(_spec)
sys.modules["crew_guard_t79"] = cg
_spec.loader.exec_module(cg)


class CtxObserveTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.root = self.base / "proj"
        self.crew = self.root / ".git" / "crew"
        self.crew.mkdir(parents=True)
        self.now = [datetime.now(timezone.utc).timestamp()]
        self.logs: list[str] = []
        self.prompt_calls: list[tuple] = []
        self.notify_calls: list[tuple] = []
        self.pane_texts: dict[str, str] = {"pNEW": "ctx 42k/1M"}
        self.fake_now_iso = datetime.now(timezone.utc).isoformat()

    def make_guard(self, **overrides) -> cg.Guard:
        kwargs = dict(
            root=self.root, crew_dir=self.crew, helm="helm-old",
            clock=lambda: self.now[0],
            list_agents=lambda: [],
            list_panes=lambda: [],
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or True),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True),
            pane_read=lambda pane, lines: self.pane_texts.get(pane, ""),
            log_fn=self.logs.append,
        )
        kwargs.update(overrides)
        return cg.Guard(**kwargs)

    def write_handoff(self, *, result: str = "ok", with_done_at: bool = True,
                      done_minutes_ago: float = 0.0) -> None:
        done_at = (datetime.now(timezone.utc)
                   - timedelta(minutes=done_minutes_ago)).replace(
            microsecond=0).isoformat().replace("+00:00", "Z")
        lines = [f"phase=done", "old=helm-old", "new=helm-new", "newPane=pNEW",
                 "result=" + result]
        if with_done_at:
            lines.append(f"doneAt={done_at}")
        (self.crew / "guard.handoff").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def samples(self) -> list[dict]:
        path = self.crew / "guard.ctxobserve.jsonl"
        if not path.exists():
            return []
        out = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # 损坏行跳过（容错口径）
        return out

    def test_window_samples_recorded_without_wake(self):
        self.write_handoff(done_minutes_ago=2)
        guard = self.make_guard()
        agents = [{"name": "helm-new", "agent_status": "working",
                   "pane_id": "pNEW", "cwd": "/home/fixture/proj"}]
        for _ in range(3):
            guard._ctx_observe(agents, self.now[0])
            self.now[0] += 120
        rows = self.samples()
        self.assertEqual(3, len(rows))
        for row in rows:
            self.assertEqual({"ts", "helm", "ctx_k", "phase"}, set(row))
            self.assertEqual("helm-new", row["helm"])
            self.assertEqual(42.0, row["ctx_k"])
            self.assertEqual("done", row["phase"])
        self.assertEqual([], self.prompt_calls)   # 零 wake
        self.assertEqual([], self.notify_calls)   # 零通知
        self.assertTrue(any("继任 helm 接手 ctx=42k" in line for line in self.logs))

    def test_outside_window_no_sample(self):
        self.write_handoff(done_minutes_ago=20)  # 超出 15 分钟窗
        guard = self.make_guard()
        agents = [{"name": "helm-new", "pane_id": "pNEW"}]
        guard._ctx_observe(agents, self.now[0])
        self.assertEqual([], self.samples())

    def test_missing_done_at_skips(self):
        # 旧文件升级残留（无 doneAt）：不观测（避免用错窗口起点）
        self.write_handoff(with_done_at=False)
        guard = self.make_guard()
        agents = [{"name": "helm-new", "pane_id": "pNEW"}]
        guard._ctx_observe(agents, self.now[0])
        self.assertEqual([], self.samples())

    def test_failed_handoff_not_observed(self):
        self.write_handoff(result="failed")
        guard = self.make_guard()
        agents = [{"name": "helm-new", "pane_id": "pNEW"}]
        guard._ctx_observe(agents, self.now[0])
        self.assertEqual([], self.samples())

    def test_unparsable_ctx_skips_silently(self):
        self.write_handoff()
        self.pane_texts["pNEW"] = "（无 ctx 标记的 pane 文本）"
        guard = self.make_guard()
        agents = [{"name": "helm-new", "pane_id": "pNEW"}]
        guard._ctx_observe(agents, self.now[0])  # 不抛异常
        self.assertEqual([], self.samples())

    def test_corrupt_jsonl_tolerated(self):
        (self.crew / "guard.ctxobserve.jsonl").write_text(
            "{not-json}\n", encoding="utf-8")
        self.write_handoff()
        guard = self.make_guard()
        agents = [{"name": "helm-new", "pane_id": "pNEW"}]
        guard._ctx_observe(agents, self.now[0])  # 追加继续，不中断
        rows = self.samples()
        self.assertEqual(1, len(rows))

    def test_successor_missing_skips(self):
        self.write_handoff()
        guard = self.make_guard()
        guard._ctx_observe([], self.now[0])  # 继任不在册
        self.assertEqual([], self.samples())


if __name__ == "__main__":
    unittest.main(verbosity=2)
