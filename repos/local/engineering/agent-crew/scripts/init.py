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
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

MARK_START = "<!-- agent-crew:orchestration:start -->"
MARK_END = "<!-- agent-crew:orchestration:end -->"


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
                  label: str, src: str = "") -> None:
    """写入一个受管产物：一致→跳过；不一致→冲突（--force 才覆盖）。src=技能侧源路径。"""
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


def copy_script(root: Path, name: str, report: Report, force: bool) -> None:
    src = SKILL_DIR / "scripts" / name
    if not src.exists():
        report.warnings.append(f"技能脚本缺失：scripts/{name}（技能安装不完整？）")
        return
    content = src.read_text(encoding="utf-8")
    write_product(root, Path("scripts") / name, content, report, force,
                  f"脚本 {name}", src=str(src))
    if name.endswith(".sh"):
        target = root / "scripts" / name
        if target.exists():
            target.chmod(0o755)


# ---------- 各生成步骤 ----------

def gen_config(root: Path, cfg: dict, report: Report, force: bool,
               portfolio_dir: str | None = None) -> dict:
    """落 orchestration.json；已存在读用现值合并。

    portfolio_dir 非空时为「沿用既有 portfolio 目录」的选择结果：合并后强制
    portfolioDir 回到沿用路径（阻断4：配置/渲染/start-helm 三处一致），与文件
    现值不同则回写修正（二跑一致时不回写，保持幂等）。
    """
    path = root / ".planning" / "orchestration.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            existing = None
        broken = not isinstance(existing, dict)
        if broken:
            # 建议10：损坏配置显式警告，不再静默降级
            report.warnings.append(
                f"配置文件损坏（{path}），已用内置默认值；请修复或删除后重跑。"
            )
            existing = {}
        merged = _deep_merge(cfg, existing)
        if portfolio_dir is not None and merged.get("portfolioDir") != portfolio_dir:
            merged["portfolioDir"] = portfolio_dir
            path.write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
            report.add(Item(
                target=Path(".planning/orchestration.json"), status="跳过",
                note=f"已存在，读用现值；portfolioDir 同步为沿用目录 {portfolio_dir}",
            ))
            return merged
        if broken:
            note = "已存在但损坏，已按内置默认值处理"
        else:
            diffs = sorted(
                k for k in set(existing) | set(cfg)
                if not k.startswith("_") and existing.get(k) != cfg.get(k)
            )
            note = (f"已存在，读用现值；与默认值差异键：{', '.join(diffs)}"
                    if diffs else "已存在，读用现值")
        report.add(Item(target=Path(".planning/orchestration.json"), status="跳过", note=note))
        return merged
    cfg = dict(cfg)
    cfg["projectName"] = cfg.get("projectName") or root.resolve().name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report.add(Item(target=Path(".planning/orchestration.json"), status="新建"))
    return cfg


def gen_role_templates(root: Path, templates: Path, ctx: dict, report: Report, force: bool) -> None:
    mapping = [
        (templates / "agents" / "qoder", Path(".qoder") / "agents", "角色"),
        (templates / "agents" / "opencode", Path(".opencode") / "agent", "角色"),
        (templates / "skills" / "firstmate-dispatch",
         Path(".opencode") / "skills" / "firstmate-dispatch", "副舵派活 SOP"),
    ]
    found = False
    for src_dir, dst_dir, label in mapping:
        if not src_dir.is_dir():
            continue
        for src in sorted(src_dir.glob("*.md")):
            found = True
            content = apply_template(src.read_text(encoding="utf-8"), ctx)
            write_product(root, dst_dir / src.name, content, report, force,
                          f"{label} {src.name}", src=str(src))
    if not found:
        report.warnings.append(
            "未找到角色模板（templates/agents/）：本产物跳过。"
            "模板由技能资产提供；若你是技能开发者请检查 templates 目录完整性。"
        )


def gen_portfolio(root: Path, portfolio_rel: Path, templates: Path, ctx: dict,
                  report: Report, force: bool) -> None:
    """portfolio 三文件：已存在即跳过（用户业务内容，不比对不重建），缺失才按文件粒度补齐。"""
    plan_tpl = templates / PORTFOLIO_PLAN_TEMPLATE
    if plan_tpl.exists():
        plan_content = apply_template(plan_tpl.read_text(encoding="utf-8"), ctx)
    else:
        plan_content = None
        report.warnings.append(
            f"未找到模板 {PORTFOLIO_PLAN_TEMPLATE}：portfolio 的 task_plan.md 跳过（findings/progress 仍生成）。"
        )
    targets = [
        (portfolio_rel / "task_plan.md", plan_content, "portfolio task_plan 骨架"),
        (portfolio_rel / "findings.md", FINDINGS_SKELETON, "portfolio findings 骨架"),
        (portfolio_rel / "progress.md", PROGRESS_SKELETON, "portfolio progress 骨架"),
    ]
    for rel, content, label in targets:
        if content is None:
            continue
        target = root / rel
        if target.exists():
            report.add(Item(target=rel, status="跳过", note=f"{label}（已存在，不重建）"))
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        report.add(Item(target=rel, status="新建", note=label))


