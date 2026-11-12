#!/usr/bin/env python3
"""工位/tab 巡检（pane-audit）：默认 dry-run 只报告，--apply 才关闭安全项。

自检口径（角色「定时自检」默认动作）：每轮跑 `python3 scripts/pane-audit.py --apply`；
自动动作只限「非白名单、非 working、明确空 pane / 空 tab / 完成流 tab」，
不确定一律只报告；working 永不关闭；白名单 tab 整体跳过。

巡检口径（decisions §3 + T45）：
- registry 指 Herdr 运行时 agent 注册表（`herdr agent list`）；
- 单 tab 最多 6 个 pane（含 root），超出告警（仅报告，人工决定迁出/关闭）；
- 总舵 tab（标签含「总舵」或配置 `helm.tabLabel`）只允许 1 个 pane，多 pane 告警；
  空 tab 与无 registry 的 pane 也报告；总舵 tab 内的多余空 pane 在 --apply 时关闭
  （保留总舵自身 pane；FB-038：guard 交接 tab create 后多出的空 shell pane）；
- 有 pane 无 registry（agent list 无对应 pane）告警；经 `herdr pane process-info`
  确认前台是**空闲交互 shell**（进程组组长是 shell 且 argv 无脚本/命令参数）且所在
  tab 仍有"有名 crew 工位"（registry agent 名非空，或 --helm-pane）时，视为「多余空
  pane」，--apply 自动关闭；`bash build.sh`、`bash wizard.sh` 等前台跑脚本的 shell
  与空闲交互 shell 同属 shell 进程名，靠进程组/argv 区分，确认不了按非空处理；
  working/blocked、带 agent 字段的 TUI、无工位 tab（含个人 TUI tab）、process-info
  不可用一律只报告；
- 完成流回收：flow plan（`<root>/.planning/<流>/task_plan.md`）所有 Phase
  `**Status:** complete`（按 `### Phase` 分段、段内缺失/非法 Status 默认 pending、
  先剥离 HTML 注释；出现非法 `**Status:**` 行按未完成处理并在 stderr 告警），且该
  tab 内 registry agent 全部 idle/done、名称按最长前缀归属流，且无 working/blocked/
  无法确认的 pane 时，提议关闭该 tab（--apply 关闭）；tab 当前聚焦时只报告；
  helm tab 与白名单 tab 不参与回收；
- 有 registry 无 pane（registry 指向不存在的 pane）告警；
- 空 tab（无 pane，非总舵）告警，--apply 时自动关闭；
- working 永不关闭；
- 白名单默认：tab 标签前缀 `Captain·` / `流程优化·` 整体跳过；总舵识别优先于这两个
  前缀（标签同时命中时按总舵 tab 处理）；agent 名 `helm*`；另可用
  `--whitelist-prefix` / `--helm-pane` 追加。
- `--root`（默认 `.`）读取 `<root>/.planning/orchestration.json` 的 `helm.tabLabel`；
  缺失/损坏/字段非法 fail-soft 回落默认「总舵」。
- blocked 回执巡检：读取 `<crew-dir>/inbox/*.json`（`--crew-dir`，默认
  `crew_paths.crew_dir(root)`）；snapshot 中 `agent_status == blocked` 的 agent 取
  其最新一条回执（按 `ts` 最大；归属 `work_slot == pane_id` 优先、其次 `from == name`），
  最新回执仍为 blocked 且年龄 > `--blocked-grace`（默认 30 分钟）时告警
  `blocked-overdue`，按 R6 处置（re-task / close / escalate）；仅报告，`--apply`
  不关闭；helm agent / 白名单 tab 跳过（与孤儿检查同口径）；回执损坏、`ts` 缺失/
  非法 fail-soft 跳过；crew dir 缺失/解析失败不报错，其余巡检照常。

数据源：默认 `herdr api snapshot`，失败退回 `tab list` + `pane list` + `agent list`；
`--snapshot FILE` 注入 fixture JSON（测试/复算用，不调用 herdr）。

用法：
  python3 scripts/pane-audit.py                       # dry-run 只报告
  python3 scripts/pane-audit.py --apply               # 自检默认：关闭空 tab/空 pane/完成流 tab
  python3 scripts/pane-audit.py --snapshot snap.json  # fixture 数据源
  python3 scripts/pane-audit.py --root <项目> --whitelist-prefix '实验·' --helm-pane wQ:p1
  python3 scripts/pane-audit.py --blocked-grace 45 --crew-dir /path/to/.git/crew
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import crew_dir as resolve_crew_dir  # noqa: E402

MAX_PANES_PER_TAB = 6
DEFAULT_WHITELIST_PREFIXES = ("Captain·", "流程优化·")
HELM_TAB_PREFIX = "总舵"
HELM_AGENT_PREFIX = "helm"
DEFAULT_BLOCKED_GRACE_MINUTES = 30
FLOW_PHASE_RE = re.compile(r"^###\s+[Pp]hase\b")
FLOW_STATUS_LINE_RE = re.compile(r"\*\*Status:\*\*(.*)$")
FLOW_STATUS_VALUE_RE = re.compile(r"^(pending|in[ _]progress|complete)\b")
SHELL_PROCESS_NAMES = frozenset({
    "bash", "zsh", "sh", "fish", "dash", "ksh", "tcsh", "csh", "nu", "xonsh",
    "pwsh", "powershell", "cmd", "cmd.exe",
})
EMPTY_PANE_STATUSES = ("", "unknown", "idle")
IDLE_AGENT_STATUSES = ("idle", "done")
KIND_TEXT = {
    "tab-overflow": "单 tab pane 超限",
    "helm-tab-multi-pane": "总舵 tab 多 pane",
    "empty-pane": "多余空 pane",
    "flow-complete-tab": "完成流回收 tab",
    "orphan-pane": "pane 无 registry",
    "orphan-registry": "registry 无 pane",
    "empty-tab": "空 tab",
    "blocked-overdue": "blocked 工位超时未处置",
}


class AuditError(RuntimeError):
    """取数/解析/参数错误；main 捕获后非零退出。"""


@dataclass(frozen=True)
class Finding:
    kind: str
    target: str
    label: str
    detail: str
    action: str | None  # "pane" / "tab" / None（仅报告）
    next_step: str


def format_finding(finding: Finding) -> str:
    return (
        f"[告警] {KIND_TEXT[finding.kind]}：{finding.target}（{finding.label}）："
        f"{finding.detail}\n  下一步：{finding.next_step}"
    )


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------

def normalize_snapshot(data: object) -> dict:
    """统一为 {tabs, panes, agents}；兼容 herdr `{"result": {"snapshot": ...}}` 包装。"""
    if not isinstance(data, dict):
        raise AuditError("snapshot 必须是 JSON 对象")
    result = data.get("result")
    if isinstance(result, dict):
        snapshot = result.get("snapshot")
        data = snapshot if isinstance(snapshot, dict) else result
    normalized: dict = {}
    for key in ("tabs", "panes", "agents"):
        value = data.get(key, [])
        if value is None:
            value = []
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise AuditError(f"snapshot.{key} 应为对象数组")
        normalized[key] = value
    return normalized


def load_fixture(path: str) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise AuditError(f"无法读取 snapshot 文件 {path}：{exc}") from exc
    except ValueError as exc:
        raise AuditError(f"snapshot JSON 解析失败 {path}：{exc}") from exc
    return normalize_snapshot(data)


def herdr_run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(["herdr", *args], text=True, capture_output=True, check=False)
    except OSError as exc:
        raise AuditError(f"无法执行 herdr {' '.join(args)}：{exc}") from exc


def herdr_json(args: list[str]) -> dict:
    proc = herdr_run(args)
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip() or f"退出码 {proc.returncode}"
        raise AuditError(f"herdr {' '.join(args)} 失败：{detail}")
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise AuditError(f"herdr {' '.join(args)} JSON 解析失败：{exc}") from exc
    if not isinstance(payload, dict):
        raise AuditError(f"herdr {' '.join(args)} 返回非对象 JSON")
    return payload


def _result(payload: dict) -> dict:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def load_live_snapshot() -> dict:
    try:
        payload = herdr_json(["api", "snapshot"])
        result = _result(payload)
        snapshot = result.get("snapshot")
        if isinstance(snapshot, dict):
            return normalize_snapshot(snapshot)
        if any(key in result for key in ("tabs", "panes", "agents")):
            return normalize_snapshot(result)
        raise AuditError("snapshot 缺少 tabs/panes/agents")
    except AuditError as snapshot_error:
        try:
            tabs = _result(herdr_json(["tab", "list"])).get("tabs")
            panes = _result(herdr_json(["pane", "list"])).get("panes")
            agents = _result(herdr_json(["agent", "list"])).get("agents")
            return normalize_snapshot({"tabs": tabs, "panes": panes, "agents": agents})
        except AuditError as list_error:
            raise AuditError(
                f"取数失败：snapshot（{snapshot_error}）；回退 list（{list_error}）"
            ) from list_error


def filter_workspace(items: list[dict], workspace: str | None) -> list[dict]:
    if not workspace:
        return items
    return [
        item for item in items
        if str(item.get("workspace_id") or workspace) == workspace
    ]


# ---------------------------------------------------------------------------
# 回执（blocked SLA；只读，失败 fail-soft）
# ---------------------------------------------------------------------------

def parse_receipt_ts(value: object) -> datetime | None:
    """解析回执 `ts`（ISO8601，`Z` 后缀；无时区按 UTC）；缺失/非法返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def load_receipts(crew_dir: Path | None) -> list[dict]:
    """读取 `<crew-dir>/inbox/*.json`；目录缺失/文件损坏 fail-soft（跳过该文件）。"""
    if crew_dir is None:
        return []
    try:
        paths = sorted((Path(crew_dir) / "inbox").glob("*.json"))
    except OSError:
        return []
    receipts: list[dict] = []
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # 损坏/不可读回执跳过
        if isinstance(data, dict):
            receipts.append(data)
    return receipts


