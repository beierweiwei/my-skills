#!/usr/bin/env python3
"""证据采集器：一条命令执行校验并把六项证据原子落盘。

用法：
  python3 scripts/crew-evidence.py --out <file> [--root <dir>] [--timeout <秒>] -- <命令...>

证据文件字段（固定标记行，可机械定位）：
  time: <ISO8601 Z>                 执行时间
  cwd: <绝对路径>                    命令执行目录（--root，默认当前目录）
  command: <完整命令>                 argv 经 shlex 拼接，可直接复跑
  rc: <int>                          被采命令退出码（信号终止折算 128+signal；超时 124）
  --- stdout ---                     被采命令 stdout 原文
  --- stderr ---                     被采命令 stderr 原文（超时追加固定说明）
  --- git status --porcelain ---     cwd 的 git 工作区快照（git 不可用时写诊断）

超时（S9）：
  `--timeout <秒>` 到点终止被采命令（独立进程组 SIGKILL，含其子进程）；证据仍落盘，
  rc=124，stderr 段追加超时说明；不设该参数＝等待命令自然结束（默认行为保持不变）。

退出码：正常镜像被采命令 rc（超时 124）；写失败恒 1
（stderr 首行固定标记 `crew-evidence: WRITE_FAILED`，与被采命令 rc 区分）；2 用法错误。
原子写：同目录临时文件 + fsync + os.replace；失败清理临时文件，不留半文件。
缺 --out、缺命令：打印用法与原因，rc=2，不产生证据文件。
"""

from __future__ import annotations

import argparse
import os
import shlex
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True

EXIT_TIMEOUT = 124
EXIT_WRITE_FAILED = 1
EXIT_USAGE = 2

STDOUT_MARK = "--- stdout ---"
STDERR_MARK = "--- stderr ---"
GIT_MARK = "--- git status --porcelain ---"
WRITE_FAIL_MARK = "crew-evidence: WRITE_FAILED"
TIMEOUT_MARK = "[crew-evidence] 超时"


def _write_failed(target: Path, message: str) -> int:
    """写失败：stderr 首行固定标记（机器可区分于被采命令 rc），恒返回 1。"""
    print(WRITE_FAIL_MARK, file=sys.stderr, flush=True)
    print(f"evidence 写入失败：{target}：{message}", file=sys.stderr, flush=True)
    return EXIT_WRITE_FAILED


def utc_now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def atomic_write_text(path: Path, text: str) -> None:
    """同目录临时文件 + fsync + os.replace；异常时清理临时文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def git_status_snapshot(root: Path) -> str:
    """git status --porcelain 原文；git 不可用/失败时返回单行诊断，不抛异常。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            text=True, capture_output=True, check=False, timeout=30,
            encoding="utf-8", errors="replace",
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"(git status 不可用：{exc})"
    if proc.returncode != 0:
        return (f"(git status 不可用：rc={proc.returncode}；"
                f"{proc.stderr.strip()})")
    return proc.stdout


def _section(text: str) -> str:
    """段落原文；保证段尾换行使下一个标记独占一行。"""
    if not text:
        return ""
    return text if text.endswith("\n") else text + "\n"


def render_evidence(*, when: str, cwd: Path, command: list[str], rc: int,
                    stdout: str, stderr: str, git_status: str) -> str:
    return (
        "# crew-evidence v1\n"
        f"time: {when}\n"
        f"cwd: {cwd}\n"
        f"command: {shlex.join(command)}\n"
        f"rc: {rc}\n"
        f"{STDOUT_MARK}\n{_section(stdout)}"
        f"{STDERR_MARK}\n{_section(stderr)}"
        f"{GIT_MARK}\n{_section(git_status)}"
    )


def _run_command(command: list[str], cwd: Path,
                 timeout: float | None) -> tuple[int, str, str]:
    """执行被采命令；无超时保持 run() 原行为，有超时用独立进程组并在到点整组终止。"""
    if timeout is None:
        proc = subprocess.run(
            command, cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", check=False,
        )
        rc = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
        return rc, proc.stdout, proc.stderr
    proc = subprocess.Popen(
        command, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", start_new_session=True,
    )
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                proc.kill()
            except OSError:
                pass
        stdout, stderr = proc.communicate()
        note = (f"{TIMEOUT_MARK}：命令在 {timeout:g}s 内未结束，"
                f"已终止进程组（rc={EXIT_TIMEOUT}）")
        stderr = (stderr + "\n" if stderr else "") + note
        return EXIT_TIMEOUT, stdout, stderr
    rc = proc.returncode if proc.returncode >= 0 else 128 - proc.returncode
    return rc, stdout, stderr


def collect(out: Path, root: Path, command: list[str],
            timeout: float | None = None) -> int:
    cwd = root.expanduser().resolve()
    try:
        rc, stdout, stderr = _run_command(command, cwd, timeout)
    except OSError as exc:
        rc, stdout, stderr = 127, "", f"命令无法执行：{exc}"
    text = render_evidence(
        when=utc_now(), cwd=cwd, command=command, rc=rc,
        stdout=stdout, stderr=stderr, git_status=git_status_snapshot(cwd),
    )
    target = out.expanduser()
    try:
        atomic_write_text(target, text)
        if not target.is_file() or target.stat().st_size == 0:
            return _write_failed(target, "写入后产物为空")
    except OSError as exc:
        return _write_failed(target, str(exc))
    print(f"evidence: {target}（rc={rc}）")
    return rc


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"无效秒数：{value}") from exc
    if seconds <= 0:
        raise argparse.ArgumentTypeError("超时秒数必须 > 0")
    return seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crew-evidence.py",
        description="执行校验命令并把命令/rc/stdout/stderr/时间/git status 原子落盘。",
        epilog=(
            "退出码：正常镜像被采命令 rc（超时 124）；写失败恒 1 且 stderr 首行为 "
            f"`{WRITE_FAIL_MARK}`；用法错误 2。\n"
            "超时：--timeout <秒> 到点终止被采命令（独立进程组）；证据仍落盘并注明"
            "超时；不设则等待命令自然结束。\n"
            "示例：python3 scripts/crew-evidence.py --out /tmp/doctor.txt -- "
            "python3 scripts/doctor.py --only runtime.guard"
        ),
    )
    parser.add_argument("--out", required=True, help="证据文件路径（父目录自动创建）")
    parser.add_argument("--root", help="命令执行目录与 git 根（默认当前目录）")
    parser.add_argument(
        "--timeout", type=_positive_seconds, default=None,
        help="被采命令超时秒数（默认不设超时；到点终止进程组，证据仍落盘，rc=124）")
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="-- 之后的被采命令（argv 原样透传）")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        parser.print_usage(sys.stderr)
        print(
            "crew-evidence: 缺少被采命令（用法：--out <file> -- <命令...>）",
            file=sys.stderr)
        return EXIT_USAGE
    root = Path(args.root).expanduser() if args.root else Path.cwd()
    return collect(Path(args.out), root, command, timeout=args.timeout)


if __name__ == "__main__":
    raise SystemExit(main())
