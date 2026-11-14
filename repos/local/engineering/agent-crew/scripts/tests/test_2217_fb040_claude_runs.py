#!/usr/bin/env python3
"""2.2.17 FB-040：crew-claude 在跑注册 + guard 在跑口径合并 + S4 pid_alive。

覆盖：
- `crew-claude.py run` 启动 claude 前在 `<crew-dir>/runs/` 原子写注册（字段齐全、
  唯一文件名），正常/非零 rc/超时统一清理，注册失败只告警不阻断 run；
  `meta.json` 增 `runs_registry`（路径或 null）；
- `crew-guard.py` 每轮把 `runs/*.json` 存活 pid 计入在跑 worker（有待办也不叫醒），
  `--once --json` 输出 `claudeRuns`；死 pid/坏 JSON/缺 pid 忽略并记日志；
- S4：`release-check.py pid_alive` 对 PermissionError 判存活（与 preflight 一致）。
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass/注解解析需要模块已注册
    spec.loader.exec_module(module)
    return module


cc = _load("crew_claude_fb040", "crew-claude.py")
cg = _load("crew_guard_fb040", "crew-guard.py")
release = _load("release_check_fb040", "release-check.py")
preflight = _load("crew_preflight_fb040", "crew-preflight.py")

CLAUDE_STUB = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    import os, sys, time

    mode = os.environ.get("FB040_MODE", "ok")
    if mode == "sleep":
        time.sleep(float(os.environ.get("FB040_SLEEP", "2")))
    if mode == "fail":
        sys.stderr.write("boom-stub\\n")
        sys.exit(int(os.environ.get("FB040_RC", "3")))
    sys.stdout.write("STUB-REPORT\\n## 结论\\n"
                     "证据一：结论正文甲与补充说明，用于抬高字节数到阈值之上。\\n"
                     "证据二：结论正文乙与补充说明，用于抬高字节数到阈值之上。\\n"
                     "证据三：结论正文丙与补充说明，用于抬高字节数到阈值之上。\\n"
                     "## 来源\\n- 本地 stub 固定输出（无外部访问）。\\n")
    ''')

PLAN = """# Task Plan: FB040

## Phases

