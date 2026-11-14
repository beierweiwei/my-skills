#!/usr/bin/env python3
"""轮次预检：一次输出五项开工检查；缺失或命令失败一律标注「不可用/未知」，
不崩、不误报（检查器语义）。

五项：
  ① guard.wake 新鲜度：有无 / 首行 generated_at / 是否超过 TTL（默认 7200s）
  ② 未读回执：inbox 文件；T42 已读游标（inbox-read-cursor.json）就绪时按其扣减，
     未就绪或损坏时按全部回执计数并标注
  ③ herdr agent 状态 + ctx：本仓（含 `git worktree list` 的 linked worktree）cwd 的
     agent；`herdr pane read` 可解析时给出 ctx（与 crew-guard.py 同口径），不可解析标未知
  ④ 看板新鲜度：调用 plan-status.py --check-fresh
  ⑤ guard pid：guard.lock + 进程存活 + 锁持有

每项输出「值 / 来源 / 是否可用」；`--json` 输出同一结构。
退出码：0 检查完成（默认检查器语义）；`--strict` 时任一检查不可用或不健康 → 1。

用法：
  python3 scripts/crew-preflight.py [--root <仓根>] [--json] [--strict] \\
      [--wake-ttl 7200]
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
PLAN_STATUS = Path(__file__).resolve().parent / "plan-status.py"

WAKE_FILE = "guard.wake"
LOCK_FILE = "guard.lock"
INBOX_DIR = "inbox"
CURSOR_FILE = "inbox-read-cursor.json"
DEFAULT_WAKE_TTL_SECONDS = 7200
DETAIL_ITEMS_MAX = 10
UNREAD_SHOW_MAX = 3  # S3：inbox 未读默认展示上限（--full 展开全量）
CTX_READS_MAX = 8
HERDR_TIMEOUT = 10.0
PLAN_STATUS_TIMEOUT = 60.0

WAKE_GENERATED_RE = re.compile(r"^generated_at:\s*(\S+)\s*$")
# 与 crew-guard.py 同口径（勿单侧变更；tests 里做了两实现逐样本比对）
CTX_RE = re.compile(
    r"ctx\s*\(?\s*(?:"
    r"(?P<used>\d+(?:\.\d+)?)\s*k\s*/\s*(?P<total>\d+(?:\.\d+)?)\s*(?P<unit>[kKmM])"
    r"|(?P<percent>\d+(?:\.\d+)?)\s*%"
    r")\s*\)?",
    re.IGNORECASE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

TITLES = {
    "wake": "guard.wake 新鲜度",
    "inbox": "未读回执",
    "agents": "agent 状态 + ctx",
    "board": "看板新鲜度",
    "guard": "guard pid",
}


def utc_now() -> str:
    return (datetime.now(timezone.utc).replace(microsecond=0)
            .isoformat().replace("+00:00", "Z"))


def parse_wake_generated_at(text: str) -> datetime | None:
    """解析 guard.wake 首行：`generated_at: <ISO>` 或旧格式裸 ISO；失败返回 None。"""
    lines = text.splitlines()
    if not lines:
        return None
    head = lines[0].strip()
    match = WAKE_GENERATED_RE.match(head)
    raw = (match.group(1) if match else head).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        generated = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated.astimezone(timezone.utc)


def parse_ctx_k(text: str) -> float | None:
    """从 pane 文本解析 ctx（每 1M 的 k 值）；百分比折算，多行取最后匹配。"""
    if not isinstance(text, str):
        return None
    matches = list(CTX_RE.finditer(ANSI_RE.sub("", text)))
    if not matches:
        return None
    match = matches[-1]
    percent = match.group("percent")
    if percent is not None:
        return float(percent) * 10.0
    used = float(match.group("used"))
    total = float(match.group("total"))
    if total <= 0:
        return None
    total_k = total * (1000.0 if match.group("unit").lower() == "m" else 1.0)
    return used / total_k * 1000.0


def _check(check_id: str, *, available: bool, ok: bool, value: str,
           source: str = "-", detail: str = "") -> dict:
    return {
        "id": check_id,
        "title": TITLES[check_id],
        "available": available,
        "ok": ok,
        "value": value,
        "source": source,
        "detail": detail,
    }


def _unavailable(check_id: str, reason: str, source: str = "-",
                 detail: str = "") -> dict:
    return _check(check_id, available=False, ok=False,
                  value=f"不可用（{reason}）", source=source,
                  detail=detail or reason)


def _crew_missing(check_id: str, crew_error: str) -> dict:
    reason = crew_error or "crew 运行时目录不可用"
    return _unavailable(check_id, f"crew 运行时目录不可用：{reason}")


def _cap_lines(lines: list[str], limit: int = DETAIL_ITEMS_MAX) -> str:
    shown = lines[:limit]
    if len(lines) > limit:
        shown = shown + [f"…（其余 {len(lines) - limit} 条省略）"]
    return "\n".join(shown)


def check_wake(crew: Path | None, ttl: int = DEFAULT_WAKE_TTL_SECONDS,
               crew_error: str = "") -> dict:
    if crew is None:
        return _crew_missing("wake", crew_error)
    path = crew / WAKE_FILE
    source = str(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return _check("wake", available=True, ok=True,
                      value="缺失（无待处理叫醒）", source=source)
    except OSError as exc:
        return _unavailable("wake", f"读取失败：{exc}", source)
    generated = parse_wake_generated_at(text)
    if generated is None:
        first = text.splitlines()[0] if text.splitlines() else ""
        return _check("wake", available=True, ok=False,
                      value="首行无法解析（按过期处理）", source=source,
                      detail=f"首行：{first[:200]}")
    age = max(0, int(time.time() - generated.timestamp()))
    expired = age > ttl
    stamp = generated.isoformat().replace("+00:00", "Z")
    body = "\n".join(text.splitlines()[1:]).strip()
    return _check(
        "wake", available=True, ok=not expired,
        value=(f"{'过期' if expired else '新鲜'}（generated_at={stamp}，"
               f"age={age}s，TTL={ttl}s）"),
        source=source, detail=body[:500])


def _read_inbox(folder: Path) -> tuple[list[dict], list[str]]:
    entries: list[dict] = []
    broken: list[str] = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            broken.append(f"{path.name} 无法解析：{exc}")
            continue
        if not isinstance(data, dict):
            broken.append(f"{path.name} 无法解析：不是 JSON 对象")
            continue
        entries.append({
            "receipt_id": str(data.get("receipt_id") or path.stem),
            "status": str(data.get("status") or "-"),
            "kind": str(data.get("kind") or "receipt"),
            "from": str(data.get("from") or "-"),
            "task": str(data.get("task") or "-"),
        })
    entries.sort(key=lambda item: item["receipt_id"])
    return entries, broken


def check_inbox(crew: Path | None, crew_error: str = "", full: bool = False) -> dict:
    if crew is None:
        return _crew_missing("inbox", crew_error)
    folder = crew / INBOX_DIR
    source = str(folder)
    if not folder.is_dir():
        return _check("inbox", available=True, ok=True,
                      value="0 条未读（inbox 目录不存在）", source=source)
    try:
        entries, broken = _read_inbox(folder)
    except OSError as exc:
        return _unavailable("inbox", f"读取失败：{exc}", source)

    read_ids: set[str] = set()
    cursor = crew / CURSOR_FILE
    if cursor.is_file():
        try:
            data = json.loads(cursor.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            note = f"游标损坏，按全部回执计未读：{exc}"
        else:
            read = data.get("read") if isinstance(data, dict) else None
            if isinstance(read, dict):
                read_ids = {str(key) for key in read}
                note = f"游标已就绪（{len(read_ids)} 条已读）"
                source = f"{folder} + {CURSOR_FILE}"
            else:
                note = "游标损坏，按全部回执计未读：缺 read 对象"
    else:
        note = "游标未就绪（按 inbox 全部文件计未读）"

    unread = [item for item in entries if item["receipt_id"] not in read_ids]
    lines = [
        f"- {item['receipt_id']} status={item['status']} "
        f"kind={item['kind']} from={item['from']} task={item['task']}"
        for item in unread
    ]
    if not full and len(lines) > UNREAD_SHOW_MAX:
        # S3：未读默认只展示前 3 条，其余折叠计数（--full 保持全量）。
        rest = len(lines) - UNREAD_SHOW_MAX
        lines = lines[:UNREAD_SHOW_MAX] + [f"…另有 {rest} 条（--full 展开）"]
    lines.extend(f"- {item}" for item in broken)
    return _check(
        "inbox", available=True, ok=not broken,
        value=(f"{len(unread)} 条未读（inbox 共 {len(entries)} 条；{note}；"
               "已读≠已处置：处置以回执 status + plan 为准）"),
        source=source, detail=_cap_lines(lines))


def _is_under(cwd: object, root: Path) -> bool:
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    try:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            return False
        path = path.resolve()
        target = root.resolve()
    except (OSError, RuntimeError):
        return False
    return path == target or target in path.parents


def _herdr_pane_ctx(herdr_bin: str, pane: str) -> str:
    try:
        proc = subprocess.run(
            [herdr_bin, "pane", "read", pane, "--lines", "80"],
            text=True, capture_output=True, check=False, timeout=HERDR_TIMEOUT,
            env={**os.environ, "HERDR_ENV": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"未知（pane read 失败：{exc}）"
    if proc.returncode != 0:
        return f"未知（pane read 退出码 {proc.returncode}）"
    ctx_k = parse_ctx_k(proc.stdout)
    if ctx_k is None:
        return "未知（不可解析）"
    return f"{ctx_k:g}k/1M"


def _git_worktree_roots(root: Path) -> list[Path]:
    """`git worktree list --porcelain` 的 linked worktree 路径；失败返回空表不崩。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            text=True, capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    roots: list[Path] = []
    for line in proc.stdout.splitlines():
        if not line.startswith("worktree "):
            continue
        value = line[len("worktree "):].strip()
        if not value:
            continue
        try:
            roots.append(Path(value).expanduser().resolve())
        except (OSError, RuntimeError):
            continue
    return roots