def latest_receipt_for(
    agent: dict, receipts: list[dict]
) -> tuple[dict, datetime] | None:
    """取该 agent 的最新一条回执：`work_slot == pane_id` 优先、其次 `from == name`。

    归属命中 slot 或 name 均视为该 agent 的回执，按 `ts` 最大取最新（同一 ts 时
    slot 命中优先）；`ts` 缺失/非法或非对象回执跳过（fail-soft）。
    """
    pane_id = str(agent.get("pane_id") or "")
    name = str(agent.get("name") or "")
    best: tuple[dict, datetime] | None = None
    best_key: tuple[datetime, bool] | None = None
    for receipt in receipts:
        if not isinstance(receipt, dict):
            continue
        slot_match = bool(pane_id) and str(receipt.get("work_slot") or "") == pane_id
        name_match = bool(name) and str(receipt.get("from") or "") == name
        if not (slot_match or name_match):
            continue
        ts = parse_receipt_ts(receipt.get("ts"))
        if ts is None:
            continue
        key = (ts, slot_match)
        if best_key is None or key > best_key:
            best_key, best = key, (receipt, ts)
    return best


# ---------------------------------------------------------------------------
# 巡检
# ---------------------------------------------------------------------------

def is_whitelisted_tab(
    tab: dict,
    prefixes: tuple[str, ...],
    helm_labels: tuple[str, ...] = (HELM_TAB_PREFIX,),
) -> bool:
    label = str(tab.get("label") or "").strip()
    return label.startswith(prefixes) or is_helm_tab(tab, helm_labels)


