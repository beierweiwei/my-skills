#!/usr/bin/env python3
"""编排值守（guard）：待办存在且无在跑 worker 时，连续 2 轮后叫醒总舵。

正常路径零 stdout；事件与异常写 <crew-dir>/guard.log。
单实例锁：<crew-dir>/guard.lock（第二实例静默退出）。
叫醒：优先 `herdr agent prompt <helm>`；总舵 pane 处于聚焦时改为写
<crew-dir>/guard.wake + Herdr 通知。每 5 分钟顺带执行 plan-status.py --write；
叫醒时执行 plan-status.py --check-fresh 并把结果带进叫醒消息。

待办口径（decisions §1）：
- 活跃流 plan 中 Phase Status ≠ complete 的未勾选 `[ ]`（排除含 ⏸ / 待 Captain / 留 Captain）；
- portfolio Backlog 中 T 行且阻塞列无 待 C / blocked；
- 已完成 Phase 的残留 `[ ]` 只记日志，不叫醒。
在跑口径：Herdr agent_status == working，排除总舵自身与 Captain*；blocked 单独叫醒。

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$")
STATUS_RE = re.compile(r"\*\*Status:\*\*\s+(pending|in[ _]progress|complete)")
TODO_RE = re.compile(r"^\s*-\s*\[\s\]\s*(.+)$")
PAUSE_MARKS = ("⏸", "待 Captain", "留 Captain")
COOLDOWN_SECONDS = 600
WRITE_INTERVAL_SECONDS = 300


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


def load_config(root: Path) -> dict:
    config = {"portfolioDir": ".planning/portfolio", "backlog": {"agentPrefix": "T"}}
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
        _parse_plan_todos(path, rel, pending, residual)
        if path.resolve() == portfolio:
            _parse_backlog(root, path, rel, pending)
    return pending, residual


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


def herdr_notify(title: str, body: str) -> bool:
    if os.environ.get("HERDR_ENV") != "1":
        return False
    try:
        subprocess.run(
            ["herdr", "notification", "show", title, "--body", body],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def run_plan_status(root: Path, flag: str) -> tuple[int, str]:
    script = Path(__file__).resolve().parent / "plan-status.py"
    proc = subprocess.run(
        [sys.executable, str(script), flag, "--root", str(root)],
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def acquire_lock(crew_dir: Path):
    """单实例锁；拿不到返回 None（第二实例静默退出）。"""
    crew_dir.mkdir(parents=True, exist_ok=True)
    handle = (crew_dir / "guard.lock").open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
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


@dataclass
class Guard:
    root: Path
    crew_dir: Path
    helm: str
    interval: int = 120
    clock: Callable[[], float] = time.time
    list_agents: Callable[[], list[dict]] = herdr_agents
    prompt: Callable[[str, str], bool] = herdr_prompt
    notify: Callable[[str, str], bool] = herdr_notify
    run_status: Callable[[Path, str], tuple[int, str]] = run_plan_status
    log_fn: Callable[[str], None] | None = None
    consecutive: int = field(default=0, init=False)
    last_wake_at: float | None = field(default=None, init=False)
    last_write_at: float | None = field(default=None, init=False)

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

    def _excluded(self, agent: dict) -> bool:
        name = str(agent.get("name") or "")
        return name == self.helm or name.startswith("Captain")

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
            self.crew_dir / "guard.wake",
            f"{utc_now()}\n{message}\n",
        )

    def _wake(self, message: str, agents: list[dict]) -> str:
        helm_agent = next(
            (agent for agent in agents if str(agent.get("name") or "") == self.helm),
            None,
        )
        if helm_agent is not None and helm_agent.get("focused"):
            self._write_wake_file(message)
            try:
                self.notify("guard 叫醒", message)
            except Exception as exc:  # noqa: BLE001
                self.log(f"guard 通知失败：{exc}")
            self.log(f"叫醒（总舵 pane 聚焦，写 guard.wake）：{message.splitlines()[0]}")
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
        self._periodic_write(now)

        try:
            agents = self.list_agents()
        except Exception as exc:  # noqa: BLE001 - Herdr 故障时不误判“无在跑”
            self.log(f"herdr agent list 失败：{type(exc).__name__}: {exc}")
            return {"error": str(exc), "woke": False}

        workers = [agent for agent in agents if not self._excluded(agent)]
        running = [
            agent for agent in workers if str(agent.get("agent_status")) == "working"
        ]
        blocked = [
            agent for agent in workers if str(agent.get("agent_status")) == "blocked"
        ]
        pending, residual = scan_todos(self.root)

        for todo in residual:
            self.log(f"已完成 Phase 残留 [ ]（仅记日志）：{todo.label}")

        condition = bool(pending) and not running
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
            "consecutive": self.consecutive,
            "woke": False,
            "reason": reason,
            "planPath": plan_path,
            "workers": [
                {"name": str(agent.get("name") or ""),
                 "status": str(agent.get("agent_status") or "")}
                for agent in workers
            ],
        }
        if not reason:
            return result
        if self.last_wake_at is not None and now - self.last_wake_at < COOLDOWN_SECONDS:
            self.log(f"冷却中，跳过叫醒：{reason}")
            return result

        lines = [f"guard 叫醒：{reason}"]
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
叫醒口径：有待办 + 无在跑 worker 连续 2 轮；冷却 10 分钟；blocked 单独叫醒；
排除总舵自身与 Captain*（按 agent 名）。优先 herdr agent prompt <helm>；总舵 pane
聚焦时写 <crew-dir>/guard.wake + 通知。叫醒时执行 plan-status.py --check-fresh；
每 5 分钟顺带执行 plan-status.py --write。正常零 stdout，事件写 <crew-dir>/guard.log。

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
                        help="配合 --once 输出 JSON（crewDir/planPath/pending/workers/wake）")
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
        helm=resolve_helm_name(root, args.helm),
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
                    "wake": bool(result.get("woke")),
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
