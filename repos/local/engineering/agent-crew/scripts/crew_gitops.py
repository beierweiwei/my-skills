#!/usr/bin/env python3
"""crew_gitops.py — guard 破坏性 git 操作观测（2.2.33/A4/FB-063；D7 快审 PASS 条件放行）。

S3 主信号（claude session JSONL 增量）+ S1 兜底（.git/logs/HEAD reflog 增量，仅 reset）。
只观测、只告警、不阻断、不注入、不 wake、不进 reason_parts（BLK-3）；状态独立
`<crew-dir>/guard.gitops.json` 原子写（BLK-2）；S1 告警必须标注 stash 假阳性（BLK-4）；
首次基线=文件末尾不回溯、offset>size 重置并记日志、删除文件清键（BLK-6）；
纯 stdlib，路径/时钟可注入，离线可单测（BLK-5）。常量集中顶部，不加配置键（REC-2）。

已知限制（D7 §5）：qoder 工位与人工终端的 checkout --/clean/stash drop 无覆盖；
引号内文本（如 echo "git reset --hard"）是已知假阳性，以「疑似+原文」兜底。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

# --- 四类破坏性操作匹配（D7 §2.3；GITFLAGS 吸收 git -C <path>/--no-pager/多空格）---
# 注意：D7 草案 `[^|;&\n]*\b--hard\b` 的 \b 在「空格+--hard」处永不成立（- 非词字符），
# 实施修正为 `(?<!\S)--hard\b`（前为空白/行首）；语义不变（不跨管道/命令段）。
GITFLAGS = r'(?:-[A-Za-z]+\s+(?:"[^"]*"|\S+)\s+|-[A-Za-z]+\s+|--[A-Za-z][\w-]*\s+)*'
OP_RES = {
    "reset --hard": re.compile(
        rf'\bgit\s+{GITFLAGS}reset\b[^|;&\n]*(?<!\S)--hard\b'),
    "checkout --": re.compile(
        rf'\bgit\s+{GITFLAGS}checkout\b[^|;&\n]*?(\s--(\s|\S)|\s\.\s*$)'),
    "clean -f": re.compile(
        rf'\bgit\s+{GITFLAGS}clean\b[^|;&\n]*\s-[A-Za-z]*f'),
    "stash drop": re.compile(
        rf'\bgit\s+{GITFLAGS}stash\s+(?:-[A-Za-z]+\s+)*drop\b'),
}
# S1 reflog 兜底：reset 行（git stash push 产生同款行，BLK-4 标注）
REFLOG_RESET_RE = re.compile(r"reset: moving to")
REFLOG_STASH_HINT = "疑似（git stash push 产生同款行）"
# 每轮 guard.log 最多告警条数（其余只进 jsonl，数据不丢日志不刷屏；D7 §2.6）
LOG_ALERT_CAP = 5
# 事件文件与状态文件名（相对 <crew-dir>；BLK-2 独立状态）
EVENTS_REL = Path("guard.gitops.jsonl")
STATE_REL = Path("guard.gitops.json")
# 命令原文截断（供人工甄别；D7 §2.3）
COMMAND_SNIPPET = 200


def slug_of(path: Path) -> str:
    """claude projects 目录 slug：路径每个非 [A-Za-z0-9] 字符 → '-'。

    转换只用于候选目录发现，撞名/歧义由行内 cwd 精确判定兜底（定错只漏检不误报）。
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def match_ops(command: str) -> list[str]:
    """命令文本 → 命中的操作类别列表（纯函数；[^|;&\\n]* 限制不跨管道/命令段）。"""
    return [op for op, pattern in OP_RES.items() if pattern.search(command)]


def _fold(command: str) -> str:
    return " ".join(command.split())


def _alert_key(op: str, command: str, cwd: str) -> str:
    return hashlib.sha1(f"{op}|{_fold(command)}|{cwd}".encode("utf-8")).hexdigest()


def _event_key(source_file: str, tool_use_id: str) -> str:
    return hashlib.sha1(f"{source_file}|{tool_use_id}".encode("utf-8")).hexdigest()