def check_capacity(root: Path, herdr_bin: str = "herdr") -> dict:
    """容量检查（2.2.31 工单 07B）：与 dispatch capacity/guard 同口径计数；
    ok ≤warn / warn >warn / hard ≥hard（满编，dispatch 拒新增口径）；仅展示，
    不改变五项结构与退出码语义。工单 08 B-3：读 config 传 helm_name（总舵排除）
    并剥 herdr result 包装——四处同数字。"""
    import crew_capacity
    helm_name = ""
    try:
        data = json.loads((root / ".planning" / "orchestration.json")
                          .read_text(encoding="utf-8"))
        helm = data.get("helm") if isinstance(data, dict) else None
        helm_name = str((helm or {}).get("agentName") or "").strip()
    except (OSError, ValueError):
        pass
    try:
        proc = subprocess.run(
            [herdr_bin, "agent", "list"],
            text=True, capture_output=True, check=False, timeout=HERDR_TIMEOUT,
            env={**os.environ, "HERDR_ENV": "1"},
        )
        payload = json.loads(proc.stdout) if proc.returncode == 0 else {}
        result = payload.get("result") if isinstance(payload, dict) else None
        agents = (result.get("agents") if isinstance(result, dict)
                  else payload.get("agents") if isinstance(payload, dict) else payload)
        cap = crew_capacity.count_crew_agents(agents or [], root, helm_name)
        cfg = crew_capacity.load_capacity_config(root)
        total = cap["total"]
        level = ("ok" if total <= cfg["warn"]
                 else "warn" if total < cfg["hard"] else "hard")
        return {"capacity": f"{total} 工位（warn={cfg['warn']} hard={cfg['hard']}）",
                "level": level, "names": cap["names"]}
    except Exception as exc:  # noqa: BLE001 - 容量检查失败不影响其余五项
        return {"capacity": f"不可用：{type(exc).__name__}: {exc}", "level": "unknown",
                "names": []}


