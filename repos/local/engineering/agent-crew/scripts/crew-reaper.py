#!/usr/bin/env python3
"""窗口对账回收（crew-reaper）：默认 dry-run 只报告，--apply 才安全回收旧总舵/旧 helm。

对账三份数据：dispatch 注册表（`<crew-dir>/dispatch-agents.json`）、现役总舵
（`<crew-dir>/guard.handoff` 优先，registry 中 role=helm 的最新 startedAt 次选）、
herdr 的 tab/pane/agent 现状。多个「总舵」tab 只保留现役；其余仅在全部 pane
为空或 registry agent idle/done、无 working/blocked/unknown、tab 非聚焦、tab
非白名单时，先 `dispatch pane close` 再 `dispatch tab close`。旧 helm agent 残留
（handoff.old / registry 中非现役 helm）按同一安全口径回收；working/blocked/
unknown 永不关闭。无法判定现役（缺 handoff、registry 无 helm、多候选并列、
两源矛盾、字段不一致）→ 只报告，不执行任何关闭。

分工：本脚本只处理「总舵 tab」与「旧 helm agent 残留」；完成流 tab、一般空
pane / 空 tab 的巡检回收仍由 pane-audit 负责（working 永不关、聚焦/白名单跳过、
空 pane 用 process-info 确认空闲交互 shell）。本脚本与 guard/pane-audit 新增的
总舵/旧 helm 回收统一走 dispatch.py；pane-audit 既有的一般空 pane / 空 tab /
完成流 tab 回收仍直写 herdr（存量行为，不在本脚本范围）。

数据源：默认 `herdr tab list` + `pane list` + `agent list`（只读）；`--snapshot
FILE` 注入 fixture JSON（仅 dry-run，与 `--apply` 互斥；不调用 herdr，空 pane
一律按无法确认只报告）。`--apply` 的每个关闭动作都走 `python3 scripts/dispatch.py
--root <root> pane close` / `tab close`；dispatch 调用失败只记录并继续其余安全项，
最终退出码仍为 0。

best-effort 边界：回收基于本轮快照，关闭前不重新取数/二次复检——快照与关闭之间
被人工开始使用的 pane/tab 仍可能被关；workspace 无法定位（快照/字段缺失）时零关闭、
只报告；先跑 dry-run 核对再 --apply。

退出码：0 成功（含无可回收、无法判定现役）；1 参数/数据错误（snapshot/herdr/
registry/crew-dir 读取失败）；2 `--snapshot` 与 `--apply` 同时给出（互斥拒绝，
fixture 快照可能已过期）；参数解析错误也返回 1，不沿用 argparse 的 2。

用法：
  python3 scripts/crew-reaper.py                      # dry-run 只报告
  python3 scripts/crew-reaper.py --apply              # 安全回收旧总舵/旧 helm
  python3 scripts/crew-reaper.py --json --apply       # 机器可读（guard 周期调用）
  python3 scripts/crew-reaper.py --snapshot snap.json --crew-dir <crew-dir> --root .
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402


def _load_pane_audit():
    """复用 pane-audit 的 snapshot 归一、总舵识别与空 pane 探针（只读写同一语义）。"""
    path = Path(__file__).resolve().with_name("pane-audit.py")
    spec = importlib.util.spec_from_file_location("pane_audit_shared", path)
    if spec is None or spec.loader is None:  # pragma: no cover - 安装损坏才触发
        raise ImportError(f"无法加载 pane-audit.py：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("pane_audit_shared", module)
    spec.loader.exec_module(module)
    return module


pa = _load_pane_audit()

DISPATCH_PATH = Path(__file__).resolve().with_name("dispatch.py")
DECISION_TEXT = {
    "keep": "保留",
    "close": "回收",
    "report": "只报告",
    "skip": "跳过",
}


class ReaperError(RuntimeError):
    """参数/取数/解析错误；main 捕获后返回 1。"""


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

def herdr_json(args: list[str]) -> dict:
    try:
        proc = subprocess.run(
            ["herdr", *args], text=True, capture_output=True, check=False, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReaperError(f"无法执行 herdr {' '.join(args)}：{exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"退出码 {proc.returncode}"
        raise ReaperError(f"herdr {' '.join(args)} 失败：{detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise ReaperError(f"herdr {' '.join(args)} JSON 解析失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise ReaperError(f"herdr {' '.join(args)} 返回非对象 JSON")
    return payload


def _result_entries(payload: dict, key: str) -> list[dict]:
    result = payload.get("result")
    items = result.get(key) if isinstance(result, dict) else None
    if not isinstance(items, list):
        raise ReaperError(f"herdr 返回缺少 result.{key}（{key} 应为数组）")
    return items


def load_live_snapshot() -> dict:
    tabs = _result_entries(herdr_json(["tab", "list"]), "tabs")
    panes = _result_entries(herdr_json(["pane", "list"]), "panes")
    agents = _result_entries(herdr_json(["agent", "list"]), "agents")
    try:
        return pa.normalize_snapshot({"tabs": tabs, "panes": panes, "agents": agents})
    except pa.AuditError as exc:
        raise ReaperError(f"herdr 数据格式非法：{exc}") from exc


def load_snapshot(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise ReaperError(f"无法读取 snapshot 文件 {path}：{exc}") from exc
    except ValueError as exc:
        raise ReaperError(f"snapshot JSON 解析失败 {path}：{exc}") from exc
    try:
        return pa.normalize_snapshot(data)
    except pa.AuditError as exc:
        raise ReaperError(f"snapshot 格式非法：{exc}") from exc


def read_handoff(crew_dir: Path) -> dict[str, str]:
    """读取 `<crew-dir>/guard.handoff`（key=value 行；缺 phase 视为无 handoff）。"""
    try:
        text = (crew_dir / "guard.handoff").read_text(encoding="utf-8")
    except OSError:
        return {}
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            fields[key] = value.strip()
    return fields if fields.get("phase") else {}


def load_registry(crew_dir: Path) -> dict:
    path = crew_dir / "dispatch-agents.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReaperError(f"dispatch 注册表读取/解析失败 {path}：{exc}") from exc
    if not isinstance(data, dict):
        raise ReaperError(f"dispatch 注册表格式非法（应为对象）：{path}")
    return {
        str(name): info for name, info in data.items() if isinstance(info, dict)
    }


# ---------------------------------------------------------------------------
# 现役总舵判定
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CurrentHelm:
    agent: str = ""
    pane: str = ""
    tab: str = ""
    source: str = "unknown"  # handoff:new / handoff:old / registry:<agent> / unknown
    reason: str = ""  # 仅 unknown 时给出原因


@dataclass(frozen=True)
class RegistryHelm:
    name: str
    info: dict


def _is_failed_handoff(handoff: dict[str, str]) -> bool:
    return handoff.get("phase") == "done" and handoff.get("result") == "failed"


def pick_registry_helm(registry: dict) -> tuple[RegistryHelm | None, str]:
    """role=helm 的最新 startedAt；多条并列/缺 startedAt 时返回 (None, 原因)。"""
    entries = [
        (name, info) for name, info in registry.items()
        if str(info.get("role") or "") == "helm"
    ]
    if not entries:
        return None, "registry 无 role=helm 记录"
    if len(entries) == 1:
        name, info = entries[0]
        return RegistryHelm(name, info), ""
    timestamps = [
        (str(info.get("startedAt") or "").strip(), name, info)
        for name, info in entries
    ]
    if any(not ts for ts, _name, _info in timestamps):
        return None, "registry 多个 helm 且 startedAt 缺失，无法排序"
    latest = max(ts for ts, _name, _info in timestamps)
    latest_entries = [(name, info) for ts, name, info in timestamps if ts == latest]
    if len(latest_entries) > 1:
        return None, f"registry 多个 helm 最新 startedAt 并列（{latest}），无法判定"
    name, info = latest_entries[0]
    return RegistryHelm(name, info), ""


def resolve_current_helm(
    handoff: dict[str, str], registry: dict, snapshot: dict,
) -> CurrentHelm:
    """handoff 优先、registry 次选；两源矛盾/字段不一致/多候选 → unknown。"""
    panes_by_id = {
        str(pane.get("pane_id")): pane
        for pane in snapshot.get("panes") or [] if pane.get("pane_id")
    }
    tab_ids = {
        str(tab.get("tab_id"))
        for tab in snapshot.get("tabs") or [] if tab.get("tab_id")
    }
    agents_by_name: dict[str, list[dict]] = {}
    for agent in snapshot.get("agents") or []:
        name = str(agent.get("name") or "")
        if name:
            agents_by_name.setdefault(name, []).append(agent)

    phase = str(handoff.get("phase") or "").strip()
    if _is_failed_handoff(handoff):
        # 失败继任可能已 dispatch start 进 registry：不信任 registry 次选，直接只报告。
        return CurrentHelm(
            source="unknown",
            reason="handoff 上次交接失败（result=failed），现役不确定，只用报告",
        )
    handoff_name = ""
    pane = ""
    tab = ""
    source = ""
    if phase in ("started", "done", "warn"):
        name_key = "old" if phase == "warn" else "new"
        handoff_name = str(handoff.get(name_key) or "").strip()
        if not handoff_name:
            return CurrentHelm(
                source="unknown",
                reason=f"handoff phase={phase} 缺字段 {name_key}，无法判定现役",
            )
        pane_key = "oldPane" if phase == "warn" else "newPane"
        tab_key = "oldTab" if phase == "warn" else "newTab"
        pane = str(handoff.get(pane_key) or "").strip()
        tab = str(handoff.get(tab_key) or "").strip()
        source = "handoff:old" if phase == "warn" else "handoff:new"

    registry_helm, registry_note = pick_registry_helm(registry)

    if handoff_name:
        if registry_helm is not None and registry_helm.name != handoff_name:
            return CurrentHelm(
                source="unknown",
                reason=(f"handoff:{handoff_name} 与 registry 最新 helm:"
                        f"{registry_helm.name} 矛盾，无法判定现役"),
            )
        registry_pane = ""
        if registry_helm is not None and registry_helm.name == handoff_name:
            registry_pane = str(registry_helm.info.get("pane") or "").strip()
            if pane and registry_pane and pane != registry_pane:
                return CurrentHelm(
                    source="unknown",
                    reason=f"handoff pane {pane} 与 registry pane {registry_pane} 不一致",
                )
        matches = agents_by_name.get(handoff_name, [])
        if len(matches) > 1:
            return CurrentHelm(
                source="unknown",
                reason=f"agent list 中同名 helm {handoff_name} 多条，无法判定",
            )
        if matches:
            agent_pane = str(matches[0].get("pane_id") or "").strip()
            if pane and agent_pane and pane != agent_pane:
                return CurrentHelm(
                    source="unknown",
                    reason=f"handoff pane {pane} 与 agent list pane {agent_pane} 不一致",
                )
            pane = pane or agent_pane
            tab = tab or str(matches[0].get("tab_id") or "").strip()
        pane = pane or registry_pane
        if pane and pane in panes_by_id:
            pane_tab = str(panes_by_id[pane].get("tab_id") or "").strip()
            if tab and pane_tab and tab != pane_tab:
                return CurrentHelm(
                    source="unknown",
                    reason=f"handoff tab {tab} 与 pane list tab {pane_tab} 不一致",
                )
            tab = tab or pane_tab
        if not tab:
            return CurrentHelm(
                source="unknown",
                reason="无法定位现役总舵 tab（handoff/agent/pane 均缺失）",
            )
        if tab not in tab_ids:
            return CurrentHelm(
                source="unknown", reason=f"现役总舵 tab {tab} 不在 herdr tab list",
            )
        return CurrentHelm(handoff_name, pane, tab, source)

    if registry_helm is None:
        reasons = []
        if not phase:
            reasons.append("缺 guard.handoff")
        elif _is_failed_handoff(handoff):
            reasons.append("handoff 上次交接失败（result=failed）")
        else:
            reasons.append(f"handoff phase={phase} 无法识别")
        if registry_note:
            reasons.append(registry_note)
        return CurrentHelm(source="unknown", reason="；".join(reasons))

    name = registry_helm.name
    info = registry_helm.info
    pane = str(info.get("pane") or "").strip()
    tab = ""
    matches = agents_by_name.get(name, [])
    if len(matches) > 1:
        return CurrentHelm(
            source="unknown", reason=f"agent list 中同名 helm {name} 多条，无法判定",
        )
    if matches:
        agent_pane = str(matches[0].get("pane_id") or "").strip()
        if pane and agent_pane and pane != agent_pane:
            return CurrentHelm(
                source="unknown",
                reason=f"registry pane {pane} 与 agent list pane {agent_pane} 不一致",
            )
        pane = pane or agent_pane
        tab = str(matches[0].get("tab_id") or "").strip()
    if pane and pane in panes_by_id:
        pane_tab = str(panes_by_id[pane].get("tab_id") or "").strip()
        if tab and pane_tab and tab != pane_tab:
            return CurrentHelm(
                source="unknown",
                reason=f"agent tab {tab} 与 pane list tab {pane_tab} 不一致",
            )
        tab = tab or pane_tab
    if not tab:
        return CurrentHelm(
            source="unknown",
            reason=(f"registry 现役 helm {name} 无法定位 tab"
                    f"（pane {pane or '缺失'} 不在 pane list）"),
        )
    if tab not in tab_ids:
        return CurrentHelm(
            source="unknown", reason=f"现役总舵 tab {tab} 不在 herdr tab list",
        )
    return CurrentHelm(name, pane, tab, f"registry:{name}")


def stale_helm_names(
    handoff: dict[str, str], registry: dict, current_name: str,
) -> set[str]:
    """旧 helm 身份：handoff.old + registry 中非现役的 role=helm 记录。"""
    names: set[str] = set()
    if handoff.get("phase") in ("started", "done", "warn") \
            and not _is_failed_handoff(handoff):
        old = str(handoff.get("old") or "").strip()
        if old:
            names.add(old)
    for name, info in registry.items():
        if str(info.get("role") or "") == "helm":
            names.add(name)
    names.discard(current_name)
    return names


# ---------------------------------------------------------------------------
# 对账报告
# ---------------------------------------------------------------------------

@dataclass
class HelmTabView:
    tab_id: str
    label: str
    pane_count: int
    agents: list[dict]
    decision: str = "report"  # keep / close / report / skip
    reason: str = ""


@dataclass
class OldHelmFinding:
    name: str
    pane: str
    tab: str
    agent_status: str
    decision: str = "report"  # keep / close / report / skip
    reason: str = ""


@dataclass
class Action:
    action: str  # "pane close" / "tab close"
    target: str
    reason: str
    tab: str = ""  # pane 动作所属 tab（tab 动作为空）
    status: str = "planned"  # would_close / closed / failed / skipped


@dataclass
class Report:
    current: CurrentHelm
    helm_tabs: list[HelmTabView] = field(default_factory=list)
    old_helm: list[OldHelmFinding] = field(default_factory=list)
    actions: list[Action] = field(default_factory=list)
    workspace_note: str = ""


def _tab_focused(tab: dict | None, tab_panes: list[dict]) -> bool:
    if (tab or {}).get("focused") is True:
        return True
    return any(pane.get("focused") is True for pane in tab_panes)


def _pane_closeable(
    pane: dict, agent: dict | None, probe,
) -> tuple[bool, str]:
    """单个 pane 是否可安全关闭：registry agent 只认 idle/done，空 pane 需探针确认。"""
    pane_id = str(pane.get("pane_id") or "")
    if agent is not None:
        name = str(agent.get("name") or "?")
        status = str(agent.get("agent_status") or "")
        if status in pa.IDLE_AGENT_STATUSES:
            return True, f"registry agent {name} {status}"
        return False, f"agent {name} 状态 {status or 'unknown'}"
    if pane.get("agent"):
        return False, "pane 内有未进 registry 的 TUI"
    status = str(pane.get("agent_status") or "")
    if status not in pa.EMPTY_PANE_STATUSES:
        return False, f"pane 状态 {status or 'unknown'}"
    if probe is None:
        return False, "无 registry pane 需 process-info 确认空闲 shell（当前未确认）"
    try:
        confirmed = probe(pane_id)
    except Exception as exc:  # noqa: BLE001 - 探针异常按无法确认（只报告）
        return False, f"process-info 探针异常（{type(exc).__name__}）"
    if confirmed is True:
        return True, "process-info 确认空闲交互 shell"
    if confirmed is False:
        return False, "process-info 显示前台非空闲 shell"
    return False, "process-info 无法确认空闲 shell"


def _pane_group_closeable(
    tab_panes: list[dict], agents_by_pane: dict[str, dict], probe,
) -> tuple[bool, list[str]]:
    problems: list[str] = []
    for pane in sorted(tab_panes, key=lambda item: str(item.get("pane_id") or "")):
        pane_id = str(pane.get("pane_id") or "")
        if not pane_id:
            problems.append("存在缺 pane_id 的 pane")
            continue
        ok, reason = _pane_closeable(pane, agents_by_pane.get(pane_id), probe)
        if not ok:
            problems.append(f"{pane_id}: {reason}")
    return not problems, problems


def _current_workspace(current: CurrentHelm, snapshot: dict) -> str:
    """现役总舵所在 workspace；解析不到返回空串（fixture/数据缺失时不做收窄）。"""
    tabs_by_id = {
        str(tab.get("tab_id")): tab for tab in snapshot.get("tabs") or []
        if tab.get("tab_id")
    }
    panes_by_id = {
        str(pane.get("pane_id")): pane for pane in snapshot.get("panes") or []
        if pane.get("pane_id")
    }
    agents_by_pane: dict[str, dict] = {}
    agents_by_name: dict[str, list[dict]] = {}
    for agent in snapshot.get("agents") or []:
        pane_id = str(agent.get("pane_id") or "")
        if pane_id and pane_id not in agents_by_pane:
            agents_by_pane[pane_id] = agent
        name = str(agent.get("name") or "")
        if name:
            agents_by_name.setdefault(name, []).append(agent)
    if current.tab:
        workspace = str((tabs_by_id.get(current.tab) or {}).get("workspace_id") or "")
        if workspace:
            return workspace
    if current.pane:
        workspace = str((panes_by_id.get(current.pane) or {}).get("workspace_id") or "")
        if workspace:
            return workspace
        workspace = str((agents_by_pane.get(current.pane) or {}).get("workspace_id") or "")
        if workspace:
            return workspace
    if current.agent:
        for agent in agents_by_name.get(current.agent, []):
            workspace = str(agent.get("workspace_id") or "")
            if workspace:
                return workspace
    return ""


def reconcile(
    snapshot: dict,
    registry: dict,
    handoff: dict[str, str],
    *,
    whitelist_prefixes: tuple[str, ...],
    helm_labels: tuple[str, ...],
    empty_probe=None,
) -> Report:
    current = resolve_current_helm(handoff, registry, snapshot)
    unknown = current.source == "unknown"
    report = Report(current=current)

    tabs = snapshot.get("tabs") or []
    panes = snapshot.get("panes") or []
    agents = snapshot.get("agents") or []
    # herdr 列表是全局的：能定位现役 workspace 时只对账该 workspace，绝不跨项目关窗口。
    # 定位不到 workspace（快照/字段缺失）时同样零关闭：无法证明哪些 tab 属于本项目。
    workspace = _current_workspace(current, snapshot)
    if workspace:
        tabs = [
            tab for tab in tabs
            if str(tab.get("workspace_id") or "") == workspace
        ]
        allowed_tab_ids = {str(tab.get("tab_id")) for tab in tabs if tab.get("tab_id")}
        panes = [
            pane for pane in panes
            if str(pane.get("tab_id") or "") in allowed_tab_ids
        ]
        allowed_pane_ids = {
            str(pane.get("pane_id")) for pane in panes if pane.get("pane_id")
        }
        agents = [
            agent for agent in agents
            if str(agent.get("tab_id") or "") in allowed_tab_ids
            or str(agent.get("pane_id") or "") in allowed_pane_ids
        ]
    elif not unknown:
        report.workspace_note = (
            "workspace=unknown（无法从 tab/pane/agent 定位现役总舵 workspace，"
            "不执行任何关闭，只报告）"
        )
    tab_by_id = {
        str(tab.get("tab_id")): tab for tab in tabs if tab.get("tab_id")
    }
    pane_by_id = {
        str(pane.get("pane_id")): pane for pane in panes if pane.get("pane_id")
    }
    panes_by_tab: dict[str, list[dict]] = {}
    for pane in panes:
        pane_id = str(pane.get("pane_id") or "")
        if pane_id:
            panes_by_tab.setdefault(str(pane.get("tab_id") or ""), []).append(pane)
    agents_by_pane: dict[str, dict] = {}
    agents_by_name: dict[str, list[dict]] = {}
    for agent in agents:
        pane_id = str(agent.get("pane_id") or "")
        if pane_id and pane_id not in agents_by_pane:
            agents_by_pane[pane_id] = agent
        name = str(agent.get("name") or "")
        if name:
            agents_by_name.setdefault(name, []).append(agent)

    actions: list[Action] = []
    planned_panes: set[str] = set()
    tab_candidates: dict[str, str] = {}
    protected_tabs: dict[str, str] = {}

    # ① 总舵 tab 对账
    helm_tab_ids = sorted(
        str(tab.get("tab_id")) for tab in tabs
        if tab.get("tab_id") and pa.is_helm_tab(tab, helm_labels)
    )
    for tab_id in helm_tab_ids:
        tab = tab_by_id[tab_id]
        label = str(tab.get("label") or "").strip()
        tab_panes = panes_by_tab.get(tab_id, [])
        tab_pane_ids = [
            str(pane.get("pane_id")) for pane in tab_panes if pane.get("pane_id")
        ]
        view_agents = []
        for pane in tab_panes:
            pane_id = str(pane.get("pane_id") or "")
            agent = agents_by_pane.get(pane_id)
            if agent is not None:
                view_agents.append({
                    "name": str(agent.get("name") or ""),
                    "pane": pane_id,
                    "agent_status": str(agent.get("agent_status") or ""),
                })
        view = HelmTabView(tab_id, label or "-", len(tab_panes), view_agents)
        report.helm_tabs.append(view)

        if tab_id == current.tab or (current.pane and current.pane in tab_pane_ids):
            view.decision = "keep"
            view.reason = "现役总舵 tab（永不关闭）"
            protected_tabs[tab_id] = view.reason
            continue
        if label.startswith(whitelist_prefixes):
            view.decision = "skip"
            view.reason = "白名单前缀，整体跳过"
            protected_tabs[tab_id] = view.reason
            continue
        if unknown:
            view.reason = f"现役总舵无法判定（{current.reason}），只报告"
            protected_tabs[tab_id] = view.reason
            continue
        if not workspace:
            view.reason = "workspace=unknown（无法定位现役总舵 workspace），只报告"
            protected_tabs[tab_id] = view.reason
            continue
        if _tab_focused(tab, tab_panes):
            view.reason = "tab 当前聚焦，只报告"
            protected_tabs[tab_id] = view.reason
            continue
        ok, problems = _pane_group_closeable(tab_panes, agents_by_pane, empty_probe)
        if not ok:
            view.reason = "；".join(problems) + "（只报告）"
            protected_tabs[tab_id] = view.reason
            continue
        view.decision = "close"
        view.reason = "旧总舵 tab：无 working/blocked/聚焦/白名单，pane 均可安全关闭"
        for pane in sorted(tab_panes,
                           key=lambda item: str(item.get("pane_id") or "")):
            pane_id = str(pane.get("pane_id"))
            actions.append(Action("pane close", pane_id, view.reason, tab=tab_id))
            planned_panes.add(pane_id)
        tab_candidates[tab_id] = view.reason

    # ② 旧 helm agent 残留（workspace 未定位时整段跳过：无法证明 pane 属于本项目）
    if not unknown and workspace:
        for name in sorted(stale_helm_names(handoff, registry, current.agent)):
            for agent in agents_by_name.get(name, []):
                pane_id = str(agent.get("pane_id") or "")
                status = str(agent.get("agent_status") or "")
                tab_id = str(agent.get("tab_id") or "") or str(
                    pane_by_id.get(pane_id, {}).get("tab_id") or ""
                )
                finding = OldHelmFinding(
                    name, pane_id or "-", tab_id or "-", status or "unknown",
                )
                report.old_helm.append(finding)
                if pane_id and pane_id == current.pane:
                    finding.decision = "keep"
                    finding.reason = "现役总舵 pane，永不关闭"
                    continue
                if tab_id and tab_id == current.tab:
                    finding.reason = "位于现役总舵 tab，保守只报告"
                    continue
                if tab_id and tab_id in protected_tabs:
                    finding.reason = (
                        f"所属 tab 未通过安全回收（{protected_tabs[tab_id]}），只报告"
                    )
                    continue
                tab_label = str((tab_by_id.get(tab_id) or {}).get("label") or "")
                if tab_label.startswith(whitelist_prefixes):
                    finding.decision = "skip"
                    finding.reason = "白名单前缀，整体跳过"
                    continue
                if tab_id and _tab_focused(
                    tab_by_id.get(tab_id), panes_by_tab.get(tab_id, []),
                ):
                    finding.reason = "tab 当前聚焦，只报告"
                    continue
                if not pane_id:
                    finding.reason = "registry 缺 pane，无法关闭"
                    continue
                if status not in pa.IDLE_AGENT_STATUSES:
                    finding.reason = f"agent 状态 {status or 'unknown'}，不自动关闭"
                    continue
                if pane_id in planned_panes:
                    finding.decision = "close"
                    finding.reason = "已随旧总舵 tab 一并回收"
                    continue
                finding.decision = "close"
                finding.reason = "旧 helm agent 残留且 idle/done，非现役"
                actions.append(Action("pane close", pane_id, finding.reason,
                                      tab=tab_id))
                planned_panes.add(pane_id)
                if tab_id:
                    tab_candidates.setdefault(tab_id, "旧 helm 残留回收后 tab 为空")

    # ③ tab 关闭统一排到所有 pane 关闭之后（先 pane 后 tab）
    for tab_id, reason in sorted(tab_candidates.items()):
        if tab_id == current.tab:
            continue
        tab = tab_by_id.get(tab_id) or {}
        label = str(tab.get("label") or "")
        if label.startswith(whitelist_prefixes):
            continue
        if _tab_focused(tab, panes_by_tab.get(tab_id, [])):
            continue
        remaining = [
            str(pane.get("pane_id"))
            for pane in panes_by_tab.get(tab_id, [])
            if str(pane.get("pane_id")) not in planned_panes
        ]
        if remaining:
            continue
        actions.append(Action("tab close", tab_id, reason))

    report.actions = actions
    return report


def report_payload(report: Report, applied: bool) -> dict:
    findings: list[dict] = []
    if report.current.source == "unknown":
        findings.append({
            "kind": "current-helm-unknown",
            "target": "current",
            "decision": "report",
            "reason": report.current.reason or "现役总舵无法判定",
        })
    if report.workspace_note:
        findings.append({
            "kind": "workspace-unknown",
            "target": "current",
            "decision": "report",
            "reason": report.workspace_note,
        })
    for view in sorted(report.helm_tabs, key=lambda item: item.tab_id):
        findings.append({
            "kind": "helm-tab",
            "target": view.tab_id,
            "label": view.label,
            "panes": view.pane_count,
            "agents": view.agents,
            "decision": view.decision,
            "reason": view.reason,
        })
    for old in sorted(report.old_helm, key=lambda item: (item.name, item.pane)):
        findings.append({
            "kind": "old-helm",
            "target": old.name,
            "pane": old.pane,
            "tab": old.tab,
            "agent_status": old.agent_status,
            "decision": old.decision,
            "reason": old.reason,
        })
    current_payload = {
        "agent": report.current.agent,
        "pane": report.current.pane,
        "tab": report.current.tab,
        "source": report.current.source,
    }
    if report.current.reason:
        current_payload["reason"] = report.current.reason
    return {
        "current_helm": current_payload,
        "source": report.current.source,
        "applied": applied,
        "findings": findings,
        "actions": [
            {
                "action": action.action,
                "target": action.target,
                "tab": action.tab,
                "status": action.status,
                "reason": action.reason,
            }
            for action in report.actions
        ],
    }


def print_report(report: Report, applied: bool) -> None:
    current = report.current
    if current.source == "unknown":
        print(f"现役总舵：无法判定（{current.reason or '未知'}）——只报告，不执行关闭",
              flush=True)
    else:
        print(f"现役总舵：{current.agent}（pane {current.pane or '-'}，"
              f"tab {current.tab or '-'}；来源 {current.source}）", flush=True)
    if report.workspace_note:
        print(f"[只报告] {report.workspace_note}", flush=True)
    if report.helm_tabs:
        print("总舵 tab 对账：", flush=True)
        for view in sorted(report.helm_tabs, key=lambda item: item.tab_id):
            agents = "、".join(
                f"{item['name'] or '?'}:{item['agent_status'] or 'unknown'}"
                for item in view.agents
            ) or "无 agent"
            print(f"  - {view.tab_id}「{view.label}」panes={view.pane_count} "
                  f"agents=[{agents}] → {DECISION_TEXT[view.decision]}"
                  f"（{view.reason}）", flush=True)
    if report.old_helm:
        print("旧 helm 残留：", flush=True)
        for old in sorted(report.old_helm, key=lambda item: (item.name, item.pane)):
            print(f"  - {old.name}（pane {old.pane}，tab {old.tab}，"
                  f"{old.agent_status}）→ {DECISION_TEXT[old.decision]}"
                  f"（{old.reason}）", flush=True)
    if not report.actions:
        print("无回收动作。", flush=True)
        return
    print("动作：" if applied else "动作（dry-run）：", flush=True)
    for action in report.actions:
        print(f"  [{action.status}] {action.action} {action.target}"
              f" — {action.reason}", flush=True)
    if applied:
        closed = sum(1 for action in report.actions if action.status == "closed")
        failed = sum(1 for action in report.actions if action.status == "failed")
        skipped = sum(1 for action in report.actions if action.status == "skipped")
        print(f"已关闭 {closed} 项，失败 {failed} 项，跳过 {skipped} 项；"
              "dispatch 失败不阻断其余安全项。", flush=True)
    else:
        print(f"dry-run：共 {len(report.actions)} 项 would_close，"
              "确认后加 --apply。", flush=True)


# ---------------------------------------------------------------------------
# 执行（只经 dispatch.py）
# ---------------------------------------------------------------------------

def run_dispatch(root: Path, argv: list[str]) -> tuple[int, str, str]:
    cmd = [sys.executable, str(DISPATCH_PATH), "--root", str(root), *argv]
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
    except OSError as exc:
        return 1, "", f"无法执行 dispatch.py：{exc}"
    return proc.returncode, proc.stdout, proc.stderr


def _safe_dispatch(dispatch, argv: list[str]) -> tuple[int, str, str]:
    try:
        return dispatch(argv)
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断其余安全项
        return 1, "", f"{type(exc).__name__}: {exc}"


def execute_actions(report: Report, dispatch) -> None:
    failed_tabs: set[str] = set()
    for action in report.actions:
        if action.action == "pane close":
            code, output, error = _safe_dispatch(
                dispatch, ["pane", "close", "--pane", action.target],
            )
            if code == 0:
                action.status = "closed"
            else:
                action.status = "failed"
                detail = (error or output).strip() or f"退出码 {code}"
                action.reason += f"；dispatch 失败：{detail}"
                if action.tab:
                    failed_tabs.add(action.tab)
            continue
        if action.target in failed_tabs:
            action.status = "skipped"
            action.reason += "；同 tab pane 关闭失败，跳过 tab 关闭"
            continue
        code, output, error = _safe_dispatch(
            dispatch, ["tab", "close", "--tab", action.target],
        )
        if code == 0:
            action.status = "closed"
        else:
            action.status = "failed"
            detail = (error or output).strip() or f"退出码 {code}"
            action.reason += f"；dispatch 失败：{detail}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class ReaperArgumentParser(argparse.ArgumentParser):
    """参数错误统一返回 1（不沿用 argparse 默认的 2）。"""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        print(f"error: {message}", file=sys.stderr, flush=True)
        raise ReaperError(message)


def build_parser() -> argparse.ArgumentParser:
    return ReaperArgumentParser(
        description="窗口对账回收：对账 dispatch 注册表 + 现役总舵（handoff/registry）"
                    "+ herdr pane/tab，默认 dry-run 只报告，--apply 才安全回收残留"
                    "旧总舵 tab / 旧 helm。",
        epilog=(
            "分工：本脚本只处理「总舵 tab」与「旧 helm agent 残留」；完成流 tab、一般空 "
            "pane / 空 tab 的巡检回收仍由 pane-audit 负责（working 永不关、聚焦/白名单"
            "跳过、空 pane 用 process-info 确认空闲交互 shell）。本脚本与 guard/pane-audit "
            "新增的总舵/旧 helm 回收统一走 dispatch.py；pane-audit 既有的一般空 pane / "
            "空 tab / 完成流 tab 回收仍直写 herdr（存量行为，不在本脚本范围）。\n"
            "安全边界：多个总舵 tab 只保留现役；其余仅在全部 pane 为空或 registry agent "
            "idle/done、无 working/blocked/unknown、tab 非聚焦、tab 非白名单时回收；"
            "旧 helm 残留同样只回收 idle/done 且非现役者；现役总舵 tab 永不关闭。\n"
            "跨 workspace：能定位现役总舵所在 workspace 时只对账该 workspace，"
            "其它项目的 tab/agent 一律不触碰（herdr 列表是全局的）；"
            "定位不到 workspace（快照/字段缺失）时零关闭、只报告。\n"
            "无法判定现役：缺 guard.handoff、handoff 上次交接失败（result=failed）、"
            "registry 无 role=helm、多候选并列、handoff/registry/pane 字段矛盾 → 只报告，"
            "不执行任何关闭（退出码仍为 0）。\n"
            "白名单：tab 标签前缀 Captain· / 流程优化· 整体跳过；可用 --whitelist-prefix "
            "追加。\n"
            "数据源：默认 herdr tab/pane/agent list（只读）；--snapshot 注入 fixture JSON"
            "（仅 dry-run，与 --apply 互斥；不调用 herdr，空 pane 一律按无法确认只报告）。\n"
            "关闭动作：统一走 dispatch.py（pane close / tab close），禁止裸 herdr 生命周期；"
            "dispatch 调用失败只记录并继续其余安全项，最终退出码仍为 0。\n"
            "best-effort 边界：回收基于本轮快照，关闭前不重新取数/二次复检——快照与"
            "关闭之间被人工开始使用的 pane/tab 仍可能被关；workspace 无法判定时"
            "零关闭只报告；先跑 dry-run 核对再 --apply。\n"
            "守卫接线：guard 周期调用由 crew-guard 负责（建议 --apply --json，失败只记日志）；"
            "人工一键用 `python3 scripts/crew-reaper.py --apply`。\n"
            "退出码：0 成功（含无可回收/无法判定现役）；1 参数或数据错误；"
            "2 --snapshot 与 --apply 互斥。\n"
            "示例：python3 scripts/crew-reaper.py --apply --json\n"
            "      python3 scripts/crew-reaper.py --snapshot snap.json --crew-dir <crew-dir>"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--root", default=".",
        help="项目根（读取 .planning/orchestration.json 的 helm.tabLabel；默认 cwd）",
    )
    parser.add_argument(
        "--crew-dir",
        help="运行时状态目录（默认 crew_paths.crew_dir(--root)；读取 "
             "dispatch-agents.json 与 guard.handoff）",
    )
    parser.add_argument(
        "--snapshot",
        help="fixture JSON 数据源（仅 dry-run，与 --apply 互斥；不调用 herdr；接受 "
             "{tabs,panes,agents} 或 herdr result 包装）",
    )
    parser.add_argument(
        "--whitelist-prefix",
        action="append",
        default=[],
        help="额外的 tab 标签白名单前缀（可重复；默认已含 Captain· / 流程优化·）",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行安全回收（pane close / tab close）；默认 dry-run 只报告；"
             "与 --snapshot 互斥",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出机器可读 JSON（current_helm/source/findings/actions）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = add_arguments(build_parser())
    try:
        args = parser.parse_args(argv)
    except ReaperError:
        return 1
    if args.apply and args.snapshot:
        # S-1：fixture 快照可能已过期，禁止据此执行真实关闭（与 pane-audit 同口径）。
        print(
            "error: --apply 与 --snapshot 互斥：fixture 快照可能已过期，"
            "禁止据此执行真实关闭；请去掉 --snapshot 基于 live 数据重跑，"
            "或先 dry-run 只报告。",
            file=sys.stderr, flush=True,
        )
        return 2
    try:
        root = Path(args.root).expanduser().resolve()
        if not root.is_dir():
            raise ReaperError(f"--root 不存在或不是目录：{root}")
        try:
            crew_dir = (
                Path(args.crew_dir).expanduser()
                if args.crew_dir else resolve_crew_dir(root)
            )
        except (CrewPathsError, OSError, RuntimeError) as exc:
            raise ReaperError(f"无法解析 crew-dir：{exc}") from exc
        snapshot = (
            load_snapshot(args.snapshot) if args.snapshot else load_live_snapshot()
        )
        registry = load_registry(crew_dir)
        handoff = read_handoff(crew_dir)
        helm_labels = pa.load_helm_labels(root)
    except ReaperError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1

    prefixes = tuple(pa.DEFAULT_WHITELIST_PREFIXES) + tuple(args.whitelist_prefix)
    # snapshot 模式按契约不调用 herdr：空 pane 没有 process-info 证据，只能只报告。
    empty_probe = None if args.snapshot else pa.probe_empty_pane
    report = reconcile(
        snapshot, registry, handoff,
        whitelist_prefixes=prefixes,
        helm_labels=helm_labels,
        empty_probe=empty_probe,
    )
    if args.apply:
        execute_actions(report, lambda command: run_dispatch(root, command))
    else:
        for action in report.actions:
            action.status = "would_close"

    if args.json:
        print(json.dumps(report_payload(report, applied=args.apply),
                         ensure_ascii=False, indent=2), flush=True)
    else:
        print_report(report, applied=args.apply)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except ReaperError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
