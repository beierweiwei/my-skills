#!/usr/bin/env python3
"""2.2.31 工单 06（FB-061）：close 可归因 + 活跃流保护 + firstmate 硬规则。

dispatch pane|tab close 记 caller（--caller > CREW_CALLER > dispatch）；pane-audit
归属未完成流 tab 只报告不自动关闭（flow-active-tab）；三栈 firstmate 模板硬规则
grep 命中。/tmp fixture + fake herdr，禁触真实 herdr。
（python3 scripts/tests/test_231_close_attrib.py）
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
_SPEC_PATH = _SCRIPTS / "pane-audit.py"
_spec = importlib.util.spec_from_file_location("pane_audit", _SPEC_PATH)
pa = importlib.util.module_from_spec(_spec)
sys.modules["pane_audit"] = pa
_spec.loader.exec_module(pa)

DISPATCH = _SCRIPTS / "dispatch.py"
REPO_ROOT = _SCRIPTS.parent


def snapshot(tabs: list[dict], panes: list[dict], agents: list[dict]) -> dict:
    return {"tabs": tabs, "panes": panes, "agents": agents}


def tab(tab_id: str, label: str) -> dict:
    return {"tab_id": tab_id, "label": label}


def pane(pane_id: str, tab_id: str, status: str | None = None) -> dict:
    data: dict = {"pane_id": pane_id, "tab_id": tab_id}
    if status is not None:
        data["agent_status"] = status
    return data


def agent(name: str, pane_id: str, tab_id: str = "t1",
          status: str = "working") -> dict:
    return {"name": name, "pane_id": pane_id, "tab_id": tab_id,
            "agent_status": status}


def kinds(findings: list) -> list[tuple[str, str]]:
    return [(finding.kind, finding.target) for finding in findings]

FAKE_HERDR = """#!/bin/sh
exit 0
"""


def write_flow_plan(root: Path, flow: str, statuses: list[str]) -> None:
    plan = root / ".planning" / flow / "task_plan.md"
    plan.parent.mkdir(parents=True, exist_ok=True)
    body = ["# Task Plan: x", "", "## Phases", ""]
    for i, status in enumerate(statuses, 1):
        body += [f"### Phase {i}: p{i}", "- [ ] t", f"- **Status:** {status}", ""]
    plan.write_text("\n".join(body), encoding="utf-8")


class CallerAttributionTest(unittest.TestCase):
    """验收 1：close 归因——三 caller 值、缺省 dispatch、env 推断、--help。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir(parents=True, exist_ok=True)
        (self.bin / "herdr").write_text(FAKE_HERDR, encoding="utf-8")
        (self.bin / "herdr").chmod(0o755)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)

    def run_close(self, *args: str, env_caller: str | None = None) -> int:
        env = dict(os.environ)
        env["PATH"] = str(self.bin) + os.pathsep + env.get("PATH", "")
        env.pop("CREW_CALLER", None)
        if env_caller is not None:
            env["CREW_CALLER"] = env_caller
        return subprocess.run(
            [sys.executable, str(DISPATCH), "--root", str(self.root), *args],
            capture_output=True, text=True, env=env,
        ).returncode

    def log_rows(self) -> list[dict]:
        path = self.root / ".git" / "crew" / "agent-log.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_explicit_caller_recorded_pane_and_tab(self):
        self.assertEqual(0, self.run_close(
            "pane", "close", "--pane", "w1:pA", "--caller", "pane-audit"))
        self.assertEqual(0, self.run_close(
            "tab", "close", "--tab", "w1:tA", "--caller", "crew-reaper"))
        rows = self.log_rows()
        pane_row = next(r for r in rows if r["op"] == "close" and r["action"] == "pane")
        tab_row = next(r for r in rows if r["op"] == "close" and r["action"] == "tab")
        self.assertEqual("pane-audit", pane_row["caller"])
        self.assertEqual("flag", pane_row["callerSource"])
        self.assertEqual("crew-reaper", tab_row["caller"])

    def test_guard_caller_value_passthrough(self):
        self.assertEqual(0, self.run_close(
            "pane", "close", "--pane", "w1:pB", "--caller", "guard"))
        row = next(r for r in self.log_rows() if r["action"] == "pane")
        self.assertEqual("guard", row["caller"])

    def test_no_caller_falls_back_to_dispatch(self):
        self.assertEqual(0, self.run_close("tab", "close", "--tab", "w1:tB"))
        row = next(r for r in self.log_rows() if r["action"] == "tab")
        self.assertEqual("dispatch", row["caller"])
        self.assertEqual("default", row["callerSource"])

    def test_env_caller_inferred(self):
        self.assertEqual(0, self.run_close(
            "pane", "close", "--pane", "w1:pC", env_caller="guard"))
        row = next(r for r in self.log_rows() if r["action"] == "pane")
        self.assertEqual("guard", row["caller"])
        self.assertEqual("env", row["callerSource"])

    def test_caller_flag_in_help(self):
        out = subprocess.run(
            [sys.executable, str(DISPATCH), "pane", "close", "--help"],
            capture_output=True, text=True,
        )
        self.assertIn("--caller", out.stdout + out.stderr)
        out2 = subprocess.run(
            [sys.executable, str(DISPATCH), "tab", "close", "--help"],
            capture_output=True, text=True,
        )
        self.assertIn("--caller", out2.stdout + out2.stderr)