def gen_agents_section(root: Path, templates: Path, ctx: dict, report: Report, force: bool) -> None:
    tpl = templates / "AGENTS-编排节.md"
    if not tpl.exists():
        report.warnings.append("未找到模板 AGENTS-编排节.md：AGENTS.md 编排节跳过。")
        return
    raw = apply_template(tpl.read_text(encoding="utf-8"), ctx)
    section_body = strip_marker_lines(raw).strip("\n")  # 阻断2：先剥模板自带标记，代码常量统一包裹
    section = f"{MARK_START}\n{section_body}\n{MARK_END}"
    target = root / "AGENTS.md"
    if not target.exists():
        write_product(root, Path("AGENTS.md"), AGENT_SKELETON_HEADER + "\n" + section + "\n",
                      report, force, "AGENTS.md（最小骨架+编排节）")
        return
    current = target.read_text(encoding="utf-8")
    if MARK_START in current and MARK_END in current:
        pre = current.split(MARK_START, 1)[0]
        # R1：post 段收敛孤儿标记行——旧双层形态的第二个 end 落在首个 end 之后，
        # 不收敛会与干净渲染结果自洽，--force 一次后仍残留 end×2 且永不再提示。
        post = strip_marker_lines(current.split(MARK_END, 1)[1])
        merged = pre + section + post
        if merged == current:
            report.add(Item(target=Path("AGENTS.md"), status="跳过", note="编排节内容一致"))
        elif not force:
            report.add(Item(target=Path("AGENTS.md"), status="冲突",
                            note="编排节已有标记且内容与渲染结果不一致", rendered=merged,
                            src=str(tpl)))
        else:
            target.write_text(merged, encoding="utf-8")
            report.add(Item(target=Path("AGENTS.md"), status="冲突（--force 覆盖）",
                            note="编排节按标记定界替换，节外内容未动"))
    else:
        merged = current.rstrip("\n") + "\n\n" + section + "\n"
        target.write_text(merged, encoding="utf-8")
        report.add(Item(target=Path("AGENTS.md"), status="追加",
                        note="编排节已追加（标记节定界，原内容未动）"))


def gen_context(root: Path, templates: Path, ctx: dict, report: Report, force: bool) -> None:
    target = root / "CONTEXT.md"
    if target.exists():
        report.add(Item(target=Path("CONTEXT.md"), status="跳过", note="已存在，不动"))
        return
    tpl = templates / "CONTEXT-骨架.md"
    if not tpl.exists():
        report.warnings.append("未找到模板 CONTEXT-骨架.md：CONTEXT.md 跳过。")
        return
    write_product(root, Path("CONTEXT.md"),
                  apply_template(tpl.read_text(encoding="utf-8"), ctx), report, force,
                  "术语文档骨架", src=str(tpl))


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

def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="agent-crew 编排体系初始化（幂等）")
    parser.add_argument("--root", help="目标项目仓根（默认 cwd）")
    parser.add_argument("--templates", help=f"模板目录覆盖（默认 {TEMPLATES}）")
    parser.add_argument("--yes", action="store_true", help="零交互，全部取默认值")
    parser.add_argument("--force", action="store_true", help="冲突时覆盖（仅限本技能管理的产物路径）")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    templates = Path(args.templates).resolve() if args.templates else TEMPLATES
    report = Report()

    if not root.is_dir():
        print(f"错误：目标目录不存在：{root}", file=sys.stderr)
        return 1

    detect(root, report)

    cfg = load_defaults()

    # portfolio 目录：已有 *portfolioMatch* 匹配目录时询问生成位置（--yes 取默认=沿用第一个候选）。
    # 先询问后落配置（阻断4）：沿用结果同步进 cfg["portfolioDir"]，使 orchestration.json、
    # 渲染 ctx、start-helm.sh 必读清单三处一致。
    portfolio_rel = Path(cfg["portfolioDir"])
    configured = portfolio_rel.as_posix()
    existing = find_portfolio_dirs(root, cfg["portfolioMatch"])
    chosen_rel: str | None = None
    if configured in {p.relative_to(root).as_posix() for p in existing}:
        chosen_rel = configured  # 配置指向的目录已存在：视为沿用，不再询问
        report.add(Item(target=portfolio_rel, status="跳过",
                        note="沿用配置指向的既有 portfolio 目录"))
    elif existing:
        options = [f"沿用 {p.relative_to(root).as_posix()}" for p in existing]
        options.append(f"新建 {configured}")
        pick = ask_choice("检测到已有 portfolio 目录，选择生成位置", options, 1, args.yes)
        if pick <= len(existing):
            chosen_rel = existing[pick - 1].relative_to(root).as_posix()
            report.add(Item(target=Path(chosen_rel), status="跳过",
                            note="沿用既有 portfolio 目录"))
        else:
            report.add(Item(target=portfolio_rel, status="新建", note="新建 portfolio 目录"))
        if chosen_rel is not None:
            cfg["portfolioDir"] = chosen_rel
            portfolio_rel = Path(chosen_rel)

    cfg = gen_config(root, cfg, report, args.force, portfolio_dir=chosen_rel)
    portfolio_rel = Path(cfg["portfolioDir"])
    ctx = render_context(cfg)
    ctx["project_name"] = ctx["project_name"] or root.name

    gen_role_templates(root, templates, ctx, report, args.force)
    for name in SCRIPT_MANIFEST:
        copy_script(root, name, report, args.force)
    gen_portfolio(root, portfolio_rel, templates, ctx, report, args.force)
    gen_agents_section(root, templates, ctx, report, args.force)
    gen_context(root, templates, ctx, report, args.force)

    # 核对清单
    print(f"\n=== agent-crew init 核对清单（{root}） ===")
    for item in report.items:
        print(f"[{item.status}] {item.target.as_posix()}" + (f"（{item.note}）" if item.note else ""))
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
