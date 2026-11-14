#!/usr/bin/env python3
"""工位容量计数与配置（2.2.31 工单 07B，D7 快审 PASS 后实现）。

计数口径（dispatch capacity / crew-preflight / crew-guard / pane-audit 共用同一
函数，四处数字可对账）——本仓 crew 工位 = herdr agent 满足：
①cwd 在本仓或 linked worktree 内（cwd 缺失/不可解析沿用包含，同 guard 口径；
  跨 workspace 项目不误计）；②agent name 非空（个人 TUI 无 name 自然排除）；
③非总舵（name == helm 或 Captain 前缀）；④非白名单工位（Captain·/流程优化·
按 agent 名前缀；tab label 不作为白名单判定——可伪造）。headless
`crew-claude.py run` 不占 pane，不计入。白名单 tab 不参与计数判定。

配置：`orchestration.json` `capacity: {warn, hard}`，缺省 7/10（Captain 直令
「超 7 警告、超 10 强制」）；夹取 warn ≥ 1、hard ≥ warn；非法值回退缺省。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

DEFAULT_CAPACITY_WARN = 7
DEFAULT_CAPACITY_HARD = 10
DEFAULT_WHITELIST_PREFIXES = ("Captain·", "流程优化·")


def load_capacity_config(root: Path) -> dict:
    """读 `orchestration.json` `capacity` 段；缺省/非法回退 7/10，夹取
    warn≥1、hard≥warn（D7 建议 6：init DEFAULT_CONFIG/doctor 同批口径）。"""
    config = {"warn": DEFAULT_CAPACITY_WARN, "hard": DEFAULT_CAPACITY_HARD}
    try:
        data = json.loads((root / ".planning" / "orchestration.json")
                          .read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    section = data.get("capacity") if isinstance(data, dict) else None
    if not isinstance(section, dict):
        return config
    warn, hard = config["warn"], config["hard"]
    value = section.get("warn")
    if not (isinstance(value, int) and not isinstance(value, bool) and value >= 1):
        return config  # warn 非法：整段回退缺省（1 ≤ warn < hard 前提破坏）
    warn = value
    value = section.get("hard")
    if isinstance(value, int) and not isinstance(value, bool) and value >= 1:
        hard = value
    return {"warn": warn, "hard": max(hard, warn)}  # 夹取 hard ≥ warn


def project_roots(root: Path) -> set[Path]:
    """本仓 + linked worktree 根集合（cwd 圈定用；git 不可用回退本仓单根）。"""
    roots = {Path(root).resolve()}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            text=True, capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return roots
    if proc.returncode != 0:
        return roots
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            try:
                roots.add(Path(line[len("worktree "):].strip()).resolve())
            except (OSError, ValueError):
                continue
    return roots


def _cwd_in_project(agent: dict, roots: set[Path]) -> bool:
    """cwd 缺失/不可解析 → True（包含，同 guard 口径）；有值则须命中根前缀。"""
    cwd = str(agent.get("cwd") or agent.get("foreground_cwd") or "").strip()
    if not cwd:
        return True
    try:
        resolved = Path(cwd).resolve()
    except (OSError, ValueError):
        return True
    return any(resolved == item or item in resolved.parents for item in roots)


def count_crew_agents(
    agents: list[dict],
    root: Path,
    helm_name: str = "",
    whitelist_prefixes: tuple[str, ...] = DEFAULT_WHITELIST_PREFIXES,
    roots: set[Path] | None = None,
) -> dict:
    """按模块 docstring 口径计数本仓 crew 工位。

    返回 {"total", "names", "excludedHelm", "excludedWhitelist",
    "excludedExternal"}；helm 豁免（换舵是替换、净工位不增，D7 B1）与白名单/
    外部项目分别计数供文案展示。
    """
    roots = roots or project_roots(root)
    names: list[str] = []
    excluded_helm: list[str] = []
    excluded_whitelist: list[str] = []
    excluded_external = 0
    for agent in agents or []:
        if not isinstance(agent, dict):
            continue
        name = str(agent.get("name") or "").strip()
        if not name:
            continue  # 个人 TUI（无 name）自然排除
        if not _cwd_in_project(agent, roots):
            excluded_external += 1
            continue
        if (helm_name and name == helm_name) or name.startswith("Captain"):
            excluded_helm.append(name)
            continue
        if any(name.startswith(prefix) for prefix in whitelist_prefixes):
            excluded_whitelist.append(name)
            continue
        names.append(name)
    return {
        "total": len(names),
        "names": sorted(names),
        "excludedHelm": sorted(excluded_helm),
        "excludedWhitelist": sorted(excluded_whitelist),
        "excludedExternal": excluded_external,
    }
