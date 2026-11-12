#!/usr/bin/env python3
"""编排回执通道：文件为真源，推送只是可选投递。

回执落点：<git-common-dir>/crew/inbox/<receipt-id>.json（tmp + rename 原子写）。
跨项目：推送前把回执副本原子写入收件方 `<git-common-dir>/crew/inbox/`；解析失败只报
「未落收件方 inbox」不阻断推送（T28）。
注册表：<git-common-dir>/crew/registry.json，只存 Herdr 不知道的路由；默认不创建。

推送边界（2.1.7）：
- opencode→opencode：POST /api/session/{id}/prompt，请求体 delivery=queue；
- qoder→qoder：默认 `herdr agent prompt <目标名>` 直推（非交互、无跨会话审批）；
  `--via sendmessage` 显式回到 DELEGATE（需接收方人工确认，inbox 为真源）；
  `--via herdr` 强制 herdr，herdr 调用失败时回退 DELEGATE 并返回诊断错误；
- 聚焦守卫（T37）：qoder 直推前先查目标 pane 的 `focused`；聚焦则跳过注入，
  回执落 inbox + Herdr 通知，投递结果记 `delivery=inbox_fallback`；
  `herdr pane list` 不可用/字段缺失按 fail-safe 同样不注入（`--via herdr` 同此口径，
  不注入、落 inbox + 通知，结果字段与 note 显式标注）；
- 可达性（A-1）：目标不在本机 `herdr agent list`，或 agent list 调用失败/不可解析时，
  按「目标不可达」处理——不注入、`ok=False`、退出码 3，不冒充 inbox_fallback 成功；
- claude / codex：本期只做路由校验与接口占位，不真实投递；
- 跨产品 / Herdr 目标：只写文件 + Herdr 通知；`--via` 只作用于同 runtime=qoder 的推送。
- `--target-runtime auto` 只查 registry；无路由按 Herdr 目标处理（文件+通知）。需要 qoder
  直推时显式传 `--target-runtime qoder`。
- 审计：投递成功后把 `delivery` 写回本地回执副本（`herdr` / `inbox_fallback` /
  `sendmessage-delegate` 等）；收件方副本不含该字段。

已读游标（T42）：
- 游标文件：`<git-common-dir>/crew/inbox-read-cursor.json`，格式
  `{"version": 1, "read": {"<receipt_id>": "<标记时间 ISO8601>"}}`；
  flock 串行化 + tmp/rename 原子写（锁文件 `inbox-read-cursor.lock`）。
- inbox 文件仍是唯一真源（R1）：游标只记读者已读到哪里，不改回执状态、不删回执文件。
- `ls --unread` 只列未读；`ls --mark-read <receipt_id>...` 或 `ls --mark-read --all`
  标记已读；游标按 crew-dir 持久化，换舵/新会话/新进程读同一游标。
- fail-safe：游标损坏/不可读 → `--unread` 按全量未读处理并告警；游标不可写 →
  标记失败退出码 1，`--unread` 退化为全量并告警。

推送失败不回滚回执。退出码：0 成功；1 参数/注册表/回执错误；3 回执成立但投递失败。

示例：
  python3 scripts/crew-msg.py write --from builder-1 --runtime opencode --task T-1 \\
      --status done --summary "切片完成" --commit abc123 \\
      --changed-file scripts/foo.py --evidence "python3 -m unittest ..." \\
      --work-slot "builder·T-1" --to helm
  python3 scripts/crew-msg.py ls --status blocked
  python3 scripts/crew-msg.py ls --unread
  python3 scripts/crew-msg.py ls --mark-read <receipt-id> [<receipt-id> ...]
  python3 scripts/crew-msg.py ls --mark-read --all
  python3 scripts/crew-msg.py read <receipt-id>
  python3 scripts/crew-msg.py push <receipt-id> --to helm

registry.json 结构：
  {
    "version": 1,
    "routes": {
      "oc-1": {"runtime": "opencode", "server": "http://127.0.0.1:4096",
               "session": "ses_xxx", "plan": ".planning/x/task_plan.md"},
      "claude-1": {"runtime": "claude", "socket": "/tmp/claude-inbox.sock"},
      "codex-1": {"runtime": "codex", "thread": "thr_xxx", "socket": "/tmp/codex.sock"}
    }
  }
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402

SENDER_RUNTIMES = ("qoder", "opencode", "claude", "codex")
VIA_MODES = ("auto", "herdr", "sendmessage")
QODER_APPROVAL_WARNING = "需接收方人工确认（Qoder 跨会话审批），inbox 为真源"
HERDR_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
STATUSES = ("done", "blocked", "captain")
CURSOR_FILENAME = "inbox-read-cursor.json"
CURSOR_LOCK_FILENAME = "inbox-read-cursor.lock"
CURSOR_VERSION = 1
RECEIPT_FIELDS = (
    "receipt_id",
    "from",
    "runtime",
    "task",
    "status",
    "commit",
    "changed_files",
    "evidence",
    "work_slot",
    "findings",
    "summary",
    "ts",
)
ROUTE_COMMON_KEYS = {"runtime", "plan"}
ROUTE_KEYS_BY_RUNTIME = {
    "opencode": {"server", "session"},
    "claude": {"socket"},
    "codex": {"thread", "socket"},
}

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_PUSH_FAILED = 3


class CrewMsgError(Exception):
    """回执或命令用法错误（退出码 1）。"""


class RegistryError(CrewMsgError):
    """注册表缺失必需路由或内容非法（退出码 1）。"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def make_receipt_id(from_: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", str(from_)).strip("-._")[:40] or "worker"
    return f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{slug}-{secrets.token_hex(3)}"


