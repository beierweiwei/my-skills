#!/usr/bin/env python3
"""T39：backend=claude 统一执行器（crew-claude.py）回归。

行为断言：模型级联解析、--model 传参与不注入 env（T56）、只读工具白名单、
超时杀进程、报告/stderr/rc/meta 一次落盘、空报告/rc≠0/超时/backend≠claude 的非零
诊断，以及 SCRIPT_MANIFEST 与角色模板/剧本 README/速查表的统一入口口径。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

_TESTS_DIR = Path(__file__).resolve().parent
_SCRIPTS = _TESTS_DIR.parent
SKILL = _SCRIPTS.parent

_spec = importlib.util.spec_from_file_location(
    "crew_claude_t39", _SCRIPTS / "crew-claude.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["crew_claude_t39"] = cc
_spec.loader.exec_module(cc)

_init_spec = importlib.util.spec_from_file_location(
    "init_t39", _SCRIPTS / "init.py")
init_mod = importlib.util.module_from_spec(_init_spec)
sys.modules["init_t39"] = init_mod
_init_spec.loader.exec_module(init_mod)

STUB = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    import json, os, signal, sys, time

    prompt = sys.stdin.read()
    record = {
        "argv": sys.argv[1:],
        "env_model": os.environ.get("ANTHROPIC_MODEL"),
        "prompt": prompt,
        "cwd": os.getcwd(),
    }
    target = os.environ.get("T39_RECORD")
    if target:
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(record, fh, ensure_ascii=False)
    mode = os.environ.get("T39_MODE", "ok")
    if mode == "sleep":
        time.sleep(10)
    if mode == "empty":
        sys.exit(0)
    if mode == "fail":
        sys.stderr.write("boom-stderr\\n")
        sys.exit(int(os.environ.get("T39_RC", "7")))
    if mode == "signal":
        os.kill(os.getpid(), signal.SIGKILL)
    sys.stdout.write("STUB-REPORT\\n## 结论\\n"
                     "证据一：结论正文甲与补充说明，用于抬高字节数到阈值之上。\\n"
                     "证据二：结论正文乙与补充说明，用于抬高字节数到阈值之上。\\n"
                     "证据三：结论正文丙与补充说明，用于抬高字节数到阈值之上。\\n"
                     "## 来源\\n- 本地 stub 固定输出（无外部访问）。\\n")
    ''')

UNIFIED_ENTRY_FILES = [
    "templates/agents/qoder/helm.md",
    "templates/agents/qoder/firstmate.md",
    "templates/agents/qoder/runner.md",
    "templates/agents/opencode/helm.md",
    "templates/agents/opencode/firstmate.md",
    "templates/agents/opencode/runner.md",
    "templates/playbooks/README.md",
]


def agents_config(default_model: str = "default-model",
                  role_model: str = "role-model",
                  role_backend: str = "claude",
                  playbook: dict | None = None) -> dict:
    return {
        "default": {"backend": "claude", "model": default_model},
        "roles": {"adversary": {"backend": role_backend, "model": role_model}},
        "playbooks": playbook or {},
    }


class ClaudeWrapperTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.root.mkdir()
        self.out = self.base / "evidence"
        self.prompt = self.base / "prompt.md"
        self.prompt.write_text("只读任务：输出 pwd。\n", encoding="utf-8")
        self.record = self.base / "record.json"
        self.stub = self.base / "claude-stub"
        self.stub.write_text(STUB, encoding="utf-8")
        self.stub.chmod(0o755)
        self.config = self.root / ".planning/orchestration.json"

    # ---------- 夹具 ----------

    def write_config(self, agents: dict) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            json.dumps({"agents": agents}, ensure_ascii=False), encoding="utf-8")

    def run_cli(self, *extra: str, mode: str = "ok", rc: int = 7,
                name: str = "case") -> tuple[int, str, str]:
        argv = [
            "run", "--role", "adversary",
            "--prompt", str(self.prompt),
            "--out-dir", str(self.out),
            "--name", name,
            "--config", str(self.config),
            "--claude-bin", str(self.stub),
            *extra,
        ]
        env = {"T39_RECORD": str(self.record), "T39_MODE": mode, "T39_RC": str(rc)}
        out, err = StringIO(), StringIO()
        with mock.patch.dict(os.environ, env, clear=False), \
                redirect_stdout(out), redirect_stderr(err):
            code = cc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def meta(self, name: str = "case") -> dict:
        return json.loads((self.out / f"{name}.meta.json").read_text(encoding="utf-8"))

    def record_data(self) -> dict:
        return json.loads(self.record.read_text(encoding="utf-8"))

    # ---------- 模型解析 ----------

    def test_resolution_playbook_over_global_over_default(self):
        self.write_config(agents_config(playbook={
            "interrogate": {"roles": {
                "adversary": {"backend": "claude", "model": "pb-model"}}}}))
        code, out, _ = self.run_cli("--playbook", "interrogate")
        self.assertEqual(0, code, out)
        meta = self.meta()
        self.assertEqual("pb-model", meta["model"])
        self.assertEqual("playbooks.interrogate.roles.adversary", meta["model_source"])
        argv = self.record_data()["argv"]
        self.assertIn("--model", argv)
        self.assertEqual("pb-model", argv[argv.index("--model") + 1])

        code, _, _ = self.run_cli(name="global")
        self.assertEqual(0, code)
        self.assertEqual("role-model", self.meta("global")["model"])
        self.assertEqual("roles.adversary", self.meta("global")["model_source"])

    def test_unknown_role_falls_back_to_default(self):
        self.write_config(agents_config(default_model="fallback-model"))
        argv = [
            "run", "--role", "custom-role", "--prompt", str(self.prompt),
            "--out-dir", str(self.out), "--name", "default",
            "--config", str(self.config), "--claude-bin", str(self.stub),
        ]
        with mock.patch.dict(os.environ, {"T39_RECORD": str(self.record),
                                          "T39_MODE": "ok"}, clear=False), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = cc.main(argv)
        self.assertEqual(0, code)
        meta = self.meta("default")
        self.assertEqual("fallback-model", meta["model"])
        self.assertEqual("default", meta["model_source"])

    # ---------- 传参口径与证据 ----------

    def test_model_flag_argv_and_artifacts(self):
        self.write_config(agents_config(role_model="glm-5.3"))
        saved = os.environ.pop("ANTHROPIC_MODEL", None)
        try:
            code, out, err = self.run_cli()
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_MODEL"] = saved
        self.assertEqual(0, code, err)

        rec = self.record_data()
        self.assertEqual([
            "-p", "--allowedTools", "Bash", "Read", "Grep", "Glob",
            "--disallowedTools", "Edit", "Write", "NotebookEdit",
            "--model", "glm-5.3",
        ], rec["argv"])
        # 不注入 env：settings.json env 会覆盖子进程同名变量，注入无效且误导
        self.assertIsNone(rec["env_model"])
        self.assertEqual("只读任务：输出 pwd。\n", rec["prompt"])
        self.assertEqual(str(self.root.resolve()), rec["cwd"])

        meta = self.meta()
        for key in ("model", "backend", "rc", "bytes", "duration", "prompt", "report"):
            self.assertIn(key, meta)
        self.assertEqual("glm-5.3", meta["model"])
        self.assertEqual("claude", meta["backend"])
        self.assertEqual(0, meta["rc"])
        self.assertFalse(meta["timeout"])
        self.assertEqual(len((self.out / "case.md").read_bytes()), meta["bytes"])
        self.assertGreaterEqual(meta["duration"], 0)
        self.assertTrue(meta["prompt"].endswith("prompt.md"))
        self.assertTrue(meta["report"].endswith("case.md"))

        self.assertEqual(
            "STUB-REPORT\n## 结论\n"
            "证据一：结论正文甲与补充说明，用于抬高字节数到阈值之上。\n"
            "证据二：结论正文乙与补充说明，用于抬高字节数到阈值之上。\n"
            "证据三：结论正文丙与补充说明，用于抬高字节数到阈值之上。\n"
            "## 来源\n- 本地 stub 固定输出（无外部访问）。\n",
            (self.out / "case.md").read_text(encoding="utf-8"))
        self.assertEqual("0\n", (self.out / "case.rc").read_text(encoding="utf-8"))
        self.assertEqual("", (self.out / "case.stderr").read_text(encoding="utf-8"))

        lines = [line for line in out.splitlines() if line.strip()]
        self.assertEqual(1, len(lines), out)
        self.assertTrue(lines[0].startswith("crew-claude: ok"), lines[0])

    def test_empty_model_omits_model_flag(self):
        self.write_config(agents_config(role_model=""))
        saved = os.environ.pop("ANTHROPIC_MODEL", None)
        try:
            code, _, err = self.run_cli()
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_MODEL"] = saved
        self.assertEqual(0, code, err)
        self.assertNotIn("--model", self.record_data()["argv"])
        self.assertIsNone(self.record_data()["env_model"])
        self.assertEqual("", self.meta()["model"])

    # ---------- 失败快速报 ----------

    def test_empty_report_fails(self):
        self.write_config(agents_config())
        code, out, err = self.run_cli(mode="empty")
        self.assertEqual(1, code, out + err)
        self.assertIn("报告为空", err)
        self.assertTrue((self.out / "case.md").is_file())
        self.assertEqual(0, self.meta()["rc"])
        self.assertEqual(0, self.meta()["bytes"])

    def test_nonzero_rc_mirrored_with_diagnosis(self):
        self.write_config(agents_config())
        code, out, err = self.run_cli(mode="fail", rc=7)
        self.assertEqual(7, code, out + err)
        self.assertIn("rc=7", err)
        self.assertIn("boom-stderr", err)
        self.assertEqual(7, self.meta()["rc"])

    def test_signal_rc_mapped_to_shell_code(self):
        self.write_config(agents_config())
        code, _, err = self.run_cli(mode="signal")
        self.assertEqual(137, code, err)
        self.assertEqual(137, self.meta()["rc"])

    def test_timeout_kills_process_and_exits_124(self):
        self.write_config(agents_config())
        code, out, err = self.run_cli("--timeout", "1", mode="sleep")
        self.assertEqual(124, code, out + err)
        self.assertIn("超时", err)
        meta = self.meta()
        self.assertTrue(meta["timeout"])
        self.assertEqual(124, meta["rc"])
        self.assertLess(meta["duration"], 5)
        self.assertTrue((self.out / "case.rc").is_file())

    def test_backend_not_claude_rejected_with_channel_hint(self):
        self.write_config(agents_config(role_backend="qoder"))
        code, _, err = self.run_cli()
        self.assertEqual(2, code, err)
        self.assertIn("不是 claude", err)
        self.assertIn("dispatch.py start", err)
        self.assertFalse(self.record.exists())
        self.assertFalse(self.out.exists())

    # ---------- 环境与参数 ----------

    def test_missing_claude_binary_exits_3(self):
        self.write_config(agents_config())
        argv = [
            "run", "--role", "adversary", "--prompt", str(self.prompt),
            "--out-dir", str(self.out), "--config", str(self.config),
            "--claude-bin", str(self.base / "nope"),
        ]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = cc.main(argv)
        self.assertEqual(3, code)

    def test_missing_prompt_and_bad_timeout_are_usage_errors(self):
        self.write_config(agents_config())
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cc.main([
                "run", "--role", "adversary", "--prompt", str(self.base / "nope.md"),
                "--out-dir", str(self.out), "--config", str(self.config),
                "--claude-bin", str(self.stub)])
        self.assertEqual(3, code)
        self.assertIn("prompt 文件不存在", err.getvalue())

        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = cc.main([
                "run", "--role", "adversary", "--prompt", str(self.prompt),
                "--out-dir", str(self.out), "--timeout", "0",
                "--config", str(self.config), "--claude-bin", str(self.stub)])
        self.assertEqual(2, code)

    def test_missing_config_exits_3(self):
        argv = [
            "run", "--role", "adversary", "--prompt", str(self.prompt),
            "--out-dir", str(self.out), "--config", str(self.base / "absent.json"),
            "--claude-bin", str(self.stub),
        ]
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            code = cc.main(argv)
        self.assertEqual(3, code)

    # ---------- 受管与文档口径 ----------

    def test_script_in_manifest(self):
        self.assertIn("crew-claude.py", init_mod.SCRIPT_MANIFEST)

    def test_default_timeout_and_readonly_tools(self):
        args = cc.build_parser().parse_args([
            "run", "--role", "adversary", "--prompt", "p", "--out-dir", "o"])
        self.assertEqual(1500, args.timeout)
        self.assertEqual(("Bash", "Read", "Grep", "Glob"), cc.ALLOWED_TOOLS)
        self.assertEqual(("Edit", "Write", "NotebookEdit"), cc.DISALLOWED_TOOLS)

    def test_templates_use_unified_entry_only(self):
        for rel in UNIFIED_ENTRY_FILES:
            with self.subTest(doc=rel):
                text = (SKILL / rel).read_text(encoding="utf-8")
                self.assertIn("crew-claude.py run", text)
                self.assertNotIn("claude -p", text)
                lines = [line for line in text.splitlines()
                         if "crew-claude.py run" in line]
                for line in lines:
                    clause = line.split("backend=claude", 1)[-1]
                    self.assertIn("crew-claude.py run", clause)
                    self.assertNotIn("--allowedTools", clause)
                    self.assertNotIn("--model", clause)
                    self.assertNotIn("--timeout", clause)

    def test_cheatsheet_has_usage_line(self):
        text = (SKILL / "references/herdr-cheatsheet.md").read_text(encoding="utf-8")
        self.assertIn("crew-claude.py run", text)


if __name__ == "__main__":
    unittest.main()
