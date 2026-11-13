#!/usr/bin/env python3
"""2.2.21 AD 报告写入通道回归（FB-046）。

覆盖：
- 事故夹具 12,761 字符 / 406 反引号 → tie=203、dangerous_gate=True、--check-stdin rc=1；
- 40 反引号无 rm 词通过；60 反引号夹 rm 拒收；tie>40 无 rm 词拒收；
- transcript 提取：只取最近一次 tool_result 之后、多围栏取最后、旧围栏不复用、
  缺围栏 rc=2、空正文/NUL 拒收、写盘字节与围栏正文一致；
- 路径边界：项目根外、.git/、.. 逃逸、symlink 逃逸、--from-file 源根外（5 类负向）；
- 大小边界：源 >2MB、空文件、NUL 拒收；
- 模板防回退：旧「单引号 heredoc 写 /tmp 再 cp」文案 0 处、新条款 9 文件 10 处、
  opencode reviewer bash 放行 crew-write.py。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

SKILL = Path(__file__).resolve().parent.parent.parent
SCRIPT = SKILL / "scripts" / "crew-write.py"
TEMPLATES = SKILL / "templates"

_spec = importlib.util.spec_from_file_location("crew_write_2221", SCRIPT)
cw = importlib.util.module_from_spec(_spec)
sys.modules["crew_write_2221"] = cw
_spec.loader.exec_module(cw)

ACCIDENT_LEN = 12761
ACCIDENT_BACKTICKS = 406

CLOSING_FENCE = "```"


def accident_command() -> str:
    """构造与事故等长的命令：12,761 字符、406 反引号、含 rm -rf 词。"""
    prefix = "cat > /tmp/p0c-review-report.md <<'EOF'\n"
    rm_line = "rm -rf /tmp/p0c-review && mkdir -p /tmp/p0c-review\n"
    codes = "".join(f"`c{i}` " for i in range(ACCIDENT_BACKTICKS // 2))
    suffix = (
        "EOF\ncp /tmp/p0c-review-report.md /x/report.md && "
        "wc -l /x/report.md && head -15 /x/report.md\n"
    )
    base = prefix + rm_line + codes + "\n" + suffix
    return base + "x" * (ACCIDENT_LEN - len(base))


def fence(body: str, ticks: int = 3) -> str:
    marks = "`" * ticks
    return f"{marks}crew-report\n{body}\n{marks}"


def assistant_text(text: str) -> dict:
    return {
        "type": "assistant",
        "message": {"role": "assistant", "content": [{"type": "text", "text": text}]},
    }


def assistant_tool_use(name: str = "Bash") -> dict:
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "call-1", "name": name, "input": {}}],
        },
    }


def user_tool_result() -> dict:
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}
            ],
        },
    }


def write_jsonl(path: Path, rows: list[dict]) -> Path:
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    return path


def run_main(argv: list[str]) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cw.main(argv)
    return rc, out.getvalue(), err.getvalue()


class TieReplayTest(unittest.TestCase):
    """1:1 重演 CLI tie 与危险删除闸的第一条件。"""

    def test_accident_fixture_matches_tie_203(self):
        command = accident_command()
        self.assertEqual(ACCIDENT_LEN, len(command))
        self.assertEqual(ACCIDENT_BACKTICKS, command.count("`"))
        self.assertEqual(203, cw.tie_count(command))
        self.assertTrue(cw.dangerous_gate(command))
        ok, detail = cw.check_stdin_text(command)
        self.assertFalse(ok, detail)

    def test_accident_fixture_check_stdin_cli_rc1(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--check-stdin"],
            input=accident_command(), text=True, capture_output=True,
        )
        self.assertEqual(1, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn("tie=203", proc.stderr)

    def test_forty_backticks_without_rm_passes(self):
        command = "echo " + "`" * 40
        self.assertEqual(20, cw.tie_count(command))
        self.assertFalse(cw.dangerous_gate(command))
        ok, _ = cw.check_stdin_text(command)
        self.assertTrue(ok)
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--check-stdin"],
            input=command, text=True, capture_output=True,
        )
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)

    def test_sixty_backticks_with_rm_rejected(self):
        command = "echo " + "`" * 60 + " rm -rf /tmp/x"
        self.assertEqual(30, cw.tie_count(command))
        self.assertTrue(cw.dangerous_gate(command))
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--check-stdin"],
            input=command, text=True, capture_output=True,
        )
        self.assertEqual(1, proc.returncode)
        self.assertIn("rm_word=True", proc.stderr)

    def test_tie_over_budget_without_rm_rejected(self):
        command = "echo " + "`" * 82  # tie=41 > 40
        self.assertEqual(41, cw.tie_count(command))
        self.assertFalse(cw.dangerous_gate(command))
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--check-stdin"],
            input=command, text=True, capture_output=True,
        )
        self.assertEqual(1, proc.returncode)

    def test_tie_matches_cli_quote_and_dollar_rules(self):
        # 单引号内跳过；$(( 不算；$() 计 1；反引号向上取整。
        self.assertEqual(0, cw.tie_count("echo '`rm -rf /`'"))
        self.assertEqual(0, cw.tie_count("echo $((1+1))"))
        self.assertEqual(1, cw.tie_count("echo $(date)"))
        self.assertEqual(1, cw.tie_count("echo `x"))


class TranscriptExtractionTest(unittest.TestCase):
    """会话 JSONL → 最后一个 crew-report 围栏正文。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def extract(self, rows: list[dict]) -> str:
        path = write_jsonl(self.dir / "session.jsonl", rows)
        return cw.extract_report_from_transcript(path)

    def test_takes_last_fence(self):
        body = self.extract([
            assistant_text(fence("first body")),
            user_tool_result(),
            assistant_text("中间说明"),
            assistant_text(fence("last body\nwith `inline`")),
            assistant_tool_use(),
        ])
        self.assertEqual("last body\nwith `inline`", body)

    def test_old_fence_before_last_tool_result_ignored(self):
        path = write_jsonl(self.dir / "old.jsonl", [
            assistant_text(fence("old body")),
            user_tool_result(),
            assistant_text("只有说明，没有新围栏"),
            assistant_tool_use(),
        ])
        with self.assertRaises(cw.CrewWriteError) as caught:
            cw.extract_report_from_transcript(path)
        self.assertEqual(2, caught.exception.rc)
        self.assertIn("crew-report", str(caught.exception))

    def test_missing_fence_rc2(self):
        path = write_jsonl(self.dir / "plain.jsonl", [assistant_text("plain text")])
        with self.assertRaises(cw.CrewWriteError) as caught:
            cw.extract_report_from_transcript(path)
        self.assertEqual(2, caught.exception.rc)

    def test_empty_body_rejected(self):
        path = write_jsonl(self.dir / "empty.jsonl", [
            assistant_text(fence("")),
            assistant_tool_use(),
        ])
        with self.assertRaises(cw.CrewWriteError) as caught:
            cw.extract_report_from_transcript(path)
        self.assertEqual(1, caught.exception.rc)

    def test_nul_rejected(self):
        path = write_jsonl(self.dir / "nul.jsonl", [
            assistant_text(fence("bad\x00body")),
            assistant_tool_use(),
        ])
        with self.assertRaises(cw.CrewWriteError) as caught:
            cw.extract_report_from_transcript(path)
        self.assertEqual(1, caught.exception.rc)

    def test_four_backtick_outer_fence_keeps_inner_code_block(self):
        body = "code:\n```\necho hi\n```\nend"
        self.assertEqual(body, self.extract([
            assistant_text(fence(body, ticks=4)),
            assistant_tool_use(),
        ]))

    def test_unclosed_fence_ignored(self):
        path = write_jsonl(self.dir / "unclosed.jsonl", [
            assistant_text("```crew-report\nno closing fence"),
            assistant_tool_use(),
        ])
        with self.assertRaises(cw.CrewWriteError) as caught:
            cw.extract_report_from_transcript(path)
        self.assertEqual(2, caught.exception.rc)


