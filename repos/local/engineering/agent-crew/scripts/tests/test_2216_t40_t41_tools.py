#!/usr/bin/env python3
"""2.2.16 T40/T41：证据采集器与轮次预检工具的行为断言。

覆盖：
- `scripts/crew-evidence.py`：成功/失败镜像 rc/缺参/六字段齐全/原子写/命令不存在；
- `scripts/crew-preflight.py`：正常、wake 过期/缺失、inbox 缺失、herdr 不可用、
  guard.lock 缺失/pid 不活、游标复用/损坏回退、看板过期与 `--strict`；
- `scripts/init.py` 的 `SCRIPT_MANIFEST` 登记（T43 的 release-check.py 条目
  随其文件同切片加入，文件未落地时该断言 skip，避免 upgrade 源缺失中止）。
"""

from __future__ import annotations

import fcntl
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPTS = SKILL / "scripts"
EVIDENCE = SCRIPTS / "crew-evidence.py"
PREFLIGHT = SCRIPTS / "crew-preflight.py"
PLAN_STATUS = SCRIPTS / "plan-status.py"
GIT = shutil.which("git") or "/usr/bin/git"
PYTHON = sys.executable

MARKS = {
    "--- stdout ---": "stdout",
    "--- stderr ---": "stderr",
    "--- git status --porcelain ---": "git_status",
}
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def fresh_stamp() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclass 注解解析需要模块已注册
    spec.loader.exec_module(module)
    return module


def evidence_fields(text: str) -> dict[str, str]:
    """按固定标记切分证据文件；header 中 `key: value` 取 time/cwd/command/rc。"""
    sections: dict[str, str] = {}
    current = "header"
    buffer: list[str] = []
    for line in text.splitlines():
        if line in MARKS:
            sections[current] = "\n".join(buffer)
            current = MARKS[line]
            buffer = []
        else:
            buffer.append(line)
    sections[current] = "\n".join(buffer)
    fields: dict[str, str] = {}
    for line in sections.pop("header", "").splitlines():
        key, sep, value = line.partition(": ")
        if sep and key in ("time", "cwd", "command", "rc"):
            fields[key] = value
    fields.update(sections)
    return fields


def stream_body() -> str:
    return (
        "# 流 plan\n\n## Goal\n目标。\n\n## Next Step\n做下一条。\n\n"
        "## Phases\n\n### Phase 1: 阶段一\n**Status:** pending\n"
    )


def portfolio_body() -> str:
    return (
        "# 组合 plan\n\n## Goal\n合成验证用目标。\n\n## Next Step\n推进流。\n\n"
        "## Phases\n\n### Phase 1: 流A\n"
        "- plan: `.planning/stream/task_plan.md`\n**Status:** pending\n\n"
        "## Backlog\n\n| # | 目标 | P | 依赖 | 阻塞 |\n|---|---|---|---|---|\n"
    )