def validate_receipt_id(receipt_id: str) -> None:
    if not isinstance(receipt_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", receipt_id
    ):
        raise CrewMsgError(
            f"非法 receipt_id: {receipt_id!r}（只允许字母数字._-，且不能以符号开头）"
        )


def default_crew_dir(root: Path | None = None) -> Path:
    try:
        return crew_dir(root or Path.cwd())
    except CrewPathsError as exc:
        raise CrewMsgError(f"运行时目录解析失败：{exc}") from exc


def inbox_dir(crew_dir: Path) -> Path:
    return Path(crew_dir) / "inbox"


def registry_path(crew_dir: Path) -> Path:
    return Path(crew_dir) / "registry.json"


def build_receipt(
    *,
    from_: str,
    runtime: str,
    task: str,
    status: str,
    summary: str,
    commit: str = "",
    changed_files: list[str] | None = None,
    evidence: list[str] | None = None,
    work_slot: str = "",
    findings: list[str] | None = None,
    receipt_id: str | None = None,
    ts: str | None = None,
) -> dict:
    if runtime not in SENDER_RUNTIMES:
        raise CrewMsgError(f"未知 runtime: {runtime!r}（可选: {', '.join(SENDER_RUNTIMES)}）")
    if status not in STATUSES:
        raise CrewMsgError(f"未知 status: {status!r}（可选: {', '.join(STATUSES)}）")
    for field, value in (("from", from_), ("task", task), ("summary", summary)):
        if not isinstance(value, str) or not value.strip():
            raise CrewMsgError(f"--{field} 不能为空")
    rid = receipt_id or make_receipt_id(from_)
    validate_receipt_id(rid)
    return {
        "receipt_id": rid,
        "from": from_,
        "runtime": runtime,
        "task": task,
        "status": status,
        "commit": commit,
        "changed_files": list(changed_files or []),
        "evidence": list(evidence or []),
        "work_slot": work_slot,
        "findings": list(findings or []),
        "summary": summary,
        "ts": ts or utc_now(),
    }