def is_helm_tab(tab: dict, helm_labels: tuple[str, ...] = (HELM_TAB_PREFIX,)) -> bool:
    label = str(tab.get("label") or "").strip()
    return any(name in label for name in helm_labels)


def load_helm_labels(root: str | Path = ".") -> tuple[str, ...]:
    """读取 <root>/.planning/orchestration.json 的 helm.tabLabel；非法时回落「总舵」。"""
    try:
        base = Path(root).expanduser()
    except RuntimeError:
        return (HELM_TAB_PREFIX,)  # 无法展开 ~user：与缺失配置同口径回落默认
    path = base / ".planning/orchestration.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (HELM_TAB_PREFIX,)
    helm = data.get("helm") if isinstance(data, dict) else None
    label = helm.get("tabLabel") if isinstance(helm, dict) else None
    if not isinstance(label, str) or not label.strip():
        return (HELM_TAB_PREFIX,)
    label = label.strip()
    return (HELM_TAB_PREFIX,) if label == HELM_TAB_PREFIX else (HELM_TAB_PREFIX, label)


def is_helm_agent(agent: dict) -> bool:
    return str(agent.get("name") or "").lower().startswith(HELM_AGENT_PREFIX)


def match_flow(agent_name: str, flows: dict[str, bool]) -> str | None:
    """agent 名按最长前缀归属流 plan（与 guard `_plan_dir_for_task` 同口径）。"""
    if not agent_name:
        return None
    matches = [
        name for name in flows
        if agent_name == name
        or agent_name.startswith(name + "-")
        or agent_name.startswith(name + "/")
    ]
    return max(matches, key=len) if matches else None


def _strip_html_comments(lines: list[str]):
    """逐行产出非 HTML 注释行（与 crew-guard `_strip_comments` 同口径）。"""
    in_comment = False
    for raw in lines:
        line = raw.rstrip()
        if in_comment:
            if "-->" in line:
                in_comment = False
            continue
        if line.strip().startswith("<!--"):
            if "-->" not in line:
                in_comment = True
            continue
        yield line


def parse_flow_statuses(text: str) -> tuple[list[str], list[tuple[int, str]]]:
    """按 `### Phase` 分段解析 `**Status:**`（与 guard `_plan_phase_statuses` 同口径）。

    段内缺失 Status 默认 pending；取值以三值之一开头（允许尾随说明文字，与 guard
    `STATUS_RE` 同口径），非法值（含 `**Status:**` 后缺空格）记入 invalid 列表且该
    Phase 保持 pending；HTML 注释整段跳过。返回 (每段状态列表, [(行号, 原始值)])。
    """
    statuses: list[str] = []
    invalid: list[tuple[int, str]] = []
    in_phase = False
    for line_no, line in enumerate(_strip_html_comments(text.splitlines()), 1):
        if line.startswith("## "):
            in_phase = False
            continue
        if FLOW_PHASE_RE.match(line):
            statuses.append("pending")
            in_phase = True
            continue
        if not in_phase:
            continue
        match = FLOW_STATUS_LINE_RE.search(line)
        if not match:
            continue
        raw_value = match.group(1)
        value = raw_value.strip()
        token = FLOW_STATUS_VALUE_RE.match(value)
        if not raw_value.startswith((" ", "\t")) or token is None:
            invalid.append((line_no, value or "（空）"))
            continue
        normalized = token.group(1)
        statuses[-1] = "in_progress" if normalized.startswith("in") else normalized
    return statuses, invalid