class FlowActiveProtectionTest(unittest.TestCase):
    """验收 2：归属未完成流 tab 只报告不自动关闭；完成流照旧；无主空 tab 仍可关。"""

    def test_active_flow_tab_reported_not_closable(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        write_flow_plan(root, "guard-noise", ["complete", "in_progress"])
        incomplete = pa.load_flow_incomplete(root)
        self.assertEqual({"guard-noise": 1}, incomplete)
        data = snapshot(
            tabs=[tab("t1", "guard-noise-builder")],
            panes=[],
            agents=[],
        )
        findings = pa.audit(data, incomplete=incomplete)
        active = [f for f in findings if f.kind == "flow-active-tab"]
        self.assertEqual(1, len(active), "活跃流空 tab 应报 flow-active-tab")
        self.assertIsNone(active[0].action, "只报告不关闭")
        self.assertIn("guard-noise", active[0].detail)
        self.assertIn("未完成 1 Phase", active[0].detail)
        # 无 incomplete 输入时维持旧口径（empty-tab 可关）
        legacy = pa.audit(data)
        self.assertEqual([("empty-tab", "t1")], kinds(legacy))

    def test_missing_or_illegal_status_counts_pending(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        plan = root / ".planning" / "flow-x" / "task_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(
            "# T\n\n## Phases\n\n### Phase 1: a\n- [ ] t\n\n"
            "### Phase 2: b\n- [ ] t\n- **Status:** in_progress\n",
            encoding="utf-8")
        self.assertEqual({"flow-x": 2}, pa.load_flow_incomplete(root),
                         "Status 缺失按 pending 计入")

    def test_complete_flow_tab_still_reclaimable(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        write_flow_plan(root, "flow-done", ["complete"])
        data = snapshot(
            tabs=[tab("t2", "flow-done-builder")],
            panes=[pane("p1", "t2", status="idle")],
            agents=[agent("flow-done-builder", "p1", "t2", status="idle")],
        )
        findings = pa.audit(data,
                            flows=pa.load_flow_plans(root),
                            incomplete=pa.load_flow_incomplete(root),
                            empty_probe=lambda _: True)
        self.assertEqual([("flow-complete-tab", "t2")], kinds(findings),
                         "完成流 idle tab 照旧回收，不被活跃流保护阻断")


class FirstmateTemplateRuleTest(unittest.TestCase):
    """验收 3：三栈 firstmate 模板硬规则 grep 命中。"""

    RULE = "自处置工位（关闭 pane/tab）前必须确认本流全部 Phase complete；否则保留并注明"

    def test_rule_in_three_stacks(self):
        for stack in ("qoder", "claude", "opencode"):
            path = REPO_ROOT / "templates" / "agents" / stack / "firstmate.md"
            text = path.read_text(encoding="utf-8")
            self.assertIn(self.RULE, text, f"{stack} 模板缺硬规则")


if __name__ == "__main__":
    unittest.main(verbosity=2)