class FromTranscriptCliTest(unittest.TestCase):
    """--from-transcript 主通道：写盘字节与围栏正文一致。"""

    def test_writes_exact_fence_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "root"
            root.mkdir()
            rows = [
                assistant_text(fence("旧正文")),
                user_tool_result(),
                assistant_text("现在落盘"),
                assistant_text(fence("正文 line 1\n`code` line 2")),
                assistant_tool_use("Bash"),
            ]
            session = write_jsonl(Path(tmp) / "sess.jsonl", rows)
            out = root / "evidence" / "report.md"
            with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p9"}), \
                    mock.patch.object(cw, "herdr_agent_session", return_value="sess-1"), \
                    mock.patch.object(cw, "find_session_file", return_value=session):
                rc, stdout, stderr = run_main(
                    ["--from-transcript", "--out", str(out), "--root", str(root)])
            self.assertEqual(0, rc, stderr)
            expected = "正文 line 1\n`code` line 2".encode("utf-8")
            self.assertEqual(expected, out.read_bytes())
            self.assertIn(f"{len(expected)} bytes", stdout)
            self.assertEqual([], list(out.parent.glob("*.tmp")))

    def test_missing_pane_env_rc2_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {}, clear=True):
                rc, _, err = run_main(
                    ["--from-transcript", "--out", str(root / "x.md"),
                     "--root", str(root)])
            self.assertEqual(2, rc, err)

    def test_herdr_lookup_missing_session_rc3(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.dict(os.environ, {"HERDR_PANE_ID": "w1:p9"}), \
                    mock.patch.object(cw, "herdr_agent_session", return_value="nope"), \
                    mock.patch.object(cw, "find_session_file", return_value=None):
                rc, _, err = run_main(
                    ["--from-transcript", "--out", str(root / "x.md"),
                     "--root", str(root)])
            self.assertEqual(3, rc, err)


class HerdrLookupTest(unittest.TestCase):
    """HERDR_PANE_ID → herdr agent list → agent_session.value。"""

    def _run_with(self, payload: dict):
        return mock.Mock(returncode=0, stdout=json.dumps(payload), stderr="")

    def test_finds_session_by_pane(self):
        payload = {"result": {"agents": [
            {"pane_id": "w1:p1", "agent": "qodercli",
             "agent_session": {"value": "sess-a"}},
            {"pane_id": "w1:p2", "agent": "qodercli",
             "agent_session": {"value": "sess-b"}},
        ]}}
        with mock.patch.object(cw.subprocess, "run", return_value=self._run_with(payload)):
            self.assertEqual("sess-b", cw.herdr_agent_session("w1:p2"))

    def test_missing_pane_is_env_error(self):
        payload = {"result": {"agents": []}}
        with mock.patch.object(cw.subprocess, "run", return_value=self._run_with(payload)):
            with self.assertRaises(cw.CrewWriteError) as caught:
                cw.herdr_agent_session("w1:p9")
        self.assertEqual(3, caught.exception.rc)

    def test_herdr_failure_is_env_error(self):
        failed = mock.Mock(returncode=1, stdout="", stderr="down")
        with mock.patch.object(cw.subprocess, "run", return_value=failed):
            with self.assertRaises(cw.CrewWriteError) as caught:
                cw.herdr_agent_session("w1:p9")
        self.assertEqual(3, caught.exception.rc)

    def test_find_session_file_prefers_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "proj-a").mkdir()
            (base / "proj-b").mkdir()
            older = base / "proj-a" / "sess.jsonl"
            newer = base / "proj-b" / "sess.jsonl"
            older.write_text("{}\n", encoding="utf-8")
            newer.write_text("{}\n", encoding="utf-8")
            os.utime(older, (1000, 1000))
            os.utime(newer, (2000, 2000))
            self.assertEqual(newer, cw.find_session_file("sess", base))