def load_flow_plans(root: str | Path = ".") -> dict[str, bool]:
    """读取 `<root>/.planning/<流>/task_plan.md` 的 Phase Status。

    按 `### Phase` 分段（与 guard 同口径）；至少一个 Phase 且全部为 complete
    才算完成；非法 Status 行 → 该流记 False 并在 stderr 告警；目录缺失、读取
    失败、Status 缺失/损坏一律记 False（不自动回收，宁可不关）。
    """
    try:
        planning = Path(root).expanduser() / ".planning"
        directories = sorted(path for path in planning.iterdir() if path.is_dir())
    except (OSError, RuntimeError):
        return {}
    flows: dict[str, bool] = {}
    for directory in directories:
        plan = directory / "task_plan.md"
        if not plan.is_file():
            continue
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            flows[directory.name] = False
            continue
        statuses, invalid = parse_flow_statuses(text)
        if invalid:
            detail = "、".join(f"第 {line} 行「{value}」" for line, value in invalid)
            print(
                f"[告警] 流 {directory.name} 的 task_plan.md 存在非法 Status：{detail}；"
                "按未完成处理（不自动回收 tab）",
                file=sys.stderr, flush=True,
            )
        flows[directory.name] = (
            bool(statuses) and not invalid
            and all(status == "complete" for status in statuses)
        )
    return flows


def _argv_of(process: dict) -> list[str] | None:
    """取进程 argv（herdr 真实结构带 argv；缺失/非法返回 None）。"""
    argv = process.get("argv")
    if isinstance(argv, list) and argv:
        return [str(item) for item in argv]
    cmdline = process.get("cmdline")
    if isinstance(cmdline, str) and cmdline.strip():
        return cmdline.split()
    return None


def _shell_argv_interactive(process: dict) -> bool | None:
    """argv 判交互 shell：True=无可执行脚本/命令参数；False=在跑脚本/命令；
    None=argv 缺失无法确认（按不可判定处理，只报告）。"""
    argv = _argv_of(process)
    if not argv:
        return None
    for arg in argv[1:]:
        if not arg.startswith("-"):
            return False  # 脚本路径/命令参数：非空闲交互 shell
        if "c" in arg[1:]:
            return False  # -c/-lc 等命令字符串：非交互
    return True


def _process_pid(process: dict) -> int | None:
    try:
        return int(process.get("pid"))
    except (TypeError, ValueError):
        return None


def probe_empty_pane(pane_id: str) -> bool | None:
    """用 `herdr pane process-info` 判定 pane 是否空闲交互 shell。

    True=前台进程组组长是 shell 且 argv 无脚本/命令参数（明确空）；
    False=前台是其它进程或在跑脚本的 shell（非空）；
    None=取数/解析失败或无法确认（只报告，不关闭）。
    """
    try:
        payload = herdr_json(["pane", "process-info", "--pane", pane_id])
    except AuditError:
        return None
    info = _result(payload).get("process_info")
    if not isinstance(info, dict):
        return None
    processes = info.get("foreground_processes")
    if not isinstance(processes, list) or not processes:
        return None
    parsed: list[tuple[dict, int | None, str]] = []
    for process in processes:
        if not isinstance(process, dict):
            return None
        name = Path(str(process.get("name") or "")).name.lower()
        parsed.append((process, _process_pid(process), name))
    if not any(name in SHELL_PROCESS_NAMES for _proc, _pid, name in parsed):
        return False  # 前台无 shell：明确非空
    pgid_raw = info.get("foreground_process_group_id")
    shell_pid = info.get("shell_pid")
    target: dict | None = None
    if pgid_raw is not None:
        try:
            pgid = int(pgid_raw)
        except (TypeError, ValueError):
            return None
        leader = next((proc for proc, pid, _name in parsed if pid == pgid), None)
        if leader is None:
            return None  # 进程组组长不在前台列表：无法确认
        name = Path(str(leader.get("name") or "")).name.lower()
        if name not in SHELL_PROCESS_NAMES:
            return False  # 前台组长是非 shell 进程：明确非空
        target = leader
    elif shell_pid is not None:
        try:
            shell_pid_int = int(shell_pid)
        except (TypeError, ValueError):
            return None
        target = next((proc for proc, pid, _name in parsed if pid == shell_pid_int), None)
        if target is None:
            return None
    else:
        # 无进程组信息：只有前台唯一进程是 shell 时才继续判 argv
        if len(parsed) != 1:
            return None
        target = parsed[0][0]
    return _shell_argv_interactive(target)


def _label(tab: dict | None) -> str:
    if not tab:
        return "-"
    return str(tab.get("label") or "-").strip() or "-"


