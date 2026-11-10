#!/usr/bin/env python3
"""init.py — agent-crew v2.2 安装 / bootstrap / 受管块刷新（幂等）。

行为契约（arch-proposal-v1 §7/§8、role-path-contract、impl-plan slice C）：
- 只安装必要脚本、AGENTS 受管块、配置、项目角色 stub、portfolio/CONTEXT/topology 骨架；
  不复制规则/验收/机制/plan 纪律，不安装 crew-lint，不安装第二份角色文件。
- 项目角色 stub 仅在文件缺失时生成（发现字段 + crew-role 描述符 + 最小正文）；
  已存在文件永不覆盖，`--force` 对 AGENTS/角色一律拒绝（rc=1、零写入、零新备份）。
- AGENTS 无标记：零写入 AGENTS，生成 docs/crew/AGENTS-block.pending.md 并返回 2；
  重复/嵌套/围栏/半对/其他标记行：fail closed（rc=1、零写入）。
- manifest（docs/crew/manifest.json）只登记 kind:file 受管脚本 + kind:block AGENTS 块
  + roleStubs + configHash；install-log.jsonl append-only。
- 锁：<common>/crew/init.lock（crew_paths.crew_dir）；并发第二个实例 rc=2 并记 locked。
- 原子写：同目录 temp + fsync + rename；覆盖受管脚本前备份到
  <common>/crew/backups/<UTC>/ + backup-manifest.json。
- `--refresh-block [--confirm]` 只重渲染 AGENTS 受管块；无 --confirm rc=2 且零写入；
  --confirm 后块外与 orchestration.json 字节不变，manifest 更新 blockSha256/renderVersion/configHash。

退出码：0 成功；1 校验/拒绝；2 待人工（pending/冲突/锁）；3 环境缺失。
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__（doctor runtime.worktree）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402

try:  # dispatch 与 init 同目录安装；缺失时用内置角色表兜底
    import dispatch as dispatch_lib  # noqa: E402
except Exception:  # noqa: BLE001
    dispatch_lib = None  # type: ignore[assignment]

SKILL_DIR = Path(__file__).resolve().parent.parent
TEMPLATES = SKILL_DIR / "templates"

MARK_START = "<!-- agent-crew:orchestration:start -->"
MARK_END = "<!-- agent-crew:orchestration:end -->"
MARK_PREFIX = "<!-- agent-crew:orchestration:"
SKILL_VERSION_FALLBACK = "2.2.3"

CONFIG_REL = Path(".planning/orchestration.json")
MANIFEST_REL = Path("docs/crew/manifest.json")
INSTALL_LOG_REL = Path("docs/crew/install-log.jsonl")
PENDING_REL = Path("docs/crew/AGENTS-block.pending.md")
DEFAULT_TOPOLOGY_REL = Path("docs/orchestration/topology.md")
BLOCK_LINE_LIMIT = 150

ROLE_ORDER = ["helm", "firstmate", "builder", "planner", "reviewer", "runner", "scout"]
ROLE_TITLES = {
    "helm": "Helm（总舵）",
    "firstmate": "First Mate（大副）",
    "builder": "Builder",
    "planner": "Planner",
    "reviewer": "Reviewer",
    "runner": "Runner",
    "scout": "Scout",
}
FALLBACK_ROLES: dict[str, dict] = {
    "helm": {"capabilities": ["read", "dispatch"],
             "gates": ["three-human-gates", "no-merge", "no-push"], "lifecycle": "persistent"},
    "firstmate": {"capabilities": ["read", "write-docs", "dispatch"],
                  "gates": ["three-human-gates", "no-merge", "no-push"], "lifecycle": "persistent"},
    "builder": {"capabilities": ["read", "write-code", "run-commands"],
                "gates": ["three-human-gates", "review-required"], "lifecycle": "keep-until"},
    "planner": {"capabilities": ["read", "write-docs"],
                "gates": ["three-human-gates"], "lifecycle": "one-shot"},
    "reviewer": {"capabilities": ["read", "report"],
                 "gates": ["three-human-gates", "report-only"], "lifecycle": "one-shot"},
    "runner": {"capabilities": ["read", "run-commands"],
               "gates": ["three-human-gates", "data-safety"], "lifecycle": "one-shot"},
    "scout": {"capabilities": ["read", "report"],
              "gates": ["three-human-gates", "report-only"], "lifecycle": "one-shot"},
}

# 受管脚本清单（不安装 crew-lint.py；不含 swap/rollback 历史脚本）
SCRIPT_MANIFEST = [
    "init.py",
    "dispatch.py",
    "doctor.py",
    "upgrade.py",
    "crew_paths.py",
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
]
COPY_BASENAMES = ("crew-rules.md", "crew-acceptance.md",
                  "orchestration-guide.md", "plan-discipline.md")

DEFAULT_ROLES = {"enabled": list(ROLE_ORDER), "instances": {}}
DEFAULT_HOOKS = {
    "paths": {"topology": DEFAULT_TOPOLOGY_REL.as_posix()},
    "roleExt": {},
    "customRoles": [],
    "gatePolicy": {"appendGates": []},
    "projectScripts": [],
}

DEFAULT_CONFIG: dict = {
    "schemaVersion": 1,
    "projectId": "",
    "projectName": "",
    "skillRoot": "",
    "skillVersion": "",
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
    "helm": {"tabLabel": "总舵", "expectedModel": "", "agentName": ""},
    "roles": DEFAULT_ROLES,
    "hooks": DEFAULT_HOOKS,
}

PLACEHOLDERS: list[tuple[tuple[str, ...], str]] = [
    (("projectId",), "project_id"),
    (("projectName",), "project_name"),
    (("skillRoot",), "skill_root"),
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
    (("topic",), "topic"),
]

EMPTY_FALLBACK: dict[str, str] = {
    "topic": "<topic>",
    "doc_req_path": "<需求文档路径>",
    "aop_reference": "（未配置——按 AOP 六维自审并记入 plan Decisions）",
}

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


class InitLocked(Exception):
    """另一个 init 持有锁。"""


class InitError(Exception):
    """输入/状态错误，按 rc=1 退出。"""


@dataclass
class Item:
    target: Path
    status: str = "新建"   # 新建/覆盖/跳过/冲突/待插入/拒绝
    note: str = ""


@dataclass
class Report:
    items: list[Item] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    conflicts: list[Item] = field(default_factory=list)

    def add(self, item: Item) -> None:
        self.items.append(item)
        if item.status == "冲突":
            self.conflicts.append(item)


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def read_skill_version(skill_dir: Path | None = None) -> str:
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
    return match.group(1).strip().strip("'\"") or SKILL_VERSION_FALLBACK


def is_inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def linked_worktree_root(path: Path) -> Path | None:
    """path 位于 linked worktree（祖先含 .git 文件而非目录）时返回该 worktree 根。"""
    resolved = path.resolve()
    for parent in (resolved, *resolved.parents):
        git = parent / ".git"
        if git.is_file():
            return parent
        if git.is_dir():
            return None
    return None


def atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if mode is not None:
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str, mode: int | None = None) -> None:
    atomic_write(path, text.encode("utf-8"), mode=mode)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return sha256_bytes(normalized.encode("utf-8"))


def sha256_file_raw(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def config_hash(config: dict) -> str:
    data = {key: value for key, value in config.items()
            if not key.startswith("_") and key != "skillVersion"}
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def project_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def strip_marker_lines(text: str) -> str:
    kept = [line for line in text.split("\n")
            if not line.strip().startswith(MARK_PREFIX)]
    return "\n".join(kept)


def marker_scan(text: str) -> dict:
    """返回 {'state': missing|pending|ok|invalid, 'body', 'errors', 'lines'}。"""
    lines = text.splitlines()
    in_fence = False
    starts: list[int] = []
    ends: list[int] = []
    others: list[tuple[int, str]] = []
    fenced: list[int] = []
    indented: list[int] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not stripped.startswith(MARK_PREFIX):
            continue
        if stripped == MARK_START:
            starts.append(index)
        elif stripped == MARK_END:
            ends.append(index)
        else:
            others.append((index + 1, stripped))
            continue
        if in_fence:
            fenced.append(index + 1)
        if line != stripped:
            indented.append(index + 1)
    if not starts and not ends and not others:
        return {"state": "pending", "body": "", "errors": [], "starts": [], "ends": []}
    errors: list[str] = []
    if len(starts) != 1 or len(ends) != 1:
        errors.append(f"标记必须恰好一对（start={len(starts)}, end={len(ends)}）")
    elif starts[0] >= ends[0]:
        errors.append("start 必须在 end 之前")
    if others:
        errors.append(f"出现其他 agent-crew:orchestration 标记行：{others}")
    if fenced:
        errors.append(f"标记位于代码围栏内：{fenced}")
    if indented:
        errors.append(f"标记前有缩进：{indented}")
    if errors:
        return {"state": "invalid", "body": "", "errors": errors,
                "starts": starts, "ends": ends}
    start, end = starts[0], ends[0]
    body_lines = [line.rstrip() for line in lines[start + 1:end]]
    while body_lines and not body_lines[0]:
        body_lines.pop(0)
    while body_lines and not body_lines[-1]:
        body_lines.pop()
    if not body_lines:
        return {"state": "invalid", "body": "", "errors": ["块体为空"],
                "starts": starts, "ends": ends}
    return {"state": "ok", "body": "\n".join(body_lines) + "\n", "errors": [],
            "starts": starts, "ends": ends}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_defaults(templates: Path | None = None) -> dict:
    path = (templates or TEMPLATES) / "project.config.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data = {key: value for key, value in data.items() if not key.startswith("_")}
        return _deep_merge(DEFAULT_CONFIG, data)
    except (OSError, ValueError):
        return json.loads(json.dumps(DEFAULT_CONFIG))


def _scrub_hooks(cfg: dict) -> None:
    hooks = cfg.get("hooks")
    if not isinstance(hooks, dict):
        cfg["hooks"] = json.loads(json.dumps(DEFAULT_HOOKS))
        return
    paths = hooks.get("paths")
    if not isinstance(paths, dict):
        paths = {}
    paths = {key: value for key, value in paths.items()
             if key in {"topology", "projectExt"}}
    paths.setdefault("topology", DEFAULT_TOPOLOGY_REL.as_posix())
    hooks["paths"] = paths
    for key, default in (("roleExt", {}), ("customRoles", []),
                         ("gatePolicy", {"appendGates": []}), ("projectScripts", [])):
        hooks.setdefault(key, json.loads(json.dumps(default)))


def _script_relative_root(root: Path, source_dir: Path | None = None) -> str:
    source = (source_dir or SKILL_DIR).resolve()
    try:
        rel = source.relative_to(root.resolve())
    except ValueError:
        return str(source)
    return rel.as_posix()


def resolve_source(root: Path) -> Path:
    """源解析：外部技能副本优先自身目录；安装副本（项目 scripts/）用配置 skillRoot。"""
    own = Path(__file__).resolve().parent.parent
    if own != root.resolve() and (own / "SKILL.md").is_file() and (own / "scripts").is_dir():
        return own
    path = root / CONFIG_REL
    if path.is_file():
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            config = {}
        raw = config.get("skillRoot") if isinstance(config, dict) else None
        if isinstance(raw, str) and raw.strip():
            candidate = Path(raw).expanduser()
            if not candidate.is_absolute():
                candidate = (root / candidate).resolve()
            if candidate.is_dir():
                return candidate
    return SKILL_DIR


def render_context(cfg: dict, root: Path | None = None) -> dict:
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
    skill_root = ctx.get("skill_root", "").rstrip("/")
    ctx["skill_root"] = skill_root
    ctx["crew_rules"] = f"{skill_root}/references/crew-rules.md" if skill_root else ""
    ctx["crew_acceptance"] = (
        f"{skill_root}/references/crew-acceptance.md" if skill_root else ""
    )
    return ctx


_SENTENCE_PUNCT = set("。；，、！？")


def _splice_list_token(text: str, token: str, value: str) -> str:
    parts: list[str] = []
    idx = 0
    for match in re.finditer(re.escape(token), text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        before = text[line_start:match.start()]
        newline = text.find("\n", match.end())
        line_end = newline if newline != -1 else len(text)
        tail = text[match.end():line_end].strip()
        pre = "\n" if before.strip() else ""
        if tail and all(ch in _SENTENCE_PUNCT for ch in tail):
            post = tail
        elif tail:
            post = "\n" + tail
        else:
            post = ""
        parts.append(text[idx:match.start()])
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
            text = _splice_list_token(text, token, rendered)
        else:
            text = text.replace(token, rendered)
    return text


# ---------------------------------------------------------------------------
# 锁 / 日志 / 备份
# ---------------------------------------------------------------------------

def _crew_dir(root: Path) -> Path:
    try:
        return crew_dir(root)
    except CrewPathsError as exc:
        raise InitError(f"运行时目录解析失败：{exc}") from exc


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def acquire_owner(root: Path) -> Path | None:
    """整个 init/refresh 期间的并发属主（pid 文件）；存活则拒绝，崩溃遗留可回收。"""
    crew = _crew_dir(root)
    crew.mkdir(parents=True, exist_ok=True)
    path = crew / "init.owner.json"
    for _ in range(2):
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            pid = None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                pid = int(data.get("pid"))
            except (OSError, ValueError, TypeError):
                pid = None
            if pid and pid != os.getpid() and _pid_alive(pid):
                return None
            try:
                path.unlink()
            except OSError:
                return None
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "startedAt": utc_now()}) + "\n")
        return path
    return None


def release_owner(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


@contextmanager
def init_lock(root: Path):
    crew = _crew_dir(root)
    crew.mkdir(parents=True, exist_ok=True)
    lock_path = crew / "init.lock"
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise InitLocked(str(exc)) from exc
        yield
    finally:
        if handle.closed is False:
            try:
                lock_path.unlink(missing_ok=True)  # 持锁删除：其他实例非阻塞退出
            except OSError:
                pass
        handle.close()


def append_install_log(root: Path, entry: dict) -> None:
    path = root / INSTALL_LOG_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def create_backup(root: Path, action: str, paths: list[Path],
                  extra: dict | None = None) -> str | None:
    existing = [path for path in paths if path.is_file()]
    if not existing:
        return None
    crew = _crew_dir(root)
    base = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_id = base
    counter = 1
    while (crew / "backups" / backup_id).exists():
        backup_id = f"{base}-{counter}"
        counter += 1
    directory = crew / "backups" / backup_id
    files: list[dict] = []
    for path in existing:
        rel = path.relative_to(root).as_posix()
        target = directory / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(path.read_bytes())
        files.append({"path": rel, "sha256": sha256_file_raw(path),
                      "size": path.stat().st_size})
    payload = {
        "schemaVersion": 1,
        "backupId": backup_id,
        "action": action,
        "createdAt": utc_now(),
        "root": str(root),
        "files": files,
    }
    if extra:
        payload.update(extra)
    atomic_write_text(directory / "backup-manifest.json",
                      json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return backup_id


def read_manifest(root: Path) -> dict | None:
    path = root / MANIFEST_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def load_config(root: Path, templates: Path | None = None) -> tuple[dict, dict | None]:
    """返回合并后的配置与磁盘原值；损坏时抛 InitError（不静默覆盖）。"""
    defaults = load_defaults(templates)
    path = root / CONFIG_REL
    if not path.exists():
        return defaults, None
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InitError(f"配置损坏（{path}）：{exc}；先修复或删除后重跑") from exc
    if not isinstance(existing, dict):
        raise InitError(f"配置必须是 JSON 对象：{path}")
    merged = _deep_merge(defaults, existing)
    return merged, existing


def prepare_config(root: Path, cfg: dict, existing: dict | None, skill_version: str,
                   source_dir: Path | None = None) -> dict:
    cfg = json.loads(json.dumps(cfg))
    cfg["projectName"] = cfg.get("projectName") or root.name
    cfg["projectId"] = cfg.get("projectId") or project_slug(cfg["projectName"])
    cfg["skillRoot"] = cfg.get("skillRoot") or _script_relative_root(
        root, source_dir or SKILL_DIR)
    cfg["skillVersion"] = skill_version
    roles = cfg.get("roles")
    if not isinstance(roles, dict) or not isinstance(roles.get("enabled"), list) \
            or not roles.get("enabled"):
        cfg["roles"] = json.loads(json.dumps(DEFAULT_ROLES))
    _scrub_hooks(cfg)
    return cfg


# ---------------------------------------------------------------------------
# 角色 stub / 脚本 / AGENTS
# ---------------------------------------------------------------------------

def role_spec(role: str) -> dict:
    if dispatch_lib is not None:
        spec = dispatch_lib.CORE_ROLES.get(role)
        if spec:
            return spec
    return FALLBACK_ROLES[role]


def role_stub_path(runtime: str, role: str) -> Path:
    if runtime == "qoder":
        return Path(".qoder/agents") / f"{role}.md"
    return Path(".opencode/agent") / f"{role}.md"


def composed_agent_path(runtime: str, role: str) -> Path:
    if runtime == "qoder":
        return Path(".qoder/agents/crew") / f"{role}.md"
    return Path(".opencode/agent") / f"crew-{role}.md"


def composed_agent_text(root: Path, runtime: str, role: str,
                        skill_root: Path | None = None) -> str:
    if dispatch_lib is None:
        raise InitError("dispatch 模块不可用，无法生成组合基础代理")
    try:
        outcome = dispatch_lib.build_compose(root, role, runtime, skill_root=skill_root)
    except Exception as exc:  # noqa: BLE001
        raise InitError(f"组合基础代理失败（{runtime}/{role}）：{exc}") from exc
    if not outcome.ok:
        raise InitError(f"组合基础代理失败（{runtime}/{role}）：{outcome.errors}")
    description = dispatch_lib.scalar(
        outcome.base_frontmatter["fields"].get("description", "")) or role
    if runtime == "qoder":
        descriptor = {
            "id": f"crew-{role}",
            "runtime": "qoder",
            "composed": True,
            "managed": True,
            "source": outcome.report.get("baseSource"),
            "override": outcome.role_path.relative_to(root).as_posix(),
        }
        return (
            "---\n"
            f"name: crew-{role}\n"
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            "---\n"
            "<!-- crew-role\n"
            f"{json.dumps(descriptor, ensure_ascii=False)}\n"
            "-->\n"
            f"{outcome.composed_body}"
        )
    raw = outcome.base_frontmatter["raw"].strip()
    return f"---\n{raw}\n---\n{outcome.composed_body}"


def regenerate_composed_agents(root: Path, skill_root: Path | None = None) -> list[dict]:
    config_path = root / CONFIG_REL
    config = json.loads(config_path.read_text(encoding="utf-8"))
    roles = config.get("roles") if isinstance(config.get("roles"), dict) else {}
    enabled = roles.get("enabled") if isinstance(roles.get("enabled"), list) else ROLE_ORDER
    generated: list[dict] = []
    for runtime in ("qoder", "opencode"):
        for role in enabled:
            try:
                dispatch_lib.locate_role(root, role, runtime)
            except Exception:  # noqa: BLE001
                continue
            try:
                text = composed_agent_text(root, runtime, role, skill_root=skill_root)
            except InitError:
                continue
            rel = composed_agent_path(runtime, role)
            target = root / rel
            atomic_write_text(target, text)
            generated.append({
                "path": rel.as_posix(), "runtime": runtime, "role": role,
                "managed": True, "sha256": sha256_text(text),
            })
    return generated


def role_stub_content(runtime: str, role: str) -> str:
    spec = role_spec(role)
    title = ROLE_TITLES[role]
    descriptor = {
        "id": role,
        "runtime": runtime,
        "extends": f"skill:{role}",
        "managed": False,
        "capabilities": list(spec["capabilities"]),
        "gates": [],
        "lifecycle": spec["lifecycle"],
        "prompt": role_stub_path(runtime, role).as_posix(),
    }
    description = (
        f"{title}（{runtime} 项目角色 {role}：权限与动作基类由技能提供，正文追加项目指令）"
    )
    if runtime == "qoder":
        frontmatter = (
            "---\n"
            f"name: {role}\n"
            f"description: {description}\n"
            "---\n"
        )
    else:
        frontmatter = (
            "---\n"
            f"description: {description}\n"
            "mode: primary\n"
            "---\n"
        )
    body = (
        "<!-- crew-role\n"
        + json.dumps(descriptor, ensure_ascii=False)
        + "\n-->\n"
        f"# {title}\n\n"
        f"项目角色 {role} / {runtime}：只在此追加项目自有指令；不要新增 `## 引用` 等受保护标题。\n"
    )
    return frontmatter + body


def script_source(name: str, source_dir: Path | None = None) -> Path:
    return (source_dir or SKILL_DIR) / "scripts" / name


def render_agents_section(templates: Path, ctx: dict) -> str:
    path = templates / "AGENTS-编排节.md"
    if not path.exists():
        raise InitError(f"未找到模板 {path}")
    raw = apply_template(path.read_text(encoding="utf-8"), ctx)
    body = strip_marker_lines(raw).strip("\n")
    if not body:
        raise InitError("AGENTS 编排节渲染为空")
    section = f"{MARK_START}\n{body}\n{MARK_END}"
    return section


def rendered_agents_text(current: str | None, section: str) -> str:
    if current is None:
        return AGENT_SKELETON_HEADER + "\n" + section + "\n"
    pre = current.split(MARK_START, 1)[0]
    post = current.split(MARK_END, 1)[1]
    return pre + section + post


def pending_text(section: str) -> str:
    indented = "\n".join("    " + line if line else line for line in section.splitlines())
    return f"""# AGENTS.md 编排节待插入（pending）

