#!/usr/bin/env python3
"""从 .planning/*/task_plan.md 派生进度看板（只读 .planning，标准库即可）。

用法:
    python3 scripts/plan-status.py            # 打印 Markdown 看板
    python3 scripts/plan-status.py --write    # 同时写入看板文件（默认 docs/进度追踪.md，可配置）
    python3 scripts/plan-status.py --selftest # 对当前 plan 跑一遍解析断言
    python3 scripts/plan-status.py --archive docs/历史/进度追踪-截至20260101.md --write
                                              # 一次性在看板头部写入归档链接（显式动作）

配置：`<仓根>/.planning/orchestration.json`（单一参数载体，键见 templates/project.config.json）。
可配置项：board.path / board.title / board.archive（空=不写归档链接行）/
portfolioMatch（portfolio 目录识别 glob）/ backlog.captainPrefix、agentPrefix（T/C 前缀分流）/
backlog.captainBlocks（T→C 阻塞映射）。
**兼容性**：配置文件不存在时全部取内置默认值；ROOT 解析顺序 = --root 参数
> 从 cwd 向上找含 .planning 的目录 > 脚本所在目录的上一级。

真源是各 plan 的 `### Phase N: Name` + `**Status:**`；本脚本不写 .planning。
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$")
STATUS_RE = re.compile(r"\*\*Status:\*\*\s+(pending|in_progress|complete)")
PLAN_REF_RE = re.compile(r"^\s*-\s*plan:\s*`([^`]+)`")

HEALTH_DONE, HEALTH_WIP, HEALTH_BLOCKED = "🟢", "🟡", "🔴"

# 内置默认 = 通用默认值（与 templates/project.config.json 保持一致；两者同源同改）。
DEFAULT_CONFIG: dict = {
    "portfolioDir": ".planning/portfolio",
    "board": {"path": "docs/进度追踪.md", "title": "进度追踪（派生看板）", "archive": ""},
    "portfolioMatch": "*portfolio*",
    "backlog": {"captainPrefix": "C", "agentPrefix": "T", "captainBlocks": {}},
}

STREAM_TABLE_HEADER = "| 流 | plan | 当前阶段 | 阶段完成 | 健康 | 该流 Next Step |"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(root: Path) -> dict:
    """读 <root>/.planning/orchestration.json；缺失或损坏时用内置默认（零配置可运行）。"""
    path = root / ".planning" / "orchestration.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        return DEFAULT_CONFIG
    except ValueError:
        # 建议10：损坏配置显式警告（stderr，不污染看板输出），不再静默降级
        print(f"[警告] 配置文件损坏（{path}），已用内置默认值；请修复或删除后重跑。",
              file=sys.stderr)
        return DEFAULT_CONFIG
    if not isinstance(data, dict):
        print(f"[警告] 配置文件格式异常（{path}，应为 JSON 对象），已用内置默认值；请修复后重跑。",
              file=sys.stderr)
        return DEFAULT_CONFIG
    return _deep_merge(DEFAULT_CONFIG, data)


def resolve_root(explicit: str | None) -> Path:
    """--root 参数 > 从 cwd 向上找含 .planning 的目录 > 脚本目录的上一级。"""
    if explicit:
        return Path(explicit).resolve()
    cur = Path.cwd().resolve()
    for candidate in [cur, *cur.parents]:
        if (candidate / ".planning").is_dir():
            return candidate
    return SCRIPT_ROOT


@dataclass
class Plan:
    path: Path
    root: Path
    title: str = ""
    phases: list[tuple[str, str]] = field(default_factory=list)
    sections: dict[str, list[str]] = field(default_factory=dict)
    deviations: int = 0

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    @property
    def complete(self) -> int:
        return sum(1 for _, s in self.phases if s == "complete")

    @property
    def total(self) -> int:
        return len(self.phases)

    @property
    def health(self) -> str:
        if self.deviations or "blocked" in self.path.read_text(encoding="utf-8").lower():
            return HEALTH_BLOCKED
        if self.total and self.complete == self.total:
            return HEALTH_DONE
        return HEALTH_WIP


def _tables(lines: list[str]) -> list[list[str]]:
    """把一段 Markdown 里的数据行（跳过分隔行与表头）拆成单元格列表。"""
    rows, seen_header = [], False
    for line in lines:
        text = line.strip()
        if not (text.startswith("|") and text.endswith("|")):
            continue
        cells = [c.strip() for c in text.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if not seen_header:
            seen_header = True
            continue
        rows.append(cells)
    return rows


def parse_plan(path: Path, root: Path) -> Plan:
    plan = Plan(path=path, root=root)
    section, phase = None, None
    in_comment = False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        if in_comment:
            # 多行 HTML 注释区间内整段跳过（建议5：防注释中段 **Status:** 污染解析）
            if "-->" in line:
                in_comment = False
            continue
        if line.strip().startswith("<!--"):
            # HTML 注释不解析（防 plan 骨架模板/示例里的 **Status:** 字样干扰）；跨行注释读到闭合
            if "-->" not in line:
                in_comment = True
            continue
        if line.startswith("# ") and not plan.title:
            plan.title = line[2:].strip()
            continue
        if line.startswith("## "):
            section, phase = line[3:].strip(), None
            plan.sections.setdefault(section, [])
            continue
        phase_head = PHASE_RE.match(line)
        if phase_head:
            phase = phase_head.group(1)
            plan.phases.append((phase, "pending"))
            continue
        status = STATUS_RE.search(line)
        if status and phase is not None:
            plan.phases[-1] = (phase, status.group(1))
            continue
        if section is not None:
            plan.sections[section].append(line)
    deviation = plan.sections.get("Deviation") or plan.sections.get("Errors Encountered", [])
    plan.deviations = len(_tables(deviation)) if "Deviation" in plan.sections else 0
    return plan


def load_plans(root: Path) -> list[Plan]:
    return [parse_plan(p, root) for p in sorted((root / ".planning").glob("*/task_plan.md"))]


def _section(plan: Plan, name: str) -> str:
    return " ".join(line.strip() for line in plan.sections.get(name, []) if line.strip())


def current_phase(plan: Plan) -> str:
    declared = _section(plan, "Current Phase")
    if declared:
        return declared
    return next((name for name, status in plan.phases if status == "in_progress"), "—")


def cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip() or "—"


def render(plans: list[Plan], cfg: dict, archive: str | None = None) -> str:
    root = plans[0].root if plans else SCRIPT_ROOT
    pattern = cfg["portfolioMatch"]
    portfolio = next(
        (p for p in plans if fnmatch.fnmatch(p.path.parent.name, pattern)), None
    )
    blocks: dict[str, str] = cfg["backlog"].get("captainBlocks") or {}
    cap_prefix, agt_prefix = cfg["backlog"]["captainPrefix"], cfg["backlog"]["agentPrefix"]
    title = cfg["board"]["title"]
    archive = cfg["board"]["archive"] if archive is None else archive

    index = {p.rel: p for p in plans}
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    out: list[str] = [
        f"# {title}",
        "",
        f"> **自动生成，勿手改**（`python3 scripts/plan-status.py --write`，生成于 {now}）。",
        "> 唯一真源：`.planning/*/task_plan.md`。",
    ]
    if archive:
        out.append(f"> 历史全文见 `{archive}`。")
    out.append("")
    if portfolio is None:
        out += [f"> 未找到 portfolio plan（`.planning/{pattern}/task_plan.md`），看板不完整。", ""]
        return "\n".join(out) + "\n"

    rows, streams = [], []
    for phase, lines in _iter_streams(portfolio):
        ref = next((PLAN_REF_RE.match(l).group(1) for l in lines if PLAN_REF_RE.match(l)), "")
        target = index.get(ref.split("#")[0])
        if target:
            done, health = f"{target.complete}/{target.total}", target.health
            now_at, next_step = current_phase(target), _section(target, "Next Step")
        else:
            done, health = "—（Backlog 承载）", HEALTH_BLOCKED if portfolio.deviations else HEALTH_WIP
            now_at, next_step = "—", "—"
        streams.append((phase, target, next_step))
        rows.append(
            f"| {cell(phase)} | `{cell(ref or '—')}` | {cell(now_at)} | {done} | {health} "
            f"| {cell(next_step)} |"
        )

    out += ["## 当前目标", "", _section(portfolio, "Goal") or "—", "", "## 总舵 Next Step", ""]
    top = _section(portfolio, "Next Step")
    if top:
        out.append(top)
    else:
        # 回退：逐流列出仍在推进的流，不合并（stream 无语义 P 字段，P0 以"仍有未完成阶段"近似）。
        derived = [s for s in streams if s[1] and s[1].complete < s[1].total and s[2]]
        out += [f"- 自动推导：{name} — {next_step}" for name, _, next_step in derived] or ["—"]

    out += ["", "## 流表", "", STREAM_TABLE_HEADER, "|---|---|---|---|---|---|"]
    out += rows

    out += ["", "## 偏差明细", ""]
    deviations = _tables(portfolio.sections.get("Deviation", []))
    backlog = _tables(portfolio.sections.get("Backlog", []))
    captain_ids = {row[0] for row in backlog if len(row) >= 5 and row[0].startswith(cap_prefix)}
    if deviations:
        out += ["| 预期 | 实际 | 原因 | 待决策 |", "|---|---|---|---|"]
        for row in deviations:
            if len(row) >= 4 and row[3].split()[0] in captain_ids:
                row = row[:3] + [f"见 {row[3].split()[0]}"]
            out.append("| " + " | ".join(cell(c) for c in row) + " |")
    else:
        out.append("—")

    out += ["", "## Backlog（agent 可执行）", ""]
    todo = [row for row in backlog if len(row) >= 5 and row[0].startswith(agt_prefix)]
    if todo:
        out += ["| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
        for row in todo:
            if row[0] in blocks:
                row = row[:4] + [f"待 {blocks[row[0]]}"]
            out.append("| " + " | ".join(cell(c) for c in row) + " |")
    else:
        out.append("—")

    out += ["", "## 待 Captain（人工决策/动作）", ""]
    pending = [row for row in backlog if row[0] in captain_ids]
    if pending:
        out += [f"- **{row[0]}** {row[1]} — {row[4]}" for row in pending]
    else:
        out.append("—")
    return "\n".join(out) + "\n"


def _iter_streams(portfolio: Plan):
    """把 portfolio 的 Phase 还原为（流名, 该 Phase 的正文行）。"""
    current, buf = None, []
    for line in portfolio.path.read_text(encoding="utf-8").splitlines():
        head = PHASE_RE.match(line)
        if head:
            if current:
                yield current, buf
            current, buf = head.group(1), []
        elif line.startswith("## ") and current:
            yield current, buf
            current, buf = None, []
        elif current is not None:
            buf.append(line)
    if current:
        yield current, buf


def selftest(plans: list[Plan], cfg: dict) -> None:
    assert plans, "没有找到任何 plan"
    assert all(p.total for p in plans), "存在没有 Phase 的 plan"
    assert all(s in ("pending", "in_progress", "complete") for p in plans for _, s in p.phases)
    board = render(plans, cfg)
    assert "## 流表" in board and "## Backlog" in board and "## 待 Captain" in board
    assert STREAM_TABLE_HEADER in board
    cap, agt = cfg["backlog"]["captainPrefix"], cfg["backlog"]["agentPrefix"]
    backlog, captain = board.split("## Backlog", 1)[1].split("## 待 Captain", 1)
    backlog_full = re.findall(rf"^\| ([{cap}{agt}]+\d+) \|", backlog, re.M)
    captain_ids = re.findall(rf"^- \*\*([{cap}{agt}]+\d+)\*\*", captain, re.M)
    # 空表=无混入即通过（阻断1：init 全新产物仓 Backlog 为空，selftest 必须首跑即绿；
    # 不在骨架预置示例数据行——那会进真实看板）
    assert (not backlog_full) or all(i.startswith(agt) for i in backlog_full), "Backlog 混入 Captain 项"
    assert (not captain_ids) or all(i.startswith(cap) for i in captain_ids), "待 Captain 混入 agent 项"
    print(f"selftest ok: {len(plans)} plans, {sum(p.total for p in plans)} phases")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 .planning/*/task_plan.md 派生 Markdown 进度看板（只读 .planning）。"
    )
    parser.add_argument("--write", action="store_true", help="写入看板文件（路径读配置 board.path）")
    parser.add_argument("--selftest", action="store_true", help="对当前 plan 跑解析断言")
    parser.add_argument("--root", help="目标仓根目录（默认：cwd 向上找 .planning）")
    parser.add_argument(
        "--archive",
        help="一次性在看板头部写入归档链接（显式动作，不改配置；配置 board.archive 为空时不写归档行）",
    )
    args = parser.parse_args(argv)

    root = resolve_root(args.root)
    cfg = load_config(root)
    plans = load_plans(root)
    if args.selftest:
        selftest(plans, cfg)
        return 0

    board = render(plans, cfg, archive=args.archive)
    if args.write:
        target = root / cfg["board"]["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(board, encoding="utf-8")
        print(f"写入 {target.relative_to(root).as_posix()}（{len(board.splitlines())} 行）")
    else:
        sys.stdout.write(board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