class PathBoundaryTest(unittest.TestCase):
    """--out / --from-file 源的项目根边界（5 类负向）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.source = self.root / "source.md"
        self.source.write_text("源内容\n", encoding="utf-8")

    def copy(self, out: Path) -> tuple[int, str, str]:
        return run_main(
            ["--from-file", str(self.source), "--out", str(out), "--root", str(self.root)])

    def test_out_outside_root_rejected(self):
        out = self.base / "outside.md"
        rc, _, err = self.copy(out)
        self.assertEqual(1, rc, err)
        self.assertFalse(out.exists())

    def test_out_in_git_rejected(self):
        out = self.root / ".git" / "report.md"
        rc, _, err = self.copy(out)
        self.assertEqual(1, rc, err)
        self.assertFalse(out.exists())

    def test_dotdot_escape_rejected(self):
        out = self.root / "sub" / ".." / ".." / "escape.md"
        rc, _, err = self.copy(out)
        self.assertEqual(1, rc, err)
        self.assertFalse((self.base / "escape.md").exists())

    def test_symlink_escape_rejected(self):
        outside_dir = self.base / "outside-dir"
        outside_dir.mkdir()
        link = self.root / "link"
        link.symlink_to(outside_dir, target_is_directory=True)
        out = link / "report.md"
        rc, _, err = self.copy(out)
        self.assertEqual(1, rc, err)
        self.assertFalse((outside_dir / "report.md").exists())

    def test_source_outside_root_rejected(self):
        outside_source = self.base / "outside-source.md"
        outside_source.write_text("x", encoding="utf-8")
        rc, _, err = run_main(
            ["--from-file", str(outside_source), "--out", str(self.root / "out.md"),
             "--root", str(self.root)])
        self.assertEqual(1, rc, err)


class FromFileTest(unittest.TestCase):
    """--from-file 兜底：大小/空/NUL 与成功复制。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def run_copy(self, source: Path, out_name: str = "out.md") -> tuple[int, str, str]:
        return run_main(
            ["--from-file", str(source), "--out", str(self.root / out_name),
             "--root", str(self.root)])

    def test_success_copies_bytes(self):
        source = self.root / "src.md"
        payload = "报告正文\n`code`\n".encode("utf-8")
        source.write_bytes(payload)
        out = self.root / "out.md"
        rc, stdout, err = self.run_copy(source)
        self.assertEqual(0, rc, err)
        self.assertEqual(payload, out.read_bytes())
        self.assertIn(f"{len(payload)} bytes", stdout)

    def test_over_two_megabytes_rejected(self):
        source = self.root / "big.md"
        source.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
        rc, _, err = self.run_copy(source)
        self.assertEqual(1, rc, err)
        self.assertIn("2MB", err)
        self.assertFalse((self.root / "out.md").exists())

    def test_empty_rejected(self):
        source = self.root / "empty.md"
        source.write_bytes(b"")
        rc, _, err = self.run_copy(source)
        self.assertEqual(1, rc, err)

    def test_nul_rejected(self):
        source = self.root / "nul.md"
        source.write_bytes(b"a\x00b")
        rc, _, err = self.run_copy(source)
        self.assertEqual(1, rc, err)
        self.assertIn("NUL", err)


