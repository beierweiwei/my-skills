#!/usr/bin/env python3
"""监听 Herdr 官方 socket 事件，替代 3 秒轮询 `herdr agent list`。

只读：只发 `session.snapshot` / `events.subscribe`，不写会话、不加 hook、不改配置。
状态变化只在边沿输出一行，供 Monitor/编排器消费：

  [HH:MM:SS] <name|pane> (<pane>) old→new

协议（Herdr 0.9.5，换行分隔 JSON）：
- `events.subscribe` 只接受带 `pane_id` 的 `pane.agent_status_changed`；
  `pane.exited` 可不带 pane_id（全局）。一次连接只能订阅一次，订阅后该连接
  只推事件，不能再发 snapshot/订阅。
- 订阅名用点号；事件行形如
  `{"event":"pane.agent_status_changed","data":{"pane_id":...,"agent_status":...}}`。
- 事件历史有界不持久；落后会收到 `events_lost` 并断开，需重连 + snapshot 对账。
- 因此新出现的 pane 不会自动纳入订阅：默认每 30s 用独立连接取一次 snapshot，
  发现新 pane 时主动重连刷新订阅（`--rescan-interval 0` 关闭）。

用法：
  python3 scripts/herdr-events.py                 # 常驻，事件边沿输出
  python3 scripts/herdr-events.py --once          # 只打印 snapshot 摘要
  python3 scripts/herdr-events.py --main-pane <id>  # 排除指定主控 pane（如编排者所在 pane）
  python3 scripts/herdr-events.py --include-main  # 连 --main-pane 指定的主控 pane 一起输出
  python3 scripts/herdr-events.py --debug         # 原始事件打到 stderr
  python3 scripts/herdr-events.py --socket /path/to/herdr.sock

主控 pane 排除：不内置任何默认值（不绑定具体 workspace 前缀）。
`--main-pane <id>` 显式指定要排除的 pane（通常是总舵/编排者所在 pane）；
不指定 = 不排除任何 pane。`--include-main` 仅在指定了 `--main-pane` 时有意义。
"""

from __future__ import annotations

import argparse
import json
import os
import select
import socket
import sys
import time
from pathlib import Path

BACKOFF_START = 0.5
BACKOFF_MAX = 15.0


class Reconnect(Exception):
    """需要重连（断线、events_lost、订阅集合变化）。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class SubscribeError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


class Conn:
    """换行分隔 JSON 的 Unix socket 连接，带半行缓冲。"""

    def __init__(self, path: str):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(path)
        self.buf = b""
        self.next_id = 0

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def send(self, method: str, params: dict) -> str:
        self.next_id += 1
        rid = f"req_{self.next_id}"
        payload = json.dumps(
            {"id": rid, "method": method, "params": params},
            separators=(",", ":"),
            ensure_ascii=False,
        )
        self.sock.sendall(payload.encode() + b"\n")
        return rid

    def read(self, timeout: float | None = None) -> dict | None:
        """读一条消息；timeout 到期返回 None，对端关闭抛 ConnectionError。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            idx = self.buf.find(b"\n")
            if idx >= 0:
                line, self.buf = self.buf[:idx], self.buf[idx + 1 :]
                if not line.strip():
                    continue
                return json.loads(line)
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                ready, _, _ = select.select([self.sock], [], [], remaining)
                if not ready:
                    return None
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed by server")
            self.buf += chunk


def request(conn: Conn, method: str, params: dict, timeout: float) -> dict:
    rid = conn.send(method, params)
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{method}: no response in {timeout}s")
        msg = conn.read(remaining)
        if msg is None:
            raise TimeoutError(f"{method}: no response in {timeout}s")
        if msg.get("id") == rid:
            return msg
        # 尚未订阅的连接不应有事件；忽略无关响应。


def fetch_snapshot(path: str, timeout: float) -> dict:
    """用独立短连接取一次 session.snapshot（订阅连接不能再发请求）。"""
    conn = Conn(path)
    try:
        msg = request(conn, "session.snapshot", {}, timeout)
    finally:
        conn.close()
    if "error" in msg:
        err = msg["error"] or {}
        raise RuntimeError(
            f"session.snapshot failed: {err.get('code')} {err.get('message', '')}".strip()
        )
    return (msg.get("result") or {}).get("snapshot") or {}


