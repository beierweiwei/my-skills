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
快速叫醒（FB-057①）：完成差集边沿——任一 worker 从 working 消失（herdr 或
claude runs 口径，探针排除）即叫醒核收，不要求全部转 idle、不要求有待办；
受全局冷却 `guard.wakeCooldownMinutes`（默认 10 分钟），被冷却吞掉的边沿记
guard.log；不受 pending 心跳间隔压制；消息带完成名单与核收指引。
blocked 年龄与重复叫醒：按 agent 身份（name，缺省 pane_id）记录首次 blocked 时间；
首次叫醒沿用全局冷却（`guard.wakeCooldownMinutes`，默认 10，夹取 1–240）；仍 blocked
且距该 agent 上次 blocked 叫醒 ≥ config `guard.blockedRepeatMinutes`（默认 15，夹取
1–240）再叫醒，该重复叫醒不受全局冷却压制；blocked 累计 ≥
`guard.blockedGraceMinutes`（默认 30，夹取 1–240）时叫醒消息提示「按 R6 处置
（re-task / close / escalate），应关闭工位或升级 Captain」；guard 不自动关闭/杀
agent；agent 不再 blocked 或消失即清除年龄。
等待审批（awaiting_approval）：qoder worker 等待权限对话框时 herdr 仍算 working，
guard 另有「卡住」来源——`crew_approvals.pending_approvals` 从 qoder run 日志按
callId 配对 `[permission-ask] prompt/settle`，未闭合且年龄 ≥
`guard.approvalWaitMinutes`（默认 15，夹取 1–240）即叫醒；重复叫醒按
agent+callId 去重，间隔 `guard.approvalRepeatMinutes`（默认 30，夹取 1–240）；
≥ `guard.approvalGraceMinutes`（默认 60，夹取 1–240）附「A 人工批准 / B 关闭重派
（close + 新 start）/ C 继续等待」建议；≥ `guard.approvalMaxAgeMinutes`（默认 240，
夹取 1–1440）停止重复并记日志。安全矩阵：helm 自身有未闭合审批时只写 guard.wake +
通知、不注入；绝不向等待审批的 worker pane 注入任何文本/按键；不自动 close。
周期心跳：pending 且真 idle（无 working/blocked/awaiting_approval）持续时，按
`guard.pendingHeartbeatMinutes`（默认 15，夹取 1–240）重复叫醒，仍受全局冷却约束；
与 blocked/awaiting_approval 同轮只发一条叫醒（reason 合并）。
空闲兜底（2.2.26 T63，2.2.31 改版）：无待办、无在跑、无卡住且总舵 idle/done 时，
`guard.idleWakeRounds=0`（默认）走状态变化驱动——仅兜底盲区两桶（Backlog 暂停
⏸/⛔ 与排队到期）逐轮集合 diff（指纹落 <crew-dir>/guard.state.json，跨重启
可比较），变化 → 下一轮叫醒一次只含增量；无变化完全静默（guard.log 每 30 轮
存活心跳行）；被全局冷却吞掉的增量持久化、冷却期满补发一次；⏸ 排队条目文本含
YYYY-MM-DD 且今天 ≥ 到期日视为变化（一次性叫醒）。`idleWakeRounds>0` 恢复旧口径：
安静期连续 N 轮报平安 + Backlog 全量清单（不含已完成；可派/⏸ 排队/⏸ 待外部/⛔
阻塞全列），受全局冷却去重、触发后计数清零，总舵 working/blocked/缺失不累计。
Backlog ⏸ 两态（AMEND）：含外部条件语义
（待 Captain/待 C/留 Captain/待拍板/待评审/待授权/待窗口）→ 暂停桶「⏸ 待外部」
不计待办；其余 ⏸（下批/batch/评估中等排队语义）→ 暂停桶「⏸ 排队」（文本带阻塞列
全文），计入待办数据但不触发即时叫醒。
自动交接：每轮读总舵 pane 的 `ctx Nk/1M`（`herdr pane read <pane> --lines 80`），
兼容小数与百分比（`ctx 75.4k/1M`、`ctx 62.3%`）；pane 先按 agent 名解析，
名字缺失/漂移时回退 <crew-dir>/dispatch-agents.json（按名或最近 role=helm 条目反查，
反查关系不可解释时 fail-safe），解析不到则 fail-safe 不动作。按 config
`helm.ctxWarn`（默认 0.30）/`helm.ctxHandoff`（默认 0.50）写 <crew-dir>/guard.handoff
并通知；≥交接阈值一次性新建 tab/继任/首报后关旧 pane 与旧 tab，失败只通知不循环；
`tab create` 后按 pane list 校验新 tab pane 数=1，多出的明确空 pane 经
`dispatch pane close` 关闭并记 guard.log（FB-038）；取数/关闭失败只告警不中断交接。
旧舵一律按 handoff `old` 身份判定，working 不杀；继任缺失/blocked/idle 无首报或旧舵
持续未收口连续 HANDOFF_STALL_ROUNDS 轮后记 failed 并通知人工。交接操作前以 oldPane
反查 pane/tab（pane list 权威、agent list 兜底，FB-045）：oldTab 缺失/不一致以反查为准
并写回 handoff；反查不到（pane/agent 消失）不按未验证 oldTab 操作。交接完成（phase=done）后
继续监控继任总舵，不因 done 永久短路；并对残留旧 helm 做一次有界回收（idle/done 且
非现役/非聚焦/非白名单才关，handoff `reclaim` 标记幂等）。每 REAPER_EVERY_ROUNDS 轮
调用 `crew-reaper.py --apply --json`，失败只记日志不阻断值守（D-7）。交接启动前先执行
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
import hashlib
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
try:  # 审批等待检测器缺失时仍保留旧值守能力（fail-soft，启动不崩）。
    from crew_approvals import pending_approvals  # noqa: E402
except Exception:  # noqa: BLE001
    pending_approvals = None
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$")
STATUS_RE = re.compile(r"\*\*Status:\*\*\s+(pending|in[ _]progress|complete)")
TODO_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$")
PAUSE_MARKS = ("⏸", "待 Captain", "留 Captain")
# 英文完成态按词边界判定：避免 incomplete / undone 误命中 complete / done
DONE_RE = re.compile(r"(?<![a-z])(?:complete|completed|done)(?![a-z])")
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
# D-7：guard 每 N 轮调用一次 crew-reaper（--apply --json）；失败只记日志不阻断
REAPER_EVERY_ROUNDS = 15
# 残留回收白名单：Captain 个人窗口 / 流程优化专用 tab 不自动关
RECLAIM_WHITELIST_PREFIXES = ("Captain·", "流程优化·")
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
# awaiting_approval（等待权限对话框）阈值：分钟，全部可经 orchestration.json 覆盖。
DEFAULT_APPROVAL_WAIT_MINUTES = 15
DEFAULT_APPROVAL_REPEAT_MINUTES = 30
DEFAULT_APPROVAL_GRACE_MINUTES = 60
DEFAULT_APPROVAL_MAX_AGE_MINUTES = 240
APPROVAL_MAX_AGE_MAX = 1440
# pending 真 idle 周期心跳与全局叫醒冷却（原硬编码 600s 配置化）。
DEFAULT_WAKE_COOLDOWN_MINUTES = 10
DEFAULT_PENDING_HEARTBEAT_MINUTES = 15
# 2.2.26 T63 空闲兜底叫醒：连续 N 轮全仓安静（无 pending/无在跑/无卡住）且 helm
# idle/done → 报平安 + Backlog 全量清单（含 ⏸ 条目）。2.2.31（guard-noise 流）：
# 默认 0＝状态变化驱动接管（盲区两桶 Backlog 暂停/排队到期指纹 diff，安静期变化
# 才叫醒一次只含增量，无变化完全静默）；>0＝恢复旧口径（安静期固定轮次报平安）。
DEFAULT_IDLE_WAKE_ROUNDS = 0
IDLE_WAKE_ROUNDS_MAX = 240
# 2.2.31 状态变化驱动：指纹/待补发增量落 <crew-dir>/guard.state.json（原子写，
# 跨进程/重启可比较）；安静期每 30 轮写 guard.log 存活心跳行（存活证明=看板 mtime
# 300s 心跳 + 本心跳行 + doctor pid 判活，不新建 cron）。
IDLE_CHANGE_STATE_FILE = "guard.state.json"
IDLE_CHANGE_STATE_VERSION = 1
IDLE_HEARTBEAT_EVERY_ROUNDS = 30
# 2.2.32/T76/B2 未消费唤醒桶：idle/done 工位名下有未消费回执/被拒派单时，guard
# 注入固定消费模板（只清单拼接，禁自由文本）。minIdleSeconds=300：CASE 实测
# 6 次 40+ 分钟空转，5 分钟避开正常回合间隙；队列/重试上限（S2）防风暴，重试
# 3 次失败升级通知后不再注入；状态持久化 guard.state.json unconsumedWake 键
# （S1：旧文件无该键视为空队列，跨重启恢复）。
UNCONSUMED_MIN_IDLE_SECONDS = 300
UNCONSUMED_QUEUE_MAX = 20
UNCONSUMED_RETRY_MAX = 3
UNCONSUMED_LOG_WINDOW = 400
UNCONSUMED_WAKE_TEMPLATE = (
    "[guard 未消费唤醒] 你名下有 {count} 项未消费回执/被拒派单：\n{items}\n"
    "请运行 `python3 scripts/crew-msg.py ls --unread` 消费并处置；"
    "被拒派单明细见 <crew>/agent-log.jsonl 的 result=refused 行"
    "（重投：`dispatch.py prompt --message-file <文件>`）。")
# ⏸ 排队条目到期语义（要件 5）：文本中 YYYY-MM-DD 取最小值为到期日，今天 ≥ 到期日
# 视为状态变化触发一次性叫醒。
DUE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# FB-054（2.2.28 S0 裁定，不得改数）：①agent idle/done 即视为审批结束、从卡住
# 集合移除（只在 guard 叫醒判定层；crew_approvals 公共返回不动）；超龄残留按
# 30 分钟记去重日志（与 approvalRepeatMinutes/blockedGraceMinutes 同量级）。
APPROVAL_RESIDUAL_MINUTES = 30
# B2：callId 形态异常只标注不抑制（保守性不倒退）；正常形态 = call_ 开头且
# 紧跟数字（call_01 / call_00_<随机> 均合法，call_x 为日志侧产物）。
CALL_ID_NORMAL_RE = re.compile(r"call_\d")
# T69②（2.2.28 Captain 口径，与 dispatch.py 同值同清单）：worker ctx 扫描阈值——
# >40% 告警附 compact 指令；≥60% 附关闭重建建议。helm 由 _excluded 排除；
# firstmate 按 dispatch registry role 排除（4c③：编排角色非切片 worker，其 ctx
# 归角色纪律与总舵监督，不适用 40/60 worker 口径；helm 的 30/50 交接阈值是
# 另一机制 helm.ctxWarn/helm.ctxHandoff，只作用 helm）。
WORKER_CTX_COMPACT = 0.40
WORKER_CTX_REBUILD = 0.60
COMPACT_CHECKLIST = (
    "compact 保真清单（compact 前确保 plan/progress/findings 写全）：目标、"
    "当前 Phase、Next Step、Decisions、Deviation、在途任务三时间戳、待回执/"
    "待核收、证据路径、占用/工位、验收断言；compact 后先读回 plan/progress/"
    "findings 再继续。")
# 2.2.26 T63 AMEND ⏸ 两态：Backlog ⏸ 行仅当含外部条件语义才排除（待外部），
# 其余 ⏸ 为纯排队（不即时叫醒、由空闲兜底承载）。
BACKLOG_EXTERNAL_PAUSE_MARKS = (
    "待 Captain", "待 C", "留 Captain", "待拍板", "待评审", "待授权", "待窗口",
)
WAKE_FILE_NAME = "guard.wake"
DEFAULT_WAKE_TTL_SECONDS = 7200
WAKE_GENERATED_RE = re.compile(r"^generated_at:\s*(\S+)\s*$")
# 同一 task 回执达到该数量时写一条非阻断「返工提醒」（不叫醒、不改 plan、不删回执）
REWORK_RECEIPT_THRESHOLD = 3

