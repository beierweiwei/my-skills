#!/usr/bin/env python3
"""dispatch.py — Agent 生命周期与工位变更的唯一入口（v2.1 切片 A）。

实现范围（arch-proposal-v1 §5.3/§7.4、role-path-contract §2.1/§4）：
  compose  组合 dry-run：读技能基类 + 项目角色文件 → 渲染 token → append-only 组合；
           输出 hash/标题/残留 token/门禁报告；只读，不写项目、不写 agent-log。
  start    启动角色：配置 → 描述符 → 继承/门禁/frontmatter 校验 → compose →
           运行时使用 init/upgrade 安装的组合基础代理
           （Qoder `crew-<role>` / opencode `crew-<role>`）→ agent-log。
  prompt / notify / close / status / list / tab / pane  生命周期与工位操作（写 agent-log）。
  notify   跨项目通知：解析收件方 cwd/git common dir，先原子落盘收件方
           `<git-common-dir>/crew/inbox/`，再 `herdr agent prompt`；不查/不写 dispatch
           注册表，不动生命周期；`--workspace + --role` 按 workspace 与角色约定解析当前
           活跃代际，`--agent` 直发；`--ack <id>` 把收件方 inbox 通知标 processed。
           聚焦守卫（T37）：目标 pane 聚焦或聚焦状态无法判定（fail-safe）→ 不注入，
           保留 inbox 并推送 Herdr 通知，投递通道记 `inbox_fallback`。

继承组合规则：
- 角色 = 技能基类（<skillRoot>/templates/agents/<runtime>/<role>.md）+ 项目角色文件；
- append-only：base 先、项目后，固定分隔标记；不做 section 合并、不做同名替换；
- frontmatter/权限只取基类；项目文件出现 tools/disallowedTools/permission → 拒绝；
- token 组合时全部渲染为实际路径，残留任何 `{{...}}` → rc=1；
- 受保护标题（`## 引用` 及含规则指针的 H2–H6）与项目同名 → rc=1；
- gates = 基类 ∪ gatePolicy.appendGates 的项目追加项；项目列核心 gate → rc=1；
- 组合产物只落 <git-common-dir>/crew/compose/<run-id>/，不写项目发现目录。

退出码：0=成功；1=校验/门禁拒绝；2=待人工；3=环境缺失（herdr/CLI/权限）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402

ROLE_ID_RE = re.compile(r"^[a-z][a-z0-9-]*$")
INSTANCE_NAME_RE = re.compile(r"^[a-z0-9_-]+$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
TOKEN_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")
RESIDUAL_TOKEN_RE = re.compile(r"\{\{[^{}\n]+\}\}")
DESCRIPTOR_RE = re.compile(r"<!--\s*crew-role\s*(\{.*?\})\s*-->", re.DOTALL)
OVERLAY_SEPARATOR = "\n\n<!-- agent-crew:project-overlay -->\n\n"

CAPABILITIES = {
    "read", "write-docs", "write-code", "run-commands", "report",
    "dispatch", "merge", "push",
}
FORBIDDEN_CUSTOM_CAPS = {"dispatch", "merge", "push"}
PERMISSION_FIELDS = {
    "tools", "disallowedTools", "allowedTools", "permission", "permissions",
}
QODER_BASE_FIELDS = {"name", "description", "disallowedTools"}
PROJECT_FRONTMATTER_FIELDS = {
    "qoder": {"name", "description"},
    "opencode": {"description", "mode"},
}

# 核心角色默认能力/门禁/生命周期（arch-proposal-v1 §5.4）
CORE_ROLES: dict[str, dict] = {
    "helm": {
        "capabilities": ["read", "dispatch"],
        "gates": ["three-human-gates", "no-merge", "no-push"],
        "lifecycle": "persistent",
    },
    "firstmate": {
        "capabilities": ["read", "write-docs", "dispatch"],
        "gates": ["three-human-gates", "no-merge", "no-push"],
        "lifecycle": "persistent",
    },
    "builder": {
        "capabilities": ["read", "write-code", "run-commands"],
        "gates": ["three-human-gates", "review-required"],
        "lifecycle": "keep-until",
    },
    "planner": {
        "capabilities": ["read", "write-docs"],
        "gates": ["three-human-gates"],
        "lifecycle": "one-shot",
    },
    "reviewer": {
        "capabilities": ["read", "report"],
        "gates": ["three-human-gates", "report-only"],
        "lifecycle": "one-shot",
    },
    "runner": {
        "capabilities": ["read", "run-commands"],
        "gates": ["three-human-gates", "data-safety"],
        "lifecycle": "one-shot",
    },
    "scout": {
        "capabilities": ["read", "report"],
        "gates": ["three-human-gates", "report-only"],
        "lifecycle": "one-shot",
    },
}
CORE_GATE_IDS = {
    "three-human-gates", "no-merge", "no-push",
    "review-required", "report-only", "data-safety",
}
DESCRIPTOR_FIELDS = {
    "id", "runtime", "extends", "managed", "capabilities",
    "gates", "lifecycle", "prompt",
}


class DispatchError(Exception):
    """校验/门禁/环境错误；code 为进程退出码。"""

    def __init__(self, message: str, code: int = 1):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def split_frontmatter(text: str) -> tuple[dict | None, str]:
    """返回 (frontmatter 解析结果, 正文)；无 frontmatter → (None, text)。

    只做 dispatch 需要的轻量解析：顶层 key + 原始行块；不引入 YAML 依赖。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end = index
            break
    if end is None:
        return None, text
    fields: dict[str, list[str]] = {}
    order: list[str] = []
    current: str | None = None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t-":
            if current:
                fields[current].append(line)
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+):\s*(.*)$", line)
        if match:
            current = match.group(1)
            fields[current] = [match.group(2)]
            order.append(current)
    parsed = {
        "fields": {key: values[0].strip() for key, values in fields.items()},
        "blocks": fields,
        "order": order,
        "raw": "\n".join(lines[1:end]),
    }
    return parsed, "\n".join(lines[end + 1:])


def scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value


def parse_list(frontmatter: dict, key: str) -> list[str]:
    """解析 `key: [A, B]` 或 `key:\n  - A` 形式的列表字段。"""
    fields = frontmatter.get("fields", {})
    if key not in fields:
        return []
    inline = scalar(fields[key])
    if inline.startswith("[") and inline.endswith("]"):
        body = inline[1:-1].strip()
        if not body:
            return []
        return [scalar(item) for item in body.split(",") if scalar(item)]
    if inline:
        return [inline]
    items: list[str] = []
    for line in frontmatter.get("blocks", {}).get(key, [])[1:]:
        stripped = line.strip()
        if stripped.startswith("-"):
            item = scalar(stripped[1:])
            if item:
                items.append(item)
    return items


def parse_headings(text: str) -> tuple[list[str], list[tuple[int, int, str]]]:
    """返回 (lines, [(行号, 级别, 标题)])；跳过代码围栏。"""
    lines = text.splitlines()
    headings: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    return lines, headings


def render_tokens(text: str, values: dict[str, str]) -> str:
    def replace(match: re.Match) -> str:
        return values.get(match.group(1), match.group(0))

    return TOKEN_RE.sub(replace, text)