def flow_reclaim_finding(
    tab: dict,
    tab_id: str,
    tab_panes: list[dict],
    tab_agents: list[dict],
    agents_by_pane: dict[str, dict],
    flows: dict[str, bool],
    probe: Callable[[str], bool | None],
) -> Finding | None:
    """完成流回收：全部 Phase complete 且 tab 内 agent 全 idle/done、无其它活物。

    保守口径：无 agent、agent 无法归属流、任一归属流未完成、存在 working/blocked/
    无法确认的 pane、helm agent —— 任一命中即不回收（返回 None）。
    当前聚焦 tab 只报告（action=None），避免把 Captain 正在看的窗口关掉。
    """
    if not flows or not tab_agents or not tab_panes:
        return None
    if any(is_helm_agent(agent) for agent in tab_agents):
        return None
    if any(str(agent.get("agent_status") or "") not in IDLE_AGENT_STATUSES
           for agent in tab_agents):
        return None
    matched: set[str] = set()
    for agent in tab_agents:
        flow = match_flow(str(agent.get("name") or ""), flows)
        if flow is None:
            return None  # 有工位无法归属流：不确定，不回收
        matched.add(flow)
    if any(not flows[flow] for flow in matched):
        return None
    for pane in tab_panes:
        pane_id = str(pane.get("pane_id") or "")
        if pane_id in agents_by_pane:
            continue
        if str(pane.get("agent_status") or "unknown") not in EMPTY_PANE_STATUSES:
            return None  # working/blocked/其他状态：可能有活物，不回收
        if pane.get("agent"):
            return None  # 未进 registry 的 TUI：不确定，不回收
        if probe(pane_id) is not True:
            return None  # 非明确空 pane：不回收
    label = _label(tab)
    detail = (f"流 plan 全部 Phase complete（{', '.join(sorted(matched))}），"
              f"{len(tab_agents)} 个 agent 均 idle/done")
    if tab.get("focused") is True:
        return Finding(
            "flow-complete-tab", tab_id, label, f"{detail}；tab 当前聚焦，仅报告",
            None,
            "确认无在看在用后关闭："
            f"`python3 scripts/dispatch.py tab close --tab {tab_id}`",
        )
    return Finding(
        "flow-complete-tab", tab_id, label, detail, "tab",
        "执行 `python3 scripts/pane-audit.py --apply` 自动回收该 tab",
    )


