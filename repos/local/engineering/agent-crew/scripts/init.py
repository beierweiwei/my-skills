#!/usr/bin/env python3
"""agent-crew init：检测目标仓 → 渲染模板 → 生成编排资产 → 输出三态核对清单（幂等）。

用法（在目标项目仓根，或用 --root 指定）:
    python3 <skill>/scripts/init.py                 # 交互默认直通，冲突默认不覆盖
    python3 <skill>/scripts/init.py --yes           # 零交互（沿用检测到的既有目录）
    python3 <skill>/scripts/init.py --force         # 冲突时覆盖（仅限本技能管理的产物路径）

行为契约（设计 §3.2）:
  检测（只读）: git 仓 / 既有产物 / planning-with-files / python3 版本；
  生成: orchestration.json → 角色模板渲染 → 脚本落 scripts/ → portfolio 三文件骨架
        → AGENTS.md 编排节（标记节定界）→ CONTEXT.md（不存在才生成）；
  幂等: 渲染结果与已存在产物一致 → skip（不重写）；不一致 → 默认不覆盖并列入冲突清单，
        --force 才覆盖；portfolio 骨架缺失文件按文件粒度补齐；
  边界: 只写下方 MANIFEST 内路径，清单外零写入。

模板缺失（如角色模板由并行流水线后续产出）→ 报警告跳过，不发明内容。
退出码: 0=成功无冲突；2=存在未处理冲突。
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

MARK_START = "<!-- agent-crew:orchestration:start -->"
MARK_END = "<!-- agent-crew:orchestration:end -->"

# SKILL.md frontmatter 无 version 时的内置版本（Phase 3.1 补 frontmatter 后以文件为准）
SKILL_VERSION_FALLBACK = "2.0.0"

CONFIG_REL = Path(".planning/orchestration.json")
CREW_RULES_REL = Path("docs/crew-rules.md")
CREW_ACCEPTANCE_REL = Path("docs/crew-acceptance.md")
CREW_RULE_SOURCES = [
    (SKILL_DIR / "references" / "crew-rules.md", CREW_RULES_REL, "规则正文 crew-rules.md"),
    (SKILL_DIR / "references" / "crew-acceptance.md", CREW_ACCEPTANCE_REL,
     "验收正文 crew-acceptance.md"),
]


def strip_marker_lines(text: str) -> str:
    """剥掉 agent-crew 标记行（阻断2 修复；R1 复用于收敛 post 段孤儿标记）。

    标记由代码常量 MARK_START/MARK_END 统一包裹；模板自带标记（含带说明的 start
    变体，如「<!-- agent-crew:orchestration:start（本节由…） -->」）与旧双层形态
    残留的孤儿标记行一律剥离，防止双层标记导致二跑恒冲突、--force 持续膨胀。
    按 "\\n" 切分拼接（非 splitlines）：非标记行连同行尾换行结构原样保留，
    对无标记文本是恒等变换——套在标记节后的 post 段上不会误伤正常仓。
    """
    kept = [
        line for line in text.split("\n")
        if not line.strip().startswith("<!-- agent-crew:orchestration:")
    ]
    return "\n".join(kept)


def read_skill_version(skill_dir: Path | None = None) -> str:
    """读 SKILL.md frontmatter 的 version；缺失/损坏时用内置常量。"""
    path = (skill_dir or SKILL_DIR) / "SKILL.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return SKILL_VERSION_FALLBACK
    if not text.startswith("---"):
        return SKILL_VERSION_FALLBACK
    end = text.find("\n---", 3)
    if end == -1:
        return SKILL_VERSION_FALLBACK
    match = re.search(r"^version:\s*(.+?)\s*$", text[3:end], re.MULTILINE)
    if not match:
        return SKILL_VERSION_FALLBACK
    value = match.group(1).strip().strip("'\"")
    return value or SKILL_VERSION_FALLBACK


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class InitReportError(Exception):
    """上次 init 失败记录损坏。"""


def init_report_path(root: Path) -> Path:
    """失败记录落点：有 git 用 .git/crew/init-report.json，否则 .planning/。"""
    proc = subprocess.run(
        ["git", "rev-parse", "--git-common-dir"], cwd=root,
        capture_output=True, text=True, check=False,
    )
    if proc.returncode == 0 and proc.stdout.strip():
        path = Path(proc.stdout.strip())
        if not path.is_absolute():
            path = root / path
        return path / "crew" / "init-report.json"
    return root / ".planning" / ".agent-crew-init.json"


def read_init_report(root: Path) -> list[str] | None:
    """返回上次失败路径列表；无记录返回 None；记录损坏抛 InitReportError。"""
    path = init_report_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InitReportError(f"{path}：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("failed"), list):
        raise InitReportError(f"{path}：缺少 failed 列表")
    paths: list[str] = []
    for entry in data["failed"]:
        if isinstance(entry, str):
            paths.append(entry)
        elif isinstance(entry, dict) and isinstance(entry.get("path"), str):
            paths.append(entry["path"])
    return paths


def write_init_report(root: Path, failed: list[Item], warnings: list[str],
                      skill_version: str) -> None:
    """写失败/冲突项；无失败且从未有记录时不落空文件，内容未变化时零写入。"""
    path = init_report_path(root)
    payload = [
        {"path": item.target.as_posix(), "status": item.status, "note": item.note}
        for item in failed
    ]
    current = None
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            current = None
    if not payload and current is None:
        return
    if (
        isinstance(current, dict)
        and current.get("failed") == payload
        and current.get("skillVersion") == skill_version
    ):
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "version": 1,
        "skillVersion": skill_version,
        "updatedAt": utc_now(),
        "failed": payload,
        "warnings": warnings,
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# 内置默认（与 templates/project.config.json 同源同改；模板在位时以模板为准）
DEFAULT_CONFIG: dict = {
    "projectName": "",
    "portfolioDir": ".planning/portfolio",
    "board": {"path": "docs/进度追踪.md", "title": "进度追踪（派生看板）", "archive": ""},
    "portfolioMatch": "*portfolio*",
    "backlog": {"captainPrefix": "C", "agentPrefix": "T", "captainBlocks": {}},
    "docNames": {"req": "需求.md", "reqPath": "", "accept": "验收.md", "context": "CONTEXT.md"},
    "captainDecisions": [
        "合并/推送/worktree 清理",
        "花钱采购",
        "客户数据与对接人",
        "法律合规与风险确认",
        "主数据/架构口径",
        "范围与验收口径变更",
        "三道人工门签认",
        "验收效果最终确认",
    ],
    "redLines": [
        "业务实现与平台通用能力分层（业务代码不进平台，能力以外部服务承接）",
        "原始数据不全量读取，只读必要列/行",
        "真实数据验收，模拟/夹具只用于开发测试，不伪造证据",
    ],
    "dataDir": "data/raw/",
    "aopReference": "",
    "helm": {"tabLabel": "总舵", "expectedModel": ""},
}

# 配置键 → 模板占位符（完整对照见 templates/project.config.json 的 _placeholders）
PLACEHOLDERS: list[tuple[tuple[str, ...], str]] = [
    (("projectName",), "project_name"),
    (("portfolioDir",), "portfolio_dir"),
    (("board", "path"), "board_path"),
    (("board", "title"), "board_title"),
    (("board", "archive"), "board_archive"),
    (("portfolioMatch",), "portfolio_match"),
    (("backlog", "captainPrefix"), "captain_prefix"),
    (("backlog", "agentPrefix"), "agent_prefix"),
    (("docNames", "req"), "doc_req"),
    (("docNames", "reqPath"), "doc_req_path"),
    (("docNames", "accept"), "doc_accept"),
    (("docNames", "context"), "doc_context"),
    (("captainDecisions",), "captain_decisions"),
    (("redLines",), "red_lines"),
    (("dataDir",), "data_dir"),
    (("aopReference",), "aop_reference"),
    (("helm", "tabLabel"), "helm_tab_label"),
    (("helm", "expectedModel"), "helm_expected_model"),
    (("topic",), "topic"),  # 工单主题 slug 模板；未配置时渲染为字面 <topic>
]

# init 管理并拷贝到目标仓 scripts/ 的脚本（不含 init.py 自身与 tests/）
SCRIPT_MANIFEST = [
    "start-agent.sh",
    "start-helm.sh",
    "helm_agent_name.py",
    "agent-watch.py",
    "worktree-watch.py",
    "herdr-events.py",
    "plan-status.py",
    "crew-state.py",
    "crew-guard.py",
    "crew-ticket.py",
    "crew-msg.py",
    "pane-audit.py",
    "crew-lint.py",
]

PORTFOLIO_PLAN_TEMPLATE = "plans/portfolio-task_plan.md"

FINDINGS_SKELETON = """# Findings