def subscribe(conn: Conn, pane_ids: set[str], timeout: float) -> None:
    subscriptions = [{"type": "pane.exited"}]
    subscriptions += [
        {"type": "pane.agent_status_changed", "pane_id": pid}
        for pid in sorted(pane_ids)
    ]
    msg = request(conn, "events.subscribe", {"subscriptions": subscriptions}, timeout)
    if "error" in msg:
        err = msg["error"] or {}
        raise SubscribeError(str(err.get("code")), str(err.get("message", "")))


class Listener:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.status: dict[str, str] = {}
        self.names: dict[str, str] = {}

    def label(self, pane_id: str) -> str:
        return self.names.get(pane_id) or pane_id

    def emit(self, pane_id: str, old: str, new: str, tag: str | None = None) -> None:
        if pane_id == self.args.main_pane and not self.args.include_main:
            return
        suffix = f" ({tag})" if tag else ""
        print(
            f"[{time.strftime('%H:%M:%S')}] {self.label(pane_id)} "
            f"({pane_id}) {old}→{new}{suffix}",
            flush=True,
        )

    def load_snapshot(self, snapshot: dict) -> dict[str, str]:
        """从 snapshot 取 pane 状态与 agent 名；返回 pane→状态。"""
        status: dict[str, str] = {}
        for pane in snapshot.get("panes") or []:
            pane_id = pane.get("pane_id")
            if pane_id:
                status[str(pane_id)] = str(pane.get("agent_status") or "unknown")
        for agent in snapshot.get("agents") or []:
            pane_id = agent.get("pane_id")
            name = agent.get("name")
            if pane_id and name:
                self.names[str(pane_id)] = str(name)
        return status

    def reconcile(self, snapshot: dict, tag: str = "对账") -> None:
        """用 snapshot 对账：差异逐条输出（含消失），并更新状态。"""
        current = self.load_snapshot(snapshot)
        for pane_id, new in current.items():
            old = self.status.get(pane_id)
            if old != new:
                self.emit(pane_id, old or "?", new, tag)
            self.status[pane_id] = new
        for pane_id in [p for p in self.status if p not in current]:
            self.emit(pane_id, self.status.pop(pane_id), "exited", tag)
            self.names.pop(pane_id, None)

    def handle_event(self, msg: dict) -> None:
        if self.args.debug:
            print(
                f"[{time.strftime('%H:%M:%S')}] debug {json.dumps(msg, ensure_ascii=False)}",
                file=sys.stderr,
                flush=True,
            )
        event = msg.get("event")
        data = msg.get("data") or {}
        if event == "pane.agent_status_changed":
            pane_id = data.get("pane_id")
            if not pane_id:
                return
            pane_id = str(pane_id)
            if data.get("name"):
                self.names[pane_id] = str(data["name"])
            new = str(data.get("agent_status") or "unknown")
            old = self.status.get(pane_id)
            if old != new:  # 状态去重：重复/落后事件不重复输出
                self.emit(pane_id, old or "?", new)
                self.status[pane_id] = new
        elif event == "pane.exited":
            pane_id = data.get("pane_id")
            if not pane_id:
                return
            pane_id = str(pane_id)
            old = self.status.pop(pane_id, None)
            self.emit(pane_id, old or "?", "exited")
            self.names.pop(pane_id, None)


def default_socket() -> str:
    return os.environ.get("HERDR_SOCKET") or str(
        Path.home() / ".config" / "herdr" / "herdr.sock"
    )


def establish(
    args: argparse.Namespace, listener: Listener
) -> tuple[Conn, set[str]]:
    """snapshot 建初态 → 订阅 → 再用独立 snapshot 对账（补订阅空窗）。"""
    snapshot = fetch_snapshot(args.socket, args.timeout)
    listener.status = listener.load_snapshot(snapshot)
    subscribed = set(listener.status)
    conn = Conn(args.socket)
    try:
        subscribe(conn, subscribed, args.timeout)
        gap_snapshot = fetch_snapshot(args.socket, args.timeout)
        listener.reconcile(gap_snapshot, "对账")
    except Exception:
        conn.close()
        raise
    return conn, subscribed


