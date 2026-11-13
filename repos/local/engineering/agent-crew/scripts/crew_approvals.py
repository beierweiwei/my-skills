#!/usr/bin/env python3
"""审批等待检测器（guard / crew-msg / dispatch 共用，只读）。

背景：qoder worker 在 yolo 下等待权限对话框时，`herdr agent_status` 仍是
working；guard 会把它算「在跑」，G2「有待办且无在跑」永不成立。等待权限
对话框不会产生 herdr blocked。本模块从 qoder 运行日志读取
`[permission-ask] prompt` 与 `[permission-ask] settle` 行，按 callId 配对，
返回未闭合的等待审批项（含等待年龄），供叫醒/注入前安全检查使用。

数据源（只读）：
  agent（agent_session.value / pane_id / cwd）
  → `~/.qoder-cn/logs/sessions/*/<session>/segments/*.jsonl`（取最新）
  → run_id = 文件名 stem
  → `~/.qoder-cn/logs/runs/<run_id>/qodercli.log`（尾部 ≤1MB）
缺 segment / run 日志时回退：
  `herdr pane process-info --pane <pane>` 取 pid
  → 扫 `~/.qoder-cn/logs/runs/*/manifest.json` 中 pid 匹配（只扫近 7 天目录）。

实现约定：
- 纯只读，不执行任何写操作；不向 pane 发送文本/按键。
- 日志不可读、缺会话、herdr 不可用一律 fail-soft：返回空并记录原因，
  调用方不得因检测失败中断。
- `now` 为 epoch 秒（可注入 clock）；`qoder_home` / `process_info` 可注入，
  便于单测与不同机器布局。
- **claude 工位不支持（T57/R6 过渡口径）**：本模块只解析 qodercli 日志；
  claude（`--kind claude` 交互工位）的权限审批对话框检测不到——
  `pending_approval_for` 对 claude 工位返回空 ≠ 无审批。安全矩阵因此降级：
  claude 工位 prompt 注入前由人工/派单方确认无审批对话框挂起
  （口径见 crew-rules R6 过渡节与 templates/agents/claude 角色提示词）。
  TODO(T57 后置票)：接 claude transcripts（~/.claude/projects/**.jsonl 的
  tool_use permission 请求事件）补齐 claude 工位审批检测数据链。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEFAULT_QODER_HOME = ".qoder-cn"
DEFAULT_TAIL_BYTES = 1024 * 1024
DEFAULT_SCAN_DAYS = 7
PREVIEW_LIMIT = 120
PROCESS_INFO_TIMEOUT = 10.0

PERMISSION_ASK_RE = re.compile(
    r"^(?P<ts>\S+)\s+.*?\[permission-ask\]\s+prompt\s+"
    r"tool=(?P<tool>\S+)\s+id=(?P<call_id>\S+)(?P<rest>.*)$"
)
PERMISSION_SETTLE_RE = re.compile(
    r"^(?P<ts>\S+)\s+.*?\[permission-ask\]\s+settle\s+"
    r"tool=(?P<tool>\S+)\s+id=(?P<call_id>\S+)"
)
MESSAGE_RE = re.compile(r'\bmessage="((?:\\.|[^"\\])*)"')
REDACT_RE = re.compile(
    r"(?i)\b(token|secret|password|passwd|api[_-]?key|authorization)"
    r"(\s*[=:]\s*|\s+)(\S+)"
)


def _note(log: Callable[[str], None] | None, message: str) -> None:
    if log is not None:
        try:
            log(message)
        except Exception:  # noqa: BLE001 - 记录失败不得中断检测
            pass


def qoder_home_path(qoder_home: str | Path | None = None) -> Path:
    if qoder_home is not None:
        return Path(qoder_home).expanduser()
    return Path.home() / DEFAULT_QODER_HOME


def session_value(agent: dict) -> str:
    """agent_session.value（dict 或字符串）；缺失返回空串。"""
    session = agent.get("agent_session") if isinstance(agent, dict) else None
    if isinstance(session, dict):
        value = session.get("value")
    elif isinstance(session, str):
        value = session
    else:
        value = None
    return str(value).strip() if value else ""


def pane_value(agent: dict) -> str:
    if not isinstance(agent, dict):
        return ""
    for key in ("pane_id", "pane"):
        value = agent.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def latest_segment(qoder_home: Path, session: str) -> Path | None:
    """`sessions/*/<session>/segments/*.jsonl` 中文件名最新的一条。"""
    if not session:
        return None
    pattern = f"*/{session}/segments/*.jsonl"
    try:
        matches = list((Path(qoder_home) / "logs" / "sessions").glob(pattern))
    except OSError:
        return None
    if not matches:
        return None
    # run_id 以 ISO 时间戳开头，文件名序即时间序；不用 mtime，避免拷贝后错序。
    return max(matches, key=lambda path: path.name)


def herdr_process_pid(pane: str, timeout: float = PROCESS_INFO_TIMEOUT) -> int | None:
    """`herdr pane process-info --pane <pane>` → 前台进程 pid（失败返回 None）。"""
    if not pane:
        return None
    try:
        proc = subprocess.run(
            ["herdr", "pane", "process-info", "--pane", pane],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
            env={**os.environ, "HERDR_ENV": "1"},
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    result = data.get("result") if isinstance(data, dict) else None
    info = result.get("process_info") if isinstance(result, dict) else None
    if not isinstance(info, dict):
        return None
    processes = info.get("foreground_processes")
    if isinstance(processes, list):
        for entry in processes:
            if not isinstance(entry, dict):
                continue
            try:
                pid = int(entry.get("pid"))
            except (TypeError, ValueError):
                continue
            if pid > 0:
                return pid
    try:
        pid = int(info.get("foreground_process_group_id"))
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def find_run_by_pid(qoder_home: Path, pid: int, now: float,
                    scan_days: int = DEFAULT_SCAN_DAYS) -> Path | None:
    """扫近 `scan_days` 天 run 目录的 manifest.json，按 pid 找 qodercli.log。"""
    runs = Path(qoder_home) / "logs" / "runs"
    cutoff = now - max(1, int(scan_days)) * 86400
    try:
        entries = list(runs.iterdir())
    except OSError:
        return None
    candidates: list[tuple[float, str, Path]] = []
    for entry in entries:
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            continue
        try:
            manifest = json.loads((entry / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        try:
            manifest_pid = int(manifest.get("pid"))
        except (TypeError, ValueError):
            continue
        if manifest_pid != pid:
            continue
        log_path = entry / "qodercli.log"
        if log_path.is_file():
            candidates.append((mtime, entry.name, log_path))
    if not candidates:
        return None
    return max(candidates)[2]


def agent_run_log(
    agent: dict,
    *,
    qoder_home: str | Path | None = None,
    now: float | None = None,
    process_info: Callable[[str], int | None] | None = None,
    scan_days: int = DEFAULT_SCAN_DAYS,
) -> Path | None:
    """agent → 当前 run 的 qodercli.log；映射失败返回 None（fail-soft）。"""
    home = qoder_home_path(qoder_home)
    session = session_value(agent)
    if session:
        segment = latest_segment(home, session)
        if segment is not None:
            run_log = home / "logs" / "runs" / segment.stem / "qodercli.log"
            if run_log.is_file():
                return run_log
    pane = pane_value(agent)
    if not pane:
        return None
    finder = process_info or herdr_process_pid
    try:
        pid = finder(pane)
    except Exception:  # noqa: BLE001 - herdr 故障按无结果处理
        return None
    if not pid:
        return None
    current = now if now is not None else datetime.now(timezone.utc).timestamp()
    return find_run_by_pid(home, int(pid), current, scan_days=scan_days)


def _parse_timestamp(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _preview(text: str) -> str:
    value = text.replace("\\n", " ").replace('\\"', '"')
    value = re.sub(r"\s+", " ", value).strip()
    value = REDACT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", value)
    try:
        value = value.replace(str(Path.home()), "~")
    except (OSError, RuntimeError):
        pass
    if len(value) > PREVIEW_LIMIT:
        value = value[: PREVIEW_LIMIT - 1] + "…"
    return value


def _extract_message(rest: str) -> str:
    match = MESSAGE_RE.search(rest)
    return _preview(match.group(1)) if match else ""


def parse_approval_lines(lines: list[str], now: float) -> list[dict]:
    """按 callId 配对 prompt/settle；返回未闭合项（纯函数，无 I/O）。"""
    open_asks: dict[str, dict] = {}
    for raw in lines:
        line = raw.rstrip("\r\n")
        if "[permission-ask]" not in line:
            continue
        match = PERMISSION_ASK_RE.match(line)
        if match is not None:
            call_id = match.group("call_id")
            if call_id in open_asks:
                continue  # 同一 callId 只保留首次 ask（年龄取最长）
            parsed = _parse_timestamp(match.group("ts"))
            if parsed is None:
                continue
            open_asks[call_id] = {
                "call_id": call_id,
                "asked_dt": parsed,
                "tool": match.group("tool"),
                "command_preview": _extract_message(match.group("rest")),
            }
            continue
        match = PERMISSION_SETTLE_RE.match(line)
        if match is not None:
            open_asks.pop(match.group("call_id"), None)
    results: list[dict] = []
    for call_id, info in open_asks.items():
        asked = info.pop("asked_dt")
        asked_ts = asked.timestamp()
        results.append({
            **info,
            "asked_at": asked.isoformat().replace("+00:00", "Z"),
            "age_seconds": max(0, int(now - asked_ts)),
        })
    results.sort(key=lambda item: (-int(item.get("age_seconds") or 0),
                                   item.get("call_id") or ""))
    return results


def scan_run_log(path: str | Path, now: float,
                 tail_bytes: int = DEFAULT_TAIL_BYTES) -> list[dict]:
    """读日志尾部并解析未闭合审批；不可读返回空列表（fail-soft）。"""
    entries, _reason = read_tail_approvals(path, now, tail_bytes=tail_bytes)
    return entries


def read_tail_approvals(
    path: str | Path,
    now: float,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
) -> tuple[list[dict], str]:
    """返回（未闭合审批, 失败原因）；失败原因为空串表示读取成功。"""
    try:
        handle_path = Path(path)
        size = handle_path.stat().st_size
        offset = max(0, size - max(1, int(tail_bytes)))
        with handle_path.open("rb") as handle:
            handle.seek(offset)
            data = handle.read(max(1, int(tail_bytes)) + (1 if offset else 0))
    except (OSError, ValueError) as exc:
        return [], f"{type(exc).__name__}: {exc}"
    if offset:
        newline = data.find(b"\n")
        data = data[newline + 1:] if newline >= 0 else b""
    text = data.decode("utf-8", "replace")
    return parse_approval_lines(text.splitlines(), now), ""


def pending_approvals(
    agents: list[dict],
    now: float,
    qoder_home: str | Path | None = None,
    *,
    log: Callable[[str], None] | None = None,
    process_info: Callable[[str], int | None] | None = None,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
    scan_days: int = DEFAULT_SCAN_DAYS,
) -> list[dict]:
    """检测 agents 中每个 agent 的未闭合审批；fail-soft，不中断调用方。

    返回项：{agent, pane, call_id, asked_at, age_seconds, tool, command_preview}
    """
    home = qoder_home_path(qoder_home)
    results: list[dict] = []
    for agent in agents or []:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or agent.get("pane_id") or "?").strip()
        path = agent_run_log(agent, qoder_home=home, now=now,
                             process_info=process_info, scan_days=scan_days)
        if path is None:
            _note(log, f"审批检测：{name} 无可用 run 日志（跳过）")
            continue
        entries, reason = read_tail_approvals(path, now, tail_bytes=tail_bytes)
        if reason:
            _note(log, f"审批检测：{name} run 日志不可读（{reason}），跳过")
            continue
        for entry in entries:
            entry["agent"] = str(agent.get("name") or "").strip()
            entry["pane"] = pane_value(agent)
            results.append(entry)
    results.sort(key=lambda item: (-int(item.get("age_seconds") or 0),
                                   item.get("agent") or "",
                                   item.get("call_id") or ""))
    return results


if __name__ == "__main__":  # 人工抽查：python3 scripts/crew_approvals.py <run-log>...
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法: python3 scripts/crew_approvals.py <qodercli.log> [...]")
    current = datetime.now(timezone.utc).timestamp()
    for raw in sys.argv[1:]:
        items = scan_run_log(raw, current)
        print(json.dumps({"log": raw, "pending": items}, ensure_ascii=False))