def validate_receipt(receipt: dict) -> None:
    if not isinstance(receipt, dict):
        raise CrewMsgError("回执必须是 JSON 对象")
    missing = [field for field in RECEIPT_FIELDS if field not in receipt]
    if missing:
        raise CrewMsgError(f"回执缺少字段: {', '.join(missing)}")
    validate_receipt_id(receipt["receipt_id"])
    if receipt["status"] not in STATUSES:
        raise CrewMsgError(f"回执 status 非法: {receipt['status']!r}")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def read_receipt(crew_dir: Path, receipt_id: str) -> dict:
    validate_receipt_id(receipt_id)
    path = inbox_dir(crew_dir) / f"{receipt_id}.json"
    if not path.exists():
        raise CrewMsgError(f"回执不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CrewMsgError(f"回执不是合法 JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CrewMsgError(f"回执不是 JSON 对象: {path}")
    return data


def list_receipts(crew_dir: Path, status: str | None = None) -> list[dict]:
    folder = inbox_dir(crew_dir)
    if not folder.is_dir():
        return []
    receipts: list[dict] = []
    for path in sorted(folder.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("不是 JSON 对象")
            data.setdefault("receipt_id", path.stem)
        except (OSError, ValueError) as exc:
            data = {"receipt_id": path.stem, "status": "invalid", "error": str(exc)}
        if status and data.get("status") != status:
            continue
        receipts.append(data)
    receipts.sort(key=lambda item: (str(item.get("ts", "")), str(item.get("receipt_id", ""))))
    return receipts


def read_cursor_path(crew_dir: Path) -> Path:
    return Path(crew_dir) / CURSOR_FILENAME


def load_read_cursor(crew_dir: Path) -> tuple[set[str], str | None]:
    """已读游标 → 已读 receipt_id 集合；缺失/损坏/不可读一律 fail-safe。

    返回 (read_ids, warning)。游标只是读取便利、不是真源：损坏或不可读时按
    「全量未读」处理并返回诊断（宁可重复列，不误漏读），不崩、不阻塞 ls。
    """
    path = read_cursor_path(crew_dir)
    if not path.exists():
        if Path(crew_dir).is_dir() and not os.access(crew_dir, os.W_OK):
            return set(), (
                f"已读游标目录不可写（{crew_dir}）：--unread 退化为全量列出，"
                "可能重复显示已读回执；修复权限后可重新标记"
            )
        return set(), None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return set(), f"已读游标损坏或不可读（{path}）：{exc}；--unread 按全量未读处理"
    read = data.get("read") if isinstance(data, dict) else None
    if not isinstance(read, dict):
        return set(), f"已读游标格式非法（{path}）：缺 read 对象；--unread 按全量未读处理"
    return {str(key) for key in read}, None


def mark_receipts_read(crew_dir: Path, receipt_ids: list[str]) -> dict:
    """把 receipt_id 标记为已读；只写游标，不改回执文件与状态（R1 文件仍为真源）。

    显式 id 中有不存在的回执时整体拒绝、不标任何一条。游标损坏时按空游标重建并在
    返回值 warning 中说明。flock 串行化读改写，tmp+rename 原子替换（P2/P3）。
    """
    crew = Path(crew_dir)
    ids = list(dict.fromkeys(receipt_ids))
    for receipt_id in ids:
        validate_receipt_id(receipt_id)
    missing = [rid for rid in ids if not (inbox_dir(crew) / f"{rid}.json").exists()]
    if missing:
        raise CrewMsgError("回执不存在，未标记任何回执: " + ", ".join(missing))
    cursor = read_cursor_path(crew)
    result: dict = {"marked": [], "already_read": [], "cursor": str(cursor), "warning": None}
    if not ids:
        return result
    crew.mkdir(parents=True, exist_ok=True)
    try:
        with open(crew / CURSOR_LOCK_FILENAME, "a+", encoding="utf-8") as handle:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                current, warning = load_read_cursor(crew)
                result["warning"] = warning
                result["marked"] = [rid for rid in ids if rid not in current]
                result["already_read"] = [rid for rid in ids if rid in current]
                if result["marked"]:
                    now = utc_now()
                    current.update(result["marked"])
                    atomic_write_json(
                        cursor,
                        {
                            "version": CURSOR_VERSION,
                            "read": {rid: now for rid in sorted(current)},
                        },
                    )
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise CrewMsgError(
            f"已读游标不可写（{cursor}）：{exc}；本次未标记任何回执"
        ) from exc
    return result


def _route_error(path: Path, errors: list[str]) -> RegistryError:
    return RegistryError(f"注册表不合法（{path}）: " + "；".join(errors))


def validate_registry(data: dict, path: Path | None = None) -> None:
    where = path or Path("registry.json")
    if not isinstance(data, dict):
        raise _route_error(where, ["顶层必须是 JSON 对象"])
    errors: list[str] = []
    extra_top = sorted(set(data) - {"version", "routes"})
    if extra_top:
        errors.append(f"未知顶层字段: {', '.join(extra_top)}")
    if "version" in data and data["version"] != 1:
        errors.append(f"不支持 version={data['version']!r}（期望 1）")
    routes = data.get("routes")
    if not isinstance(routes, dict):
        errors.append("routes 必须是对象")
        routes = {}
    for name, route in routes.items():
        prefix = f'路由 "{name}"'
        if not isinstance(name, str) or not name.strip():
            errors.append("路由名必须是非空字符串")
            continue
        if not isinstance(route, dict):
            errors.append(f"{prefix} 必须是对象")
            continue
        runtime = route.get("runtime")
        if runtime not in ROUTE_KEYS_BY_RUNTIME:
            errors.append(
                f"{prefix} 的 runtime={runtime!r} 不支持"
                f"（可选: {', '.join(ROUTE_KEYS_BY_RUNTIME)}）"
            )
            continue
        allowed = ROUTE_COMMON_KEYS | ROUTE_KEYS_BY_RUNTIME[runtime]
        extra = sorted(set(route) - allowed)
        if extra:
            errors.append(f"{prefix} 含未知字段: {', '.join(extra)}")
        plan = route.get("plan")
        if plan is not None and (not isinstance(plan, str) or not plan.strip()):
            errors.append(f"{prefix} 的 plan 必须是非空字符串")
        if runtime == "codex":
            if not any(
                isinstance(route.get(field), str) and route.get(field, "").strip()
                for field in ("thread", "socket")
            ):
                errors.append(f"{prefix} 至少需要 thread 或 socket")
            for field in ("thread", "socket"):
                if field in route and (
                    not isinstance(route[field], str) or not route[field].strip()
                ):
                    errors.append(f"{prefix} 的 {field} 必须是非空字符串")
        else:
            for field in ROUTE_KEYS_BY_RUNTIME[runtime]:
                value = route.get(field)
                if not isinstance(value, str) or not value.strip():
                    errors.append(f"{prefix} 缺少非空字段 {field}")
        if runtime == "opencode":
            server = route.get("server")
            if isinstance(server, str) and server.strip():
                parsed = urllib.parse.urlsplit(server)
                if parsed.scheme not in ("http", "https") or not parsed.netloc:
                    errors.append(f"{prefix} 的 server 必须是 http(s) URL")
    if errors:
        raise _route_error(where, errors)


def load_registry(crew_dir: Path) -> dict:
    path = registry_path(crew_dir)
    if not path.exists():
        return {"version": 1, "routes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"注册表不是合法 JSON: {path}: {exc}") from exc
    validate_registry(data, path)
    return data


def format_message(receipt: dict) -> str:
    lines = [
        f"回执 {receipt['receipt_id']} | {receipt['status']} | "
        f"{receipt['from']}@{receipt['runtime']} | task={receipt['task']}",
        f"summary: {receipt['summary']}",
    ]
    if receipt.get("commit"):
        lines.append(f"commit: {receipt['commit']}")
    if receipt.get("changed_files"):
        lines.append("changed_files: " + ", ".join(map(str, receipt["changed_files"])))
    if receipt.get("evidence"):
        lines.append("evidence: " + " | ".join(map(str, receipt["evidence"])))
    if receipt.get("work_slot"):
        lines.append(f"work_slot: {receipt['work_slot']}")
    if receipt.get("findings"):
        lines.append("findings: " + "；".join(map(str, receipt["findings"])))
    if receipt.get("status") == "captain":
        lines.append("status=captain 表示 ⏸ 待 Captain")
    return "\n".join(lines)


def http_post_json(url: str, payload: dict, timeout: float = 10.0) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.status, response.read()


def notify(title: str, body: str) -> bool:
    if os.environ.get("HERDR_ENV") != "1":
        return False
    try:
        proc = subprocess.run(
            ["herdr", "notification", "show", title, "--body", body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _notify_result(receipt: dict, crew_dir: Path, runtime: str, note: str) -> dict:
    path = inbox_dir(crew_dir) / f"{receipt['receipt_id']}.json"
    sent = notify(
        f"回执 {receipt['status']}: {receipt['receipt_id']}",
        f"{receipt['summary']}\n{path}",
    )
    return {
        "target": None,
        "runtime": runtime,
        "delivery": "notification",
        "ok": True,
        "notified": sent,
        "note": note,
    }


def herdr_prompt(name: str, message: str, timeout: float = 10.0) -> tuple[bool, str]:
    """通过 herdr 直接把消息推入目标 agent；返回 (ok, 诊断)。"""
    try:
        proc = subprocess.run(
            ["herdr", "agent", "prompt", name, message],
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or "").strip()
    error = f"herdr agent prompt 退出码 {proc.returncode}"
    if detail:
        error += f": {detail[:200]}"
    return False, error


def _herdr_target_name(to: str | None) -> str:
    name = str(to or "").strip()
    return name if HERDR_NAME_RE.fullmatch(name) else ""


def pane_focus_state(name: str, timeout: float = 10.0) -> tuple[bool | None, str, bool]:
    """目标 agent 的 pane 是否聚焦（T37）；None=无法判定，调用方按 fail-safe 不注入。

    返回 (focused, reason, present)：present=False 表示目标不可达——不在本机
    `herdr agent list`，或 agent list 调用失败/输出不可解析。调用方必须区分：
    不可达 → 投递失败（ok=False，非 0 退出）；可达但 pane 聚焦/状态不可判定 →
    fail-safe 不注入（inbox_fallback）。解析链：`herdr agent list` 取 pane_id →
    `herdr pane list` 取该 pane 的 focused；pane list 不可用、pane 缺失或
    focused 字段缺失一律返回 None + 原因。
    """
    if not name:
        return None, "缺目标 agent 名", False
    try:
        proc = subprocess.run(
            ["herdr", "agent", "list"],
            text=True, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"herdr agent list 调用失败：{exc}", False
    if proc.returncode != 0:
        return None, f"herdr agent list 退出码 {proc.returncode}", False
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"herdr agent list 输出非 JSON：{exc}", False
    result = data.get("result") if isinstance(data, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return None, "herdr agent list 缺 agents", False
    entry = next((agent for agent in agents
                  if isinstance(agent, dict) and agent.get("name") == name), None)
    if entry is None:
        return None, f"目标 {name} 不在本机 herdr agent list", False
    pane_id = str(entry.get("pane_id") or "").strip()
    if not pane_id:
        return None, f"agent list 缺 {name} 的 pane_id", True
    try:
        proc = subprocess.run(
            ["herdr", "pane", "list"],
            text=True, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"herdr pane list 调用失败：{exc}", True
    if proc.returncode != 0:
        return None, f"herdr pane list 退出码 {proc.returncode}", True
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"herdr pane list 输出非 JSON：{exc}", True
    result = data.get("result") if isinstance(data, dict) else None
    panes = result.get("panes") if isinstance(result, dict) else None
    if not isinstance(panes, list):
        return None, "herdr pane list 缺 panes", True
    pane = next((item for item in panes
                 if isinstance(item, dict) and str(item.get("pane_id") or "") == pane_id), None)
    if pane is None:
        return None, f"herdr pane list 未找到 pane {pane_id}", True
    focused = pane.get("focused")
    if not isinstance(focused, bool):
        return None, f"pane {pane_id} 缺 focused 字段", True
    return focused, "", True


def _resolve_peer_inbox(to: str | None, timeout: float = 10.0) -> tuple[Path | None, str]:
    """目标 agent → 收件方 `<git-common-dir>/crew/inbox`（T28）。

    返回 (inbox_dir, reason)；解析失败时 inbox_dir=None，reason 显式告知未落盘。
    """
    name = _herdr_target_name(to)
    if not name:
        return None, "目标不是 herdr agent 名，无法定位收件方项目"
    try:
        proc = subprocess.run(
            ["herdr", "agent", "list"],
            text=True, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"herdr agent list 调用失败：{exc}"
    if proc.returncode != 0:
        return None, f"herdr agent list 退出码 {proc.returncode}"
    try:
        data = json.loads(proc.stdout)
    except ValueError as exc:
        return None, f"herdr agent list 输出非 JSON：{exc}"
    result = data.get("result") if isinstance(data, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        agents = []
    entry = next((agent for agent in agents
                  if isinstance(agent, dict) and agent.get("name") == name), None)
    if entry is None:
        return None, f"目标 {name} 不在本机 herdr agent list"
    cwd = str(entry.get("cwd") or entry.get("foreground_cwd") or "").strip()
    if not cwd or not Path(cwd).is_dir():
        return None, f"目标 {name} 缺可用 cwd"
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            text=True, capture_output=True, check=False, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 调用失败：{exc}"
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, f"目标 {name} 不是 git 仓库（cwd={cwd}）"
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (Path(cwd) / common).resolve()
    return common / "crew" / "inbox", ""


def mirror_receipt_to_peer_inbox(
    receipt: dict,
    crew_dir: Path,
    *,
    to: str | None,
    timeout: float = 10.0,
) -> dict:
    """把回执副本原子写入收件方 inbox（跨项目）；失败只报不抛，不阻断推送。"""
    inbox, reason = _resolve_peer_inbox(to, timeout=timeout)
    if inbox is None:
        return {"status": "skipped", "path": None, "reason": reason}
    try:
        if inbox.resolve() == inbox_dir(crew_dir).resolve():
            return {"status": "same", "path": str(inbox), "reason": None}
    except OSError:
        pass
    path = inbox / f"{receipt['receipt_id']}.json"
    if path.exists():
        return {"status": "dedup", "path": str(path), "reason": None}
    try:
        atomic_write_json(path, receipt)
    except OSError as exc:
        return {"status": "skipped", "path": None,
                "reason": f"收件方 inbox 写入失败：{exc}"}
    return {"status": "written", "path": str(path), "reason": None}


def _delegate_qoder(
    receipt: dict,
    to: str | None,
    *,
    note: str,
    ok: bool = True,
    warning: str = "",
    error: str | None = None,
) -> dict:
    result = {
        "target": to,
        "runtime": "qoder",
        "delivery": "delegate",
        "ok": ok,
        "message": format_message(receipt),
        "note": note,
    }
    if warning:
        result["warning"] = warning
    if error:
        result["error"] = error
    return result


def _inbox_fallback_result(
    receipt: dict,
    crew_dir: Path,
    target: str,
    *,
    focused: bool | None,
    reason: str,
) -> dict:
    """聚焦/无法判定时跳过注入：回执落 inbox + Herdr 通知（T37）。"""
    path = inbox_dir(crew_dir) / f"{receipt['receipt_id']}.json"
    sent = notify(
        f"回执 {receipt['status']}: {receipt['receipt_id']}（未注入）",
        f"{reason}\n{receipt['summary']}\n{path}",
    )
    return {
        "target": target,
        "runtime": "qoder",
        "delivery": "inbox_fallback",
        "ok": True,
        "focused": focused,
        "notified": sent,
        "note": (
            f"{reason}；已跳过 herdr 注入，回执已落 inbox"
            + (" 并推送 Herdr 通知" if sent else "（Herdr 通知未发送）")
        ),
    }


def deliver_qoder(
    receipt: dict,
    *,
    to: str | None,
    via: str,
    crew_dir: Path,
    timeout: float = 10.0,
) -> dict:
    if via == "sendmessage":
        return _delegate_qoder(
            receipt,
            to,
            note="--via sendmessage：由调用方 Agent 用 SendMessage 转发 message",
            warning=QODER_APPROVAL_WARNING,
        )
    target = _herdr_target_name(to)
    if not target:
        if via == "herdr":
            return _delegate_qoder(
                receipt,
                to,
                ok=False,
                note="已回退 DELEGATE；请调用方 Agent 用 SendMessage 转发 message",
                warning=QODER_APPROVAL_WARNING,
                error="无可用 herdr 名，无法执行 --via herdr",
            )
        return _delegate_qoder(
            receipt,
            to,
            note="无可用 herdr 名，回退 DELEGATE（兼容行为）",
            warning=QODER_APPROVAL_WARNING,
        )
    focused, focus_reason, present = pane_focus_state(target, timeout=timeout)
    if not present:
        return _delegate_qoder(
            receipt,
            target,
            ok=False,
            note=(
                "目标不可达：未执行 herdr 注入；回执已落发送方 inbox（R1），"
                "请核对目标名/在线状态后重推，或用 SendMessage 转发 message"
            ),
            warning=QODER_APPROVAL_WARNING,
            error=f"目标不可达：{focus_reason}",
        )
    if focused is not False:
        reason = (
            "目标 pane 聚焦（Captain 正在使用），按聚焦守卫跳过注入"
            if focused
            else f"无法判定目标 pane 聚焦状态（{focus_reason}），按 fail-safe 跳过注入"
        )
        if via == "herdr":
            reason += "（--via herdr 强制模式同此口径：聚焦/不可判定不注入）"
        return _inbox_fallback_result(
            receipt, crew_dir, target, focused=focused, reason=reason)
    ok, error = herdr_prompt(target, format_message(receipt), timeout=timeout)
    if ok:
        return {
            "target": target,
            "runtime": "qoder",
            "delivery": "herdr",
            "ok": True,
            "note": "herdr agent prompt 直推（非交互、无跨会话审批）",
        }
    return _delegate_qoder(
        receipt,
        target,
        ok=False,
        note="herdr prompt 失败，已回退 DELEGATE；inbox 为真源，请调用方转发 message",
        warning=QODER_APPROVAL_WARNING,
        error=error,
    )


def deliver_receipt(
    receipt: dict,
    crew_dir: Path,
    *,
    to: str | None = None,
    target_runtime: str | None = None,
    via: str = "auto",
    timeout: float = 10.0,
) -> dict:
    validate_receipt(receipt)
    if via not in VIA_MODES:
        raise CrewMsgError(f"未知 via: {via!r}（可选: {', '.join(VIA_MODES)}）")
    registry = load_registry(crew_dir)
    routes = registry.get("routes", {})
    if not to:
        return {"target": None, "runtime": None, "delivery": "none", "ok": True}

    # R1 跨项目真源：回执副本先落收件方 inbox（失败不阻断推送，但必须显式告知）。
    peer_inbox = mirror_receipt_to_peer_inbox(
        receipt, crew_dir, to=to, timeout=timeout)

    def finish(result: dict) -> dict:
        result["peer_inbox"] = peer_inbox
        return result

    route = routes.get(to)
    if target_runtime in (None, "auto"):
        runtime = route["runtime"] if route else "herdr"
    else:
        runtime = target_runtime
        if route is not None and route["runtime"] != runtime:
            raise RegistryError(
                f'目标 "{to}" 在注册表中的 runtime={route["runtime"]}，'
                f"与 --target-runtime={runtime} 冲突"
            )

    if runtime == "herdr":
        result = _notify_result(receipt, crew_dir, "herdr", "Herdr 目标：只写文件 + 通知")
        result["target"] = to
        return finish(result)

    if runtime != receipt.get("runtime"):
        result = _notify_result(
            receipt,
            crew_dir,
            runtime,
            f"跨产品（{receipt.get('runtime')}→{runtime}）：只写文件 + 通知",
        )
        result["target"] = to
        return finish(result)

    if runtime == "qoder":
        return finish(deliver_qoder(
            receipt, to=to, via=via, crew_dir=crew_dir, timeout=timeout))

    if route is None:
        raise RegistryError(f'目标 "{to}" 缺少 {runtime} 路由（registry.json）')

    if runtime == "opencode":
        server = route["server"].rstrip("/")
        url = (
            f"{server}/api/session/"
            f"{urllib.parse.quote(route['session'], safe='')}/prompt"
        )
        payload = {
            "parts": [{"type": "text", "text": format_message(receipt)}],
            "delivery": "queue",
        }
        try:
            status_code, body = http_post_json(url, payload, timeout=timeout)
        except urllib.error.HTTPError as exc:
            return finish({
                "target": to,
                "runtime": "opencode",
                "delivery": "http",
                "ok": False,
                "http_status": exc.code,
                "url": url,
                "error": f"HTTP {exc.code}: {exc.reason}",
            })
        except Exception as exc:  # noqa: BLE001 - 推送失败是数据，不是崩溃
            return finish({
                "target": to,
                "runtime": "opencode",
                "delivery": "http",
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
            })
        ok = 200 <= status_code < 300
        return finish({
            "target": to,
            "runtime": "opencode",
            "delivery": "http",
            "ok": ok,
            "http_status": status_code,
            "url": url,
            "error": None if ok else f"HTTP {status_code}: {body[:200]!r}",
        })

    return finish({
        "target": to,
        "runtime": runtime,
        "delivery": "placeholder",
        "ok": True,
        "note": f"{runtime} 本期只留路由与接口占位，未真实投递（回执已落文件）",
    })


def record_delivery(crew_dir: Path, receipt_id: str, delivery: str) -> tuple[bool, str]:
    """把投递通道写回本地回执副本供事后审计（T37）；失败不阻断推送。"""
    path = inbox_dir(crew_dir) / f"{receipt_id}.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return False, f"回执不可读：{exc}"
    if not isinstance(data, dict):
        return False, "回执不是 JSON 对象"
    data["delivery"] = delivery
    try:
        atomic_write_json(path, data)
    except OSError as exc:
        return False, f"回执写入失败：{exc}"
    return True, ""


def audit_delivery(result: dict, via: str) -> str:
    """投递结果 → 审计通道：显式 sendmessage 的 delegate 记为 sendmessage-delegate。"""
    delivery = str(result.get("delivery") or "none")
    if delivery == "delegate" and via == "sendmessage":
        return "sendmessage-delegate"
    return delivery


def write_receipt(
    crew_dir: Path,
    receipt: dict,
    *,
    push: bool = True,
    to: str | None = None,
    target_runtime: str | None = None,
    via: str = "auto",
    timeout: float = 10.0,
) -> dict:
    validate_receipt(receipt)
    path = inbox_dir(crew_dir) / f"{receipt['receipt_id']}.json"
    if path.exists():
        return {
            "receipt_id": receipt["receipt_id"],
            "path": str(path),
            "duplicate": True,
            "push": None,
            "delivery_record": None,
        }
    atomic_write_json(path, receipt)
    result = None
    delivery_record = None
    if push and to:
        result = deliver_receipt(
            receipt,
            crew_dir,
            to=to,
            target_runtime=target_runtime,
            via=via,
            timeout=timeout,
        )
        recorded, record_error = record_delivery(
            crew_dir, receipt["receipt_id"], audit_delivery(result, via))
        delivery_record = {"ok": recorded, "error": record_error or None}
    return {
        "receipt_id": receipt["receipt_id"],
        "path": str(path),
        "duplicate": False,
        "push": result,
        "delivery_record": delivery_record,
    }


def _print_push(result: dict) -> None:
    line = (
        f"推送：{result.get('delivery')} runtime={result.get('runtime') or '-'} "
        f"ok={'true' if result.get('ok') else 'false'}"
    )
    if "notified" in result:
        line += f" notified={'true' if result['notified'] else 'false'}"
    if result.get("http_status") is not None:
        line += f" http={result['http_status']}"
    if result.get("error"):
        line += f" error={result['error']}"
    print(line)
    peer = result.get("peer_inbox") or {}
    if peer:
        if peer.get("status") in ("written", "dedup"):
            print(f"收件方 inbox：{peer['status']} {peer.get('path')}")
        elif peer.get("status") == "same":
            print("收件方 inbox：同一项目（本地回执即收件方真源）")
        else:
            print(f"未落收件方 inbox：{peer.get('reason') or '原因未知'}")
    if result.get("warning"):
        print(f"warning: {result['warning']}")
    if result.get("note"):
        print(f"说明：{result['note']}")
    if result.get("delivery") == "delegate":
        print("DELEGATE（由调用方 Agent 用 SendMessage 发送以下内容）：")
        print(result.get("message", ""))


def _cmd_write(args, crew_dir: Path) -> int:
    receipt = build_receipt(
        from_=args.from_,
        runtime=args.runtime,
        task=args.task,
        status=args.status,
        summary=args.summary,
        commit=args.commit,
        changed_files=args.changed_file,
        evidence=args.evidence,
        work_slot=args.work_slot,
        findings=args.finding,
        receipt_id=args.receipt_id,
    )
    try:
        result = write_receipt(
            crew_dir,
            receipt,
            push=not args.no_push,
            to=args.to,
            target_runtime=args.target_runtime,
            via=args.via,
            timeout=args.timeout,
        )
    except RegistryError as exc:
        print(f"error: 回执已写入但推送路由无效：{exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"回执 {result['receipt_id']} -> {result['path']}")
        if result["duplicate"]:
            print("已存在同 id 回执：未重写、未重复推送")
        push_result = result.get("push")
        if push_result:
            _print_push(push_result)
        elif not args.no_push and not args.to:
            print("未指定 --to：只落文件，不推送")
        record = result.get("delivery_record")
        if record and not record.get("ok"):
            print(f"warning: 回执未记录投递通道：{record.get('error')}", file=sys.stderr)

    push_result = result.get("push")
    if push_result and not push_result.get("ok"):
        print(f"error: 推送失败（回执仍成立）: {push_result.get('error')}", file=sys.stderr)
        return EXIT_PUSH_FAILED
    return EXIT_OK


def _cmd_ls(args, crew_dir: Path) -> int:
    warnings: list[str] = []
    mark_result = None
    if args.mark_read:
        if args.all and args.receipt_ids:
            raise CrewMsgError("--mark-read 的 --all 与显式 receipt_id 不能同时使用")
        if args.all:
            ids = [
                item["receipt_id"]
                for item in list_receipts(crew_dir, status=args.status)
            ]
        elif args.receipt_ids:
            ids = list(args.receipt_ids)
        else:
            raise CrewMsgError(
                "ls --mark-read 需要 receipt_id 或 --all"
                "（例：ls --mark-read r-1 / ls --mark-read --all）"
            )
        mark_result = mark_receipts_read(crew_dir, ids)
        if mark_result["warning"]:
            warnings.append(mark_result["warning"])
    elif args.receipt_ids or args.all:
        raise CrewMsgError("receipt_id / --all 只配合 --mark-read 使用（例：ls --mark-read r-1）")

    receipts = list_receipts(crew_dir, status=args.status)
    if args.unread:
        read_ids, cursor_warning = load_read_cursor(crew_dir)
        if cursor_warning and cursor_warning not in warnings:
            warnings.append(cursor_warning)
        receipts = [item for item in receipts if item.get("receipt_id") not in read_ids]
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if args.json:
        if mark_result is not None:
            payload = dict(mark_result)
            payload["receipts"] = receipts
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(receipts, ensure_ascii=False, indent=2))
        return EXIT_OK
    if mark_result is not None:
        line = f"已读游标：标记 {len(mark_result['marked'])} 条"
        if mark_result["marked"]:
            line += f"（{', '.join(mark_result['marked'])}）"
        line += (
            f"，此前已读 {len(mark_result['already_read'])} 条；"
            f"游标 {mark_result['cursor']}"
        )
        print(line)
    if not receipts:
        print("(无未读回执)" if args.unread else "(无回执)")
        return EXIT_OK
    for receipt in receipts:
        if "error" in receipt:
            print(f"! {receipt['receipt_id']}: {receipt['error']}")
            continue
        summary = " ".join(str(receipt.get("summary", "")).split())
        print(
            f"{receipt.get('ts', '')}\t{receipt.get('status', '')}\t"
            f"{receipt.get('receipt_id', '')}\t{receipt.get('from', '')}\t"
            f"{receipt.get('task', '')}\t{summary}"
        )
    return EXIT_OK


def _cmd_read(args, crew_dir: Path) -> int:
    receipt = read_receipt(crew_dir, args.receipt_id)
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return EXIT_OK


def _cmd_push(args, crew_dir: Path) -> int:
    receipt = read_receipt(crew_dir, args.receipt_id)
    result = deliver_receipt(
        receipt,
        crew_dir,
        to=args.to,
        target_runtime=args.target_runtime,
        via=args.via,
        timeout=args.timeout,
    )
    recorded, record_error = record_delivery(
        crew_dir, receipt["receipt_id"], audit_delivery(result, args.via))
    if not recorded:
        print(f"warning: 回执未记录投递通道：{record_error}", file=sys.stderr)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        _print_push(result)
    return EXIT_OK if result.get("ok") else EXIT_PUSH_FAILED


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument(
        "--crew-dir",
        help="crew 运行时目录（默认 <git-common-dir>/crew）",
    )
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    return parser


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
回执字段：receipt_id / from / runtime / task / status(done|blocked|captain) / commit /
changed_files[] / evidence[] / work_slot / findings[] / summary / ts。
status=captain 表示 ⏸ 待 Captain。回执先原子落盘，推送失败不影响回执成立。

推送边界：
- qoder→qoder：默认 `herdr agent prompt <目标名>` 直推（非交互、无跨会话审批）；
  直推前查目标 pane 的 focused（T37）：聚焦或无法判定 → 不注入，回执落 inbox +
  Herdr 通知，结果记 delivery=inbox_fallback（`--via herdr` 同此口径）；
  目标不在本机 agent list 或 agent list 不可用 → 目标不可达，ok=false + 退出码 3；
  `--via sendmessage` 保留 DELEGATE（由调用方用 SendMessage 转发）并告警需接收方
  人工确认；`--via herdr` 强制 herdr，herdr 调用失败时回退 DELEGATE 并返回诊断错误；
- opencode→opencode：POST /api/session/{id}/prompt，请求体 delivery=queue；
- claude / codex：本批仅校验路由并返回 placeholder，不真实投递；
- 跨产品 / Herdr 目标：只写文件 + Herdr 通知；`--via` 只作用于同 runtime=qoder 的推送。
- `--target-runtime auto` 只查 registry；无路由按 Herdr 目标处理（文件+通知）。需要 qoder
  直推时显式传 `--target-runtime qoder`。
- 审计：投递通道写回本地回执副本 delivery（herdr / inbox_fallback /
  sendmessage-delegate 等）。

已读游标：`<git-common-dir>/crew/inbox-read-cursor.json`
（`{"version": 1, "read": {"<receipt_id>": "<标记时间>"}}`，flock + tmp/rename 原子写）。
`ls --unread` 只列未读；`ls --mark-read <receipt_id>...` / `ls --mark-read --all` 标记已读；
游标只记读取进度，不改回执状态、不删回执文件（inbox 文件仍是真源 R1）。
游标损坏/不可读 → `--unread` 按全量未读告警处理；游标不可写 → 标记失败 rc=1。

注册表 .git/crew/registry.json 只存 Herdr 不知道的路由，默认不创建；非法路由明确报错。
退出码：0 成功；1 参数/注册表/回执错误；3 回执成立但投递失败。
"""
    parser = argparse.ArgumentParser(
        prog="crew-msg.py",
        description="编排回执通道：文件为真源，推送只是可选投递。",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    common = _common_parser()

    write = subparsers.add_parser(
        "write", parents=[common], help="原子写回执，并按需推送（同 id 幂等）"
    )
    write.add_argument("--from", dest="from_", required=True, help="发送方 agent 名")
    write.add_argument("--runtime", required=True, choices=SENDER_RUNTIMES)
    write.add_argument("--task", required=True, help="任务/工单标识")
    write.add_argument(
        "--status", required=True, choices=STATUSES, help="captain 表示 ⏸ 待 Captain"
    )
    write.add_argument("--summary", required=True, help="一句话结论")
    write.add_argument("--commit", default="", help="commit hash（可空）")
    write.add_argument(
        "--changed-file", action="append", default=[], metavar="PATH", help="改动文件（可重复）"
    )
    write.add_argument(
        "--evidence", action="append", default=[], metavar="REF", help="证据指针（可重复）"
    )
    write.add_argument("--work-slot", default="", help="工位标识")
    write.add_argument(
        "--finding", action="append", default=[], metavar="TEXT", help="发现清单（可重复；无则留空）"
    )
    write.add_argument("--receipt-id", help="默认按时间+发送方自动生成")
    write.add_argument("--to", help="推送目标 peer；不填只落文件")
    write.add_argument(
        "--target-runtime",
        choices=("auto",) + SENDER_RUNTIMES + ("herdr",),
        default="auto",
        help="auto=查注册表；无注册表路由按 Herdr 目标处理",
    )
    write.add_argument("--no-push", action="store_true", help="只落文件，不推送")
    write.add_argument(
        "--via",
        choices=VIA_MODES,
        default="auto",
        help="推送方式：auto=qoder 默认 herdr；herdr 强制；sendmessage 保留 DELEGATE 并告警",
    )
    write.add_argument("--timeout", type=float, default=10.0, help="HTTP/herdr 推送超时秒数")

    ls = subparsers.add_parser(
        "ls", parents=[common], help="列回执；--unread 只看未读，--mark-read 标记已读"
    )
    ls.add_argument("--status", choices=STATUSES, help="按状态过滤")
    ls.add_argument("--unread", action="store_true", help="只列未读回执（按已读游标）")
    ls.add_argument(
        "--mark-read",
        action="store_true",
        help="把 receipt_id（位置参数）或 --all 标记为已读；只写游标，不改回执",
    )
    ls.add_argument(
        "--all",
        action="store_true",
        help="配合 --mark-read：标记 --status 范围内全部回执",
    )
    ls.add_argument(
        "receipt_ids",
        nargs="*",
        metavar="RECEIPT_ID",
        help="配合 --mark-read 的回执 id（可多个）",
    )

    read = subparsers.add_parser("read", parents=[common], help="读取单条回执（JSON）")
    read.add_argument("receipt_id")

    push = subparsers.add_parser("push", parents=[common], help="重试/显式推送已有回执")
    push.add_argument("receipt_id")
    push.add_argument("--to", required=True, help="推送目标 peer")
    push.add_argument(
        "--target-runtime",
        choices=("auto",) + SENDER_RUNTIMES + ("herdr",),
        default="auto",
        help="auto=查注册表；无注册表路由按 Herdr 目标处理",
    )
    push.add_argument(
        "--via",
        choices=VIA_MODES,
        default="auto",
        help="推送方式：auto=qoder 默认 herdr；herdr 强制；sendmessage 保留 DELEGATE 并告警",
    )
    push.add_argument("--timeout", type=float, default=10.0, help="HTTP/herdr 推送超时秒数")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    crew_dir = Path(args.crew_dir) if args.crew_dir else default_crew_dir()
    try:
        if args.command == "write":
            return _cmd_write(args, crew_dir)
        if args.command == "ls":
            return _cmd_ls(args, crew_dir)
        if args.command == "read":
            return _cmd_read(args, crew_dir)
        if args.command == "push":
            return _cmd_push(args, crew_dir)
    except CrewMsgError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    parser.error(f"未知命令: {args.command}")
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