class EvidenceCliTest(unittest.TestCase):
    """T40：一条命令落六项证据，失败镜像 rc，原子写无半文件。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "repo"
        self.root.mkdir()
        subprocess.run([GIT, "init", "-q", str(self.root)], check=True,
                       capture_output=True)
        (self.root / "dirty.txt").write_text("x\n", encoding="utf-8")
        self.out = self.tmp / "evidence" / "doctor.txt"

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [PYTHON, str(EVIDENCE), *args],
            capture_output=True, text=True, cwd=str(self.tmp))

    def test_success_records_six_fields(self):
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--",
            PYTHON, "-c",
            "import sys; print('hello-out'); print('hello-err', file=sys.stderr)")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertTrue(self.out.is_file(), "证据文件未生成")
        text = self.out.read_text(encoding="utf-8")
        fields = evidence_fields(text)
        for key in ("time", "cwd", "command", "rc", "stdout", "stderr", "git_status"):
            self.assertIn(key, fields, f"缺字段 {key}")
        self.assertRegex(fields["time"], TIME_RE)
        self.assertEqual(str(self.root), fields["cwd"])
        self.assertIn("-c", fields["command"])
        self.assertIn("hello-out", fields["command"])
        self.assertEqual("0", fields["rc"])
        self.assertIn("hello-out", fields["stdout"])
        self.assertIn("hello-err", fields["stderr"])
        self.assertIn("?? dirty.txt", fields["git_status"])
        self.assertIn("evidence:", proc.stdout)

    def test_failure_mirrors_rc_and_keeps_evidence(self):
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--",
            PYTHON, "-c",
            "import sys; print('partial'); print('boom', file=sys.stderr); "
            "sys.exit(7)")
        self.assertEqual(7, proc.returncode, proc.stderr)
        fields = evidence_fields(self.out.read_text(encoding="utf-8"))
        self.assertEqual("7", fields["rc"])
        self.assertIn("partial", fields["stdout"])
        self.assertIn("boom", fields["stderr"])

    def test_missing_out_is_usage_error(self):
        proc = self.run_cli("--", PYTHON, "-c", "print(1)")
        self.assertEqual(2, proc.returncode)
        self.assertIn("usage", proc.stderr.lower())
        self.assertIn("--out", proc.stderr)

    def test_missing_command_is_usage_error(self):
        proc = self.run_cli("--out", str(self.out))
        self.assertEqual(2, proc.returncode)
        self.assertIn("缺少被采命令", proc.stderr)
        self.assertFalse(self.out.exists(), "参数错误不得产生证据文件")

    def test_command_not_found_records_127(self):
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--",
            str(self.tmp / "no-such-command-xyz"))
        self.assertEqual(127, proc.returncode)
        fields = evidence_fields(self.out.read_text(encoding="utf-8"))
        self.assertEqual("127", fields["rc"])
        self.assertIn("命令无法执行", fields["stderr"])

    def test_out_parent_created_and_nonempty(self):
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--",
            PYTHON, "-c", "print('ok')")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertGreater(self.out.stat().st_size, 0)
        leftovers = list(self.out.parent.glob("*.tmp"))
        self.assertEqual([], leftovers, f"残留临时文件：{leftovers}")

    def test_atomic_write_failure_leaves_no_partial_file(self):
        evidence = load_module("evidence_t40", EVIDENCE)
        target = self.tmp / "atomic" / "out.txt"
        with mock.patch.object(evidence.os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                evidence.atomic_write_text(target, "content\n")
        self.assertFalse(target.exists(), "replace 失败后不得留下目标半文件")
        self.assertEqual([], list(target.parent.glob("*.tmp")),
                         "replace 失败后不得残留临时文件")

    def test_write_failure_has_fixed_marker_and_rc1(self):
        """S-7：写失败恒 rc=1，stderr 首行固定标记（区分被采命令 rc=1）。"""
        out_dir = self.tmp / "out-as-directory"
        out_dir.mkdir()
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(out_dir), "--",
            PYTHON, "-c", "print('ok')")
        self.assertEqual(1, proc.returncode, proc.stderr)
        self.assertEqual("crew-evidence: WRITE_FAILED",
                         proc.stderr.splitlines()[0])

    def test_help_documents_exit_code_contract(self):
        proc = self.run_cli("--help")
        self.assertEqual(0, proc.returncode)
        self.assertIn("写失败恒 1", proc.stdout)
        self.assertIn("crew-evidence: WRITE_FAILED", proc.stdout)
        self.assertIn("--timeout", proc.stdout)
        self.assertIn("超时", proc.stdout)
        self.assertIn("124", proc.stdout)

    def test_timeout_kills_command_and_keeps_evidence(self):
        """S9：--timeout 到点终止被采命令，证据仍落盘（rc=124 + stderr 超时说明）。"""
        started = time.monotonic()
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--timeout", "1", "--",
            PYTHON, "-c", "import time; time.sleep(30)")
        elapsed = time.monotonic() - started
        self.assertEqual(124, proc.returncode, proc.stderr)
        self.assertLess(elapsed, 10, "超时未及时终止被采命令")
        self.assertTrue(self.out.is_file(), "超时后证据文件仍应落盘")
        fields = evidence_fields(self.out.read_text(encoding="utf-8"))
        self.assertEqual("124", fields["rc"])
        self.assertIn("超时", fields["stderr"])
        self.assertIn("124", fields["stderr"])

    def test_timeout_does_not_affect_normal_command(self):
        proc = self.run_cli(
            "--root", str(self.root), "--out", str(self.out), "--timeout", "5", "--",
            PYTHON, "-c", "print('ok')")
        self.assertEqual(0, proc.returncode, proc.stderr)
        fields = evidence_fields(self.out.read_text(encoding="utf-8"))
        self.assertEqual("0", fields["rc"])
        self.assertIn("ok", fields["stdout"])


class PreflightParserTest(unittest.TestCase):
    """T41：wake/ctx 解析与 crew-guard 同口径；不可用路径 fail-safe。"""

    @classmethod
    def setUpClass(cls):
        cls.pf = load_module("preflight_t41", PREFLIGHT)
        cls.guard = load_module("crew_guard_t41", SCRIPTS / "crew-guard.py")

    def test_ctx_parse_matches_guard(self):
        samples = (
            "ctx (75.4k/1M)",
            "ctx 412k/1M ok",
            "ctx ( 62.3% )",
            "ctx unknown",
            "no context here",
            "a ctx 100k/1M\nb ctx (250.5k/1M)",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertEqual(self.guard.parse_ctx_k(sample),
                                 self.pf.parse_ctx_k(sample))

    def test_wake_parse_formats(self):
        self.assertIsNotNone(
            self.pf.parse_wake_generated_at("generated_at: 2026-11-12T00:00:00Z\nmsg"))
        self.assertIsNotNone(self.pf.parse_wake_generated_at("2026-11-12T00:00:00Z\nmsg"))
        self.assertIsNone(self.pf.parse_wake_generated_at(""))
        self.assertIsNone(self.pf.parse_wake_generated_at("garbage\nmsg"))

    def test_board_unavailable_when_script_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.pf.check_board(Path(tmp), Path(tmp) / "missing.py")
        self.assertFalse(result["available"])
        self.assertFalse(result["ok"])
        self.assertIn("不可用", result["value"])

    def test_agents_unavailable_when_herdr_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"PATH": str(Path(tmp) / "empty")}):
                result = self.pf.check_agents(Path(tmp))
        self.assertFalse(result["available"])
        self.assertIn("不可用", result["value"])

    def test_guard_unavailable_without_crew_dir(self):
        result = self.pf.check_guard(None, "crew 目录不可用：fixture")
        self.assertFalse(result["available"])
        self.assertIn("不可用", result["value"])


class PreflightCliTest(unittest.TestCase):
    """T41：一次运行五项检查；缺失/失败标注不可用或未知，默认 rc=0。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.make_project()
        self.crew = self.root / ".git" / "crew"
        self.crew.mkdir(exist_ok=True)
        self.bin_empty = self.tmp / "bin-empty"
        self.bin_empty.mkdir()

    # ---------- 夹具 ----------

    def make_project(self) -> Path:
        root = self.tmp / "repo"
        root.mkdir()
        subprocess.run([GIT, "init", "-q", str(root)], check=True,
                       capture_output=True)
        planning = root / ".planning"
        (planning / "portfolio").mkdir(parents=True)
        (planning / "portfolio" / "task_plan.md").write_text(
            portfolio_body(), encoding="utf-8")
        (planning / "stream").mkdir()
        (planning / "stream" / "task_plan.md").write_text(
            stream_body(), encoding="utf-8")
        proc = subprocess.run(
            [PYTHON, str(PLAN_STATUS), "--root", str(root), "--write"],
            capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        return root

    def make_herdr(self, agents: list[dict] | None = None,
                   pane_text: str = "status\nctx (75.4k/1M)\n", fail: bool = False) -> Path:
        bindir = self.tmp / "bin-herdr"
        bindir.mkdir(exist_ok=True)
        script = bindir / "herdr"
        payload = json.dumps(agents or [], ensure_ascii=False)
        body = f"""#!{PYTHON}
import json, sys
args = sys.argv[1:]
if {fail!r}:
    print("herdr down", file=sys.stderr)
    sys.exit(1)
if args[:3] == ["agent", "list"]:
    print(json.dumps({{"result": {{"agents": {payload}}}}}, ensure_ascii=False))
    sys.exit(0)
if args[:2] == ["pane", "read"]:
    print({json.dumps(pane_text)})
    sys.exit(0)
print("unsupported args", file=sys.stderr)
sys.exit(2)
"""
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
        return bindir

    def write_wake(self, generated_at: str | None, body: str = "叫醒：有活") -> None:
        if generated_at is None:
            (self.crew / "guard.wake").unlink(missing_ok=True)
            return
        (self.crew / "guard.wake").write_text(
            f"generated_at: {generated_at}\n{body}\n", encoding="utf-8")

    def write_receipt(self, receipt_id: str, status: str = "done") -> None:
        folder = self.crew / "inbox"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{receipt_id}.json").write_text(json.dumps({
            "receipt_id": receipt_id, "from": "stub", "runtime": "qoder",
            "task": "T-1", "status": status, "commit": "",
            "changed_files": [], "evidence": [], "work_slot": "stub",
            "findings": [], "summary": "stub", "ts": "2026-11-12T00:00:00Z",
        }, ensure_ascii=False), encoding="utf-8")

    def hold_lock(self, pid: int) -> None:
        path = self.crew / "guard.lock"
        handle = open(path, "w", encoding="utf-8")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(f"{pid}\n")
        handle.flush()
        self.addCleanup(handle.close)

    def run_preflight(self, *args: str, bindir: Path | None = None,
                      json_out: bool = True) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{bindir or self.bin_empty}:/usr/bin:/bin",
            "HOME": os.environ.get("HOME", str(self.tmp)),
        }
        argv = [PYTHON, str(PREFLIGHT), "--root", str(self.root)]
        if json_out:
            argv.append("--json")
        argv.extend(args)
        return subprocess.run(argv, capture_output=True, text=True, env=env,
                              cwd=str(self.tmp))

    def checks(self, proc: subprocess.CompletedProcess) -> tuple[dict, dict]:
        self.assertEqual(0, proc.returncode, proc.stderr)
        data = json.loads(proc.stdout)
        return {item["id"]: item for item in data["checks"]}, data

    # ---------- 正常路径 ----------

    def test_normal_project_all_checks_available(self):
        self.write_wake(fresh_stamp())
        self.write_receipt("r-1")
        self.hold_lock(os.getpid())
        bindir = self.make_herdr([{
            "name": "fm", "agent_status": "working", "pane_id": "w12:p8F",
            "cwd": str(self.root),
        }])
        proc = self.run_preflight(bindir=bindir)
        checks, data = self.checks(proc)
        self.assertEqual(["wake", "inbox", "agents", "board", "guard", "capacity"],
                         [item["id"] for item in data["checks"]])  # capacity=07B 附项
        for key in ("wake", "inbox", "agents", "board", "guard"):
            self.assertTrue(checks[key]["available"], checks[key])
            self.assertIn("value", checks[key])
            self.assertIn("source", checks[key])
        self.assertTrue(checks["wake"]["ok"])
        self.assertIn("1 条未读", checks["inbox"]["value"])
        self.assertTrue(checks["agents"]["ok"])
        self.assertIn("75.4", checks["agents"]["detail"])
        self.assertIn("working=1", checks["agents"]["value"])
        self.assertTrue(checks["board"]["ok"])
        self.assertTrue(checks["guard"]["ok"])
        self.assertIn("存活", checks["guard"]["value"])

    def test_human_output_has_five_items_with_value_source_available(self):
        self.write_wake(fresh_stamp())
        self.hold_lock(os.getpid())
        proc = self.run_preflight(json_out=False)
        self.assertEqual(0, proc.returncode, proc.stderr)
        for mark in ("①", "②", "③", "④", "⑤"):
            self.assertIn(mark, proc.stdout)
        self.assertIn("值=", proc.stdout)
        self.assertIn("来源=", proc.stdout)
        self.assertIn("可用", proc.stdout)

    # ---------- 单项缺失/异常 ----------

    def test_expired_wake_flags_but_stays_rc0(self):
        self.write_wake("2020-01-01T00:00:00Z")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertTrue(checks["wake"]["available"])
        self.assertFalse(checks["wake"]["ok"])
        self.assertIn("过期", checks["wake"]["value"])

    def test_expired_wake_strict_fails(self):
        self.write_wake("2020-01-01T00:00:00Z")
        self.hold_lock(os.getpid())
        proc = self.run_preflight("--strict")
        self.assertEqual(1, proc.returncode)
        data = json.loads(proc.stdout)
        self.assertFalse(data["ok"])

    def test_missing_wake_is_reported(self):
        self.write_wake(None)
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertTrue(checks["wake"]["available"])
        self.assertTrue(checks["wake"]["ok"])
        self.assertIn("缺失", checks["wake"]["value"])

    def test_missing_inbox_counts_zero(self):
        self.write_wake(fresh_stamp())
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertTrue(checks["inbox"]["available"])
        self.assertIn("0 条", checks["inbox"]["value"])

    def test_herdr_unavailable_is_fail_safe(self):
        self.write_wake(fresh_stamp())
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertFalse(checks["agents"]["available"])
        self.assertFalse(checks["agents"]["ok"])
        self.assertIn("不可用", checks["agents"]["value"])

    def test_herdr_error_rc_is_unavailable(self):
        self.write_wake(fresh_stamp())
        self.hold_lock(os.getpid())
        bindir = self.make_herdr([], fail=True)
        proc = self.run_preflight(bindir=bindir)
        checks, _ = self.checks(proc)
        self.assertFalse(checks["agents"]["available"])
        self.assertIn("退出码", checks["agents"]["detail"])

    def test_missing_guard_lock_is_flagged(self):
        self.write_wake(fresh_stamp())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertTrue(checks["guard"]["available"])
        self.assertFalse(checks["guard"]["ok"])
        self.assertIn("缺失", checks["guard"]["value"])

    def test_dead_guard_pid_is_flagged(self):
        self.write_wake(fresh_stamp())
        (self.crew / "guard.lock").write_text("999999\n", encoding="utf-8")
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertFalse(checks["guard"]["ok"])
        self.assertIn("已退出", checks["guard"]["value"])

    def test_dead_pid_strict_fails(self):
        (self.crew / "guard.lock").write_text("999999\n", encoding="utf-8")
        proc = self.run_preflight("--strict")
        self.assertEqual(1, proc.returncode)
        data = json.loads(proc.stdout)
        guard = next(item for item in data["checks"] if item["id"] == "guard")
        self.assertFalse(guard["available"] and guard["ok"])

    # ---------- 未读游标（T42 就绪则复用） ----------

    def test_cursor_reduces_unread(self):
        self.write_receipt("r-1")
        self.write_receipt("r-2")
        cursor = self.crew / "inbox-read-cursor.json"
        cursor.write_text(json.dumps({
            "version": 1, "read": {"r-1": "2026-11-12T00:00:00Z"},
        }), encoding="utf-8")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertIn("1 条未读", checks["inbox"]["value"])
        self.assertIn("inbox-read-cursor.json", checks["inbox"]["source"])
        self.assertIn("r-2", checks["inbox"]["detail"])
        self.assertNotIn("r-1", checks["inbox"]["detail"])

    def test_corrupt_cursor_falls_back_to_all(self):
        self.write_receipt("r-1")
        self.write_receipt("r-2")
        (self.crew / "inbox-read-cursor.json").write_text(
            "not-json", encoding="utf-8")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertIn("2 条未读", checks["inbox"]["value"])
        self.assertIn("游标损坏", checks["inbox"]["value"])

    def test_missing_cursor_marks_fallback(self):
        self.write_receipt("r-1")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertIn("游标未就绪", checks["inbox"]["value"])

    def test_inbox_value_marks_read_is_not_disposed(self):
        """S-3：未读清单同时标注「已读≠已处置」，避免把游标当处置口径。"""
        self.write_receipt("r-1")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertIn("已读≠已处置", checks["inbox"]["value"])
        self.assertIn("回执 status + plan", checks["inbox"]["value"])

    # ---------- 看板 ----------

    def test_stale_board_is_flagged_and_strict_fails(self):
        plan = self.root / ".planning/stream/task_plan.md"
        plan.write_text(plan.read_text(encoding="utf-8") + "\n补充一行。\n",
                        encoding="utf-8")
        self.hold_lock(os.getpid())
        proc = self.run_preflight()
        checks, _ = self.checks(proc)
        self.assertTrue(checks["board"]["available"])
        self.assertFalse(checks["board"]["ok"])
        self.assertIn("过期", checks["board"]["value"])
        strict = self.run_preflight("--strict")
        self.assertEqual(1, strict.returncode)