> init 检测到 `AGENTS.md` 存在但没有 agent-crew 受管标记：按契约零写入 AGENTS，只生成本文件。
> 完成下面三步后重跑 `python3 scripts/init.py`，由 init 接管受管块。

## 第一步：插到哪

- 打开项目根目录 `AGENTS.md`。
- 选择编排规则应处的位置：通常在项目自有规则之后、文档/命令清单之前；不要插进代码块或已有标记内。

## 第二步：插什么

把下面整段（含 start/end 标记各一行、行首无缩进）插入 `AGENTS.md`：

```markdown
{section}
```

## 第三步：插完怎么验

- 重跑 `python3 scripts/init.py --check`：不再提示 pending。
- 跑 `python3 scripts/doctor.py --root . --only agents.markers --json`：恰好一对标记。
- 跑 `python3 scripts/doctor.py --root . --only agents.block --json`：块体非空、≤{BLOCK_LINE_LIMIT} 行、无未解析 token。
"""


# ---------------------------------------------------------------------------
# 检测
# ---------------------------------------------------------------------------

def detect(root: Path, report: Report) -> None:
    if sys.version_info < (3, 10):
        report.warnings.append("python3 版本过低：需要 ≥3.10。")
    probe = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"], cwd=root,
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        report.warnings.append(
            "非 git 仓：运行时状态落 <root>/.crew；worktree-watch/agent-watch 监听不可用。"
        )


def forbidden_copies(root: Path, skill_root: str) -> list[str]:
    skill_path = Path(skill_root)
    if not skill_path.is_absolute():
        skill_path = (root / skill_path).resolve()
    copies: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel.startswith((".git/", ".planning/")):
            continue
        try:
            path.resolve().relative_to(skill_path)
            continue
        except ValueError:
            pass
        if path.name in COPY_BASENAMES:
            copies.append(rel)
    return sorted(set(copies))


# ---------------------------------------------------------------------------
# --refresh-block
# ---------------------------------------------------------------------------

def refresh_block(root: Path, templates: Path, skill_version: str, confirm: bool) -> int:
    agents_path = root / "AGENTS.md"
    if not agents_path.is_file():
        print("refresh-block: AGENTS.md 不存在；先跑 init", file=sys.stderr)
        return 2
    current = agents_path.read_text(encoding="utf-8")
    scan = marker_scan(current)
    if scan["state"] == "invalid":
        print("refresh-block: 标记非法，拒绝写入：" + "；".join(scan["errors"]), file=sys.stderr)
        return 1
    if scan["state"] == "pending":
        print("refresh-block: AGENTS.md 无受管标记；先按 pending 指引插入", file=sys.stderr)
        return 2
    manifest = read_manifest(root)
    if manifest is None:
        print("refresh-block: 缺少 manifest；先跑 init", file=sys.stderr)
        return 1
    try:
        cfg, _ = load_config(root, templates)
        cfg = prepare_config(root, cfg, None, skill_version,
                             source_dir=resolve_source(root))
    except InitError as exc:
        print(f"refresh-block: {exc}", file=sys.stderr)
        return 1
    if not cfg.get("skillRoot"):
        print("refresh-block: 配置缺 skillRoot", file=sys.stderr)
        return 1
    ctx = render_context(cfg, root)
    try:
        section = render_agents_section(templates, ctx)
    except InitError as exc:
        print(f"refresh-block: {exc}", file=sys.stderr)
        return 1
    new_body = marker_scan(section)["body"]
    if new_body == scan["body"]:
        print("refresh-block: 块内容已是最新，无需重渲染")
        return 0
    if not confirm:
        print("refresh-block: 检测到块内容变化（未写入）。确认后重跑 --refresh-block --confirm。")
        import difflib
        diff = difflib.unified_diff(
            scan["body"].splitlines(), new_body.splitlines(),
            fromfile="AGENTS.md#block(current)", tofile="AGENTS.md#block(rendered)", lineterm="",
        )
        for line in list(diff)[:80]:
            print(line)
        return 2
    try:
        with init_lock(root):
            current = agents_path.read_text(encoding="utf-8")
            scan = marker_scan(current)
            if scan["state"] != "ok":
                print("refresh-block: 锁内复检标记失败", file=sys.stderr)
                return 1
            pre = current.split(MARK_START, 1)[0]
            post = current.split(MARK_END, 1)[1]
            atomic_write_text(agents_path, pre + section + post)
            updated = agents_path.read_text(encoding="utf-8")
            body = marker_scan(updated)["body"]
            config_on_disk = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
            entries = [entry for entry in manifest.get("entries", [])
                       if isinstance(entry, dict)]
            block_entries = [entry for entry in entries if entry.get("kind") == "block"]
            if block_entries:
                block_entries[0].update({
                    "path": "AGENTS.md",
                    "marker": "agent-crew:orchestration",
                    "blockSha256": sha256_text(body),
                    "renderVersion": skill_version,
                })
            else:
                entries.append({
                    "kind": "block",
                    "path": "AGENTS.md",
                    "marker": "agent-crew:orchestration",
                    "blockSha256": sha256_text(body),
                    "renderVersion": skill_version,
                })
            manifest["entries"] = entries
            manifest["configHash"] = config_hash(config_on_disk)
            manifest["skillVersion"] = skill_version
            atomic_write_text(root / MANIFEST_REL,
                              json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            append_install_log(root, {
                "ts": utc_now(),
                "action": "refresh-block",
                "operator": os.environ.get("USER") or "unknown",
                "projectId": manifest.get("projectId"),
                "skillVersion": skill_version,
                "configHash": manifest["configHash"],
                "root": str(root),
                "result": "ok",
                "files": [{"path": "AGENTS.md", "kind": "block", "status": "refreshed",
                           "beforeSha": sha256_text(scan["body"]),
                           "afterSha": sha256_bytes(body.encode("utf-8"))}],
                "conflicts": [],
                "next": "",
            })
    except InitLocked:
        print("refresh-block: 另一个 init/upgrade 正在运行（locked）", file=sys.stderr)
        return 2
    print("refresh-block: 已重渲染 AGENTS 受管块（块外未动）")
    return 0


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------

def run_check(root: Path, templates: Path, skill_version: str) -> int:
    config_path = root / CONFIG_REL
    agents_path = root / "AGENTS.md"
    if not config_path.is_file():
        state = "pending" if agents_path.is_file() else "missing"
        print(f"未安装（{state}）：缺少 {CONFIG_REL.as_posix()}")
        return 2
    manifest = read_manifest(root)
    if manifest is None:
        print(f"未安装：缺少 {MANIFEST_REL.as_posix()}")
        return 2
    problems: list[str] = []
    stale: list[str] = []
    modified: list[str] = []
    pending_state = False
    ok = 0

    def check_file_entry(entry: dict) -> None:
        nonlocal ok
        rel = str(entry.get("path") or "")
        path = root / rel
        if not path.is_file():
            problems.append(f"缺失 {rel}")
            return
        if sha256_text_file_matches(path, entry.get("sha256")):
            ok += 1
        else:
            stale.append(rel)

    entries = manifest.get("entries")
    if not isinstance(entries, list):
        problems.append("manifest entries 非法")
        entries = []
    for entry in entries:
        if not isinstance(entry, dict):
            problems.append("manifest entry 非对象")
            continue
        if entry.get("kind") == "file":
            check_file_entry(entry)
        elif entry.get("kind") == "block":
            rel = str(entry.get("path") or "AGENTS.md")
            text = (root / rel).read_text(encoding="utf-8") if (root / rel).is_file() else ""
            scan = marker_scan(text) if text else {"state": "pending", "body": ""}
            if scan["state"] == "pending":
                pending_state = True
            elif scan["state"] == "invalid":
                problems.append("AGENTS.md 标记非法：" + "；".join(scan["errors"]))
            elif sha256_text(scan["body"]) == str(entry.get("blockSha256") or "").removeprefix("sha256:"):
                ok += 1
            else:
                stale.append(f"{rel}#block")
    if not agents_path.is_file():
        pending_state = True
    else:
        scan_agents = marker_scan(agents_path.read_text(encoding="utf-8"))
        if scan_agents["state"] == "pending":
            pending_state = True
        elif scan_agents["state"] == "invalid":
            problems.append("AGENTS.md 标记非法：" + "；".join(scan_agents["errors"]))
    config_hash_stored = manifest.get("configHash")
    try:
        config_on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        problems.append(f"配置损坏：{exc}")
        config_on_disk = {}
    if config_hash_stored and config_hash(config_on_disk) != str(config_hash_stored).removeprefix("sha256:"):
        stale.append(f"{CONFIG_REL.as_posix()}#configHash")
    config_version = config_on_disk.get("skillVersion")
    if config_version != skill_version:
        stale.append(f"{CONFIG_REL.as_posix()}#skillVersion({config_version}→{skill_version})")
    hints: list[str] = []
    skill_root_raw = config_on_disk.get("skillRoot")
    if isinstance(skill_root_raw, str) and skill_root_raw.strip():
        try:
            skill_root_path = Path(skill_root_raw).expanduser()
        except RuntimeError:
            skill_root_path = None  # 无法展开 ~user：跳过该提示，其余检查照常
        if skill_root_path is not None:
            if not skill_root_path.is_absolute():
                skill_root_path = root / skill_root_path
            worktree = linked_worktree_root(skill_root_path)
            if worktree is not None and not is_inside(skill_root_path, root):
                hints.append(
                    f"skillRoot 指向开发 worktree：{worktree}"
                    "（源随开发分支漂移，安装版本易失配）；"
                    "下一步：将 skillRoot 改指稳定分发副本（如 skills/engineering/agent-crew "
                    "指向 repos/local 实体）")
    for stub in manifest.get("roleStubs", []) if isinstance(manifest.get("roleStubs"), list) else []:
        if not isinstance(stub, dict) or not stub.get("path"):
            continue
        path = root / str(stub["path"])
        if not path.is_file():
            problems.append(f"缺失 {stub['path']}")
        elif stub.get("sha256") and sha256_file_raw(path) != str(stub["sha256"]).removeprefix("sha256:"):
            modified.append(str(stub["path"]))
        else:
            ok += 1
    for item in manifest.get("composedAgents", []) if isinstance(manifest.get("composedAgents"), list) else []:
        if not isinstance(item, dict) or not item.get("path"):
            continue
        rel = str(item["path"])
        path = root / rel
        if not path.is_file():
            problems.append(f"缺失组合基础代理 {rel}")
            continue
        if item.get("sha256") and sha256_text(path.read_text(encoding="utf-8")) != str(item["sha256"]).removeprefix("sha256:"):
            stale.append(rel)
            continue
        try:
            expected = composed_agent_text(root, str(item.get("runtime") or ""), str(item.get("role") or ""))
        except InitError as exc:
            problems.append(str(exc))
            continue
        if sha256_text(expected) != sha256_text(path.read_text(encoding="utf-8")):
            stale.append(f"{rel}#composedStale")
        else:
            ok += 1
    try:
        cfg, _ = load_config(root, templates)
        cfg = prepare_config(root, cfg, None, skill_version,
                             source_dir=resolve_source(root))
        copies = forbidden_copies(root, str(cfg.get("skillRoot") or ""))
        if copies:
            problems.append("项目内出现规则/技能源副本：" + ", ".join(copies))
    except InitError as exc:
        problems.append(str(exc))
    for rel in sorted(set(problems)):
        print(f"[缺失/失败] {rel}")
    for rel in sorted(set(stale)):
        print(f"[过期] {rel}")
    for rel in sorted(set(modified)):
        print(f"[项目修改] {rel}（不判漂移）")
    for hint in hints:
        print(f"[风险提示] {hint}")
    print(f"检查结果：{ok} 项一致，{len(set(problems))} 项失败，"
          f"{len(set(stale))} 项过期，{len(set(modified))} 项项目修改。")
    # 优先级：真实校验问题 > AGENTS pending > 仅漂移 > 一致。
    if problems:
        return 1
    if pending_state:
        print("AGENTS.md 无受管标记（pending）：按 docs/crew/AGENTS-block.pending.md 插入后重跑")
        return 2
    if stale:
        return 1
    return 0


def sha256_text_file_matches(path: Path, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return False
    return sha256_text(text) == str(stored).removeprefix("sha256:")


# ---------------------------------------------------------------------------
# --only-failed 记录
# ---------------------------------------------------------------------------

def init_report_path(root: Path) -> Path:
    try:
        return _crew_dir(root) / "init-report.json"
    except InitError:
        return root / ".planning" / ".agent-crew-init.json"


def read_init_report(root: Path) -> list[str] | None:
    path = init_report_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise InitError(f"上次 init 记录损坏：{exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("failed"), list):
        raise InitError(f"上次 init 记录损坏：{path}")
    return [entry if isinstance(entry, str) else entry.get("path")
            for entry in data["failed"]
            if isinstance(entry, str) or isinstance(entry, dict)]


def write_init_report(root: Path, failed: list[Item]) -> None:
    path = init_report_path(root)
    payload = [{"path": item.target.as_posix(), "status": item.status, "note": item.note}
               for item in failed]
    if not payload:
        if path.exists():
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps({"version": 1, "updatedAt": utc_now(),
                                        "failed": payload}, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# init 主流程
# ---------------------------------------------------------------------------

def _portfolio_rel(root: Path, cfg: dict) -> Path:
    configured = Path(str(cfg.get("portfolioDir") or ".planning/portfolio"))
    pattern = str(cfg.get("portfolioMatch") or "*portfolio*")
    planning = root / ".planning"
    if planning.is_dir():
        matches = sorted(p for p in planning.iterdir()
                         if p.is_dir() and fnmatch.fnmatch(p.name, pattern))
        if matches and configured not in [p.relative_to(root) for p in matches]:
            return matches[0].relative_to(root)
    return configured


def _generate_skeletons(root: Path, templates: Path, cfg: dict, ctx: dict) -> dict[Path, str]:
    products: dict[Path, str] = {}
    portfolio = _portfolio_rel(root, cfg)
    plan_tpl = templates / PORTFOLIO_PLAN_TEMPLATE
    if plan_tpl.exists():
        products[portfolio / "task_plan.md"] = apply_template(
            plan_tpl.read_text(encoding="utf-8"), ctx)
    products[portfolio / "findings.md"] = FINDINGS_SKELETON
    products[portfolio / "progress.md"] = PROGRESS_SKELETON
    context_tpl = templates / "CONTEXT-骨架.md"
    if context_tpl.exists():
        products[Path("CONTEXT.md")] = apply_template(
            context_tpl.read_text(encoding="utf-8"), ctx)
    topology_rel = Path(str((cfg.get("hooks") or {}).get("paths", {}).get("topology")
                            or DEFAULT_TOPOLOGY_REL))
    topology_tpl = templates / "project" / "topology.md"
    if topology_tpl.exists():
        # 红线/人工门唯一落点是 AGENTS 受管块；topology 只留指针，避免跨文档重复句
        topology_ctx = dict(ctx)
        topology_ctx["red_lines"] = "项目红线与人工门以 `AGENTS.md` 受管块为准（唯一落点）。"
        products[topology_rel] = apply_template(
            topology_tpl.read_text(encoding="utf-8"), topology_ctx)
    playbooks_readme = templates / "project" / "playbooks-README.md"
    if playbooks_readme.exists():
        products[Path(".planning/playbooks/README.md")] = apply_template(
            playbooks_readme.read_text(encoding="utf-8"), ctx)
    return products


def run_apply(root: Path, source_dir: Path, templates: Path, skill_version: str,
              force: bool, only: set[Path] | None, dry_run: bool) -> int:
    owner = None if dry_run else acquire_owner(root)
    if not dry_run and owner is None:
        try:
            append_install_log(root, {
                "ts": utc_now(), "action": "init", "result": "locked",
                "operator": os.environ.get("USER") or "unknown",
                "skillVersion": skill_version, "root": str(root),
            })
        except OSError:
            pass
        print("错误：另一个 init/upgrade 正在运行（locked）", file=sys.stderr)
        return 2
    try:
        return _run_apply_locked(root, source_dir, templates, skill_version, force,
                                 only, dry_run)
    finally:
        release_owner(owner)


def _run_apply_locked(root: Path, source_dir: Path, templates: Path, skill_version: str,
                      force: bool, only: set[Path] | None, dry_run: bool) -> int:
    report = Report()
    detect(root, report)
    try:
        cfg, existing = load_config(root, templates)
        cfg = prepare_config(root, cfg, existing, skill_version, source_dir=source_dir)
    except InitError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    ctx = render_context(cfg, root)

    # 预检 AGENTS 标记
    agents_path = root / "AGENTS.md"
    current_agents = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else None
    scan = marker_scan(current_agents) if current_agents is not None else {
        "state": "missing", "body": "", "errors": []
    }
    if scan["state"] == "invalid":
        print("错误：AGENTS.md 标记非法，fail closed（零写入）：" + "；".join(scan["errors"]),
              file=sys.stderr)
        return 1
    try:
        section = render_agents_section(templates, ctx)
    except InitError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    rendered_body = marker_scan(section)["body"]

    # --force 拒绝项目自有漂移（AGENTS 块 / 项目角色）
    manifest_before = read_manifest(root)
    known_stubs = {
        str(stub.get("path")): str(stub.get("sha256") or "").removeprefix("sha256:")
        for stub in (manifest_before or {}).get("roleStubs", [])
        if isinstance(stub, dict)
    }
    if force:
        if scan["state"] == "ok" and scan["body"] != rendered_body:
            print("错误：--force 不覆盖 AGENTS 受管块（块漂移需人工确认或 --refresh-block --confirm）",
                  file=sys.stderr)
            return 1
        for runtime in ("qoder", "opencode"):
            for role in ROLE_ORDER:
                rel = role_stub_path(runtime, role)
                path = root / rel
                if path.is_file() and known_stubs.get(rel.as_posix()) \
                        and sha256_file_raw(path) != known_stubs[rel.as_posix()]:
                    print(f"错误：--force 不覆盖项目角色文件：{rel.as_posix()}（项目自有）",
                          file=sys.stderr)
                    return 1

    # 计划（dry-run 与 apply 共用）
    actions: list[tuple[Path, str, str, str | None]] = []  # rel, status, note, content

    def plan_write(rel: Path, content: str, note: str) -> None:
        if only is not None and rel not in only:
            return
        target = root / rel
        if target.exists():
            try:
                same = target.read_text(encoding="utf-8") == content
            except OSError:
                same = False
            if same:
                actions.append((rel, "跳过", note, None))
            elif rel.suffix in {".py", ".sh"}:
                actions.append((rel, "覆盖" if force else "冲突", note, content))
            else:
                actions.append((rel, "跳过", note + "（已存在，不重建）", None))
        else:
            actions.append((rel, "新建", note, content))

    # 脚本
    for name in SCRIPT_MANIFEST:
        src = script_source(name, source_dir)
        if not src.exists():
            report.warnings.append(f"技能脚本缺失：scripts/{name}")
            continue
        plan_write(Path("scripts") / name, src.read_text(encoding="utf-8"), f"脚本 {name}")

    # 项目角色 stub（仅缺失时）
    for runtime in ("qoder", "opencode"):
        for role in ROLE_ORDER:
            rel = role_stub_path(runtime, role)
            if only is not None and rel not in only:
                continue
            target = root / rel
            if target.exists():
                actions.append((rel, "跳过", "项目角色（已存在，永不覆盖）", None))
            else:
                actions.append((rel, "新建", f"项目角色 stub（{runtime}/{role}）",
                                role_stub_content(runtime, role)))

    # 配置 / 骨架
    config_text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    config_path = root / CONFIG_REL
    if config_path.exists():
        try:
            same = config_path.read_text(encoding="utf-8") == config_text
        except OSError:
            same = False
        actions.append((CONFIG_REL, "跳过" if same else "新建",
                        "配置（保留用户值；更新 skillVersion/补齐新键）" if not same else "配置",
                        None if same else config_text))
    else:
        actions.append((CONFIG_REL, "新建", "配置", config_text))
    for rel, content in _generate_skeletons(root, templates, cfg, ctx).items():
        if only is not None and rel not in only:
            continue
        target = root / rel
        actions.append((rel, "跳过" if target.exists() else "新建",
                        "骨架（已存在不重建）" if target.exists() else "骨架", None if target.exists() else content))

    # AGENTS
    if only is None or Path("AGENTS.md") in only:
        if scan["state"] == "missing":
            actions.append((Path("AGENTS.md"), "新建", "AGENTS.md（骨架+受管块）",
                            rendered_agents_text(None, section)))
        elif scan["state"] == "pending":
            actions.append((PENDING_REL, "新建", "AGENTS 无标记：生成 pending（AGENTS 零写入）",
                            pending_text(section)))
        elif scan["body"] == rendered_body:
            actions.append((Path("AGENTS.md"), "跳过", "AGENTS 受管块一致", None))
        else:
            actions.append((Path("AGENTS.md"), "冲突",
                            "AGENTS 受管块与渲染不一致（人工确认后 --refresh-block --confirm）", None))

    pending = scan["state"] == "pending"
    conflicts = [action for action in actions if action[1] == "冲突"]

    if dry_run:
        print(f"=== agent-crew init --dry-run（{root}；技能版本 {skill_version}） ===")
        for rel, status, note, _content in actions:
            print(f"[{status}] {rel.as_posix()}" + (f"（{note}）" if note else ""))
        for warning in report.warnings:
            print(f"[警告] {warning}")
        print("dry-run：零写入。")
        return 0

    # 锁 + 备份 + 写入
    try:
        with init_lock(root):
            files = [root / rel for rel, status, _note, _content in actions
                     if status in {"新建", "覆盖"} and _content is not None
                     and (root / rel).is_file()]
            backup_id = None
            if force and any(action[1] == "覆盖" for action in actions):
                backup_id = create_backup(root, "init-force", files)
            for rel, status, note, content in actions:
                target = root / rel
                if status == "新建" or status == "覆盖":
                    if content is None:
                        continue
                    mode = 0o755 if rel.suffix == ".sh" else None
                    atomic_write_text(target, content, mode=mode)
                    report.add(Item(rel, status, note))
                elif status == "跳过":
                    report.add(Item(rel, "跳过", note))
                else:
                    report.add(Item(rel, "冲突", note))

            # 更新 AGENTS 扫描结果（新建后）
            current_agents = agents_path.read_text(encoding="utf-8") if agents_path.is_file() else None
            final_scan = marker_scan(current_agents) if current_agents is not None else {
                "state": "missing", "body": "", "errors": []
            }

            # manifest
            cfg_on_disk = json.loads((root / CONFIG_REL).read_text(encoding="utf-8"))
            entries: list[dict] = []
            for name in SCRIPT_MANIFEST:
                rel = Path("scripts") / name
                target = root / rel
                if target.is_file():
                    entries.append({"kind": "file", "path": rel.as_posix(),
                                    "sha256": sha256_text(target.read_text(encoding="utf-8"))})
            if final_scan["state"] == "ok":
                entries.append({
                    "kind": "block", "path": "AGENTS.md",
                    "marker": "agent-crew:orchestration",
                    "blockSha256": sha256_text(final_scan["body"]),
                    "renderVersion": skill_version,
                })
            role_stubs: list[dict] = []
            for runtime in ("qoder", "opencode"):
                for role in ROLE_ORDER:
                    rel = role_stub_path(runtime, role)
                    target = root / rel
                    if target.is_file():
                        role_stubs.append({"path": rel.as_posix(), "managed": False,
                                           "sha256": sha256_file_raw(target)})
            composed_agents = regenerate_composed_agents(root, skill_root=source_dir)
            manifest = {
                "manifestVersion": 1,
                "skillVersion": skill_version,
                "projectId": cfg_on_disk.get("projectId"),
                "configHash": config_hash(cfg_on_disk),
                "entries": entries,
                "roleStubs": role_stubs,
                "composedAgents": composed_agents,
            }
            atomic_write_text(root / MANIFEST_REL,
                              json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            result = "pending" if pending else ("conflict" if report.conflicts else "ok")
            append_install_log(root, {
                "ts": utc_now(),
                "action": "init",
                "operator": os.environ.get("USER") or "unknown",
                "projectId": cfg_on_disk.get("projectId"),
                "skillVersion": skill_version,
                "configHash": manifest["configHash"],
                "root": str(root),
                "backupId": backup_id,
                "files": [{"path": item.target.as_posix(), "status": item.status}
                          for item in report.items],
                "conflicts": [item.target.as_posix() for item in report.conflicts],
                "result": result,
                "next": "按 pending 指引插入 AGENTS 块后重跑" if pending else "",
            })
            write_init_report(root, report.conflicts)
    except InitLocked:
        try:
            append_install_log(root, {
                "ts": utc_now(), "action": "init", "result": "locked",
                "operator": os.environ.get("USER") or "unknown",
                "skillVersion": skill_version, "root": str(root),
            })
        except OSError:
            pass
        print("错误：另一个 init/upgrade 正在运行（locked）", file=sys.stderr)
        return 2
    except InitError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1

    print(f"\n=== agent-crew init 核对清单（{root}；技能版本 {skill_version}） ===")
    for item in report.items:
        print(f"[{item.status}] {item.target.as_posix()}" + (f"（{item.note}）" if item.note else ""))
    for warning in report.warnings:
        print(f"[警告] {warning}")
    if report.conflicts:
        print("\n冲突清单（默认不覆盖）：")
        for item in report.conflicts:
            print(f"  - {item.target.as_posix()}（{item.note}）")
    if pending:
        print(f"\nAGENTS.md 无标记：已生成 {PENDING_REL.as_posix()}，AGENTS.md 未写入；"
              "插入块后重跑 init。")
        return 2
    return 2 if report.conflicts else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="agent-crew 编排体系初始化（v2.2 幂等）")
    parser.add_argument("--root", help="目标项目仓根（默认 cwd）")
    parser.add_argument("--templates", help=f"模板目录覆盖（默认 {TEMPLATES}）")
    parser.add_argument("--yes", action="store_true", help="零交互（兼容参数）")
    parser.add_argument("--force", action="store_true",
                        help="覆盖 kind:file 受管脚本（AGENTS/项目角色永不覆盖）")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，零写入")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="只读校验安装状态与漂移")
    mode.add_argument("--only-failed", action="store_true", help="只重跑上次冲突/失败项")
    mode.add_argument("--refresh-block", action="store_true",
                      help="只重渲染 AGENTS 受管块（需 --confirm 才写）")
    parser.add_argument("--confirm", action="store_true", help="配合 --refresh-block 确认写入")
    return parser


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    source_dir = resolve_source(root) if root.is_dir() else SKILL_DIR
    templates = Path(args.templates).resolve() if args.templates else source_dir / "templates"
    skill_version = read_skill_version(source_dir)
    if not root.is_dir():
        print(f"错误：目标目录不存在：{root}", file=sys.stderr)
        return 1
    if args.check:
        return run_check(root, templates, skill_version)
    if args.refresh_block:
        return refresh_block(root, templates, skill_version, args.confirm)
    only: set[Path] | None = None
    if args.only_failed:
        try:
            failed = read_init_report(root)
        except InitError as exc:
            print(f"错误：{exc}", file=sys.stderr)
            return 1
        if failed is None:
            print("未找到上次 init 冲突记录；先正常运行 init。")
            return 0
        if not failed:
            print("上次 init 无冲突/失败项。")
            return 0
        only = {Path(path) for path in failed if path}
    return run_apply(root, source_dir, templates, skill_version,
                     args.force, only, args.dry_run)


def main(argv: list[str] | None = None) -> int:
    try:
        return run(argv)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