### Phase 1: 进行中
- owner: builder
- [ ] 待办A
- **Status:** in_progress
"""


class ClaudeRunsRegistrationTest(unittest.TestCase):
    """A：crew-claude 运行注册（真实子进程 + 假 claude）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.root.mkdir()
        self.out = self.base / "evidence"
        self.prompt = self.base / "prompt.md"
        self.prompt.write_text("只读任务：输出 pwd。\n", encoding="utf-8")
        self.config = self.root / ".planning/orchestration.json"
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(json.dumps({"agents": {
            "roles": {"adversary": {"backend": "claude", "model": "glm-5.3"}},
        }}, ensure_ascii=False), encoding="utf-8")
        self.stub = self.base / "claude-stub"
        self.stub.write_text(CLAUDE_STUB, encoding="utf-8")
        self.stub.chmod(0o755)
        self.runs = self.root / ".crew/runs"

    def start_run(self, *extra: str, env: dict | None = None) -> subprocess.Popen:
        argv = [
            sys.executable, str(_SCRIPTS / "crew-claude.py"), "run",
            "--role", "adversary",
            "--prompt", str(self.prompt),
            "--out-dir", str(self.out),
            "--name", "case",
            "--root", str(self.root),
            "--claude-bin", str(self.stub),
            *extra,
        ]
        merged = {key: value for key, value in os.environ.items()
                  if key not in ("ANTHROPIC_MODEL",)}
        merged.update(env or {})
        return subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=merged)

    def registry_files(self) -> list[Path]:
        return sorted(self.runs.glob("*.json")) if self.runs.is_dir() else []

    def wait_for_registry(self, proc: subprocess.Popen,
                          timeout: float = 10.0) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            files = self.registry_files()
            if files:
                return json.loads(files[0].read_text(encoding="utf-8"))
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        out, err = proc.communicate(timeout=10)
        self.fail(f"未等到 runs 注册：rc={proc.returncode} out={out} err={err}")

    def finish(self, proc: subprocess.Popen) -> tuple[int, str, str]:
        out, err = proc.communicate(timeout=30)
        return proc.returncode, out, err

    def meta(self) -> dict:
        return json.loads((self.out / "case.meta.json").read_text(encoding="utf-8"))

    # ---------- 运行期间注册 ----------

    def test_registry_exists_during_run_with_full_schema_then_cleaned(self):
        proc = self.start_run(env={"FB040_MODE": "sleep", "FB040_SLEEP": "2"})
        data = self.wait_for_registry(proc)
        self.assertEqual(proc.pid, data["pid"])
        self.assertEqual("adversary", data["role"])
        self.assertEqual("claude", data["backend"])
        self.assertEqual("glm-5.3", data["model"])
        self.assertTrue(data["started_at"].endswith("Z"), data["started_at"])
        self.assertEqual(str(self.out.resolve()), data["out_dir"])
        self.assertEqual(str(self.root.resolve()), data["root"])
        self.assertEqual("crew-claude.py run", data["command"])
        self.assertEqual(1, len(self.registry_files()))

        code, _, err = self.finish(proc)
        self.assertEqual(0, code, err)
        self.assertEqual([], self.registry_files(), "正常结束后注册必须清理")
        registered = self.meta()["runs_registry"]
        self.assertIsNotNone(registered)
        self.assertIn("runs", Path(registered).parts)
        self.assertTrue(Path(registered).name.startswith("claude-"))

    def test_two_concurrent_runs_use_distinct_registry_files(self):
        first = self.start_run(env={"FB040_MODE": "sleep", "FB040_SLEEP": "1.5"})
        self.wait_for_registry(first)
        second_out = self.base / "evidence2"
        proc = subprocess.Popen(
            [sys.executable, str(_SCRIPTS / "crew-claude.py"), "run",
             "--role", "adversary", "--prompt", str(self.prompt),
             "--out-dir", str(second_out), "--name", "case2",
             "--root", str(self.root), "--claude-bin", str(self.stub)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "FB040_MODE": "sleep", "FB040_SLEEP": "1.5"})
        deadline = time.time() + 10
        while time.time() < deadline and len(self.registry_files()) < 2:
            time.sleep(0.02)
        self.assertEqual(2, len(self.registry_files()), "并发 run 注册文件必须唯一")
        self.finish(first)
        self.finish(proc)
        self.assertEqual([], self.registry_files())

    # ---------- 失败 / 超时同样清理 ----------

    def test_nonzero_rc_cleans_registry(self):
        proc = self.start_run(env={"FB040_MODE": "fail", "FB040_RC": "3"})
        code, _, err = self.finish(proc)
        self.assertEqual(3, code, err)
        self.assertEqual([], self.registry_files())
        self.assertIsNotNone(self.meta()["runs_registry"])

    def test_timeout_cleans_registry(self):
        proc = self.start_run("--timeout", "1",
                              env={"FB040_MODE": "sleep", "FB040_SLEEP": "5"})
        code, _, err = self.finish(proc)
        self.assertEqual(124, code, err)
        self.assertEqual([], self.registry_files())
        self.assertTrue(self.meta()["timeout"])

    # ---------- 注册失败不阻断 ----------

    def test_registry_failure_warns_but_run_continues(self):
        (self.root / ".crew").write_text("not-a-dir\n", encoding="utf-8")
        proc = self.start_run(env={"FB040_MODE": "ok"})
        code, _, err = self.finish(proc)
        self.assertEqual(0, code, err)
        self.assertIn("运行注册失败", err)
        self.assertIsNone(self.meta()["runs_registry"])