def audit(
    snapshot: dict,
    whitelist_prefixes: tuple[str, ...] = DEFAULT_WHITELIST_PREFIXES,
    helm_panes: frozenset[str] = frozenset(),
    helm_labels: tuple[str, ...] = (HELM_TAB_PREFIX,),
    *,
    flows: dict[str, bool] | None = None,
    empty_probe: Callable[[str], bool | None] | None = None,
    blocked_receipts: list[dict] | None = None,
    now: datetime | None = None,
    blocked_grace_minutes: int = DEFAULT_BLOCKED_GRACE_MINUTES,
) -> list[Finding]:
    tabs = snapshot.get("tabs") or []
    panes = snapshot.get("panes") or []
    agents = snapshot.get("agents") or []
    tab_by_id = {str(tab["tab_id"]): tab for tab in tabs if tab.get("tab_id")}
    agents_by_pane = {
        str(agent["pane_id"]): agent for agent in agents if agent.get("pane_id")
    }
    panes_by_tab: dict[str, list[dict]] = {}
    agents_by_tab: dict[str, list[dict]] = {}
    pane_ids: set[str] = set()
    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        pane_ids.add(str(pane_id))
        panes_by_tab.setdefault(str(pane.get("tab_id")), []).append(pane)
    for agent in agents:
        tab_id = agent.get("tab_id")
        if tab_id:
            agents_by_tab.setdefault(str(tab_id), []).append(agent)

    def probe(pane_id: str) -> bool | None:
        if empty_probe is None:
            return None
        try:
            return empty_probe(pane_id)
        except Exception:  # noqa: BLE001 - 探针异常按无法判定（只报告）
            return None

    findings: list[Finding] = []

    for tab_id, tab in tab_by_id.items():
        label = _label(tab)
        child_panes = panes_by_tab.get(tab_id, [])
        tab_agents = agents_by_tab.get(tab_id, [])
        if is_helm_tab(tab, helm_labels):
            if not child_panes:
                findings.append(Finding(
                    "empty-tab", tab_id, label, "无 pane（总舵 tab 仅报告，不自动关）",
                    None,
                    "确认总舵已迁出后手动关闭："
                    f"`python3 scripts/dispatch.py tab close --tab {tab_id}`",
                ))
            elif len(child_panes) > 1:
                findings.append(Finding(
                    "helm-tab-multi-pane", tab_id, label,
                    f"{len(child_panes)} 个 pane（总舵 tab 只保留 1 个 pane，含 root）", None,
                    "多余空 pane 由 `--apply` 自动关闭（保留总舵 pane）；"
                    f"非空 pane 人工确认（`herdr pane list --tab {tab_id}`）",
                ))
            continue
        if is_whitelisted_tab(tab, whitelist_prefixes, helm_labels):
            continue
        if not child_panes:
            findings.append(Finding(
                "empty-tab", tab_id, label, "无 pane", "tab",
                "执行 `python3 scripts/pane-audit.py --apply` 自动关闭该空 tab",
            ))
            continue
        if len(child_panes) > MAX_PANES_PER_TAB:
            findings.append(Finding(
                "tab-overflow", tab_id, label,
                f"{len(child_panes)} 个 pane（上限 {MAX_PANES_PER_TAB}，含 root）", None,
                f"人工确认后关闭多余 pane（`herdr pane list --tab {tab_id}`），或拆到新 tab",
            ))
        reclaim = flow_reclaim_finding(
            tab, tab_id, child_panes, tab_agents, agents_by_pane,
            flows or {}, probe,
        )
        if reclaim is not None:
            findings.append(reclaim)

    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        pane_id = str(pane_id)
        tab = tab_by_id.get(str(pane.get("tab_id")))
        if tab and is_whitelisted_tab(tab, whitelist_prefixes, helm_labels) \
                and not is_helm_tab(tab, helm_labels):
            continue
        if pane_id in helm_panes:
            continue
        agent = agents_by_pane.get(pane_id)
        if agent is not None:  # 有 registry 的 pane 归 registry 侧巡检
            continue
        status = str(pane.get("agent_status") or "unknown")
        tab_panes = panes_by_tab.get(str(pane.get("tab_id")), [])
        tab_pane_ids = {str(item.get("pane_id")) for item in tab_panes if item.get("pane_id")}
        # 只接管「有名 crew 工位」所在 tab（含 --helm-pane）；个人 tab 里的
        # 无名 runtime TUI（claude/qodercli 等）不算工位，空 pane 一律只报告。
        sibling_ok = any(
            pid in helm_panes
            or str(agents_by_pane.get(pid, {}).get("name") or "").strip()
            for pid in tab_pane_ids
        )
        # 只在「明确空」候选上探针：无 agent 字段、状态 idle/unknown
        probed = not pane.get("agent") and status in EMPTY_PANE_STATUSES
        probe_result = probe(pane_id) if probed else None
        if probe_result is True and sibling_ok:
            findings.append(Finding(
                "empty-pane", pane_id, _label(tab),
                "无 registry 且 process-info 确认前台为空闲 shell（进程组/argv 无脚本参数；"
                "working 永不关；保留同 tab 的工位 pane / 总舵 pane）",
                "pane",
                "执行 `python3 scripts/pane-audit.py --apply` 自动关闭该空 pane",
            ))
            continue
        note = "；working 不关闭" if status == "working" else ""
        if status == "blocked":
            note = "；blocked 不自动关"
        if probed and probe_result is False:
            note += "；前台进程非空闲 shell，不自动关"
        elif probed and probe_result is None and empty_probe is not None:
            note += "；process-info 无法确认，仅报告"
        if probe_result is True and not sibling_ok:
            note += "；tab 内无可识别工位，仅报告"
        findings.append(Finding(
            "orphan-pane", pane_id, _label(tab), f"无 registry 记录（状态 {status}{note}）",
            None,
            f"确认 pane 用途后手动关闭：`herdr pane close {pane_id}`（人工终端请保留）",
        ))

    for agent in agents:
        pane_id = str(agent.get("pane_id") or "")
        if pane_id and pane_id in pane_ids:
            continue
        if is_helm_agent(agent):
            continue
        tab = tab_by_id.get(str(agent.get("tab_id") or ""))
        if tab and is_whitelisted_tab(tab, whitelist_prefixes, helm_labels):
            continue
        name = str(agent.get("name") or pane_id or "?")
        findings.append(Finding(
            "orphan-registry", name, _label(tab),
            f"registry 指向的 pane {pane_id or '缺失'} 不存在", None,
            "核对 registry：`herdr agent list`，清理或重启对应工位",
        ))

    if blocked_receipts:
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        current = current.astimezone(timezone.utc)
        for agent in agents:
            if str(agent.get("agent_status") or "") != "blocked":
                continue
            if is_helm_agent(agent):
                continue
            tab = tab_by_id.get(str(agent.get("tab_id") or ""))
            if tab and is_whitelisted_tab(tab, whitelist_prefixes, helm_labels):
                continue
            latest = latest_receipt_for(agent, blocked_receipts)
            if latest is None:
                continue
            receipt, ts = latest
            if str(receipt.get("status") or "") != "blocked":
                continue  # 最新回执已 done/captain：旧 blocked 不重复告警
            age_minutes = (current - ts).total_seconds() / 60
            if age_minutes <= blocked_grace_minutes:
                continue  # 严格大于 grace 才告警（age == grace 不报）
            pane_id = str(agent.get("pane_id") or agent.get("name") or "?")
            name = str(agent.get("name") or pane_id)
            receipt_id = str(receipt.get("receipt_id") or "?")
            findings.append(Finding(
                "blocked-overdue", pane_id, _label(tab),
                f"{name} 最新回执 {receipt_id} 仍为 blocked，已 {int(age_minutes)} 分钟"
                f"（grace {blocked_grace_minutes} 分钟）",
                None,
                "按 R6 回写原工位并处置：re-task / close / escalate；"
                "blocked 工位不适用 2 小时保留窗",
            ))

    findings.sort(key=lambda finding: (finding.target, finding.kind))
    return findings