def _read_state(crew_dir: Path) -> dict:
    """读观测状态；缺省/旧格式的 reflogOffset 一律 None（BLK-A4-1 修复）。

    - 状态文件缺失/损坏 → 全缺省，reflogOffset=None（首轮只拍基线，不回溯）。
    - 旧格式 reflogOffset=0（int，修复前缺省值）无法与「空 reflog 基线=0」
      区分 → 确定性策略：一律视为旧格式置 None 重走基线（重置到末尾并记
      日志），杜绝升级后一次性回溯告警；唯一漏检窗口=「空 reflog 基线后
      立即产生新 reset 行」一行（漏检优于误报，失败侧安全）。
    """
    fresh = {"version": 1, "fileOffsets": {}, "alerted": {},
             "reflogOffset": None, "seenEvents": {}}
    try:
        data = json.loads((crew_dir / STATE_REL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return fresh
    if not isinstance(data, dict):
        return fresh
    data.setdefault("version", 1)
    data.setdefault("fileOffsets", {})
    data.setdefault("alerted", {})
    data.setdefault("seenEvents", {})
    data.setdefault("reflogOffset", None)
    if data["reflogOffset"] == 0:  # 旧格式 int 0 → 重走基线（见 docstring）
        data["reflogOffset"] = None
        data["_migratedReflog"] = True  # observe 记迁移日志后剔除，不落盘
    return data


def _write_state(crew_dir: Path, state: dict) -> None:
    tmp = crew_dir / (STATE_REL.name + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, sort_keys=True) + "\n",
                   encoding="utf-8")
    tmp.replace(crew_dir / STATE_REL)  # 原子写（P3/BLK-2）


def _append_events(crew_dir: Path, events: list[dict]) -> None:
    if not events:
        return
    with (crew_dir / EVENTS_REL).open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def _in_roots(cwd: str, roots: set[Path]) -> bool:
    """行内 cwd 归属判定（D7 §2.4）：resolve 后 ==root 或 root 在 parents；不猜。"""
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    try:
        path = Path(cwd).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    return any(path == root or root in path.parents for root in roots)


def _tail_read(path: Path, offset: int) -> tuple[str, int, bool]:
    """按 offset 增量读文本尾部；返回 (新增段, 新 offset, truncated)。

    首次（offset=0 且无基线）由调用方先置 offset=size；这里只处理：
    正常增量 / offset>size（truncate → 重置到末尾，BLK-6）。
    """
    size = path.stat().st_size
    if offset > size:
        return "", size, True
    with path.open("rb") as handle:
        handle.seek(offset)
        chunk = handle.read()
    return chunk.decode("utf-8", "replace"), size, False


def scan_transcripts(files: list[Path], offsets: dict, seen: dict,
                     roots: set[Path], now: float,
                     source_dir: Path) -> list[dict]:
    """S3：claude session JSONL 增量扫描（纯函数内核；BLK-5 离线可测）。

    files/offsets/seen 由调用方持有（可注入）；返回新事件列表并原地更新
    offsets/seen。首次见到文件基线=末尾不回溯（BLK-6）。
    """
    events: list[dict] = []
    for path in files:
        try:
            size = path.stat().st_size
        except OSError:
            offsets.pop(str(path), None)  # 删除文件清键（BLK-6）
            continue
        key = str(path)
        offset = offsets.get(key)
        if offset is None:  # 首次基线 = 当前末尾，不回溯（BLK-6）
            offsets[key] = size
            continue
        if offset >= size:
            offsets[key] = size
            continue
        chunk, new_offset, truncated = _tail_read(path, offset)
        offsets[key] = new_offset
        if truncated:
            continue  # truncate 已重置基线，本轮不读（下轮起正常增量）
        for line in chunk.splitlines():
            try:
                data = json.loads(line)
            except ValueError:
                continue  # 尾部半行竞态属正常（D7 §2.2）
            if not isinstance(data, dict) or data.get("type") != "assistant":
                continue  # 天然排除 api-request-blob 重复行（D7 §1）
            if not _in_roots(data.get("cwd"), roots):
                continue
            message = data.get("message")
            if not isinstance(message, dict):
                continue
            contents = message.get("content")
            if not isinstance(contents, list):
                continue
            session_id = str(data.get("sessionId") or "")[:8]  # REC-3
            for item in contents:
                if not isinstance(item, dict) or item.get("type") != "tool_use":
                    continue
                if item.get("name") != "Bash":
                    continue
                command = item.get("input", {}).get("command") if isinstance(
                    item.get("input"), dict) else None
                if not isinstance(command, str):
                    continue
                dedup = _event_key(key, str(item.get("id") or ""))
                if dedup in seen:
                    continue   # 崩溃窗口尾部行二次去重（D7 §2.6）
                seen[dedup] = now
                for op in match_ops(command):
                    events.append({
                        "ts": now, "source": "session", "sessionId": session_id,
                        "cwd": str(data.get("cwd") or ""), "op": op,
                        "command": command[:COMMAND_SNIPPET],
                        "offset": offsets[key],
                    })
    return events


def scan_reflog(reflog: Path | None, offset: int, roots: set[Path],
                now: float) -> tuple[list[dict], int, bool]:
    """S1：reflog 增量兜底（仅 reset；BLK-4 stash 假阳性标注）。

    返回 (新事件, 新 offset, 首次基线)。首次 offset=None → 基线=末尾不回溯。
    """
    if reflog is None:
        return [], offset, False
    try:
        size = reflog.stat().st_size
    except OSError:
        return [], offset, False
    if offset is None:
        return [], size, True
    if offset >= size:
        return [], size, False
    chunk, new_offset, truncated = _tail_read(reflog, offset)
    if truncated:
        return [], new_offset, False
    events = []
    for line in chunk.splitlines():
        if REFLOG_RESET_RE.search(line):
            events.append({
                "ts": now, "source": "reflog", "sessionId": "",
                "cwd": "", "op": "reset --hard",
                "command": (REFLOG_STASH_HINT + " | " + line)[:COMMAND_SNIPPET],
                "offset": new_offset,
            })
    return events, new_offset, False


def find_transcript_dirs(projects_dir: Path, roots: set[Path]) -> list[Path]:
    """候选目录发现：projects_dir 下 slug 前缀匹配（宁多勿少，cwd 精确判定兜底）。"""
    prefixes = tuple(slug_of(root) for root in roots)
    found: list[Path] = []
    try:
        entries = sorted(projects_dir.iterdir())
    except OSError:
        return []
    for entry in entries:
        if entry.is_dir() and entry.name.startswith(prefixes):
            found.append(entry)
    return found


def git_common_dir(root: Path) -> Path | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (root / common).resolve()
    return common


def git_worktree_roots(root: Path) -> set[Path]:
    """与 crew-guard.git_worktree_roots 同口径（`git worktree list --porcelain`）。

    guard 文件名含连字符不可 import，此处独立实现保持行为一致（D7 §3 只读共享）。
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            text=True, capture_output=True, check=False, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0:
        return set()
    roots: set[Path] = set()
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                roots.add(Path(line[len("worktree "):].strip()).resolve())
            except (OSError, RuntimeError):
                continue
    return roots


def observe(guard, projects_dir: Path | None = None) -> None:
    """guard 挂载入口（run_once 每轮调用；异常由挂载点 try/except 兜底，BLK-3）。

    只写 guard.log / guard.gitops.jsonl / guard.gitops.json；不 wake、不注入。
    projects_dir 可注入（默认 ~/.claude/projects；单测夹具用）。
    """
    root = Path(guard.root)
    crew_dir = Path(guard.crew_dir)
    roots = {root} | git_worktree_roots(root)
    now = time.time()
    state = _read_state(crew_dir)
    if state.pop("_migratedReflog", False):
        guard.log("gitops 观测：检测到旧格式 reflogOffset=0，重置基线到末尾"
                  "（避免升级后回溯历史告警；BLK-A4-1）")
    offsets = dict(state["fileOffsets"])
    seen = dict(state["seenEvents"])

    projects_dir = projects_dir or Path.home() / ".claude" / "projects"
    files = sorted(
        path for directory in find_transcript_dirs(projects_dir, roots)
        for path in directory.glob("*.jsonl"))
    if not files and not find_transcript_dirs(projects_dir, roots):
        guard.log("gitops 观测：无候选 session 目录（本仓可能无 claude 会话）")

    events = scan_transcripts(files, offsets, seen, roots, now, projects_dir)

    reflog = None
    common = git_common_dir(root)
    if common is not None:
        candidate = common / "logs" / "HEAD"
        if candidate.is_file():
            reflog = candidate
    reflog_offset = state["reflogOffset"]
    reflog_events, reflog_offset, first_baseline = scan_reflog(
        reflog, reflog_offset, roots, now)
    if first_baseline and reflog is not None:
        guard.log("gitops 观测：reflog 首次基线=末尾（不回溯历史）")
    events.extend(reflog_events)

    new_alerts: list[dict] = []
    for event in events:
        key = _alert_key(event["op"], event["command"], event["cwd"])
        if key in state["alerted"]:
            continue  # 签名终身一次（D7 §2.6）
        state["alerted"][key] = now
        new_alerts.append(event)

    _append_events(crew_dir, events)
    for event in new_alerts[:LOG_ALERT_CAP]:
        hint = ""
        if event["source"] == "reflog":
            hint = f"（{REFLOG_STASH_HINT}）"
        guard.log(f"gitops 破坏性 git 操作观测：{event['op']} {hint}"
                  f"cwd={event['cwd'] or '-'} cmd={event['command'][:80]}")
    if len(new_alerts) > LOG_ALERT_CAP:
        guard.log(f"gitops 本轮新告警 {len(new_alerts)} 条，仅记前 {LOG_ALERT_CAP} 条"
                  f"（其余见 {EVENTS_REL.name}）")

    state["fileOffsets"] = offsets
    state["seenEvents"] = seen
    state["reflogOffset"] = reflog_offset
    _write_state(crew_dir, state)


def observe_once(guard) -> None:
    """dispatch 薄包装：guard 直接调用（import 失败由挂载点静默降级，BLK-3）。"""
    observe(guard)