class GuardClaudeRunsTest(unittest.TestCase):
    """B：guard 在跑口径合并 `<crew-dir>/runs/` 存活注册。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "repo"
        self.crew = self.base / "crew"
        plan = self.root / ".planning/flow/task_plan.md"
        plan.parent.mkdir(parents=True, exist_ok=True)
        plan.write_text(PLAN, encoding="utf-8")
        self.runs = self.crew / "runs"
        self.runs.mkdir(parents=True, exist_ok=True)
        self.prompt_calls: list[tuple[str, str]] = []
        self.notify_calls: list[tuple[str, str]] = []
        self.logs: list[str] = []

    def make_guard(self, clock: float = 1000.0) -> "cg.Guard":
        return cg.Guard(
            root=self.root,
            crew_dir=self.crew,
            helm="helm-1",
            interval=120,
            clock=lambda: clock,
            list_agents=lambda: [],
            list_panes=lambda: [],
            prompt=lambda name, message: (
                self.prompt_calls.append((name, message)) or True),
            notify=lambda title, body: (
                self.notify_calls.append((title, body)) or True),
            pane_read=lambda pane, lines=80: "",
            dispatch=lambda root, args: (0, "", ""),
            run_status=lambda root, flag: (0, "看板新鲜"),
            log_fn=self.logs.append,
        )

    def spawn_child(self, argv: list[str]) -> subprocess.Popen:
        """起一个真实子进程做 pid 身份核验夹具；测试结束终止并回收。"""
        proc = subprocess.Popen(
            argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.addCleanup(proc.wait, 10)
        self.addCleanup(proc.terminate)
        return proc

    def spawn_marked_claude(self) -> subprocess.Popen:
        """argv 含 crew-claude 的假进程（模拟 crew-claude.py run）。

        用当前解释器 + 标记参数，避免 `bash -c exec` 过渡瞬间 /proc cmdline 为空。
        """
        return self.spawn_child([
            sys.executable, "-c", "import time; time.sleep(30)",
            "crew-claude-adversary"])

    def write_run(self, name: str, payload) -> Path:
        path = self.runs / name
        text = payload if isinstance(payload, str) else json.dumps(payload)
        path.write_text(text, encoding="utf-8")
        return path

    @staticmethod
    def dead_pid() -> int:
        try:
            return int(Path("/proc/sys/kernel/pid_max").read_text().strip()) + 1
        except (OSError, ValueError):
            return 4_194_305

    def test_live_registry_counts_as_running_and_blocks_wake(self):
        child = self.spawn_marked_claude()
        self.write_run("claude-1-aa.json", {
            "pid": child.pid, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z", "out_dir": "/tmp/x"})
        guard = self.make_guard()
        first = guard.run_once()
        second = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertFalse(second["woke"])
        self.assertEqual(0, guard.consecutive)
        self.assertEqual(child.pid, first["claudeRuns"][0]["pid"])
        self.assertEqual([], self.prompt_calls)
        self.assertFalse(any("claude 运行注册" in line for line in self.logs), self.logs)

    def test_herdr_worker_completion_wakes_even_with_claude_run_alive(self):
        """FB-057① 差集边沿：herdr worker 完成即叫醒，claude run 存活不再抑制。

        旧总数边沿语义（claude 存活=未全部完成=不叫醒）废除：w 完成是真实
        边沿，claude run 不算完成项、不进完成名单。
        """
        child = self.spawn_marked_claude()
        agents = [{"name": "w", "agent_status": "working", "pane_id": "p1"}]
        guard = self.make_guard()
        guard.list_agents = lambda: list(agents)
        guard.run_once()
        agents[0] = {"name": "w", "agent_status": "idle", "pane_id": "p1"}
        self.write_run("claude-2-bb.json", {
            "pid": child.pid, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z"})
        result = guard.run_once()
        self.assertTrue(result["woke"], "herdr worker 完成边沿必须叫醒")
        self.assertIn("worker 完成待核收：w", self.prompt_calls[0][1])
        # 4c⑤：claude runs key 带 role，总舵可对应工位；名单不含 claude 项
        self.assertNotIn(f"claude:adversary:{child.pid}", self.prompt_calls[0][1])
        self.assertEqual({f"claude:adversary:{child.pid}"}, guard.prev_working)

    def test_dead_pid_registry_ignored_logged_and_wake_still_fires(self):
        dead = self.dead_pid()
        path = self.write_run("claude-3-cc.json", {
            "pid": dead, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z"})
        guard = self.make_guard()
        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual([], first["claudeRuns"])
        self.assertTrue(any("已死" in line for line in self.logs), self.logs)
        self.assertFalse(path.exists(), "死条目可顺手删除")
        second = guard.run_once()
        self.assertTrue(second["woke"])
        self.assertEqual("helm-1", self.prompt_calls[0][0])

    def test_bad_json_registry_ignored_without_crash(self):
        path = self.write_run("claude-bad.json", "{not-json")
        guard = self.make_guard()
        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual([], first["claudeRuns"])
        self.assertTrue(any("忽略" in line for line in self.logs), self.logs)
        self.assertTrue(path.exists())
        self.assertTrue(guard.run_once()["woke"])

    def test_registry_without_pid_ignored(self):
        self.write_run("claude-nopid.json", {"role": "adversary"})
        guard = self.make_guard()
        first = guard.run_once()
        self.assertFalse(first["woke"])
        self.assertEqual([], first["claudeRuns"])
        self.assertTrue(any("pid" in line for line in self.logs), self.logs)
        self.assertTrue(guard.run_once()["woke"])

    def test_once_json_exposes_claude_runs(self):
        child = self.spawn_marked_claude()
        self.write_run("claude-4-dd.json", {
            "pid": child.pid, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z", "out_dir": "/tmp/out"})
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(
                cg, "herdr_agents",
                return_value=[{"name": "w", "agent_status": "idle"}]
        ), mock.patch.object(
                cg, "run_plan_status", return_value=(0, "看板新鲜")
        ), mock.patch.object(cg, "herdr_prompt") as prompt, mock.patch.object(
                cg, "herdr_notify"), redirect_stdout(out), redirect_stderr(err):
            code = cg.main([
                "--once", "--json", "--root", str(self.root),
                "--crew-dir", str(self.crew), "--helm", "helm-1"])
        self.assertEqual(0, code, err.getvalue())
        payload = json.loads(out.getvalue())
        self.assertFalse(payload["wake"])
        self.assertEqual(child.pid, payload["claudeRuns"][0]["pid"])
        self.assertEqual("adversary", payload["claudeRuns"][0]["role"])
        self.assertIn("workers", payload)
        prompt.assert_not_called()

    # ---------- 建议-2：pid 复用 / 进程身份核验 / 超龄兜底 ----------

    def test_unrelated_live_process_is_pid_reuse_removed_not_counted(self):
        child = self.spawn_child(["sleep", "30"])
        path = self.write_run("claude-reuse.json", {
            "pid": child.pid, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z"})
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual([], result["claudeRuns"])
        self.assertFalse(path.exists(), "pid 复用注册应删除（下一轮起可自愈）")
        self.assertTrue(
            any("cmdline" in line and "crew-claude" in line for line in self.logs),
            self.logs)

    def test_marked_crew_claude_process_counts_as_running(self):
        child = self.spawn_marked_claude()
        path = self.write_run("claude-marker.json", {
            "pid": child.pid, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z"})
        guard = self.make_guard()
        result = guard.run_once()
        self.assertEqual([child.pid], [run["pid"] for run in result["claudeRuns"]])
        self.assertTrue(path.exists())
        self.assertFalse(any("cmdline" in line for line in self.logs), self.logs)

    def test_unreadable_cmdline_fresh_started_at_counts(self):
        self.write_run("claude-unread-fresh.json", {
            "pid": os.getpid(), "role": "adversary",
            "started_at": "1970-01-01T00:16:40Z"})  # clock=1000 → 新鲜
        guard = self.make_guard()
        with mock.patch.object(cg, "proc_cmdline",
                               side_effect=PermissionError("denied")):
            result = guard.run_once()
        self.assertEqual([os.getpid()],
                         [run["pid"] for run in result["claudeRuns"]])

    def test_unreadable_cmdline_stale_registry_ignored_with_warning(self):
        path = self.write_run("claude-unread-old.json", {
            "pid": os.getpid(), "role": "adversary",
            "started_at": "1970-01-01T00:16:40Z"})  # 时钟设为 25h 后
        guard = self.make_guard(clock=1000.0 + 25 * 3600)
        with mock.patch.object(cg, "proc_cmdline",
                               side_effect=OSError("denied")):
            result = guard.run_once()
        self.assertEqual([], result["claudeRuns"])
        self.assertTrue(path.exists(), "超龄取证条目保留，下一轮同样忽略")
        self.assertTrue(any("超龄" in line for line in self.logs), self.logs)

    def test_unreadable_cmdline_missing_or_invalid_started_at_counts_fail_safe(self):
        cases = {
            "missing": {"pid": os.getpid(), "role": "adversary"},
            "invalid": {"pid": os.getpid(), "role": "adversary",
                        "started_at": "not-a-date"},
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                self.logs.clear()
                path = self.write_run(f"claude-unread-{label}.json", payload)
                guard = self.make_guard()
                with mock.patch.object(cg, "proc_cmdline",
                                       side_effect=OSError("denied")):
                    result = guard.run_once()
                self.assertEqual([os.getpid()],
                                 [run["pid"] for run in result["claudeRuns"]])
                self.assertTrue(path.exists())
                self.assertTrue(
                    any("started_at" in line for line in self.logs), self.logs)
                path.unlink()

    def test_oversized_pid_registry_removed_without_crash(self):
        """R-4：2**63 超出 pid_t，注册不得让每轮 _claude_runs 抛异常。"""
        path = self.write_run("claude-huge.json", {
            "pid": 2 ** 63, "role": "adversary",
            "started_at": "2026-11-12T00:00:00Z"})
        guard = self.make_guard()
        first = guard.run_once()
        second = guard.run_once()
        self.assertEqual([], first["claudeRuns"])
        self.assertFalse(path.exists(), "无效/超范围 pid 注册应删除")
        self.assertTrue(
            any("无效/超范围 pid" in line for line in self.logs), self.logs)
        self.assertTrue(second["woke"], "删除后第二轮恢复连续叫醒语义")


class PidAliveS4Test(unittest.TestCase):
    """S4：release-check pid_alive 与 crew-preflight 口径一致。"""

    def test_current_pid_is_alive(self):
        self.assertTrue(release.pid_alive(os.getpid()))

    def test_permission_error_means_alive(self):
        with mock.patch.object(release.os, "kill", side_effect=PermissionError):
            self.assertTrue(release.pid_alive(12345))

    def test_process_lookup_error_means_dead(self):
        with mock.patch.object(
                release.os, "kill", side_effect=ProcessLookupError):
            self.assertFalse(release.pid_alive(12345))

    def test_other_oserror_stays_dead(self):
        with mock.patch.object(release.os, "kill", side_effect=OSError):
            self.assertFalse(release.pid_alive(12345))

    def test_oversized_pid_is_invalid_and_never_raises(self):
        """R-4：2**63 超出 pid_t 范围，guard/release-check/preflight 均返回 False。"""
        self.assertFalse(cg.pid_alive(2 ** 63))
        self.assertFalse(release.pid_alive(2 ** 63))
        self.assertFalse(preflight._pid_alive(2 ** 63))


if __name__ == "__main__":
    unittest.main()