# ---------------------------------------------------------------------------
# 关闭动作（--apply 仅作用于 Finding.action）
# ---------------------------------------------------------------------------

def close_pane(pane_id: str) -> bool:
    return herdr_run(["pane", "close", pane_id]).returncode == 0


def close_tab(tab_id: str) -> bool:
    return herdr_run(["tab", "close", tab_id]).returncode == 0


def apply_findings(
    findings: list[Finding],
    probe: Callable[[str], bool | None] | None = None,
) -> tuple[list[str], list[str], list[Finding]]:
    """执行关闭动作；pane 关闭前用 probe 复检一次（S-2：缩小判定与关闭竞态）。

    复检结果不是明确的 True（空闲交互 shell）时不关闭，回到「仅报告」。
    """
    closed_panes: list[str] = []
    closed_tabs: list[str] = []
    failed: list[Finding] = []
    for finding in findings:
        if finding.action not in ("pane", "tab"):
            continue
        if finding.action == "pane" and probe is not None:
            try:
                confirmed = probe(finding.target)
            except Exception:  # noqa: BLE001 - 探针异常按无法确认（不关闭）
                confirmed = None
            if confirmed is not True:
                print(f"[跳过] pane {finding.target} 复检未确认空闲"
                      f"（{confirmed!r}），改为仅报告", flush=True)
                continue
        try:
            ok = (close_pane if finding.action == "pane" else close_tab)(finding.target)
        except AuditError as exc:
            print(f"[失败] 关闭 {finding.action} {finding.target}：{exc}",
                  file=sys.stderr, flush=True)
            failed.append(finding)
            continue
        if ok:
            (closed_panes if finding.action == "pane" else closed_tabs).append(finding.target)
            print(f"已关闭 {finding.action} {finding.target}（{KIND_TEXT[finding.kind]}："
                  f"{finding.label}）", flush=True)
        else:
            print(f"[失败] 关闭 {finding.action} {finding.target}：herdr 返回非零",
                  file=sys.stderr, flush=True)
            failed.append(finding)
    return closed_panes, closed_tabs, failed


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        description="工位/tab 巡检：默认 dry-run 只报告，--apply 才关闭安全项。"
                    "自检默认动作：pane-audit --apply。",
        epilog=(
            "自检默认：角色「定时自检」每轮跑 `pane-audit --apply`；自动动作只限"
            "「非白名单、非 working、明确空 pane / 空 tab / 完成流 tab」。\n"
            "巡检口径：单 tab ≤6 pane（含 root）；总舵 tab（标签含「总舵」或配置 helm.tabLabel）"
            "只允许 1 个 pane，多 pane 报告；总舵 tab 内多余空 pane 自动关（保留总舵 pane，"
            "FB-038：交接 tab create 后多出的空 shell pane）；pane 无 registry / registry 无 pane / "
            "空 tab（非总舵）告警；working 永不关闭。\n"
            "空 pane：无 registry、无 agent 字段、状态 idle/unknown，且 `herdr pane process-info` "
            "确认前台是空闲交互 shell（进程组组长是 shell 且 argv 无脚本/命令参数），所在 tab "
            "仍有有名 crew 工位（registry agent 名非空或 --helm-pane）时才自动关；跑脚本的 "
            "shell（bash build.sh、等 read 的向导等）与空闲 shell 靠进程组/argv 区分，"
            "process-info 不可用、argv 无法确认、前台非 shell、无工位 tab（含个人 TUI tab）"
            "一律只报告。\n"
            "竞态窗口：pane 关闭前会用 process-info 复检一次（复检不通过则跳过关闭）；tab 回收"
            "基于本轮快照，关闭前不重新取数——快照与关闭之间被人工开始使用的 tab 仍可能被关，"
            "此为已知残余窗口。\n"
            "完成流回收：flow plan（<root>/.planning/<流>/task_plan.md）按 `### Phase` 分段解析 "
            "`**Status:**`，段内缺失/非法默认 pending（非法行在 stderr 告警，该流按未完成处理）；"
            "全部 Phase complete 且 tab 内 agent 全 idle/done、名称可归属流时提议关闭 tab，"
            "--apply 关闭；当前聚焦 tab、helm tab、含 blocked/working/无法确认 pane 的 tab 不回收。\n"
            "白名单默认：tab 标签前缀 Captain· / 流程优化· 整体跳过（不自动关任何 pane/tab）；"
            "总舵识别优先于这两个前缀（标签同时命中时按总舵 tab 处理）；agent 名 helm*；"
            "可用 --whitelist-prefix / --helm-pane 追加。\n"
            "blocked 巡检：读取 <crew-dir>/inbox 回执，blocked agent 取最新回执（ts 最大），"
            "仍 blocked 且年龄 > --blocked-grace（默认 30 分钟）时告警 blocked-overdue，"
            "按 R6 处置（re-task / close / escalate）；仅报告，--apply 不关闭。\n"
            "示例：python3 scripts/pane-audit.py --snapshot snapshot.json\n"
            "      python3 scripts/pane-audit.py --apply\n"
            "      python3 scripts/pane-audit.py --blocked-grace 45 --crew-dir <git-common-dir>/crew"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument(
        "--root",
        default=".",
        help="项目根（读取 .planning/orchestration.json 的 helm.tabLabel；默认 cwd）",
    )
    parser.add_argument(
        "--snapshot",
        help="fixture JSON 数据源（不调用 herdr；接受 {tabs,panes,agents} 或 herdr result 包装）",
    )
    parser.add_argument("--workspace", help="只巡检指定 workspace_id（缺 workspace_id 的条目保留）")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="关闭白名单外的安全项（空 tab / 多余空 pane / 完成流 tab；"
             "working 永不关闭，白名单跳过）",
    )
    parser.add_argument(
        "--whitelist-prefix",
        action="append",
        default=[],
        help="额外的 tab 标签白名单前缀（可重复；默认已含 Captain· / 流程优化·）",
    )
    parser.add_argument(
        "--helm-pane",
        action="append",
        default=[],
        help="总舵本 pane_id（可重复；默认按 agent 名 helm* 或 tab 标签含「总舵」/配置 helm.tabLabel 识别）",
    )
    parser.add_argument(
        "--blocked-grace",
        type=int,
        default=DEFAULT_BLOCKED_GRACE_MINUTES,
        metavar="MIN",
        help="blocked 最新回执年龄上限（分钟，默认 30；严格大于才告警，≤0 报参数错误）",
    )
    parser.add_argument(
        "--crew-dir",
        help="运行时状态目录（默认 crew_paths.crew_dir(--root)；读取其 inbox/*.json 回执）",
    )
    return parser


