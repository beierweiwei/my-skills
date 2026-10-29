#!/usr/bin/env python3
"""总舵启动配置读取与 agent 名派生（工单 D-helm-fix：herdr agent 名同机全局唯一）。

用法：python3 helm_agent_name.py <项目根>
输出：可直接 eval 的 KEY=value 赋值行（供 start-helm.sh 消费）：
  AGENT_NAME      herdr agent 实例名（同机全局唯一）
  TAB_LABEL       总舵 tab 标签（默认「总舵」）
  EXPECTED_MODEL  期望模型（默认空=只提示实测值，不做断言）
  PORTFOLIO_DIR   portfolio 目录（默认 .planning/portfolio）
  DOC_CONTEXT     术语表文档名（默认 CONTEXT.md）

agent 名规则：配置 helm.agentName 优先（用户自负合法性）；
未配置时派生 helm-<projectName slug>（projectName 取配置，空则用目录名）；
slugify：小写、[a-z0-9_-] 以外字符收敛为单个 -、去首尾 -；结果为空时兜底 project
（纯中文目录名等场景；撞名由 start-helm.sh 友好提示引导显式配置）。
配置文件缺失/损坏时按内置默认处理（与 plan-status 口径一致，不静默改写配置）。"""

from __future__ import annotations

import json
import pathlib
import re
import shlex
import sys

FIXED_SLUG = "project"


def slugify(name: str) -> str:
    """herdr agent 名合法化：小写、非法字符收敛为单个 -、去首尾 -。"""
    s = name.strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "-", s)
    return s.strip("-")


def load_config(root: pathlib.Path) -> dict:
    p = root / ".planning" / "orchestration.json"
    if not p.exists():
        return {}
    try:
        cfg = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        print(f"警告：配置文件损坏（{p}），已用内置默认值；请修复或删除后重跑。", file=sys.stderr)
        return {}
    return cfg if isinstance(cfg, dict) else {}


def get(cfg: dict, *keys, default=""):
    cur = cfg
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return default if cur is None else cur


def resolve_agent_name(root: pathlib.Path, cfg: dict | None = None) -> str:
    """helm 的 herdr agent 实例名：配置 helm.agentName 优先，否则派生。"""
    if cfg is None:
        cfg = load_config(root)
    configured = str(get(cfg, "helm", "agentName", default="")).strip()
    if configured:
        return configured
    slug = slugify(str(get(cfg, "projectName", default="")))
    if not slug:
        slug = slugify(root.name) or FIXED_SLUG
    return f"helm-{slug}"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"用法：{pathlib.Path(argv[0]).name} <项目根>", file=sys.stderr)
        return 2
    root = pathlib.Path(argv[1]).resolve()
    cfg = load_config(root)
    out = [
        ("AGENT_NAME", resolve_agent_name(root, cfg)),
        ("TAB_LABEL", str(get(cfg, "helm", "tabLabel", default="总舵"))),
        ("EXPECTED_MODEL", str(get(cfg, "helm", "expectedModel", default=""))),
        ("PORTFOLIO_DIR", str(get(cfg, "portfolioDir", default=".planning/portfolio"))),
        ("DOC_CONTEXT", str(get(cfg, "docNames", "context", default="CONTEXT.md"))),
    ]
    for key, value in out:
        print(f"{key}={shlex.quote(value)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