class PreflightLockProbeTest(unittest.TestCase):
    """S7：_lock_held 只读探测，不在竞态下新建空 guard.lock。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.pf = load_module("preflight_s7", PREFLIGHT)

    def test_missing_lock_reports_reason_without_creating_file(self):
        path = self.tmp / "guard.lock"
        held, reason = self.pf._lock_held(path)
        self.assertFalse(held)
        self.assertTrue(reason)
        self.assertFalse(path.exists(), "探测不得新建空 guard.lock")

    def test_held_then_released_semantics(self):
        path = self.tmp / "guard.lock"
        path.write_text(f"{os.getpid()}\n", encoding="utf-8")
        handle = open(path, "r", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            held, reason = self.pf._lock_held(path)
            self.assertTrue(held, reason)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
        held, reason = self.pf._lock_held(path)
        self.assertFalse(held, reason)


class PreflightWorktreeAgentsTest(unittest.TestCase):
    """S8：agent 口径=root + linked worktree（与 guard _in_project 一致）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.pf = load_module("preflight_s8", PREFLIGHT)

    def _git_repo(self, name: str) -> Path:
        root = self.tmp / name
        root.mkdir()
        subprocess.run([GIT, "init", "-q", str(root)], check=True,
                       capture_output=True)
        (root / "README.md").write_text("x\n", encoding="utf-8")
        subprocess.run([GIT, "-C", str(root), "add", "README.md"],
                       check=True, capture_output=True)
        subprocess.run(
            [GIT, "-C", str(root), "-c", "user.email=t@example.com",
             "-c", "user.name=T", "commit", "-qm", "init"],
            check=True, capture_output=True)
        return root

    def _herdr(self, agents: list[dict]) -> str:
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir(exist_ok=True)
        script = bin_dir / "herdr"
        payload = json.dumps(agents, ensure_ascii=False)
        script.write_text(
            f"""#!{PYTHON}
import json, sys
args = sys.argv[1:]
if args[:3] == ["agent", "list"]:
    print(json.dumps({{"result": {{"agents": {payload}}}}}, ensure_ascii=False))
    sys.exit(0)
if args[:2] == ["pane", "read"]:
    print("ctx (10k/1M)")
    sys.exit(0)
sys.exit(2)
""", encoding="utf-8")
        script.chmod(0o755)
        return str(script)

    def test_linked_worktree_agent_counted_outside_excluded(self):
        root = self._git_repo("repo")
        worktree = self.tmp / "repo-wt"
        subprocess.run(
            [GIT, "-C", str(root), "worktree", "add", "-q", "-b", "wt", str(worktree)],
            check=True, capture_output=True)
        outside = self._git_repo("outside")
        herdr = self._herdr([
            {"name": "root-agent", "agent_status": "idle", "cwd": str(root)},
            {"name": "wt-agent", "agent_status": "working", "cwd": str(worktree)},
            {"name": "outside-agent", "agent_status": "working", "cwd": str(outside)},
        ])
        result = self.pf.check_agents(root, herdr)
        self.assertTrue(result["available"], result)
        self.assertIn("2 个本仓 agent", result["value"])
        self.assertIn("wt-agent", result["detail"])
        self.assertNotIn("outside-agent", result["detail"])

    def test_non_git_root_keeps_old_behavior(self):
        root = self.tmp / "plain"
        root.mkdir()
        herdr = self._herdr([
            {"name": "root-agent", "agent_status": "idle", "cwd": str(root)},
        ])
        result = self.pf.check_agents(root, herdr)
        self.assertTrue(result["available"], result)
        self.assertIn("1 个本仓 agent", result["value"])


class ManifestTest(unittest.TestCase):
    """T40/T41 受管脚本登记；T43 条目随其文件同切片加入。"""

    @classmethod
    def setUpClass(cls):
        cls.init = load_module("init_t40t41", SCRIPTS / "init.py")

    def test_new_tools_registered(self):
        self.assertIn("crew-evidence.py", self.init.SCRIPT_MANIFEST)
        self.assertIn("crew-preflight.py", self.init.SCRIPT_MANIFEST)

    def test_release_check_registered_when_file_exists(self):
        if not (SCRIPTS / "release-check.py").is_file():
            self.skipTest(
                "release-check.py 由 T43 实现；清单条目随文件同切片加入"
                "（先加条目会让 upgrade 以「源缺失受管脚本」中止，见 T40/T41 回执）")
        self.assertIn("release-check.py", self.init.SCRIPT_MANIFEST)


if __name__ == "__main__":
    unittest.main()