def resolve_crew_dir_arg(root: str, explicit: str | None) -> Path | None:
    """--crew-dir 或 crew_paths.crew_dir(root)；缺失/解析失败 fail-soft 返回 None。"""
    try:
        if explicit:
            return Path(explicit).expanduser()
        return resolve_crew_dir(root)
    except (RuntimeError, OSError):  # CrewPathsError / ~ 展开失败：不报错，跳过 blocked 巡检
        return None


def main(argv: list[str] | None = None) -> int:
    parser = add_arguments(build_parser())
    args = parser.parse_args(argv)
    if args.blocked_grace <= 0:
        print("error: --blocked-grace 必须为正整数（分钟）", file=sys.stderr, flush=True)
        return 2
    try:
        snapshot = load_fixture(args.snapshot) if args.snapshot else load_live_snapshot()
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        return 1
    for key in ("tabs", "panes", "agents"):
        snapshot[key] = filter_workspace(snapshot[key], args.workspace)

    prefixes = tuple(DEFAULT_WHITELIST_PREFIXES) + tuple(args.whitelist_prefix)
    helm_labels = load_helm_labels(args.root)
    flows = load_flow_plans(args.root)
    blocked_receipts = load_receipts(resolve_crew_dir_arg(args.root, args.crew_dir))
    findings = audit(
        snapshot, prefixes, frozenset(args.helm_pane), helm_labels,
        flows=flows,
        empty_probe=probe_empty_pane,
        blocked_receipts=blocked_receipts,
        blocked_grace_minutes=args.blocked_grace,
    )
    if not findings:
        print(
            f"pane 巡检通过：{len(snapshot['tabs'])} 个 tab / {len(snapshot['panes'])} 个 pane / "
            f"{len(snapshot['agents'])} 条 registry，无告警。",
            flush=True,
        )
        return 0

    for finding in findings:
        print(format_finding(finding), flush=True)

    closable = [finding for finding in findings if finding.action]
    if not args.apply:
        print(
            f"\ndry-run：共 {len(findings)} 项告警，其中可安全自动关闭 {len(closable)} 项"
            f"（空 tab / 多余空 pane / 完成流 tab）；确认后加 --apply。",
            flush=True,
        )
        return 0

    closed_panes, closed_tabs, failed = apply_findings(findings, probe=probe_empty_pane)
    reported = len(findings) - len(closed_panes) - len(closed_tabs) - len(failed)
    print(
        f"\n已关闭：{len(closed_panes)} 个 pane、{len(closed_tabs)} 个 tab；"
        f"失败 {len(failed)} 项；其余 {reported} 项仅报告。",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(0)
    except AuditError as exc:
        print(f"error: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1)
