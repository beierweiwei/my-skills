#!/usr/bin/env python3
"""编排回执通道：文件为真源，推送只是可选投递。

回执落点：<git-common-dir>/crew/inbox/<receipt-id>.json（tmp + rename 原子写）。
注册表：<git-common-dir>/crew/registry.json，只存 Herdr 不知道的路由；默认不创建。

推送边界（2.1.7）：
- opencode→opencode：POST /api/session/{id}/prompt，请求体 delivery=queue；
- qoder→qoder：默认 `herdr agent prompt <目标名>` 直推（非交互、无跨会话审批）；
  `--via sendmessage` 显式回到 DELEGATE（需接收方人工确认，inbox 为真源）；
  `--via herdr` 强制 herdr，失败时回退 DELEGATE 并返回诊断错误；
- claude / codex：本期只做路由校验与接口占位，不真实投递；
- 跨产品 / Herdr 目标：只写文件 + Herdr 通知；`--via` 只作用于同 runtime=qoder 的推送。
- `--target-runtime auto` 只查 registry；无路由按 Herdr 目标处理（文件+通知）。需要 qoder
  直推时显式传 `--target-runtime qoder`。

推送失败不回滚回执。退出码：0 成功；1 参数/注册表/回执错误；3 回执成立但投递失败。

示例：
  python3 scripts/crew-msg.py write --from builder-1 --runtime opencode --task T-1 \\
      --status done --summary "切片完成" --commit abc123 \\
      --changed-file scripts/foo.py --evidence "python3 -m unittest ..." \\
      --work-slot "builder·T-1" --to helm
  python3 scripts/crew-msg.py ls --status blocked
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
        subprocess.run(
            ["herdr", "notification", "show", title, "--body", body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
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


def deliver_qoder(
    receipt: dict,
    *,
    to: str | None,
    via: str,
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
        return result

    if runtime != receipt.get("runtime"):
        result = _notify_result(
            receipt,
            crew_dir,
            runtime,
            f"跨产品（{receipt.get('runtime')}→{runtime}）：只写文件 + 通知",
        )
        result["target"] = to
        return result

    if runtime == "qoder":
        return deliver_qoder(receipt, to=to, via=via, timeout=timeout)

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
            return {
                "target": to,
                "runtime": "opencode",
                "delivery": "http",
                "ok": False,
                "http_status": exc.code,
                "url": url,
                "error": f"HTTP {exc.code}: {exc.reason}",
            }
        except Exception as exc:  # noqa: BLE001 - 推送失败是数据，不是崩溃
            return {
                "target": to,
                "runtime": "opencode",
                "delivery": "http",
                "ok": False,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
            }
        ok = 200 <= status_code < 300
        return {
            "target": to,
            "runtime": "opencode",
            "delivery": "http",
            "ok": ok,
            "http_status": status_code,
            "url": url,
            "error": None if ok else f"HTTP {status_code}: {body[:200]!r}",
        }

    return {
        "target": to,
        "runtime": runtime,
        "delivery": "placeholder",
        "ok": True,
        "note": f"{runtime} 本期只留路由与接口占位，未真实投递（回执已落文件）",
    }


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
        }
    atomic_write_json(path, receipt)
    result = None
    if push and to:
        result = deliver_receipt(
            receipt,
            crew_dir,
            to=to,
            target_runtime=target_runtime,
            via=via,
            timeout=timeout,
        )
    return {
        "receipt_id": receipt["receipt_id"],
        "path": str(path),
        "duplicate": False,
        "push": result,
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

    push_result = result.get("push")
    if push_result and not push_result.get("ok"):
        print(f"error: 推送失败（回执仍成立）: {push_result.get('error')}", file=sys.stderr)
        return EXIT_PUSH_FAILED
    return EXIT_OK


def _cmd_ls(args, crew_dir: Path) -> int:
    receipts = list_receipts(crew_dir, status=args.status)
    if args.json:
        print(json.dumps(receipts, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not receipts:
        print("(无回执)")
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
  `--via sendmessage` 保留 DELEGATE（由调用方用 SendMessage 转发）并告警需接收方
  人工确认；`--via herdr` 强制 herdr，失败时回退 DELEGATE 并返回诊断错误；
- opencode→opencode：POST /api/session/{id}/prompt，请求体 delivery=queue；
- claude / codex：本批仅校验路由并返回 placeholder，不真实投递；
- 跨产品 / Herdr 目标：只写文件 + Herdr 通知；`--via` 只作用于同 runtime=qoder 的推送。
- `--target-runtime auto` 只查 registry；无路由按 Herdr 目标处理（文件+通知）。需要 qoder
  直推时显式传 `--target-runtime qoder`。

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

    ls = subparsers.add_parser("ls", parents=[common], help="列回执")
    ls.add_argument("--status", choices=STATUSES, help="按状态过滤")

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