# T57：runtime 三栈；交接 start_args 按现役舵 runtime 组装（R2）
HANDOFF_RUNTIMES = ("qoder", "opencode", "claude")
# 2.2.32/T79/D：继任 helm 接手窗口 ctx 观测（只观测不叫醒）。15 分钟＝工程默认
# （无行为影响；标定素材 n=4 不足，样本 ≥10 后再随阈值一并复审走 D7——S6）。
# 窗口起点=guard.handoff doneAt（写出 phase=done 时补记）；样本落
# <crew>/guard.ctxobserve.jsonl（append-only，最小字段 ts/helm/ctx_k/phase）。
HANDOFF_CTX_OBSERVE_MINUTES = 15
CTX_OBSERVE_FILE = "guard.ctxobserve.jsonl"


def effective_helm_runtime(info: object) -> str:
    """dispatch registry 记录的有效 runtime；R7 backwards：旧过渡记录
    （runtime=qoder + actualRuntime=claude 双轨）在新语义下读作 claude。
    actualRuntime 仅此读取点兼容存量（close 后自然淘汰，doctor 提示清理）；
    新启动一律写如实 runtime。非法/缺失返回 ""。"""
    if not isinstance(info, dict):
        return ""
    actual = info.get("actualRuntime")
    if actual in HANDOFF_RUNTIMES:
        return str(actual)
    runtime = info.get("runtime")
    return runtime if runtime in HANDOFF_RUNTIMES else ""


