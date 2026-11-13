#!/usr/bin/env python3
"""报告写入通道：正文不进 Bash 命令文本（FB-046 / 2.2.21）。

背景：Qoder CLI 1.1.88 的安全检测对「命令文本含 rm/rmdir 词 ∧ 替换计数 tie>64」
强制人工审批（bypassImmune，yolo 与 permission rules 均放不了行）。长报告正文
（Markdown 代码引用密集，反引号成对计入 tie）经 heredoc/echo/printf 落盘会命中
该门并整夜卡住会话。本脚本把正文移出命令文本。

主通道（qoder）：
  python3 scripts/crew-write.py --from-transcript --out 目标路径 [--root 项目根]
  解析链：HERDR_PANE_ID → `herdr agent list` → agent_session.value →
  `~/.qoder-cn/projects/*/<session>.jsonl`；只取最近一次 tool_result 之后的
  assistant text 块，提取最后一个 crew-report 围栏正文，原子落盘。
  调用前在最后一条消息写围栏（三反引号 + crew-report 起，三反引号收；
  正文含三反引号代码块时外层改用四反引号），中间不要再插入其他工具调用。

兜底（有 Write 角色先落文件）：
  python3 scripts/crew-write.py --from-file 项目内源文件 --out 目标路径

自检（测试/工具用；运行时正文仍应走 transcript）：
  python3 scripts/crew-write.py --check-stdin < 命令文本
  stdin 命令文本 tie>40 或含 rm/rmdir 词 → rc=1。

跨 runtime 口径：
  qoder → --from-transcript；
  claude → `crew-claude.py run` stdout 落盘（该脚本已实现一次落盘）；
  opencode / 有 Write 角色 → Write 工具；
  无 Write 且 transcript 不可用 → 分段兜底：单段反引号 ≤40、段内无 rm/rmdir
  词、tie ≤40；禁止拆段/编码规避。

边界：--out 与 --from-file 源必须位于项目根内、非 .git/、防 .. 与 symlink 逃逸；
单文件 ≤2MB；空正文/NUL 拒绝；成功输出路径与字节数。

退出码：0 成功；1 内容/路径/大小拒绝或写失败（写失败 stderr 首行固定
`crew-write: WRITE_FAILED`）；2 用法错误或未找到 crew-report 围栏；
3 环境缺失（herdr 不可用/会话 JSONL 不存在）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

EXIT_REJECT = 1
EXIT_USAGE = 2
EXIT_ENV = 3

MAX_BYTES = 2 * 1024 * 1024
TIE_BUDGET = 40
DEFAULT_PROJECTS_DIR = Path.home() / ".qoder-cn" / "projects"
FENCE_OPEN_RE = re.compile(r"^(?P<ticks>`{3,})crew-report(?:[ \t].*)?$")
FENCE_CLOSE_RE = re.compile(r"^(?P<ticks>`{3,})[ \t]*$")
RM_WORD_RE = re.compile(r"\brm(?:dir)?\b", re.IGNORECASE)
SESSION_RE = re.compile(r"[A-Za-z0-9._-]+")
WRITE_FAIL_MARK = "crew-write: WRITE_FAILED"


class CrewWriteError(Exception):
    """带退出码的失败；main 统一打印并返回。"""

    def __init__(self, message: str, rc: int) -> None:
        super().__init__(message)
        self.rc = rc


def usage_error(message: str) -> CrewWriteError:
    return CrewWriteError(message, EXIT_USAGE)


def reject(message: str) -> CrewWriteError:
    return CrewWriteError(message, EXIT_REJECT)


def env_error(message: str) -> CrewWriteError:
    return CrewWriteError(message, EXIT_ENV)


def tie_count(command: str) -> int:
    """1:1 重演 Qoder CLI 1.1.88 的 tie()。

    反引号成对计 1（Math.ceil(n/2)）；`$(` 计 1（`$((` 不算）；单引号内跳过；
    反斜杠在非单引号内吃掉下一个字符。事故命令 12,761 字符/406 反引号 → 203。
    """
    subs = 0
    backticks = 0
    in_single = False
    in_double = False
    i = 0
    length = len(command)
    while i < length:
        ch = command[i]
        if ch == "\\" and not in_single:
            i += 2
            continue
        if ch == "'" and not in_double:
            in_single = not in_single
            i += 1
            continue
        if ch == '"' and not in_single:
            in_double = not in_double
            i += 1
            continue
        if in_single:
            i += 1
            continue
        if ch == "`":
            backticks += 1
        if (ch == "$" and i + 1 < length and command[i + 1] == "("
                and not (i + 2 < length and command[i + 2] == "(")):
            subs += 1
        i += 1
    return subs + (backticks + 1) // 2


def dangerous_gate(command: str) -> bool:
    """CLI 危险删除闸的第一条件：\brm(?:dir)?\b 词命中。"""
    return RM_WORD_RE.search(command) is not None


def check_stdin_text(command: str) -> tuple[bool, str]:
    """兜底分段自检：tie>40 或含 rm/rmdir 词即拒收。返回 (ok, 说明)。"""
    tie = tie_count(command)
    rm_word = dangerous_gate(command)
    detail = f"tie={tie}（预算 ≤{TIE_BUDGET}）rm_word={rm_word}"
    if tie > TIE_BUDGET or rm_word:
        return False, detail
    return True, detail


def herdr_agent_session(pane_id: str, timeout: float = 10.0) -> str:
    """HERDR_PANE_ID → `herdr agent list` → pane 对应 agent 的 agent_session.value。"""
    pane = str(pane_id or "").strip()
    if not pane:
        raise usage_error("缺少 pane id（设置 HERDR_PANE_ID 或传 --pane-id）")
    try:
        proc = subprocess.run(
            ["herdr", "agent", "list"],
            text=True, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise env_error(f"herdr agent list 调用失败：{exc}") from exc
    if proc.returncode != 0:
        raise env_error(
            f"herdr agent list 退出码 {proc.returncode}：{proc.stderr.strip()}")
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        raise env_error(f"herdr agent list 输出非 JSON：{exc}") from exc
    result = data.get("result") if isinstance(data, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        raise env_error("herdr agent list 缺 result.agents")
    entry = next(
        (item for item in agents
         if isinstance(item, dict) and str(item.get("pane_id") or "") == pane),
        None,
    )
    if entry is None:
        raise env_error(f"pane {pane} 不在本机 herdr agent list")
    session = entry.get("agent_session")
    session_id = session.get("value") if isinstance(session, dict) else None
    if not isinstance(session_id, str) or not session_id.strip():
        raise env_error(f"pane {pane} 的 agent 缺 agent_session.value")
    session_id = session_id.strip()
    if not SESSION_RE.fullmatch(session_id):
        raise env_error(f"agent_session.value 非法：{session_id!r}")
    return session_id


def find_session_file(session: str, projects_dir: Path | None = None) -> Path | None:
    """在 `~/.qoder-cn/projects/*/<session>.jsonl` 中定位会话文件（多命中取最新）。"""
    base = Path(projects_dir) if projects_dir is not None else DEFAULT_PROJECTS_DIR
    if not base.is_dir():
        return None
    candidates = list(base.glob(f"*/{session}.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _jsonl_objects(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def _message_blocks(obj: dict) -> list[dict]:
    message = obj.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    return [block for block in content if isinstance(block, dict)]


def collect_texts_after_last_tool_result(path: Path) -> list[str]:
    """只取最近一次 tool_result 之后的 assistant text 块（旧围栏不复用）。"""
    entries: list[tuple[int, str]] = []
    last_tool_result = -1
    for index, obj in enumerate(_jsonl_objects(path)):
        if not isinstance(obj, dict):
            continue
        blocks = _message_blocks(obj)
        block_types = {block.get("type") for block in blocks}
        if obj.get("type") == "user" and "tool_result" in block_types:
            last_tool_result = index
        if obj.get("type") != "assistant":
            continue
        for block in blocks:
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                entries.append((index, block["text"]))
    return [text for index, text in entries if index > last_tool_result]


def last_crew_report_body(text: str) -> str | None:
    """取最后一个完整 crew-report 围栏正文；未闭合围栏忽略。

    围栏长度感知（CommonMark 口径）：外层用 N 个反引号（N≥3）开启，需 ≥N 个
    反引号的独占行关闭；正文里的短围栏（如三反引号代码块）按正文保留。
    """
    body: str | None = None
    capture: list[str] | None = None
    ticks = 0
    for line in text.splitlines():
        stripped = line.strip()
        if capture is None:
            match = FENCE_OPEN_RE.match(stripped)
            if match:
                capture = []
                ticks = len(match.group("ticks"))
            continue
        match = FENCE_CLOSE_RE.match(stripped)
        if match and len(match.group("ticks")) >= ticks:
            body = "\n".join(capture)
            capture = None
            continue
        capture.append(line)
    return body


def extract_report_from_transcript(path: Path) -> str:
    """会话 JSONL → 最后一个 crew-report 围栏正文（缺围栏 rc=2，空正文 rc=1）。"""
    texts = collect_texts_after_last_tool_result(path)
    body = last_crew_report_body("\n".join(texts))
    if body is None:
        raise usage_error(
            "未找到 crew-report 围栏（在最后一条消息写三反引号 crew-report 围栏，"
            "紧接调用 helper，中间不要插入其他工具调用）")
    if not body.strip():
        raise reject("crew-report 围栏正文为空")
    validate_body(body)
    return body


def validate_body(body: str) -> bytes:
    """空正文/NUL/超 2MB 拒绝；返回 UTF-8 字节。"""
    data = body.encode("utf-8")
    if not body.strip():
        raise reject("正文为空")
    if b"\x00" in data:
        raise reject("正文含 NUL 字节")
    if len(data) > MAX_BYTES:
        raise reject(f"正文 {len(data)} 字节超过上限 {MAX_BYTES}（2MB）")
    return data


def _within_root(path: Path, root: Path, label: str) -> Path:
    root = root.expanduser().resolve()
    resolved = path.expanduser().resolve()
    if resolved != root and root not in resolved.parents:
        raise reject(f"{label} 越界：{resolved} 不在项目根 {root} 内")
    if ".git" in resolved.relative_to(root).parts:
        raise reject(f"{label} 位于 .git 内：{resolved}")
    if resolved.is_dir():
        raise reject(f"{label} 是目录：{resolved}")
    return resolved


def resolve_out(out: str, root: Path) -> Path:
    return _within_root(Path(out), root, "--out")


def read_source_file(source: str, root: Path) -> str:
    path = _within_root(Path(source), root, "--from-file 源文件")
    if not path.is_file():
        raise reject(f"--from-file 源文件不存在：{path}")
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise reject(f"--from-file 源文件不可读：{path}：{exc}") from exc
    if size > MAX_BYTES:
        raise reject(f"--from-file 源文件 {size} 字节超过上限 {MAX_BYTES}（2MB）")
    if size == 0:
        raise reject("--from-file 源文件为空")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise reject(f"--from-file 源文件不可读：{path}：{exc}") from exc
    if b"\x00" in data:
        raise reject("--from-file 源文件含 NUL 字节")
    return data.decode("utf-8", errors="replace")


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """同目录临时文件 + fsync + os.replace；异常时清理临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def _transcript_body(args: argparse.Namespace) -> str:
    pane = args.pane_id or os.environ.get("HERDR_PANE_ID", "")
    session = herdr_agent_session(pane)
    path = find_session_file(session)
    if path is None:
        raise env_error(
            f"未找到会话 JSONL：{DEFAULT_PROJECTS_DIR}/*/{session}.jsonl"
            "（transcript 通道仅支持 qoder runtime）")
    return extract_report_from_transcript(path)


