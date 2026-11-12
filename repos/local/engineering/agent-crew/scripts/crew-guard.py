#!/usr/bin/env python3
"""编排值守（guard）：待办存在且无在跑 worker 时，连续 2 轮后叫醒总舵。

正常路径零 stdout；事件与异常写 <crew-dir>/guard.log。
单实例锁：<crew-dir>/guard.lock（第二实例静默退出）。
叫醒：优先 `herdr agent prompt <helm>`；总舵 pane 聚焦（T37：按 agent 的 pane_id
查 `herdr pane list` 的 focused）或聚焦状态无法判定（fail-safe）时改为写
<crew-dir>/guard.wake + Herdr 通知。每 5 分钟顺带执行 plan-status.py --write；
叫醒时执行 plan-status.py --check-fresh 并把结果带进叫醒消息。
guard.wake 首行 `generated_at: <ISO8601 Z>`（兼容旧格式裸 ISO 首行）；超过 2h
（7200s）或首行无法解析的遗留 wake 在每轮开始时删除并记 guard.log（fail-safe），
新鲜文件保留不动；wake 读写异常不中断值守。

待办口径（decisions §1）：
- 活跃流 plan 中 Phase Status ≠ complete 的未勾选 `[ ]`（排除含 ⏸ / 待 Captain / 留 Captain）；
- portfolio Backlog 中 T 行且阻塞列无 待 C / blocked / 已完成（complete/done）；
- 已完成 Phase 的残留 `[ ]` 只记日志，不叫醒。
在跑口径：Herdr agent_status == working + `<crew-dir>/runs/` 存活注册
（`crew-claude.py run` 写入；死 pid/坏 JSON/缺 pid 忽略并记日志；存活注册需进程
身份/年龄核验——cmdline 不含 crew-claude 视为 pid 复用、按死删除，cmdline 不可读
且 started_at >24h 忽略并保留取证，缺 started_at 按存活计入 fail-safe），排除
总舵自身与 Captain*；blocked 单独叫醒。
cwd 隔离：只统计 cwd 在本仓或 `git worktree list --porcelain` 列出的 linked worktree
内的 agent；外部项目（其他仓）不参与 running/blocked/快速叫醒。cwd 缺失或不可解析时
沿用旧口径包含（无法确认归属时不改变既有行为）。
快速叫醒：上一轮有 working、本轮全部转 idle 且有待办时立即叫醒（仍受 10 分钟冷却）。
blocked 年龄与重复叫醒：按 agent 身份（name，缺省 pane_id）记录首次 blocked 时间；
首次叫醒沿用 10 分钟全局冷却；仍 blocked 且距该 agent 上次 blocked 叫醒
≥ config `guard.blockedRepeatMinutes`（默认 15，夹取 1–240）再叫醒，该重复叫醒
不受全局冷却压制；blocked 累计 ≥ `guard.blockedGraceMinutes`（默认 30，夹取 1–240）
时叫醒消息提示「按 R6 处置（re-task / close / escalate），应关闭工位或升级 Captain」；
guard 不自动关闭/杀 agent；agent 不再 blocked 或消失即清除年龄。
自动交接：每轮读总舵 pane 的 `ctx Nk/1M`（`herdr pane read <pane> --lines 80`），
兼容小数与百分比（`ctx 75.4k/1M`、`ctx 62.3%`）；pane 先按 agent 名解析，
名字缺失/漂移时回退 <crew-dir>/dispatch-agents.json（按名或最近 role=helm 条目反查，
反查关系不可解释时 fail-safe），解析不到则 fail-safe 不动作。按 config
`helm.ctxWarn`（默认 0.30）/`helm.ctxHandoff`（默认 0.50）写 <crew-dir>/guard.handoff
并通知；≥交接阈值一次性新建 tab/继任/首报后关旧 pane 与旧 tab，失败只通知不循环；
`tab create` 后按 pane list 校验新 tab pane 数=1，多出的明确空 pane 经
`dispatch pane close` 关闭并记 guard.log（FB-038）；取数/关闭失败只告警不中断交接。
旧舵一律按 handoff `old` 身份判定，working 不杀；继任缺失/blocked/idle 无首报或旧舵
持续未收口连续 HANDOFF_STALL_ROUNDS 轮后记 failed 并通知人工。交接完成（phase=done）后
继续监控继任总舵，不因 done 永久短路。交接启动前先执行
`plan-status.py --rotate --apply --keep ROTATE_KEEP`（默认 10）归档 progress；
rotate 失败或异常只写 guard.log 告警，继续原交接流程（不阻断、不回落 split）。
回执返工提醒：每轮扫描 <crew-dir>/inbox/*.json 中带 `task` 的回执并分组，
同一 task ≥3 条时写一行 guard.log「返工提醒」（同一进程每 task 只记一次）；
不叫醒、不改 plan、不阻断、不删回执；--once --json 输出 `rework` 数组。

用法：
  python3 scripts/crew-guard.py                 # 每 120s 一轮
  python3 scripts/crew-guard.py --once          # 单轮（测试/人工抽查）
  python3 scripts/crew-guard.py --root <仓根> --helm <agent名> --crew-dir <目录>

兜底：建议另配 30–60 分钟 cron 只检查 guard 进程存活（如 pgrep -f crew-guard.py），
本脚本不自动创建 cron。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$")
STATUS_RE = re.compile(r"\*\*Status:\*\*\s+(pending|in[ _]progress|complete)")
TODO_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$")
PAUSE_MARKS = ("⏸", "待 Captain", "留 Captain")
# 英文完成态按词边界判定：避免 incomplete / undone 误命中 complete / done
DONE_RE = re.compile(r"(?<![a-z])(?:complete|completed|done)(?![a-z])")
COOLDOWN_SECONDS = 600
WRITE_INTERVAL_SECONDS = 300
# 存活 claude 运行注册的进程身份/年龄核验（互检建议-2）：pid 复用按死处理
CLAUDE_PROCESS_MARKER = "crew-claude"
CLAUDE_RUN_MAX_AGE_SECONDS = 24 * 3600
# os.kill 的 pid_t 为 32 位有符号；超出范围（如 2**63）报 OverflowError，按无效 pid 处理
PID_MAX_VALID = 2 ** 31 - 1
# 交接启动前 progress 轮转保留条数（与 plan-status.py --rotate 默认一致；显式常量便于审计）
ROTATE_KEEP = 10
PROBE_NAME_PREFIXES = ("s9-", "probe-", "test-")
CTX_RE = re.compile(
    r"ctx\s*\(?\s*(?:"
    r"(?P<used>\d+(?:\.\d+)?)\s*k\s*/\s*(?P<total>\d+(?:\.\d+)?)\s*(?P<unit>[kKmM])"
    r"|(?P<percent>\d+(?:\.\d+)?)\s*%"
    r")\s*\)?",
    re.IGNORECASE,
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DEFAULT_CTX_WARN = 0.30
DEFAULT_CTX_HANDOFF = 0.50
# 交接等待有界：继任缺失/blocked/idle 无首报，或旧舵持续未收口满 N 轮
# → phase=done result=failed
HANDOFF_STALL_ROUNDS = 3
# 创建阶段失败的 newPane/newTab 是孤儿资源；其它失败步骤下继任可能仍可用，提示保留。
HANDOFF_CREATE_FAILURE_STEPS = ("tab create", "dispatch start", "dispatch prompt", "state")
DEFAULT_TAB_LABEL = "总舵"
RETRO_SECTION = "流程反馈（复盘信箱）"
RETRO_OPEN_STATUS = "待复盘"
RETRO_MARKER_NAME = "retro.wake"
RETRO_FILE_RE = re.compile(r"^retro-(\d{8})\.md$")
DEFAULT_RETRO_PERIOD_DAYS = 7
DEFAULT_BLOCKED_REPEAT_MINUTES = 15
DEFAULT_BLOCKED_GRACE_MINUTES = 30
BLOCKED_MINUTES_MIN = 1
BLOCKED_MINUTES_MAX = 240
WAKE_FILE_NAME = "guard.wake"
DEFAULT_WAKE_TTL_SECONDS = 7200
WAKE_GENERATED_RE = re.compile(r"^generated_at:\s*(\S+)\s*$")
# 同一 task 回执达到该数量时写一条非阻断「返工提醒」（不叫醒、不改 plan、不删回执）
REWORK_RECEIPT_THRESHOLD = 3


@dataclass(frozen=True)
class Todo:
    plan: str
    phase: str
    text: str

    @property
    def label(self) -> str:
        return f"{self.plan} · {self.phase}: {self.text}"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_wake_generated_at(text: str) -> datetime | None:
    """解析 guard.wake 首行生成时间：新格式 `generated_at: <ISO>` 或旧格式裸 ISO。

    缺失、空白或无法解析返回 None（调用方按过期处理，fail-safe 不抛异常）。
    """
    lines = text.splitlines()
    if not lines:
        return None
    head = lines[0].strip()
    match = WAKE_GENERATED_RE.match(head)
    raw = (match.group(1) if match else head).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        generated = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return generated.astimezone(timezone.utc)


def wake_expired(generated: datetime | None, now: float) -> bool:
    """生成时间缺失/不可解析，或距今超过 DEFAULT_WAKE_TTL_SECONDS（2h）视为过期。"""
    if generated is None:
        return True
    return now - generated.timestamp() > DEFAULT_WAKE_TTL_SECONDS


def is_probe_agent(agent: dict) -> bool:
    """测试探针：按 agent 名或 cwd 判定，仅用于排除 blocked 叫醒。"""
    name = str(agent.get("name") or "").strip().lower()
    if name.startswith(PROBE_NAME_PREFIXES):
        return True
    cwd = agent.get("cwd")
    if not isinstance(cwd, str) or not cwd.strip():
        return False
    try:
        path = Path(cwd).expanduser()
        if not path.is_absolute():
            return False
        path = path.resolve()
        tmp = Path("/tmp").resolve()
        return path == tmp or tmp in path.parents
    except (OSError, RuntimeError):
        return False


def pid_alive(pid: int) -> bool:
    """pid 存活判定：ProcessLookupError=死；PermissionError=活（进程存在但无权限，
    与 crew-preflight 口径一致）；OverflowError（超出 pid_t 范围）按无效/死处理；
    其余 OSError 按死处理（值守不因坏 pid 崩溃）。"""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OverflowError:
        return False
    except OSError:
        return False
    return True


def proc_cmdline(pid: int) -> str:
    """读 /proc/<pid>/cmdline（NUL 分隔转空格）；不可读时抛 OSError。

    pid 复用核验用（建议-2）：只认 cmdline 含 crew-claude 的进程。
    """
    return (
        Path(f"/proc/{pid}/cmdline")
        .read_bytes()
        .replace(b"\0", b" ")
        .decode("utf-8", "replace")
        .strip()
    )


def registry_age_seconds(started_at: str, now: float) -> float | None:
    """运行注册 started_at（UTC ISO Z）→ 已运行秒数；缺失/不可解析返回 None。"""
    text = str(started_at or "").strip()
    if not text:
        return None
    try:
        started = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    return (datetime.fromtimestamp(now, timezone.utc) - started).total_seconds()


def parse_ctx_k(text: str) -> float | None:
    """从 `herdr pane read` 文本解析总舵 ctx，统一折算为每 1M 的 k 值。

    兼容 `ctx (412k/1M)`、`ctx 75.4k/1M`（小数）、`ctx ( 75.4k / 1M )`、
    `ctx 62.3%`（百分比折算为 623）；多行取最后一个匹配。解析失败
    （含 `ctx unknown`、空串、缺总量 `ctx 75.4k`）返回 None，不抛异常。
    """
    if not isinstance(text, str):
        return None
    matches = list(CTX_RE.finditer(ANSI_RE.sub("", text)))
    if not matches:
        return None
    match = matches[-1]
    percent = match.group("percent")
    if percent is not None:
        return float(percent) * 10.0
    used = float(match.group("used"))
    total = float(match.group("total"))
    if total <= 0:
        return None
    total_k = total * (1000.0 if match.group("unit").lower() == "m" else 1.0)
    return used / total_k * 1000.0


def parse_worktree_paths(output: str) -> set[Path]:
    roots: set[Path] = set()
    for line in output.splitlines():
        if line.startswith("worktree "):
            value = line[len("worktree "):].strip()
            if value:
                roots.add(Path(value).expanduser().resolve())
    return roots


def git_worktree_roots(root: Path) -> set[Path]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return parse_worktree_paths(proc.stdout) if proc.returncode == 0 else set()


def load_config(root: Path) -> dict:
    config = {
        "portfolioDir": ".planning/portfolio",
        "backlog": {"agentPrefix": "T"},
        "helm": {
            "ctxWarn": DEFAULT_CTX_WARN,
            "ctxHandoff": DEFAULT_CTX_HANDOFF,
            "tabLabel": DEFAULT_TAB_LABEL,
        },
        "retro": {"enabled": True, "periodDays": DEFAULT_RETRO_PERIOD_DAYS},
        "guard": {
            "blockedRepeatMinutes": DEFAULT_BLOCKED_REPEAT_MINUTES,
            "blockedGraceMinutes": DEFAULT_BLOCKED_GRACE_MINUTES,
        },
    }
    path = root / ".planning" / "orchestration.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return config
    if not isinstance(data, dict):
        return config
    portfolio = data.get("portfolioDir")
    if isinstance(portfolio, str) and portfolio.strip():
        config["portfolioDir"] = portfolio
    backlog = data.get("backlog")
    if isinstance(backlog, dict):
        prefix = backlog.get("agentPrefix")
        if isinstance(prefix, str) and prefix:
            config["backlog"]["agentPrefix"] = prefix
    helm = data.get("helm")
    if isinstance(helm, dict):
        for key, default in (("ctxWarn", DEFAULT_CTX_WARN),
                             ("ctxHandoff", DEFAULT_CTX_HANDOFF)):
            value = helm.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1:
                config["helm"][key] = float(value)
        label = helm.get("tabLabel")
        if isinstance(label, str) and label.strip():
            config["helm"]["tabLabel"] = label.strip()
    retro = data.get("retro")
    if isinstance(retro, dict):
        enabled = retro.get("enabled")
        if isinstance(enabled, bool):
            config["retro"]["enabled"] = enabled
        period = retro.get("periodDays")
        if isinstance(period, int) and not isinstance(period, bool):
            config["retro"]["periodDays"] = max(1, min(30, period))
    guard = data.get("guard")
    if isinstance(guard, dict):
        for key, default in (
            ("blockedRepeatMinutes", DEFAULT_BLOCKED_REPEAT_MINUTES),
            ("blockedGraceMinutes", DEFAULT_BLOCKED_GRACE_MINUTES),
        ):
            value = guard.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                config["guard"][key] = max(BLOCKED_MINUTES_MIN,
                                           min(BLOCKED_MINUTES_MAX, value))
    return config


def resolve_root(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).resolve()
    current = Path.cwd().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / ".planning").is_dir():
            return candidate
    return Path(__file__).resolve().parent.parent


def default_crew_dir(root: Path) -> Path:
    try:
        return resolve_crew_dir(root)
    except CrewPathsError as exc:
        raise SystemExit(f"crew-guard: 运行时目录解析失败：{exc}") from exc


def resolve_helm_name(root: Path, explicit: str | None) -> str:
    if explicit:
        return explicit
    configured = ""
    try:
        cfg = json.loads((root / ".planning" / "orchestration.json").read_text(encoding="utf-8"))
        if isinstance(cfg, dict) and isinstance(cfg.get("helm"), dict):
            configured = str(cfg["helm"].get("agentName") or "").strip()
    except (OSError, ValueError):
        pass
    if configured:
        return configured
    slug = re.sub(r"[^a-z0-9_-]+", "-", root.name.strip().lower()).strip("-")
    return f"helm-{slug or 'project'}"


def _strip_comments(lines: list[str]):
    """逐行产出非 HTML 注释行（与 plan-status.py 同口径）。"""
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


def _plan_phase_statuses(text: str) -> list[str]:
    """按 `### Phase` 分段取 `**Status:**`（缺省 pending）；HTML 注释先剥离，
    `## Notes` 等非 Phase 区域的 Status 不计入（与 plan-status.py 同口径）。"""
    statuses: list[str] = []
    in_phase = False
    for line in _strip_comments(text.splitlines()):
        if line.startswith("## "):
            in_phase = False
            continue
        if PHASE_RE.match(line):
            statuses.append("pending")
            in_phase = True
            continue
        if not in_phase:
            continue
        match = STATUS_RE.search(line)
        if match:
            statuses[-1] = match.group(1)
    return statuses


def _table_rows(lines: list[str]) -> list[list[str]]:
    rows, seen_header = [], False
    for line in lines:
        text = line.strip()
        if not (text.startswith("|") and text.endswith("|")):
            continue
        cells = [cell.strip() for cell in text.strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        if not seen_header:
            seen_header = True
            continue
        rows.append(cells)
    return rows


def _section_lines(path: Path, section: str) -> list[str]:
    lines, current = [], None
    for line in _strip_comments(path.read_text(encoding="utf-8").splitlines()):
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current == section:
            lines.append(line)
    return lines


def _parse_plan_todos(path: Path, rel: str, pending: list[Todo], residual: list[Todo]) -> None:
    phases: list[dict] = []
    current: dict | None = None
    for line in _strip_comments(path.read_text(encoding="utf-8").splitlines()):
        if line.startswith("## "):
            current = None
            continue
        head = PHASE_RE.match(line)
        if head:
            current = {"name": head.group(1), "status": "pending", "todos": []}
            phases.append(current)
            continue
        if current is None:
            continue
        status = STATUS_RE.search(line)
        if status:
            current["status"] = status.group(1)
            continue
        todo = TODO_RE.match(line)
        if todo:
            text = todo.group(1).strip()
            if any(mark in text for mark in PAUSE_MARKS):
                continue
            current["todos"].append(text)
    for phase in phases:
        bucket = residual if phase["status"] == "complete" else pending
        for text in phase["todos"]:
            bucket.append(Todo(rel, phase["name"], text))


def _parse_backlog(root: Path, path: Path, rel: str, pending: list[Todo]) -> None:
    config = load_config(root)
    prefix = config["backlog"]["agentPrefix"]
    for cells in _table_rows(_section_lines(path, "Backlog")):
        if len(cells) < 2:
            continue
        ident = cells[0]
        if not ident.startswith(prefix):
            continue
        if any("⏸" in cell for cell in cells):
            continue
        blocker = cells[-1]
        if any(mark in blocker for mark in ("待 C", "待 Captain", "留 Captain")):
            continue
        if "blocked" in blocker.lower():
            continue
        if "已完成" in blocker or DONE_RE.search(blocker.lower()):
            continue
        pending.append(Todo(rel, "Backlog", " ".join(cells[:2]).strip()))


def scan_todos(root: Path) -> tuple[list[Todo], list[Todo]]:
    planning = root / ".planning"
    pending: list[Todo] = []
    residual: list[Todo] = []
    if not planning.is_dir():
        return pending, residual
    config = load_config(root)
    portfolio = (root / config["portfolioDir"] / "task_plan.md").resolve()
    for path in sorted(planning.glob("*/task_plan.md")):
        rel = path.relative_to(root).as_posix()
        try:
            _parse_plan_todos(path, rel, pending, residual)
            if path.resolve() == portfolio:
                _parse_backlog(root, path, rel, pending)
        except (OSError, UnicodeDecodeError, ValueError):
            # S1：坏 plan（非 UTF-8/不可读）跳过，不拖垮整轮值守
            continue
    return pending, residual


def parse_feedback_open(path: Path, log: Callable[[str], None] | None = None) -> int | None:
    """统计「流程反馈（复盘信箱）」表 `状态=待复盘` 行数；缺失或损坏返回 None（fail-safe）。"""
    def note(message: str) -> None:
        if log is not None:
            log(message)

    try:
        lines = _section_lines(path, RETRO_SECTION)
    except (OSError, ValueError) as exc:
        note(f"feedback.md 读取失败，跳过复盘提醒：{exc}")
        return None
    table = [line for line in lines if line.strip().startswith("|")]
    if not table:
        note("feedback.md 缺「流程反馈（复盘信箱）」表，跳过复盘提醒")
        return None
    header = [cell.strip() for cell in table[0].strip().strip("|").split("|")]
    if "状态" not in header:
        note("feedback.md 复盘信箱表头缺「状态」列，跳过复盘提醒")
        return None
    status_idx = header.index("状态")
    open_rows = 0
    for raw in table[1:]:
        cells = [cell.strip() for cell in raw.strip().strip("|").split("|")]
        if all(set(cell) <= set("-: ") for cell in cells):
            continue
        if len(cells) != len(header):
            note(f"feedback.md 复盘信箱行格式损坏，跳过复盘提醒：{raw.strip()[:80]}")
            return None
        if cells[status_idx] == RETRO_OPEN_STATUS:
            open_rows += 1
    return open_rows


def latest_retro_date(portfolio: Path) -> date | None:
    """扫描 `<portfolio>/retro-YYYYMMDD.md`，返回最大有效日期；无报告返回 None。"""
    latest: date | None = None
    try:
        names = [entry.name for entry in portfolio.iterdir()]
    except OSError:
        return None
    for name in names:
        match = RETRO_FILE_RE.match(name)
        if not match:
            continue
        try:
            value = datetime.strptime(match.group(1), "%Y%m%d").date()
        except ValueError:
            continue
        if latest is None or value > latest:
            latest = value
    return latest


def herdr_agents() -> list[dict]:
    env = {**os.environ, "HERDR_ENV": "1"}
    proc = subprocess.run(
        ["herdr", "agent", "list"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "herdr agent list failed")
    payload = json.loads(proc.stdout)
    return payload.get("result", {}).get("agents", [])


def herdr_panes() -> list[dict]:
    env = {**os.environ, "HERDR_ENV": "1"}
    proc = subprocess.run(
        ["herdr", "pane", "list"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "herdr pane list failed")
    payload = json.loads(proc.stdout)
    return payload.get("result", {}).get("panes", [])


def herdr_prompt(name: str, message: str) -> bool:
    try:
        proc = subprocess.run(
            ["herdr", "agent", "prompt", name, message],
            text=True,
            capture_output=True,
            check=False,
            timeout=60,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def herdr_pane_read(pane: str, lines: int = 80) -> str:
    env = {**os.environ, "HERDR_ENV": "1"}
    proc = subprocess.run(
        ["herdr", "pane", "read", pane, "--lines", str(lines)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "herdr pane read failed")
    return proc.stdout


def run_dispatch(root: Path, args: list[str]) -> tuple[int, str, str]:
    script = Path(__file__).resolve().parent / "dispatch.py"
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(root), *args],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return proc.returncode, proc.stdout, proc.stderr


def herdr_notify(title: str, body: str) -> bool:
    """返回通知是否真实送达；非 0 退出按失败返回 False（A-3 审计真实性）。"""
    if os.environ.get("HERDR_ENV") != "1":
        return False
    try:
        proc = subprocess.run(
            ["herdr", "notification", "show", title, "--body", body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def run_plan_status_args(root: Path, args: list[str]) -> tuple[int, str]:
    """按 argv 列表调用 plan-status.py（多参数逐个成元素，禁止拼成单个参数串）。"""
    script = Path(__file__).resolve().parent / "plan-status.py"
    proc = subprocess.run(
        [sys.executable, str(script), *args, "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def run_plan_status(root: Path, flag: str) -> tuple[int, str]:
    return run_plan_status_args(root, [flag])


def acquire_lock(crew_dir: Path):
    """单实例锁；拿不到返回 None（第二实例静默退出）。"""
    crew_dir.mkdir(parents=True, exist_ok=True)
    handle = (crew_dir / "guard.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    handle.seek(0)
    handle.truncate()
    handle.write(f"{os.getpid()}\n")
    handle.flush()
    return handle


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def read_handoff_state(crew_dir: Path) -> dict[str, str]:
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


def resolve_guard_helm(root: Path, crew_dir: Path, explicit: str | None) -> str:
    """交接后重启 guard 时按 handoff 状态识别现役总舵（显式 --helm 不覆盖）。

    phase=warn → old（当前现役总舵）；phase=started / done(result≠failed)
    → new（继任）；done(result=failed) 或其它 → 配置 helm，不采用失败继任名。
    """
    current = resolve_helm_name(root, explicit)
    if explicit:
        return current
    state = read_handoff_state(crew_dir)
    phase = state.get("phase")
    if phase == "warn":
        return state.get("old") or current
    if phase == "started":
        return state.get("new") or current
    if phase == "done" and state.get("result") != "failed":
        return state.get("new") or current
    return current


@dataclass
class Guard:
    root: Path
    crew_dir: Path
    helm: str
    interval: int = 120
    clock: Callable[[], float] = time.time
    list_agents: Callable[[], list[dict]] = herdr_agents
    list_panes: Callable[[], list[dict]] = herdr_panes
    prompt: Callable[[str, str], bool] = herdr_prompt
    notify: Callable[[str, str], bool] = herdr_notify
    pane_read: Callable[[str, int], str] = herdr_pane_read
    dispatch: Callable[[Path, list[str]], tuple[int, str, str]] = run_dispatch
    run_status: Callable[[Path, str], tuple[int, str]] = run_plan_status
    run_rotate: Callable[[Path], tuple[int, str]] | None = None
    log_fn: Callable[[str], None] | None = None
    consecutive: int = field(default=0, init=False)
    prev_working: int = field(default=0, init=False)
    last_wake_at: float | None = field(default=None, init=False)
    last_write_at: float | None = field(default=None, init=False)
    blocked_since: dict[str, float] = field(default_factory=dict, init=False)
    blocked_last_wake: dict[str, float] = field(default_factory=dict, init=False)
    handoff_attempted: bool = field(default=False, init=False)
    handoff_failed_noted: bool = field(default=False, init=False)
    handoff_wait_rounds: int = field(default=0, init=False)
    handoff_close_rounds: int = field(default=0, init=False)
    rework_logged: set[str] = field(default_factory=set, init=False)
    rework_skipped: set[str] = field(default_factory=set, init=False)
    project_roots: set[Path] | None = None
    roots_cache: set[Path] | None = field(default=None, init=False)

    def log(self, message: str) -> None:
        if self.log_fn is not None:
            self.log_fn(message)
            return
        try:
            path = self.crew_dir / "guard.log"
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(f"{utc_now()} {message}\n")
        except OSError:
            pass

    def _read_handoff(self) -> dict[str, str]:
        return read_handoff_state(self.crew_dir)

    @staticmethod
    def _valid_task_key(task: str) -> bool:
        """task 作为 plan 目录前缀匹配键：拒绝空、NUL、绝对路径与 `..` 段。"""
        if not task or "\x00" in task:
            return False
        if task.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", task):
            return False
        return ".." not in re.split(r"[\\/]", task)

    def _plan_dir_for_task(self, task: str) -> str | None:
        """最长前缀匹配流 plan 目录：task == dir 或 task.startswith(dir + "-" / "/")。
        本仓回执用切片式 task（如 patch-2213-code），需映射回流目录（patch-2213）。"""
        planning = self.root / ".planning"
        try:
            candidates = [path.name for path in planning.iterdir()
                          if path.is_dir() and (path / "task_plan.md").is_file()]
        except OSError:
            return None
        matches = [name for name in candidates
                   if task == name or task.startswith(name + "-")
                   or task.startswith(name + "/")]
        return max(matches, key=len) if matches else None

    def _log_rework_skip(self, task: str, reason: str) -> None:
        if task in self.rework_skipped:
            return
        self.rework_skipped.add(task)
        display = task.replace("\x00", "\\0")
        self.log(f"返工提醒跳过：task={display}（{reason}）")

    def _rework_task_active(self, task: str) -> bool:
        """仅有活跃流 plan 的 task 才做返工提醒：task 按最长前缀映射到
        `.planning/<流>/task_plan.md`，plan 的 Phase 状态未全部 complete 才算活跃。
        无匹配 plan、已收口 plan、非法 task 名与坏 plan 文件都不提醒（S3/A-S5 降噪；
        非法/坏输入记日志一次并跳过，不拖垮整轮）。"""
        if not self._valid_task_key(task):
            self._log_rework_skip(task, "非法 task 名（绝对路径 / .. / NUL）")
            return False
        plan_dir = self._plan_dir_for_task(task)
        if plan_dir is None:
            return False
        plan = self.root / ".planning" / plan_dir / "task_plan.md"
        try:
            text = plan.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self._log_rework_skip(task, f"plan 读取失败：{type(exc).__name__}: {exc}")
            return False
        statuses = _plan_phase_statuses(text)
        return not statuses or any(status != "complete" for status in statuses)

    def _scan_rework(self) -> list[dict]:
        """统计 <crew-dir>/inbox/*.json 的回执 task；同一活跃 task ≥3 条写一次提醒。
        回执按 receipt_id 去重（缺失回退文件名，R3）；只读回执，不叫醒、不改 plan、
        不删文件；同一 guard 进程内每个 task 只记一次日志。task 按最长前缀映射到
        `.planning/<流>/task_plan.md`；无匹配或 plan 已全部 complete 的 task 不计数
        （历史噪声过滤，S3/A-S5）。"""
        counts: dict[str, int] = {}
        seen: set[str] = set()
        try:
            paths = sorted((self.crew_dir / "inbox").glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            receipt_id = data.get("receipt_id")
            receipt_key = (receipt_id.strip()
                           if isinstance(receipt_id, str) and receipt_id.strip() else path.name)
            if receipt_key in seen:
                continue
            seen.add(receipt_key)
            task = data.get("task")
            if isinstance(task, str) and task.strip():
                task_key = task.strip()
                counts[task_key] = counts.get(task_key, 0) + 1
        rework = [{"task": task, "receipts": count}
                  for task, count in sorted(counts.items())
                  if count >= REWORK_RECEIPT_THRESHOLD
                  and self._rework_task_active(task)]
        for row in rework:
            if row["task"] in self.rework_logged:
                continue
            self.rework_logged.add(row["task"])
            self.log(f"返工提醒：task={row['task']} 回执 {row['receipts']} 条，"
                     "建议评估是否触发 rework-reset（需人签）")
        return rework

    def _write_handoff(self, **updates: object) -> None:
        fields = self._read_handoff()
        fields.update({key: str(value).replace("\n", " ") for key, value in updates.items()})
        order = ("phase", "old", "oldPane", "oldTab", "new", "newPane", "newTab",
                 "time", "reason", "result", "error", "successorWasWorking")
        lines = [f"{key}={fields[key]}" for key in order if key in fields]
        lines.extend(f"{key}={value}" for key, value in fields.items() if key not in order)
        atomic_write_text(self.crew_dir / "guard.handoff", "\n".join(lines) + "\n")

    def _notify_handoff(self, title: str, body: str) -> None:
        try:
            self.notify(title, body)
        except Exception as exc:  # noqa: BLE001 - 通知失败不影响值守
            self.log(f"guard 交接通知失败：{exc}")

    def _handoff_fail(self, step: str, detail: str, base: dict[str, str]) -> dict:
        error = f"{step}: {detail}".strip()
        fields = dict(base)
        fields.setdefault("time", utc_now())
        self._write_handoff(**fields, phase="done", result="failed", error=error)
        old = str(base.get("old") or self.helm)
        body = f"{old} 自动交接失败（{step}），旧舵未关闭；请 Captain 人工接手。\n{detail}"
        resources = [
            f"{label} {base[key]}"
            for key, label in (("newPane", "pane"), ("newTab", "tab"))
            if base.get(key)
        ]
        if resources:
            if step in HANDOFF_CREATE_FAILURE_STEPS:
                body += (f"\n已创建资源：{'，'.join(resources)}；"
                         "请人工确认后清理（dispatch.py pane close / tab close）。")
            else:
                body += (f"\n继任资源：{'，'.join(resources)}；"
                         "请保留，人工确认后处置（不要当作孤儿资源）。")
        self._notify_handoff("guard 交接失败", body)
        self.log(f"交接失败（{step}）：{detail}")
        return {"action": "failed", "reason": f"交接失败：{error}", "error": error}

    def _find_agent(self, agents: list[dict], name: str) -> dict | None:
        return next(
            (agent for agent in agents if str(agent.get("name") or "") == name),
            None,
        )

    def _agent_pane(self, agent: dict | None) -> str:
        if not isinstance(agent, dict):
            return ""
        for key in ("pane_id", "pane"):
            value = agent.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _dispatch_registry(self) -> dict:
        try:
            data = json.loads(
                (self.crew_dir / "dispatch-agents.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _registry_pane(info: object) -> str:
        if not isinstance(info, dict):
            return ""
        for key in ("pane", "pane_id"):
            value = info.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    def _resolve_helm_pane(self, agents: list[dict]) -> tuple[str, str] | None:
        """总舵 pane 解析：agent name → dispatch registry（按名/最近 helm 条目反查）。

        名字漂移时以 registry pane 在 agent list 中的实际名字为准；解析不到
        返回 None（fail-safe：本轮不动作、不误交接）。
        """
        agent = self._find_agent(agents, self.helm)
        pane = self._agent_pane(agent)
        if pane:
            return pane, self.helm

        registry = self._dispatch_registry()
        candidates: list[tuple[str, str]] = []
        direct_info = registry.get(self.helm)
        if isinstance(direct_info, dict) and direct_info.get("role") == "helm":
            direct = self._registry_pane(direct_info)
            if direct:
                candidates.append((self.helm, direct))
        helm_entries = []
        for name, info in registry.items():
            if not isinstance(info, dict) or info.get("role") != "helm":
                continue
            entry_pane = self._registry_pane(info)
            if entry_pane:
                helm_entries.append(
                    (str(info.get("startedAt") or ""), str(name), entry_pane))
        for _, name, entry_pane in sorted(helm_entries, reverse=True):
            if all(entry_pane != pane for _, pane in candidates):
                candidates.append((name, entry_pane))

        for name, entry_pane in candidates:
            actual = next(
                (item for item in agents if self._agent_pane(item) == entry_pane),
                None,
            )
            if actual is None:
                continue
            actual_name = str(actual.get("name") or "").strip() or name
            if actual_name != name and not actual_name.startswith(self.helm):
                self.log(
                    f"registry 反查关系不可解释：条目 {name} 指向 pane {entry_pane}，"
                    f"实际 agent {actual_name}；fail-safe 不动作"
                )
                continue
            if actual_name != self.helm:
                self.log(
                    f"helm 名漂移：guard 记录 {self.helm}，实际 agent "
                    f"{actual_name}（pane {entry_pane}，来源 dispatch registry）"
                )
            return entry_pane, actual_name
        self.log(
            f"ctx 检查跳过：未找到总舵 agent {self.helm}"
            "（dispatch registry 未解析到可用 pane）"
        )
        return None

    def _ctx_for_helm(self, agents: list[dict]) -> tuple[float, str, str] | None:
        resolved = self._resolve_helm_pane(agents)
        if resolved is None:
            return None
        pane, helm_name = resolved
        try:
            text = self.pane_read(pane, 80)
        except Exception as exc:  # noqa: BLE001 - 读 pane 失败不动作
            self.log(f"pane read 失败（{pane}）：{type(exc).__name__}: {exc}")
            return None
        ctx_k = parse_ctx_k(text)
        if ctx_k is None:
            self.log(f"ctx 解析失败（{pane}），本轮不动作")
            return None
        return ctx_k, pane, helm_name

    def _helm_runtime(self) -> str:
        try:
            registry = json.loads(
                (self.crew_dir / "dispatch-agents.json").read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            return ""
        info = registry.get(self.helm) if isinstance(registry, dict) else None
        runtime = info.get("runtime") if isinstance(info, dict) else None
        return runtime if runtime in ("qoder", "opencode") else ""

    def _successor_name(self, agents: list[dict]) -> str:
        names = {str(agent.get("name") or "") for agent in agents}
        names.add(self.helm)
        try:
            registry = json.loads(
                (self.crew_dir / "dispatch-agents.json").read_text(encoding="utf-8")
            )
            if isinstance(registry, dict):
                names.update(str(name) for name in registry)
        except (OSError, ValueError):
            pass
        number = 2
        candidate = f"{self.helm}-{number}"
        while candidate in names:
            number += 1
            candidate = f"{self.helm}-{number}"
        return candidate

    @staticmethod
    def _extract_id(output: str, keys: tuple[str, ...]) -> str:
        try:
            data = json.loads(output)
        except ValueError:
            return ""

        def walk(value: object) -> str:
            if isinstance(value, dict):
                for key in keys:
                    item = value.get(key)
                    if isinstance(item, str) and ":" in item:
                        return item
                for item in value.values():
                    found = walk(item)
                    if found:
                        return found
            elif isinstance(value, list):
                for item in value:
                    found = walk(item)
                    if found:
                        return found
            return ""

        return walk(data)

    @staticmethod
    def _extract_pane(output: str) -> str:
        return Guard._extract_id(output, ("pane_id", "pane"))

    @staticmethod
    def _extract_tab(output: str) -> str:
        return Guard._extract_id(output, ("tab_id", "tab"))

    def _rotate_progress_before_handoff(self) -> None:
        """交接前 progress 归档（`--rotate --apply --keep ROTATE_KEEP`）：
        成功记日志；失败/异常只告警，不阻断交接、不回落 split。"""
        try:
            if self.run_rotate is not None:
                code, output = self.run_rotate(self.root)
            else:
                code, output = run_plan_status_args(
                    self.root,
                    ["--rotate", "--apply", "--keep", str(ROTATE_KEEP)],
                )
        except Exception as exc:  # noqa: BLE001 - 归档失败不得中断交接
            self.log(f"交接前 progress rotate 异常：{type(exc).__name__}: {exc}（继续交接）")
            return
        detail = " / ".join(line.strip() for line in output.splitlines() if line.strip())
        if code == 0:
            if "无需轮转" in output:
                self.log(f"交接前 progress 无需轮转（零写入）：{detail or 'ok'}")
            else:
                self.log(f"交接前 progress 已归档（rotate --apply --keep {ROTATE_KEEP}）："
                         f"{detail or 'ok'}")
        else:
            self.log(f"交接前 progress rotate 失败（退出码 {code}，继续交接）：{detail}")

    def _ensure_handoff_tab_single_pane(self, new_tab: str, new_pane: str) -> None:
        """FB-038：`tab create` 后校验新 tab pane 数=1，多出的空 pane 关闭。

        只关「非 new_pane、无 agent 字段、状态非 working/blocked」的明确空 pane；
        pane list 取数/解析失败、多余 pane 非空或关闭失败一律只记 guard.log 告警，
        不中断交接（fail-safe：宁可留告警，不误杀）。
        """
        try:
            panes = self.list_panes()
        except Exception as exc:  # noqa: BLE001 - 校验失败不得中断交接
            self.log(f"交接 tab pane 数校验失败：{type(exc).__name__}: {exc}"
                     f"（继续交接，请人工核查 tab {new_tab}）")
            return
        tab_panes = [
            pane for pane in panes
            if isinstance(pane, dict) and str(pane.get("tab_id") or "") == new_tab
        ]
        if not tab_panes:
            self.log(f"交接 tab pane 数校验：pane list 未返回 tab {new_tab} 的 pane"
                     "（继续交接，请人工核查）")
            return
        pane_ids_in_tab = {str(pane.get("pane_id") or "") for pane in tab_panes}
        if new_pane not in pane_ids_in_tab:
            # 返回的 new_pane 与 pane list 对不上（解析漂移）：无法判断哪个是继任 pane，
            # 一律不关闭，避免误杀唯一 pane。
            self.log(f"交接 tab pane 数校验：pane list 未包含新 pane {new_pane}"
                     f"（tab {new_tab}），仅告警不关闭（继续交接，请人工核查）")
            return
        extras = [
            pane for pane in tab_panes
            if str(pane.get("pane_id") or "") != new_pane
        ]
        if not extras:
            return
        for pane in extras:
            pane_id = str(pane.get("pane_id") or "?")
            status = str(pane.get("agent_status") or "")
            if pane.get("agent") or status in ("working", "blocked"):
                self.log(f"交接 tab 多余 pane {pane_id} 非空或 {status or '有 agent'}，"
                         f"未关闭（请人工核查 tab {new_tab}）")
                continue
            code, output, error = self.dispatch(
                self.root, ["pane", "close", "--pane", pane_id]
            )
            if code == 0:
                self.log(f"交接 tab 多余 pane 已关闭：{pane_id}（tab {new_tab}）")
            else:
                detail = (error or output).strip().splitlines()
                reason = detail[0] if detail else f"退出码 {code}"
                self.log(f"交接 tab 多余 pane {pane_id} 关闭失败（退出码 {code}）：{reason}"
                         f"（继续交接，请人工核查 tab {new_tab}）")

    def _start_handoff(self, agents: list[dict], ctx_k: float, old_pane: str) -> dict:
        if self.handoff_attempted:
            return {"action": "wait", "reason": "交接已尝试，等待继任首报"}
        self.handoff_attempted = True
        self.handoff_failed_noted = False
        self.handoff_wait_rounds = 0
        self.handoff_close_rounds = 0
        pct = ctx_k / 1000.0
        helm_cfg = load_config(self.root)["helm"]
        reason = f"ctx {ctx_k:g}k/1M ≥ {helm_cfg['ctxHandoff']:.0%} 交接阈值"
        old_agent = self._find_agent(agents, self.helm)
        old_tab = str(old_agent.get("tab_id") or "") if old_agent else ""
        workspace = str(old_agent.get("workspace_id") or "") if old_agent else ""
        if not workspace and ":" in old_pane:
            workspace = old_pane.split(":", 1)[0]
        base = {
            "old": self.helm,
            "oldPane": old_pane,
            "oldTab": old_tab,
            "time": utc_now(),
            "reason": reason,
        }
        if not workspace:
            return self._handoff_fail(
                "tab create",
                "无法确定 workspace（agent 无 workspace_id 且旧 pane 无冒号前缀）",
                base,
            )
        successor = self._successor_name(agents)
        tab_label = helm_cfg["tabLabel"]

        # 交接前置：先归档 progress（失败只告警），再做任何 tab/pane 变更。
        self._rotate_progress_before_handoff()
        code, output, error = self.dispatch(
            self.root,
            ["tab", "create", "--workspace", workspace, "--cwd", str(self.root),
             "--label", tab_label, "--no-focus"],
        )
        if code != 0:
            return self._handoff_fail("tab create", error or output, base)
        new_pane = self._extract_pane(output)
        new_tab = self._extract_tab(output)
        base.update({"new": successor, "newPane": new_pane, "newTab": new_tab})
        if not new_pane:
            return self._handoff_fail("tab create", f"未返回 pane：{output.strip()}", base)
        if not new_tab:
            return self._handoff_fail("tab create", f"未返回 tab：{output.strip()}", base)

        # FB-038：tab create 后确认新 tab 只有一个 pane（多出空 pane 关闭，失败只告警）。
        self._ensure_handoff_tab_single_pane(new_tab, new_pane)

        start_args = ["start", "--role", "helm", "--pane", new_pane,
                      "--permission-mode", "bypass_permissions"]
        runtime = self._helm_runtime()
        if runtime:
            start_args.extend(["--runtime", runtime])
        start_args.extend(["--name", successor, "--json"])
        code, output, error = self.dispatch(self.root, start_args)
        if code != 0:
            return self._handoff_fail("dispatch start", error or output, base)

        portfolio = load_config(self.root)["portfolioDir"]
        message = (
            f"交接接手：原总舵 {self.helm} 的 ctx 已达 {ctx_k:g}k/1M（{pct:.0%}）。\n"
            "按「交接读取范围（硬）」读取：`python3 scripts/plan-status.py --brief` 一屏 + "
            f"活跃流 plan 的 Next Step/Status（grep）+ `python3 scripts/plan-status.py --handover` 摘要 + "
            f"{portfolio}/feedback.md 待复盘条目（grep）+ `<crew-dir>/guard.handoff`。\n"
            "禁整读 看板/progress/task_plan/feedback/findings/历史流 plan/共识；"
            "单文件 >20KB 不整读；看板仅按关键词 grep。\n"
            "交接必读 ≤32KB（含 `--handover` 输出与必读文件），超阈值先摘要/grep、"
            "写明只读段再定向补读。\n"
            "完成接手首报（当前进度/在跑 agent/待办/下一步），首报前不接新活；"
            "首报后 guard 会确认并关闭旧舵 pane 与旧 tab。"
        )
        code, output, error = self.dispatch(
            self.root, ["prompt", "--agent", successor, "--message", message]
        )
        if code != 0:
            return self._handoff_fail("dispatch prompt", error or output, base)

        self._write_handoff(
            **base, phase="started", result="ok", successorWasWorking="0",
        )
        self._notify_handoff(
            "guard 交接",
            f"交接已启动：继任总舵 {successor}（pane {new_pane}，tab {new_tab}），"
            f"旧舵 {self.helm} 待首报后关闭；请 Captain 知悉。",
        )
        self.log(f"交接已启动：{self.helm} → {successor}（pane {new_pane}，tab {new_tab}）")
        return {"action": "started", "reason": reason, "new": successor,
                "newPane": new_pane, "newTab": new_tab}

    def _continue_handoff(self, fields: dict[str, str], agents: list[dict]) -> dict:
        successor = fields.get("new", "")
        old_pane = fields.get("oldPane", "")
        if not successor:
            return self._handoff_fail("state", "guard.handoff 缺 new", fields)
        # 旧舵身份一律取 handoff old：guard 重启后 self.helm 可能已是继任。
        old_name = fields.get("old") or self.helm
        base = {
            "old": old_name,
            "oldPane": old_pane,
            "oldTab": fields.get("oldTab", ""),
            "new": successor,
            "newPane": fields.get("newPane", ""),
            "newTab": fields.get("newTab", ""),
            "time": utc_now(),
            "reason": fields.get("reason", ""),
        }

        agent = self._find_agent(agents, successor)
        status = str(agent.get("agent_status") or "") if agent else ""
        if status == "working" and fields.get("successorWasWorking") != "1":
            self._write_handoff(successorWasWorking="1")
            fields["successorWasWorking"] = "1"
            self.log(f"继任 {successor} 正在接手")
        ready = status == "done" or (
            status == "idle" and fields.get("successorWasWorking") == "1"
        )
        if not ready:
            # 有界等待：继任缺失/blocked/unknown，或 idle 但未观测到首报
            # （successorWasWorking≠1，常见于 guard 停机窗口内完成首报）连续 N 轮
            # → failed 通知人工。
            if agent is None or status in ("idle", "blocked", "unknown"):
                self.handoff_wait_rounds += 1
                state_text = "不存在" if agent is None else status
                if status == "idle":
                    state_text = "idle（successorWasWorking≠1）"
                if self.handoff_wait_rounds >= HANDOFF_STALL_ROUNDS:
                    return self._handoff_fail(
                        "首报等待",
                        f"继任 {successor} {state_text}"
                        f"（连续 {self.handoff_wait_rounds} 轮）",
                        base,
                    )
                self.log(
                    f"继任 {successor} {state_text}，等待首报"
                    f"（{self.handoff_wait_rounds}/{HANDOFF_STALL_ROUNDS} 轮）"
                )
            return {"action": "wait", "reason": f"等待继任 {successor} 首报"}

        old_agent = self._find_agent(agents, old_name)
        old_status = str(old_agent.get("agent_status") or "") if old_agent else ""
        close_note = f"旧舵 {old_name} 仍在运行（{old_status or 'working'}），未关闭"
        pane_closed = False
        if old_pane and old_status in ("", "idle", "done", "unknown"):
            code, output, error = self.dispatch(
                self.root, ["pane", "close", "--pane", old_pane]
            )
            if code != 0:
                return self._handoff_fail("pane close", error or output, base)
            pane_closed = True
            close_note = "旧舵 pane 已关闭"
        elif not old_pane:
            close_note = "旧舵 pane 未知，未关闭"
        else:
            # 旧舵仍在 working/blocked：不杀，有界等待后转 failed 交人工。
            self.handoff_close_rounds += 1
            if self.handoff_close_rounds >= HANDOFF_STALL_ROUNDS:
                return self._handoff_fail(
                    "旧舵收口",
                    f"旧舵 {old_name} 连续 {self.handoff_close_rounds} 轮"
                    f"仍为 {old_status}，不自动关闭",
                    base,
                )
            self.log(
                f"{close_note}（{self.handoff_close_rounds}/{HANDOFF_STALL_ROUNDS} 轮）"
            )
            return {"action": "wait", "reason": close_note}

        old_tab = fields.get("oldTab", "")
        if pane_closed and old_tab:
            remaining = None
            try:
                remaining = [
                    pane for pane in self.list_panes()
                    if isinstance(pane, dict)
                    and str(pane.get("tab_id") or "") == old_tab
                ]
            except Exception as exc:  # noqa: BLE001 - 无法确认剩余 pane 时不关旧 tab
                self.log(
                    f"旧 tab {old_tab} 剩余 pane 检查失败：{type(exc).__name__}: {exc}"
                )
            if remaining is None:
                close_note += f"；旧 tab {old_tab} 未关闭（无法确认剩余 pane）"
            elif remaining:
                close_note += (
                    f"；旧 tab {old_tab} 仍有 {len(remaining)} 个 pane，未关闭"
                )
            else:
                code, output, error = self.dispatch(
                    self.root, ["tab", "close", "--tab", old_tab]
                )
                if code != 0:
                    detail = (error or output).strip()
                    close_note += f"；旧 tab {old_tab} 关闭失败（{detail}），待人工或 pane-audit 兜底"
                    self.log(f"旧 tab {old_tab} 关闭失败：{detail}")
                else:
                    close_note += f"；旧 tab {old_tab} 已关闭"

        self._write_handoff(
            **base, phase="done", result="ok", successorWasWorking="1",
        )
        self.helm = successor
        # 一次交接已完成；新 helm 后续再达阈值时允许再次交接（防同进程永久短路）。
        self.handoff_attempted = False
        self.handoff_wait_rounds = 0
        self.handoff_close_rounds = 0
        self._notify_handoff(
            "guard 交接",
            f"交接完成：{base['old']} → {successor}；{close_note}；请 Captain 知悉。",
        )
        self.log(f"交接完成：{base['old']} → {successor}；{close_note}")
        return {"action": "done", "reason": f"交接完成（{close_note}）",
                "new": successor, "newPane": base["newPane"]}

    def _check_handoff(self, agents: list[dict]) -> dict:
        fields = self._read_handoff()
        phase = fields.get("phase")
        if phase == "started":
            return self._continue_handoff(fields, agents)
        if phase == "done" and fields.get("result") == "failed":
            # 交接失败不自动重试（防循环）；失败时已通知，等待人工处置。
            if not self.handoff_failed_noted:
                self.handoff_failed_noted = True
                self.log(
                    f"上次交接失败（{fields.get('error', '')}），"
                    "不再自动重试，等待人工处置"
                )
            return {"action": "none"}
        if phase == "done":
            # done 不得永久短路：交接完成后继续监控继任总舵（handoff new）。
            successor = fields.get("new", "")
            if successor and successor != self.helm:
                self.log(f"交接已完成，改监控继任总舵 {successor}")
                self.helm = successor

        ctx = self._ctx_for_helm(agents)
        if ctx is None:
            return {"action": "none"}
        ctx_k, pane, helm_name = ctx
        if helm_name != self.helm:
            self.helm = helm_name
        ratio = ctx_k / 1000.0
        cfg = load_config(self.root)["helm"]
        if ratio >= cfg["ctxHandoff"]:
            return self._start_handoff(agents, ctx_k, pane)
        if ratio >= cfg["ctxWarn"]:
            if phase == "warn":
                return {"action": "none"}
            reason = f"ctx {ctx_k:g}k/1M ≥ {cfg['ctxWarn']:.0%} 预警阈值"
            helm_agent = self._find_agent(agents, self.helm)
            old_tab = str(helm_agent.get("tab_id") or "") if helm_agent else ""
            self._write_handoff(
                phase="warn", old=self.helm, oldPane=pane, oldTab=old_tab,
                time=utc_now(), reason=reason,
            )
            self._notify_handoff(
                "guard 交接预警",
                f"总舵 {self.helm} ctx 已达 {ctx_k:g}k/1M（{ratio:.0%}），该准备交接。",
            )
            self.log(f"交接预警：{self.helm} ctx {ctx_k:g}k/1M")
            return {"action": "warn", "reason": reason}
        return {"action": "none"}

    def _retro_due(self, running: list[dict], handoff: dict) -> str:
        """返回周复盘提醒文案；不满足（未到期/有在跑/交接中/同日已提醒）返回空串。"""
        if running or handoff.get("action") in ("started", "wait"):
            return ""
        config = load_config(self.root)
        retro = config["retro"]
        if not retro["enabled"]:
            return ""
        portfolio_rel = str(config["portfolioDir"])
        portfolio = self.root / portfolio_rel
        open_count = parse_feedback_open(portfolio / "feedback.md", self.log)
        if not open_count:
            return ""
        today = datetime.fromtimestamp(self.clock()).date()
        latest = latest_retro_date(portfolio)
        if latest is not None and (today - latest).days <= retro["periodDays"]:
            return ""
        marker = self.crew_dir / RETRO_MARKER_NAME
        try:
            last = marker.read_text(encoding="utf-8").strip()
        except OSError:
            last = ""
        except ValueError as exc:
            self.log(f"retro.wake 读取失败，跳过复盘提醒：{exc}")
            return ""
        if last == today.isoformat():
            return ""
        latest_text = latest.strftime("%Y%m%d") if latest else "无"
        report = f"{portfolio_rel}/retro-{today.strftime('%Y%m%d')}.md"
        return (f"周复盘到期：feedback 待复盘 {open_count} 条，最近复盘 {latest_text}；"
                f"请按 helm 模板「复盘」执行，报告 {report}。")

    def _mark_retro_wake(self) -> None:
        today = datetime.fromtimestamp(self.clock()).date()
        try:
            atomic_write_text(
                self.crew_dir / RETRO_MARKER_NAME, today.isoformat() + "\n")
        except OSError as exc:
            self.log(f"retro.wake 写入失败：{exc}")

    def _roots(self) -> set[Path]:
        if self.project_roots is not None:
            return {Path(root).expanduser().resolve() for root in self.project_roots}
        if self.roots_cache is None:
            self.roots_cache = {self.root.resolve()} | git_worktree_roots(self.root)
        return self.roots_cache

    def _in_project(self, agent: dict) -> bool:
        """只统计本仓 / linked worktree 的 agent；cwd 缺失或不可解析时沿用旧口径包含。"""
        cwd = agent.get("cwd")
        if not isinstance(cwd, str) or not cwd.strip():
            return True
        try:
            path = Path(cwd).expanduser().resolve()
        except (OSError, RuntimeError):
            return True
        return any(path == root or root in path.parents for root in self._roots())

    def _claude_runs(self) -> list[dict]:
        """读 `<crew-dir>/runs/*.json` 的存活注册（FB-040）。

        `crew-claude.py run` 用普通子进程跑 claude，不在 herdr 注册；这里按 os.kill
        判活计入在跑 worker。死 pid 顺手删除；坏 JSON / 缺 pid 忽略并记一行日志；
        超出 pid_t 范围的 pid 视为无效并删除（不因 OverflowError 中断整轮）。
        存活注册再做进程身份/年龄核验（互检建议-2）：cmdline 可读且不含 crew-claude
        （含空串，如僵尸进程）视为 pid 复用，删除注册并按死处理；cmdline 不可读时
        started_at 超龄（>24h）忽略但保留取证，started_at 缺失/不可解析按存活计入
        （fail-safe，宁可误等不误叫醒）。
        """
        runs_dir = self.crew_dir / "runs"
        try:
            paths = sorted(runs_dir.glob("*.json"))
        except OSError:
            return []
        alive: list[dict] = []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, ValueError) as exc:
                self.log(f"claude 运行注册读取失败，忽略：{path.name}"
                         f"（{type(exc).__name__}: {exc}）")
                continue
            pid = data.get("pid") if isinstance(data, dict) else None
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
                self.log(f"claude 运行注册缺少有效 pid，忽略：{path.name}")
                continue
            if pid > PID_MAX_VALID:
                self.log(f"claude 运行注册 pid={pid} 为无效/超范围 pid，"
                         f"忽略并删除：{path.name}")
                try:
                    path.unlink()
                except OSError as exc:
                    self.log(f"claude 运行注册删除失败：{path.name}（{exc}）")
                continue
            if not pid_alive(pid):
                self.log(f"claude 运行注册 pid={pid} 已死，忽略：{path.name}")
                try:
                    path.unlink()
                except OSError as exc:
                    self.log(f"claude 运行注册删除失败：{path.name}（{exc}）")
                continue
            try:
                cmdline: str | None = proc_cmdline(pid)
            except OSError:
                cmdline = None
            if cmdline is not None and CLAUDE_PROCESS_MARKER not in cmdline:
                self.log(
                    f"claude 运行注册 pid={pid} 的进程 cmdline 不含 "
                    f"{CLAUDE_PROCESS_MARKER}（pid 复用/非 crew-claude 进程），"
                    f"按死处理并删除：{path.name}")
                try:
                    path.unlink()
                except OSError as exc:
                    self.log(f"claude 运行注册删除失败：{path.name}（{exc}）")
                continue
            if cmdline is None:
                age = registry_age_seconds(
                    str(data.get("started_at") or ""), self.clock())
                if age is not None and age > CLAUDE_RUN_MAX_AGE_SECONDS:
                    self.log(
                        f"claude 运行注册 pid={pid} 超龄（{age / 3600:.1f}h）且无法"
                        f"核验进程，忽略：{path.name}")
                    continue
                if age is None:
                    self.log(
                        f"claude 运行注册 pid={pid} 无法读取 cmdline 且缺 started_at"
                        f"（或不可解析），按存活计入（fail-safe）：{path.name}")
            alive.append({
                "pid": pid,
                "role": str(data.get("role") or ""),
                "started_at": str(data.get("started_at") or ""),
                "out_dir": str(data.get("out_dir") or ""),
            })
        return alive

    def _excluded(self, agent: dict) -> bool:
        name = str(agent.get("name") or "")
        return name == self.helm or name.startswith("Captain")

    @staticmethod
    def _blocked_key(agent: dict) -> str:
        """blocked 年龄身份：name，缺省 pane_id；两者皆空返回空串（不计数）。"""
        name = str(agent.get("name") or "").strip()
        if name:
            return name
        return str(agent.get("pane_id") or agent.get("pane") or "").strip()

    @staticmethod
    def _blocked_label(agent: dict) -> str:
        return Guard._blocked_key(agent) or "?"

    def _track_blocked(self, now: float, blocked: list[dict]) -> dict[str, float]:
        """更新 blocked 年龄表；agent 不再 blocked 或消失时清除（含上次叫醒时间）。"""
        active: dict[str, float] = {}
        for agent in blocked:
            key = self._blocked_key(agent)
            if key:
                active[key] = self.blocked_since.get(key, now)
        for key in list(self.blocked_since):
            if key not in active:
                del self.blocked_since[key]
                self.blocked_last_wake.pop(key, None)
        self.blocked_since.update(active)
        return active

    def _blocked_minutes(self, now: float, agent: dict,
                         since_map: dict[str, float]) -> int:
        key = self._blocked_key(agent)
        since = since_map.get(key, now) if key else now
        return max(0, int((now - since) // 60))

    def _blocked_ages(self, now: float, blocked: list[dict],
                      since_map: dict[str, float]) -> dict[str, int]:
        return {self._blocked_label(agent): self._blocked_minutes(now, agent, since_map)
                for agent in blocked}

    def _blocked_due(self, now: float, blocked: list[dict],
                     repeat_seconds: int) -> tuple[list[dict], bool]:
        """返回（到期叫醒的 agent，是否含重复叫醒）；重复叫醒不受全局冷却压制。"""
        due: list[dict] = []
        repeat_due = False
        for agent in blocked:
            key = self._blocked_key(agent)
            last = self.blocked_last_wake.get(key) if key else None
            if last is None:
                due.append(agent)
            elif now - last >= repeat_seconds:
                due.append(agent)
                repeat_due = True
        return due, repeat_due

    def _blocked_wake_lines(self, now: float, blocked: list[dict],
                            since_map: dict[str, float],
                            grace_seconds: int) -> list[str]:
        lines: list[str] = []
        for agent in blocked:
            key = self._blocked_key(agent)
            since = since_map.get(key, now) if key else now
            text = (f"- {self._blocked_label(agent)}："
                    f"已 blocked {self._blocked_minutes(now, agent, since_map)} 分钟")
            if now - since >= grace_seconds:
                text += ("（按 R6 处置（re-task / close / escalate），"
                         "应关闭工位或升级 Captain；guard 不自动关闭/杀 agent）")
            lines.append(text)
        return lines

    def _periodic_write(self, now: float) -> None:
        if self.last_write_at is not None and now - self.last_write_at < WRITE_INTERVAL_SECONDS:
            return
        self.last_write_at = now
        try:
            code, output = self.run_status(self.root, "--write")
        except Exception as exc:  # noqa: BLE001 - 值守不能因刷新失败崩溃
            self.log(f"plan-status --write 失败：{type(exc).__name__}: {exc}")
            return
        if code != 0:
            self.log(f"plan-status --write 退出码 {code}：{output}")
        elif "建议交接时 --rotate" in output:
            self.log(f"plan-status --write 体量告警：{output}")

    def _freshness_note(self) -> str:
        try:
            code, output = self.run_status(self.root, "--check-fresh")
        except Exception as exc:  # noqa: BLE001
            return f"--check-fresh 失败：{type(exc).__name__}: {exc}"
        state = "新鲜" if code == 0 else "已过期"
        detail = " / ".join(line for line in output.splitlines()[:3] if line.strip())
        return f"看板{state}（--check-fresh）{('：' + detail) if detail else ''}"

    def _write_wake_file(self, message: str) -> None:
        atomic_write_text(
            self.crew_dir / WAKE_FILE_NAME,
            f"generated_at: {utc_now()}\n{message}\n",
        )

    def _consume_stale_wake(self, now: float) -> None:
        """每轮开始先消费遗留 guard.wake：过期或不可解析则删除并记日志，新鲜保留。"""
        path = self.crew_dir / WAKE_FILE_NAME
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except UnicodeDecodeError:
            text = ""  # 编码损坏按不可解析处理（fail-safe 过期）
        except OSError as exc:
            self.log(f"guard.wake 读取失败，跳过过期检查：{exc}")
            return
        generated = parse_wake_generated_at(text)
        if not wake_expired(generated, now):
            return
        stamp = generated.isoformat().replace("+00:00", "Z") if generated else "无法解析"
        try:
            path.unlink()
        except FileNotFoundError:
            return
        except OSError as exc:
            self.log(f"guard.wake 过期但删除失败：{exc}")
            return
        self.log(f"guard.wake 已过期/已忽略（generated_at={stamp}，"
                 f"阈值 {DEFAULT_WAKE_TTL_SECONDS}s）：已删除")

    def _pane_focus_state(self, agent: dict) -> tuple[bool | None, str]:
        """目标 agent 的 pane 是否聚焦（T37）；None=无法判定，按 fail-safe 不注入。"""
        pane_id = str(agent.get("pane_id") or "").strip()
        if not pane_id:
            return None, "agent list 缺 pane_id"
        try:
            panes = self.list_panes()
        except Exception as exc:  # noqa: BLE001 - pane list 故障按无法判定处理
            return None, f"herdr pane list 不可用：{type(exc).__name__}: {exc}"
        pane = next((item for item in panes
                     if isinstance(item, dict)
                     and str(item.get("pane_id") or "") == pane_id), None)
        if pane is None:
            return None, f"pane list 未找到 {pane_id}"
        focused = pane.get("focused")
        if not isinstance(focused, bool):
            return None, f"pane {pane_id} 缺 focused 字段"
        return focused, ""

    def _wake(self, message: str, agents: list[dict]) -> str:
        helm_agent = next(
            (agent for agent in agents if str(agent.get("name") or "") == self.helm),
            None,
        )
        if helm_agent is not None:
            focused, focus_reason = self._pane_focus_state(helm_agent)
            if focused is not False:
                self._write_wake_file(message)
                try:
                    self.notify("guard 叫醒", message)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"guard 通知失败：{exc}")
                state = ("总舵 pane 聚焦" if focused
                         else f"聚焦状态无法判定（{focus_reason}）")
                self.log(f"叫醒（{state}，写 guard.wake）：{message.splitlines()[0]}")
                return "wake-file"

        try:
            prompted = bool(self.prompt(self.helm, message))
        except Exception as exc:  # noqa: BLE001
            prompted = False
            self.log(f"herdr agent prompt 异常：{exc}")
        if prompted:
            self.log(f"叫醒（herdr agent prompt {self.helm}）：{message.splitlines()[0]}")
            return "prompt"

        self._write_wake_file(message)
        try:
            self.notify("guard 叫醒", message)
        except Exception as exc:  # noqa: BLE001
            self.log(f"guard 通知失败：{exc}")
        self.log(f"prompt 失败，回退 guard.wake：{message.splitlines()[0]}")
        return "wake-file"

    def run_once(self) -> dict:
        now = self.clock()
        rework = self._scan_rework()
        self._consume_stale_wake(now)
        self._periodic_write(now)

        try:
            agents = self.list_agents()
        except Exception as exc:  # noqa: BLE001 - Herdr 故障时不误判“无在跑”
            self.log(f"herdr agent list 失败：{type(exc).__name__}: {exc}")
            return {"error": str(exc), "woke": False, "rework": rework}

        workers = [
            agent for agent in agents
            if not self._excluded(agent) and self._in_project(agent)
        ]
        running = [
            agent for agent in workers if str(agent.get("agent_status")) == "working"
        ]
        blocked = [
            agent for agent in workers
            if str(agent.get("agent_status")) == "blocked" and not is_probe_agent(agent)
        ]
        claude_runs = self._claude_runs()
        # 年龄表先于 handoff 分支更新：交接期间 blocked 时长继续累计。
        blocked_since = self._track_blocked(now, blocked)
        pending, residual = scan_todos(self.root)

        for todo in residual:
            self.log(f"已完成 Phase 残留 [ ]（仅记日志）：{todo.label}")

        current_working = len(running) + len(claude_runs)
        prev_working = self.prev_working
        # worker 从 working 全部转 idle 且仍有待办：立即叫醒，不等连续 2 轮
        fast_wake = prev_working > 0 and current_working == 0 and bool(pending)
        self.prev_working = current_working

        condition = bool(pending) and not running and not claude_runs
        if condition:
            self.consecutive += 1
        else:
            self.consecutive = 0

        reason = ""
        if blocked:
            names = ", ".join(
                str(agent.get("name") or agent.get("pane_id") or "?") for agent in blocked
            )
            reason = f"worker 卡住（blocked）：{names}"
        elif condition and fast_wake:
            reason = (
                f"有待办 {len(pending)} 条且无在跑 worker"
                f"（上一轮 {prev_working} 个 working 已转 idle）"
            )
        elif condition and self.consecutive >= 2:
            reason = f"有待办 {len(pending)} 条且无在跑 worker（连续 {self.consecutive} 轮）"

        plan_path = pending[0].plan if pending else ""
        if plan_path and not Path(plan_path).is_absolute():
            plan_path = str((self.root / plan_path).resolve())
        result = {
            "pending": len(pending),
            "residual": len(residual),
            "running": len(running),
            "blocked": len(blocked),
            "blockedAges": self._blocked_ages(now, blocked, blocked_since),
            "consecutive": self.consecutive,
            "woke": False,
            "reason": reason,
            "planPath": plan_path,
            "rework": rework,
            "workers": [
                {"name": str(agent.get("name") or ""),
                 "status": str(agent.get("agent_status") or "")}
                for agent in workers
            ],
            "claudeRuns": claude_runs,
        }
        try:
            handoff = self._check_handoff(agents)
        except Exception as exc:  # noqa: BLE001 - 交接检测失败不终止值守
            self.log(f"交接检测异常：{type(exc).__name__}: {exc}")
            handoff = {"action": "none"}
        result["handoff"] = handoff
        if handoff.get("action") in ("started", "wait", "done"):
            result["reason"] = handoff.get("reason", "")
            return result
        if not blocked:
            retro_reason = self._retro_due(running, handoff)
            if retro_reason:
                result["reason"] = retro_reason
                result["retro"] = True
                woke = False
                try:
                    self._wake(retro_reason, agents)
                    woke = True
                except Exception as exc:  # noqa: BLE001
                    self.log(f"复盘叫醒失败：{type(exc).__name__}: {exc}")
                if woke:
                    self._mark_retro_wake()
                    self.last_wake_at = now
                    result["woke"] = True
                return result
        if not reason:
            return result
        guard_cfg = load_config(self.root)["guard"]
        if blocked:
            due, repeat_due = self._blocked_due(
                now, blocked, guard_cfg["blockedRepeatMinutes"] * 60)
            if not due:
                self.log(f"blocked 未到重复叫醒间隔，跳过叫醒：{reason}")
                return result
            # 首次叫醒受全局冷却；重复叫醒按每 agent 自限速，不受全局冷却压制。
            if (not repeat_due and self.last_wake_at is not None
                    and now - self.last_wake_at < COOLDOWN_SECONDS):
                self.log(f"冷却中，跳过叫醒：{reason}")
                return result
        elif self.last_wake_at is not None and now - self.last_wake_at < COOLDOWN_SECONDS:
            self.log(f"冷却中，跳过叫醒：{reason}")
            return result

        lines = [f"guard 叫醒：{reason}"]
        if blocked:
            lines.append("已 blocked 时长：")
            lines.extend(self._blocked_wake_lines(
                now, blocked, blocked_since,
                guard_cfg["blockedGraceMinutes"] * 60))
        if pending:
            lines.append(f"待办（前 {min(5, len(pending))} 条）：")
            lines.extend(f"- {todo.label}" for todo in pending[:5])
        lines.append(self._freshness_note())
        lines.append("请接手编排。")
        message = "\n".join(lines)
        try:
            self._wake(message, agents)
        except Exception as exc:  # noqa: BLE001
            self.log(f"叫醒失败：{type(exc).__name__}: {exc}")
        self.last_wake_at = now
        if blocked:
            for agent in blocked:
                key = self._blocked_key(agent)
                if key:
                    self.blocked_last_wake[key] = now
        result["woke"] = True
        return result

    def run_forever(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001 - 单轮异常不终止值守
                self.log(f"轮次异常：{type(exc).__name__}: {exc}")
            time.sleep(self.interval)


def build_parser() -> argparse.ArgumentParser:
    epilog = """\
叫醒口径：有待办 + 无在跑 worker 连续 2 轮；若上一轮有 working、本轮全部转 idle
则立即叫醒（仍受 10 分钟冷却）；blocked 单独叫醒；
在跑 worker = Herdr agent_status == working + `<crew-dir>/runs/` 存活注册
（`crew-claude.py run` 写入；死 pid/坏 JSON/缺 pid 忽略并记日志；存活注册需进程
身份/年龄核验，pid 复用按死处理并删除注册）；
排除总舵自身与 Captain*（按 agent 名）；blocked 判定排除测试探针（agent 名
s9-/probe-/test- 前缀，或 cwd 在 /tmp 下），真实 blocked 仍叫醒。cwd 隔离：只统计
cwd 在本仓或 linked worktree（git worktree list --porcelain）内的 agent，外部项目
忽略；cwd 缺失/不可解析时沿用旧口径包含。优先 herdr agent prompt <helm>；总舵 pane
聚焦时写 <crew-dir>/guard.wake + 通知。叫醒时执行 plan-status.py --check-fresh；
每 5 分钟顺带执行 plan-status.py --write。正常零 stdout，事件写 <crew-dir>/guard.log。

guard.wake 过期：首行 `generated_at: <ISO8601 Z>`（兼容旧格式裸 ISO 首行）；
超过 7200s 或首行无法解析的遗留 wake 在每轮开始时删除并记 guard.log（fail-safe），
新鲜文件保留不动；wake 读写异常不中断值守。

blocked 年龄与重复叫醒：按 agent 身份（name，缺省 pane_id）记录首次 blocked 时间；
首次叫醒沿用 10 分钟全局冷却；仍 blocked 且距该 agent 上次 blocked 叫醒
≥ guard.blockedRepeatMinutes（默认 15，夹取 1–240）再叫醒，不受全局冷却压制；
累计 ≥ guard.blockedGraceMinutes（默认 30，夹取 1–240）时叫醒消息提示
「按 R6 处置（re-task / close / escalate），应关闭工位或升级 Captain」；
guard 不自动关闭/杀 agent。配置位于 .planning/orchestration.json。

自动交接：每轮 `herdr pane read <pane> --lines 80` 解析总舵 `ctx`，兼容
`ctx (412k/1M)`、`ctx 75.4k/1M`（小数）、`ctx 62.3%`（百分比折算），多行取最后；
pane 先按 agent 名解析，名字缺失/漂移时回退 <crew-dir>/dispatch-agents.json
（按名或最近 role=helm 条目反查；关系不可解释或解析不到时 fail-safe 不动作）。
ctxWarn（默认 30%）写 <crew-dir>/guard.handoff phase=warn 并通知；
ctxHandoff（默认 50%）一次性 `dispatch.py tab create` 新 tab（配置 helm.tabLabel
默认「总舵」）→ `start --name <继任>` → `prompt` → 首报后 close 旧 pane，确认旧 tab
无剩余 pane 后 close 旧 tab；guard.handoff 记录 oldTab/newTab（旧文件缺字段时只关旧 pane）；
总舵 tab 永不 split。旧舵按 handoff old 身份判定且不杀：working 时保持 started 等待；
继任缺失/blocked/idle 无首报或旧舵持续未收口连续 3 轮则记 phase=done result=failed + 通知人工，
不循环、不自动重试、不回落 pane split；失败通知带出 newPane/newTab：
创建阶段失败按孤儿资源提示清理，首报等待/旧舵收口失败提示保留待人工处置。
交接完成（phase=done, result=ok）后继续监控继任总舵（handoff new），不因 done 永久短路；
done(result=failed) 不自动重试，人工删除或改写 <crew-dir>/guard.handoff 后重启 guard 恢复。
旧 tab 有剩余 pane/无法确认或关闭失败只记 note，由人工或 pane-audit --apply 兜底。

周复盘兜底：仅当 feedback 复盘信箱 `待复盘` >0、无在跑 worker 且最近 retro 报告超期
（config retro.periodDays，默认 7，夹取 1–30）时叫醒，每日最多一次
（<crew-dir>/retro.wake 记 YYYY-MM-DD）；与普通待办叫醒分开判定。

兜底建议：30–60 分钟 cron 只检查 guard 进程存活（如 pgrep -f crew-guard.py），
本脚本不自动创建 cron。
"""
    parser = argparse.ArgumentParser(
        prog="crew-guard.py",
        description="编排值守：待办存在且无在跑 worker 时叫醒总舵（单实例，正常零输出）。",
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--root", help="目标仓根目录（默认：cwd 向上找 .planning）")
    parser.add_argument("--crew-dir", help="crew 运行时目录（默认 <git-common-dir>/crew）")
    parser.add_argument("--helm", help="总舵 herdr agent 名（默认读配置 helm.agentName 或派生）")
    parser.add_argument("--interval", type=int, default=120, help="轮询秒数（默认 120）")
    parser.add_argument("--once", action="store_true", help="只跑一轮（测试/人工抽查）")
    parser.add_argument("--json", action="store_true",
                        help="配合 --once 输出 JSON"
                             "（crewDir/planPath/pending/workers/claudeRuns/wake/"
                             "handoff/rework）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("error: --interval 必须为正数", file=sys.stderr)
        return 1
    root = resolve_root(args.root)
    crew_dir = Path(args.crew_dir) if args.crew_dir else default_crew_dir(root)
    try:
        lock = acquire_lock(crew_dir)
    except OSError as exc:
        print(f"error: 无法创建单实例锁：{exc}", file=sys.stderr)
        return 1
    if lock is None:
        return 0
    guard = Guard(
        root=root,
        crew_dir=crew_dir,
        helm=resolve_guard_helm(root, crew_dir, args.helm),
        interval=args.interval,
        list_agents=herdr_agents,
        prompt=herdr_prompt,
        notify=herdr_notify,
        run_status=run_plan_status,
    )
    try:
        if args.once:
            result = guard.run_once()
            if args.json:
                payload = {
                    "crewDir": str(crew_dir),
                    "planPath": result.get("planPath", ""),
                    "pending": result.get("pending", 0),
                    "workers": result.get("workers", []),
                    "claudeRuns": result.get("claudeRuns", []),
                    "wake": bool(result.get("woke")),
                    "handoff": result.get("handoff", {}),
                    "rework": result.get("rework", []),
                }
                print(json.dumps(payload, ensure_ascii=False))
            return 0
        guard.run_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
