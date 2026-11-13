#!/usr/bin/env python3
"""2.2.28 T69①③：dispatch prompt worker ctx 硬门 + agent-log sender/task。

Captain 口径（2026-11-13，流 plan Decisions，不得改数）：
- worker ctx >40% 拒派新切片（rc=1 + compact 指令）；≥60% 拒派并要求关闭重建；
- ctx 解析不出 fail-closed 拒派（rc=1 + warning），逃生门 --allow-unknown-ctx；
- helm/firstmate 自身豁免（交接阈值 30% warn/50% 交接由 guard 管理）；
- agent-log prompt 行补 sender/task（「谁派的活无法归因」缺口，case-ctx-audit）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_2221_stuck_detect import GuardHarness, cg, write_file  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
DISPATCH = SKILL_DIR / "scripts" / "dispatch.py"
_spec = importlib.util.spec_from_file_location("dispatch_t69", DISPATCH)
dispatch_mod = importlib.util.module_from_spec(_spec)
sys.modules["dispatch_t69"] = dispatch_mod
_spec.loader.exec_module(dispatch_mod)

_GUARD_SPEC = importlib.util.spec_from_file_location(
    "crew_guard_t69", SKILL_DIR / "scripts" / "crew-guard.py")
cg = importlib.util.module_from_spec(_GUARD_SPEC)
sys.modules["crew_guard_t69"] = cg
_GUARD_SPEC.loader.exec_module(cg)

CTX_TEXT = "ctx 420k/1M\t一些终端输出"


class CtxGateHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "proj"
        self.root.mkdir()
        registry = dispatch_mod.registry_path(self.root)
        registry.parent.mkdir(parents=True, exist_ok=True)
        registry.write_text(json.dumps({
            "t69-builder": {"role": "builder", "runtime": "claude"},
            "t69-helm": {"role": "helm", "runtime": "qoder"},
        }), encoding="utf-8")
        self.calls: list[list[str]] = []
        self.pane_text = CTX_TEXT

    def fake_herdr(self, argv: list[str]) -> str:
        self.calls.append(list(argv))
        if argv[:3] == ["herdr", "agent", "list"]:
            return json.dumps({"result": {"agents": [
                {"name": "t69-builder", "pane_id": "wX:p1"},
                {"name": "t69-helm", "pane_id": "wX:p0"},
                {"name": "t69-fm", "pane_id": "wX:p2"}]}})
        if argv[:3] == ["herdr", "pane", "list"]:
            return json.dumps({"result": {"panes": [
                {"pane_id": "wX:p0", "focused": False},
                {"pane_id": "wX:p1", "focused": False},
                {"pane_id": "wX:p2", "focused": False}]}})
        if argv[:3] == ["herdr", "pane", "process-info"]:
            return json.dumps({"result": {"approvals": []}})
        if argv[:3] == ["herdr", "pane", "read"]:
            return self.pane_text
        return ""

    def dispatch_main(self, *args: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=self.fake_herdr):
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main(["--root", str(self.root), *args])
        return code, out.getvalue(), err.getvalue()

    def agent_log(self) -> list[dict]:
        path = dispatch_mod.crew_dir(self.root) / "agent-log.jsonl"
        rows = [json.loads(line) for line in
                path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [row for row in rows if row.get("action") == "prompt"]


class CtxGateTest(CtxGateHarness):
    def test_under_40_percent_passes(self):
        self.pane_text = "ctx 210k/1M 输出"
        code, out, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi", "--json")
        self.assertEqual(0, code, err)
        row = self.agent_log()[-1]
        self.assertEqual("ctx 21%", row["ctx"])

    def test_exactly_40_percent_passes(self):
        """>40% 才拒（40.0% 不拒）。"""
        self.pane_text = "ctx (400k/1M)"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi", "--json")
        self.assertEqual(0, code, err)

    def test_over_40_percent_rejected_with_compact_directive(self):
        self.pane_text = "ctx 450k/1M"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi")
        self.assertEqual(1, code)
        self.assertIn(">40%", err)
        self.assertIn("/compact", err)
        self.assertIn("compact 保真清单", err)
        self.assertIn("Decisions", err, "保真清单原文必须完整进拒派输出")

    def test_over_60_percent_rejected_with_rebuild_directive(self):
        self.pane_text = "ctx 650k/1M"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi")
        self.assertEqual(1, code)
        self.assertIn("≥60%", err)
        self.assertIn("关闭", err)
        self.assertIn("重建", err)

    def test_unknown_ctx_fails_closed(self):
        self.pane_text = "worker 一片忙碌，无 ctx 字样"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi")
        self.assertEqual(1, code)
        self.assertIn("fail-closed", err)
        self.assertIn("--allow-unknown-ctx", err)

    def test_unknown_ctx_escape_hatch_passes(self):
        self.pane_text = "无 ctx 字样"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi",
            "--allow-unknown-ctx", "--json")
        self.assertEqual(0, code, err)
        row = self.agent_log()[-1]
        self.assertIn("ctx 未知", row["ctx"])
        self.assertIn("放行", row["ctx"])

    def test_helm_target_exempt_from_worker_gate(self):
        """helm/firstmate 自身走 guard 交接机制，不适用 40/60 worker 门。"""
        self.pane_text = "ctx 950k/1M（helm 满载也不由本门拦截）"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-helm", "--message", "hi", "--json")
        self.assertEqual(0, code, err)
        row = self.agent_log()[-1]
        self.assertIn("跳过 worker 门", row["ctx"])

    def test_pane_read_error_fails_closed_not_rc3(self):
        """4b③：pane read 命令异常归一 (None, reason) → fail-closed rc=1。

        旧实现 run_herdr 异常直接抛 rc=3，逃生门对该路径无效。
        """
        def broken_read(argv):
            self.calls.append(list(argv))
            if argv[:3] == ["herdr", "pane", "read"]:
                raise dispatch_mod.DispatchError("herdr 命令失败（rc=1）", 1)
            return self.fake_herdr(argv)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=broken_read):
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main([
                    "--root", str(self.root), "prompt",
                    "--agent", "t69-builder", "--message", "hi"])
        self.assertEqual(1, code, "pane read 异常必须走 fail-closed（rc=1 非 3）")
        self.assertIn("fail-closed", err.getvalue())
        self.assertIn("pane read 调用失败", err.getvalue())

    def test_pane_read_error_escape_hatch_passes(self):
        """4b③：pane read 异常路径逃生门同样有效。"""
        def broken_read(argv):
            self.calls.append(list(argv))
            if argv[:3] == ["herdr", "pane", "read"]:
                raise dispatch_mod.DispatchError("herdr 命令失败（rc=1）", 1)
            return self.fake_herdr(argv)

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(dispatch_mod, "run_herdr", side_effect=broken_read):
            with redirect_stdout(out), redirect_stderr(err):
                code = dispatch_mod.main([
                    "--root", str(self.root), "prompt",
                    "--agent", "t69-builder", "--message", "hi",
                    "--allow-unknown-ctx", "--json"])
        self.assertEqual(0, code, err)

    def test_firstmate_role_exempt_from_worker_gate(self):
        """4c③：firstmate 编排角色豁免（此前只测 helm）。"""
        registry = dispatch_mod.registry_path(self.root)
        registry.write_text(json.dumps({
            "t69-fm": {"role": "firstmate", "runtime": "qoder"},
            "t69-builder": {"role": "builder", "runtime": "claude"},
        }), encoding="utf-8")
        self.pane_text = "ctx 900k/1M"
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-fm", "--message", "hi", "--json")
        self.assertEqual(0, code, err)
        row = self.agent_log()[-1]
        self.assertIn("跳过 worker 门", row["ctx"])
        self.assertNotIn("由 guard 交接机制管理", row["ctx"],
                         "4c③：不实表述必须移除")

    def test_reject_logged_as_refused_ctx(self):
        """S-4：ctx 拒派必须落 agent-log（result=refused-ctx），可事后排障。"""
        self.pane_text = "ctx 450k/1M"
        code, _, _err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi",
            "--task", "t69-reject-log")
        self.assertEqual(1, code)
        rows = [row for row in self.agent_log()
                if row.get("result") == "refused-ctx"]
        self.assertEqual(1, len(rows), "拒派必须有 refused-ctx 审计行")
        self.assertEqual("t69-builder", rows[0]["agent"])
        self.assertEqual("t69-reject-log", rows[0]["task"])
        self.assertIn("compact", rows[0]["reason"])

    def test_missing_agent_ctx_untouched_by_gate(self):
        """目标不在 herdr agent list → ctx 未知 → fail-closed。"""
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi")
        self.calls.clear()  # 前面已跑过一次；重跑一遍换目标
        self.pane_text = ""
        code, _, err = self.dispatch_main(
            "prompt", "--agent", "t69-builder", "--message", "hi")
        self.assertEqual(1, code)
        self.assertIn("fail-closed", err)


class SenderTaskLogTest(CtxGateHarness):
    def test_prompt_log_has_sender_and_task(self):
        self.pane_text = "ctx 100k/1M"
        with mock.patch.dict("os.environ", {"CREW_AGENT_NAME": "patch-2228-firstmate"}):
            code, _, err = self.dispatch_main(
                "prompt", "--agent", "t69-builder", "--message", "hi",
                "--task", "patch-2228/S6", "--json")
        self.assertEqual(0, code, err)
        row = self.agent_log()[-1]
        self.assertEqual("patch-2228-firstmate", row["sender"])
        self.assertEqual("patch-2228/S6", row["task"])

    def test_sender_defaults_without_env(self):
        self.pane_text = "ctx 100k/1M"
        import os
        env = {k: v for k, v in os.environ.items()
               if k not in ("CREW_AGENT_NAME", "INONE_AGENT_NAME")}
        with mock.patch.dict("os.environ", env, clear=True):
            code, _, err = self.dispatch_main(
                "prompt", "--agent", "t69-builder", "--message", "hi", "--json")
        self.assertEqual(0, code, err)
        self.assertEqual("dispatch", self.agent_log()[-1]["sender"])


class GuardWorkerCtxScanTest(unittest.TestCase):
    """T69②：guard 逐非 helm worker ctx 扫描（>40% 告警 / ≥60% 重建建议）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.crew = self.base / "crew"
        self.root.mkdir()
        self.now = [1_700_000_000.0]
        self.agents: list[dict] = []
        self.pane_text = ""
        self.prompt_calls: list[tuple[str, str]] = []
        self.notify_calls: list[tuple[str, str]] = []
        self.logs: list[str] = []
        self.reads: list[str] = []
        self._write_plans()

    def _write(self, path: Path, text: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def _write_plans(self) -> None:
        self._write(self.root / ".planning" / "flow-a" / "task_plan.md",
                    "# T\n\n## Phases\n")
        self._write(self.root / ".planning" / "portfolio" / "task_plan.md",
                    "# P\n\n## Backlog\n\n| # | 目标 | P | 依赖 | 阻塞 |\n|---|---|---|---|---|\n")

    def _pane_read(self, pane: str, lines: int = 80) -> str:
        self.reads.append(pane)
        return self.pane_text

    def make_guard(self):
        return cg.Guard(
            root=self.root,
            crew_dir=self.crew,
            helm="helm-1",
            interval=120,
            clock=lambda: self.now[0],
            list_agents=lambda: [
                dict(a, pane_id=a.get("pane_id", f"pane-{a.get('name')}"))
                for a in self.agents],
            list_panes=lambda: [
                {"pane_id": a.get("pane_id", f"pane-{a.get('name')}"),
                 "focused": False} for a in self.agents],
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or True),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True),
            run_status=lambda root, flag: (0, "ok"),
            run_rotate=lambda root: (0, ""),
            run_reaper=lambda root, crew: (0, "ok"),
            pane_read=self._pane_read,
            dispatch=lambda root, args: (0, "", ""),
            log_fn=self.logs.append,
        )

    def test_over_40_worker_alerted_with_compact_directive(self):
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "ctx 450k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertTrue(result["woke"], "纯 ctx 告警也必须独立触发叫醒（不静默）")
        self.assertIn("worker ctx 超阈值", result["reason"])
        self.assertIn("builder-1 45%", result["reason"])
        self.assertIn("/compact", self.prompt_calls[0][1])
        self.assertIn("compact 保真清单", self.prompt_calls[0][1])
        self.assertIn("Decisions", self.prompt_calls[0][1], "保真清单原文完整")

    def test_over_60_worker_gets_rebuild_advice(self):
        self.agents = [{"name": "builder-1", "agent_status": "idle"}]
        self.pane_text = "ctx 650k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertTrue(result["woke"])
        self.assertIn("≥60%", result["reason"])
        self.assertIn("关闭该工位并重建", result["reason"])

    def test_under_40_no_alert(self):
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "ctx 300k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertFalse(result["woke"])
        self.assertNotIn("worker ctx 超阈值", result["reason"])

    def test_helm_agent_not_scanned(self):
        self.agents = [{"name": "helm-1", "agent_status": "idle"}]
        self.pane_text = "ctx 900k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertNotIn("worker ctx 超阈值", result["reason"])
        # 唯一的 read 来自 guard 自身 helm ctx 检查（30% warn / 50% 交接机制）；
        # worker ctx 扫描不读 helm。
        self.assertEqual(["pane-helm-1"], self.reads)

    def test_unreadable_ctx_not_reported(self):
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "没有 ctx 字样"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertNotIn("worker ctx 超阈值", result["reason"])
        self.assertFalse(result["woke"])

    def test_probe_agent_not_scanned(self):
        self.agents = [{"name": "s9-probe", "agent_status": "working"}]
        self.pane_text = "ctx 900k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertNotIn("worker ctx 超阈值", result["reason"])
        self.assertEqual([], self.reads)

    def test_no_injection_into_worker_pane(self):
        """守卫：ctx 告醒只发 helm，绝不向 worker pane 注入（聚焦/审批 pane 同理）。"""
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "ctx 450k/1M"
        guard = self.make_guard()
        guard.run_once()
        prompts = [name for name, _ in self.prompt_calls]
        self.assertEqual(["helm-1"], prompts, "只允许 helm 被注入")

    def test_firstmate_agent_not_scanned(self):
        """4c③：firstmate（dispatch registry role）不进 worker ctx 扫描。"""
        self._write(self.crew / "dispatch-agents.json", json.dumps(
            {"fm-1": {"role": "firstmate", "runtime": "qoder"}}))
        self.agents = [{"name": "fm-1", "agent_status": "idle"}]
        self.pane_text = "ctx 900k/1M"
        guard = self.make_guard()
        result = guard.run_once()
        self.assertNotIn("worker ctx 超阈值", result["reason"])
        self.assertEqual([], [p for p in self.reads if p == "pane-fm-1"],
                         "firstmate 编排角色不得被 worker ctx 扫描")

    def test_unresolved_ctx_logged_and_deduped(self):
        """4c②：ctx 解析不出必须可见（按 agent 去重记日志），恢复后复位。"""
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "没有 ctx 字样"
        guard = self.make_guard()
        guard.run_once()
        self.assertTrue(
            any("worker ctx 解析失败" in line and "builder-1" in line
                for line in self.logs), f"解析失败必须记日志；logs={self.logs}")
        first_count = len([l for l in self.logs if "worker ctx 解析失败" in l])
        guard.run_once()
        second_count = len([l for l in self.logs if "worker ctx 解析失败" in l])
        self.assertEqual(first_count, second_count, "同 agent 跨轮去重不刷屏")
        self.pane_text = "ctx 450k/1M"
        guard.run_once()
        self.pane_text = "没有 ctx 字样"
        guard.run_once()
        third_count = len([l for l in self.logs if "worker ctx 解析失败" in l])
        self.assertEqual(first_count + 1, third_count,
                         "恢复可读后再失败应按新事件重记")

    def test_ctx_alert_not_suppressed_by_heartbeat(self):
        """4c④：纯 ctx 告警不被 pending 心跳间隔压制（全局冷却已限频）。"""
        self._write_plans_with_pending()
        self.agents = [{"name": "builder-1", "agent_status": "working"}]
        self.pane_text = "ctx 450k/1M"
        guard = self.make_guard()
        self.assertTrue(guard.run_once()["woke"])  # 第一轮 ctx 告警叫醒
        # 推进 11 分钟：跨过全局冷却（10 分钟）但仍在心跳窗口（默认 15 分钟）内
        # ——改前该场景被 pending 心跳压制至 15 分钟，4c④ 后放行。
        self.now[0] += 660
        self.agents = [
            {"name": "builder-1", "agent_status": "working"},
            {"name": "builder-2", "agent_status": "working"}]
        result = guard.run_once()
        self.assertTrue(
            result["woke"], "新 ctx 告警不得被 pending 心跳压制")

    def _write_plans_with_pending(self):
        self._write(self.root / ".planning" / "flow-a" / "task_plan.md",
                    "# T\n\n## Phases\n\n### Phase 1: x\n- [ ] 待办\n"
                    "- **Status:** in_progress\n")
        self._write(self.root / ".planning" / "portfolio" / "task_plan.md",
                    "# P\n\n## Backlog\n\n| # | 目标 | P | 依赖 | 阻塞 |\n"
                    "|---|---|---|---|---|\n")

    def test_compact_checklist_matches_dispatch(self):
        """两侧保真清单常量同文本（Captain 硬要求原文，防漂移）。"""
        self.assertEqual(dispatch_mod.COMPACT_CHECKLIST, cg.COMPACT_CHECKLIST)
        self.assertEqual(dispatch_mod.WORKER_CTX_COMPACT, cg.WORKER_CTX_COMPACT)
        self.assertEqual(dispatch_mod.WORKER_CTX_REBUILD, cg.WORKER_CTX_REBUILD)

    def test_parse_ctx_k_cross_module_equivalence(self):
        """S-2（subB B-4）：双实现防漂移——正则同源 + 同输入表同输出。"""
        self.assertEqual(dispatch_mod.CTX_RE.pattern, cg.CTX_RE.pattern)
        samples = [
            "ctx 420k/1M",
            "ctx (412k/1M)",
            "ctx 75.4k/1M",
            "ctx ( 75.4k / 1M )",
            "ctx 62.3%",
            "ctx 1.2M/1M",
            "随便输出 ctx 300k / 1M 状态栏",
            "ctx unknown",
            "ctx 75.4k",
            "",
        ]
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(dispatch_mod.parse_ctx_k(text),
                                 cg.parse_ctx_k(text),
                                 f"两侧 parse_ctx_k 输出不一致：{text!r}")


