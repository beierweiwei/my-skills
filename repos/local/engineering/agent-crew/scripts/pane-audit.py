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
- 多个总舵 tab / 旧 helm 残留（FB-045）：现役总舵解析优先级＝guard.handoff
  （phase=started/done 的 new；warn 的 old）→ dispatch registry role=helm 最新
  startedAt → 无法判定只报告。现役总舵 tab 永不关；旧总舵 tab 仅在非聚焦、
  非白名单、pane 全为空或 registry agent idle/done 且无 working/blocked/无法确认
  时，--apply 经 `dispatch tab close` 回收；handoff old ≠ 现役且 agent 仍在时告警
  「旧 helm 残留」，仅 idle/done、pane 非现役/非聚焦/非白名单时经
  `dispatch pane close` 回收；其余只报告（含各 tab id/标签/所属 agent/status）；
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
`--snapshot FILE` 注入 fixture JSON（测试/复算用，不调用 herdr）。**`--snapshot` 与
`--apply` 互斥（rc=2）**：fixture 可能已过期，禁止据此执行真实关闭；apply 请基于
live 数据重跑，或先用 dry-run 只报告（S-1）。

总舵/旧 helm 回收为 best-effort（S-7）：workspace 缺失/不一致、聚焦未知、空 pane
无法确认等任一不确定即不关；`dispatch` 关闭前不做二次复检（tab 回收基于本轮快照，
与既有 tab 回收同一残余竞态窗口）。

后台 job 语义（S12）：pane 内用 `&` / `nohup` 等启动的后台 job，在 `--apply` 关闭
pane 时仍可能收到 SIGHUP；本工具只按前台进程组/进程信息判定，不保证后台 job 安全。

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
    "multiple-helm-tabs": "多个总舵 tab",
    "residual-old-helm": "旧 helm 残留",
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


# ---------------------------------------------------------------------------
# 现役总舵解析（handoff → registry → unknown；FB-045）
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HelmContext:
    """现役总舵上下文；current 为空表示无法判定（只报告）。"""

    current: str = ""
    current_pane: str = ""
    current_tab: str = ""
    source: str = "unknown"
    old: str = ""
    old_pane: str = ""
    old_tab: str = ""
    reason: str = ""


def read_handoff(crew_dir: Path | None) -> dict[str, str]:
    """读取 `<crew-dir>/guard.handoff`；缺失/损坏 fail-soft 返回 {}。"""
    if crew_dir is None:
        return {}
    try:
        text = (Path(crew_dir) / "guard.handoff").read_text(encoding="utf-8")
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