def handoff_start_args(runtime: str, pane: str, name: str) -> list[str]:
    """交接继任舵的 dispatch start 实参（R2：继承现役舵 runtime，非单值假设）。

    2.2.32/T76/C2：不再手传 --permission-mode（dispatch start 已拒绝手传，
    rc=2；权限模式由 orchestration.json agents.permissionMode/模板解析）；
    runtime 已知时显式带 --runtime（多栈消歧语义保留）。"""
    args = ["start", "--role", "helm", "--pane", pane]
    if runtime:
        args += ["--runtime", runtime]
    args += ["--name", name, "--json"]
    return args


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
    """测试探针：按 agent 名或 cwd 判定（4c⑥ 更新用途清单）。

    现用于：blocked 叫醒排除（原始用途）、fast_wake working 集合过滤
    （FB-057① S0 A1）、worker ctx 扫描排除（T69②）。三者动机一致：
    探针会话的短暂状态不构成编排事件，误报即 FB-002 刷屏形态。
    """
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
            "approvalWaitMinutes": DEFAULT_APPROVAL_WAIT_MINUTES,
            "approvalRepeatMinutes": DEFAULT_APPROVAL_REPEAT_MINUTES,
            "approvalGraceMinutes": DEFAULT_APPROVAL_GRACE_MINUTES,
            "approvalMaxAgeMinutes": DEFAULT_APPROVAL_MAX_AGE_MINUTES,
            "wakeCooldownMinutes": DEFAULT_WAKE_COOLDOWN_MINUTES,
            "pendingHeartbeatMinutes": DEFAULT_PENDING_HEARTBEAT_MINUTES,
            "idleWakeRounds": DEFAULT_IDLE_WAKE_ROUNDS,
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
        for key, low, high in (
            ("blockedRepeatMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("blockedGraceMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("approvalWaitMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("approvalRepeatMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("approvalGraceMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("approvalMaxAgeMinutes", BLOCKED_MINUTES_MIN, APPROVAL_MAX_AGE_MAX),
            ("wakeCooldownMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("pendingHeartbeatMinutes", BLOCKED_MINUTES_MIN, BLOCKED_MINUTES_MAX),
            ("idleWakeRounds", 0, IDLE_WAKE_ROUNDS_MAX),
        ):
            value = guard.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                config["guard"][key] = max(low, min(high, value))
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


def _parse_backlog(root: Path, path: Path, rel: str, pending: list[Todo],
                   paused: list[Todo]) -> None:
    """Backlog 行解析（2.2.26 T63 AMEND ⏸ 两态口径）：

    - ⏸ 且含外部条件语义（待 Captain/待 C/留 Captain/待拍板/待评审/待授权/待窗口）
      → 暂停桶「⏸ 待外部：」，不计待办、不叫醒。
    - 其余 ⏸（下批/batch/评估中等纯排队语义）→ 暂停桶「⏸ 排队：」，计入待办数据
      （idle 兜底 wake 清单可见）但不触发即时叫醒，由空闲兜底/攒批触发承载。
    - 无 ⏸ 的行：blocked/已完成后过滤——可派进 pending（触发既有叫醒通道）；
      blocked 行进暂停桶「⛔ 阻塞：」（仅兜底清单可见，不触发）。

    2.2.31：已完成判定（「已完成」/「✅」/DONE_RE）必须先于 ⏸/外部标记匹配——
    已完成行常带「待 Captain 签认」等后缀（本仓 T40–T49 实证），后匹配会让已完成
    行误入暂停桶、持续进出空闲兜底清单（制造假变化/假安静，guard-noise 流根因 1）。
    """
    config = load_config(root)
    prefix = config["backlog"]["agentPrefix"]
    for cells in _table_rows(_section_lines(path, "Backlog")):
        if len(cells) < 2:
            continue
        ident = cells[0]
        if not ident.startswith(prefix):
            continue
        text = " ".join(cells[:2]).strip()
        blocker = cells[-1]
        if "已完成" in blocker or "✅" in blocker or DONE_RE.search(blocker.lower()):
            continue
        if any(mark in blocker for mark in BACKLOG_EXTERNAL_PAUSE_MARKS):
            paused.append(Todo(rel, "Backlog", f"⏸ 待外部：{text}"))
            continue
        if any("⏸" in cell for cell in cells):
            # 2.2.31：排队桶文本带 blocker 全文——到期语义（YYYY-MM-DD）常写在
            # ⏸ 标记后（如「⏸ 复盘 2026-11-20」），_due_entries 需从文本解析到期日。
            paused.append(Todo(rel, "Backlog", f"⏸ 排队：{text}（{blocker}）"))
            continue
        if "blocked" in blocker.lower():
            paused.append(Todo(rel, "Backlog", f"⛔ 阻塞：{text}"))
            continue
        pending.append(Todo(rel, "Backlog", text))


def scan_todos(root: Path) -> tuple[list[Todo], list[Todo], list[Todo]]:
    planning = root / ".planning"
    pending: list[Todo] = []
    residual: list[Todo] = []
    paused: list[Todo] = []
    if not planning.is_dir():
        return pending, residual, paused
    config = load_config(root)
    portfolio = (root / config["portfolioDir"] / "task_plan.md").resolve()
    for path in sorted(planning.glob("*/task_plan.md")):
        rel = path.relative_to(root).as_posix()
        try:
            _parse_plan_todos(path, rel, pending, residual)
            if path.resolve() == portfolio:
                _parse_backlog(root, path, rel, pending, paused)
        except (OSError, UnicodeDecodeError, ValueError):
            # S1：坏 plan（非 UTF-8/不可读）跳过，不拖垮整轮值守
            continue
    return pending, residual, paused


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


def herdr_tabs() -> list[dict]:
    env = {**os.environ, "HERDR_ENV": "1"}
    proc = subprocess.run(
        ["herdr", "tab", "list"],
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=15,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "herdr tab list failed")
    payload = json.loads(proc.stdout)
    return payload.get("result", {}).get("tabs", [])


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


def run_reaper_apply(root: Path, crew_dir: Path) -> tuple[int, str]:
    """周期调用 crew-reaper（B 单交付）：`--apply --json`，失败由调用方记日志。

    脚本缺失/异常返回非 0（-1 表示不可用）；不抛异常、不阻断值守。
    """
    script = Path(__file__).resolve().parent / "crew-reaper.py"
    if not script.is_file():
        return -1, "crew-reaper.py 不存在（跳过周期回收）"
    proc = subprocess.run(
        [sys.executable, str(script), "--root", str(root),
         "--crew-dir", str(crew_dir), "--apply", "--json"],
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


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


def pane_tab_lookup(
    pane_id: str,
    agents: list[dict] | None,
    panes: list[dict] | None,
) -> tuple[str, str]:
    """以 pane 反查所在 tab：返回 (tab_id, source)，source ∈ {"panes","agents",""}。

    pane list 是 pane→tab 的权威来源，agent list 兜底；都查不到返回 ("","")。
    FB-045：手工 handoff 的 oldTab 可能沿用上一轮旧值，必须以 oldPane 反查为准。
    """
    if not pane_id:
        return "", ""
    for pane in panes or []:
        if not isinstance(pane, dict):
            continue
        if str(pane.get("pane_id") or "") != pane_id:
            continue
        tab_id = str(pane.get("tab_id") or "").strip()
        if tab_id:
            return tab_id, "panes"
    for agent in agents or []:
        if not isinstance(agent, dict):
            continue
        value = agent.get("pane_id") or agent.get("pane")
        if str(value or "").strip() != pane_id:
            continue
        tab_id = str(agent.get("tab_id") or "").strip()
        if tab_id:
            return tab_id, "agents"
    return "", ""


def pane_present(
    pane_id: str,
    agents: list[dict] | None,
    panes: list[dict] | None,
) -> bool:
    """pane 是否仍存在于 pane list 或 agent list（两路任一命中即算存在）。

    两路都没有才判定「pane/agent 消失」；仅一路有（如 registry 残留或 pane list
    过滤）时仍按存在处理，交给 dispatch 关闭结果做最终裁决（失败转人工）。
    """
    if not pane_id:
        return False
    for pane in panes or []:
        if isinstance(pane, dict) and str(pane.get("pane_id") or "") == pane_id:
            return True
    for agent in agents or []:
        if not isinstance(agent, dict):
            continue
        value = agent.get("pane_id") or agent.get("pane")
        if str(value or "").strip() == pane_id:
            return True
    return False


@dataclass(frozen=True)
class HandoffTabCheck:
    """手工 handoff 字段校验结果（独立可测，不依赖 Guard/main 循环）。"""

    status: str  # ok | missing_handoff_tab | mismatch | unresolved | unresolved_data | pane_gone
    old_tab: str  # 校正后的 oldTab（沿用或反查值）
    resolved_tab: str = ""
    source: str = ""
    pane_exists: bool = False
    note: str = ""


def validate_handoff_fields(
    fields: dict[str, str],
    agents: list[dict] | None,
    panes: list[dict] | None,
) -> HandoffTabCheck:
    """校验 handoff `oldTab` 必须属于 `oldPane` 所在 tab。

    - 反查到 tab：缺失则纠正（missing_handoff_tab）、不一致则以反查为准（mismatch）；
    - 反查不到但 pane/agent 仍在：unresolved，沿用 handoff oldTab（不静默）；
    - 反查不到且 pane/agent 消失：pane_gone，fail-soft（不清空、不按 oldTab 操作）；
    - panes=None（pane list 不可用）：unresolved_data，保持 legacy 兜底。
    """
    old_pane = str(fields.get("oldPane") or "").strip()
    handoff_tab = str(fields.get("oldTab") or "").strip()
    if panes is None:
        return HandoffTabCheck(
            "unresolved_data", handoff_tab, pane_exists=bool(old_pane),
            note="pane list 不可用，沿用 handoff oldTab",
        )
    if not old_pane:
        return HandoffTabCheck(
            "pane_gone", handoff_tab,
            note="handoff 缺 oldPane，无法反查",
        )
    resolved, source = pane_tab_lookup(old_pane, agents, panes)
    exists = pane_present(old_pane, agents, panes)
    if resolved:
        if not handoff_tab:
            return HandoffTabCheck(
                "missing_handoff_tab", resolved, resolved, source, True,
                f"handoff 缺 oldTab，按 {old_pane} 反查纠正为 {resolved}",
            )
        if handoff_tab != resolved:
            return HandoffTabCheck(
                "mismatch", resolved, resolved, source, True,
                f"handoff oldTab={handoff_tab} 与 {old_pane} 反查 {resolved} 不一致",
            )
        return HandoffTabCheck("ok", handoff_tab, resolved, source, True)
    if exists:
        return HandoffTabCheck(
            "unresolved", handoff_tab, "", "", True,
            f"未反查到 {old_pane} 所在 tab，沿用 handoff oldTab={handoff_tab or '空'}",
        )
    return HandoffTabCheck(
        "pane_gone", handoff_tab, "", "", False,
        f"旧 pane {old_pane} 已不存在",
    )


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
    list_tabs: Callable[[], list[dict]] = herdr_tabs
    prompt: Callable[[str, str], bool] = herdr_prompt
    notify: Callable[[str, str], bool] = herdr_notify
    pane_read: Callable[[str, int], str] = herdr_pane_read
    dispatch: Callable[[Path, list[str]], tuple[int, str, str]] = run_dispatch
    run_status: Callable[[Path, str], tuple[int, str]] = run_plan_status
    run_rotate: Callable[[Path], tuple[int, str]] | None = None
    run_reaper: Callable[[Path, Path], tuple[int, str]] | None = None
    log_fn: Callable[[str], None] | None = None
    # awaiting_approval 检测器：None = crew_approvals.pending_approvals（可注入便于单测）。
    stuck_detect: Callable[[list[dict], float], list[dict]] | None = None
    consecutive: int = field(default=0, init=False)
    # FB-057①（2.2.28 S0 A1）：上一轮 working 名单集合（herdr + claude runs 口径，
    # 探针排除）；差集边沿替代旧「全部清零」总数边沿，部分完成也触发快速叫醒。
    prev_working: set[str] = field(default_factory=set, init=False)
    # 2.2.26 T63：空闲兜底计数——无 pending/无在跑/无卡住且 helm idle/done 的连续轮数
    idle_quiet_rounds: int = field(default=0, init=False)
    last_wake_at: float | None = field(default=None, init=False)
    last_pending_wake_at: float | None = field(default=None, init=False)
    last_write_at: float | None = field(default=None, init=False)
    blocked_since: dict[str, float] = field(default_factory=dict, init=False)
    blocked_last_wake: dict[str, float] = field(default_factory=dict, init=False)
    approval_last_wake: dict[str, float] = field(default_factory=dict, init=False)
    approval_max_logged: set[str] = field(default_factory=set, init=False)
    approval_logged: set[str] = field(default_factory=set, init=False)
    # 4c①：审批残留日志按 agent|callId keyed 去重（消息含时变年龄，按文本去重
    # 永不命中——subA 实证 4 轮 4 条）；随 _track_approvals 按 ask 存活集清理。
    residual_logged: set[str] = field(default_factory=set, init=False)
    # 4c②：worker ctx 解析失败已告警过的 agent（恢复可读时移除，防每轮刷屏）。
    ctx_unresolved_logged: set[str] = field(default_factory=set, init=False)
    handoff_attempted: bool = field(default=False, init=False)
    handoff_failed_noted: bool = field(default=False, init=False)
    # 2.2.32/T76/B2：工位 idle/done 起始时间表（回 working/blocked 清除；重启后从
    # 重启时刻保守起算）与未消费唤醒状态 {key: {done, retries, pending}}——
    # key=receipt|<id> 或 refused|<agent>|<ts>；持久化 guard.state.json。
    agent_idle_since: dict[str, float] = field(default_factory=dict, init=False)
    unconsumed_woken: dict[str, dict] = field(default_factory=dict, init=False)
    unconsumed_restored: bool = field(default=False, init=False)
    handoff_wait_rounds: int = field(default=0, init=False)
    handoff_close_rounds: int = field(default=0, init=False)
    reaper_rounds: int = field(default=0, init=False)
    # 2.2.31 工单 07B：容量叫醒签名（清单不变不重复叫醒）+ >hard 首见时间戳；
    # 工单 08 B-2：升级通知按签名一次性（同 episode 不重发，签名变化复位）。
    capacity_wake_signature: str = field(default="", init=False)
    capacity_first_hard_at: float | None = field(default=None, init=False)
    capacity_escalated_signature: str = field(default="", init=False)
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

    def _ctx_observe(self, agents: list[dict], now: float) -> None:
        """2.2.32/T79/D：继任 helm 接手窗口 ctx 观测（只观测不叫醒）。

        guard.handoff phase=done（result=ok，继任实际接手）且带 doneAt 时，对
        继任（handoff new）在 doneAt 起 HANDOFF_CTX_OBSERVE_MINUTES 分钟内每轮
        采样一次 pane ctx（parse_ctx_k），追加 <crew>/guard.ctxobserve.jsonl
        （最小字段 ts/helm/ctx_k/phase，不加推测字段——S6）+ guard.log 一行摘要。
        本批不设阈值/不分类/不告警（n=4 不足标定，样本 ≥10 后随阈值复审）；
        不 wake/不通知/不阻断/不改 ctxWarn/ctxHandoff 既有行为。窗口外/缺
        doneAt（旧文件升级残留）/继任不在册/ctx 解析失败 → 静默跳过本轮。
        重启按 doneAt 续窗不重采（JSONL append-only 时间序列，幂等天然成立）。"""
        state = self._read_handoff()
        if (state.get("phase") != "done" or state.get("result") != "ok"
                or not state.get("new") or not state.get("doneAt")):
            return
        try:
            done_at = datetime.fromisoformat(
                str(state["doneAt"]).replace("Z", "+00:00"))
        except ValueError:
            return
        elapsed = now - done_at.timestamp()
        window = HANDOFF_CTX_OBSERVE_MINUTES * 60
        if elapsed < 0 or elapsed > window:
            return
        successor = str(state["new"])
        agent = next((a for a in agents
                      if isinstance(a, dict)
                      and str(a.get("name") or "") == successor), None)
        if agent is None:
            return
        pane = str(agent.get("pane_id") or "").strip()
        if not pane:
            return
        try:
            text = self.pane_read(pane, 80)
        except Exception:  # noqa: BLE001 - 读失败不记样本、不中断值守
            return
        ctx_k = parse_ctx_k(text)
        if ctx_k is None:
            return
        sample = {"ts": utc_now(), "helm": successor,
                  "ctx_k": ctx_k, "phase": state.get("phase", "")}
        try:
            with open(self.crew_dir / CTX_OBSERVE_FILE, "a",
                      encoding="utf-8") as handle:
                handle.write(json.dumps(sample, ensure_ascii=False) + "\n")
        except OSError as exc:
            self.log(f"ctx 观测写入失败：{type(exc).__name__}: {exc}")
            return
        self.log(f"继任 helm 接手 ctx={ctx_k:g}k"
                 f"（+{int(elapsed // 60)} 分钟，观测窗 {HANDOFF_CTX_OBSERVE_MINUTES} 分钟内）")

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

    # —— 2.2.32/T76/B2 未消费唤醒桶（兜底：refused 复投 / unrouted 后建 /
    # done 残留未读；跨项目不注入为既定安全取舍，由收件方舵轮询兜底） ——

    def _track_agent_idle(self, agents: list[dict], now: float) -> None:
        """维护工位 idle/done 起始时间表；状态回 working/blocked 或工位消失时清除。
        重启后从重启时刻保守起算（多等一个观察窗，不误叫正常回合间隙）。"""
        seen: set[str] = set()
        for agent in agents:
            name = str(agent.get("name") or "")
            if not name:
                continue
            seen.add(name)
            if str(agent.get("agent_status") or "") in ("idle", "done"):
                self.agent_idle_since.setdefault(name, now)
            else:
                self.agent_idle_since.pop(name, None)
        for name in list(self.agent_idle_since):
            if name not in seen:
                del self.agent_idle_since[name]

    def _unread_inbox_receipts(self) -> list[dict]:
        """本 crew inbox 未消费回执（按 crew-msg 只读游标判定；guard 不代标读）。
        游标缺失/损坏按全量未读处理（fail-safe 宁可多列，与 crew-msg 同口径）。"""
        read_ids: set[str] = set()
        try:
            cursor = json.loads(
                (self.crew_dir / "inbox-read-cursor.json").read_text(encoding="utf-8"))
            read_ids = {str(k) for k in (cursor.get("read") or {})}
        except (OSError, ValueError, AttributeError):
            read_ids = set()
        out: list[dict] = []
        try:
            paths = sorted((self.crew_dir / "inbox").glob("*.json"))
        except OSError:
            return []
        for path in paths:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            rid = str(data.get("receipt_id") or path.stem)
            if rid in read_ids:
                continue
            out.append(data)
        return out

    @staticmethod
    def _match_unconsumed_owner(receipt: dict, name: str) -> bool:
        """保守归属（2.2.32 补强）：优先 receipt['to'] == name 精确命中
        （crew-msg write 落的收件方字段）；无 to 才回退 work_slot/task/findings
        正文匹配；发送方自己排除（D7 B2-①：匹配不上跳过＋记日志，宁可漏叫
        不叫错）。"""
        if str(receipt.get("from") or "") == name:
            return False
        to = str(receipt.get("to") or "").strip()
        if to:
            return to == name
        findings = receipt.get("findings")
        hay = " ".join(filter(None, [
            str(receipt.get("work_slot") or ""),
            str(receipt.get("task") or ""),
            " ".join(str(x) for x in findings) if isinstance(findings, list) else "",
        ]))
        return name in hay

    def _refused_prompts(self) -> list[dict]:
        """agent-log.jsonl 近窗口内 result=refused 的 prompt 行（含目标 agent 名），
        供 dispatch prompt 聚焦/审批拒后的兜底复投提醒。"""
        path = self.crew_dir / "agent-log.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        rows: list[dict] = []
        for line in lines[-UNCONSUMED_LOG_WINDOW:]:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if (isinstance(row, dict) and row.get("action") == "prompt"
                    and row.get("result") == "refused"):
                rows.append(row)
        return rows

    def _persist_unconsumed(self) -> None:
        """unconsumedWake 并入 guard.state.json（合并读改写，不覆盖其它键；写失败
        只记日志）。_write_idle_state 亦恒带该键，两写入路径互不丢。

        首轮护栏：state 文件不存在且队列为空时直接 return——主流程 idle 基线
        以「文件是否存在」判首轮（首轮建基线静默），此处预创建空文件会让首轮
        被当成 diff 误叫醒（IdleChangeWakeTest 10 例回归实证）。"""
        state = self._read_idle_state()
        if state is None and not self.unconsumed_woken:
            return
        if state is None:
            state = {"version": IDLE_CHANGE_STATE_VERSION,
                     "buckets": {}, "pendingNotify": None}
        state["unconsumedWake"] = self.unconsumed_woken
        try:
            atomic_write_text(
                self.crew_dir / IDLE_CHANGE_STATE_FILE,
                json.dumps(state, ensure_ascii=False, indent=1))
        except OSError as exc:
            self.log(f"guard.state.json 写入失败（unconsumed）：{type(exc).__name__}: {exc}")

    def _restore_unconsumed(self) -> None:
        """跨重启恢复未消费唤醒状态（S1：旧文件无 unconsumedWake 键=空队列）。"""
        state = self._read_idle_state()
        saved = state.get("unconsumedWake") if isinstance(state, dict) else None
        if isinstance(saved, dict):
            self.unconsumed_woken = {
                str(k): dict(v) for k, v in saved.items() if isinstance(v, dict)}

    def _scan_unconsumed_wake(self, agents: list[dict], now: float) -> None:
        """兜底桶主扫描：idle/done ≥300s 的在册工位名下有未消费回执/被拒派单 →
        注入固定消费模板。守卫链（安全矩阵不放松）：working/blocked 不叫；
        probe/排除名单跳过；审批挂起或聚焦/不可判定 → 延后（不注入、不计数）；
        注入失败按重试计，UNCONSUMED_RETRY_MAX 次后升级通知并停叫；
        按条目 key 只成功叫一次；候选上限 UNCONSUMED_QUEUE_MAX。"""
        candidates: dict[str, list[tuple[str, str]]] = {}
        matched_keys: set[str] = set()
        unread = self._unread_inbox_receipts()
        for receipt in unread[:UNCONSUMED_QUEUE_MAX]:
            rid = str(receipt.get("receipt_id") or "")
            for agent in agents:
                name = str(agent.get("name") or "")
                if name and self._match_unconsumed_owner(receipt, name):
                    key = f"receipt|{rid}"
                    matched_keys.add(key)
                    candidates.setdefault(name, []).append(
                        (key, f"回执 {rid}（task={receipt.get('task') or '—'}）"))
        for row in self._refused_prompts()[:UNCONSUMED_QUEUE_MAX]:
            target = str(row.get("agent") or "")
            if not target:
                continue
            key = f"refused|{target}|{row.get('ts')}"
            matched_keys.add(key)
            candidates.setdefault(target, []).append(
                (key, f"被拒派单（task={row.get('task') or '—'}）"))
        # 编排角色排除（对齐 _scan_worker_ctx 口径）：总舵不进兜底桶（guard 只叫
        # helm 的既有拓扑不变；叫 helm 走 _wake 各通道）；registry role=helm 漂移
        # 名与 helm 前缀测试名都命中；firstmate 仍可被叫。
        try:
            registry_roles = self._dispatch_registry()
        except Exception:  # noqa: BLE001 - registry 不可读按名字前缀兜底
            registry_roles = {}
        for name, items in sorted(candidates.items()):
            agent = next((a for a in agents if str(a.get("name") or "") == name), None)
            if agent is None:
                continue
            if (str(agent.get("agent_status") or "") not in ("idle", "done")
                    or is_probe_agent(agent) or self._excluded(agent)):
                continue
            info = registry_roles.get(name) if isinstance(registry_roles, dict) else None
            role = (str(info.get("role")) if isinstance(info, dict)
                    else "").strip().lower()
            if role == "helm" or name.startswith("helm"):
                continue
            idle_since = self.agent_idle_since.get(name)
            if idle_since is None or now - idle_since < UNCONSUMED_MIN_IDLE_SECONDS:
                continue
            pending = [(key, label) for key, label in items[:UNCONSUMED_QUEUE_MAX]
                       if not self.unconsumed_woken.get(key, {}).get("done")]
            if not pending:
                continue
            keys = [key for key, _ in pending]
            defer_reason: str | None = None
            if self._detect_stuck([agent], now):
                defer_reason = "目标有未闭合审批"
            else:
                focused, focus_reason = self._pane_focus_state(agent)
                if focused is not False:
                    defer_reason = ("目标 pane 聚焦" if focused
                                    else f"聚焦状态无法判定（{focus_reason}）")
            if defer_reason:
                for key in keys:
                    self.unconsumed_woken.setdefault(key, {})["pending"] = True
                self.log(f"未消费唤醒延后（{name}：{defer_reason}，不注入）："
                         f"{len(pending)} 项")
                continue
            message = UNCONSUMED_WAKE_TEMPLATE.format(
                count=len(pending),
                items="\n".join(f"- {label}" for _, label in pending))
            try:
                prompted = bool(self.prompt(name, message))
            except Exception as exc:  # noqa: BLE001 - 注入异常按失败重试
                prompted = False
                self.log(f"未消费唤醒注入异常（{name}）：{type(exc).__name__}: {exc}")
            for key in keys:
                entry = self.unconsumed_woken.setdefault(key, {"retries": 0})
                entry["pending"] = False
                entry["at"] = now
                if prompted:
                    entry["done"] = True
                    continue
                entry["retries"] = int(entry.get("retries") or 0) + 1
                if entry["retries"] >= UNCONSUMED_RETRY_MAX:
                    entry["done"] = True  # 停止重试，升级通知一次
                    self.log(f"未消费唤醒重试 {entry['retries']} 次未成功（{name}，"
                             f"{key}），升级通知后停止注入")
                    try:
                        self.notify("guard 未消费唤醒升级",
                                    f"{name} 的未消费项 {key} 连续 "
                                    f"{UNCONSUMED_RETRY_MAX} 次注入失败，请人工查看。")
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"guard 通知失败：{exc}")
            if prompted:
                self.log(f"未消费唤醒（herdr agent prompt {name}）：{len(pending)} 项")
        # 无主未读回执只记日志（宁可漏叫不叫错）
        unmatched = [r for r in unread
                     if f"receipt|{r.get('receipt_id')}" not in matched_keys]
        if unmatched:
            self.log(f"未消费唤醒：{len(unmatched)} 条未读回执无在册归属（跳过）")
        self._persist_unconsumed()

    def _write_handoff(self, **updates: object) -> None:
        fields = self._read_handoff()
        fields.update({key: str(value).replace("\n", " ") for key, value in updates.items()})
        order = ("phase", "old", "oldPane", "oldTab", "tabCheck", "new", "newPane",
                 "newTab", "reclaim", "time", "reason", "result", "error",
                 "successorWasWorking", "doneAt")
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
        self._write_handoff(**fields, phase="done", result="failed", error=error,
                            doneAt=utc_now())
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
        return effective_helm_runtime(info)

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
                self.root, ["pane", "close", "--pane", pane_id,
                "--caller", "guard"]
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

        runtime = self._helm_runtime()
        start_args = handoff_start_args(runtime, new_pane, successor)
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
            "完成接手首报（当前进度/在跑 agent/待办/下一步，须带 handoff_read_bytes=<n>），"
            "首报前不接新活；"
            "首报后 guard 会确认并关闭旧舵 pane 与旧 tab。"
        )
        # 2.2.32/T76/B3：交接 prompt 走文件（dispatch --message 只收文件路径，
        # 内联文本被拒；先落 <crew>/handoff-message-<ts>.md 再传 --message-file）。
        message_file = self.crew_dir / f"handoff-message-{int(time.time())}.md"
        try:
            message_file.write_text(message, encoding="utf-8")
        except OSError as exc:
            return self._handoff_fail(
                "handoff message file", f"写入失败：{exc}", base)
        code, output, error = self.dispatch(
            self.root, ["prompt", "--agent", successor,
                        "--message-file", str(message_file)]
        )
        if code != 0:
            return self._handoff_fail("dispatch prompt", error or output, base)

        # 新一轮交接：清掉上一轮 handoff 的校验/回收标记，避免误抑制本轮残留回收。
        self._write_handoff(
            **base, phase="started", result="ok", successorWasWorking="0",
            tabCheck="", reclaim="",
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
        # FB-045：操作前用 oldPane 反查真实 tab；手工 handoff 的 oldTab 可能沿用
        # 上一轮旧值。反查到就以反查为准并写回纠正；反查不到时 fail-soft。
        try:
            panes: list[dict] | None = self.list_panes()
        except Exception as exc:  # noqa: BLE001 - pane list 不可用时保持 legacy 兜底
            panes = None
            self.log(f"旧舵 pane/tab 反查失败（pane list）：{type(exc).__name__}: {exc}")
        check = validate_handoff_fields(fields, agents, panes)
        base["oldTab"] = check.old_tab
        base["tabCheck"] = f"{check.status}:{check.note}"
        if check.status == "mismatch":
            self.log(f"交接 oldTab 不一致：{check.note}；以反查为准并写回 handoff")
            self._write_handoff(oldTab=check.old_tab, tabCheck=base["tabCheck"])
        elif check.status == "missing_handoff_tab":
            self.log(f"交接 oldTab 缺失：{check.note}；已写回 handoff")
            self._write_handoff(oldTab=check.old_tab, tabCheck=base["tabCheck"])

        close_note = f"旧舵 {old_name} 仍在运行（{old_status or 'working'}），未关闭"
        pane_closed = False
        if check.status == "pane_gone":
            close_note = (f"旧舵 {old_name} 的 pane {old_pane or '未知'} 已不存在；"
                          "旧 tab 未验证、未关闭")
            self.log(f"残留收敛：{close_note}")
        elif old_pane and old_status in ("", "idle", "done", "unknown"):
            code, output, error = self.dispatch(
                self.root, ["pane", "close", "--pane", old_pane,
                    "--caller", "guard"]
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

        old_tab = base["oldTab"]
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
                    self.root, ["tab", "close", "--tab", old_tab,
                    "--caller", "guard"]
                )
                if code != 0:
                    detail = (error or output).strip()
                    close_note += f"；旧 tab {old_tab} 关闭失败（{detail}），待人工或 pane-audit 兜底"
                    self.log(f"旧 tab {old_tab} 关闭失败：{detail}")
                else:
                    close_note += f"；旧 tab {old_tab} 已关闭"

        self._write_handoff(
            **base, phase="done", result="ok", successorWasWorking="1",
            doneAt=utc_now(),
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

    def _tabs_by_id(self) -> dict[str, dict] | None:
        """tab_id → tab（含 label/focused）；取数失败返回 None（调用方按无法判定处理）。"""
        try:
            tabs = self.list_tabs()
        except Exception as exc:  # noqa: BLE001 - tab list 故障按无法判定处理
            self.log(f"tab list 不可用（残留回收无法确认白名单/聚焦）："
                     f"{type(exc).__name__}: {exc}")
            return None
        return {
            str(tab["tab_id"]): tab
            for tab in tabs
            if isinstance(tab, dict) and tab.get("tab_id")
        }

    def _reclaim_residual_old_helm(
        self, fields: dict[str, str], agents: list[dict]
    ) -> str:
        """phase=done 后回收残留旧 helm（FB-045）：每份 handoff 只尝试/记录一次。

        仅当旧 helm agent_status ∈ {idle, done}、pane 存在且非现役 pane、tab 非现役
        tab、pane/tab 非聚焦、tab 非白名单时才经 `dispatch pane close` 关闭，tab
        无剩余 pane 再关 tab；其余情况只记日志。结果写入 handoff `reclaim=` 标记，
        guard 重启后不重复动作；不循环、不阻断主流程。
        """
        if fields.get("reclaim"):
            return ""
        old_name = str(fields.get("old") or "").strip()
        current = str(fields.get("new") or self.helm).strip() or self.helm
        if not old_name:
            self._write_handoff(reclaim="skipped:no-old")
            return "skipped"
        if old_name == current:
            self._write_handoff(reclaim="skipped:current")
            return "skipped"
        old_agent = self._find_agent(agents, old_name)
        if old_agent is None:
            self._write_handoff(reclaim="gone")
            self.log(f"残留回收：旧 helm {old_name} 已不存在（reclaim=gone）")
            return "gone"
        if not self._in_project(old_agent):
            self._write_handoff(reclaim="skipped:out-of-project")
            self.log(f"残留回收：旧 helm {old_name} cwd 不在本仓/worktree，"
                     "不关闭")
            return "skipped"
        status = str(old_agent.get("agent_status") or "")
        if status not in ("idle", "done"):
            self._write_handoff(reclaim=f"skipped:status={status or 'unknown'}")
            self.log(f"残留回收：旧 helm {old_name} 状态 {status or 'unknown'}，"
                     "不关闭（仅记录一次）")
            return "skipped"
        pane_id = self._agent_pane(old_agent)
        if not pane_id:
            self._write_handoff(reclaim="skipped:no-pane")
            self.log(f"残留回收：旧 helm {old_name} 无 pane 信息，不关闭")
            return "skipped"
        try:
            panes = self.list_panes()
        except Exception as exc:  # noqa: BLE001 - 无法确认 pane/tab 时不动作
            self._write_handoff(reclaim="skipped:pane-list")
            self.log(f"残留回收：pane list 不可用（{type(exc).__name__}: {exc}），"
                     "不关闭")
            return "skipped"
        pane_entry = next(
            (pane for pane in panes
             if isinstance(pane, dict)
             and str(pane.get("pane_id") or "") == pane_id),
            None,
        )
        if pane_entry is None:
            self._write_handoff(reclaim="gone")
            self.log(f"残留回收：旧 helm {old_name} 的 pane {pane_id} 已不存在"
                     "（reclaim=gone）")
            return "gone"
        current_agent = self._find_agent(agents, current)
        # 现役 pane/tab 以 agent list 为准，handoff newPane/newTab 兜底（保护面更大）。
        current_pane = (self._agent_pane(current_agent)
                        or str(fields.get("newPane") or "").strip())
        current_tab = ""
        if current_agent is not None:
            current_tab = str(current_agent.get("tab_id") or "")
            if not current_tab and current_pane:
                current_tab, _ = pane_tab_lookup(current_pane, agents, panes)
        if not current_tab:
            current_tab = str(fields.get("newTab") or "").strip()
        if pane_id == current_pane:
            self._write_handoff(reclaim="skipped:current-pane")
            self.log(f"残留回收：旧 helm {old_name} pane 与现役 pane 相同，不关闭")
            return "skipped"
        tab_id = (str(pane_entry.get("tab_id") or "")
                  or str(old_agent.get("tab_id") or ""))
        if not tab_id:
            self._write_handoff(reclaim="unresolved:tab")
            self.log(f"残留回收：旧 helm {old_name} 的 tab 无法确认（pane list/agent "
                     "list 均无 tab 信息），未关闭")
            return "unresolved"
        if tab_id == current_tab:
            self._write_handoff(reclaim="skipped:current-tab")
            self.log(f"残留回收：旧 helm {old_name} 与现役总舵同一 tab，不关闭")
            return "skipped"
        if pane_entry.get("focused") is not False:
            self._write_handoff(reclaim="skipped:focused")
            self.log(f"残留回收：旧 helm {old_name} pane {pane_id} 聚焦或聚焦无法判定，"
                     "未关闭")
            return "skipped"
        tabs = self._tabs_by_id()
        if tabs is None:
            self._write_handoff(reclaim="unresolved:tab-list")
            self.log(f"残留回收：tab list 不可用，未关闭旧 helm {old_name}")
            return "unresolved"
        tab = tabs.get(tab_id)
        if tab is None:
            self._write_handoff(reclaim="unresolved:tab-missing")
            self.log(f"残留回收：tab {tab_id} 不在 tab list 中（无法确认白名单），"
                     f"未关闭旧 helm {old_name}")
            return "unresolved"
        label = str(tab.get("label") or "")
        if label.startswith(RECLAIM_WHITELIST_PREFIXES):
            self._write_handoff(reclaim="skipped:whitelist")
            self.log(f"残留回收：旧 helm {old_name} 所在 tab {tab_id}（{label}）"
                     "属白名单，未关闭")
            return "skipped"
        if tab.get("focused") is True:
            self._write_handoff(reclaim="skipped:focused")
            self.log(f"残留回收：旧 helm {old_name} 所在 tab {tab_id} 当前聚焦，"
                     "未关闭")
            return "skipped"

        code, output, error = self.dispatch(
            self.root, ["pane", "close", "--pane", pane_id,
                "--caller", "guard"]
        )
        if code != 0:
            detail = (error or output).strip().splitlines()
            reason = detail[0] if detail else f"退出码 {code}"
            self._write_handoff(reclaim="skipped:close-failed")
            self.log(f"残留回收：旧 helm {old_name} pane {pane_id} 关闭失败"
                     f"（{reason}），不再重试")
            return "skipped"
        remaining = [
            pane for pane in panes
            if isinstance(pane, dict)
            and str(pane.get("tab_id") or "") == tab_id
            and str(pane.get("pane_id") or "") != pane_id
        ]
        note = f"pane {pane_id} 已关闭"
        if remaining:
            note += f"；旧 tab {tab_id} 仍有 {len(remaining)} 个 pane，未关闭"
        else:
            tcode, tout, terr = self.dispatch(
                self.root, ["tab", "close", "--tab", tab_id,
                    "--caller", "guard"]
            )
            if tcode == 0:
                note += f"；旧 tab {tab_id} 已关闭"
            else:
                detail = (terr or tout).strip().splitlines()
                reason = detail[0] if detail else f"退出码 {tcode}"
                note += f"；旧 tab {tab_id} 关闭失败（{reason}）"
                self.log(f"残留回收：旧 tab {tab_id} 关闭失败，待人工或 "
                         "pane-audit 兜底")
        self._write_handoff(reclaim="done")
        self.log(f"残留回收：旧 helm {old_name}（{note}）")
        return "done"

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
            # FB-045：交接完成后回收残留旧 helm（一次有界，失败不阻断）。
            try:
                self._reclaim_residual_old_helm(fields, agents)
            except Exception as exc:  # noqa: BLE001 - 回收失败不阻断值守
                self.log(f"残留回收异常：{type(exc).__name__}: {exc}（不阻断值守）")

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
                time=utc_now(), reason=reason, tabCheck="", reclaim="",
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

    def _scan_worker_ctx(self, workers: list[dict], now: float) -> list[dict]:
        """T69②：逐非编排角色 worker 扫 ctx（>40%/≥60% 收集）；失败可见不静默。

        firstmate/helm 按 dispatch registry role 排除（4c③：编排角色非切片
        worker，其 ctx 归角色纪律与总舵监督，40/60 worker 口径不适用）。
        发布门修复（2026-11-14 guard.log 17:19 事故）：helm 工位名带代号漂移
        （如 crew-orchestrator-helm-10 ≠ 配置名）时 workers 名单按配置名排除
        失效——这里统一以 registry 最新 role 记录为准，漂移名同样命中
        role=helm 排除，不止比配置名。探针 agent 不扫（无编排意义）。
        pane 缺失或 ctx 解析不出：不误报，但**必须可见**（4c②）——按 agent
        去重记一行日志，恢复可读后复位。返回 [{name, ratio}]，
        ratio ∈ (0.40, 1]。
        """
        try:
            registry = self._dispatch_registry()
        except Exception:  # noqa: BLE001 - registry 不可读不阻断扫描
            registry = {}
        alerts: list[dict] = []
        unresolved: list[str] = []
        for agent in workers:
            if is_probe_agent(agent):
                continue
            name = self._blocked_label(agent)
            info = registry.get(name) if isinstance(registry, dict) else None
            role = (str(info.get("role")) if isinstance(info, dict)
                    else "").strip().lower()
            if role in ("helm", "firstmate"):
                continue
            pane = self._agent_pane(agent)
            if not pane:
                unresolved.append(f"{name}（缺 pane_id）")
                continue
            try:
                text = self.pane_read(pane, 80)
            except Exception as exc:  # noqa: BLE001 - 读失败不误报但可见
                self.log(f"worker ctx 读取失败（{name}，pane {pane}）："
                         f"{type(exc).__name__}: {exc}")
                continue
            ctx_k = parse_ctx_k(text)
            if ctx_k is None:
                unresolved.append(f"{name}（pane {pane}）")
                continue
            self.ctx_unresolved_logged.discard(name)
            ratio = ctx_k / 1000.0
            if ratio > WORKER_CTX_COMPACT:
                alerts.append({"name": name, "pane": pane, "ratio": ratio})
        fresh = [item for item in unresolved
                 if item.split("（")[0] not in self.ctx_unresolved_logged]
        if fresh:
            for item in fresh:
                self.ctx_unresolved_logged.add(item.split("（")[0])
            self.log("worker ctx 解析失败（不误报、本轮不可见，恢复后复位）："
                     + "；".join(sorted(fresh)))
        return alerts

    def _working_keys(self, running: list[dict], claude_runs: list[dict]) -> set[str]:
        """FB-057①：working 名单集合（herdr `_blocked_key` + claude runs pid）。

        探针 agent（is_probe_agent）不进集合——S0 A1 并入修法：cwd 在本仓的
        探针结束时不得触发快速叫醒（FB-002 刷屏教训）。
        """
        keys: set[str] = set()
        for agent in running:
            if is_probe_agent(agent):
                continue
            key = self._blocked_key(agent)
            if key:
                keys.add(key)
        for run in claude_runs:
            pid = run.get("pid")
            if pid:
                # 4c⑤：带 role——总舵看到「刚完成待核收」能对应到工位。
                role = str(run.get("role") or "").strip() or "?"
                keys.add(f"claude:{role}:{pid}")
        return keys

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

    def _approval_log(self, message: str) -> None:
        """检测原因按消息去重，避免每轮为同一 agent 重复写 guard.log。"""
        if message in self.approval_logged:
            return
        self.approval_logged.add(message)
        self.log(message)

    def _detect_stuck(self, agents: list[dict], now: float) -> list[dict]:
        """检测未闭合 permission-ask（awaiting_approval）；失败 fail-soft 返回空。"""
        try:
            if self.stuck_detect is not None:
                items = self.stuck_detect(agents, now)
            elif pending_approvals is None:
                self._approval_log(
                    "审批等待检测器不可用（crew_approvals.py 缺失），跳过检测")
                return []
            else:
                items = pending_approvals(agents, now, log=self._approval_log)
        except Exception as exc:  # noqa: BLE001 - 检测失败不得中断值守
            self.log(f"审批等待检测失败：{type(exc).__name__}: {exc}")
            return []
        return [item for item in items or [] if isinstance(item, dict)]

    @staticmethod
    def _approval_key(entry: dict) -> str:
        agent = str(entry.get("agent") or entry.get("pane") or "?").strip()
        call_id = str(entry.get("call_id") or "?").strip()
        return f"{agent}|{call_id}"

    @staticmethod
    def _approval_label(entry: dict) -> str:
        return str(entry.get("agent") or entry.get("pane") or "?").strip() or "?"

    @staticmethod
    def _approval_age(entry: dict) -> int:
        try:
            return max(0, int(entry.get("age_seconds") or 0))
        except (TypeError, ValueError):
            return 0

    def _track_approvals(self, approvals: list[dict],
                         extra_active: set[str] | None = None) -> None:
        """ask 消失即清除年龄/去重状态（只保留本轮仍存在的 agent+callId）。

        extra_active：被抑制的残留项键（4c①）——并入存活集参与清理，使
        residual_logged 在 ask 消失（settle 补写）后复位、复现为新事件。
        """
        active = {self._approval_key(item) for item in approvals}
        if extra_active:
            active |= extra_active
        for key in list(self.approval_last_wake):
            if key not in active:
                del self.approval_last_wake[key]
        for key in list(self.approval_max_logged):
            if key not in active:
                self.approval_max_logged.discard(key)
        for key in list(self.residual_logged):
            if key not in active:
                self.residual_logged.discard(key)

    @staticmethod
    def _agent_status_index(agents: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
        """herdr agent 状态索引（name / pane_id → 归一化 status）。"""
        by_name: dict[str, str] = {}
        by_pane: dict[str, str] = {}
        for agent in agents or []:
            if not isinstance(agent, dict):
                continue
            status = str(agent.get("agent_status") or "").strip().lower()
            if not status:
                continue
            name = str(agent.get("name") or "").strip()
            if name:
                by_name[name] = status
            pane = str(agent.get("pane_id") or agent.get("pane") or "").strip()
            if pane:
                by_pane[pane] = status
        return by_name, by_pane

    def _suppress_finished_stuck(
        self, agents: list[dict], stuck: list[dict],
    ) -> tuple[list[dict], list[tuple[dict, str]]]:
        """FB-054 ①：agent idle/done 即视为审批结束，从卡住集合移除。

        只作用于 guard 叫醒判定层（S0 B1）：crew_approvals.pending_approvals
        公共返回不动（crew-msg/dispatch 注入前安全检查依赖它）；helm 走
        `_helm_has_pending_ask` 独立路径，不受影响。agent↔entry 匹配失配时
        fail-open 照常保留叫醒（S7，宁可误报不漏报）。
        """
        if not stuck:
            return stuck, []
        by_name, by_pane = self._agent_status_index(agents)
        kept: list[dict] = []
        suppressed: list[tuple[dict, str]] = []
        for entry in stuck:
            name = str(entry.get("agent") or "").strip()
            pane = str(entry.get("pane") or "").strip()
            status = by_name.get(name) if name else None
            if status is None and pane:
                status = by_pane.get(pane)
            if status is None:
                kept.append(entry)
            elif status in ("idle", "done"):
                suppressed.append((entry, status))
            else:
                kept.append(entry)
        return kept, suppressed

    def _log_residual_approvals(self, suppressed: list[tuple[dict, str]]) -> None:
        """S6（4c① keyed 去重）：超龄（>APPROVAL_RESIDUAL_MINUTES）被抑制项记残留日志。

        按 agent|callId keyed 去重（消息含时变年龄，按文本去重永不命中——
        subA 实证 4 轮 4 条）；键随 _track_approvals 的 ask 存活集清理。
        """
        for entry, status in suppressed:
            age = self._approval_age(entry)
            if age < APPROVAL_RESIDUAL_MINUTES * 60:
                continue
            key = (f"{self._approval_label(entry)}|{entry.get('call_id') or '?'}")
            if key in self.residual_logged:
                continue
            self.residual_logged.add(key)
            self.log(
                f"审批残留（已抑制叫醒，agent {status}）："
                f"{self._approval_label(entry)} callId {entry.get('call_id') or '?'}，"
                f"ask 开口 {age // 60} 分钟无 settle——疑日志未写 settle/滚出窗口，"
                "审批数据链缺陷待排查（FB-054）；同键不重复记录")

    def _note_abnormal_call_ids(self, stuck: list[dict]) -> None:
        """B2：callId 非 call_数字 形态只标注不抑制（叫醒判定照常）。"""
        for entry in stuck:
            call_id = str(entry.get("call_id") or "").strip()
            if call_id and not CALL_ID_NORMAL_RE.match(call_id):
                self._approval_log(
                    f"callId 形态异常（{call_id}，非 call_N）："
                    f"{self._approval_label(entry)}——疑审批数据链缺陷，"
                    "叫醒判定照常（FB-054 S0 B2）")

    @staticmethod
    def _call_id_note(entry: dict) -> str:
        call_id = str(entry.get("call_id") or "").strip()
        if call_id and not CALL_ID_NORMAL_RE.match(call_id):
            return f"；callId 形态异常（{call_id}），疑数据链缺陷"
        return ""

    def _approval_due(
        self,
        now: float,
        approvals: list[dict],
        guard_cfg: dict,
    ) -> tuple[list[dict], bool]:
        """返回（到期叫醒项，是否含重复叫醒）；≥max 停止重复并记日志。"""
        wait = guard_cfg["approvalWaitMinutes"] * 60
        repeat = guard_cfg["approvalRepeatMinutes"] * 60
        max_age = guard_cfg["approvalMaxAgeMinutes"] * 60
        due: list[dict] = []
        repeat_due = False
        for entry in approvals:
            age = self._approval_age(entry)
            if age < wait:
                continue
            key = self._approval_key(entry)
            last = self.approval_last_wake.get(key)
            if age >= max_age and last is not None:
                if key not in self.approval_max_logged:
                    self.approval_max_logged.add(key)
                    self.log(
                        f"审批等待超过上限（{guard_cfg['approvalMaxAgeMinutes']} 分钟），"
                        f"停止重复叫醒：{key}（age={age // 60} 分钟）")
                continue
            if last is None:
                due.append(entry)
            elif now - last >= repeat:
                due.append(entry)
                repeat_due = True
        return due, repeat_due

    def _approval_wake_lines(
        self,
        approvals: list[dict],
        guard_cfg: dict,
    ) -> list[str]:
        grace = guard_cfg["approvalGraceMinutes"] * 60
        lines: list[str] = []
        for entry in approvals:
            age = self._approval_age(entry)
            text = (f"- {self._approval_label(entry)}：等待审批 {age // 60} 分钟"
                    f"（callId {entry.get('call_id') or '?'}"
                    f"，tool={entry.get('tool') or '?'}）{self._call_id_note(entry)}")
            preview = str(entry.get("command_preview") or "").strip()
            if preview:
                text += f"；命令预览：{preview}"
            if age >= grace:
                text += ("\n  处置建议：A 人工批准（到 pane 处理对话框） / "
                         "B 关闭重派（close + 新 start） / C 继续等待")
            lines.append(text)
        lines.append("安全矩阵：guard 不会向等待审批的 pane 注入任何文本/按键，"
                     "也不会自动关闭/杀 agent；处置由 Captain/总舵决策。")
        return lines

    @staticmethod
    def _stuck_payload(approvals: list[dict]) -> list[dict]:
        payload: list[dict] = []
        for entry in approvals:
            age = Guard._approval_age(entry)
            payload.append({
                "agent": str(entry.get("agent") or ""),
                "pane": str(entry.get("pane") or ""),
                "callId": str(entry.get("call_id") or ""),
                "tool": str(entry.get("tool") or ""),
                "ageSeconds": age,
                "ageMinutes": age // 60,
                "commandPreview": str(entry.get("command_preview") or ""),
            })
        return payload

    def _helm_has_pending_ask(self, helm_agent: dict, now: float) -> bool:
        return bool(self._detect_stuck([helm_agent], now))

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

    def _maybe_run_reaper(self) -> None:
        """D-7：按固定轮次间隔调用 `crew-reaper.py --apply --json`。

        脚本不存在/退出非 0/异常只记 guard.log，不阻断值守、不影响待办与叫醒。
        """
        self.reaper_rounds += 1
        if self.reaper_rounds % REAPER_EVERY_ROUNDS != 0:
            return
        runner = self.run_reaper or run_reaper_apply
        try:
            code, output = runner(self.root, self.crew_dir)
        except Exception as exc:  # noqa: BLE001 - 周期回收失败不影响值守
            self.log(f"周期回收调用异常：{type(exc).__name__}: {exc}（不阻断值守）")
            return
        detail = " / ".join(line.strip() for line in output.splitlines()
                            if line.strip())[:300]
        if code == 0:
            self.log(f"周期回收完成：{detail or 'ok'}")
        elif code < 0:
            self.log(f"周期回收跳过：{detail or '脚本不可用'}")
        else:
            self.log(f"周期回收失败（退出码 {code}）：{detail or '无输出'}"
                     "（不阻断值守）")

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

    # —— 2.2.31 工单 07B：周期容量检查（Captain 直令 7/10）——
    CAPACITY_HARD_ESCALATE_MINUTES = 60

    def _capacity_persist(self, first_hard_at: float | None) -> None:
        """>hard 首见时间戳落 <crew-dir>/guard.capacity.json（原子写）；重启丢失
        则重计时（少发无害，help 已注明）。"""
        import json as _json
        payload = {"version": 1, "capacityFirstHardAt": first_hard_at}
        try:
            atomic_write_text(self.crew_dir / "guard.capacity.json",
                              _json.dumps(payload, ensure_ascii=False))
        except OSError:
            pass

    def _capacity_check(self, now: float, agents: list[dict]) -> None:
        """每轮容量检查：>warn 记 guard.log；>hard（工单 08 建议 1：严格大于，
        满编 hard 是合法稳态，不空跑回收）先调 pane-audit --apply 强制回收
        （复用既有安全判定与 caller 留痕，禁新写目标选择器/直调裸 herdr）→
        重取 live 计数 → 仍 >hard → _wake 叫醒「必须关闭清单」（签名去重，
        清单不变不重复）；拒建持续 60 分钟升级 herdr_notify 通知 Captain——
        同一签名 episode 只升级一次，签名变化＝新 episode 重新计时（工单 08
        B-2）。全在跑（无可安全回收）只告警+拒建，不杀在跑任务。"""
        try:
            import crew_capacity
        except ImportError:
            return
        cap = crew_capacity.count_crew_agents(agents, self.root, self.helm)
        cfg = crew_capacity.load_capacity_config(self.root)
        total = cap["total"]
        names = ", ".join(cap["names"])
        if total <= cfg["hard"]:
            # 合法稳态（含满编 hard，工单 08 建议 1）：清 >hard 计时与升级标记
            if self.capacity_first_hard_at is not None:
                self.capacity_first_hard_at = None
                self._capacity_persist(None)
            self.capacity_escalated_signature = ""
            if total > cfg["warn"]:
                if self.capacity_wake_signature != f"warn:{names}":
                    self.log(f"容量警告：crew 工位 {total} 超 warn={cfg['warn']}"
                             f"（hard={cfg['hard']}）：{names}")
                    self.capacity_wake_signature = f"warn:{names}"
            elif self.capacity_wake_signature:
                self.capacity_wake_signature = ""  # 回落 ok：签名复位
            return
        # >hard：先按既有安全规则回收（pane-audit --apply；working/blocked/
        # unknown 永不关、非白名单、非聚焦、idle/done、完成流）
        audit_script = Path(__file__).resolve().parent / "pane-audit.py"
        proc = None
        try:
            proc = subprocess.run(
                [sys.executable, str(audit_script), "--root", str(self.root),
                 "--apply", "--caller", "guard"],
                capture_output=True, text=True, check=False, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            self.log(f"容量强制回收调用 pane-audit 失败：{exc}")
        if proc is not None and proc.returncode != 0:
            # 工单 08 B-1：rc≠0 不得静默——记 guard.log 后继续告警链路
            lines = (proc.stderr or proc.stdout or "").strip().splitlines()
            self.log(f"容量强制回收 pane-audit 退出码 {proc.returncode}："
                     f"{lines[-1][:200] if lines else '无输出'}")
        live = self.list_agents()
        cap2 = crew_capacity.count_crew_agents(live, self.root, self.helm)
        total2 = cap2["total"]
        if total2 <= cfg["hard"]:
            self.log(f"容量强制回收完成：{total} → {total2}（hard={cfg['hard']}）")
            self.capacity_wake_signature = ""
            self.capacity_first_hard_at = None
            self.capacity_escalated_signature = ""
            self._capacity_persist(None)
            return
        signature = f"hard:{sorted(cap2['names'])}"
        if self.capacity_wake_signature and signature != self.capacity_wake_signature:
            # 状态变化＝新 episode：60 分钟重新计时、升级标记复位（工单 08 B-2）
            self.capacity_first_hard_at = now
            self.capacity_escalated_signature = ""
            self._capacity_persist(now)
        if self.capacity_first_hard_at is None:
            self.capacity_first_hard_at = now
            try:
                stored = json.loads((self.crew_dir / "guard.capacity.json")
                                    .read_text(encoding="utf-8"))
            except (OSError, ValueError):
                stored = {}
            if isinstance(stored, dict) and stored.get("capacityFirstHardAt"):
                self.capacity_first_hard_at = float(stored["capacityFirstHardAt"])
            self._capacity_persist(self.capacity_first_hard_at)
        escalate = (self.capacity_first_hard_at is not None
                    and now - self.capacity_first_hard_at
                    >= self.CAPACITY_HARD_ESCALATE_MINUTES * 60)
        should_wake = signature != self.capacity_wake_signature
        should_escalate = escalate and self.capacity_escalated_signature != signature
        if should_wake or should_escalate:
            listing = "\n".join(f"- {name}" for name in cap2["names"][:20])
            message = (
                f"guard 容量超限：crew 工位 {total2} > hard={cfg['hard']}，"
                "安全回收后仍超限，dispatch start 已拒建新工位。必须关闭清单：\n"
                f"{listing}\n"
                "处置：逐个 `python3 scripts/dispatch.py close <agent>`（working/"
                "blocked 先人工处置），或人工评估后 `dispatch start --force`。")
            if should_escalate:
                message += ("\n超限已持续 ≥60 分钟，升级通知 Captain。")
                try:
                    self.notify("guard 容量超限升级", message)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"容量升级通知失败：{exc}")
                self.capacity_escalated_signature = signature
            try:
                self._wake(message, agents, now)
            except Exception as exc:  # noqa: BLE001
                self.log(f"容量叫醒失败：{type(exc).__name__}: {exc}")
            self.capacity_wake_signature = signature

    # —— 2.2.31 状态变化驱动（guard-noise 流要件 1–7）——

    def _read_idle_state(self) -> dict | None:
        """读 guard.state.json（基线指纹 + 待补发增量）；缺/坏/版本不符返回 None。"""
        try:
            data = json.loads((self.crew_dir / IDLE_CHANGE_STATE_FILE)
                              .read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if (not isinstance(data, dict)
                or data.get("version") != IDLE_CHANGE_STATE_VERSION
                or not isinstance(data.get("buckets"), dict)):
            return None
        return data

    def _write_idle_state(self, buckets: dict[str, list[str]],
                          pending_notify: dict | None) -> None:
        payload = {
            "version": IDLE_CHANGE_STATE_VERSION,
            "ts": utc_now(),
            "digest": hashlib.sha256(
                json.dumps(buckets, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")).hexdigest(),
            "buckets": buckets,
            "pendingNotify": pending_notify,
            # 2.2.32/T76/B2：未消费唤醒状态恒随写（两写入路径互不丢；S1 向后兼容）
            "unconsumedWake": self.unconsumed_woken,
        }
        try:
            atomic_write_text(self.crew_dir / IDLE_CHANGE_STATE_FILE,
                              json.dumps(payload, ensure_ascii=False, indent=1))
        except OSError as exc:
            self.log(f"guard.state.json 写入失败：{type(exc).__name__}: {exc}")

    def _activity_buckets(self, pending: list[Todo], paused: list[Todo],
                          working_keys: set[str], blocked: list[dict],
                          stuck: list[dict], now: float) -> dict[str, list[str]]:
        """状态变化驱动的指纹桶（要件 1）：仅兜底盲区两桶——Backlog 暂停（⏸/⛔）
        与排队到期。

        收窄依据（guard-noise 流实现取舍）：pending/在跑/blocked/审批的变化会打断
        安静期（idle_quiet=False）且由既有叫醒通道承载（D7 报告 a 项六通道独立、
        cfg=0 下照常触发，E3/E4 运行复现），进指纹会与旧通道叠加并改变 2.2.30
        已验收行为（test_2221 两例回归实证）；paused/due 恰不打断安静期——兜底
        在安静语境触达即覆盖真盲区（审查建议 1 的敞口）。审批残留（FB-054 开口）
        同口径不进指纹。
        """
        return {
            "paused": sorted(todo.text for todo in paused),
            "due": sorted(self._due_entries(paused, now)),
        }

    @staticmethod
    def _due_entries(paused: list[Todo], now: float) -> list[str]:
        """⏸ 排队条目到期检测（要件 5）：文本含 YYYY-MM-DD 且今天 ≥ 最小日期。"""
        today = datetime.fromtimestamp(now).date()
        due: list[str] = []
        for todo in paused:
            if not todo.text.startswith("⏸ 排队："):
                continue
            dates = []
            for match in DUE_DATE_RE.findall(todo.text):
                try:
                    dates.append(datetime.strptime(match, "%Y-%m-%d").date())
                except ValueError:
                    continue  # 形如 2026-13-45 的假日期忽略
            if dates and today >= min(dates):
                due.append(todo.text)
        return due

    @staticmethod
    def _bucket_diff(old: dict,
                     new: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
        """逐桶集合 diff：产出新增/移除增量（要件 2 的消息来源）。"""
        changes: dict[str, dict[str, list[str]]] = {}
        for bucket in sorted(set(old) | set(new)):
            old_set = set(old.get(bucket) or [])
            new_set = set(new.get(bucket) or [])
            added = sorted(new_set - old_set)
            removed = sorted(old_set - new_set)
            if added or removed:
                changes[bucket] = {"added": added, "removed": removed}
        return changes

    @staticmethod
    def _merge_notify(pending_notify: dict | None,
                      changes: dict[str, dict[str, list[str]]]) -> dict:
        """被冷却吞掉的增量合并持久化（要件 7）：同桶 added/removed 取并集。"""
        sources: list[dict] = []
        if pending_notify:
            sources.append(pending_notify.get("changes") or {})
        sources.append(changes)
        merged: dict[str, dict[str, list[str]]] = {}
        for bucket in sorted({key for source in sources for key in source}):
            added: set[str] = set()
            removed: set[str] = set()
            for source in sources:
                entry = source.get(bucket) or {}
                added |= set(entry.get("added") or [])
                removed |= set(entry.get("removed") or [])
            merged[bucket] = {"added": sorted(added), "removed": sorted(removed)}
        return {"changes": merged}

    def _idle_change_message(self, notify: dict) -> str:
        lines = ["guard 状态变化：值守范围内状态发生变化，增量如下。"]
        changes = notify.get("changes") or {}
        titles = {"pending": "待办", "paused": "Backlog 暂停",
                  "workers": "在跑 worker", "blocked": "blocked worker",
                  "stuck": "等待审批", "due": "排队条目到期"}
        for bucket in sorted(changes):
            title = titles.get(bucket, bucket)
            for arrow, key, verb in (("+", "added", "新增"),
                                     ("-", "removed", "移除")):
                items = changes[bucket].get(key) or []
                if not items:
                    continue
                lines.append(f"- {title}{verb}：")
                lines.extend(f"  {arrow} {item}" for item in items[:10])
                if len(items) > 10:
                    lines.append(f"  …等 {len(items)} 条")
        lines.append("状态变化驱动模式（idleWakeRounds=0）：无变化时保持静默；"
                     "请按增量评估分派/核收。")
        lines.append(self._freshness_note())
        lines.append("请接手编排。")
        return "\n".join(lines)

    def _idle_change_wake(self, agents: list[dict], now: float,
                          cooldown_seconds: int,
                          pending_notify: dict | None) -> str | None:
        """状态变化兜底（idleWakeRounds=0 新口径，要件 2/7）：送达待补发增量。

        变化属强信号，只受全局冷却、不受 pending 心跳间隔压制；被冷却吞掉的
        增量已持久化在 guard.state.json，冷却期满后的首个无既有叫醒轮次补发
        一次。返回消息或 None（不叫/失败）。
        """
        if not pending_notify or not (pending_notify.get("changes") or {}):
            return None
        if (self.last_wake_at is not None
                and now - self.last_wake_at < cooldown_seconds):
            self.log(f"状态变化叫醒冷却中，增量留待补发"
                     f"（{len(pending_notify['changes'])} 桶）")
            return None
        message = self._idle_change_message(pending_notify)
        try:
            self._wake(message, agents, now)
        except Exception as exc:  # noqa: BLE001 - 叫醒失败保留增量下轮重试
            self.log(f"状态变化叫醒失败：{type(exc).__name__}: {exc}")
            return None
        self.last_wake_at = now
        return message

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

    def _wake(self, message: str, agents: list[dict],
              now: float | None = None) -> str:
        current = self.clock() if now is None else now
        helm_agent = next(
            (agent for agent in agents if str(agent.get("name") or "") == self.helm),
            None,
        )
        if helm_agent is not None:
            if self._helm_has_pending_ask(helm_agent, current):
                # 安全矩阵：注入会被权限对话框消费为「批准」，只通知不注入。
                self._write_wake_file(message)
                try:
                    self.notify("guard 叫醒", message)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"guard 通知失败：{exc}")
                self.log(
                    f"叫醒（helm 自身有未闭合审批，只写 guard.wake + 通知，不注入）："
                    f"{message.splitlines()[0]}")
                return "wake-file"
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
        self._maybe_run_reaper()
        # 2.2.33/A4/FB-063：破坏性 git 操作观测（S3 主 + S1 兜底；只观测不阻断，
        # 不 wake/不进 reason_parts；import 失败静默降级，BLK-3；D7 快审 PASS）。
        try:
            import crew_gitops
            crew_gitops.observe_once(self)
        except Exception as exc:  # noqa: BLE001 - 观测失败不影响值守
            self.log(f"gitops 观测异常：{type(exc).__name__}: {exc}")

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
        # 2.2.32/T76/B2：未消费唤醒兜底桶——idle/done 工位名下未消费回执/被拒
        # 派单注入固定消费模板（守卫链不放松；失败只记日志不阻断值守主流程）。
        self._track_agent_idle(workers, now)
        if not self.unconsumed_restored:
            self.unconsumed_restored = True
            self._restore_unconsumed()
        try:
            self._scan_unconsumed_wake(workers, now)
        except Exception as exc:  # noqa: BLE001
            self.log(f"未消费唤醒扫描异常：{type(exc).__name__}: {exc}")
        # 2.2.32/T79/D：继任 helm 接手窗口 ctx 观测（只观测不叫醒，失败不阻断）
        try:
            self._ctx_observe(agents, now)
        except Exception as exc:  # noqa: BLE001
            self.log(f"ctx 观测异常：{type(exc).__name__}: {exc}")
        try:
            self._capacity_check(now, agents)
        except Exception as exc:  # noqa: BLE001 - 容量检查失败不阻断值守
            self.log(f"容量检查异常：{type(exc).__name__}: {exc}")
        claude_runs = self._claude_runs()
        # 年龄表先于 handoff 分支更新：交接期间 blocked 时长继续累计。
        blocked_since = self._track_blocked(now, blocked)
        # awaiting_approval：等待权限对话框的 worker（herdr 仍可能是 working）。
        # FB-054 ①：agent idle/done 的开口先从卡住集合移除（超龄记残留日志）；
        # B2：畸形 callId 只标注，判定照常。
        stuck = self._detect_stuck(workers, now)
        stuck, residual = self._suppress_finished_stuck(agents, stuck)
        residual_keys = {
            f"{self._approval_label(entry)}|{entry.get('call_id') or '?'}"
            for entry, _status in residual}
        self._log_residual_approvals(residual)
        self._note_abnormal_call_ids(stuck)
        self._track_approvals(stuck, extra_active=residual_keys)
        guard_cfg = load_config(self.root)["guard"]
        approval_due, approval_repeat_due = self._approval_due(now, stuck, guard_cfg)
        pending, residual, paused = scan_todos(self.root)

        for todo in residual:
            self.log(f"已完成 Phase 残留 [ ]（仅记日志）：{todo.label}")

        working_keys = self._working_keys(running, claude_runs)
        finished = self.prev_working - working_keys
        # FB-057①：完成差集边沿——任一 worker 从 working 消失即触发快速叫醒，
        # 不再要求「全部清零且有待办」（S0 A1；冷却沿用全局 wakeCooldownMinutes）。
        fast_wake = bool(finished)
        self.prev_working = working_keys

        condition = bool(pending) and not running and not claude_runs
        if condition:
            self.consecutive += 1
        else:
            self.consecutive = 0
        # 真 idle = 无 working（含 claude run）/无 blocked/无 awaiting_approval。
        pending_idle = condition and not blocked and not stuck

        # 2.2.26 T63 空闲兜底计数：全仓安静（无 pending/无在跑/无卡住）且 helm
        # idle/done 才累计；helm working/blocked/缺失或出现待办/在跑即清零。
        # 排队 ⏸（paused）不进 pending、不打断安静期（由兜底报平安承载，AMEND）。
        idle_wake_cfg = guard_cfg["idleWakeRounds"]
        helm_agent = self._find_agent(agents, self.helm)
        helm_status = str(helm_agent.get("agent_status") or "") if helm_agent else ""
        idle_quiet = (
            not pending and not running and not claude_runs
            and not blocked and not stuck
            and helm_status in ("idle", "done")
        )
        if idle_quiet:
            self.idle_quiet_rounds += 1
            # 2.2.31 存活心跳（要件 6）：变化驱动下安静期无周期信号，guard.log
            # 周期行 + 看板 mtime（_periodic_write 每 300s）+ doctor pid 判活构成
            # 存活证明；只记日志，不叫醒。
            if (idle_wake_cfg == 0
                    and self.idle_quiet_rounds % IDLE_HEARTBEAT_EVERY_ROUNDS == 0):
                self.log(f"存活心跳：安静 {self.idle_quiet_rounds} 轮（约 "
                         f"{self.idle_quiet_rounds * self.interval // 60} 分钟），"
                         f"状态无变化，值守在线")
        else:
            self.idle_quiet_rounds = 0

        # —— 2.2.31 状态变化驱动（要件 1–3）：逐轮指纹 diff，基线落盘跨进程/
        # 重启可比较；首次（无 state）建基线不叫醒（防升级即叫）。
        # idleWakeRounds>0 旧口径：基线照常刷新但不维护补发队列（行为不变）。
        buckets = self._activity_buckets(pending, paused, working_keys,
                                         blocked, stuck, now)
        idle_state = self._read_idle_state()
        pending_notify: dict | None = None
        if idle_state is None:
            self._write_idle_state(buckets, None)
        elif idle_wake_cfg == 0:
            idle_changes = self._bucket_diff(idle_state.get("buckets") or {},
                                             buckets)
            if idle_changes:
                pending_notify = self._merge_notify(
                    idle_state.get("pendingNotify"), idle_changes)
            else:
                pending_notify = idle_state.get("pendingNotify")
            self._write_idle_state(buckets, pending_notify)
        else:
            self._write_idle_state(buckets, None)

        reason_parts: list[str] = []
        if blocked:
            names = ", ".join(
                str(agent.get("name") or agent.get("pane_id") or "?") for agent in blocked
            )
            reason_parts.append(f"worker 卡住（blocked）：{names}")
        if stuck:
            detail = ", ".join(
                f"{self._approval_label(item)}（callId {item.get('call_id') or '?'}，"
                f"{self._approval_age(item) // 60} 分钟）{self._call_id_note(item)}"
                for item in stuck
            )
            reason_parts.append(f"worker 等待审批（awaiting_approval）：{detail}")
        if not reason_parts:
            if fast_wake:
                reason_parts.append(
                    f"worker 完成待核收：{', '.join(sorted(finished))}"
                )
            elif pending_idle and self.consecutive >= 2:
                reason_parts.append(
                    f"有待办 {len(pending)} 条且无在跑 worker"
                    f"（连续 {self.consecutive} 轮）"
                )
        reason = "；".join(reason_parts)
        # T69②：worker ctx 扫描在 reason 定型后追加——与 blocked/stuck 并列可见
        # （不互挤，S0 S4 教训），纯 ctx 告警也能独立成 reason 触发叫醒。
        ctx_alerts = self._scan_worker_ctx(workers, now)
        if ctx_alerts:
            parts = []
            for item in ctx_alerts:
                text = f"{item['name']} {item['ratio']:.0%}"
                if item["ratio"] >= WORKER_CTX_REBUILD:
                    text += "（≥60%，建议关闭该工位并重建）"
                else:
                    text += "（>40%，请 /compact，compact 前按保真清单写全 "
                    "plan/progress/findings）"
                parts.append(text)
            reason_parts.append("worker ctx 超阈值：" + "；".join(parts))
            reason = "；".join(reason_parts)

        plan_path = pending[0].plan if pending else ""
        if plan_path and not Path(plan_path).is_absolute():
            plan_path = str((self.root / plan_path).resolve())
        result = {
            "pending": len(pending),
            "residual": len(residual),
            "running": len(running),
            "blocked": len(blocked),
            "blockedAges": self._blocked_ages(now, blocked, blocked_since),
            "stuck": self._stuck_payload(stuck),
            "consecutive": self.consecutive,
            "backlogPaused": len(paused),
            "idleQuietRounds": self.idle_quiet_rounds,
            "idleWake": False,
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
        if not blocked and not stuck:
            retro_reason = self._retro_due(running, handoff)
            if retro_reason:
                result["reason"] = retro_reason
                result["retro"] = True
                woke = False
                try:
                    self._wake(retro_reason, agents, now)
                    woke = True
                except Exception as exc:  # noqa: BLE001
                    self.log(f"复盘叫醒失败：{type(exc).__name__}: {exc}")
                if woke:
                    self._mark_retro_wake()
                    self.last_wake_at = now
                    result["woke"] = True
                return result
        # 2.2.26 T63 空闲兜底：安静期连续 idleWakeRounds 轮（默认 30≈60 分钟（T64，对齐每小时心跳口径））→
        # 报平安 + Backlog 全量清单（含 ⏸ 排队/⏸ 待外部条目，AMEND）；受全局冷却
        # 去重；触发后计数清零；总舵 working/blocked/缺失不累计（见上方计数逻辑）。
        if idle_wake_cfg > 0 and self.idle_quiet_rounds >= idle_wake_cfg:
            cooldown = guard_cfg["wakeCooldownMinutes"] * 60
            if (self.last_wake_at is not None
                    and now - self.last_wake_at < cooldown):
                self.log(f"空闲兜底叫醒冷却中，跳过（连续安静 "
                         f"{self.idle_quiet_rounds} 轮）")
                return result
            backlog_rows = list(pending) + list(paused)
            lines = [
                f"guard 空闲兜底：无待办且无在跑 worker，安静期已连续 "
                f"{self.idle_quiet_rounds} 轮（约 "
                f"{self.idle_quiet_rounds * self.interval // 60} 分钟），报平安。",
            ]
            if backlog_rows:
                lines.append("Backlog 清单（不含已完成，前 20 条）：")
                lines.extend(f"- {todo.text}" for todo in backlog_rows[:20])
                if len(backlog_rows) > 20:
                    lines.append(f"- …等 {len(backlog_rows)} 条")
            else:
                lines.append("Backlog 清单：无条目。")
            lines.append("如 Backlog 有可派/排队条目，请评估续派；否则无需动作。")
            lines.append(self._freshness_note())
            lines.append("请接手编排。")
            message = "\n".join(lines)
            try:
                self._wake(message, agents, now)
            except Exception as exc:  # noqa: BLE001
                self.log(f"空闲兜底叫醒失败：{type(exc).__name__}: {exc}")
            self.last_wake_at = now
            self.idle_quiet_rounds = 0
            result["woke"] = True
            result["idleWake"] = True
            result["idleQuietRounds"] = 0
            return result
        if not reason:
            if idle_wake_cfg == 0 and idle_quiet:
                # 2.2.31 状态变化兜底：安静期（idle_quiet，兜底本义语境）且本轮
                # 无既有叫醒理由时送达待补发增量（冷却内被吞的增量已持久化，
                # 此处即「冷却期满补发」轮次）。非安静期的 pending/worker/blocked/
                # 审批变化由既有通道承载，兜底不介入（不改变 2.2.30 已验收行为）。
                message = self._idle_change_wake(
                    agents, now, guard_cfg["wakeCooldownMinutes"] * 60,
                    pending_notify)
                if message is not None:
                    self._write_idle_state(buckets, None)  # 增量已送达，清队列
                    result["woke"] = True
                    result["idleChangeWake"] = True
                    return result
            return result
        cooldown = guard_cfg["wakeCooldownMinutes"] * 60
        blocked_due: list[dict] = []
        blocked_repeat_due = False
        if blocked or approval_due:
            if blocked:
                blocked_due, blocked_repeat_due = self._blocked_due(
                    now, blocked, guard_cfg["blockedRepeatMinutes"] * 60)
            if not blocked_due and not approval_due:
                self.log(f"卡住未到重复叫醒间隔，跳过叫醒：{reason}")
                return result
            # 首次叫醒受全局冷却；重复叫醒按每 agent 自限速，不受全局冷却压制。
            if (not blocked_repeat_due and not approval_repeat_due
                    and self.last_wake_at is not None
                    and now - self.last_wake_at < cooldown):
                self.log(f"冷却中，跳过叫醒：{reason}")
                return result
        else:
            if not (pending_idle or fast_wake or ctx_alerts):
                # 只有未到阈值的 awaiting_approval/blocked：本轮不叫醒。
                return result
            if self.last_wake_at is not None and now - self.last_wake_at < cooldown:
                # FB-057①：被冷却吞掉的完成边沿记日志（S0 S3；不补叫——prev 集合
                # 已更新，退化为慢通道/60 分钟兜底，不加持久化状态）。
                if fast_wake:
                    self.log(f"快速叫醒冷却中，完成边沿被吞（不补叫）：{reason}")
                else:
                    self.log(f"冷却中，跳过叫醒：{reason}")
                return result
            # C 周期心跳：pending 真 idle 持续时按配置间隔重复叫醒（受冷却约束）。
            # 快速叫醒是新的活动边沿，只受全局冷却，不被心跳间隔压制。
            heartbeat = guard_cfg["pendingHeartbeatMinutes"] * 60
            if (not (fast_wake or ctx_alerts) and self.last_pending_wake_at is not None
                    and now - self.last_pending_wake_at < heartbeat):
                self.log(f"pending 心跳未到间隔，跳过叫醒：{reason}")
                return result

        lines = [f"guard 叫醒：{reason}"]
        if blocked:
            lines.append("已 blocked 时长：")
            lines.extend(self._blocked_wake_lines(
                now, blocked, blocked_since,
                guard_cfg["blockedGraceMinutes"] * 60))
        if approval_due:
            lines.append("等待审批时长：")
            lines.extend(self._approval_wake_lines(approval_due, guard_cfg))
        if fast_wake:
            lines.append(f"刚完成待核收：{', '.join(sorted(finished))}")
            lines.append("请核收：查收对应工位 inbox 回执"
                         "（`crew-msg.py ls --unread`）与 pane 终态；工位处置按 F4。")
        if ctx_alerts:
            lines.append(COMPACT_CHECKLIST)
        if pending:
            lines.append(f"待办（前 {min(5, len(pending))} 条）：")
            lines.extend(f"- {todo.label}" for todo in pending[:5])
        lines.append(self._freshness_note())
        lines.append("请接手编排。")
        message = "\n".join(lines)
        try:
            self._wake(message, agents, now)
        except Exception as exc:  # noqa: BLE001
            self.log(f"叫醒失败：{type(exc).__name__}: {exc}")
        self.last_wake_at = now
        for agent in blocked_due:
            key = self._blocked_key(agent)
            if key:
                self.blocked_last_wake[key] = now
        for entry in approval_due:
            self.approval_last_wake[self._approval_key(entry)] = now
        if not blocked and not approval_due:
            self.last_pending_wake_at = now
        result["woke"] = True
        # 2.2.31：既有通道叫醒不清 paused/due 待补发增量——增量只由安静期兜底轮
        # 送达（既有叫醒消息不含盲区桶明细，提前清空会丢可见性）。
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
叫醒口径：有待办 + 无在跑 worker 连续 2 轮；完成差集边沿（FB-057①）——任一
worker 从 working 消失（探针排除）即叫醒核收（不要求全部转 idle/有待办；受
guard.wakeCooldownMinutes 全局冷却，默认 10 分钟，被吞边沿记 guard.log）；
blocked 与 awaiting_approval 单独叫醒；pending 真 idle 按
guard.pendingHeartbeatMinutes 心跳；
空闲兜底（2.2.26 T63，2.2.31 改版）：idleWakeRounds=0（默认）为状态变化驱动——
仅兜底盲区两桶（Backlog 暂停 ⏸/⛔ 与排队到期）逐轮集合 diff（指纹落
<crew-dir>/guard.state.json），安静期（idle_quiet）下变化 → 下一轮叫醒一次只含
增量；无变化零叫醒（guard.log 每 30 轮存活心跳行）；被冷却吞掉的增量持久化、
冷却期满补发一次；⏸ 排队条目「今天 ≥ 文本中 YYYY-MM-DD」视为变化一次性叫醒。
pending/在跑/blocked/审批变化打断安静期、由既有通道承载（不进指纹）。idleWakeRounds>0
恢复旧口径：安静期连续 N 轮报平安 + Backlog 清单（不含已完成；可派/⏸/⛔ 全列）；
Backlog ⏸ 两态：含外部条件语义（待 Captain/待 C/留 Captain/待拍板/待评审/待授权/
待窗口）→「⏸ 待外部」不计待办；其余 ⏸（排队语义）→「⏸ 排队」计入待办数据但不
触发即时叫醒（文本带阻塞列全文，供到期解析）；
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
首次叫醒沿用 guard.wakeCooldownMinutes（默认 10，夹取 1–240）全局冷却；仍 blocked
且距该 agent 上次 blocked 叫醒 ≥ guard.blockedRepeatMinutes（默认 15，夹取 1–240）
再叫醒，不受全局冷却压制；累计 ≥ guard.blockedGraceMinutes（默认 30，夹取 1–240）
时叫醒消息提示「按 R6 处置（re-task / close / escalate），应关闭工位或升级
Captain」；guard 不自动关闭/杀 agent。配置位于 .planning/orchestration.json。

等待审批（awaiting_approval）：按 callId 配对 qoder run 日志
`[permission-ask] prompt/settle`；未闭合且年龄 ≥ guard.approvalWaitMinutes
（默认 15，夹取 1–240）叫醒，重复间隔 guard.approvalRepeatMinutes（默认 30，
夹取 1–240），≥ guard.approvalGraceMinutes（默认 60，夹取 1–240）附 A/B/C 处置
建议，≥ guard.approvalMaxAgeMinutes（默认 240，夹取 1–1440）停止重复并记日志。
安全矩阵：helm 自身有未闭合审批时只写 guard.wake + 通知、不注入；绝不向等待审批
的 worker pane 注入任何文本/按键；不自动 close。--once --json 输出 stuck 列表与年龄。
周期心跳：pending 且真 idle（无 working/blocked/awaiting_approval）时按
guard.pendingHeartbeatMinutes（默认 15，夹取 1–240）重复叫醒，受全局冷却约束；
与 blocked/awaiting_approval 同轮只发一条叫醒。

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
FB-045 反查：交接操作前以 oldPane 反查 pane/tab（pane list 权威、agent list 兜底）；
oldTab 缺失/不一致时以反查为准关闭并写回 handoff（tabCheck 字段记
mismatch / missing_handoff_tab），反查不到（pane/agent 消失）不按未验证 oldTab
操作——只关确认存在的 pane 或只报告。
残留回收：phase=done 后若 handoff old agent 仍在且非现役总舵，仅当 idle/done、
pane/tab 非现役、非聚焦、非白名单时经 dispatch 关 pane（tab 无剩余 pane 再关 tab）；
其余只记日志；每份 handoff 只尝试/记录一次（handoff reclaim 标记，重启不重复）。
周期回收（D-7）：每 REAPER_EVERY_ROUNDS（默认 15）轮调用
`crew-reaper.py --apply --json`；脚本不存在/失败只记 guard.log，不阻断值守。

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
                             "（crewDir/planPath/pending/workers/claudeRuns/stuck/"
                             "wake/handoff/rework）")
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
                    "stuck": result.get("stuck", []),
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
