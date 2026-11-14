#!/usr/bin/env python3
"""2.2.32 工单 B/T78/FB-060：crew-claude.py run 报告完整性校验。

覆盖：
- fake claude 三态（空 / 过短 / 正常）：空与过短 → 失败返回（可重派）、
  report_ok=false、meta 带原因列表、报告文件仍落盘不删；正常 → rc=0、
  report_ok=true、report_lines 与报告一致、stdout 摘要含 report_ok/行数/字节。
- 阈值口径：CLI --min-report-lines/--min-report-bytes 覆盖（0=关闭该项）、
  配置 reportCheck.{minLines,minBytes} 读取、CLI 显式值优先于配置。
- 关键段：`## ` 标题或首行行数/字节计法二选一即过；两者皆无 → 拒绝。
- rc≠0/超时语义不变：主因仍列（problems 同时含 rc 与报告问题）。
- 模板静态面：qoder/claude/opencode 三栈 scout/runner 报告首行计数与
  R1 回执同值要求（grep 断言）。
fixture 全部为中性占位内容。
"""

from __future__ import annotations

import importlib.util
import json
import os
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
    "crew_claude_t78", _SCRIPTS / "crew-claude.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["crew_claude_t78"] = cc
_spec.loader.exec_module(cc)

# —— fake claude：CC_MODE 控制输出形态 ——

STUB = textwrap.dedent(
    '''\
    #!/usr/bin/env python3
    import os, sys

    sys.stdin.read()
    mode = os.environ.get("CC_MODE", "normal")
    if mode == "empty":
        sys.exit(0)
    if mode == "short":
        sys.stdout.write("## 摘\\n短结论。\\n")
        sys.exit(0)
    if mode == "plain":
        sys.stdout.write("纯文本无标题甲。\\n纯文本无标题乙。\\n纯文本无标题丙。\\n"
                         "纯文本无标题丁。\\n纯文本无标题戊。\\n纯文本无标题己。\\n"
                         "纯文本无标题庚。\\n纯文本无标题辛。\\n")
        sys.exit(0)
    if mode == "firstline":
        sys.stdout.write("lines=6 bytes=240 报告首行计数\\n"
                         "正文甲：补充说明与口径的长正文行，用于抬高字节数。\\n"
                         "正文乙：补充说明与口径的长正文行，用于抬高字节数。\\n"
                         "正文丙：补充说明与口径的长正文行，用于抬高字节数。\\n"
                         "正文丁：补充说明与口径的长正文行，用于抬高字节数。\\n"
                         "正文戊：补充说明与口径的长正文行，用于抬高字节数。\\n")
        sys.exit(0)
    sys.stdout.write(
        "## 结论\\n"
        "调研结论：能力可用（置信度高）。\\n"
        "证据一：官方文档明确支持该能力，附接入说明。\\n"
        "证据二：社区实测案例与限制清单完整。\\n"
        "证据三：替代方案对比与费用口径齐备。\\n"
        "## 来源\\n"
        "- 官方文档（访问于 2026-11-14）。\\n")
    ''')

NORMAL_REPORT = (
    "## 结论\n"
    "调研结论：能力可用（置信度高）。\n"
    "证据一：官方文档明确支持该能力，附接入说明。\n"
    "证据二：社区实测案例与限制清单完整。\n"
    "证据三：替代方案对比与费用口径齐备。\n"
    "## 来源\n"
    "- 官方文档（访问于 2026-11-14）。\n"
)
NORMAL_LINES = 7  # 非空行计数


def agents_claude() -> dict:
    return {"default": {"backend": "claude", "model": "stub-model"},
            "roles": {}, "playbooks": {}}


class ReportIntegrityTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "proj"
        self.out = self.base / "evidence"
        self.prompt = self.base / "prompt.md"
        self.prompt.write_text("只读任务：输出报告。\n", encoding="utf-8")
        self.stub = self.base / "claude-stub"
        self.stub.write_text(STUB, encoding="utf-8")
        self.stub.chmod(0o755)
        self.config = self.root / ".planning/orchestration.json"
        self._write_config({"agents": agents_claude()})

    def _write_config(self, payload: dict) -> None:
        self.config.parent.mkdir(parents=True, exist_ok=True)
        self.config.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def _run(self, *extra: str, mode: str = "normal",
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
        out, err = StringIO(), StringIO()
        with mock.patch.dict(os.environ, {"CC_MODE": mode}, clear=False), \
                redirect_stdout(out), redirect_stderr(err):
            code = cc.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _meta(self, name: str = "case") -> dict:
        return json.loads((self.out / f"{name}.meta.json").read_text(encoding="utf-8"))

    # ---------- 纯函数 ----------

    def test_check_report_pure_function_boundaries(self):
        ok, lines, problems = cc.check_report(
            b"", min_lines=5, min_bytes=200)
        self.assertFalse(ok)
        self.assertEqual(0, lines)
        self.assertEqual(problems, ["报告为空（stdout 0 字节）"])
        # 阈值 0=关闭该项：只剩关键段判定
        ok, lines, problems = cc.check_report("甲\n".encode("utf-8") * 1,
                                              min_lines=0, min_bytes=0)
        self.assertFalse(ok)  # 单行无 ## 也无首行计数 → 关键段失败
        self.assertIn("关键段", problems[0])
        ok, _lines, problems = cc.check_report("## 甲\n".encode("utf-8"),
                                               min_lines=0, min_bytes=0)
        self.assertTrue(ok)
        self.assertEqual(problems, [])

    # ---------- 三态 ----------

    def test_empty_report_rejected_but_kept_on_disk(self):
        code, out, err = self._run(mode="empty")
        self.assertEqual(1, code, out + err)
        self.assertIn("报告为空", err)
        report = self.out / "case.md"
        self.assertTrue(report.is_file())
        self.assertEqual(report.read_bytes(), b"")  # 落盘保留，不删
        meta = self._meta()
        self.assertFalse(meta["report_ok"])
        self.assertEqual(meta["report_lines"], 0)
        self.assertTrue(any("为空" in p for p in meta["report_problems"]))
        self.assertIn("report_ok=false", out)
        self.assertIn("report_lines=0", out)

    def test_short_report_rejected_with_line_and_byte_reasons(self):
        code, out, err = self._run(mode="short")
        self.assertEqual(1, code, out + err)
        self.assertIn("非空行 2 < 最小 5", err)
        self.assertIn("字节", err)
        meta = self._meta()
        self.assertFalse(meta["report_ok"])
        self.assertEqual(meta["report_lines"], 2)
        self.assertEqual(len(meta["report_problems"]), 2)  # 行数 + 字节（含 ## 过关键段）

    def test_normal_report_ok_with_meta_counts(self):
        code, out, err = self._run(mode="normal")
        self.assertEqual(0, code, out + err)
        meta = self._meta()
        self.assertTrue(meta["report_ok"])
        self.assertEqual(meta["report_lines"], NORMAL_LINES)
        self.assertEqual(meta["bytes"], len(NORMAL_REPORT.encode("utf-8")))
        self.assertNotIn("report_problems", meta)
        self.assertIn("report_ok=true", out)
        self.assertIn(f"report_lines={NORMAL_LINES}", out)
        self.assertIn(f"bytes={len(NORMAL_REPORT.encode('utf-8'))}", out)

    # ---------- 关键段二选一 ----------

    def test_plain_report_without_heading_or_count_rejected(self):
        code, _out, err = self._run(mode="plain")
        self.assertEqual(1, code, err)
        self.assertIn("关键段", err)

    def test_firstline_count_passes_without_heading(self):
        code, out, err = self._run(mode="firstline")
        self.assertEqual(0, code, out + err)
        self.assertTrue(self._meta()["report_ok"])

    # ---------- 阈值覆盖与配置读取 ----------

    def test_cli_threshold_override_relaxes_short_report(self):
        code, out, err = self._run("--min-report-lines", "1",
                                   "--min-report-bytes", "1", mode="short")
        self.assertEqual(0, code, out + err)  # 含 ## 过关键段，阈值放开后通过

    def test_cli_zero_disables_thresholds_but_empty_still_fails(self):
        code, _out, err = self._run("--min-report-lines", "0",
                                    "--min-report-bytes", "0", mode="empty")
        self.assertEqual(1, code, err)
        self.assertIn("报告为空", err)

    def test_config_reportcheck_read_and_cli_overrides_it(self):
        self._write_config({"agents": agents_claude(),
                            "reportCheck": {"minLines": 99, "minBytes": 99}})
        code, _out, err = self._run(mode="normal")  # 配置收紧 → 正常报告被拒
        self.assertEqual(1, code, err)
        self.assertIn("非空行 7 < 最小 99", err)
        code, out, err = self._run("--min-report-lines", "5",
                                   "--min-report-bytes", "200", mode="normal")
        self.assertEqual(0, code, out + err)  # CLI 显式值优先于配置

    # ---------- rc≠0 / 超时语义不变 ----------

    def test_nonzero_rc_keeps_primary_reason_plus_report_problem(self):
        stub = self.base / "claude-fail"
        stub.write_text(textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            import sys
            sys.stdin.read()
            sys.stderr.write("boom-stderr\\n")
            sys.exit(7)
            '''), encoding="utf-8")
        stub.chmod(0o755)
        argv = ["run", "--role", "adversary", "--prompt", str(self.prompt),
                "--out-dir", str(self.out), "--name", "fail",
                "--config", str(self.config), "--claude-bin", str(stub)]
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cc.main(argv)
        self.assertEqual(7, code)  # 主因退出码不被报告校验覆盖
        self.assertIn("rc=7", err.getvalue())
        self.assertIn("报告为空", err.getvalue())  # problems 同时列，不吞主因

    # ---------- 模板静态面（三栈首行计数） ----------

    def test_scout_runner_templates_require_first_line_counts(self):
        templates = [
            SKILL / f"templates/agents/{stack}/{role}.md"
            for stack in ("qoder", "claude", "opencode")
            for role in ("scout", "runner")
        ]
        self.assertEqual(len(templates), 6)
        for path in templates:
            with self.subTest(template=path.relative_to(SKILL).as_posix()):
                text = path.read_text(encoding="utf-8")
                self.assertIn("行数: N 字节: M", text)
                self.assertIn("R1 回执", text)
                self.assertIn("不一致=未完成", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