def _run_check_stdin() -> int:
    command = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    ok, detail = check_stdin_text(command)
    if ok:
        print(f"ok: {detail}")
        return 0
    print(
        f"reject: {detail}；正文不要进命令文本：qoder 用 --from-transcript，"
        "有 Write 角色用 Write，其余按分段预算（单段反引号 ≤40、段内无 rm/rmdir 词）",
        file=sys.stderr)
    return EXIT_REJECT


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crew-write.py",
        description="报告写入通道：正文不进 Bash 命令文本（原子落盘 + 路径/大小边界）。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "跨 runtime 口径：\n"
            "  qoder              → --from-transcript（正文写 crew-report 围栏，紧接调用）\n"
            "  claude             → crew-claude.py run stdout 落盘\n"
            "  opencode/有 Write  → Write 工具\n"
            "  无 Write 且 transcript 不可用 → 分段兜底（单段反引号 ≤40、段内无\n"
            "                     rm/rmdir 词、tie ≤40；禁止拆段/编码规避）\n"
            "退出码：0 成功；1 内容/路径/大小拒绝或写失败（写失败 stderr 首行\n"
            f"  `{WRITE_FAIL_MARK}`）；2 用法错误或缺 crew-report 围栏；\n"
            "  3 环境缺失（herdr/会话 JSONL）。\n"
            "示例：python3 scripts/crew-write.py --from-transcript "
            "--out .planning/<task>/evidence/report.md\n"
            "自检：python3 scripts/crew-write.py --check-stdin < command.txt"
        ),
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--from-transcript", action="store_true",
        help="主通道（qoder）：从当前 agent 会话 JSONL 提取 crew-report 围栏正文")
    modes.add_argument(
        "--from-file", metavar="PATH",
        help="兜底：复制项目根内的已有文件（有 Write 角色先落文件）")
    modes.add_argument(
        "--check-stdin", action="store_true",
        help="自检：stdin 读命令文本，tie>40 或含 rm/rmdir 词 → rc=1")
    parser.add_argument("--out", metavar="PATH", help="输出路径（项目根内，非 .git/）")
    parser.add_argument("--root", metavar="DIR", help="项目根（默认当前目录）")
    parser.add_argument(
        "--pane-id", metavar="ID",
        help="目标 pane（默认读 HERDR_PANE_ID；用于 --from-transcript）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.check_stdin:
            return _run_check_stdin()
        if not (args.from_transcript or args.from_file):
            raise usage_error(
                "缺输入通道：--from-transcript / --from-file / --check-stdin 三选一")
        if not args.out:
            raise usage_error("缺 --out 输出路径")
        root = Path(args.root).expanduser() if args.root else Path.cwd()
        out = resolve_out(args.out, root)
        if args.from_file:
            body = read_source_file(args.from_file, root)
        else:
            body = _transcript_body(args)
        data = validate_body(body)
        atomic_write_bytes(out, data)
        print(f"written: {out} ({len(data)} bytes)")
        return 0
    except CrewWriteError as exc:
        print(f"crew-write: {exc}", file=sys.stderr)
        return exc.rc
    except OSError as exc:
        print(WRITE_FAIL_MARK, file=sys.stderr)
        print(f"crew-write: 写入失败：{exc}", file=sys.stderr)
        return EXIT_REJECT


if __name__ == "__main__":
    raise SystemExit(main())