> 调研与中间结论记录；只增不改，过时内容标注「历史（截至日期）」。

## 发现

（空）
"""

PROGRESS_SKELETON = """# Progress（编排台账）

> 里程碑 / 进展 / 自动驾驶里程记录（时间、事项、决定、证据、待 Captain 项）。

## 里程

（空）
"""

AGENT_SKELETON_HEADER = "# AGENTS\n"


@dataclass
class Item:
    """一个产物的核对状态。"""
    target: Path          # 目标仓内的相对路径（posix 显示）
    status: str = "新建"   # 新建 / 追加 / 跳过 / 冲突 / 警告
    note: str = ""
    rendered: str = ""    # 渲染后内容（冲突报告用）
    src: str = ""         # 技能侧源路径（冲突清单人工比对用，建议14）


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[Item] = field(default_factory=list)

    def add(self, item: Item) -> None:
        self.items.append(item)
        if item.status == "冲突":
            self.conflicts.append(item)


# ---------- 配置 ----------

def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_defaults() -> dict:
    """默认值母本 = templates/project.config.json（缺 _ 键）；缺失时用内置 DEFAULT_CONFIG。"""
    path = TEMPLATES / "project.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = {k: v for k, v in data.items() if not k.startswith("_")}
        return _deep_merge(DEFAULT_CONFIG, data)
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)


# 空值兜底渲染（阻断3 / 建议11）：这些占位符空值直通会产出病句或失效示例，渲染为提示文案。
EMPTY_FALLBACK: dict[str, str] = {
    # 工单主题 slug 未配置 → 字面 <topic>（保留模板语义可见，b2 占位符集第 9 项）
    "topic": "<topic>",
    # 正文文档路径未配置 → 字面占位（防「读␣␣并执行」式坏文案）
    "doc_req_path": "<需求文档路径>",
    # AOP 总纲真源未配置 → 按 b2 回执 §4 约定文案兜底（防「以␣␣为唯一真源」病句）
    "aop_reference": "（未配置——按 AOP 六维自审并记入 plan Decisions）",
}


def render_context(cfg: dict) -> dict:
    """配置值 → 占位符渲染上下文；列表渲染为多行「- 条目」；空值按 EMPTY_FALLBACK 兜底。"""
    ctx: dict[str, str] = {}
    for keys, name in PLACEHOLDERS:
        value: object = cfg
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        if name in EMPTY_FALLBACK and (value is None or value == ""):
            ctx[name] = EMPTY_FALLBACK[name]
            continue
        if isinstance(value, list):
            ctx[name] = "\n".join(f"- {item}" for item in value)
        else:
            ctx[name] = "" if value is None else str(value)
    ctx["crew_rules"] = CREW_RULES_REL.as_posix()
    ctx["crew_acceptance"] = CREW_ACCEPTANCE_REL.as_posix()
    return ctx


_SENTENCE_PUNCT = set("。；，、！？")


def _splice_list_token(text: str, token: str, value: str) -> str:
    """列表占位符句中渲染时断行（建议6，模板不动）：前有同行文字→值前断行；
    后接同行文字→值后断行；纯句读标点附着末条（「决定：{{x}}。」→ 末条自然收句）。"""
    parts: list[str] = []
    idx = 0
    for m in re.finditer(re.escape(token), text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        before = text[line_start:m.start()]
        nl = text.find("\n", m.end())
        line_end = nl if nl != -1 else len(text)
        tail = text[m.end():line_end].strip()
        pre = "\n" if before.strip() else ""
        if tail and all(ch in _SENTENCE_PUNCT for ch in tail):
            post = tail
        elif tail:
            post = "\n" + tail
        else:
            post = ""
        parts.append(text[idx:m.start()])
        parts.append(pre + value + post)
        idx = line_end
    parts.append(text[idx:])
    return "".join(parts)


def apply_template(text: str, ctx: dict) -> str:
    for name, value in ctx.items():
        token = "{{" + name + "}}"
        if token not in text:
            continue
        rendered = str(value)
        if "\n" in rendered:
            # 列表值（多行「- 条目」）句中渲染会打碎 Markdown 列表结构，改走断行拼接
            text = _splice_list_token(text, token, rendered)
        else:
            text = text.replace(token, rendered)
    return text


# ---------- 检测（只读） ----------

def detect(root: Path, report: Report) -> None:
    if sys.version_info < (3, 10):
        report.warnings.append(
            f"python3 版本过低（{sys.version_info.major}.{sys.version_info.minor}）："
            "agent-watch/herdr-events/plan-status 需 ≥3.10，请升级后重跑 init。"
        )
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=root,
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        report.warnings.append(
            "非 git 仓：worktree-watch/agent-watch 状态文件依赖 .git/，监听不可用；"
            "建议 git init 后重跑 init 补齐。plan-status 不受影响（只读 .planning）。"
        )
    pwf = Path.home() / ".qoder-cn" / "skills" / "planning-with-files" / "scripts" / "init-session.sh"
    if not pwf.exists():
        report.warnings.append(
            "未检测到 planning-with-files 技能：流 plan 日常创建依赖它"
            "（在 my-skills 仓执行 bash scripts/install-skills.sh --skill planning-with-files）；"
            "本次 init 已直接生成骨架文件，不影响启动。"
        )


def find_portfolio_dirs(root: Path, pattern: str) -> list[Path]:
    planning = root / ".planning"
    if not planning.is_dir():
        return []
    return sorted(
        p for p in planning.iterdir()
        if p.is_dir() and fnmatch.fnmatch(p.name, pattern)
    )


# ---------- 写入 helper（清单外零写入的单一通道） ----------

def write_product(root: Path, rel: Path, content: str, report: Report, force: bool,
                  label: str, src: str = "", only: set[Path] | None = None) -> None:
    """写入一个受管产物：一致→跳过；不一致→冲突（--force 才覆盖）。src=技能侧源路径。
    only 非空时只处理集合内路径（--only-failed）。"""
    if only is not None and rel not in only:
        return
    target = root / rel
    if target.exists():
        current = target.read_text(encoding="utf-8")
        if current == content:
            report.add(Item(target=rel, status="跳过", note=label))
            return
        if not force:
            report.add(Item(
                target=rel, status="冲突", note=label, rendered=content, src=src,
            ))
            return
        action = "冲突（--force 覆盖）"
    else:
        action = "新建"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    report.add(Item(target=rel, status=action, note=label))


def script_products(report: Report | None = None) -> list[tuple[Path, str, str, str]]:
    products: list[tuple[Path, str, str, str]] = []
    for name in SCRIPT_MANIFEST:
        src = SKILL_DIR / "scripts" / name
        if not src.exists():
            if report is not None:
                report.warnings.append(f"技能脚本缺失：scripts/{name}（技能安装不完整？）")
            continue
        products.append((
            Path("scripts") / name,
            src.read_text(encoding="utf-8"),
            f"脚本 {name}",
            str(src),
        ))
    return products


def gen_scripts(root: Path, report: Report, force: bool,
                only: set[Path] | None = None) -> None:
    for rel, content, label, src in script_products(report):
        if only is not None and rel not in only:
            continue
        write_product(root, rel, content, report, force, label, src=src, only=only)
        if rel.suffix == ".sh":
            target = root / rel
            if target.exists():
                target.chmod(0o755)


def role_products(templates: Path, ctx: dict) -> list[tuple[Path, str, str, str]]:
    mapping = [
        (templates / "agents" / "qoder", Path(".qoder") / "agents", "角色"),
        (templates / "agents" / "opencode", Path(".opencode") / "agent", "角色"),
        (templates / "skills" / "firstmate-dispatch",
         Path(".opencode") / "skills" / "firstmate-dispatch", "副舵派活 SOP"),
    ]
    products: list[tuple[Path, str, str, str]] = []
    for src_dir, dst_dir, label in mapping:
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.md")):
            products.append((
                dst_dir / src.name,
                apply_template(src.read_text(encoding="utf-8"), ctx),
                f"{label} {src.name}",
                str(src),
            ))
    return products


def rule_doc_products(report: Report | None = None) -> list[tuple[Path, str, str, str]]:
    products: list[tuple[Path, str, str, str]] = []
    for src, rel, label in CREW_RULE_SOURCES:
        if not src.exists():
            if report is not None:
                report.warnings.append(f"规则正文缺失：{src}（技能安装不完整？）")
            continue
        products.append((rel, src.read_text(encoding="utf-8"), label, str(src)))
    return products


# ---------- 各生成步骤 ----------

def gen_config(root: Path, cfg: dict, report: Report, force: bool,
               portfolio_dir: str | None = None, skill_version: str = "",
               only: set[Path] | None = None) -> dict:
    """落 orchestration.json；已存在读用现值合并，并维护 skillVersion 版本戳。

    portfolio_dir 非空时为「沿用既有 portfolio 目录」的选择结果：合并后强制
    portfolioDir 回到沿用路径（阻断4：配置/渲染/start-helm 三处一致），与文件
    现值不同则回写修正（二跑一致时不回写，保持幂等）。
    only 非空时只处理集合内路径（--only-failed），但仍返回合并后的配置供渲染。
    """
    path = root / CONFIG_REL
    selected = only is None or CONFIG_REL in only
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        broken = not isinstance(existing, dict)
        if broken:
            # 建议10：损坏配置显式警告，不再静默降级；不覆盖用户文件
            report.warnings.append(
                f"配置文件损坏（{path}），已用内置默认值；请修复或删除后重跑。"
            )
            merged = _deep_merge(cfg, {})
            merged["skillVersion"] = skill_version
            if selected:
                report.add(Item(target=CONFIG_REL, status="跳过",
                                note="已存在但损坏，已按内置默认值处理；版本戳未写入"))
            return merged
        merged = _deep_merge(cfg, existing)
        updates: dict = {}
        if portfolio_dir is not None and merged.get("portfolioDir") != portfolio_dir:
            merged["portfolioDir"] = portfolio_dir
            updates["portfolioDir"] = portfolio_dir
        installed = existing.get("skillVersion")
        if installed != skill_version:
            merged["skillVersion"] = skill_version
            updates["skillVersion"] = skill_version
            if selected:
                report.warnings.append(
                    f"源已变更（已安装 skillVersion={installed if installed else '缺失'}，"
                    f"源={skill_version}）：需重跑 update-all 后重跑 init。"
                )
        if not selected:
            return merged
        if updates:
            data = dict(existing)
            data.update(updates)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            note = "已存在，读用现值；" + "、".join(f"{key} 已更新" for key in updates)
        else:
            diffs = sorted(
                k for k in set(existing) | set(cfg)
                if not k.startswith("_") and existing.get(k) != cfg.get(k)
            )
            note = (f"已存在，读用现值；与默认值差异键：{', '.join(diffs)}"
                    if diffs else "已存在，读用现值")
        report.add(Item(target=CONFIG_REL, status="跳过", note=note))
        return merged
    cfg = dict(cfg)
    cfg["projectName"] = cfg.get("projectName") or root.resolve().name
    cfg["skillVersion"] = skill_version
    if selected:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report.add(Item(target=CONFIG_REL, status="新建"))
    return cfg


def gen_role_templates(root: Path, templates: Path, ctx: dict, report: Report, force: bool,
                       only: set[Path] | None = None) -> None:
    mapping = [
        templates / "agents" / "qoder",
        templates / "agents" / "opencode",
        templates / "skills" / "firstmate-dispatch",
    ]
    for rel, content, label, src in role_products(templates, ctx):
        if only is not None and rel not in only:
            continue
        write_product(root, rel, content, report, force, label, src=src, only=only)
    if not any(path.is_dir() for path in mapping):
        report.warnings.append(
            "未找到角色模板（templates/agents/）：本产物跳过。"
            "模板由技能资产提供；若你是技能开发者请检查 templates 目录完整性。"
        )


def portfolio_products(portfolio_rel: Path, templates: Path, ctx: dict,
                       report: Report | None = None) -> list[tuple[Path, str, str]]:
    plan_tpl = templates / PORTFOLIO_PLAN_TEMPLATE
    targets: list[tuple[Path, str, str]] = []
    if plan_tpl.exists():
        targets.append((
            portfolio_rel / "task_plan.md",
            apply_template(plan_tpl.read_text(encoding="utf-8"), ctx),
            "portfolio task_plan 骨架",
        ))
    elif report is not None:
        report.warnings.append(
            f"未找到模板 {PORTFOLIO_PLAN_TEMPLATE}：portfolio 的 task_plan.md 跳过（findings/progress 仍生成）。"
        )
    targets += [
        (portfolio_rel / "findings.md", FINDINGS_SKELETON, "portfolio findings 骨架"),
        (portfolio_rel / "progress.md", PROGRESS_SKELETON, "portfolio progress 骨架"),
    ]
    return targets


def gen_portfolio(root: Path, portfolio_rel: Path, templates: Path, ctx: dict,
                  report: Report, force: bool, only: set[Path] | None = None) -> None:
    """portfolio 三文件：已存在即跳过（用户业务内容，不比对不重建），缺失才按文件粒度补齐。"""
    for rel, content, label in portfolio_products(portfolio_rel, templates, ctx, report):
        if only is not None and rel not in only:
            continue
        target = root / rel
        if target.exists():
            report.add(Item(target=rel, status="跳过", note=f"{label}（已存在，不重建）"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        report.add(Item(target=rel, status="新建", note=label))


def build_agents_section(templates: Path, ctx: dict) -> tuple[str | None, str]:
    tpl = templates / "AGENTS-编排节.md"
    if not tpl.exists():
        return None, "未找到模板 AGENTS-编排节.md：AGENTS.md 编排节跳过。"
    raw = apply_template(tpl.read_text(encoding="utf-8"), ctx)
    section_body = strip_marker_lines(raw).strip("\n")  # 阻断2：先剥模板自带标记，代码常量统一包裹
    return f"{MARK_START}\n{section_body}\n{MARK_END}", ""


def expected_agents_text(templates: Path, ctx: dict, current: str | None) -> tuple[str | None, str]:
    """按 init 的合并规则计算 AGENTS.md 期望全文；模板缺失时返回 (None, 警告)。"""
    section, warning = build_agents_section(templates, ctx)
    if section is None:
        return None, warning
    if current is None:
        return AGENT_SKELETON_HEADER + "\n" + section + "\n", ""
    if MARK_START in current and MARK_END in current:
        pre = current.split(MARK_START, 1)[0]
        # R1：post 段收敛孤儿标记行——旧双层形态的第二个 end 落在首个 end 之后，
        # 不收敛会与干净渲染结果自洽，--force 一次后仍残留 end×2 且永不再提示。
        post = strip_marker_lines(current.split(MARK_END, 1)[1])
        return pre + section + post, ""
    return current.rstrip("\n") + "\n\n" + section + "\n", ""


def gen_agents_section(root: Path, templates: Path, ctx: dict, report: Report, force: bool,
                       only: set[Path] | None = None) -> None:
    rel = Path("AGENTS.md")
    if only is not None and rel not in only:
        return
    target = root / rel
    current = target.read_text(encoding="utf-8") if target.exists() else None
    merged, warning = expected_agents_text(templates, ctx, current)
    if merged is None:
        report.warnings.append(warning)
        return
    if current is None:
        write_product(root, rel, merged, report, force,
                      "AGENTS.md（最小骨架+编排节）", only=only)
        return
    if merged == current:
        report.add(Item(target=rel, status="跳过", note="编排节内容一致"))
    elif MARK_START in current and MARK_END in current:
        if not force:
            report.add(Item(target=rel, status="冲突",
                            note="编排节已有标记且内容与渲染结果不一致", rendered=merged,
                            src=str(templates / "AGENTS-编排节.md")))
        else:
            target.write_text(merged, encoding="utf-8")
            report.add(Item(target=rel, status="冲突（--force 覆盖）",
                            note="编排节按标记定界替换，节外内容未动"))
    else:
        target.write_text(merged, encoding="utf-8")
        report.add(Item(target=rel, status="追加",
                        note="编排节已追加（标记节定界，原内容未动）"))


def gen_context(root: Path, templates: Path, ctx: dict, report: Report, force: bool,
                only: set[Path] | None = None) -> None:
    rel = Path("CONTEXT.md")
    if only is not None and rel not in only:
        return
    target = root / rel
    if target.exists():
        report.add(Item(target=rel, status="跳过", note="已存在，不动"))
        return
    tpl = templates / "CONTEXT-骨架.md"
    if not tpl.exists():
        report.warnings.append("未找到模板 CONTEXT-骨架.md：CONTEXT.md 跳过。")
        return
    write_product(root, rel, apply_template(tpl.read_text(encoding="utf-8"), ctx),
                  report, force, "术语文档骨架", src=str(tpl), only=only)


def gen_crew_docs(root: Path, report: Report, force: bool,
                  only: set[Path] | None = None) -> None:
    for rel, content, label, src in rule_doc_products(report):
        if only is not None and rel not in only:
            continue
        write_product(root, rel, content, report, force, label, src=src, only=only)


# ---------- 交互 ----------

def ask_choice(prompt: str, options: list[str], default: int, yes: bool) -> int:
    """tty 编号选项问答（建议8：1/2/… 编号 + 列出全部候选）；非 tty / --yes / EOF 取 default。
    返回 1 起编号；兼容中文回答（「沿用」→1，「新建」→末项）。"""
    if yes or not sys.stdin.isatty():
        return default
    menu = "\n".join(f"  {i}) {opt}" for i, opt in enumerate(options, 1))
    try:
        answer = input(f"{prompt}：\n{menu}\n编号 [默认: {default}]: ").strip()
    except EOFError:
        return default
    if not answer:
        return default
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return int(answer)
    if answer.startswith("新建"):
        return len(options)
    if answer.startswith("沿用"):
        return 1
    return default


# ---------- 主流程 ----------

def _check_content(root: Path, rel: Path, content: str) -> tuple[str, str]:
    target = root / rel
    if not target.exists():
        return "缺失", ""
    try:
        current = target.read_text(encoding="utf-8")
    except OSError as exc:
        return "过期", f"无法读取：{exc}"
    if current != content:
        return "过期", "内容与渲染结果不一致"
    return "一致", ""


def run_check(root: Path, templates: Path, skill_version: str) -> int:
    """只读校验全部受管产物；逐项报一致/缺失/过期，有漂移非零退出。"""
    print(f"技能版本：{skill_version}")
    counts = {"一致": 0, "缺失": 0, "过期": 0}

    def record(rel: Path, status: str, note: str = "") -> None:
        counts[status] += 1
        print(f"[{status}] {rel.as_posix()}" + (f"（{note}）" if note else ""))

    report = Report()
    cfg = load_defaults()
    config_path = root / CONFIG_REL
    if not config_path.exists():
        record(CONFIG_REL, "缺失")
    else:
        try:
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            record(CONFIG_REL, "过期", f"JSON 损坏：{exc}")
        else:
            if not isinstance(loaded, dict):
                record(CONFIG_REL, "过期", "不是 JSON 对象")
            else:
                cfg = _deep_merge(cfg, loaded)
                installed = loaded.get("skillVersion")
                if installed == skill_version:
                    record(CONFIG_REL, "一致", f"skillVersion={skill_version}")
                else:
                    record(CONFIG_REL, "过期",
                           f"skillVersion={installed if installed else '缺失'}，源={skill_version}")
                    print(
                        f"源已变更（已安装 skillVersion={installed if installed else '缺失'}，"
                        f"源={skill_version}）：需重跑 update-all 后重跑 init。"
                    )
    cfg["projectName"] = cfg.get("projectName") or root.name
    ctx = render_context(cfg)
    ctx["project_name"] = ctx["project_name"] or root.name

    for rel, content, _label, _src in role_products(templates, ctx):
        record(rel, *_check_content(root, rel, content))
    for rel, content, _label, _src in script_products(report):
        record(rel, *_check_content(root, rel, content))
    for rel, content, _label, _src in rule_doc_products(report):
        record(rel, *_check_content(root, rel, content))

    agents_rel = Path("AGENTS.md")
    agents_tpl = templates / "AGENTS-编排节.md"
    if agents_tpl.exists():
        target = root / agents_rel
        current = target.read_text(encoding="utf-8") if target.exists() else None
        expected, warning = expected_agents_text(templates, ctx, current)
        if expected is None:
            report.warnings.append(warning)
        elif current is None:
            record(agents_rel, "缺失")
        elif expected == current:
            record(agents_rel, "一致")
        else:
            record(agents_rel, "过期", "编排节内容与渲染结果不一致")

    portfolio_rel = Path(cfg["portfolioDir"])
    for rel, _content, _label in portfolio_products(portfolio_rel, templates, ctx, report):
        record(rel, "一致" if (root / rel).exists() else "缺失")

    if (templates / "CONTEXT-骨架.md").exists():
        context_rel = Path("CONTEXT.md")
        record(context_rel, "一致" if (root / context_rel).exists() else "缺失")

    for warning in report.warnings:
        print(f"[警告] {warning}")
    print(
        f"检查结果：{counts['一致']} 项一致，{counts['缺失']} 项缺失，"
        f"{counts['过期']} 项过期。"
    )
    return 0 if counts["缺失"] == 0 and counts["过期"] == 0 else 1


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-crew 编排体系初始化（幂等）")
    parser.add_argument("--root", help="目标项目仓根（默认 cwd）")
    parser.add_argument("--templates", help=f"模板目录覆盖（默认 {TEMPLATES}）")
    parser.add_argument("--yes", action="store_true", help="零交互，全部取默认值")
    parser.add_argument("--force", action="store_true", help="冲突时覆盖（仅限本技能管理的产物路径）")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check", action="store_true",
        help="只读校验受管产物（逐项一致/缺失/过期），有漂移非零退出",
    )
    mode.add_argument(
        "--only-failed", action="store_true",
        help="只重跑上次 init 记录的失败/冲突项（记录在 .git/crew/init-report.json）",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    templates = Path(args.templates).resolve() if args.templates else TEMPLATES
    skill_version = read_skill_version()

    if not root.is_dir():
        print(f"错误：目标目录不存在：{root}", file=sys.stderr)
        return 1

    if args.check:
        return run_check(root, templates, skill_version)

    report = Report()
    only: set[Path] | None = None
    if args.only_failed:
        try:
            failed = read_init_report(root)
        except InitReportError as exc:
            print(f"错误：上次 init 失败记录损坏（{exc}）；请先正常运行 init 修复。",
                  file=sys.stderr)
            return 1
        if failed is None:
            print(
                f"未找到上次 init 失败记录（{init_report_path(root)}）；"
                "请先正常运行 init 后再用 --only-failed。"
            )
            return 0
        if not failed:
            print("上次 init 未记录失败/冲突项，无需 --only-failed。")
            return 0
        only = {Path(path) for path in failed}

    detect(root, report)

    cfg = load_defaults()

    if only is not None:
        # 只重跑失败项：读现有配置取得 portfolioDir，不触发交互与目录选择
        config_path = root / CONFIG_REL
        if config_path.exists():
            try:
                existing = json.loads(config_path.read_text(encoding="utf-8"))
            except ValueError:
                existing = None
            if isinstance(existing, dict):
                cfg = _deep_merge(cfg, existing)
        portfolio_rel = Path(cfg["portfolioDir"])
        chosen_rel: str | None = None
    else:
        # portfolio 目录：已有 *portfolioMatch* 匹配目录时询问生成位置（--yes 取默认=沿用第一个候选）。
        # 先询问后落配置（阻断4）：沿用结果同步进 cfg["portfolioDir"]，使 orchestration.json、
        # 渲染 ctx、start-helm.sh 必读清单三处一致。
        portfolio_rel = Path(cfg["portfolioDir"])
        configured = portfolio_rel.as_posix()
        existing_dirs = find_portfolio_dirs(root, cfg["portfolioMatch"])
        chosen_rel = None
        if configured in {p.relative_to(root).as_posix() for p in existing_dirs}:
            chosen_rel = configured  # 配置指向的目录已存在：视为沿用，不再询问
            report.add(Item(target=portfolio_rel, status="跳过",
                            note="沿用配置指向的既有 portfolio 目录"))
        elif existing_dirs:
            options = [f"沿用 {p.relative_to(root).as_posix()}" for p in existing_dirs]
            options.append(f"新建 {configured}")
            pick = ask_choice("检测到已有 portfolio 目录，选择生成位置", options, 1, args.yes)
            if pick <= len(existing_dirs):
                chosen_rel = existing_dirs[pick - 1].relative_to(root).as_posix()
                report.add(Item(target=Path(chosen_rel), status="跳过",
                                note="沿用既有 portfolio 目录"))
            else:
                report.add(Item(target=portfolio_rel, status="新建", note="新建 portfolio 目录"))
            if chosen_rel is not None:
                cfg["portfolioDir"] = chosen_rel
                portfolio_rel = Path(chosen_rel)

    cfg = gen_config(root, cfg, report, args.force, portfolio_dir=chosen_rel,
                     skill_version=skill_version, only=only)
    portfolio_rel = Path(cfg["portfolioDir"])
    ctx = render_context(cfg)
    ctx["project_name"] = ctx["project_name"] or root.name

    gen_role_templates(root, templates, ctx, report, args.force, only=only)
    gen_scripts(root, report, args.force, only=only)
    gen_crew_docs(root, report, args.force, only=only)
    gen_portfolio(root, portfolio_rel, templates, ctx, report, args.force, only=only)
    gen_agents_section(root, templates, ctx, report, args.force, only=only)
    gen_context(root, templates, ctx, report, args.force, only=only)

    failed_items = [item for item in report.items if item.status == "冲突"]
    write_init_report(root, failed_items, report.warnings, skill_version)

    # 核对清单
    print(f"\n=== agent-crew init 核对清单（{root}；技能版本 {skill_version}） ===")
    for item in report.items:
        print(f"[{item.status}] {item.target.as_posix()}" + (f"（{item.note}）" if item.note else ""))
    if only is not None:
        requested = {path.as_posix() for path in only}
        handled = {item.target.as_posix() for item in report.items}
        unmatched = sorted(requested - handled)
        if unmatched:
            print("\n[提示] 以下失败项未匹配到当前受管产物（模板/脚本可能已变更）：")
            for path in unmatched:
                print(f"  - {path}")
    for warning in report.warnings:
        print(f"[警告] {warning}")
    if report.conflicts:
        print("\n冲突清单（默认不覆盖；人工比对后删除旧文件重跑，或加 --force 仅覆盖本技能产物路径）：")
        for item in report.conflicts:
            line = f"  - {item.target.as_posix()}（{item.note}）"
            if item.src:
                line += f"\n      技能侧源：{item.src}"
            print(line)
    print("\n后续步骤：")
    print("  1) 启动总舵：bash scripts/start-helm.sh（需 Herdr server 运行）")
    print("  2) 日常编排：对主 agent 说「启动总舵 / 派活」即可；详见技能 SKILL.md")
    return 2 if report.conflicts and not args.force else 0


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
