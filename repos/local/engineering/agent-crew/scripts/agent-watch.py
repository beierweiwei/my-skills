#!/usr/bin/env python3
"""后台监视 Herdr Agent 状态，状态变化时发通知。

只读；不等待、不压缩、不杀进程。用于异步编排：编排者继续做别的事，
状态变化由 Herdr 通知提醒。

用法：
  python3 scripts/agent-watch.py --once
  python3 scripts/agent-watch.py --interval 15
  python3 scripts/agent-watch.py --interval 15 --workspace wX --agent demo-p1
  python3 scripts/agent-watch.py --once --no-notify --workspace wX --state-file /tmp/aw-wX.state
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def herdr_agents() -> list[dict]:
    env = {**os.environ, "HERDR_ENV": "1"}
    proc = subprocess.run(
        ["herdr", "agent", "list"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "herdr agent list failed")
    payload = json.loads(proc.stdout)
    return payload.get("result", {}).get("agents", [])


def agent_key(agent: dict) -> str:
    return str(agent.get("name") or agent.get("pane_id") or "unknown")


def agent_label(agent: dict) -> str:
    name = agent.get("name") or agent.get("pane_id") or "unknown"
    workspace = agent.get("workspace_id") or "?"
    tab = agent.get("tab_id") or "?"
    return f"{name} ({workspace}:{tab})"


SEP = "/"


def state_key(agent: dict) -> str:
    """状态键 = workspace + agent：不同 workspace 同名 agent 互不覆盖。"""
    return f"{agent.get('workspace_id') or '?'}{SEP}{agent_key(agent)}"


def in_scope(key: str, workspace: str | None, filters: set[str]) -> bool:
    """该状态键是否属于本次运行范围（= 可被本次结果覆盖或判为消失）。

    无 workspace 前缀的旧格式键一律视为不在范围内，升级后静默淘汰，
    避免一次性误报「已消失」。
    """
    ws, sep, name = key.partition(SEP)
    if not sep:
        return False
    if workspace is not None and ws != workspace:
        return False
    if filters and name not in filters:
        return False
    return True


def diff_states(
    current: dict[str, str],
    previous: dict[str, str],
    workspace: str | None = None,
    filters: set[str] | None = None,
    notify_initial: bool = False,
) -> list[tuple[str, str | None, str | None]]:
    """返回 (key, 旧状态, 新状态)；新状态为 None 表示已消失。"""
    filters = set(filters or ())
    changes: list[tuple[str, str | None, str | None]] = []
    for key, status in current.items():
        old = previous.get(key)
        if old is None:
            if notify_initial:
                changes.append((key, None, status))
        elif old != status:
            changes.append((key, old, status))
    for key, old in previous.items():
        if key in current:
            continue
        if in_scope(key, workspace, filters):
            changes.append((key, old, None))
    return changes


def merge_state(
    previous: dict[str, str],
    current: dict[str, str],
    workspace: str | None,
    filters: set[str],
) -> dict[str, str]:
    """保留范围外的历史基线，范围内用本次结果覆盖（含淘汰已消失项）。"""
    kept = {
        key: value
        for key, value in previous.items()
        if SEP in key and not in_scope(key, workspace, filters)
    }
    kept.update(current)
    return kept


def notify(title: str, body: str) -> None:
    if os.environ.get("HERDR_ENV") != "1":
        return
    subprocess.run(
        ["herdr", "notification", "show", title, "--body", body],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def load_state(path: Path) -> dict[str, str]:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0, help="0=只跑一次；>0 循环秒数")
    parser.add_argument("--once", action="store_true", help="只跑一次（等价 --interval 0）")
    parser.add_argument("--workspace", help="只看指定 workspace_id")
    parser.add_argument("--agent", action="append", default=[], help="只看指定 agent 名或 pane_id（可重复）")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--notify-initial", action="store_true", help="首次发现 Agent 也通知")
    parser.add_argument("--state-file", help="状态去重文件（默认 .git/agent-watch.state）")
    args = parser.parse_args()
    if args.once:
        args.interval = 0

    common = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"],
        text=True,
        capture_output=True,
        check=False,
    ).stdout.strip()
    state_path = Path(args.state_file) if args.state_file else Path(common or ".") / "agent-watch.state"

    while True:
        try:
            agents = herdr_agents()
        except Exception as exc:  # noqa: BLE001
            print(f"error: {exc}", file=sys.stderr)
            return 1

        filters = set(args.agent)
        if args.workspace:
            agents = [a for a in agents if str(a.get("workspace_id")) == args.workspace]
        if filters:
            agents = [
                a
                for a in agents
                if agent_key(a) in filters or str(a.get("pane_id")) in filters
            ]

        current = {state_key(a): str(a.get("agent_status") or "unknown") for a in agents}
        labels = {state_key(a): agent_label(a) for a in agents}
        previous = load_state(state_path)
        changes = diff_states(current, previous, args.workspace, filters, args.notify_initial)

        print(f"[{time.strftime('%H:%M:%S')}] agents={len(agents)}")
        for agent in agents:
            print(f"  {str(agent.get('agent_status') or 'unknown'):10} {agent_label(agent)}")
        if changes:
            lines = [
                f"{labels.get(key, key)} → {new}"
                if old is None
                else f"{labels.get(key, key)}: {old} → {new or '已消失'}"
                for key, old, new in changes
            ]
            print("状态变化：")
            for line in lines:
                print(f"  - {line}")
            if not args.no_notify:
                notify("Agent 状态变化", "\n".join(lines))

        merged = merge_state(previous, current, args.workspace, filters)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(merged, ensure_ascii=False, sort_keys=True))

        if args.interval <= 0:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except Exception as exc:  # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
