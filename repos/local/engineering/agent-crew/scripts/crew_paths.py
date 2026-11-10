#!/usr/bin/env python3
"""运行时状态目录解析（唯一实现）：crew_dir(root) = <git-common-dir>/crew。

契约（arch-proposal-v1 §9.1）：
- git 仓：`git rev-parse --git-common-dir` → `<common>/crew`；linked worktree 共享主仓状态；
- 非 git（git 明确报 not a git repository）：回退 `<root>/.crew`；
- 其他失败（权限/损坏/超时）：fail closed，不静默回退。
"""

from __future__ import annotations

import subprocess
from pathlib import Path


class CrewPathsError(RuntimeError):
    """运行时目录解析失败；调用方按环境错误处理（退出码 3）。"""


def crew_dir(root: Path | str) -> Path:
    root = Path(root).expanduser().resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False,
        )
    except OSError as exc:
        raise CrewPathsError(f"git 不可用：{exc}") from exc
    if proc.returncode == 0 and proc.stdout.strip():
        common = Path(proc.stdout.strip())
        if not common.is_absolute():
            common = (root / common).resolve()
        return common / "crew"
    if "not a git repository" in (proc.stderr or "").lower():
        return root / ".crew"
    raise CrewPathsError(
        f"无法解析 git common dir（rc={proc.returncode}）：{proc.stderr.strip()}"
    )