def load_helm_registry(crew_dir: Path | None) -> list[tuple[str, str, str]]:
    """registry 中 role=helm 条目 → [(startedAt, name, pane)]；损坏 fail-soft。"""
    if crew_dir is None:
        return []
    try:
        data = json.loads(
            (Path(crew_dir) / "dispatch-agents.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    entries: list[tuple[str, str, str]] = []
    for name, info in data.items():
        if not isinstance(info, dict) or info.get("role") != "helm":
            continue
        pane = info.get("pane") or info.get("pane_id") or ""
        entries.append((str(info.get("startedAt") or ""), str(name), str(pane)))
    return entries


def resolve_helm_context(root: str | Path, crew_dir: Path | None = None) -> HelmContext:
    """现役总舵解析优先级：handoff（started/done 的 new；warn 的 old）→ registry
    最新 startedAt → unknown。无法判定时 current 为空（调用方只报告）。"""
    if crew_dir is None:
        try:
            crew_dir = resolve_crew_dir(root)
        except (RuntimeError, OSError):
            crew_dir = None
    handoff = read_handoff(crew_dir)
    phase = handoff.get("phase", "")
    old = handoff.get("old", "")
    old_pane = handoff.get("oldPane", "")
    old_tab = handoff.get("oldTab", "")
    new = handoff.get("new", "")
    result = handoff.get("result", "")
    if phase in ("started", "done") and result == "failed":
        # B-1 / §5.12：失败继任可能已 dispatch start 进 registry，绝不能回退 registry
        # 把它当现役（会把真实现役旧舵 tab 误判为可回收）。现役 unknown，只报告。
        return HelmContext(
            source="unknown",
            old=old, old_pane=old_pane, old_tab=old_tab,
            reason="handoff 上次交接失败（result=failed），现役不确定，只报告",
        )
    if phase in ("started", "done") and new:
        return HelmContext(
            current=new,
            current_pane=handoff.get("newPane", ""),
            current_tab=handoff.get("newTab", ""),
            source="handoff:new",
            old=old, old_pane=old_pane, old_tab=old_tab,
        )
    if phase == "warn" and old:
        return HelmContext(
            current=old,
            current_pane=old_pane,
            current_tab=old_tab,
            source="handoff:old",
            old=old, old_pane=old_pane, old_tab=old_tab,
        )
    entries = load_helm_registry(crew_dir)
    if entries:
        entries.sort(reverse=True)
        latest_started = entries[0][0]
        if sum(1 for entry in entries if entry[0] == latest_started) == 1:
            _started, name, pane = entries[0]
            return HelmContext(
                current=name,
                current_pane=pane,
                source=f"registry:{name}",
                old=old, old_pane=old_pane, old_tab=old_tab,
            )
    return HelmContext(old=old, old_pane=old_pane, old_tab=old_tab)


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


def _pane_descriptor(pane: dict, agent: dict | None) -> str:
    pane_id = str(pane.get("pane_id") or "?")
    status = str(pane.get("agent_status") or "unknown")
    if agent is not None:
        return (f"{pane_id}[{agent.get('name') or '?'} "
                f"status={agent.get('agent_status') or 'unknown'}]")
    return f"{pane_id}[无 registry status={status}]"


def _helm_tab_summary(
    tab_id: str,
    tab: dict,
    panes_by_tab: dict[str, list[dict]],
    agents_by_pane: dict[str, dict],
) -> str:
    panes = panes_by_tab.get(tab_id, [])
    descriptors = ", ".join(
        _pane_descriptor(pane, agents_by_pane.get(str(pane.get("pane_id") or "")))
        for pane in panes
    ) or "无 pane"
    focused = tab.get("focused")
    focus_text = ("聚焦" if focused is True
                  else "非聚焦" if focused is False else "聚焦未知")
    return f"{tab_id}（{_label(tab)}，{focus_text}）：{descriptors}"


def _helm_tab_reclaimable(
    tab_id: str,
    tab: dict,
    panes_by_tab: dict[str, list[dict]],
    agents_by_pane: dict[str, dict],
    whitelist_prefixes: tuple[str, ...],
    probe: Callable[[str], bool | None],
    current_name: str,
    current_workspace: str,
) -> tuple[bool, str]:
    """总舵 tab 回收安全判定（保守）：任一不确定即不关。

    S-3：能定位现役 workspace 时，tab/pane/agent 的 `workspace_id` 必须显式一致；
    缺失即视为无法确认归属，只报告不回收。定位不到 workspace 时零动作。
    """
    if not current_workspace:
        return False, "无法定位现役 workspace"
    if str(tab.get("workspace_id") or "") != current_workspace:
        return False, "tab workspace 缺失或与现役不一致"
    label = str(tab.get("label") or "").strip()
    if label.startswith(whitelist_prefixes):
        return False, "白名单 tab"
    if tab.get("focused") is True:
        return False, "tab 当前聚焦"
    panes = panes_by_tab.get(tab_id, [])
    if not panes:
        if tab.get("focused") is not False:
            return False, "空 tab 聚焦无法判定"
        return True, "空 tab"
    for pane in panes:
        pane_id = str(pane.get("pane_id") or "")
        if not pane_id:
            return False, "pane id 缺失"
        if str(pane.get("workspace_id") or "") != current_workspace:
            return False, f"pane {pane_id} workspace 缺失或与现役不一致"
        focused = pane.get("focused")
        if focused is True:
            return False, f"pane {pane_id} 聚焦"
        if not isinstance(focused, bool) and not isinstance(tab.get("focused"), bool):
            return False, f"pane {pane_id} 聚焦无法判定"
        agent = agents_by_pane.get(pane_id)
        if agent is not None:
            name = str(agent.get("name") or "")
            status = str(agent.get("agent_status") or "")
            if str(agent.get("workspace_id") or "") != current_workspace:
                return False, f"agent {name or pane_id} workspace 缺失或与现役不一致"
            if name and name == current_name:
                return False, f"pane {pane_id} 是现役总舵 {name}"
            if status not in IDLE_AGENT_STATUSES:
                return False, f"agent {name or pane_id} 状态 {status or 'unknown'}"
            continue
        status = str(pane.get("agent_status") or "unknown")
        if status not in EMPTY_PANE_STATUSES:
            return False, f"pane {pane_id} 状态 {status}"
        if pane.get("agent"):
            return False, f"pane {pane_id} 有未进 registry 的 TUI"
        if probe(pane_id) is not True:
            return False, f"pane {pane_id} 空 pane 无法确认"
    return True, ""


def helm_reclaim_findings(
    tab_by_id: dict[str, dict],
    panes_by_tab: dict[str, list[dict]],
    agents: list[dict],
    agents_by_pane: dict[str, dict],
    helm_labels: tuple[str, ...],
    whitelist_prefixes: tuple[str, ...],
    probe: Callable[[str], bool | None],
    helm_context: HelmContext | None,
    pane_by_id: dict[str, dict],
) -> list[Finding]:
    """「多个总舵 tab」与「旧 helm 残留」发现（FB-045）。

    - 现役总舵 tab 永不关；无法判定现役 → 全部只报告；
    - 旧总舵 tab 仅在 tab 非聚焦、非白名单、pane 全为空或 registry agent idle/done
      时提议 `--apply` 经 dispatch 回收（action=dispatch-tab）；
    - 旧 helm agent 残留：仅 idle/done、非现役 pane/tab、非聚焦、非白名单时
      提议 dispatch pane close（action=dispatch-pane）。
    """
    context = helm_context or HelmContext()
    agent_by_name = {
        str(agent.get("name") or ""): agent
        for agent in agents if agent.get("name")
    }
    current_name = context.current
    # 无法判定现役总舵（current 为空）时，pane/tab 字段一律不可信 → 只报告。
    current_pane = context.current_pane if current_name else ""
    current_tab = context.current_tab if current_name else ""
    current_agent = agent_by_name.get(current_name) if current_name else None
    if current_agent is not None:
        current_pane = (str(current_agent.get("pane_id") or current_agent.get("pane")
                            or "") or current_pane)
        tab_from_agent = str(current_agent.get("tab_id") or "")
        if not tab_from_agent and current_pane:
            tab_from_agent = str(
                pane_by_id.get(current_pane, {}).get("tab_id") or "")
        if tab_from_agent:
            current_tab = tab_from_agent
    if current_pane and not current_tab:
        current_tab = str(pane_by_id.get(current_pane, {}).get("tab_id") or "")
    if current_tab and current_tab not in tab_by_id:
        current_tab = ""  # handoff/registry 的 tab 已不存在：以快照为准
    # 跨 workspace 保护（S-3）：能定位现役总舵 workspace 时，只对账显式同
    # workspace 的 tab/pane/agent；缺 workspace_id 的条目只报告、不回收；
    # 定位不到 workspace 时总舵/旧 helm 回收零动作、只报告。显式其它 workspace
    # 的 tab 不报告也不触碰（herdr 列表是全局的，别的项目「总舵」不归本仓处置）。
    current_workspace = ""
    if current_agent is not None:
        current_workspace = str(current_agent.get("workspace_id") or "")
    if not current_workspace and current_pane:
        current_workspace = str(
            pane_by_id.get(current_pane, {}).get("workspace_id") or "")
    if not current_workspace and current_tab:
        current_workspace = str(
            tab_by_id.get(current_tab, {}).get("workspace_id") or "")

    def _workspace_state(item: dict) -> str:
        if not current_workspace:
            return "unknown"
        workspace = str(item.get("workspace_id") or "")
        if not workspace:
            return "missing"
        return "match" if workspace == current_workspace else "other"

    helm_tabs = {
        tab_id: tab for tab_id, tab in tab_by_id.items()
        if is_helm_tab(tab, helm_labels)
        and _workspace_state(tab) != "other"
    }
    findings: list[Finding] = []
    if len(helm_tabs) >= 2:
        summaries = "；".join(
            _helm_tab_summary(tab_id, tab, panes_by_tab, agents_by_pane)
            for tab_id, tab in sorted(helm_tabs.items())
        )
        current_tab_ready = bool(
            current_tab and current_tab in helm_tabs
            and _workspace_state(helm_tabs[current_tab]) == "match")
        if current_tab_ready:
            current_summary = _helm_tab_summary(
                current_tab, helm_tabs[current_tab], panes_by_tab, agents_by_pane)
            for tab_id, tab in sorted(helm_tabs.items()):
                if tab_id == current_tab:
                    continue
                state = _workspace_state(tab)
                if state == "missing":
                    safe, reason = False, "tab 缺 workspace_id（无法确认归属）"
                elif state != "match":
                    safe, reason = False, "无法定位现役 workspace"
                else:
                    safe, reason = _helm_tab_reclaimable(
                        tab_id, tab, panes_by_tab, agents_by_pane,
                        whitelist_prefixes, probe, current_name, current_workspace,
                    )
                detail = (f"现役总舵 {current_name or '?'}（{context.source}）在 "
                          f"{current_summary}；本 tab 非现役。全部总舵 tab：{summaries}")
                if safe:
                    findings.append(Finding(
                        "multiple-helm-tabs", tab_id, _label(tab),
                        f"{detail}；判定：可回收（{reason}）", "dispatch-tab",
                        "执行 `python3 scripts/pane-audit.py --apply` 经 dispatch "
                        "关闭旧总舵 tab（保留现役）",
                    ))
                else:
                    findings.append(Finding(
                        "multiple-helm-tabs", tab_id, _label(tab),
                        f"{detail}；判定：仅报告（{reason or '无法确认'}）", None,
                        f"人工确认后关闭：`python3 scripts/dispatch.py tab close "
                        f"--tab {tab_id}`（现役总舵 tab {current_tab} 永不关）",
                    ))
        else:
            findings.append(Finding(
                "multiple-helm-tabs", "helm-tabs", "总舵",
                f"检测到 {len(helm_tabs)} 个总舵 tab，无法确认现役总舵 tab"
                f"（来源 {context.source}"
                f"{'：' + context.reason if context.reason else ''}）；{summaries}",
                None,
                "人工确认现役总舵后关闭旧 tab（`python3 scripts/dispatch.py tab "
                "close --tab <旧tab>`）；handoff/registry 缺失或矛盾时本项只报告",
            ))

    if context.old and context.old != current_name and context.old in agent_by_name:
        old_agent = agent_by_name[context.old]
        old_pane = (str(old_agent.get("pane_id") or old_agent.get("pane") or "")
                    or context.old_pane)
        old_status = str(old_agent.get("agent_status") or "")
        old_pane_entry = pane_by_id.get(old_pane)
        old_tab = (str(old_agent.get("tab_id") or "")
                   or str((old_pane_entry or {}).get("tab_id") or "")
                   or context.old_tab)
        old_tab_entry = tab_by_id.get(old_tab)
        label = _label(old_tab_entry)
        detail = (f"handoff old={context.old} 仍存在（status="
                  f"{old_status or 'unknown'}，pane={old_pane or '未知'}，"
                  f"tab={old_tab or '未知'}（{label}））；现役 {current_name or '未知'}")
        covered = any(
            finding.kind == "multiple-helm-tabs" and finding.target == old_tab
            for finding in findings
        )
        if covered:
            findings.append(Finding(
                "residual-old-helm", old_pane or context.old, label,
                f"{detail}；由「多个总舵 tab」回收覆盖", None,
                "随旧总舵 tab 一并回收（见上一条告警）",
            ))
            return findings
        safe = True
        reason = ""
        if not current_name:
            safe, reason = False, "无法判定现役总舵"
        elif not current_workspace:
            safe, reason = False, "无法定位现役 workspace（零动作）"
        elif old_status not in IDLE_AGENT_STATUSES:
            safe, reason = False, f"agent 状态 {old_status or 'unknown'}"
        elif not old_pane or not old_pane_entry:
            safe, reason = False, "pane 缺失或无法确认"
        elif old_pane == current_pane:
            safe, reason = False, "pane 是现役总舵"
        elif old_tab and old_tab == current_tab:
            safe, reason = False, "tab 是现役总舵"
        elif old_pane_entry.get("focused") is not False:
            safe, reason = False, "pane 聚焦或聚焦无法判定"
        elif old_tab_entry is None:
            safe, reason = False, "tab 无法确认（白名单/聚焦未知）"
        elif str(old_agent.get("workspace_id") or "") != current_workspace \
                or str(old_pane_entry.get("workspace_id") or "") != current_workspace:
            safe, reason = False, "旧 helm agent/pane workspace 缺失或与现役不一致"
        elif str(old_tab_entry.get("workspace_id") or "") != current_workspace:
            safe, reason = False, "旧 helm tab workspace 缺失或与现役不一致"
        elif str(old_tab_entry.get("label") or "").strip().startswith(
                whitelist_prefixes):
            safe, reason = False, "白名单 tab"
        elif old_tab_entry.get("focused") is True:
            safe, reason = False, "tab 当前聚焦"
        action = "dispatch-pane" if safe else None
        verdict = "可回收" if safe else f"仅报告（{reason or '无法确认'}）"
        next_step = (
            "执行 `python3 scripts/pane-audit.py --apply` 经 dispatch 关闭旧 helm pane"
            if safe else
            "人工确认后关闭：`python3 scripts/dispatch.py pane close --pane "
            f"{old_pane}`（working/blocked/聚焦/白名单不自动关）"
        )
        findings.append(Finding(
            "residual-old-helm", old_pane or context.old, label,
            f"{detail}；判定：{verdict}", action, next_step,
        ))
    return findings


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
    helm_context: HelmContext | None = None,
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
    pane_by_id: dict[str, dict] = {}
    for pane in panes:
        pane_id = pane.get("pane_id")
        if not pane_id:
            continue
        pane_ids.add(str(pane_id))
        pane_by_id[str(pane_id)] = pane
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

    if helm_context is not None:
        findings.extend(helm_reclaim_findings(
            tab_by_id, panes_by_tab, agents, agents_by_pane, helm_labels,
            whitelist_prefixes, probe, helm_context, pane_by_id,
        ))

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


def make_dispatch_runner(root: str | Path) -> Callable[[str, str], bool]:
    """总舵/旧 helm 回收统一走 `dispatch.py pane|tab close`（禁止直写 herdr 生命周期）。"""
    script = Path(__file__).resolve().parent / "dispatch.py"

    def runner(kind: str, target: str) -> bool:
        args = (["pane", "close", "--pane", target] if kind == "pane"
                else ["tab", "close", "--tab", target])
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--root", str(root), *args],
                text=True, capture_output=True, check=False,
            )
        except OSError as exc:
            print(f"[失败] dispatch {kind} close {target}：{exc}",
                  file=sys.stderr, flush=True)
            return False
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()
            print(f"[失败] dispatch {kind} close {target}："
                  f"{detail or f'退出码 {proc.returncode}'}",
                  file=sys.stderr, flush=True)
            return False
        return True

    return runner


def apply_findings(
    findings: list[Finding],
    probe: Callable[[str], bool | None] | None = None,
    dispatch_runner: Callable[[str, str], bool] | None = None,
) -> tuple[list[str], list[str], list[Finding]]:
    """执行关闭动作；pane 关闭前用 probe 复检一次（S-2：缩小判定与关闭竞态）。

    复检结果不是明确的 True（空闲交互 shell）时不关闭，回到「仅报告」。
    `dispatch-pane` / `dispatch-tab`（总舵/旧 helm 回收）统一经 dispatch_runner；
    未注入时按失败记录（不静默回落裸 herdr）。
    """
    closed_panes: list[str] = []
    closed_tabs: list[str] = []
    failed: list[Finding] = []
    for finding in findings:
        if finding.action not in ("pane", "tab", "dispatch-pane", "dispatch-tab"):
            continue
        via_dispatch = finding.action.startswith("dispatch-")
        kind = finding.action.rsplit("-", 1)[-1] if via_dispatch else finding.action
        if kind == "pane" and not via_dispatch and probe is not None:
            try:
                confirmed = probe(finding.target)
            except Exception:  # noqa: BLE001 - 探针异常按无法确认（不关闭）
                confirmed = None
            if confirmed is not True:
                print(f"[跳过] pane {finding.target} 复检未确认空闲"
                      f"（{confirmed!r}），改为仅报告", flush=True)
                continue
        try:
            if via_dispatch:
                if dispatch_runner is None:
                    print(f"[失败] 回收 {finding.action} {finding.target}："
                          "未配置 dispatch runner", file=sys.stderr, flush=True)
                    failed.append(finding)
                    continue
                ok = bool(dispatch_runner(kind, finding.target))
            else:
                ok = (close_pane if kind == "pane" else close_tab)(finding.target)
        except AuditError as exc:
            print(f"[失败] 关闭 {kind} {finding.target}：{exc}",
                  file=sys.stderr, flush=True)
            failed.append(finding)
            continue
        if ok:
            (closed_panes if kind == "pane" else closed_tabs).append(finding.target)
            print(f"已关闭 {kind} {finding.target}（{KIND_TEXT[finding.kind]}："
                  f"{finding.label}）", flush=True)
        else:
            print(f"[失败] 关闭 {kind} {finding.target}：返回非零",
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
            "数据源互斥（S-1）：--snapshot 与 --apply 不可同用（rc=2）；fixture 可能过期，"
            "禁止据此执行真实关闭，apply 请基于 live 数据重跑。\n"
            "总舵回收 best-effort（S-7）：workspace 缺失/不一致、聚焦未知、空 pane 无法确认"
            "等任一不确定即不关；dispatch 关闭前不做二次复检（tab 基于本轮快照，与既有 tab"
            "回收同一残余竞态窗口）。\n"
            "总舵回收（FB-045）：多个总舵 tab / 旧 helm 残留默认只报告；现役总舵按 "
            "guard.handoff（started/done 的 new、warn 的 old）→ dispatch registry 最新 "
            "role=helm startedAt 解析；done/started result=failed 一律 unknown、不回退 "
            "registry；无法判定只报告。现役总舵 tab 永不关；旧总舵 tab 仅在非聚焦、"
            "非白名单、workspace 显式一致、pane 全空或 agent idle/done 时 --apply 经 "
            "dispatch 回收；旧 helm agent 残留仅 idle/done 且非现役/非聚焦/非白名单/"
            "同 workspace 时经 dispatch 关 pane；working/blocked/无法确认一律不关。\n"
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
            "后台 job 语义：pane 内后台 job（& / nohup 等）在 --apply 关闭 pane 时仍可能"
            "收到 SIGHUP；本工具只按前台进程组/进程信息判定，不保证后台 job 安全。\n"
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
    if args.apply and args.snapshot:
        # S-1：fixture 快照可能已过期，禁止据此执行真实关闭（退化为「看陈旧数据关窗口」）。
        print(
            "error: --apply 与 --snapshot 互斥：fixture 快照可能已过期，"
            "禁止据此执行真实关闭；请去掉 --snapshot 基于 live 数据重跑，"
            "或先 dry-run 只报告。",
            file=sys.stderr, flush=True,
        )
        return 2
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
    crew_dir = resolve_crew_dir_arg(args.root, args.crew_dir)
    blocked_receipts = load_receipts(crew_dir)
    helm_context = resolve_helm_context(args.root, crew_dir)
    findings = audit(
        snapshot, prefixes, frozenset(args.helm_pane), helm_labels,
        flows=flows,
        empty_probe=probe_empty_pane,
        blocked_receipts=blocked_receipts,
        blocked_grace_minutes=args.blocked_grace,
        helm_context=helm_context,
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
            f"（空 tab / 多余空 pane / 完成流 tab / 旧总舵与旧 helm 回收）；"
            "确认后加 --apply。",
            flush=True,
        )
        return 0

    closed_panes, closed_tabs, failed = apply_findings(
        findings,
        probe=probe_empty_pane,
        dispatch_runner=make_dispatch_runner(args.root),
    )
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