def check_agents(root: Path, herdr_bin: str = "herdr") -> dict:
    source = f"{herdr_bin} agent list + pane read"
    try:
        proc = subprocess.run(
            [herdr_bin, "agent", "list"],
            text=True, capture_output=True, check=False, timeout=HERDR_TIMEOUT,
            env={**os.environ, "HERDR_ENV": "1"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable("agents", f"herdr 不可用：{exc}", source)
    if proc.returncode != 0:
        return _unavailable(
            "agents", f"herdr agent list 退出码 {proc.returncode}", source)
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        return _unavailable("agents", f"herdr agent list 输出非 JSON：{exc}", source)
    result = payload.get("result") if isinstance(payload, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return _unavailable("agents", "herdr agent list 缺 agents", source)

    project_roots = [root.resolve(), *_git_worktree_roots(root)]
    project = [
        item for item in agents
        if isinstance(item, dict)
        and any(_is_under(item.get("cwd"), candidate) for candidate in project_roots)
    ]
    project.sort(key=lambda item: str(item.get("name") or ""))
    if not project:
        return _check("agents", available=True, ok=True,
                      value=f"0 个本仓 agent（本机共 {len(agents)} 个）",
                      source=source)

    counts: dict[str, int] = {}
    lines: list[str] = []
    for index, item in enumerate(project):
        name = str(item.get("name") or item.get("pane_id") or "-")
        status = str(item.get("agent_status") or "unknown").strip().lower() or "unknown"
        counts[status] = counts.get(status, 0) + 1
        pane = str(item.get("pane_id") or "").strip()
        ctx = "未知（缺 pane_id）"
        if pane and index < CTX_READS_MAX:
            ctx = _herdr_pane_ctx(herdr_bin, pane)
        lines.append(f"- {name} status={status} ctx={ctx} pane={pane or '-'}")
    if len(project) > CTX_READS_MAX:
        lines.append(f"…（ctx 仅读取前 {CTX_READS_MAX} 个 agent）")
    order = ("working", "idle", "blocked", "done")
    parts = [f"{key}={counts.get(key, 0)}" for key in order]
    others = sum(value for key, value in counts.items() if key not in order)
    if others:
        parts.append(f"其他={others}")
    return _check(
        "agents", available=True, ok=True,
        value=f"{len(project)} 个本仓 agent（{'，'.join(parts)}）",
        source=source, detail=_cap_lines(lines, CTX_READS_MAX + 2))


def check_board(root: Path, plan_status: Path = PLAN_STATUS) -> dict:
    source = f"{plan_status} --check-fresh"
    if not plan_status.is_file():
        return _unavailable("board", f"缺少 {plan_status.name}", source)
    try:
        proc = subprocess.run(
            [sys.executable, str(plan_status), "--root", str(root), "--check-fresh"],
            text=True, capture_output=True, check=False,
            timeout=PLAN_STATUS_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _unavailable("board", f"调用失败：{exc}", source)
    output = (proc.stdout + proc.stderr).strip()
    if proc.returncode == 0:
        first = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else "看板新鲜"
        return _check("board", available=True, ok=True, value=first, source=source)
    if proc.returncode == 1:
        lines = [line for line in (proc.stderr or proc.stdout).splitlines() if line.strip()]
        first = lines[0] if lines else "看板过期"
        return _check("board", available=True, ok=False, value=first,
                      source=source, detail=_cap_lines(lines[1:]))
    return _unavailable("board", f"plan-status 退出码 {proc.returncode}", source) | {
        "detail": output[:500]}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    return True


def _lock_held(path: Path) -> tuple[bool, str]:
    """flock 探测：guard 存活时持有 guard.lock；返回 (held, error)。

    只读打开：guard.lock 在 is_file 与 open 之间被删时返回 (False, 原因)，
    不会留下空锁文件污染后续判断。
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return True, ""
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False, ""
    except OSError as exc:
        return False, str(exc)


def _proc_cmdline(pid: int) -> str:
    try:
        data = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return data.replace(b"\0", b" ").decode("utf-8", "replace").strip()


def check_guard(crew: Path | None, crew_error: str = "") -> dict:
    if crew is None:
        return _crew_missing("guard", crew_error)
    path = crew / LOCK_FILE
    source = str(path)
    if not path.is_file():
        return _check("guard", available=True, ok=False,
                      value="guard.lock 缺失（guard 未运行）", source=source)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _unavailable("guard", f"guard.lock 读取失败：{exc}", source)
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    try:
        pid = int(first)
    except ValueError:
        return _check("guard", available=True, ok=False,
                      value=f"guard.lock 无有效 pid（内容：{first[:40]}）",
                      source=source)
    if pid <= 0:
        return _check("guard", available=True, ok=False,
                      value=f"guard.lock 无有效 pid（{pid}）", source=source)
    held, lock_error = _lock_held(path)
    if lock_error:
        return _unavailable("guard", f"guard.lock 不可读：{lock_error}", source)
    alive = _pid_alive(pid)
    if alive and held:
        value = f"pid={pid} 存活；锁=持有"
    elif not alive and not held:
        value = f"pid={pid} 已退出；锁=未持有（陈旧锁）"
    elif alive and not held:
        value = f"pid={pid} 存活但锁未持有（可能非 guard 或 pid 复用）"
    else:
        value = f"pid={pid} 已退出但锁被持有（异常）"
    cmdline = _proc_cmdline(pid) if alive else ""
    return _check("guard", available=True, ok=alive and held, value=value,
                  source=source, detail=f"cmdline={cmdline}" if cmdline else "")


def collect_checks(root: Path, wake_ttl: int,
                   full: bool = False) -> tuple[list[dict], Path | None, str]:
    try:
        crew = resolve_crew_dir(root)
        crew_error = ""
    except CrewPathsError as exc:
        crew, crew_error = None, str(exc)
    checks = [
        check_wake(crew, wake_ttl, crew_error),
        check_inbox(crew, crew_error, full=full),
        check_agents(root),
        check_board(root),
        check_guard(crew, crew_error),
    ]
    # 2.2.31 工单 07B：容量附项（同口径计数；展示不阻断——healthy 仍按既有五项）
    capacity = check_capacity(root)
    capacity_check = {
        "id": "capacity",
        "label": "工位容量（附项）",
        "available": capacity["level"] != "unknown",
        "ok": capacity["level"] in ("ok", "warn"),
        "detail": f"{capacity['capacity']} level={capacity['level']}",
    }
    return [*checks, capacity_check], crew, crew_error


def render_text(root: Path, crew: Path | None, crew_error: str,
                checks: list[dict]) -> str:
    marks = "①②③④⑤"
    lines = [f"preflight root={root} time={utc_now()}"]
    if crew is not None:
        lines[0] += f" crew={crew}"
    else:
        lines[0] += f" crew=不可用（{crew_error or '未知原因'}）"
    for mark, item in zip(marks, checks):
        available = "可用=是" if item["available"] else "可用=否"
        health = "健康" if item["ok"] else ("未知" if not item["available"] else "需关注")
        lines.append(
            f"{mark} {item['title']}：[{available}；{health}] "
            f"值={item['value']}；来源={item['source']}")
        if item["detail"]:
            lines.extend(f"   {line}" for line in item["detail"].splitlines())
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crew-preflight.py",
        description="轮次预检：一次输出 wake/未读回执/agent+ctx/看板新鲜度/guard pid 五项。",
    )
    parser.add_argument(
        "--root", help="目标仓根（默认脚本所在仓，即 scripts/ 的上一级）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（同五项结构）")
    parser.add_argument(
        "--strict", action="store_true",
        help="任一检查不可用或不健康时退出码 1（默认检查器语义恒为 0）")
    parser.add_argument(
        "--wake-ttl", type=int, default=DEFAULT_WAKE_TTL_SECONDS,
        help=f"guard.wake 过期阈值秒数（默认 {DEFAULT_WAKE_TTL_SECONDS}）")
    parser.add_argument(
        "--full", action="store_true",
        help="展开 inbox 未读全量明细（默认只展示前 3 条并折叠计数）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else SCRIPT_ROOT
    checks, crew, crew_error = collect_checks(root, args.wake_ttl, full=args.full)
    healthy = all(item["available"] and item["ok"] for item in checks
                 if item.get("id") != "capacity")  # 07B：附项不改变退出码语义
    if args.json:
        print(json.dumps({
            "generated_at": utc_now(),
            "root": str(root),
            "crew_dir": str(crew) if crew is not None else None,
            "crew_error": crew_error,
            "strict": args.strict,
            "ok": healthy,
            "checks": checks,
        }, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(render_text(root, crew, crew_error, checks))
    if args.strict and not healthy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