def run_once(args: argparse.Namespace) -> int:
    snapshot = fetch_snapshot(args.socket, args.timeout)
    listener = Listener(args)
    status = listener.load_snapshot(snapshot)
    agents = len(snapshot.get("agents") or [])
    if args.main_pane:
        main = "included" if args.include_main else f"excluded {args.main_pane}"
    else:
        main = "unrestricted"
    print(
        f"[{time.strftime('%H:%M:%S')}] snapshot panes={len(status)} "
        f"agents={agents} main={main}",
        flush=True,
    )
    for pane_id in sorted(status):
        if pane_id == args.main_pane and not args.include_main:
            continue
        print(f"  {status[pane_id]:10} {listener.label(pane_id)} ({pane_id})")
    return 0


def run_listen(args: argparse.Namespace) -> int:
    listener = Listener(args)
    delay = BACKOFF_START
    established = False

    while True:
        conn: Conn | None = None
        try:
            conn, subscribed = establish(args, listener)
            established = True
            delay = BACKOFF_START
            if args.main_pane:
                main = "included" if args.include_main else f"excluded {args.main_pane}"
            else:
                main = "unrestricted"
            rescan = (
                f"{args.rescan_interval:g}s" if args.rescan_interval > 0 else "off"
            )
            print(
                f"[{time.strftime('%H:%M:%S')}] listening panes={len(subscribed)} "
                f"main={main} rescan={rescan}",
                flush=True,
            )
            next_rescan = (
                time.monotonic() + args.rescan_interval
                if args.rescan_interval > 0
                else None
            )

            while True:
                timeout = (
                    None if next_rescan is None else max(0.0, next_rescan - time.monotonic())
                )
                msg = conn.read(timeout)

                if msg is None:  # 定期对账 + 发现新 pane
                    try:
                        snapshot = fetch_snapshot(args.socket, args.timeout)
                    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
                        print(
                            f"[{time.strftime('%H:%M:%S')}] rescan failed: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                    else:
                        listener.reconcile(snapshot, "对账")
                        new_panes = set(listener.status) - subscribed
                        if new_panes:
                            raise Reconnect(f"new panes {sorted(new_panes)}")
                    next_rescan = time.monotonic() + args.rescan_interval
                    continue

                if "error" in msg:
                    err = msg.get("error") or {}
                    code = str(err.get("code"))
                    raise Reconnect(f"server error: {code}")
                if "event" in msg:
                    listener.handle_event(msg)
                # 其他（理论上不会出现的响应）忽略。

        except SubscribeError as exc:
            if not established:
                print(
                    f"error: events.subscribe failed: {exc.code} {exc}",
                    file=sys.stderr,
                )
                return 1
            reason = f"events.subscribe failed: {exc.code}"
        except (ConnectionError, OSError, TimeoutError, ValueError) as exc:
            if not established:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            reason = f"{type(exc).__name__}: {exc}"
        except Reconnect as exc:
            reason = exc.reason
        except RuntimeError as exc:
            if not established:
                print(f"error: {exc}", file=sys.stderr)
                return 1
            reason = str(exc)
        finally:
            if conn is not None:
                conn.close()

        # 首次成功建立后，断线/events_lost 指数退避重连。
        print(
            f"[{time.strftime('%H:%M:%S')}] reconnect in {delay:.1f}s: {reason}",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(delay)
        delay = min(delay * 2, BACKOFF_MAX)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="只读监听 Herdr socket 事件，状态边沿输出一行"
    )
    parser.add_argument("--socket", default=default_socket(), help="Herdr socket 路径")
    parser.add_argument("--once", action="store_true", help="只打印 snapshot 摘要后退出")
    parser.add_argument("--debug", action="store_true", help="原始事件打印到 stderr")
    parser.add_argument(
        "--main-pane",
        default=None,
        help="要排除的主控 pane id（通常是编排者所在 pane）；不指定=不排除任何 pane",
    )
    parser.add_argument(
        "--include-main",
        action="store_true",
        help="连 --main-pane 指定的主控 pane 一起输出",
    )
    parser.add_argument(
        "--rescan-interval",
        type=float,
        default=30.0,
        help="定期 snapshot 对账/发现新 pane 的秒数；0=关闭（默认 30）",
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0, help="请求超时秒数（默认 5）"
    )
    args = parser.parse_args()

    if args.once:
        return run_once(args)
    return run_listen(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
