#!/usr/bin/env python3
"""从 .planning/*/task_plan.md 派生进度看板（只读 .planning，标准库即可）。

用法:
    python3 scripts/plan-status.py            # 打印 Markdown 看板
    python3 scripts/plan-status.py --write    # 同时写入看板文件（默认 docs/进度追踪.md，可配置）
    python3 scripts/plan-status.py --check-fresh
                                              # 校验看板是否落后于 plan/配置（过期列明来源并非零退出）
    python3 scripts/plan-status.py --lint     # create-time lint（Phase 标题/单 Status/Next Step/裸编号）
    python3 scripts/plan-status.py --playbooks <path>
                                              # 剧本库结构校验：path 为文件或目录（目录取 *.md、
                                              # 跳过 README.md）；每剧本 ≥1 个 ### Phase N:，
                                              # 每 Phase 恰一行 **Status:** pending|in_progress|complete
    python3 scripts/plan-status.py --selftest # 对当前 plan 跑一遍解析断言
    python3 scripts/plan-status.py --archive docs/历史/进度追踪-截至20260101.md --write
                                              # 一次性在看板头部写入归档链接（显式动作）

刷新时机：收口回执后立即 --write；guard 每 5 分钟 --write；
交接第 0 步 --write 后接 --check-fresh。

配置：`<仓根>/.planning/orchestration.json`（单一参数载体，键见 templates/project.config.json）。
可配置项：board.path / board.title / board.archive（空=不写归档链接行）/
portfolioMatch（portfolio 目录识别 glob）/ backlog.captainPrefix、agentPrefix（T/C 前缀分流，
S8-hotfix 起分流互斥口径：C 前缀 OR 行内含 ⏸ 标记 → 待 Captain 汇总（⏸ 语义优先于前缀），
其余 T/N 等非 ⏸ → Backlog）/
backlog.captainBlocks（T→C 阻塞映射）。
**兼容性**：配置文件不存在时全部取内置默认值；ROOT 解析顺序 = --root 参数
> 从 cwd 向上找含 .planning 的目录 > 脚本所在目录的上一级。

真源是各 plan 的 `### Phase N: Name` + `**Status:**`；本脚本不写 .planning。

剧本库校验（--playbooks）复用同一 Phase/Status 解析：Phase 必须位于 `## Phases`
节内且每剧本至少 1 个合法 `### Phase N:`；每 Phase 恰一行
`**Status:** pending|in_progress|complete`；Phase 条目只允许 `- [ ]` / `- [x]` /
`- owner:` / `- plan:`；错误按 `rel:line: [rule] message` 输出（rc 1），路径不存在 rc 3。

看板布局（S8-b1，AGENTS 编排效率原则 11 增补「写法四规则」+ Captain 2026-10-30 渲染细化）：
业务链总览与待 Captain 置顶（业务主线+人工决策先行），当前目标/总舵 Next Step 居中，
流表只留概览列（流/当前阶段/阶段完成/健康），各流 todo 细节在流表下方按流分节
（#### 流名 + 行首 todo 列表），偏差明细与 Backlog 置后。
todo 条目一律标准 md 语法：行首 `- [x]` / `- [ ]`；待 Captain 项 `- [ ] ⏸ 文字（待 Captain）`
（⏸ 入文字，保持 checkbox 标准性）；状态灯口径 [x]✅ / [ ]🟡 / ⏸🔴。
各流 plan 的 `## 业务链…` 节（节名含「业务链」即命中）下 | 节点 | 状态 | 说明 | 表
解析为业务链节点（状态列 todo 三元，链序=表序）；无该节的 plan 不渲染（零破坏）。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent

PHASE_RE = re.compile(r"^### Phase\s+\d+\s*[:：]\s*(.+?)\s*$")
STATUS_RE = re.compile(r"\*\*Status:\*\*\s+(pending|in_progress|complete)")
STATUS_MARK_RE = re.compile(r"\*\*Status:\s*\*\*")
PLAN_REF_RE = re.compile(r"^\s*-\s*plan:\s*`([^`]+)`")
# 派生看板源指纹（--check-fresh）：写入时把各 plan 内容哈希与配置哈希藏进 HTML 注释。
BOARD_VERSION = "v1"
FINGERPRINT_RE = re.compile(r"^<!--\s*plan-sources:\s*(.*?)\s*-->$")
CONFIG_REL = ".planning/orchestration.json"
WRITE_HINT = "请运行 `python3 scripts/plan-status.py --write` 重刷看板"

HEALTH_DONE, HEALTH_WIP, HEALTH_BLOCKED = "🟢", "🟡", "🔴"
# 看板「当前阶段」自动推导（S7-b1 缺陷1）：一律取 Phases 中首个 Status≠complete 的阶段名，
# 活跃期手工 ## Current Phase 不参与显示（防手工滞后误导）；全部 complete 时回落手工值作收口说明。
ALL_DONE_MARK = "全部完成"
# Deviation 生命周期（S7-b1 缺陷2）：行内任意列含「已解决」即视为已解决条目（与文档口径
# 「待决策列追加 已解决（YYYY-MM-DD）」一致——宽松子串匹配，覆盖文档定稿格式）；
# 看板默认只显示活跃行，节尾注折叠计数；health 偏差计数同步只数活跃行。
RESOLVED_MARK = "已解决"

# 业务链视图（S8-b1）：状态灯口径（Captain 2026-10-30 细化）[x]✅ / [ ]🟡 / ⏸🔴；
# todo 条目一律标准 md 语法（行首 - [x] / - [ ]；待 Captain 项 - [ ] ⏸ 文字（待 Captain））。
CHAIN_LEGEND = "> 图例：[x] ✅ 完成 · [ ] 🟡 未完成 · ⏸ 🔴 待 Captain"
LIGHT_DONE, LIGHT_TODO, LIGHT_CAPTAIN = "✅", "🟡", "⏸"

# 内置默认 = 通用默认值（与 templates/project.config.json 保持一致；两者同源同改）。
DEFAULT_CONFIG: dict = {
    "portfolioDir": ".planning/portfolio",
    "board": {"path": "docs/进度追踪.md", "title": "进度追踪（派生看板）", "archive": ""},
    "portfolioMatch": "*portfolio*",
    "backlog": {"captainPrefix": "C", "agentPrefix": "T", "captainBlocks": {}},
}

# 流表只留概览列（S8-b1：Markdown 表格单元格不换行、不塞多条 todo）；细节按流分节渲染。
STREAM_TABLE_HEADER = "| 流 | 当前阶段 | 阶段完成 | 健康 |"


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
    chain: list[tuple[str, str, str]] = field(default_factory=list)  # (节点, done|todo|captain, 说明)
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


def _row_resolved(row: list[str]) -> bool:
    """Deviation 行内任意列含「已解决」即命中（宽松子串，兼容文档定稿的已解决（YYYY-MM-DD））。"""
    return any(RESOLVED_MARK in c for c in row)


def _row_has_pause(row: list[str]) -> bool:
    """Backlog 行内含 ⏸ 标记（[⏸] / [ ] ⏸ / 裸 ⏸ 均算）→ 待 Captain 语义（S8-hotfix）。
    ⏸ 语义优先于前缀：C+⏸ 仍 captain、T/N+⏸ 也 captain；分流互斥（⏸ 行不留在 Backlog 表），
    其余（T/N 等非 ⏸）→ Backlog。"""
    return "⏸" in " ".join(row)


def _todo_mark(text: str) -> str:
    """业务链状态列 todo 三元判定：[⏸] 或裸 ⏸（b2 文档定稿单值简写，S8R B1-fix）→captain、
    [x]/[X]→done、其余（含 [ ] 与非标写法）→todo。第三态两形态等价；
    裸 ⏸ 仅整格单值时命中（strip 相等），说明列/混合文本不参与判定故无误伤。"""
    if "[⏸]" in text or text.strip() == "⏸":
        return "captain"
    if "[x]" in text.lower():
        return "done"
    return "todo"


def _parse_chain(sections: dict[str, list[str]]) -> list[tuple[str, str, str]]:
    """业务链表解析（S8-b1）：节名含「业务链」即命中（兼容「业务链节点（…）」带后缀形态）；
    列按位置取（节点/状态/说明），列头名不参与解析（「说明」/「目标完成状态说明」均兼容）；
    状态列为准（说明列内嵌标记不翻转节点状态），缺说明列记空串，链序=表序。"""
    for name, lines in sections.items():
        if "业务链" not in name:
            continue
        chain = []
        for row in _tables(lines):
            if len(row) < 2:
                continue
            chain.append((row[0], _todo_mark(row[1]), row[2] if len(row) >= 3 else ""))
        return chain
    return []


def _todo_lights(text: str) -> str:
    """文本内 todo 三元标记转状态灯（[x]✅ / [ ]🟡 / [⏸]⏸），与图例口径一致。"""
    return (text.replace("[⏸]", LIGHT_CAPTAIN)
                .replace("[x]", LIGHT_DONE)
                .replace("[X]", LIGHT_DONE)
                .replace("[ ]", LIGHT_TODO))


def _cap_suffix(text: str) -> str:
    """待 Captain 项后缀：文字已含「待 Captain / 待 C」指引则不重复补注。"""
    return "" if ("待 Captain" in text or "待 C" in text) else "（待 Captain）"


def _todo_line(line: str) -> str:
    """todo 行转标准 md 语法：行首 - [x] / - [ ]；- [⏸] 转 - [ ] ⏸ 文字（待 Captain）；
    非 todo 行原样（兼容超集）。"""
    m = re.match(r"^-\s*\[(x|X| |⏸)\]\s*(.+)$", line.strip())
    if not m:
        return line.strip()
    mark, body = m.group(1), m.group(2).strip()
    if mark in ("x", "X"):
        return f"- [x] {body}"
    if mark == "⏸":
        return f"- [ ] ⏸ {body}{_cap_suffix(body)}"
    return f"- [ ] {body}"


def _next_step_lines(plan: Plan) -> list[str]:
    """流 plan Next Step 节的非空行（保持行序，供分节 todo 渲染）。"""
    return [l.strip() for l in plan.sections.get("Next Step", []) if l.strip()]


def _captain_lines(plans: list[Plan], portfolio: Plan, cap_prefix: str,
                   stream_names: dict[str, str]) -> list[str]:
    """待 Captain 汇总（S8-b1）：portfolio Backlog C 前缀行 + 各 plan Backlog [⏸] 行 +
    各流业务链表状态列 [⏸] 节点；标准 todo 语法（- [ ] ⏸ 文字），⏸ 入文字；
    流标注=流名优先（stream_names：plan 相对路径→portfolio 流名），plan title 兜底。"""
    lines = []
    for row in _tables(portfolio.sections.get("Backlog", [])):
        if len(row) >= 5 and row[0].startswith(cap_prefix):
            lines.append(f"- [ ] ⏸ **{row[0]}** {row[1]} — {row[4]}{_cap_suffix(row[4])}")
    for p in plans:
        label = stream_names.get(p.rel) or p.title
        for row in _tables(p.sections.get("Backlog", [])):
            if len(row) < 2 or not _row_has_pause(row):
                continue
            if p is portfolio and len(row) >= 5 and row[0].startswith(cap_prefix):
                continue  # 已在上面 C 汇总，不重复
            lines.append(
                f"- [ ] ⏸ **{cell(row[0])}**（{label}）{_todo_lights(cell(row[1]))}"
                f" — {_todo_lights(cell(row[-1]))}{_cap_suffix(row[-1])}"
            )
        for node, mark, desc in p.chain:
            if mark != "captain":
                continue
            body = f"— {_todo_lights(cell(desc))}" if desc else ""
            lines.append(f"- [ ] ⏸ **{cell(node)}**（{label}）{body}"
                         + (_cap_suffix(desc) if desc else ""))
    return lines


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
    plan.chain = _parse_chain(plan.sections)
    if "Deviation" in plan.sections:
        # health 偏差计数只数活跃行（已解决条目不再让流表健康变 🔴）
        plan.deviations = sum(1 for r in _tables(plan.sections["Deviation"]) if not _row_resolved(r))
    return plan


def load_plans(root: Path) -> list[Plan]:
    return [parse_plan(p, root) for p in sorted((root / ".planning").glob("*/task_plan.md"))]


# —— --check-fresh：看板源指纹（plan 内容哈希 + 配置哈希 + 生成器版本）——
# 新鲜度以内容指纹为准：只 touch 不改内容的 plan 不误报；看板缺指纹（旧版生成/手工替换）视为过期，
# 此时用 mtime 兜底列出晚于看板的 plan，保证「哪个 plan 更新」可定位。

def _sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def _fingerprint_parts(plans: list[Plan], cfg: dict) -> tuple[str, str, dict[str, str]]:
    cfg_hash = _sha12(json.dumps(cfg, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    sources = {p.rel: _sha12(p.path.read_bytes()) for p in plans}
    return BOARD_VERSION, cfg_hash, sources


def fingerprint_line(plans: list[Plan], cfg: dict) -> str:
    version, cfg_hash, sources = _fingerprint_parts(plans, cfg)
    parts = [f"{version} {cfg_hash}"]
    parts += [f"{digest} {rel}" for rel, digest in sorted(sources.items())]
    return "<!-- plan-sources: " + " | ".join(parts) + " -->"


def parse_fingerprint(board_text: str) -> tuple[str, str, dict[str, str]] | None:
    for line in board_text.splitlines():
        m = FINGERPRINT_RE.match(line.strip())
        if not m:
            continue
        parts = [part for part in m.group(1).split(" | ") if part]
        head = parts[0].split(" ", 1) if parts else []
        if len(head) != 2:
            return None
        sources = {}
        for part in parts[1:]:
            digest, _, rel = part.partition(" ")
            if not rel:
                return None
            sources[rel] = digest
        return head[0], head[1], sources
    return None


def check_fresh(root: Path, cfg: dict, plans: list[Plan]) -> int:
    """校验派生看板是否反映当前 plan/配置；过期时列明哪个源已变化并非零退出。"""
    board_path = root / cfg["board"]["path"]
    try:
        board_rel = board_path.relative_to(root).as_posix()
    except ValueError:
        board_rel = str(board_path)
    if not board_path.is_file():
        print(f"[过期] 看板不存在：{board_rel}；{WRITE_HINT}。", file=sys.stderr)
        return 1
    embedded = parse_fingerprint(board_path.read_text(encoding="utf-8"))
    version, cfg_hash, sources = _fingerprint_parts(plans, cfg)
    if embedded is None:
        print(f"[过期] 看板缺少源指纹（旧版生成或被手工替换），无法校验；{WRITE_HINT}。",
              file=sys.stderr)
        board_mtime = board_path.stat().st_mtime
        for plan in plans:
            if plan.path.stat().st_mtime > board_mtime:
                print(f"  - {plan.rel}（mtime 晚于看板）", file=sys.stderr)
        return 1
    old_version, old_cfg_hash, old_sources = embedded
    issues = []
    if old_version != version:
        issues.append(f"看板生成器版本变化（{old_version} → {version}）")
    if old_cfg_hash != cfg_hash:
        issues.append(f"配置已更新：{CONFIG_REL}")
    for rel, digest in sorted(sources.items()):
        if rel not in old_sources:
            issues.append(f"{rel}（新增 plan）")
        elif old_sources[rel] != digest:
            issues.append(f"{rel}（plan 已更新）")
    for rel in sorted(old_sources):
        if rel not in sources:
            issues.append(f"{rel}（plan 已移除）")
    if issues:
        print("[过期] 派生看板落后于源，以下内容已变化：", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        print(f"{WRITE_HINT}。", file=sys.stderr)
        return 1
    print(f"看板新鲜：{board_rel} 与 {len(sources)} 个 plan 一致。")
    return 0


# —— create-time lint（--lint）：只报结构问题，不改 plan ——
# 规则：### Phase N: 标题格式 / 每 Phase 恰一行 **Status:** / Next Step ≤10 条 /
#       Phase 项为 - [ ]|- [x] 形式 / 禁止裸编号（编号后无任务描述）；HTML 注释整段跳过。

@dataclass
class LintIssue:
    rel: str
    line: int
    rule: str
    message: str

    def format(self) -> str:
        return f"{self.rel}:{self.line}: [{self.rule}] {self.message}"


BARE_ID_RE = re.compile(r"^(?:[A-Za-z]{1,3}\d+|\d+(?:[.\-]\d+)*)[：:。，,.]?$")


def _todo_body(text: str) -> str:
    return re.sub(r"^-\s*(?:\[(?:x|X| )\]\s*)?", "", text).strip()


def _lint_file(path: Path, root: Path, playbook: bool = False) -> list[LintIssue]:
    rel = path.relative_to(root).as_posix()
    issues: list[LintIssue] = []

    def add(line_no: int, rule: str, message: str) -> None:
        issues.append(LintIssue(rel, line_no, rule, message))

    section, section_line = None, 0
    phase: tuple[str, int] | None = None
    phase_status: list[int] = []
    phase_count = 0
    phases_section_line = 0
    phases_in_section = 0
    next_step: list[int] = []
    in_comment = False

    def close_phase() -> None:
        nonlocal phase, phase_status
        if phase is None:
            return
        name, head_line = phase
        if not phase_status:
            add(head_line, "Status 行", f"Phase「{name}」缺少 **Status:** 行（应恰好 1 个）")
        elif len(phase_status) > 1:
            for line_no in phase_status[1:]:
                add(line_no, "Status 行",
                    f"Phase「{name}」出现 {len(phase_status)} 个 **Status:** 行（应恰好 1 个）")
        phase, phase_status = None, []

    def close_next_step() -> None:
        nonlocal next_step
        if len(next_step) > 10:
            add(section_line, "Next Step 条数",
                f"Next Step 共 {len(next_step)} 条（上限 10 条），第 11 条在第 {next_step[10]} 行")
        next_step = []

    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        stripped = raw.strip()
        if in_comment:
            if "-->" in stripped:
                in_comment = False
            continue
        if stripped.startswith("<!--"):
            if "-->" not in stripped:
                in_comment = True
            continue
        if stripped.startswith("## "):
            close_phase()
            if section == "Next Step":
                close_next_step()
            section, section_line = stripped[3:].strip(), line_no
            if playbook and section == "Phases" and not phases_section_line:
                phases_section_line = line_no
            continue
        if re.match(r"^###\s+[Pp]hase", stripped):
            close_phase()
            m = PHASE_RE.match(stripped)
            if not m:
                add(line_no, "Phase 标题",
                    f"Phase 标题格式应为「### Phase N: 名称」，实际：{stripped}")
                continue
            phase, phase_status = (m.group(1), line_no), []
            phase_count += 1
            if playbook:
                if section == "Phases":
                    phases_in_section += 1
                else:
                    add(line_no, "Phase 位置",
                        f"Phase「{m.group(1)}」必须位于 `## Phases` 节内"
                        f"（当前节：{section or '无'}）")
            continue
        if not stripped:
            continue
        if section == "Next Step":
            next_step.append(line_no)
            body = _todo_body(stripped)
            if BARE_ID_RE.match(body):
                add(line_no, "裸编号", f"编号后必须跟任务描述：{body}")
            continue
        if phase is None:
            continue
        if STATUS_MARK_RE.search(stripped):
            phase_status.append(line_no)
            if playbook and not STATUS_RE.search(stripped):
                add(line_no, "Status 行",
                    f"Status 值应为 pending|in_progress|complete，实际：{stripped}")
            continue
        if stripped.startswith("-"):
            if re.match(r"^-\s*\[(?:x|X| )\]\s*\S", stripped):
                body = _todo_body(stripped)
                if BARE_ID_RE.match(body):
                    add(line_no, "裸编号", f"编号后必须跟任务描述：{body}")
                continue
            if re.match(r"^-\s*(?:plan|owner):\s*\S", stripped):
                continue
            add(line_no, "Phase 项格式", f"Phase 项须为「- [ ]」/「- [x]」形式，实际：{stripped}")
        elif playbook:
            add(line_no, "Phase 项格式",
                f"Phase 项须为「- [ ]」/「- [x]」/「- owner:」/「- plan:」形式，实际：{stripped}")
    close_phase()
    if playbook:
        if phases_section_line == 0:
            add(1, "Phases 节", "缺少 `## Phases` 节（### Phase N: 必须位于其下）")
        elif not phases_in_section:
            add(phases_section_line, "Phases 节", "`## Phases` 节内没有 `### Phase N:` 阶段")
        if phase_count == 0:
            add(1, "Phase 缺失", "剧本至少需要 1 个「### Phase N: 名称」阶段")
    if section == "Next Step":
        close_next_step()
    return issues


def lint_playbooks(target: Path) -> int:
    """剧本结构校验（复用 _lint_file 的 Phase/Status 解析；目录跳过 README.md）。"""
    if target.is_dir():
        root = target
        files = sorted(p for p in target.glob("*.md")
                       if p.is_file() and p.name.lower() != "readme.md")
    else:
        root = target.parent
        files = [target]
    issues: list[LintIssue] = []
    for path in files:
        issues.extend(_lint_file(path, root, playbook=True))
    if issues:
        for issue in issues:
            print(issue.format(), file=sys.stderr)
        print(f"剧本 lint 失败：{len(issues)} 处问题（{len(files)} 个剧本）。", file=sys.stderr)
        return 1
    print(f"剧本 lint ok：{len(files)} 个剧本结构合法。")
    return 0


def lint_plans(plans: list[Plan]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for plan in plans:
        issues.extend(_lint_file(plan.path, plan.root))
    return issues


def run_lint(plans: list[Plan]) -> int:
    issues = lint_plans(plans)
    if issues:
        for issue in issues:
            print(issue.format(), file=sys.stderr)
        print(f"lint 失败：{len(issues)} 处问题（{len(plans)} 个 plan）。", file=sys.stderr)
        return 1
    print(f"lint ok：{len(plans)} 个 plan 无结构问题。")
    return 0


def _section(plan: Plan, name: str) -> str:
    return " ".join(line.strip() for line in plan.sections.get(name, []) if line.strip())


def current_phase(plan: Plan) -> str:
    """自动推导（S7-b1）：首个 Status≠complete 的 Phase 名；全部 complete 回落手工
    ## Current Phase（收口说明兜底），再无则收口态「全部完成」；无 Phase 显示「—」。"""
    active = next((name for name, status in plan.phases if status != "complete"), None)
    if active is not None:
        return active
    declared = _section(plan, "Current Phase")
    if declared:
        return declared
    return ALL_DONE_MARK if plan.phases else "—"


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
        fingerprint_line(plans, cfg),
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
            now_at = current_phase(target)
        else:
            done, health = "—（Backlog 承载）", HEALTH_BLOCKED if portfolio.deviations else HEALTH_WIP
            now_at = "—"
        streams.append((phase, target, ref))
        # 流表只留概览列（S8-b1：表格单元格不塞多条 todo），细节在流表下方按流分节
        rows.append(f"| {cell(phase)} | {cell(now_at)} | {done} | {health} |")

    backlog = _tables(portfolio.sections.get("Backlog", []))
    captain_ids = {row[0] for row in backlog if len(row) >= 5 and row[0].startswith(cap_prefix)}
    name_by_rel = {t.rel: name for name, t, _ in streams if t}  # plan 路径→流名（标注统一用流名）
    captain = _captain_lines(plans, portfolio, cap_prefix, name_by_rel)

    # —— 前部：业务链总览 + 待 Captain（业务主线与人工决策先行，工单细节置后）——
    chain_plans = [p for p in plans if p.chain]
    if chain_plans:
        out += ["## 业务链总览", "", CHAIN_LEGEND, ""]
        for p in chain_plans:
            out += [f"#### {name_by_rel.get(p.rel) or p.title}", ""]
            for node, mark, desc in p.chain:
                if mark == "done":
                    line = f"- [x] {LIGHT_DONE} **{cell(node)}**"
                elif mark == "captain":
                    line = f"- [ ] {LIGHT_CAPTAIN} **{cell(node)}**"
                else:
                    line = f"- [ ] {LIGHT_TODO} **{cell(node)}**"
                if desc:
                    line += f" — {_todo_lights(cell(desc))}"
                    if mark == "captain":
                        line += _cap_suffix(desc)
                out.append(line)
            out.append("")

    out += ["## 待 Captain（人工决策/动作）", ""]
    out += captain or ["—"]

    out += ["", "## 当前目标", "", _section(portfolio, "Goal") or "—", "", "## 总舵 Next Step", ""]
    top = _section(portfolio, "Next Step")
    if top:
        out.append(_todo_lights(top))
    else:
        # 回退：逐流列出仍在推进的流，不合并（stream 无语义 P 字段，P0 以"仍有未完成阶段"近似）。
        derived = [s for s in streams if s[1] and s[1].complete < s[1].total and _next_step_lines(s[1])]
        out += [f"- 自动推导：{name}" for name, _, _ in derived] or ["—"]

    # 最近完成（写法规约 ≤10 条，新的在上）：只渲染 portfolio 节，超出截断并注明总数。
    recent = [l.strip() for l in portfolio.sections.get("最近完成", []) if l.strip()]
    out += ["", "## 最近完成", ""]
    if recent:
        out += [_todo_line(l) for l in recent[:10]]
        if len(recent) > 10:
            out += ["", f"> 共 {len(recent)} 条，仅显示最近 10 条。"]
    else:
        out.append("—")

    out += ["", "## 流表", "", STREAM_TABLE_HEADER, "|---|---|---|---|"]
    out += rows
    # 各流 todo 细节：流表下方按流分节（#### 流名 + 行首 todo 列表；S8-b1 表格不塞多条）
    for name, target, ref in streams:
        if not target:
            continue
        todos = [_todo_line(l) for l in _next_step_lines(target)]
        if not todos:
            continue
        out += ["", f"#### {cell(name)}（`{ref or target.rel}`）", ""]
        out += todos

    out += ["", "## 偏差明细", ""]
    deviations_all = _tables(portfolio.sections.get("Deviation", []))
    deviations = [r for r in deviations_all if not _row_resolved(r)]
    resolved_count = len(deviations_all) - len(deviations)
    backlog = _tables(portfolio.sections.get("Backlog", []))
    captain_ids = {row[0] for row in backlog if len(row) >= 5 and row[0].startswith(cap_prefix)}
    if deviations:
        out += ["| 预期 | 实际 | 原因 | 待决策 |", "|---|---|---|---|"]
        for row in deviations:
            if len(row) >= 4 and row[3].split()[0] in captain_ids:
                row = row[:3] + [f"见 {row[3].split()[0]}"]
            out.append("| " + " | ".join(cell(c) for c in row) + " |")
        if resolved_count:
            out += ["", f"> 已解决 {resolved_count} 条（折叠）"]
    elif resolved_count:
        out.append(f"—（{resolved_count} 条已解决，全部折叠）")
    else:
        out.append("—")

    out += ["", "## Backlog（agent 可执行）", ""]
    # 分流（S8-hotfix）：C 前缀 OR ⏸ → 待 Captain 汇总；其余（T/N 等非 ⏸）→ Backlog，互斥
    todo = [row for row in backlog if len(row) >= 5
            and not row[0].startswith(cap_prefix) and not _row_has_pause(row)]
    if todo:
        out += ["| # | 目标 | P | 依赖 | 阻塞 |", "|---|---|---|---|---|"]
        for row in todo:
            if row[0] in blocks:
                row = row[:4] + [f"待 {blocks[row[0]]}"]
            out.append("| " + " | ".join(cell(c) for c in row) + " |")
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
    assert board.index("## 待 Captain") < board.index("## 流表"), "待 Captain 应置顶于流表"
    cap, agt = cfg["backlog"]["captainPrefix"], cfg["backlog"]["agentPrefix"]
    # captain 切片止于紧邻 ## 节（S8-hotfix）：待 Captain 与 Backlog 间还隔着
    # 当前目标/总舵 Next Step/流表等节，旧口径切到 ## Backlog 会误吞中间节行
    captain = board.split("## 待 Captain", 1)[1].split("\n## ", 1)[0]
    backlog = board.split("## Backlog", 1)[1]
    backlog_full = re.findall(rf"^\| ([{cap}{agt}]+\d+) \|", backlog, re.M)
    # S8-hotfix 分流口径：待 Captain 合法成员 = C 前缀行 + 任意前缀 ⏸ 行（T/N+⏸ 合法，
    # 不再断言全 C 前缀）；守门改为——①Backlog 表不混入 ⏸ 行（⏸ 语义应进待 Captain）；
    # ②待 Captain 节条目行均标准 `- [ ] ⏸` 语法（渲染格式回退检测）。
    backlog_pause = [l for l in backlog.splitlines() if l.startswith("|") and "⏸" in l]
    captain_stray = [l for l in captain.splitlines()
                     if l.startswith("- ") and not l.startswith("- [ ] ⏸")]
    # 空表=无混入即通过（阻断1：init 全新产物仓 Backlog 为空，selftest 必须首跑即绿；
    # 不在骨架预置示例数据行——那会进真实看板）
    assert (not backlog_full) or all(not i.startswith(cap) for i in backlog_full), "Backlog 混入 Captain 项"
    assert not backlog_pause, "Backlog 混入 ⏸ 行（应进待 Captain）"
    assert not captain_stray, "待 Captain 节混入非 ⏸ 条目行"
    print(f"selftest ok: {len(plans)} plans, {sum(p.total for p in plans)} phases")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="从 .planning/*/task_plan.md 派生 Markdown 进度看板（只读 .planning）。",
        epilog=(
            "刷新时机：收口回执后立即执行 --write；guard 每 5 分钟执行 --write；"
            "交接第 0 步执行 --write 后接 --check-fresh。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--write", action="store_true", help="写入看板文件（路径读配置 board.path）")
    parser.add_argument("--selftest", action="store_true", help="对当前 plan 跑解析断言")
    parser.add_argument(
        "--check-fresh",
        action="store_true",
        help="校验看板是否落后于各 plan/配置（过期列明来源并非零退出；新鲜时单行输出）",
    )
    parser.add_argument(
        "--lint",
        action="store_true",
        help="create-time lint：Phase 标题格式/单 Status 行/Next Step ≤10/Phase 项格式/裸编号"
             "（失败按 文件:行号 列出并非零退出）",
    )
    parser.add_argument(
        "--playbooks",
        metavar="PATH",
        help="剧本库结构校验：PATH 为文件或目录（目录取 *.md、跳过 README.md）；"
             "Phase 必须位于 `## Phases` 节内且每剧本至少 1 个「### Phase N:」，"
             "每 Phase 恰一行 **Status:** pending|in_progress|complete，"
             "Phase 条目只用 - [ ]/- [x]/- owner:/- plan:"
             "（失败按 文件:行号 列出并非零退出；路径不存在 rc=3）",
    )
    parser.add_argument("--root", help="目标仓根目录（默认：cwd 向上找 .planning）")
    parser.add_argument(
        "--archive",
        help="一次性在看板头部写入归档链接（显式动作，不改配置；配置 board.archive 为空时不写归档行）",
    )
    args = parser.parse_args(argv)

    root = resolve_root(args.root)
    if args.playbooks:
        target = Path(args.playbooks).expanduser()
        if not target.exists():
            print(f"剧本校验失败：路径不存在：{target}", file=sys.stderr)
            return 3
        return lint_playbooks(target)
    cfg = load_config(root)
    plans = load_plans(root)
    if args.selftest:
        selftest(plans, cfg)
        return 0
    if args.lint:
        return run_lint(plans)
    if args.check_fresh:
        return check_fresh(root, cfg, plans)

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
