#!/usr/bin/env python3
"""只读监控 Git worktree 的未合并/未提交状态。

状态变化时通过 Herdr 发通知；不执行合并、不删除 worktree。
用法：
  python3 scripts/worktree-watch.py --once
  python3 scripts/worktree-watch.py --interval 60
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], text=True, capture_output=True, check=check
    )


def parse_worktrees() -> list[dict[str, str]]:
    out = git("worktree", "list", "--porcelain").stdout
    items: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in out.splitlines():
        if not line.strip():
            if current:
                items.append(current)
                current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    if current:
        items.append(current)
    return items


def inspect(path: Path, branch: str, base: str) -> tuple[str, bool, bool]:
    if not path.exists():
        return "STALE", False, False
    dirty = bool(git("-C", str(path), "status", "--porcelain", check=False).stdout.strip())
    merged = (
        git("-C", str(path), "merge-base", "--is-ancestor", "HEAD", base, check=False).returncode
        == 0
    )
    if branch == "(detached)":
        if dirty:
            return "DIRTY+DETACHED", dirty, merged
        return ("DETACHED" if merged else "DETACHED+UNMERGED"), dirty, merged
    if dirty and not merged:
        return "DIRTY+UNMERGED", dirty, merged
    if dirty:
        return "DIRTY", dirty, merged
    return ("MERGED" if merged else "UNMERGED"), dirty, merged


def notify(title: str, body: str) -> None:
    if os.environ.get("HERDR_ENV") != "1":
        return
    subprocess.run(
        ["herdr", "notification", "show", title, "--body", body],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=0, help="0=只跑一次；>0 循环秒数")
    parser.add_argument("--base", default="master", help="合并目标分支（默认 master）")
    parser.add_argument("--no-notify", action="store_true")
    parser.add_argument("--state-file", help="通知去重状态文件")
    args = parser.parse_args()

    root = Path(git("rev-parse", "--show-toplevel").stdout.strip())
    common = Path(git("rev-parse", "--git-common-dir").stdout.strip())
    state = Path(args.state_file) if args.state_file else common / "worktree-watch.state"

    while True:
        rows: list[tuple[str, str, str]] = []
        alerts: list[str] = []
        for wt in parse_worktrees():
            path = Path(wt.get("worktree", ""))
            if not path or path.resolve() == root.resolve():
                continue
            branch = wt.get("branch", "")
            if branch.startswith("refs/heads/"):
                branch = branch[len("refs/heads/"):]
            branch = branch or "(detached)"
            status, _, _ = inspect(path, branch, args.base)
            rows.append((branch, status, str(path)))
            if status != "MERGED":
                alerts.append(f"{branch} [{status}] {path}")

        print(f"[{time.strftime('%H:%M:%S')}] linked worktrees={len(rows)}")
        for branch, status, path in rows:
            print(f"  {status:14} {branch:24} {path}")
        if not rows:
            print("  没有 linked worktree（只有主 worktree）")

        if alerts:
            summary = "\n".join(alerts)
            digest = hashlib.sha256(summary.encode()).hexdigest()
            previous = state.read_text().strip() if state.exists() else ""
            if digest != previous:
                print("需要处理：")
                for alert in alerts:
                    print(f"  - {alert}")
                if not args.no_notify:
                    notify("有未合并的 worktree", summary)
                state.write_text(digest)
        else:
            if state.exists():
                state.unlink()

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