class TemplateClauseTest(unittest.TestCase):
    """模板防回退：旧明文 0 处、新条款 9 文件 10 处。"""

    TARGETS = (
        "templates/playbooks/release.md",
        "templates/playbooks/maintain-verification.md",
        "templates/_templates/ticket.md",
        "templates/agents/qoder/firstmate.md",
        "templates/agents/qoder/reviewer.md",
        "templates/agents/qoder/runner.md",
        "templates/agents/opencode/firstmate.md",
        "templates/agents/opencode/reviewer.md",
        "templates/agents/opencode/runner.md",
    )
    OLD_PHRASES = (
        "单引号 heredoc 写 `/tmp` 再 `cp`",
        "禁止无引号 heredoc/命令替换",
    )
    NEW_SIGNATURES = (
        "禁止正文进 Bash 命令文本",
        "crew-write.py --from-transcript",
        "审批等待不可自救",
    )

    def read(self, rel: str) -> str:
        return (SKILL / rel).read_text(encoding="utf-8")

    def test_old_wording_removed_everywhere(self):
        for path in TEMPLATES.rglob("*.md"):
            text = path.read_text(encoding="utf-8")
            for phrase in self.OLD_PHRASES:
                with self.subTest(path=path.relative_to(SKILL).as_posix(), phrase=phrase):
                    self.assertNotIn(phrase, text)

    def test_new_clause_in_all_targets(self):
        for rel in self.TARGETS:
            text = self.read(rel)
            for signature in self.NEW_SIGNATURES:
                with self.subTest(rel=rel, signature=signature):
                    self.assertIn(signature, text, f"{rel} 缺少新落盘条款")

    def test_release_has_two_places(self):
        text = self.read("templates/playbooks/release.md")
        self.assertEqual(2, text.count("禁止正文进 Bash 命令文本"))

    def test_opencode_reviewer_allows_crew_write(self):
        text = self.read("templates/agents/opencode/reviewer.md")
        self.assertIn('"python3 scripts/crew-write.py*": allow', text)


class HelpTest(unittest.TestCase):
    """--help 写清跨 runtime 口径。"""

    def test_help_contains_cross_runtime_guidance(self):
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            text=True, capture_output=True,
        )
        self.assertEqual(0, proc.returncode, proc.stderr)
        # argparse 按终端宽度换行，可能把 `crew-\nclaude.py` 折开；去换行后再核对。
        compact = proc.stdout.replace("\n", "")
        for token in ("--from-transcript", "crew-claude.py", "Write",
                      "分段兜底", "反引号 ≤40", "rm/rmdir", "--check-stdin"):
            with self.subTest(token=token):
                self.assertIn(token, compact)


if __name__ == "__main__":
    unittest.main()