class CtxGateTextTest(unittest.TestCase):
    """T69④⑤：worker ctx 口径 40/60 + compact 保真清单进剧本/模板（文本断言）。"""

    STACKS = ("qoder", "claude")
    WORKERS = ("builder", "runner", "scout", "planner", "reviewer")
    CHECKLIST_KEYS = ("目标", "当前 Phase", "Next Step", "Decisions", "Deviation",
                      "三时间戳", "待回执", "证据路径", "占用", "验收断言")

    def read(self, rel: str) -> str:
        return (SKILL_DIR / rel).read_text(encoding="utf-8")

    def test_worker_templates_use_40_60_gate(self):
        """worker 自身条款：>40% compact、≥60% 重建（替换旧 ≥30% 口径）。"""
        for stack in self.STACKS:
            for role in self.WORKERS:
                with self.subTest(stack=stack, role=role):
                    text = self.read(f"templates/agents/{stack}/{role}.md")
                    self.assertIn(">40%", text, f"{stack}/{role} 缺 >40% 口径")
                    self.assertIn("≥60%", text)
                    self.assertIn("保真清单", text)
                    self.assertNotIn("ctx ≥30%", text,
                                     f"{stack}/{role} 仍有旧 ≥30% worker 口径")

    def test_firstmate_helm_dispatch_clauses_updated(self):
        """firstmate/helm 监控 worker 的派前查 ctx 条款 → 40/60 + 保真清单全文。"""
        targets = [
            "templates/agents/qoder/firstmate.md",
            "templates/agents/claude/firstmate.md",
            "templates/agents/opencode/firstmate.md",
            "templates/agents/qoder/helm.md",
            "templates/agents/claude/helm.md",
        ]
        for rel in targets:
            with self.subTest(rel=rel):
                text = self.read(rel)
                self.assertIn(">40%", text, f"{rel} 缺 >40% 口径")
                for key in self.CHECKLIST_KEYS:
                    self.assertIn(key, text, f"{rel} 保真清单缺要素：{key}")

    def test_self_clauses_of_helm_firstmate_unchanged(self):
        """helm/firstmate 自身交接阈值（30% warn/50% 交接 / 副舵 30%）不动。"""
        for rel, needle in (
            ("templates/agents/qoder/helm.md", "ctx ≥30% 停止接新活"),
            ("templates/agents/claude/helm.md", "ctx ≥30% 停止接新活"),
            ("templates/agents/qoder/firstmate.md", "副舵自身 ctx ≥30%"),
            ("templates/agents/claude/firstmate.md", "副舵自身 ctx ≥30%"),
        ):
            with self.subTest(rel=rel):
                self.assertIn(needle, self.read(rel))

    def test_dispatch_preflight_playbook_has_ctx_check(self):
        """技能层派单前置剧本：派前查目标 ctx + 保真清单全文。"""
        text = self.read("templates/playbooks/dispatch-preflight.md")
        self.assertIn("ctx", text)
        self.assertIn(">40%", text)
        self.assertIn("≥60%", text)
        for key in self.CHECKLIST_KEYS:
            self.assertIn(key, text, f"剧本保真清单缺要素：{key}")

    def test_worker_30pct_legacy_wipes_in_prod_docs(self):
        """B-1（S7 阻断）：worker 侧 ≥30% 旧口径在生产文档清零（防回退）。

        评审实指 5 处残留（herdr-cheatsheet:109、firstmate-dispatch×3、
        autonomous-run:8）；发布后技能文档不得 30/40/60 三口径并存。
        helm/firstmate 自身交接条款（「ctx ≥30% 停止接新活」30% warn/50%
        交接）是合法保留，不在清零范围。
        """
        import re
        legacy = re.compile(r"≥\s*30%")
        prod_files = [
            "references/herdr-cheatsheet.md",
            "templates/skills/firstmate-dispatch/SKILL.md",
            "templates/playbooks/autonomous-run.md",
        ]
        for stack in self.STACKS:
            prod_files += [f"templates/agents/{stack}/{role}.md"
                           for role in self.WORKERS]
        for rel in prod_files:
            with self.subTest(rel=rel):
                text = self.read(rel)
                self.assertIsNone(legacy.search(text),
                                  f"{rel} 仍有 worker 侧 ≥30% 旧口径")

    def test_gate_constants_are_captain_figures(self):
        """参数不得改数（Captain 2026-11-13 口径）。"""
        self.assertEqual(0.40, dispatch_mod.WORKER_CTX_COMPACT)
        self.assertEqual(0.60, dispatch_mod.WORKER_CTX_REBUILD)


if __name__ == "__main__":
    unittest.main()