def write_json_atomic(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def append_log(root: Path, row: dict) -> None:
    path = crew_dir(root) / "agent-log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 配置与 token
# ---------------------------------------------------------------------------

def load_config(root: Path) -> dict:
    path = root / ".planning/orchestration.json"
    if not path.is_file():
        raise DispatchError(f"缺少项目配置：{path}（先跑 init）")
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise DispatchError(f"配置读取失败：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise DispatchError(f"配置必须是 JSON 对象：{path}")
    return data


def resolve_skill_root(root: Path, config: dict) -> Path:
    raw = config.get("skillRoot")
    if not isinstance(raw, str) or not raw.strip():
        raise DispatchError("配置缺 skillRoot（普通技能安装路径）；先补配置再 dispatch")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    if not path.is_dir():
        raise DispatchError(f"skillRoot 不存在或不可读：{path}")
    return path


def config_preflight_errors(root: Path, config: dict) -> list[str]:
    """start 前置校验：配置路径/hooks schema 不合法时 fail closed，不触碰 Herdr。"""
    errors: list[str] = []
    root_abs = root.resolve()
    hooks = config.get("hooks")
    if hooks is not None and not isinstance(hooks, dict):
        errors.append("hooks.schema: hooks 必须是对象")
        hooks = {}
    if isinstance(hooks, dict):
        allowed = {"paths", "roleExt", "customRoles", "gatePolicy", "projectScripts"}
        unknown = sorted(set(hooks) - allowed)
        if unknown:
            errors.append(f"hooks.schema: 未知 hooks 类 {unknown}")
        paths = hooks.get("paths")
        if paths is not None and not isinstance(paths, dict):
            errors.append("hooks.schema: hooks.paths 必须是对象")
            paths = {}
        if isinstance(paths, dict):
            extra = sorted(set(paths) - {"topology", "projectExt"})
            if extra:
                errors.append(f"hooks.schema: hooks.paths 未知键 {extra}")
            topology = paths.get("topology")
            if topology:
                target = (root / str(topology)).resolve()
                if not target.is_file():
                    errors.append(f"config.paths: paths.topology 不存在：{topology}")
                elif not target.is_relative_to(root_abs) or ".git" in target.parts:
                    errors.append(f"config.paths: paths.topology 越界：{topology}")
            ext = paths.get("projectExt")
            if ext and not (root / str(ext)).is_dir():
                errors.append(f"config.paths: paths.projectExt 不存在：{ext}")
        role_ext = hooks.get("roleExt")
        if isinstance(role_ext, dict):
            for role_id, rel in role_ext.items():
                if not (root / str(rel)).is_file():
                    errors.append(f"docs.pointers: roleExt 文件不存在：{rel}（{role_id}）")
    portfolio = root / str(config.get("portfolioDir") or ".planning/portfolio")
    if not portfolio.is_dir():
        errors.append(f"config.paths: portfolioDir 不存在：{portfolio.relative_to(root)}")
    board = _as_dict(config.get("board"))
    board_path = board.get("path")
    if board_path:
        target = (root / str(board_path)).resolve()
        if not target.is_relative_to(root_abs):
            errors.append(f"config.paths: board.path 越界：{board_path}")
        elif not target.parent.is_dir():
            errors.append(f"config.paths: board.path 父目录不存在：{target.parent}")
    return errors


def _as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def token_context(root: Path, config: dict, skill_root: Path | None = None) -> dict[str, str]:
    skill_root = skill_root or resolve_skill_root(root, config)
    doc = _as_dict(config.get("docNames"))
    board = _as_dict(config.get("board"))
    backlog = _as_dict(config.get("backlog"))
    helm = _as_dict(config.get("helm"))
    captain = config.get("captainDecisions")
    red_lines = config.get("redLines")
    values = {
        "skill_root": str(skill_root),
        "crew_rules": str(skill_root / "references" / "crew-rules.md"),
        "crew_acceptance": str(skill_root / "references" / "crew-acceptance.md"),
        "plan_discipline": str(skill_root / "references" / "plan-discipline.md"),
        "project_name": str(config.get("projectName") or root.name),
        "portfolio_dir": str(config.get("portfolioDir") or ".planning/portfolio"),
        "portfolio_match": str(config.get("portfolioMatch") or "*portfolio*"),
        "board_path": str(board.get("path") or "docs/进度追踪.md"),
        "board_title": str(board.get("title") or "进度追踪（派生看板）"),
        "board_archive": str(board.get("archive") or ""),
        "captain_prefix": str(backlog.get("captainPrefix") or "C"),
        "agent_prefix": str(backlog.get("agentPrefix") or "T"),
        "doc_req": str(doc.get("req") or "需求.md"),
        "doc_req_path": str(doc.get("reqPath") or "<需求文档路径>"),
        "doc_accept": str(doc.get("accept") or "验收.md"),
        "doc_context": str(doc.get("context") or "CONTEXT.md"),
        "data_dir": str(config.get("dataDir") or "data/raw/"),
        "aop_reference": str(
            config.get("aopReference")
            or "（未配置——按 AOP 六维自审并记入 plan Decisions）"
        ),
        "helm_tab_label": str(helm.get("tabLabel") or "总舵"),
        "helm_expected_model": str(helm.get("expectedModel") or ""),
        "topic": str(config.get("topic") or "<topic>"),
    }
    # 列表 token 无配置值时 fail closed：不静默渲染为空、丢红线和决策清单
    if isinstance(captain, list) and captain:
        values["captain_decisions"] = "\n".join(f"- {item}" for item in captain)
    if isinstance(red_lines, list) and red_lines:
        values["red_lines"] = "\n".join(f"- {item}" for item in red_lines)
    return values


# ---------------------------------------------------------------------------
# 角色文件与描述符
# ---------------------------------------------------------------------------

def role_file_path(root: Path, runtime: str, role_id: str, custom: bool) -> Path:
    if runtime == "qoder":
        parts = [".qoder", "agents"] + (["custom"] if custom else []) + [f"{role_id}.md"]
    else:
        name = f"custom-{role_id}" if custom else role_id
        parts = [".opencode", "agent", f"{name}.md"]
    return root.joinpath(*parts)


def core_role_path(root: Path, runtime: str, role_id: str) -> Path:
    return role_file_path(root, runtime, role_id, custom=False)


def custom_role_path(root: Path, runtime: str, role_id: str) -> Path:
    return role_file_path(root, runtime, role_id, custom=True)


def registry_runtime_resolution(root: Path, role_id: str) -> dict:
    """按托管注册表现役记录解析 role 的唯一 runtime。

    同 role 记录按 runtime 分组取各自最新 startedAt；整体最新唯一 → 解析，
    返回 {"runtime", "source": "registry:<agent>"}；无记录、最新并列
    （多 runtime 无更晚可判定记录）或注册表损坏/不可读 → 返回 {"error": <原因>}，
    由调用方统一包装 --runtime 指引。
    """
    try:
        registry = load_registry(root)
    except DispatchError as exc:
        return {"error": f"注册表不可读/损坏：{exc}"}
    latest: dict[str, tuple[str, str]] = {}
    for agent, info in registry.items():
        if not isinstance(info, dict) or info.get("role") != role_id:
            continue
        candidate_runtime = info.get("runtime")
        if candidate_runtime not in ("qoder", "opencode"):
            continue
        started = info.get("startedAt")
        stamp = started if isinstance(started, str) else ""
        best = latest.get(candidate_runtime)
        if best is None or stamp > best[0]:
            latest[candidate_runtime] = (stamp, agent)
    if not latest:
        return {"error": f"dispatch-agents.json 无 role={role_id} 的现役启动记录"}
    top = max(stamp for stamp, _ in latest.values())
    winners = sorted(
        (candidate_runtime, agent)
        for candidate_runtime, (stamp, agent) in latest.items()
        if stamp == top
    )
    if len(winners) > 1:
        detail = "、".join(f"{item[0]}:{item[1]}" for item in winners)
        return {"error": f"同 role 多 runtime 最新记录并列，无法判定（{detail}）"}
    resolved_runtime, agent = winners[0]
    return {"runtime": resolved_runtime, "source": f"registry:{agent}"}


def locate_role(root: Path, role_id: str,
                runtime: str | None) -> tuple[str, bool, Path, str | None]:
    """定位项目角色文件；返回 (runtime, 是否自定义, 路径, runtimeResolvedFrom)。

    双 runtime 且未显式 --runtime 时按托管注册表最新现役记录解析；无依据或最新
    记录并列则拒绝并给出 --runtime 指引。显式 --runtime 不读注册表。
    """
    if not ROLE_ID_RE.match(role_id):
        raise DispatchError(f"非法角色 id：{role_id!r}（要求 {ROLE_ID_RE.pattern}）")
    candidates: list[tuple[str, bool, Path]] = []
    for candidate_runtime in (runtime,) if runtime else ("qoder", "opencode"):
        core = core_role_path(root, candidate_runtime, role_id)
        custom = custom_role_path(root, candidate_runtime, role_id)
        if role_id in CORE_ROLES:
            if custom.is_file():
                raise DispatchError(
                    f"自定义角色不得与核心角色同名：{custom.relative_to(root)}"
                )
            if core.is_file():
                candidates.append((candidate_runtime, False, core))
        else:
            if core.is_file():
                raise DispatchError(
                    f"角色落点不合法：{core.relative_to(root)}（自定义角色应落 "
                    f".qoder/agents/custom/ 或 .opencode/agent/custom-<id>.md）"
                )
            if custom.is_file():
                candidates.append((candidate_runtime, True, custom))
    if not candidates:
        where = runtime or "qoder/opencode"
        raise DispatchError(f"角色文件不存在：{role_id}（runtime={where}）；先由 init 生成 stub")
    if len({item[0] for item in candidates}) > 1:
        resolution = registry_runtime_resolution(root, role_id)
        if "error" in resolution:
            raise DispatchError(
                f"角色 {role_id} 同时存在于两套 runtime；注册表无法唯一判定现役 runtime："
                f"{resolution['error']}。先按现役总舵/该角色实际运行的 runtime 判定，"
                "再显式指定：--runtime qoder 或 --runtime opencode"
            )
        candidates = [item for item in candidates if item[0] == resolution["runtime"]]
        resolved_from = resolution["source"]
    else:
        resolved_from = "flag" if runtime else "single-runtime"
    resolved_runtime, custom, path = candidates[0]
    return resolved_runtime, custom, path, resolved_from


def parse_project_role(root: Path, path: Path, runtime: str, custom: bool) -> dict:
    text = path.read_text(encoding="utf-8")
    frontmatter, body = split_frontmatter(text)
    if frontmatter is None:
        raise DispatchError(f"项目角色文件缺 frontmatter：{path.relative_to(root)}")
    matches = DESCRIPTOR_RE.findall(body)
    if len(matches) != 1:
        raise DispatchError(
            f"crew-role 描述符必须恰好一个（实际 {len(matches)}）：{path.relative_to(root)}"
        )
    try:
        descriptor = json.loads(matches[0])
    except ValueError as exc:
        raise DispatchError(f"crew-role 描述符 JSON 非法：{path.relative_to(root)}：{exc}") from exc
    if not isinstance(descriptor, dict):
        raise DispatchError(f"crew-role 描述符必须是 JSON 对象：{path.relative_to(root)}")
    overlay_body = DESCRIPTOR_RE.sub("", body, count=1).lstrip("\n")
    return {
        "frontmatter": frontmatter,
        "descriptor": descriptor,
        "overlay_body": overlay_body,
        "rel_path": path.relative_to(root).as_posix(),
        "custom": custom,
        "runtime": runtime,
    }


def validate_project_frontmatter(parsed: dict, role_id: str, runtime: str,
                                 custom: bool) -> list[str]:
    errors: list[str] = []
    fields = parsed["frontmatter"]["fields"]
    allowed = PROJECT_FRONTMATTER_FIELDS[runtime]
    for key in parsed["frontmatter"]["order"]:
        if key in PERMISSION_FIELDS:
            errors.append(
                f"{parsed['rel_path']}: frontmatter 出现权限/工具字段 {key}；"
                "权限只取技能基类（项目文件只保留发现字段）"
            )
        elif key not in allowed:
            errors.append(
                f"{parsed['rel_path']}: frontmatter 非发现字段 {key}（只允许 {sorted(allowed)}）"
            )
    if runtime == "qoder":
        name = scalar(fields.get("name", ""))
        if name != role_id:
            errors.append(f"{parsed['rel_path']}: Qoder frontmatter name={name!r} 必须等于 id {role_id!r}")
        if not scalar(fields.get("description", "")):
            errors.append(f"{parsed['rel_path']}: Qoder frontmatter 缺 description")
    else:
        if not scalar(fields.get("description", "")):
            errors.append(f"{parsed['rel_path']}: opencode frontmatter 缺 description")
        mode = scalar(fields.get("mode", ""))
        if mode not in {"primary", "subagent"}:
            errors.append(f"{parsed['rel_path']}: opencode mode 必须是 primary/subagent（实际 {mode!r}）")
        elif not custom and mode != "primary":
            errors.append(f"{parsed['rel_path']}: 核心角色 opencode mode 必须是 primary")
    return errors


def descriptor_gate_ids(config: dict) -> list[str]:
    hooks = _as_dict(config.get("hooks"))
    policy = _as_dict(hooks.get("gatePolicy"))
    gates = policy.get("appendGates")
    if not isinstance(gates, list):
        return []
    return [gate["id"] for gate in gates if isinstance(gate, dict) and isinstance(gate.get("id"), str)]


def validate_descriptor(parsed: dict, config: dict, base_gates: list[str]) -> tuple[dict, list[str]]:
    errors: list[str] = []
    descriptor = parsed["descriptor"]
    rel = parsed["rel_path"]
    runtime = parsed["runtime"]
    custom = parsed["custom"]

    unknown = sorted(set(descriptor) - DESCRIPTOR_FIELDS)
    if unknown:
        errors.append(f"{rel}: 描述符未知字段 {unknown}（schema 固定）")
    missing = sorted(DESCRIPTOR_FIELDS - set(descriptor))
    if missing:
        errors.append(f"{rel}: 描述符缺字段 {missing}")
    role_id = descriptor.get("id")
    stem = Path(rel).stem
    expected_id = stem[len("custom-"):] if stem.startswith("custom-") else stem
    if not isinstance(role_id, str) or not ROLE_ID_RE.match(role_id):
        errors.append(f"{rel}: 描述符 id 非法：{role_id!r}")
    elif role_id != expected_id:
        errors.append(f"{rel}: 描述符 id={role_id!r} 必须等于文件角色名 {expected_id!r}")
    elif custom and role_id in CORE_ROLES:
        errors.append(f"{rel}: 自定义角色不得与核心角色同名：{role_id}")
    if descriptor.get("runtime") != runtime:
        errors.append(f"{rel}: 描述符 runtime={descriptor.get('runtime')!r} 与文件落点 {runtime} 不一致")

    extends = descriptor.get("extends")
    base_role = extends[6:] if isinstance(extends, str) and extends.startswith("skill:") else None
    if base_role is None:
        errors.append(f"{rel}: extends 必须是 'skill:<roleId>'（实际 {extends!r}）")
    elif base_role not in CORE_ROLES:
        errors.append(f"{rel}: extends 基类不存在：{base_role}")
    if descriptor.get("managed") is not False:
        errors.append(f"{rel}: managed 必须为 false（项目角色文件归项目，永不受管）")
    if descriptor.get("prompt") != rel:
        errors.append(f"{rel}: 描述符 prompt={descriptor.get('prompt')!r} 必须等于文件路径 {rel!r}")
    if descriptor.get("lifecycle") != CORE_ROLES.get(base_role or "", {}).get("lifecycle"):
        errors.append(
            f"{rel}: lifecycle 必须取基类 {CORE_ROLES.get(base_role or '', {}).get('lifecycle')!r}"
        )

    caps = descriptor.get("capabilities")
    if not isinstance(caps, list) or not caps or not all(isinstance(item, str) for item in caps):
        errors.append(f"{rel}: capabilities 必须是非空字符串数组")
        caps = []
    unknown_caps = sorted(set(caps) - CAPABILITIES)
    if unknown_caps:
        errors.append(f"{rel}: 未知 capabilities {unknown_caps}")
    if base_role:
        base_caps = CORE_ROLES[base_role]["capabilities"]
        if custom:
            extra = sorted(set(caps) - set(base_caps))
            if extra:
                errors.append(f"{rel}: 自定义角色 capabilities 超出基类 {base_role}：{extra}")
            forbidden = sorted(set(caps) & FORBIDDEN_CUSTOM_CAPS)
            if forbidden:
                errors.append(
                    f"{rel}: 自定义角色不得声明 {forbidden}（不能成为第二派单入口）"
                )
        elif sorted(set(caps)) != sorted(set(base_caps)):
            errors.append(
                f"{rel}: 核心角色 capabilities 必须等于基类 {base_role}：{base_caps}"
            )

    gates = descriptor.get("gates")
    if not isinstance(gates, list) or not all(isinstance(item, str) for item in gates):
        errors.append(f"{rel}: gates 必须是字符串数组")
        gates = []
    append_ids = descriptor_gate_ids(config)
    composed_gates = list(base_gates)
    for gate in gates:
        if gate in CORE_GATE_IDS:
            errors.append(f"{rel}: 项目不得列核心 gate {gate}（核心门不可删除/改写）")
        elif gate not in append_ids:
            errors.append(
                f"{rel}: gate {gate} 未在 hooks.gatePolicy.appendGates 声明（只能追加已声明项目门）"
            )
        elif gate not in composed_gates:
            composed_gates.append(gate)
    return {
        "id": role_id,
        "base_role": base_role,
        "capabilities": list(CORE_ROLES.get(base_role or "", {}).get("capabilities", [])),
        "gates": composed_gates,
    }, errors


# ---------------------------------------------------------------------------
# 组合
# ---------------------------------------------------------------------------

@dataclass
class ComposeOutcome:
    report: dict
    root: Path | None = None
    rendered_base_body: str = ""
    rendered_overlay_body: str = ""
    composed_body: str = ""
    base_frontmatter: dict | None = None
    role_path: Path | None = None
    base_path: Path | None = None
    skill_root: Path | None = None
    runtime: str | None = None
    custom: bool = False
    role_id: str = ""
    base_role: str | None = None
    capabilities: list[str] = field(default_factory=list)
    gates: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    mode: str = ""
    project_mode: str = ""
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def base_template_path(skill_root: Path, runtime: str, base_role: str) -> Path:
    return skill_root / "templates" / "agents" / runtime / f"{base_role}.md"


def build_compose(root: Path, role_id: str, runtime: str | None,
                  skill_root: Path | None = None) -> ComposeOutcome:
    report: dict = {
        "role": role_id,
        "runtime": runtime,
        "runtimeResolvedFrom": None,
        "baseSource": None,
        "baseSha256": None,
        "overlayPath": None,
        "overlaySha256": None,
        "composedSha256": None,
        "baseHeadings": [],
        "appendedHeadings": [],
        "duplicateHeadings": [],
        "protectedHeadingConflicts": [],
        "unresolvedTokens": [],
        "residualSkillRootTokens": [],
        "basePointersPresent": False,
        "delivery": None,
        "gates": [],
        "composedGates": [],
        "capabilities": [],
        "policySource": "base",
        "warnings": [],
        "errors": [],
    }
    outcome = ComposeOutcome(report=report, root=root, role_id=role_id, runtime=runtime)

    def fail(message: str) -> None:
        outcome.errors.append(message)
        report["errors"] = outcome.errors

    try:
        config = load_config(root)
        resolved_runtime, custom, path, resolved_from = locate_role(root, role_id, runtime)
        outcome.runtime = resolved_runtime
        outcome.custom = custom
        outcome.role_path = path
        report["runtime"] = resolved_runtime
        report["runtimeResolvedFrom"] = resolved_from
        report["overlayPath"] = path.relative_to(root).as_posix()
        report["overlaySha256"] = sha256_file(path)
        report["delivery"] = "composed-agent"

        parsed = parse_project_role(root, path, resolved_runtime, custom)
        outcome.project_mode = scalar(parsed["frontmatter"]["fields"].get("mode", ""))
        errors = validate_project_frontmatter(parsed, role_id, resolved_runtime, custom)
        descriptor = parsed["descriptor"]
        extends = descriptor.get("extends")
        base_role = extends[6:] if isinstance(extends, str) and extends.startswith("skill:") else None
        base_gates = CORE_ROLES.get(base_role or "", {}).get("gates", [])
        info, descriptor_errors = validate_descriptor(parsed, config, base_gates)
        errors.extend(descriptor_errors)
        for message in errors:
            fail(message)
        outcome.base_role = info["base_role"]
        outcome.capabilities = info["capabilities"]
        outcome.gates = info["gates"]
        report["capabilities"] = info["capabilities"]
        report["gates"] = list(base_gates)
        report["composedGates"] = info["gates"]

        if info["base_role"] and info["base_role"] in CORE_ROLES:
            outcome.skill_root = skill_root or resolve_skill_root(root, config)
            base_path = base_template_path(outcome.skill_root, resolved_runtime, info["base_role"])
            if not base_path.is_file():
                fail(f"技能基类缺失：{base_path}（runtime={resolved_runtime}, role={info['base_role']}）")
                return outcome
            outcome.base_path = base_path
            report["baseSource"] = base_path.relative_to(outcome.skill_root).as_posix()
            report["baseSha256"] = sha256_file(base_path)
            base_text = base_path.read_text(encoding="utf-8")
            base_fm, base_body = split_frontmatter(base_text)
            if base_fm is None:
                fail(f"技能基类缺 frontmatter：{base_path}")
                return outcome
            outcome.base_frontmatter = base_fm
            outcome.mode = scalar(base_fm["fields"].get("mode", ""))
            if resolved_runtime == "opencode" and not custom:
                if not outcome.mode:
                    fail(f"opencode 基类缺 mode：{base_path}")
                elif outcome.project_mode != outcome.mode:
                    fail(
                        f"核心角色 opencode mode 必须等于基类 {outcome.mode!r}"
                        f"（项目为 {outcome.project_mode!r}）"
                    )

            if resolved_runtime == "qoder":
                unsupported = sorted(set(base_fm["order"]) - QODER_BASE_FIELDS)
                if unsupported:
                    fail(
                        f"Qoder 基类 frontmatter 无法表达：{unsupported}；"
                        "只支持 name/description/disallowedTools（不静默忽略）"
                    )
                outcome.disallowed_tools = parse_list(base_fm, "disallowedTools")

            tokens = token_context(root, config, outcome.skill_root)
            rendered_base = render_tokens(base_body, tokens)
            rendered_overlay = render_tokens(parsed["overlay_body"], tokens)
            outcome.rendered_base_body = rendered_base
            outcome.rendered_overlay_body = rendered_overlay
        else:
            tokens = token_context(root, config, outcome.skill_root)
            rendered_base = ""
            rendered_overlay = render_tokens(parsed["overlay_body"], tokens)
            outcome.rendered_overlay_body = rendered_overlay

        if outcome.rendered_overlay_body.strip():
            composed = (
                outcome.rendered_base_body.rstrip("\n")
                + OVERLAY_SEPARATOR
                + outcome.rendered_overlay_body.strip("\n")
                + "\n"
            )
        else:
            composed = outcome.rendered_base_body
            if composed and not composed.endswith("\n"):
                composed += "\n"
        outcome.composed_body = composed
        report["composedSha256"] = sha256_bytes(composed.encode("utf-8"))

        base_lines, base_headings = parse_headings(outcome.rendered_base_body)
        _, overlay_headings = parse_headings(outcome.rendered_overlay_body)
        report["baseHeadings"] = [title for _, _, title in base_headings]
        report["appendedHeadings"] = [title for _, _, title in overlay_headings]
        base_h2 = {title for _, level, title in base_headings if level >= 2}
        overlay_h2: list[str] = []
        for _, level, title in overlay_headings:
            if level >= 2 and title in base_h2 and title not in overlay_h2:
                overlay_h2.append(title)
        report["duplicateHeadings"] = overlay_h2

        # 受保护标题：`## 引用` 及含规则指针的基类 H2–H6
        protected: set[str] = set()
        pointer_values = (
            tokens.get("crew_rules", ""),
            tokens.get("crew_acceptance", ""),
        )
        for index, (line_no, level, title) in enumerate(base_headings):
            if level < 2:
                continue
            end = base_headings[index + 1][0] if index + 1 < len(base_headings) else len(base_lines)
            section = "\n".join(base_lines[line_no:end])
            if (
                title == "引用"
                or "{{crew_rules}}" in section
                or "{{crew_acceptance}}" in section
                or "{{skill_root}}" in section
                or any(value and value in section for value in pointer_values)
            ):
                protected.add(title)
        report["protectedHeadingConflicts"] = [
            title for title in overlay_h2 if title in protected
        ]
        for title in report["protectedHeadingConflicts"]:
            fail(
                f"受保护标题冲突：项目覆盖出现与基类同名的 `{title}`；"
                "请改用项目独有标题（如 `## 项目补充`），base 规则指针不可覆盖"
            )
        report["warnings"] = [
            f"项目覆盖与基类重复标题（非受保护，WARN）：{title}；建议改用项目独有标题"
            for title in overlay_h2 if title not in protected
        ]

        rules_path = tokens.get("crew_rules", "")
        acceptance_path = tokens.get("crew_acceptance", "")
        report["basePointersPresent"] = bool(
            rules_path and acceptance_path
            and rules_path in outcome.rendered_base_body
            and acceptance_path in outcome.rendered_base_body
        )
        if not report["basePointersPresent"]:
            fail("基类规则指针缺失：渲染后的 base 未包含 crew_rules/crew_acceptance 实际路径")

        unresolved = sorted(set(RESIDUAL_TOKEN_RE.findall(composed)))
        report["unresolvedTokens"] = unresolved
        if unresolved:
            fail(f"组合文本存在未解析 token：{unresolved}")
        report["residualSkillRootTokens"] = sorted(
            set(re.findall(r"\{\{skill_root\}\}", composed))
        )
        if report["residualSkillRootTokens"]:
            fail(f"组合文本残留 {{{{skill_root}}}}：{report['residualSkillRootTokens']}")
    except DispatchError as exc:
        fail(str(exc))
    except OSError as exc:
        fail(f"读取失败：{exc}")
    report["errors"] = outcome.errors
    return outcome


# ---------------------------------------------------------------------------
# 运行时命令
# ---------------------------------------------------------------------------

def require_herdr() -> str:
    path = shutil.which("herdr")
    if not path:
        raise DispatchError("未找到 herdr；先安装 Herdr 并确认 PATH", 3)
    return path


def run_herdr(argv: list[str]) -> str:
    require_herdr()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise DispatchError(f"herdr 调用失败：{exc}", 3) from exc
    if proc.returncode != 0:
        raise DispatchError(
            f"herdr 命令失败（rc={proc.returncode}）：{' '.join(argv)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def herdr_agent_names() -> list[str]:
    output = run_herdr(["herdr", "agent", "list"])
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise DispatchError(f"herdr agent list 输出非 JSON：{exc}", 3) from exc
    result = data.get("result") if isinstance(data, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return []
    return [agent["name"] for agent in agents
            if isinstance(agent, dict) and isinstance(agent.get("name"), str)]


RECEIPT_LINE = "请完成处置后回执到发送方 inbox（R2），并注明 receipt_id。"
GENERATION_RE = re.compile(r"-(\d+)$")


def herdr_agents() -> list[dict]:
    output = run_herdr(["herdr", "agent", "list"])
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise DispatchError(f"herdr agent list 输出非 JSON：{exc}", 3) from exc
    result = data.get("result") if isinstance(data, dict) else None
    agents = result.get("agents") if isinstance(result, dict) else None
    if not isinstance(agents, list):
        return []
    return [agent for agent in agents if isinstance(agent, dict)]


def pane_focus_state(name: str, agents: list[dict] | None = None
                     ) -> tuple[bool | None, str, bool]:
    """目标 agent 的 pane 是否聚焦（T37）；None=无法判定，调用方按 fail-safe 不注入。

    返回 (focused, reason, present)：present=False 表示目标不在本机 herdr agent list
    （没有可被覆盖的 pane，调用方可按原路径投递）。解析链：`herdr agent list` 取
    pane_id → `herdr pane list` 取该 pane 的 focused；pane list 不可用、pane 缺失或
    focused 字段缺失一律返回 None + 原因。agents 传入时跳过重复的 agent list 调用。
    """
    if agents is None:
        try:
            agents = herdr_agents()
        except DispatchError as exc:
            return None, f"herdr agent list 不可用：{exc}", True
    entry = resolve_agent_entry(agents, name)
    if entry is None:
        return None, f"目标 {name} 不在本机 herdr agent list", False
    pane_id = str(entry.get("pane_id") or "").strip()
    if not pane_id:
        return None, f"agent list 缺 {name} 的 pane_id", True
    try:
        output = run_herdr(["herdr", "pane", "list"])
    except DispatchError as exc:
        return None, f"herdr pane list 不可用：{exc}", True
    try:
        data = json.loads(output)
    except ValueError as exc:
        return None, f"herdr pane list 输出非 JSON：{exc}", True
    result = data.get("result") if isinstance(data, dict) else None
    panes = result.get("panes") if isinstance(result, dict) else None
    if not isinstance(panes, list):
        return None, "herdr pane list 缺 panes", True
    pane = next((item for item in panes
                 if isinstance(item, dict) and str(item.get("pane_id") or "") == pane_id), None)
    if pane is None:
        return None, f"herdr pane list 未找到 pane {pane_id}", True
    focused = pane.get("focused")
    if not isinstance(focused, bool):
        return None, f"pane {pane_id} 缺 focused 字段", True
    return focused, "", True


def herdr_notify(title: str, body: str) -> bool:
    """best-effort Herdr 通知（T37：聚焦回退时告知 Captain）；失败不阻断。

    返回通知是否真实送达：`herdr notification show` 非 0 退出按失败返回 False，
    供审计字段记录真实结果（A-3）。
    """
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


def role_name_matches(name: str, role: str) -> bool:
    """角色命名约定：helm 名称含 helm；firstmate 含 firstmate 或以 -firstmate 结尾；
    其他角色等于 <role> 或以 -<role> 结尾（均大小写不敏感）。"""
    lowered, role_l = name.lower(), role.lower()
    if role_l == "helm":
        return "helm" in lowered
    if role_l == "firstmate":
        return lowered.endswith("-firstmate") or "firstmate" in lowered
    return lowered == role_l or lowered.endswith(f"-{role_l}")


def generation_of(name: str) -> int:
    """名称尾部 `-<整数>` 为代际；无数字后缀视为第 1 代。"""
    match = GENERATION_RE.search(name)
    return int(match.group(1)) if match else 1


def resolve_notify_target(agents: list[dict], workspace: str | None, role: str) -> str:
    """按 workspace + role 选当前活跃 helm/角色，返回 agent 名。

    helm 按名称尾部数字取最新代际（最新代际须唯一）；其他角色要求唯一匹配，多实例
    并存时用 --agent 显式指定。agent_status 归一化（strip+lower）后仅 idle/working
    视为活跃；blocked 单独拒绝，缺失/空白/done/unknown/其他值一律 rc=1 拒绝。
    """
    candidates = [agent for agent in agents
                  if isinstance(agent.get("name"), str)
                  and role_name_matches(agent["name"], role)]
    workspaces = {str(agent.get("workspace_id") or "") for agent in candidates}
    if workspace:
        candidates = [agent for agent in candidates
                      if str(agent.get("workspace_id") or "") == workspace]
    elif len(workspaces) > 1:
        raise DispatchError(
            f"检测到多个 workspace 的 {role} 候选"
            f"（{', '.join(sorted(w or '?' for w in workspaces))}）；请用 --workspace 显式指定",
            1)
    if not candidates:
        scope = f"workspace={workspace} " if workspace else ""
        raise DispatchError(
            f"未找到匹配 {scope}role={role} 的 agent；用 herdr agent list 核实命名与活跃状态", 1)
    if role.lower() == "helm":
        latest_gen = max(generation_of(agent["name"]) for agent in candidates)
        latest = [agent for agent in candidates if generation_of(agent["name"]) == latest_gen]
        if len(latest) > 1:
            names = ", ".join(agent["name"] for agent in latest)
            raise DispatchError(
                f"最新代际（第 {latest_gen} 代）不唯一：{names}；"
                "用 herdr agent list 核实后清理重复 agent", 1)
        target = latest[0]
    else:
        if len(candidates) > 1:
            names = ", ".join(agent["name"] for agent in candidates)
            raise DispatchError(
                f"workspace={workspace or '?'} 匹配到多个 {role} agent：{names}；"
                "多流并存请用 --agent <名> 指定", 1)
        target = candidates[0]
    raw_status = target.get("agent_status")
    status = str(raw_status or "").strip().lower()
    if status == "blocked":
        raise DispatchError(
            f"目标处于 blocked（{target['name']}，agent_status=blocked），"
            "herdr 会拒绝投递；先处置 blocked 或改用 --agent", 1)
    if status not in ("idle", "working"):
        if raw_status is None:
            shown = "缺失/None"
        else:
            shown = str(raw_status).strip() or "空白"
        raise DispatchError(
            f"目标不可用（{target['name']}，agent_status={shown}）；"
            "请先确认当前活跃 helm / 用 herdr agent list 核实", 1)
    return target["name"]


# —— 跨项目通知落收件方 inbox（T28）——
NOTIFY_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def project_identity(root: Path) -> str:
    """发送方项目标识：配置 projectId > 目录名（配置缺失不阻断 notify）。"""
    try:
        config = load_config(root)
    except DispatchError:
        return root.name
    project_id = str(config.get("projectId") or "").strip()
    return project_id or root.name


def sender_agent_name() -> str:
    return (os.environ.get("CREW_AGENT_NAME") or os.environ.get("INONE_AGENT_NAME")
            or "dispatch").strip() or "dispatch"


def resolve_agent_entry(agents: list[dict], name: str) -> dict | None:
    return next((agent for agent in agents if agent.get("name") == name), None)


def resolve_peer_inbox(entry: dict | None) -> tuple[Path | None, str]:
    """由 herdr agent 条目解析收件方 `<git-common-dir>/crew/inbox`。

    返回 (inbox_dir, reason)；失败时 inbox_dir=None，reason 供输出/日志显式降级告知。
    """
    if not isinstance(entry, dict):
        return None, "目标不在本机 herdr agent list"
    cwd = str(entry.get("cwd") or entry.get("foreground_cwd") or "").strip()
    if not cwd:
        return None, "herdr agent list 缺 cwd"
    if not Path(cwd).is_dir():
        return None, f"目标 cwd 不存在：{cwd}"
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 调用失败：{exc}"
    if proc.returncode != 0 or not proc.stdout.strip():
        return None, f"目标不是 git 仓库（cwd={cwd}）"
    common = Path(proc.stdout.strip())
    if not common.is_absolute():
        common = (Path(cwd) / common).resolve()
    return common / "crew" / "inbox", ""


def notification_id_for(source: str, target: str, message: str,
                        receipt_required: bool) -> str:
    """内容寻址的通知 id：同来源/目标/正文/回执要求重复发送即幂等去重。"""
    material = "\x1f".join(
        [source, target, message, "1" if receipt_required else "0"])
    return "notify-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def write_peer_notification(inbox: Path, payload: dict) -> tuple[str, Path]:
    path = inbox / f"{payload['notification_id']}.json"
    if path.exists():
        return "dedup", path
    write_json_atomic(path, payload)
    return "written", path


def cmd_notify_ack(ident: str, root: Path) -> int:
    """把收件方 inbox 中指定通知标记 processed（未处理口径的终结动作）。"""
    ident = ident.strip()
    if not ident or not NOTIFY_ID_RE.match(ident):
        raise DispatchError(f"非法通知 id：{ident!r}", 1)
    inbox = crew_dir(root) / "inbox"
    matches: list[tuple[Path, dict]] = []
    if inbox.is_dir():
        for path in sorted(inbox.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict) or data.get("kind") != "notification":
                continue
            if data.get("notification_id") == ident or path.stem == ident:
                matches.append((path, data))
    if not matches:
        raise DispatchError(f"收件方 inbox 未找到通知：{ident}（{inbox}）", 1)
    path, data = matches[0]
    if str(data.get("status") or "").strip().lower() == "processed":
        print(f"通知已处理（no-op）：{ident}")
        return 0
    data["status"] = "processed"
    data["processed_ts"] = utc_now()
    data["processed_by"] = sender_agent_name()
    write_json_atomic(path, data)
    append_log(root, {
        "ts": data["processed_ts"],
        "action": "notify-ack",
        "notification_id": data.get("notification_id"),
        "target": data.get("target"),
        "path": str(path),
        "result": "ok",
    })
    print(f"通知已标记 processed：{ident}（{path}）")
    return 0


def extract_pane_id(data: object, keys: tuple[str, ...] = ("pane_id", "paneId")) -> str | None:
    if isinstance(data, dict):
        for key in keys:
            value = data.get(key)
            if isinstance(value, str) and ":" in value:
                return value
        for value in data.values():
            found = extract_pane_id(value, keys)
            if found:
                return found
    elif isinstance(data, list):
        for value in data:
            found = extract_pane_id(value, keys)
            if found:
                return found
    return None


def parse_pane_id(output: str, action: str) -> str:
    try:
        data = json.loads(output)
    except ValueError as exc:
        raise DispatchError(f"{action} 输出非 JSON：{output.strip()!r}", 3) from exc
    # herdr 响应根 `id` 是命令标识（cli:pane:split），真实 pane 在 result.pane.pane_id；
    # 优先全树找 pane_id/paneId，找不到才退回 id 兼容旧格式。
    pane_id = extract_pane_id(data) or extract_pane_id(data, ("id",))
    if not pane_id:
        raise DispatchError(f"{action} 未返回 pane_id：{output.strip()!r}", 3)
    return pane_id


def extract_tab_id(data: object) -> str | None:
    """全树找 tab_id/tabId（herdr `tab create` 返回 result.tab.tab_id）。"""
    return extract_pane_id(data, ("tab_id", "tabId"))


def lookup_tab_id(pane_id: str | None) -> str | None:
    """herdr `tab create` 未返回 tab_id 时，按 pane_id 反查 `pane list` 兜底。"""
    if not pane_id:
        return None
    try:
        data = json.loads(run_herdr(["herdr", "pane", "list"]))
    except (DispatchError, ValueError):
        return None
    result = data.get("result") if isinstance(data, dict) else None
    panes = result.get("panes") if isinstance(result, dict) else None
    if not isinstance(panes, list):
        return None
    for pane in panes:
        if not isinstance(pane, dict) or pane.get("pane_id") != pane_id:
            continue
        tab_id = pane.get("tab_id")
        if isinstance(tab_id, str) and tab_id:
            return tab_id
    return None


# ---------------------------------------------------------------------------
# 注册表与 agent-log
# ---------------------------------------------------------------------------

def registry_path(root: Path) -> Path:
    return crew_dir(root) / "dispatch-agents.json"


def panes_path(root: Path) -> Path:
    return crew_dir(root) / "dispatch-panes.json"


def load_registry(root: Path) -> dict:
    path = registry_path(root)
    if not path.is_file():
        return {}
    try:
        data = load_json(path)
    except (OSError, ValueError) as exc:
        raise DispatchError(f"托管注册表损坏：{path}：{exc}") from exc
    return data if isinstance(data, dict) else {}


def save_registry(root: Path, registry: dict) -> None:
    write_json_atomic(registry_path(root), registry)


def load_panes(root: Path) -> dict:
    path = panes_path(root)
    if not path.is_file():
        return {}
    try:
        data = load_json(path)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_panes(root: Path, panes: dict) -> None:
    write_json_atomic(panes_path(root), panes)


def project_id(config: dict, root: Path) -> str:
    raw = config.get("projectId")
    if not raw:
        source = str(config.get("projectName") or root.name).strip().lower()
        raw = re.sub(r"[^a-z0-9-]+", "-", source).strip("-")
        if raw and not raw[0].isalpha():
            raw = f"p-{raw}"
    if not isinstance(raw, str) or not ROLE_ID_RE.match(raw):
        raise DispatchError(f"projectId 非法：{raw!r}（要求 {ROLE_ID_RE.pattern}）")
    return raw


def custom_alias(config: dict, role_id: str) -> str:
    hooks = _as_dict(config.get("hooks"))
    entries = hooks.get("customRoles")
    if not isinstance(entries, list):
        return role_id
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id") == role_id:
            alias = entry.get("alias")
            if isinstance(alias, str) and alias:
                return alias
    return role_id


def agent_name_for(config: dict, role_id: str, custom: bool, instance: int | None,
                   root: Path) -> str:
    helm = _as_dict(config.get("helm"))
    if role_id == "helm" and helm.get("agentName"):
        name = str(helm.get("agentName"))
        if not INSTANCE_NAME_RE.match(name):
            raise DispatchError(f"helm.agentName 非法：{name!r}（要求 {INSTANCE_NAME_RE.pattern}）")
        return name
    suffix = custom_alias(config, role_id) if custom else role_id
    name = f"{project_id(config, root)}-{suffix}"
    if instance is not None:
        if instance < 1:
            raise DispatchError("--instance 必须是正整数")
        name += f"-{instance}"
    return name


def ensure_role_enabled(config: dict, role_id: str, custom: bool) -> list[str]:
    errors: list[str] = []
    roles = _as_dict(config.get("roles"))
    enabled = roles.get("enabled")
    if isinstance(enabled, list) and enabled and role_id not in enabled:
        errors.append(f"角色 {role_id} 不在 roles.enabled 中；先改配置再启动")
    if custom:
        hooks = _as_dict(config.get("hooks"))
        entries = hooks.get("customRoles")
        match = [entry for entry in entries if isinstance(entry, dict) and entry.get("id") == role_id] \
            if isinstance(entries, list) else []
        if not match:
            errors.append(f"自定义角色 {role_id} 未在 hooks.customRoles 登记（禁用）")
        elif not match[0].get("enabled"):
            errors.append(f"自定义角色 {role_id} 在 hooks.customRoles 中未启用")
    return errors


def run_id() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(3)


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def print_json(data: object) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def print_runtime_resolution_human(report: dict) -> None:
    source = report.get("runtimeResolvedFrom")
    if source == "flag":
        print("runtime 来源：--runtime 显式指定")
    elif source == "single-runtime":
        print("runtime 来源：仅一套 runtime 存在该角色（未指定 --runtime）")
    elif isinstance(source, str) and source.startswith("registry:"):
        print(f"runtime 自动解析：{source}（未指定 --runtime，按注册表最新启动记录）")


def print_compose_human(report: dict) -> None:
    print(f"组合角色：{report['role']}（{report['runtime']}，delivery={report['delivery']}）")
    print_runtime_resolution_human(report)
    if report["baseSource"]:
        print(f"基类：{report['baseSource']} sha256={report['baseSha256']}")
    print(f"项目覆盖：{report['overlayPath']} sha256={report['overlaySha256']}")
    print(f"组合 sha256：{report['composedSha256']}")
    print(f"base 标题：{', '.join(report['baseHeadings'])}")
    print(f"追加标题：{', '.join(report['appendedHeadings'])}")
    print(f"组合 gates：{report['composedGates']}")
    if report["duplicateHeadings"]:
        print(f"重复标题（H2–H6）：{report['duplicateHeadings']}")
    for warning in report.get("warnings", []):
        print(f"[警告] {warning}")
    if report["protectedHeadingConflicts"]:
        print(f"受保护标题冲突：{report['protectedHeadingConflicts']}")
    for error in report["errors"]:
        print(f"[拒绝] {error}")


def cmd_compose(args: argparse.Namespace, root: Path) -> int:
    outcome = build_compose(root, args.role, args.runtime)
    if args.json:
        print_json(outcome.report)
    else:
        print_compose_human(outcome.report)
    return 0 if outcome.ok else 1


def deliver_qoder(outcome: ComposeOutcome, name: str, pane: str,
                  run_dir: Path, permission_mode: str | None,
                  model: str | None = None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    qoder_dir = run_dir / "qoder"
    qoder_dir.mkdir(parents=True, exist_ok=True)
    composed_path = run_dir / f"{outcome.role_id}.md"
    base_path = qoder_dir / "base.md"
    overlay_path = qoder_dir / "overlay.md"
    composed_path.write_text(outcome.composed_body, encoding="utf-8")
    base_path.write_text(outcome.rendered_base_body, encoding="utf-8")
    overlay_path.write_text(outcome.rendered_overlay_body, encoding="utf-8")
    agent_rel = Path(".qoder/agents/crew") / f"{outcome.role_id}.md"
    agent_path = outcome.root / agent_rel
    if not agent_path.is_file() or outcome.composed_body not in agent_path.read_text(encoding="utf-8"):
        raise DispatchError(
            f"组合基础代理缺失或过期：{agent_rel.as_posix()}；先运行 init --yes 或 upgrade --apply")
    argv = [
        "herdr", "agent", "start", name,
        "--kind", "qodercli",
        "--pane", pane,
        "--",
        "--agent", f"crew-{outcome.role_id}",
    ]
    for tool in outcome.disallowed_tools:
        argv.extend(["--disallowed-tools", tool])
    if model:
        argv.extend(["-m", model])
    if permission_mode:
        argv.extend(["--permission-mode", permission_mode])
    # Qoder 会话名对齐 Herdr/dispatch 启动名，保证 SendMessage/peer 可互认
    argv.extend(["--name", name])
    run_herdr(argv)
    return {
        "delivery": "composed-agent",
        "composePath": str(run_dir),
        "promptPath": str(composed_path),
        "composedPath": str(agent_path),
        "agentFile": str(agent_path),
    }


def deliver_opencode(outcome: ComposeOutcome, name: str, pane: str, run_dir: Path,
                     model: str | None = None) -> dict:
    run_dir.mkdir(parents=True, exist_ok=True)
    composed_path = run_dir / f"{outcome.role_id}.md"
    composed_path.write_text(outcome.composed_body, encoding="utf-8")
    agent_rel = Path(".opencode/agent") / f"crew-{outcome.role_id}.md"
    agent_path = outcome.root / agent_rel
    if not agent_path.is_file() or outcome.composed_body not in agent_path.read_text(encoding="utf-8"):
        raise DispatchError(
            f"组合基础代理缺失或过期：{agent_rel.as_posix()}；先运行 init --yes 或 upgrade --apply")
    run_herdr([
        "herdr", "agent", "start", name,
        "--kind", "opencode",
        "--pane", pane,
        "--",
        "--agent", f"crew-{outcome.role_id}",
        *(["-m", model] if model else []),
    ])
    return {
        "delivery": "composed-agent",
        "composePath": str(run_dir),
        "promptPath": str(composed_path),
        "composedPath": str(agent_path),
        "agentFile": str(agent_path),
    }


def cmd_start(args: argparse.Namespace, root: Path) -> int:
    config = load_config(root)
    preflight = config_preflight_errors(root, config)
    if preflight:
        if args.json:
            print_json({"errors": preflight})
        for error in preflight:
            print(f"[拒绝] {error}", file=sys.stderr)
        return 1
    outcome = build_compose(root, args.role, args.runtime)
    if not outcome.ok:
        if args.json:
            print_json(outcome.report)
        else:
            print_compose_human(outcome.report)
        return 1
    runtime = outcome.runtime
    assert runtime is not None
    errors = ensure_role_enabled(config, args.role, outcome.custom)
    if runtime == "opencode":
        mode = outcome.project_mode
        if mode == "subagent":
            errors.append("opencode subagent 不允许 dispatch 直接启动；由 primary 通过 Task 派生")
    if errors:
        if args.json:
            print_json({"errors": errors, "compose": outcome.report})
        for error in errors:
            print(f"[拒绝] {error}", file=sys.stderr)
        return 1
    if not args.pane:
        hint = "目标 pane" if runtime == "qoder" else "split 源 pane（dispatch 负责创建带 OPENCODE_CONFIG_DIR 的 pane）"
        raise DispatchError(f"{runtime} start 需要 --pane（{hint}）")

    name = args.name
    if name is not None:
        if not INSTANCE_NAME_RE.match(name):
            raise DispatchError(f"--name 非法：{name!r}（要求 {INSTANCE_NAME_RE.pattern}）")
    else:
        name = agent_name_for(config, args.role, outcome.custom, args.instance, root)
    if name in herdr_agent_names():
        raise DispatchError(
            f"Herdr 实例名 {name} 已存在。三个可执行选项：①关闭旧 agent；"
            "②改 projectId；③改自定义角色 alias（仅自定义）。dispatch 不自动改名/杀进程"
        )

    run_dir = crew_dir(root) / "compose" / run_id()
    model = (args.model or "").strip()
    if runtime == "qoder":
        delivery = deliver_qoder(outcome, name, args.pane, run_dir,
                                 args.permission_mode, model)
    else:
        delivery = deliver_opencode(outcome, name, args.pane, run_dir, model)

    skill_root = outcome.skill_root
    base_rel = outcome.base_path.relative_to(skill_root).as_posix() if outcome.base_path else ""
    skill_version = str(config.get("skillVersion") or "")
    registry = load_registry(root)
    registry[name] = {
        "role": args.role,
        "runtime": runtime,
        "agent": name,
        "pane": delivery.get("pane", args.pane),
        "composePath": delivery["composePath"],
        "composedSha256": outcome.report["composedSha256"],
        "model": model,
        "startedAt": utc_now(),
    }
    save_registry(root, registry)
    row = {
        "ts": utc_now(),
        "action": "start",
        "role": args.role,
        "runtime": runtime,
        "agent": name,
        "model": model,
        "delivery": delivery["delivery"],
        "policySource": "base",
        "baseRef": f"{skill_version or 'unknown'}:{base_rel}",
        "baseSource": outcome.report["baseSource"],
        "baseSha256": outcome.report["baseSha256"],
        "overlayPath": outcome.report["overlayPath"],
        "overlaySha256": outcome.report["overlaySha256"],
        "composedSha256": outcome.report["composedSha256"],
        "composePath": delivery["composePath"],
        "promptPath": delivery.get("promptPath"),
        "composedPath": delivery.get("composedPath"),
        "agentFile": delivery.get("agentFile"),
        "capabilities": outcome.capabilities,
        "gates": outcome.gates,
        "result": "ok",
    }
    append_log(root, row)
    payload = {
        "agent": name,
        "role": args.role,
        "runtime": runtime,
        "runtimeResolvedFrom": outcome.report.get("runtimeResolvedFrom"),
        "model": model,
        "delivery": delivery["delivery"],
        "composedSha256": outcome.report["composedSha256"],
        "composePath": delivery["composePath"],
        "gates": outcome.gates,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"已启动 {name}（{runtime}，delivery={delivery['delivery']}）")
        print_runtime_resolution_human(outcome.report)
        print(f"组合产物：{delivery['composePath']}")
    return 0


def cmd_prompt(args: argparse.Namespace, root: Path) -> int:
    registry = load_registry(root)
    info = registry.get(args.agent)
    if not isinstance(info, dict):
        raise DispatchError(f"{args.agent} 不是 dispatch 托管的 agent；不能用 prompt 直发")
    focused, focus_reason, _present = pane_focus_state(args.agent)
    if focused is not False:
        append_log(root, {
            "ts": utc_now(),
            "action": "prompt",
            "agent": args.agent,
            "role": info.get("role"),
            "runtime": info.get("runtime"),
            "delivery": "none",
            "focus": "focused" if focused else "unknown",
            "focus_reason": focus_reason or None,
            "result": "refused",
        })
        if focused:
            raise DispatchError(
                f"目标 {args.agent} 的 pane 当前聚焦（Captain 正在使用）：已拒绝注入，"
                "避免覆盖输入。请让 Captain 切走焦点后重试，"
                "或改用 crew-msg.py write 落 inbox + 通知。", 2)
        raise DispatchError(
            f"无法判定目标 {args.agent} 的 pane 聚焦状态（{focus_reason}）："
            "按 fail-safe 拒绝注入。先检查 `herdr pane list`，恢复后重试。", 2)
    run_herdr(["herdr", "agent", "prompt", args.agent, args.message])
    append_log(root, {
        "ts": utc_now(),
        "action": "prompt",
        "agent": args.agent,
        "role": info.get("role"),
        "runtime": info.get("runtime"),
        "delivery": "herdr",
        "focus": "unfocused",
        "result": "ok",
    })
    print(f"已发送提示：{args.agent}" if not args.json else json.dumps(
        {"agent": args.agent, "result": "ok", "delivery": "herdr"}, ensure_ascii=False))
    return 0


def cmd_notify(args: argparse.Namespace, root: Path) -> int:
    """跨项目通知：先落收件方 inbox，再 herdr agent prompt；不查/不写注册表，
    不 start/close/碰 pane/tab。目标 pane 聚焦或状态无法判定时不注入（T37），
    保留 inbox 并推送 Herdr 通知；收件方 inbox 解析失败时显式告知。"""
    if args.ack:
        if args.message or args.agent or args.workspace or args.role:
            raise DispatchError("--ack 只能单独使用（不发送、不解析目标）", 1)
        return cmd_notify_ack(args.ack, root)
    if not args.message:
        raise DispatchError("缺少 --message（或使用 --ack <通知 id>）", 1)
    receipt_required = not args.no_require_receipt
    message = args.message
    if receipt_required and not message.rstrip("\n").endswith(RECEIPT_LINE):
        message = message.rstrip("\n") + "\n" + RECEIPT_LINE
    if args.agent:
        if args.workspace or args.role:
            raise DispatchError("--agent 与 --workspace/--role 不能同时使用", 1)
        target, resolution = args.agent, "literal"
        try:
            agents = herdr_agents()
        except DispatchError as exc:
            agents = []
            target_entry = None
            list_error = f"herdr agent list 不可用：{exc}"
        else:
            target_entry = resolve_agent_entry(agents, target)
            list_error = ""
    else:
        if not args.role:
            raise DispatchError("--workspace/--role 解析模式必须提供 --role", 1)
        agents = herdr_agents()
        target = resolve_notify_target(agents, args.workspace, args.role)
        resolution = "resolved"
        target_entry = resolve_agent_entry(agents, target)
        list_error = ""

    # 文件真源先落盘（R1/P3）；解析失败不阻断 prompt，但必须在输出与日志显式说明。
    inbox, inbox_reason = resolve_peer_inbox(target_entry)
    if inbox is None and list_error:
        inbox_reason = list_error
    inbox_status, inbox_path = "skipped", None
    if inbox is not None:
        source_project = project_identity(root)
        from_agent = sender_agent_name()
        payload_file = {
            "kind": "notification",
            "notification_id": notification_id_for(
                f"{source_project}:{from_agent}", target, message, receipt_required),
            "from": f"{source_project}:{from_agent}",
            "from_project": source_project,
            "from_agent": from_agent,
            "from_root": str(root),
            "target": target,
            "target_project": str(target_entry.get("cwd")
                                  or target_entry.get("foreground_cwd") or ""),
            "target_inbox": str(inbox),
            "ts": utc_now(),
            "message": message,
            "receipt_required": receipt_required,
            "status": "pending",
        }
        try:
            inbox_status, inbox_path = write_peer_notification(inbox, payload_file)
        except OSError as exc:
            inbox_status, inbox_path = "skipped", None
            inbox_reason = f"收件方 inbox 写入失败：{exc}"

    focused, focus_reason, present = pane_focus_state(
        target, None if list_error else agents)
    prompt_skipped = None
    notified = False
    channel = "herdr"
    skip_reason = ""
    if focused is True or (focused is None and present):
        channel = "inbox_fallback"
        prompt_skipped = "focused" if focused else "unknown"
        skip_reason = (
            "目标 pane 聚焦（Captain 正在使用）"
            if focused
            else f"无法判定目标 pane 聚焦状态（{focus_reason}）"
        )
        notified = herdr_notify(
            "跨项目通知（未注入）",
            f"{target}：{skip_reason}；已跳过注入，请查收件方 inbox。\n{message}",
        )
    else:
        run_herdr(["herdr", "agent", "prompt", target, message])

    result = "ok"
    if prompt_skipped and inbox_status not in ("written", "dedup"):
        result = "degraded"
    row = {
        "ts": utc_now(),
        "action": "notify",
        "agent": target,
        "target": target,
        "delivery": channel,
        "target_resolution": resolution,
        "prompt_skipped": prompt_skipped,
        "focus_reason": focus_reason or None,
        "notified": notified,
        "receipt_required": receipt_required,
        "inbox_status": inbox_status,
        "inbox_path": str(inbox_path) if inbox_path else None,
        "inbox_reason": inbox_reason or None,
        "result": result,
    }
    if args.role:
        row["role"] = args.role
    append_log(root, row)
    payload = {
        "agent": target,
        "target": target,
        "delivery": channel,
        "target_resolution": resolution,
        "prompt_skipped": prompt_skipped,
        "notified": notified,
        "receipt_required": receipt_required,
        "inbox": {
            "status": inbox_status,
            "path": str(inbox_path) if inbox_path else None,
            "reason": inbox_reason or None,
        },
        "result": result,
    }
    if args.role:
        payload["role"] = args.role
    if args.workspace:
        payload["workspace"] = args.workspace
    if args.json:
        print_json(payload)
    elif prompt_skipped:
        print(f"{skip_reason}：已跳过注入")
        if inbox_status in ("written", "dedup"):
            label = "已落" if inbox_status == "written" else "已存在（去重）"
            print(f"通知{label}收件方 inbox：{inbox_path}")
        else:
            print(f"未落收件方 inbox：{inbox_reason or '原因未知'}")
        print(f"Herdr 通知：{'已发送' if notified else '未发送（HERDR_ENV 未启用或失败）'}")
    else:
        print(f"已通知：{target}")
        if inbox_status in ("written", "dedup"):
            label = "已落" if inbox_status == "written" else "已存在（去重）"
            print(f"通知{label}收件方 inbox：{inbox_path}")
        else:
            print(f"未落收件方 inbox：{inbox_reason or '原因未知'}"
                  "（已降级为 pane prompt）")
    if result != "ok":
        print("通知未送达：请人工转达或检查收件方项目", file=sys.stderr)
    return 0 if result == "ok" else 2


def cmd_close(args: argparse.Namespace, root: Path) -> int:
    registry = load_registry(root)
    info = registry.get(args.agent)
    if not isinstance(info, dict):
        raise DispatchError(f"{args.agent} 不是 dispatch 托管的 agent；不能用 close 直关")
    pane = info.get("pane")
    if not pane:
        raise DispatchError(f"注册表缺 pane：{args.agent}；先核对 herdr agent list")
    run_herdr(["herdr", "pane", "close", str(pane)])
    registry.pop(args.agent, None)
    save_registry(root, registry)
    append_log(root, {
        "ts": utc_now(),
        "action": "close",
        "agent": args.agent,
        "role": info.get("role"),
        "runtime": info.get("runtime"),
        "pane": pane,
        "result": "ok",
    })
    print(f"已关闭：{args.agent}（pane {pane}）")
    return 0


def cmd_status(args: argparse.Namespace, root: Path) -> int:
    try:
        state_dir = crew_dir(root)
    except CrewPathsError as exc:
        raise DispatchError(str(exc), 3) from exc
    agents = herdr_agent_names()
    registry = load_registry(root)
    payload = {
        "crewDir": str(state_dir),
        "agents": agents,
        "managed": registry,
    }
    if args.json:
        print_json(payload)
    else:
        print(f"crewDir：{state_dir}")
        print(f"Herdr agents：{', '.join(agents) or '（无）'}")
        print(f"dispatch 托管：{', '.join(sorted(registry)) or '（无）'}")
    return 0


def discover_roles(root: Path) -> list[dict]:
    entries: list[dict] = []
    patterns = [
        ("qoder", False, ".qoder/agents/*.md"),
        ("qoder", True, ".qoder/agents/custom/*.md"),
        ("opencode", False, ".opencode/agent/*.md"),
        ("opencode", True, ".opencode/agent/custom-*.md"),
    ]
    for runtime, custom, pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if runtime == "opencode" and custom is False and path.name.startswith("custom-"):
                continue
            text = path.read_text(encoding="utf-8")
            _, body = split_frontmatter(text)
            matches = DESCRIPTOR_RE.findall(body)
            entry = {
                "path": path.relative_to(root).as_posix(),
                "runtime": runtime,
                "core": not custom,
                "id": None,
                "extends": None,
                "enabled": True,
                "errors": [],
            }
            if len(matches) != 1:
                entry["errors"].append(f"描述符数量={len(matches)}（必须恰好一个）")
                entries.append(entry)
                continue
            try:
                descriptor = json.loads(matches[0])
            except ValueError as exc:
                entry["errors"].append(f"描述符 JSON 非法：{exc}")
                entries.append(entry)
                continue
            if isinstance(descriptor, dict):
                entry["id"] = descriptor.get("id")
                entry["extends"] = descriptor.get("extends")
            entries.append(entry)
    return entries


def cmd_list(args: argparse.Namespace, root: Path) -> int:
    entries = discover_roles(root)
    if args.json:
        print_json({"roles": entries})
    else:
        for entry in entries:
            problems = f"  [{'; '.join(entry['errors'])}]" if entry["errors"] else ""
            print(f"{entry['runtime']:8} {str(entry['id'] or '?'):24} "
                  f"{entry['path']}{problems}")
    return 0


def cmd_tab(args: argparse.Namespace, root: Path) -> int:
    if args.tab_command == "create":
        argv = ["herdr", "tab", "create"]
        if args.workspace:
            argv.extend(["--workspace", args.workspace])
        if args.cwd:
            argv.extend(["--cwd", args.cwd])
        if args.label:
            argv.extend(["--label", args.label])
        if args.no_focus:
            argv.append("--no-focus")
        output = run_herdr(argv)
        data = json.loads(output)
        pane_id = extract_pane_id(data)
        tab_id = extract_tab_id(data) or lookup_tab_id(pane_id)
        result = "ok"
        if not tab_id:
            result = "partial"
            print(
                "dispatch: 警告：herdr tab create 未返回 tab_id，agent-log 记 partial"
                "（guard 将 fail-closed）",
                file=sys.stderr,
            )
        append_log(root, {
            "ts": utc_now(), "action": "tab", "op": "create",
            "tab": tab_id, "pane": pane_id, "label": args.label, "result": result,
        })
        print_json({"tab": tab_id, "pane": pane_id})
        return 0
    run_herdr(["herdr", "tab", "close", args.tab])
    append_log(root, {"ts": utc_now(), "action": "tab", "op": "close", "tab": args.tab, "result": "ok"})
    print(f"已关闭 tab：{args.tab}")
    return 0


def cmd_pane(args: argparse.Namespace, root: Path) -> int:
    if args.pane_command == "split":
        argv = ["herdr", "pane", "split", args.pane]
        if args.direction:
            argv.extend(["--direction", args.direction])
        if args.cwd:
            argv.extend(["--cwd", args.cwd])
        if args.no_focus:
            argv.append("--no-focus")
        for item in args.env or []:
            argv.extend(["--env", item])
        output = run_herdr(argv)
        new_pane = parse_pane_id(output, "herdr pane split")
        panes = load_panes(root)
        panes[new_pane] = {"env": list(args.env or []), "createdAt": utc_now()}
        save_panes(root, panes)
        append_log(root, {
            "ts": utc_now(), "action": "pane", "op": "split",
            "pane": new_pane, "source": args.pane, "result": "ok",
        })
        print_json({"pane": new_pane})
        return 0
    run_herdr(["herdr", "pane", "close", args.pane])
    panes = load_panes(root)
    panes.pop(args.pane, None)
    save_panes(root, panes)
    append_log(root, {
        "ts": utc_now(), "action": "pane", "op": "close",
        "pane": args.pane, "result": "ok",
    })
    print(f"已关闭 pane：{args.pane}")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dispatch.py",
        description="唯一派单入口：compose/start/prompt/close/notify/status/list/tab/pane",
    )
    parser.add_argument("--root", default=".", help="项目仓根（默认 cwd）")
    sub = parser.add_subparsers(dest="command", required=True)

    compose_p = sub.add_parser("compose", help="组合 dry-run（不启动、不写项目）")
    compose_p.add_argument("--role", required=True)
    compose_p.add_argument("--runtime", choices=["qoder", "opencode"])
    compose_p.add_argument("--json", action="store_true")
    compose_p.set_defaults(func=cmd_compose)

    start_p = sub.add_parser("start", help="启动角色（组合 + 运行时交付）")
    start_p.add_argument("--role", required=True)
    start_p.add_argument("--runtime", choices=["qoder", "opencode"])
    start_p.add_argument("--pane", help="Qoder 目标 pane；opencode 为 split 源 pane")
    start_p.add_argument("--name", help="显式 Herdr/dispatch 实例名（默认 helm 用配置 helm.agentName，其余派生）")
    start_p.add_argument("--instance", type=int, help="同角色多实例编号（Herdr 名后缀）")
    start_p.add_argument("--permission-mode", help="透传 qodercli --permission-mode（默认不传）")
    start_p.add_argument("--model", help="透传运行时模型（qoder/opencode：-m <model>；空不传）")
    start_p.add_argument("--json", action="store_true")
    start_p.set_defaults(func=cmd_start)

    prompt_p = sub.add_parser(
        "prompt",
        help="向 dispatch 托管的 agent 发提示（目标 pane 聚焦/无法判定时拒绝注入，rc=2）")
    prompt_p.add_argument("--agent", required=True)
    prompt_p.add_argument("--message", required=True)
    prompt_p.add_argument("--json", action="store_true")
    prompt_p.set_defaults(func=cmd_prompt)

    notify_p = sub.add_parser(
        "notify",
        help="跨项目通知：先落收件方 inbox，再 herdr agent prompt（目标聚焦则只落"
             " inbox + 通知，delivery=inbox_fallback）；"
             "--workspace+--role 解析活跃代际，--agent 直发，--ack 标记已处理")
    notify_p.add_argument("--agent", help="外部 agent 名（旧用法，直发）")
    notify_p.add_argument("--workspace", help="workspace_id（与 --role 同用）")
    notify_p.add_argument("--role",
                          help="角色名（与 --workspace 同用）；helm 按代际解析，"
                               "其他角色要求唯一匹配，多实例用 --agent")
    notify_p.add_argument("--message", help="通知正文（--ack 时不需要）")
    notify_p.add_argument("--ack", metavar="ID",
                          help="把收件方 inbox 中的通知标记 processed（终结未处理口径）")
    notify_p.add_argument("--no-require-receipt", action="store_true",
                          help="不自动追加回执要求行")
    notify_p.add_argument("--json", action="store_true")
    notify_p.set_defaults(func=cmd_notify)

    close_p = sub.add_parser("close", help="关闭 dispatch 托管的 agent")
    close_p.add_argument("--agent", required=True)
    close_p.add_argument("--json", action="store_true")
    close_p.set_defaults(func=cmd_close)

    status_p = sub.add_parser("status", help="角色/agent/工位状态")
    status_p.add_argument("--json", action="store_true")
    status_p.set_defaults(func=cmd_status)

    list_p = sub.add_parser("list", help="列出角色文件与可发现性")
    list_p.add_argument("--json", action="store_true")
    list_p.set_defaults(func=cmd_list)

    tab_p = sub.add_parser("tab", help="建/关 tab")
    tab_sub = tab_p.add_subparsers(dest="tab_command", required=True)
    tab_create = tab_sub.add_parser("create")
    tab_create.add_argument("--workspace")
    tab_create.add_argument("--cwd")
    tab_create.add_argument("--label")
    tab_create.add_argument("--no-focus", action="store_true")
    tab_create.set_defaults(func=cmd_tab)
    tab_close = tab_sub.add_parser("close")
    tab_close.add_argument("--tab", required=True)
    tab_close.set_defaults(func=cmd_tab)

    pane_p = sub.add_parser("pane", help="split/close pane")
    pane_sub = pane_p.add_subparsers(dest="pane_command", required=True)
    pane_split = pane_sub.add_parser("split")
    pane_split.add_argument("--pane", required=True)
    pane_split.add_argument("--direction", choices=["right", "down"])
    pane_split.add_argument("--cwd")
    pane_split.add_argument("--env", action="append")
    pane_split.add_argument("--no-focus", action="store_true")
    pane_split.set_defaults(func=cmd_pane)
    pane_close = pane_sub.add_parser("close")
    pane_close.add_argument("--pane", required=True)
    pane_close.set_defaults(func=cmd_pane)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"dispatch: 项目根不存在：{root}", file=sys.stderr)
        return 1
    try:
        return args.func(args, root)
    except DispatchError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return exc.code
    except CrewPathsError as exc:
        print(f"dispatch: {exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
