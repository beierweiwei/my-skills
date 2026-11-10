#!/usr/bin/env python3
"""doctor.py — agent-crew 只读体检（hash + 语义），实现 doctor-checklist A–H。

用法：
  python3 scripts/doctor.py --root . --profile release --strict --json --out <path>
  python3 scripts/doctor.py --root . --only role.frontmatter --json
  python3 scripts/doctor.py --root . --only install.version --check-source <skill>

退出码：0=PASS（warn 允许；--strict 时 warn 失败）；1=FAIL；2=PENDING；3=ENV。
只读：不自动修复、不杀 agent、不覆盖 AGENTS/角色，不写项目文件（--out 为用户显式输出）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402
import dispatch as dispatch_lib  # noqa: E402

MARK_START = "<!-- agent-crew:orchestration:start -->"
MARK_END = "<!-- agent-crew:orchestration:end -->"
BLOCK_LINE_LIMIT = 150
RESIDUAL_TOKEN_RE = re.compile(r"\{\{[^{}\n]+\}\}")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
COPY_BASENAMES = ("crew-rules.md", "crew-acceptance.md",
                  "orchestration-guide.md", "plan-discipline.md")
PROJECT_SKILLS_PREFIXES = (".qoder/skills/", ".opencode/skills/")
QODER_AGENT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
CORE_IDS = set(dispatch_lib.CORE_ROLES)
CORE_TOP_KEYS = {
    "projectId", "projectName", "skillRoot", "skillVersion", "portfolioDir",
    "portfolioMatch", "board", "backlog", "docNames", "captainDecisions",
    "redLines", "dataDir", "aopReference", "helm", "roles", "hooks",
    "schemaVersion",
}
RUNTIME_DIRECT_PATTERNS = (
    re.compile(r"herdr\s+agent\s+(?:start|prompt|close)\b"),
    re.compile(r"herdr\s+tab\s+(?:create|close)\b"),
    re.compile(r"herdr\s+pane\s+(?:split|close)\b"),
    re.compile(r"qodercli\b[^\n]*\s--agent\b"),
    re.compile(r"opencode\s+run\b[^\n]*\s--agent\b"),
    re.compile(r"opencode\s+--agent\b"),
)
THIRD_PARTY_DIRS = frozenset({".venv", "site-packages", "node_modules"})
ARCHIVED_DOCS_PREFIX = "docs/历史/"
PLAYBOOK_SEEDS = ("autonomous-run.md", "babysit.md", "session-pickup.md", "investigation.md")


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def parse_qoder_agent_list(stdout: str) -> set[str]:
    """解析 `qodercli agent list`：兼容 `name · Inherit` 与历史 `name (Project)` 格式。

    跳过空行、`N active agents` 与 `Project:`/`Built-in:`/`Custom:` 等分节行；
    只取第一个分隔符（`·`/`(`）前的角色名，非角色名行忽略。
    """
    names: set[str] = set()
    for line in stdout.splitlines():
        stripped = re.sub(r"^[-*+]\s+", "", line.strip())
        if not stripped or stripped.endswith(":"):
            continue
        if re.fullmatch(r"\d+\s+active agents?", stripped, re.IGNORECASE):
            continue
        name = re.split(r"\s+·\s+|\s*\(", stripped, maxsplit=1)[0].strip()
        if QODER_AGENT_NAME_RE.fullmatch(name):
            names.add(name)
    return names


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_text_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    if not text.endswith("\n"):
        text += "\n"
    return sha256_bytes(text.encode("utf-8"))


def same_hash(left: str | None, right: str | None) -> bool:
    def clean(value: str | None) -> str:
        return (value or "").strip().removeprefix("sha256:")

    return bool(clean(left)) and clean(left) == clean(right)


def config_hash(config: dict) -> str:
    data = {key: value for key, value in config.items()
            if not key.startswith("_") and key != "skillVersion"}
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_bytes(payload.encode("utf-8"))


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def resolve_under(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path)


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


def normalize_line(line: str) -> str:
    text = line.strip()
    text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s+)+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def headings(text: str) -> list[tuple[int, int, str]]:
    result: list[tuple[int, int, str]] = []
    in_fence = False
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = HEADING_RE.match(line)
        if match:
            result.append((index, len(match.group(1)), match.group(2).strip()))
    return result


def marker_info(text: str) -> dict:
    """标记位置：start/end 行号、是否行首、是否围栏内、其他标记行。"""
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
        if not stripped.startswith("<!-- agent-crew:orchestration:"):
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
    return {
        "lines": lines, "starts": starts, "ends": ends,
        "others": others, "fenced": fenced, "indented": indented,
    }


def block_body(text: str) -> tuple[str | None, list[str]]:
    info = marker_info(text)
    errors: list[str] = []
    if len(info["starts"]) != 1 or len(info["ends"]) != 1:
        errors.append(f"标记必须恰好一对（start={len(info['starts'])}, end={len(info['ends'])}）")
    elif info["starts"][0] >= info["ends"][0]:
        errors.append("start 必须在 end 之前")
    if info["others"]:
        errors.append(f"出现其他 agent-crew:orchestration 标记行：{info['others']}")
    if info["fenced"]:
        errors.append(f"标记位于代码围栏内：{info['fenced']}")
    if info["indented"]:
        errors.append(f"标记前有缩进：{info['indented']}")
    if errors:
        return None, errors
    start, end = info["starts"][0], info["ends"][0]
    lines = info["lines"][start + 1:end]
    normalized = [line.rstrip() for line in lines]
    while normalized and not normalized[0]:
        normalized.pop(0)
    while normalized and not normalized[-1]:
        normalized.pop()
    return "\n".join(normalized) + "\n", []


@dataclass
class Result:
    id: str
    status: str = "pass"  # pass/warn/fail/skip/pending/env
    detail: str = ""
    evidence: dict = field(default_factory=dict)
    next: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "detail": self.detail,
            "evidence": self.evidence,
            "next": self.next,
        }


class Doctor:
    def __init__(self, root: Path, profile: str, strict: bool,
                 check_source: str | None = None):
        self.root = root
        self.profile = profile
        self.strict = strict
        self.check_source = check_source
        self._config: dict | None = None
        self._config_error = ""
        self._config_loaded = False
        self._manifest: dict | None = None
        self._manifest_error = ""
        self._manifest_loaded = False
        self._crew: Path | None = None
        self._crew_error = ""

    # ---------- 缓存加载 ----------

    def config(self) -> dict | None:
        if not self._config_loaded:
            self._config_loaded = True
            path = self.root / ".planning/orchestration.json"
            text = read_text(path)
            if text is None:
                self._config_error = f"缺少配置：{path}"
            else:
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    self._config_error = f"配置 JSON 非法：{exc}"
                else:
                    if isinstance(data, dict):
                        self._config = data
                    else:
                        self._config_error = "配置必须是 JSON 对象"
        return self._config

    def manifest(self) -> dict | None:
        if not self._manifest_loaded:
            self._manifest_loaded = True
            self._manifest_error = ""
            path = self.root / "docs/crew/manifest.json"
            text = read_text(path)
            if text is None:
                self._manifest_error = f"缺少 manifest：{path}"
            else:
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    self._manifest_error = f"manifest JSON 非法：{exc}"
                else:
                    if isinstance(data, dict):
                        self._manifest = data
                    else:
                        self._manifest_error = "manifest 必须是 JSON 对象"
        return self._manifest

    def crew(self) -> Path | None:
        if self._crew is None and not self._crew_error:
            try:
                self._crew = crew_dir(self.root)
            except CrewPathsError as exc:
                self._crew_error = str(exc)
        return self._crew

    def skill_root(self) -> Path | None:
        config = self.config()
        if not config:
            return None
        raw = config.get("skillRoot")
        if not isinstance(raw, str) or not raw.strip():
            return None
        return resolve_under(self.root, raw)

    def source_root(self) -> Path | None:
        if self.check_source:
            return Path(self.check_source).expanduser().resolve()
        return self.skill_root()

    def manifest_entries(self, kind: str) -> list[dict]:
        manifest = self.manifest() or {}
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries
                if isinstance(entry, dict) and entry.get("kind") == kind]

    def role_entries(self) -> list[dict]:
        entries: list[dict] = []
        qoder_dir = self.root / ".qoder/agents"
        for path in sorted(qoder_dir.glob("*.md")):
            entries.append({"path": path, "runtime": "qoder", "custom": False})
        custom_dir = qoder_dir / "custom"
        for path in sorted(custom_dir.glob("*.md")):
            entries.append({"path": path, "runtime": "qoder", "custom": True})
        oc_dir = self.root / ".opencode/agent"
        for path in sorted(oc_dir.glob("*.md")):
            if path.name.startswith("crew-"):
                continue  # managed 组合基础代理，不是项目角色
            custom = path.name.startswith("custom-")
            entries.append({"path": path, "runtime": "opencode", "custom": custom})
        for entry in entries:
            stem = entry["path"].stem
            if entry["runtime"] == "opencode" and entry["custom"] and stem.startswith("custom-"):
                stem = stem[len("custom-"):]
            entry["id"] = stem
        return entries

    def nested_role_files(self) -> list[Path]:
        nested: list[Path] = []
        qoder_custom = self.root / ".qoder/agents/custom"
        if qoder_custom.is_dir():
            nested.extend(path for path in qoder_custom.rglob("*.md")
                          if path.parent != qoder_custom)
        oc_dir = self.root / ".opencode/agent"
        if oc_dir.is_dir():
            nested.extend(path for path in oc_dir.rglob("*.md")
                          if path.parent != oc_dir)
        return sorted(nested)

    # ---------- 通用 ----------

    def _parse_role(self, entry: dict) -> tuple[dict | None, str]:
        try:
            parsed = dispatch_lib.parse_project_role(
                self.root, entry["path"], entry["runtime"], entry["custom"]
            )
            return parsed, ""
        except dispatch_lib.DispatchError as exc:
            return None, str(exc)

    def _descriptor_errors(self, parsed: dict) -> list[str]:
        descriptor = parsed.get("descriptor") or {}
        extends = descriptor.get("extends")
        base_role = extends[6:] if isinstance(extends, str) and extends.startswith("skill:") else None
        base_gates = dispatch_lib.CORE_ROLES.get(base_role or "", {}).get("gates", [])
        config = self.config() or {}
        _, errors = dispatch_lib.validate_descriptor(parsed, config, base_gates)
        return errors

    def _manifest_block_hash(self) -> tuple[str | None, str | None]:
        for entry in self.manifest_entries("block"):
            if entry.get("path") == "AGENTS.md":
                return entry.get("blockSha256"), entry.get("renderVersion")
        return None, None

    def _skip_scan_path(self, path: Path, archived: bool = False) -> bool:
        rel = path.relative_to(self.root).as_posix()
        if any(part in THIRD_PARTY_DIRS for part in Path(rel).parts):
            return True
        return archived and rel.startswith(ARCHIVED_DOCS_PREFIX)

    def _is_git_ignored(self, rel: str) -> bool:
        """git 仓内被忽略的路径不算项目内容；非 git/无 git 时按未忽略处理。"""
        rc, _, _ = self._run_cli(
            ["git", "-C", str(self.root), "check-ignore", "-q", "--", rel])
        return rc == 0

    # ================= A. manifest =================

    def check_manifest_schema(self) -> Result:
        manifest = self.manifest()
        if manifest is None:
            return Result("manifest.schema", "fail", self._manifest_error,
                          next="先跑 init/upgrade 生成 docs/crew/manifest.json")
        problems: list[str] = []
        if manifest.get("manifestVersion") != 1:
            problems.append(f"manifestVersion={manifest.get('manifestVersion')!r}（支持 1）")
        for key in ("skillVersion", "projectId"):
            if not isinstance(manifest.get(key), str) or not manifest.get(key):
                problems.append(f"缺 {key}")
        entries = manifest.get("entries")
        if not isinstance(entries, list):
            problems.append("entries 必须是数组")
            entries = []
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict) or entry.get("kind") not in {"file", "block"}:
                problems.append(f"entries[{index}] kind 必须是 file/block")
                continue
            path = str(entry.get("path") or "")
            basename = Path(path).name
            if basename in COPY_BASENAMES:
                problems.append(f"entries[{index}] 登记了规则/验收副本：{path}")
            if entry["kind"] == "file":
                for key in ("path", "sha256"):
                    if not entry.get(key):
                        problems.append(f"entries[{index}] 缺 {key}")
            else:
                for key in ("path", "marker", "blockSha256", "renderVersion"):
                    if not entry.get(key):
                        problems.append(f"entries[{index}] 缺 {key}")
        role_stubs = manifest.get("roleStubs", [])
        if not isinstance(role_stubs, list):
            problems.append("roleStubs 必须是数组")
        if problems:
            return Result("manifest.schema", "fail", "；".join(problems),
                          evidence={"path": "docs/crew/manifest.json"})
        return Result("manifest.schema", "pass",
                      f"manifestVersion=1；entries={len(entries)}")

    def check_manifest_hash_file(self) -> Result:
        if self.manifest() is None:
            return Result("manifest.hash.file", "fail", self._manifest_error)
        entries = self.manifest_entries("file")
        problems: list[str] = []
        for entry in entries:
            rel = str(entry.get("path") or "")
            if not rel.startswith("scripts/") or not rel.endswith((".py", ".sh")):
                problems.append(f"{rel} 不是受管脚本（scripts/*.py|sh）")
                continue
            path = self.root / rel
            if not path.is_file():
                problems.append(f"{rel} 不存在")
                continue
            actual = sha256_text_file(path)
            if not same_hash(entry.get("sha256"), actual):
                problems.append(f"{rel} sha256 不一致（manifest={entry.get('sha256')} actual={actual}）")
        if problems:
            return Result("manifest.hash.file", "fail", "；".join(problems))
        return Result("manifest.hash.file", "pass", f"{len(entries)} 个受管脚本 hash 一致")

    def check_manifest_hash_block(self) -> Result:
        if self.manifest() is None:
            return Result("manifest.hash.block", "fail", self._manifest_error)
        entries = self.manifest_entries("block")
        if not entries:
            return Result("manifest.hash.block", "fail", "manifest 未登记 AGENTS.md block 条目")
        problems: list[str] = []
        for entry in entries:
            rel = str(entry.get("path") or "")
            path = self.root / rel
            text = read_text(path)
            if text is None:
                problems.append(f"{rel} 不存在")
                continue
            body, errors = block_body(text)
            if errors:
                problems.append(f"{rel}: {'；'.join(errors)}")
                continue
            actual = sha256_bytes((body or "").encode("utf-8"))
            if not same_hash(entry.get("blockSha256"), actual):
                problems.append(f"{rel} blockSha256 不一致（manifest={entry.get('blockSha256')} actual={actual}）")
            if entry.get("sha256"):
                problems.append(f"{rel} 不得存整文件 sha256")
        if problems:
            return Result("manifest.hash.block", "fail", "；".join(problems))
        return Result("manifest.hash.block", "pass", f"{len(entries)} 个 block hash 一致")

    def check_manifest_config(self) -> Result:
        if self.manifest() is None:
            return Result("manifest.config", "fail", self._manifest_error)
        config = self.config()
        if config is None:
            return Result("manifest.config", "fail", self._config_error)
        stored = self.manifest().get("configHash")
        actual = config_hash(config)
        if not stored:
            return Result("manifest.config", "warn", "manifest 缺 configHash",
                          next="init --refresh-block --confirm 重算 manifest")
        if not same_hash(stored, actual):
            return Result(
                "manifest.config", "warn",
                f"configHash 不一致（manifest={stored} actual={actual}）",
                next="init --refresh-block --confirm（重算 manifest configHash，不改配置文件）",
            )
        return Result("manifest.config", "pass", "configHash 一致")

    def check_manifest_role_stubs(self) -> Result:
        if self.manifest() is None:
            return Result("manifest.roleStubs", "fail", self._manifest_error)
        stubs = self.manifest().get("roleStubs") or []
        entry_paths = {entry.get("path") for entry in self.manifest_entries("file")}
        problems: list[str] = []
        warns: list[str] = []
        for stub in stubs:
            if not isinstance(stub, dict):
                problems.append("roleStubs 条目必须是对象")
                continue
            rel = stub.get("path")
            if not rel:
                problems.append("roleStubs 条目缺 path")
                continue
            if rel in entry_paths:
                problems.append(f"{rel} 被列入受管 entries")
            path = self.root / rel
            if not path.is_file():
                problems.append(f"{rel} 不存在")
                continue
            if stub.get("managed") is not False:
                problems.append(f"{rel} managed 必须为 false")
            if stub.get("sha256") and not same_hash(stub.get("sha256"), sha256_bytes(path.read_bytes())):
                warns.append(f"{rel} 项目已编辑（project-modified）")
        if problems:
            return Result("manifest.roleStubs", "fail", "；".join(problems))
        if warns:
            return Result("manifest.roleStubs", "warn", "；".join(warns))
        return Result("manifest.roleStubs", "pass", f"{len(stubs)} 条 stub 记录")

    def check_manifest_orphan(self) -> Result:
        if self.manifest() is None:
            return Result("manifest.orphan", "fail", self._manifest_error)
        missing = [str(entry.get("path")) for entry in self.manifest_entries("file")
                   if not (self.root / str(entry.get("path"))).is_file()]
        if missing:
            return Result("manifest.orphan", "fail", f"manifest 指向不存在文件：{missing}")
        registered = {str(entry.get("path")) for entry in self.manifest_entries("file")}
        config = self.config() or {}
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        project_scripts = hooks.get("projectScripts")
        if isinstance(project_scripts, list):
            for entry in project_scripts:
                if isinstance(entry, dict):
                    rel = entry.get("path")
                    if isinstance(rel, str) and rel:
                        registered.add(rel)
        scripts = self.root / "scripts"
        orphans: list[str] = []
        if scripts.is_dir():
            for path in sorted(scripts.rglob("*")):
                rel = path.relative_to(self.root).as_posix()
                if not path.is_file() or path.suffix not in (".py", ".sh"):
                    continue
                if rel.startswith("scripts/custom/") or "tests/" in rel or "__pycache__" in rel:
                    continue
                if rel not in registered:
                    orphans.append(rel)
        if orphans:
            return Result("manifest.orphan", "warn",
                          f"未登记脚本：{orphans}",
                          next="init/upgrade 纳入 manifest，或移到 scripts/custom/")
        return Result("manifest.orphan", "pass", "无孤儿受管脚本")

    # ================= B. AGENTS =================

    def check_agents_markers(self) -> Result:
        path = self.root / "AGENTS.md"
        text = read_text(path)
        if text is None:
            return Result("agents.markers", "fail", "AGENTS.md 不存在")
        info = marker_info(text)
        if not info["starts"] and not info["ends"]:
            pending = self.root / "docs/crew/AGENTS-block.pending.md"
            if pending.is_file():
                return Result("agents.markers", "pending",
                              "AGENTS.md 无标记；pending 块待人工插入",
                              evidence={"pending": pending.relative_to(self.root).as_posix()},
                              next="把 pending 块插入 AGENTS.md 后重跑")
            return Result("agents.markers", "fail", "AGENTS.md 无标记且无 pending 块",
                          next="先跑 init 生成 pending 块")
        _, errors = block_body(text)
        if errors:
            return Result("agents.markers", "fail", "；".join(errors),
                          evidence={"lines": info["starts"] + info["ends"]})
        return Result("agents.markers", "pass",
                      f"恰好一对标记（start={info['starts'][0] + 1}, end={info['ends'][0] + 1}）")

    def check_agents_block(self) -> Result:
        text = read_text(self.root / "AGENTS.md")
        if text is None:
            return Result("agents.block", "fail", "AGENTS.md 不存在")
        body, errors = block_body(text)
        if errors:
            return Result("agents.block", "fail", "；".join(errors))
        line_count = body.count("\n")
        problems: list[str] = []
        if not body.strip():
            problems.append("块体为空")
        if line_count > BLOCK_LINE_LIMIT:
            problems.append(f"块体 {line_count} 行 > {BLOCK_LINE_LIMIT}")
        if MARK_START in body:
            problems.append("块体内嵌套 start 标记")
        tokens = sorted(set(RESIDUAL_TOKEN_RE.findall(body)))
        if tokens:
            problems.append(f"块内未解析 token：{tokens}")
        skill_root = self.skill_root()
        pointer_like = any(name in body for name in (
            "references/crew-rules.md", "references/crew-acceptance.md",
            "references/plan-discipline.md", "SKILL.md",
        ))
        config = self.config() or {}
        raw_root = config.get("skillRoot")
        accepted = {str(skill_root)} if skill_root else set()
        if isinstance(raw_root, str) and raw_root.strip():
            accepted.add(raw_root.strip().rstrip("/"))
        if pointer_like and skill_root and not any(item in body for item in accepted):
            problems.append("块内指针未指向当前 skillRoot（stale）")
        stored, _ = self._manifest_block_hash()
        actual = sha256_bytes(body.encode("utf-8"))
        if stored and not same_hash(stored, actual):
            problems.append(f"blockSha256 不一致（manifest={stored} actual={actual}）")
        if problems:
            next_step = "init --refresh-block --confirm" if any(
                key in "；".join(problems) for key in ("stale", "blockSha256")) else ""
            return Result("agents.block", "fail", "；".join(problems),
                          evidence={"lines": line_count}, next=next_step)
        if not stored:
            return Result("agents.block", "warn", "块体合法，但 manifest 未登记 block hash",
                          next="init --refresh-block --confirm")
        return Result("agents.block", "pass", f"块体 {line_count} 行；blockSha256 一致")

    def check_agents_duplicate(self) -> Result:
        text = read_text(self.root / "AGENTS.md")
        if text is None:
            return Result("agents.duplicate", "fail", "AGENTS.md 不存在")
        seen: dict[str, int] = {}
        duplicates: list[str] = []
        for line_no, _, title in headings(text):
            key = normalize_line(title)
            if key in seen:
                duplicates.append(f"{title}（行 {seen[key] + 1}/{line_no + 1}）")
            else:
                seen[key] = line_no
        if duplicates:
            return Result("agents.duplicate", "fail", "重复标题：" + "；".join(duplicates))
        return Result("agents.duplicate", "pass", "无重复标题")

    def check_agents_pending(self) -> Result:
        text = read_text(self.root / "AGENTS.md")
        if text and marker_info(text)["starts"]:
            return Result("agents.pending", "pass", "AGENTS.md 已有标记块")
        pending = self.root / "docs/crew/AGENTS-block.pending.md"
        if pending.is_file():
            return Result("agents.pending", "pending", "pending 块待插入",
                          next="人工插入 pending 块后重跑")
        return Result("agents.pending", "fail", "无标记块且无 pending 文件",
                      next="跑 init 生成 pending 块")

    # ================= C. docs =================

    def check_docs_pointers(self) -> Result:
        problems: list[str] = []
        files: list[Path] = []
        config = self.config() or {}
        agents = self.root / "AGENTS.md"
        if agents.is_file():
            files.append(agents)
        files.extend(entry["path"] for entry in self.role_entries())
        for path in files:
            text = read_text(path) or ""
            rel = path.relative_to(self.root).as_posix()
            for line_no, line in enumerate(text.splitlines(), 1):
                for token in RESIDUAL_TOKEN_RE.findall(line):
                    problems.append(f"{rel}:{line_no} 残留 {token}")
        agents_text = read_text(agents) if agents.is_file() else None
        if agents_text:
            body, _ = block_body(agents_text)
            skill_root = self.skill_root()
            pointer_like = body and any(name in body for name in (
                "references/crew-rules.md", "references/crew-acceptance.md",
                "references/plan-discipline.md", "SKILL.md",
            ))
            raw_root = config.get("skillRoot")
            accepted = {str(skill_root)} if skill_root else set()
            if isinstance(raw_root, str) and raw_root.strip():
                accepted.add(raw_root.strip().rstrip("/"))
            if pointer_like and skill_root and not any(item in body for item in accepted):
                problems.append("AGENTS.md 块内指针未指向当前 skillRoot（stale）")
        role_ext = ((config.get("hooks") or {}).get("roleExt")
                    if isinstance(config.get("hooks"), dict) else None)
        if isinstance(role_ext, dict):
            known = CORE_IDS | {entry["id"] for entry in self.role_entries()}
            for role_id, rel in role_ext.items():
                if role_id not in known and role_id != "_common":
                    problems.append(f"roleExt 未知角色：{role_id}")
                    continue
                if not (self.root / str(rel)).is_file():
                    problems.append(f"roleExt 文件不存在：{rel}")
        if problems:
            next_step = "init --refresh-block --confirm" if any("stale" in item for item in problems) else ""
            return Result("docs.pointers", "fail", "；".join(problems[:20]),
                          evidence={"count": len(problems)}, next=next_step)
        return Result("docs.pointers", "pass", f"扫描 {len(files)} 个项目文件，无残留 token/断链")

    def check_docs_duplicate(self) -> Result:
        config = self.config() or {}
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        paths = hooks.get("paths") if isinstance(hooks.get("paths"), dict) else {}
        files: list[Path] = []
        if (self.root / "AGENTS.md").is_file():
            files.append(self.root / "AGENTS.md")
        topology = paths.get("topology")
        if topology and (self.root / str(topology)).is_file():
            files.append(self.root / str(topology))
        ext_dir = paths.get("projectExt")
        if ext_dir and (self.root / str(ext_dir)).is_dir():
            files.extend(sorted((self.root / str(ext_dir)).glob("*.md")))
        files.extend(entry["path"] for entry in self.role_entries())
        occurrences: dict[str, list[str]] = {}
        for path in files:
            rel = path.relative_to(self.root).as_posix()
            in_fence = False
            for line_no, line in enumerate((read_text(path) or "").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("```") or stripped.startswith("~~~"):
                    in_fence = not in_fence
                    continue
                if in_fence or not stripped or stripped.startswith("#"):
                    continue
                normalized = normalize_line(line)
                if len(normalized) < 30:
                    continue
                occurrences.setdefault(normalized, []).append(f"{rel}:{line_no}")
        duplicates = [
            f"{locations[0]} 与 {locations[1]} 重复同一规则句"
            for locations in occurrences.values()
            if len({loc.rsplit(":", 1)[0] for loc in locations}) >= 2
        ]
        if duplicates:
            return Result("docs.duplicate", "fail", "；".join(duplicates[:10]),
                          evidence={"count": len(duplicates)})
        return Result("docs.duplicate", "pass", f"扫描 {len(files)} 个项目文件，无跨文档重复规则句")

    def check_skill_source(self) -> Result:
        config = self.config()
        if config is None:
            return Result("skill.source", "fail", self._config_error)
        raw = config.get("skillRoot")
        if not isinstance(raw, str) or not raw.strip():
            return Result("skill.source", "fail", "配置缺 skillRoot")
        skill_root = resolve_under(self.root, raw)
        if not skill_root.is_dir():
            return Result("skill.source", "fail", f"skillRoot 不存在或不可读：{skill_root}")
        problems: list[str] = []
        warns: list[str] = []
        worktree: Path | None = None
        if not is_inside(skill_root, self.root):
            worktree = linked_worktree_root(skill_root)
            if worktree is not None:
                warns.append(f"skillRoot 指向开发 worktree：{worktree}"
                             "（源随开发分支漂移，安装版本易失配）")
        if Path(raw).is_absolute():
            warns.append("skillRoot 是绝对路径（不可移植）")
        elif is_inside(skill_root, self.root):
            rel = skill_root.resolve().relative_to(self.root.resolve()).as_posix()
            if not (rel.startswith(".qoder/skills/") or rel.startswith(".opencode/skills/")):
                problems.append(f"skillRoot 位置非法：{rel}（只允许 .qoder/skills/ 或 .opencode/skills/）")
        copies: list[str] = []
        source_names = {"crew-rules.md", "crew-acceptance.md", "SKILL.md",
                        "orchestration-guide.md", "plan-discipline.md"}
        for path in self.root.rglob("*"):
            rel = path.relative_to(self.root).as_posix()
            if not path.is_file() or rel.startswith(".git/") or rel.startswith(".planning/"):
                continue
            if self._skip_scan_path(path):
                continue
            if rel.startswith(PROJECT_SKILLS_PREFIXES):
                continue  # 项目级 skills 目录由技能安装器管理，不判技能源副本
            if path.name in source_names and not is_inside(path, skill_root):
                if self._is_git_ignored(rel):
                    continue  # 被 .gitignore 忽略的外部技能源（如 skill-src/）不算项目副本
                copies.append(rel)
        if copies:
            problems.append(f"项目内出现技能源/规则副本：{sorted(set(copies))}")
        if problems:
            return Result("skill.source", "fail", "；".join(problems))
        if warns:
            if worktree is not None:
                next_step = ("将 skillRoot 改指稳定分发副本（如 skills/engineering/agent-crew "
                             "指向 repos/local 实体），避免依赖开发 worktree 造成版本漂移")
            else:
                next_step = "可移植时改用项目相对路径 .qoder/skills/agent-crew"
            return Result("skill.source", "warn", "；".join(warns), next=next_step)
        return Result("skill.source", "pass", f"skillRoot 合法：{skill_root}")

    def check_docs_links(self) -> Result:
        files: list[Path] = []
        if (self.root / "AGENTS.md").is_file():
            files.append(self.root / "AGENTS.md")
        docs = self.root / "docs"
        if docs.is_dir():
            files.extend(sorted(path for path in docs.rglob("*.md")
                                if not self._skip_scan_path(path, archived=True)))
        files.extend(entry["path"] for entry in self.role_entries()
                     if not self._skip_scan_path(entry["path"]))
        broken: list[str] = []
        for path in files:
            text = read_text(path) or ""
            rel = path.relative_to(self.root).as_posix()
            for line_no, line in enumerate(text.splitlines(), 1):
                for target in LINK_RE.findall(line):
                    if target.startswith(("http://", "https://", "mailto:", "#")):
                        continue
                    clean = target.split("#", 1)[0]
                    if not clean:
                        continue
                    resolved = Path(clean) if Path(clean).is_absolute() else path.parent / clean
                    if not resolved.exists():
                        broken.append(f"{rel}:{line_no} -> {target}")
        if broken:
            return Result("docs.links", "warn", "断链：" + "；".join(broken[:10]),
                          evidence={"count": len(broken)})
        return Result("docs.links", "pass", f"扫描 {len(files)} 个 Markdown 相对链接")

    # ================= D. config =================

    def check_config_schema(self) -> Result:
        config = self.config()
        if config is None:
            return Result("config.schema", "fail", self._config_error)
        problems: list[str] = []
        unknown = sorted(key for key in config
                         if not key.startswith("_") and key not in CORE_TOP_KEYS)
        if unknown:
            problems.append(f"未知配置键：{unknown}")
        project_id = config.get("projectId")
        if not isinstance(project_id, str) or not dispatch_lib.ROLE_ID_RE.match(project_id):
            problems.append(f"projectId 非法：{project_id!r}")
        if not isinstance(config.get("skillRoot"), str) or not config.get("skillRoot"):
            problems.append("缺 skillRoot")
        if not isinstance(config.get("skillVersion"), str) or not config.get("skillVersion"):
            problems.append("缺 skillVersion")
        roles = config.get("roles")
        if roles is None:
            problems.append("缺 roles.enabled")
        elif not isinstance(roles, dict) or not isinstance(roles.get("enabled"), list) \
                or not roles.get("enabled"):
            problems.append("roles.enabled 必须是非空数组")
        else:
            known = CORE_IDS | {entry["id"] for entry in self.role_entries()}
            unknown_roles = sorted(set(roles["enabled"]) - known)
            if unknown_roles:
                problems.append(f"roles.enabled 含未知角色：{unknown_roles}")
            if not ({"helm", "firstmate"} & set(roles["enabled"])):
                problems.append("roles.enabled 至少保留一个 dispatch 角色（helm/firstmate）")
        if problems:
            return Result("config.schema", "fail", "；".join(problems),
                          evidence={"path": ".planning/orchestration.json"})
        return Result("config.schema", "pass", "配置 schema 合法")

    def check_config_paths(self) -> Result:
        config = self.config()
        if config is None:
            return Result("config.paths", "fail", self._config_error)
        problems: list[str] = []
        warns: list[str] = []
        skill_root = self.skill_root()
        if not skill_root or not skill_root.is_dir():
            problems.append(f"skillRoot 不存在：{skill_root}")
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        paths = hooks.get("paths") if isinstance(hooks.get("paths"), dict) else {}
        topology = paths.get("topology") or "docs/orchestration/topology.md"
        topology_path = self.root / str(topology)
        if not topology_path.is_file():
            if paths.get("topology"):
                problems.append(f"paths.topology 不存在：{topology}")
            else:
                warns.append(f"默认 topology 不存在：{topology}")
        elif not is_inside(topology_path, self.root) or ".git" in topology_path.parts:
            problems.append(f"paths.topology 越界：{topology}")
        ext = paths.get("projectExt")
        if ext:
            ext_path = self.root / str(ext)
            if not ext_path.is_dir():
                problems.append(f"paths.projectExt 不存在：{ext}")
        portfolio = self.root / str(config.get("portfolioDir") or ".planning/portfolio")
        if not portfolio.is_dir():
            problems.append(f"portfolioDir 不存在：{portfolio}")
        board = config.get("board") if isinstance(config.get("board"), dict) else {}
        board_path = board.get("path")
        if board_path:
            target = self.root / str(board_path)
            if not is_inside(target, self.root):
                problems.append(f"board.path 越界：{board_path}")
            elif not target.parent.is_dir():
                problems.append(f"board.path 父目录不存在：{target.parent}")
        data_dir = self.root / str(config.get("dataDir") or "data/raw/")
        if not data_dir.is_dir():
            warns.append(f"dataDir 不存在：{data_dir}")
        if problems:
            return Result("config.paths", "fail", "；".join(problems))
        if warns:
            return Result("config.paths", "warn", "；".join(warns))
        return Result("config.paths", "pass", "配置路径合法")

    def check_config_identity(self) -> Result:
        config = self.config()
        if config is None:
            return Result("config.identity", "fail", self._config_error)
        problems: list[str] = []
        warns: list[str] = []
        project_id = config.get("projectId")
        manifest = self.manifest() or {}
        if manifest.get("projectId") and manifest.get("projectId") != project_id:
            problems.append(f"projectId 与 manifest 不一致：{project_id} vs {manifest.get('projectId')}")
        helm = config.get("helm") if isinstance(config.get("helm"), dict) else {}
        name = helm.get("agentName")
        if name:
            if not dispatch_lib.INSTANCE_NAME_RE.match(str(name)):
                problems.append(f"helm.agentName 非法：{name!r}")
            elif not str(name).startswith(f"{project_id}-"):
                warns.append(f"helm.agentName 未命名空间化：{name}")
        if problems:
            return Result("config.identity", "fail", "；".join(problems))
        if warns:
            return Result("config.identity", "warn", "；".join(warns))
        return Result("config.identity", "pass", f"projectId={project_id}")

    # ================= E. roles =================

    def check_role_frontmatter(self) -> Result:
        problems: list[str] = []
        entries = self.role_entries()
        for entry in entries:
            text = read_text(entry["path"])
            if text is None:
                problems.append(f"{entry['path'].name}: 不可读")
                continue
            frontmatter, _ = dispatch_lib.split_frontmatter(text)
            rel = entry["path"].relative_to(self.root).as_posix()
            if frontmatter is None:
                problems.append(f"{rel}: 缺 frontmatter")
                continue
            parsed = {"frontmatter": frontmatter, "rel_path": rel}
            problems.extend(dispatch_lib.validate_project_frontmatter(
                parsed, entry["id"], entry["runtime"], entry["custom"]))
            if entry["runtime"] == "opencode" and not entry["custom"]:
                parsed_full, error = self._parse_role(entry)
                if not error and self.skill_root():
                    descriptor = parsed_full.get("descriptor") or {}
                    extends = descriptor.get("extends")
                    base_role = extends[6:] if isinstance(extends, str) and extends.startswith("skill:") else None
                    base_path = (self.skill_root() / "templates/agents/opencode"
                                 / f"{base_role}.md")
                    base_text = read_text(base_path) if base_role else None
                    base_fm, _ = dispatch_lib.split_frontmatter(base_text or "")
                    base_mode = dispatch_lib.scalar(
                        (base_fm or {}).get("fields", {}).get("mode", ""))
                    mode = dispatch_lib.scalar(frontmatter["fields"].get("mode", ""))
                    if base_mode and mode and mode != base_mode:
                        problems.append(
                            f"{rel}: mode={mode!r} 必须等于基类 {base_mode!r}")
        if problems:
            return Result("role.frontmatter", "fail", "；".join(problems[:20]),
                          evidence={"roles": len(entries)})
        return Result("role.frontmatter", "pass", f"{len(entries)} 个项目角色 frontmatter 合法")

    def check_role_descriptor(self) -> Result:
        problems: list[str] = []
        entries = self.role_entries()
        for entry in entries:
            parsed, error = self._parse_role(entry)
            if error:
                problems.append(error)
                continue
            problems.extend(self._descriptor_errors(parsed))
        if problems:
            return Result("role.descriptor", "fail", "；".join(problems[:20]))
        return Result("role.descriptor", "pass", f"{len(entries)} 个描述符合法")

    def _filter_errors(self, errors: list[str], keywords: tuple[str, ...]) -> list[str]:
        return [error for error in errors if any(key in error for key in keywords)]

    def check_role_capability(self) -> Result:
        errors: list[str] = []
        for entry in self.role_entries():
            parsed, error = self._parse_role(entry)
            if error:
                continue
            errors.extend(self._filter_errors(self._descriptor_errors(parsed),
                                              ("capabilities", "capability")))
        if errors:
            return Result("role.capability", "fail", "；".join(errors[:20]))
        return Result("role.capability", "pass", "能力边界合法")

    def check_role_gates(self) -> Result:
        errors: list[str] = []
        for entry in self.role_entries():
            parsed, error = self._parse_role(entry)
            if error:
                continue
            errors.extend(self._filter_errors(
                self._descriptor_errors(parsed),
                ("gate", "第二派单入口"),
            ))
        if errors:
            return Result("role.gates", "fail", "；".join(errors[:20]))
        return Result("role.gates", "pass", "门禁边界合法（base ∪ appendGates）")

    def check_role_inherit(self) -> Result:
        problems: list[str] = []
        warns: list[str] = []
        seen: dict[tuple[str, str], str] = {}
        manifest_entries = {entry.get("path") for entry in self.manifest_entries("file")}
        stubs = {stub.get("path"): stub for stub in (self.manifest() or {}).get("roleStubs", [])
                 if isinstance(stub, dict)}
        for entry in self.role_entries():
            rel = entry["path"].relative_to(self.root).as_posix()
            key = (entry["runtime"], entry["id"])
            if key in seen:
                problems.append(f"{rel} 与 {seen[key]} 重复同一逻辑角色 {entry['id']}")
            seen[key] = rel
            if rel in manifest_entries:
                problems.append(f"{rel} 被列入受管 entries")
            parsed, error = self._parse_role(entry)
            if error:
                problems.append(error)
                continue
            errors = self._filter_errors(self._descriptor_errors(parsed),
                                         ("extends", "managed", "基类"))
            problems.extend(errors)
            stub = stubs.get(rel)
            if stub and stub.get("sha256") and not same_hash(
                    stub.get("sha256"), sha256_bytes(entry["path"].read_bytes())):
                warns.append(f"{rel} 项目已编辑（project-modified）")
        if problems:
            return Result("role.inherit", "fail", "；".join(problems[:20]))
        if warns:
            return Result("role.inherit", "warn", "；".join(warns))
        return Result("role.inherit", "pass", f"{len(seen)} 个角色继承关系合法")

    def check_role_compose(self) -> Result:
        problems: list[str] = []
        entries = self.role_entries()
        for entry in entries:
            parsed, error = self._parse_role(entry)
            if error:
                problems.append(error)
                continue
            descriptor = parsed.get("descriptor") or {}
            role_id = descriptor.get("id") or entry["id"]
            outcome = dispatch_lib.build_compose(self.root, str(role_id), entry["runtime"])
            report = outcome.report
            if not outcome.ok:
                problems.extend(f"{role_id}: {item}" for item in outcome.errors)
                continue
            if report["unresolvedTokens"]:
                problems.append(f"{role_id}: unresolvedTokens={report['unresolvedTokens']}")
            if report["residualSkillRootTokens"]:
                problems.append(f"{role_id}: residualSkillRootTokens={report['residualSkillRootTokens']}")
            if report["protectedHeadingConflicts"]:
                problems.append(f"{role_id}: 受保护标题冲突 {report['protectedHeadingConflicts']}")
            if not report["basePointersPresent"]:
                problems.append(f"{role_id}: basePointersPresent=false")
            if not report["composedSha256"]:
                problems.append(f"{role_id}: 缺 composedSha256")

        if problems:
            return Result("role.compose", "fail", "；".join(problems[:20]),
                          evidence={"roles": len(entries)})
        return Result("role.compose", "pass", f"{len(entries)} 个角色组合通过（append-only，无残留 token）")

    def check_role_composed(self) -> Result:
        config = self.config() or {}
        manifest = self.manifest() or {}
        roles = config.get("roles") if isinstance(config.get("roles"), dict) else {}
        enabled = roles.get("enabled") if isinstance(roles.get("enabled"), list) else list(CORE_IDS)
        manifest_entries = {
            str(item.get("path")): item
            for item in (manifest.get("composedAgents") or [])
            if isinstance(item, dict)
        }
        problems: list[str] = []
        checked = 0
        for runtime in ("qoder", "opencode"):
            for role in enabled:
                try:
                    dispatch_lib.locate_role(self.root, str(role), runtime)
                except Exception:  # noqa: BLE001
                    continue
                rel = (Path(".qoder/agents/crew") / f"{role}.md" if runtime == "qoder"
                       else Path(".opencode/agent") / f"crew-{role}.md")
                path = self.root / rel
                if not path.is_file():
                    problems.append(f"缺少组合基础代理：{rel.as_posix()}")
                    continue
                entry = manifest_entries.get(rel.as_posix())
                if not entry:
                    problems.append(f"manifest 未登记组合基础代理：{rel.as_posix()}")
                    continue
                text = read_text(path) or ""
                if entry.get("sha256") and sha256_bytes(text.encode("utf-8")) != str(
                        entry.get("sha256")).removeprefix("sha256:"):
                    problems.append(f"组合基础代理 hash 不一致：{rel.as_posix()}")
                    continue
                outcome = dispatch_lib.build_compose(self.root, str(role), runtime)
                if not outcome.ok or outcome.composed_body not in text:
                    problems.append(f"组合基础代理过期：{rel.as_posix()}")
                    continue
                checked += 1
        if problems:
            return Result("role.composed", "fail", "；".join(problems[:20]))
        return Result("role.composed", "pass",
                      f"{checked} 个组合基础代理存在且 hash/内容一致")

    def check_role_delivery(self) -> Result:
        crew = self.crew()
        if crew is None:
            return Result("role.delivery", "env", self._crew_error)
        log = crew / "agent-log.jsonl"
        if not log.is_file():
            return Result("role.delivery", "skip", "无 start 记录（agent-log 不存在）")
        rows: list[dict] = []
        for line_no, line in enumerate((read_text(log) or "").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                return Result("role.delivery", "fail", f"agent-log:{line_no} JSON 非法")
            if isinstance(row, dict):
                rows.append(row)
        starts = [row for row in rows if row.get("action") == "start"]
        if not starts:
            return Result("role.delivery", "skip", "无 start 记录")
        row = starts[-1]
        problems: list[str] = []
        if row.get("delivery") != "composed-agent":
            problems.append(f"delivery 非法：{row.get('delivery')!r}")
        if not row.get("composedSha256"):
            problems.append("缺 composedSha256")
        compose_path = row.get("composePath")
        if not compose_path:
            problems.append("缺 composePath")
        else:
            path = Path(str(compose_path))
            compose_root = crew / "compose"
            if not is_inside(path, compose_root):
                problems.append(f"composePath 不在 {compose_root}：{compose_path}")
            elif not path.exists():
                problems.append(f"composePath 不存在：{compose_path}")
            prompt = row.get("promptPath")
            if prompt and not Path(str(prompt)).exists():
                problems.append(f"promptPath 不存在：{prompt}")
        if row.get("delivery") == "composed-agent":
            composed = row.get("composedPath") or row.get("promptPath")
            if not composed:
                problems.append("缺 composedPath")
            else:
                composed_path = Path(str(composed))
                allowed = (is_inside(composed_path, crew / "compose")
                           or is_inside(composed_path, self.root / ".qoder/agents/crew")
                           or is_inside(composed_path, self.root / ".opencode/agent"))
                if not allowed:
                    problems.append(f"composedPath 位置非法：{composed}")
                elif not composed_path.is_file():
                    problems.append(f"composedPath 不存在：{composed}")
        for base in (self.root / ".qoder/agents", self.root / ".opencode/agent",
                     self.root / "docs"):
            if base.exists():
                for path in base.rglob("*composed*"):
                    problems.append(f"项目发现目录出现组合产物：{path.relative_to(self.root)}")
        role_id, runtime = row.get("role"), row.get("runtime")
        if role_id and runtime:
            outcome = dispatch_lib.build_compose(self.root, str(role_id), str(runtime))
            if outcome.ok and row.get("composedSha256") != outcome.report["composedSha256"]:
                problems.append(
                    "agent-log composedSha256 与当前 compose 报告不一致 "
                    f"({row.get('composedSha256')} vs {outcome.report['composedSha256']})"
                )
        if problems:
            return Result("role.delivery", "fail", "；".join(problems[:20]),
                          evidence={"agentLog": str(log)})
        return Result("role.delivery", "pass",
                      f"最近 start({row.get('agent')}) 交付可复核")

    def check_role_names(self) -> Result:
        problems: list[str] = []
        ids: dict[tuple[str, str], str] = {}
        runtime_names: dict[tuple[str, str], str] = {}
        for entry in self.role_entries():
            rel = entry["path"].relative_to(self.root).as_posix()
            key = (entry["runtime"], entry["id"])
            if key in ids:
                problems.append(f"重复逻辑 id {entry['id']}（{ids[key]} / {rel}）")
            ids[key] = rel
            if entry["custom"] and entry["id"] in CORE_IDS:
                problems.append(f"自定义角色与核心角色同名：{rel}")
            if entry["runtime"] == "opencode":
                runtime_name = entry["path"].stem
            else:
                frontmatter, _ = dispatch_lib.split_frontmatter(read_text(entry["path"]) or "")
                runtime_name = (dispatch_lib.scalar(
                    (frontmatter or {}).get("fields", {}).get("name", ""))
                    or entry["path"].stem)
            name_key = (entry["runtime"], runtime_name)
            if name_key in runtime_names:
                problems.append(f"重复 runtime agent 名 {runtime_name}（{runtime_names[name_key]} / {rel}）")
            runtime_names[name_key] = rel
        if problems:
            return Result("role.names", "fail", "；".join(problems[:20]))
        return Result("role.names", "pass", "角色 id / runtime 名唯一")

    def _run_cli(self, argv: list[str], timeout: int = 30) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  cwd=str(self.root), timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return 127, "", str(exc)
        return proc.returncode, proc.stdout, proc.stderr

    def check_role_discovery_qoder(self) -> Result:
        nested = [path for path in self.nested_role_files()
                  if ".qoder/agents" in path.as_posix()]
        if nested:
            return Result("role.discovery.qoder", "fail",
                          f"非规范落点：{[p.relative_to(self.root).as_posix() for p in nested]}")
        entries = [entry for entry in self.role_entries() if entry["runtime"] == "qoder"]
        if not entries:
            return Result("role.discovery.qoder", "pass", "无 Qoder 角色文件")
        if not shutil.which("qodercli"):
            return Result("role.discovery.qoder", "env", "qodercli 不在 PATH")
        rc, stdout, stderr = self._run_cli(
            ["qodercli", "agent", "list", "--setting-sources", "project"], timeout=60)
        if rc != 0:
            return Result("role.discovery.qoder", "env",
                          f"qodercli agent list rc={rc}：{stderr.strip()[:200]}")
        discovered = parse_qoder_agent_list(stdout)
        missing = [entry["id"] for entry in entries if entry["id"] not in discovered]
        if missing:
            return Result("role.discovery.qoder", "fail", f"文件存在但未被发现：{missing}")
        return Result("role.discovery.qoder", "pass", f"{len(entries)} 个 Qoder 角色被发现")

    def check_role_discovery_opencode(self) -> Result:
        nested = [path for path in self.nested_role_files()
                  if ".opencode/agent" in path.as_posix()]
        if nested:
            return Result("role.discovery.opencode", "fail",
                          "非规范落点："
                          f"{[p.relative_to(self.root).as_posix() for p in nested]}；"
                          "自定义角色应在顶层 custom-<id>.md")
        entries = [entry for entry in self.role_entries() if entry["runtime"] == "opencode"]
        if not entries:
            return Result("role.discovery.opencode", "pass", "无 opencode 角色文件")
        if not shutil.which("opencode"):
            return Result("role.discovery.opencode", "env", "opencode 不在 PATH")
        rc, stdout, stderr = self._run_cli(["opencode", "agent", "list"], timeout=60)
        if rc != 0:
            return Result("role.discovery.opencode", "env",
                          f"opencode agent list rc={rc}：{stderr.strip()[:200]}")
        discovered = {line.strip().split(" ")[0] for line in stdout.splitlines() if line.strip()}
        missing = [entry["path"].stem for entry in entries
                   if entry["path"].stem not in discovered]
        if missing:
            return Result("role.discovery.opencode", "fail", f"文件存在但未被发现：{missing}")
        return Result("role.discovery.opencode", "pass", f"{len(entries)} 个 opencode 角色被发现")

    def _runtime_direct_files(self) -> list[Path]:
        files: list[Path] = []
        agents = self.root / "AGENTS.md"
        if agents.is_file():
            files.append(agents)
        for rel in ("docs/orchestration", "docs/crew"):
            base = self.root / rel
            if base.is_dir():
                files.extend(sorted(path for path in base.rglob("*.md") if path.is_file()))
        files.extend(entry["path"] for entry in self.role_entries())
        custom = self.root / "scripts/custom"
        if custom.is_dir():
            files.extend(sorted(path for path in custom.rglob("*") if path.is_file()))
        return [path for path in files if not self._skip_scan_path(path)]

    def check_role_runtime_direct(self) -> Result:
        hits: list[str] = []
        for path in self._runtime_direct_files():
            rel = path.relative_to(self.root).as_posix()
            if rel.startswith("scripts/") and Path(rel).name.startswith("start-"):
                text = read_text(path) or ""
                if "dispatch.py" in text:
                    continue
            text = read_text(path)
            if text is None:
                continue
            for line_no, line in enumerate(text.splitlines(), 1):
                for pattern in RUNTIME_DIRECT_PATTERNS:
                    match = pattern.search(line)
                    if match:
                        hits.append(f"{rel}:{line_no} `{match.group(0)}`")
                        break
        if hits:
            return Result("role.runtime-direct", "fail",
                          "禁止绕过 dispatch 直启：" + "；".join(hits[:20]),
                          next="改用 python3 scripts/dispatch.py start/prompt/close")
        return Result("role.runtime-direct", "pass", "未发现 runtime 直启/生命周期命令")

    def check_role_orphan(self) -> Result:
        config = self.config() or {}
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        problems: list[str] = []
        warns: list[str] = []
        custom_entries = hooks.get("customRoles")
        registered: set[str] = set()
        if custom_entries is not None:
            if not isinstance(custom_entries, list):
                problems.append("hooks.customRoles 必须是数组")
            else:
                for entry in custom_entries:
                    if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
                        problems.append("hooks.customRoles 条目缺 id")
                        continue
                    registered.add(entry["id"])
                    role_id = entry["id"]
                    if not (dispatch_lib.custom_role_path(self.root, "qoder", role_id).is_file()
                            or dispatch_lib.custom_role_path(self.root, "opencode", role_id).is_file()):
                        problems.append(f"customRoles 启用的角色缺角色文件：{role_id}")
        discovered_custom = {entry["id"] for entry in self.role_entries() if entry["custom"]}
        for role_id in sorted(discovered_custom - registered):
            warns.append(f"未登记自定义角色（禁用）：{role_id}")
        role_ext = hooks.get("roleExt")
        if isinstance(role_ext, dict):
            for role_id, rel in role_ext.items():
                if not (self.root / str(rel)).is_file():
                    problems.append(f"roleExt 文件不存在：{rel}")
        if problems:
            return Result("role.orphan", "fail", "；".join(problems[:20]))
        if warns:
            return Result("role.orphan", "warn", "；".join(warns))
        return Result("role.orphan", "pass", "无孤儿自定义角色/扩展")

    # ================= F. runtime =================

    def check_runtime_dir(self) -> Result:
        crew = self.crew()
        if crew is None:
            return Result("runtime.dir", "env", self._crew_error)
        if (self.root / ".git").is_file() and (self.root / ".git/crew").exists():
            return Result("runtime.dir", "fail",
                          f"worktree 内出现本地状态目录：{self.root / '.git/crew'}")
        mode = "non-git" if crew.name == ".crew" and crew.parent == self.root else "git"
        if mode == "non-git":
            return Result("runtime.dir", "warn", f"非 git 项目，降级 {crew}（mode=non-git）")
        return Result("runtime.dir", "pass", f"crewDir={crew}")

    def check_runtime_state(self) -> Result:
        crew = self.crew()
        if crew is None:
            return Result("runtime.state", "env", self._crew_error)
        events = crew / "events.jsonl"
        if not events.is_file():
            return Result("runtime.state", "pass", "无事件日志（空状态）")
        for line_no, line in enumerate((read_text(events) or "").splitlines(), 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except ValueError:
                return Result("runtime.state", "fail", f"events.jsonl:{line_no} JSON 非法",
                              next="保留现场；用 crew-state recover")
        return Result("runtime.state", "pass", "事件日志可解析")

    def check_runtime_names(self) -> Result:
        config = self.config()
        if config is None:
            return Result("runtime.names", "fail", self._config_error)
        if not shutil.which("herdr"):
            return Result("runtime.names", "env", "herdr 不在 PATH")
        rc, stdout, stderr = self._run_cli(["herdr", "agent", "list"])
        if rc != 0:
            return Result("runtime.names", "env",
                          f"herdr agent list rc={rc}：{stderr.strip()[:200]}")
        try:
            data = json.loads(stdout)
        except ValueError:
            return Result("runtime.names", "env", "herdr agent list 输出非 JSON")
        result = data.get("result") if isinstance(data, dict) else None
        agents = result.get("agents") if isinstance(result, dict) else None
        agents = agents if isinstance(agents, list) else []
        project_id = str(config.get("projectId") or "")
        helm = config.get("helm") if isinstance(config.get("helm"), dict) else {}
        helm_name = helm.get("agentName")
        role_ids = {entry["id"] for entry in self.role_entries()} | CORE_IDS
        custom_entries = ((config.get("hooks") or {}).get("customRoles")
                          if isinstance(config.get("hooks"), dict) else None)
        if isinstance(custom_entries, list):
            role_ids |= {str(entry.get("alias")) for entry in custom_entries
                         if isinstance(entry, dict) and entry.get("alias")}
        warns: list[str] = []
        for agent in agents:
            if not isinstance(agent, dict):
                continue
            name = str(agent.get("name") or "")
            if not name or name == helm_name:
                continue
            if project_id and name.startswith(f"{project_id}-"):
                suffix = name[len(project_id) + 1:]
                suffix = re.sub(r"-\d+$", "", suffix)
                if suffix not in role_ids:
                    warns.append(f"未知托管 agent 名：{name}")
        if warns:
            return Result("runtime.names", "warn", "；".join(warns))
        return Result("runtime.names", "pass", f"Herdr {len(agents)} 个 agent 命名合法")

    def check_runtime_lock(self) -> Result:
        crew = self.crew()
        if crew is None:
            return Result("runtime.lock", "env", self._crew_error)
        locks = sorted(crew.glob("*.lock")) if crew.is_dir() else []
        if not locks:
            return Result("runtime.lock", "pass", "无锁文件")
        active: list[str] = []
        stale: list[str] = []
        guard_pid: int | None = None
        for lock in locks:
            pid = None
            text = read_text(lock) or ""
            try:
                data = json.loads(text)
            except (ValueError, TypeError):
                data = None
            if isinstance(data, dict):
                try:
                    pid = int(data.get("pid"))
                except (ValueError, TypeError):
                    pid = None
            else:
                # guard.lock 等纯文本锁：首行/首列写 pid
                try:
                    pid = int(text.strip().split()[0])
                except (ValueError, IndexError):
                    pid = None
            if lock.name == "guard.lock":
                # guard 常驻持锁：活 pid 属正常，不算活锁 fail
                if pid is not None and pid > 0 and _pid_alive(pid):
                    guard_pid = pid
                elif pid is None:
                    stale.append(f"{lock.name}（无法识别 pid）")
                else:
                    stale.append(f"{lock.name}（pid={pid}）")
                continue
            if pid is None:
                stale.append(f"{lock.name}（无法识别 pid）")
            elif pid > 0 and _pid_alive(pid):
                active.append(f"{lock.name}（pid={pid}）")
            else:
                stale.append(f"{lock.name}（pid={pid}）")
        if active:
            return Result("runtime.lock", "fail", f"有活锁：{active}",
                          next="等待锁释放；确认无 init/upgrade 在跑后人工清理")
        if stale:
            return Result("runtime.lock", "warn", f"stale 锁：{stale}",
                          next="确认进程已退出后人工删除锁文件")
        return Result("runtime.lock", "pass", f"guard 运行中（pid={guard_pid}）")

    def check_runtime_worktree(self) -> Result:
        rc, stdout, _ = self._run_cli(["git", "-C", str(self.root), "status", "--porcelain"])
        if rc != 0:
            return Result("runtime.worktree", "warn", "非 git 项目，跳过工作区检查")
        dirty = [line for line in stdout.splitlines() if line.strip()]
        if dirty:
            return Result("runtime.worktree", "fail",
                          f"工作区不干净（{len(dirty)} 项）；升级/迁移前先清理",
                          evidence={"status": dirty[:20]})
        return Result("runtime.worktree", "pass", "工作区干净")

    # ================= G. hooks =================

    def check_hooks_schema(self) -> Result:
        config = self.config()
        if config is None:
            return Result("hooks.schema", "fail", self._config_error)
        hooks = config.get("hooks")
        if hooks is None:
            return Result("hooks.schema", "pass", "未配置 hooks")
        if not isinstance(hooks, dict):
            return Result("hooks.schema", "fail", "hooks 必须是对象")
        problems: list[str] = []
        allowed = {"paths", "roleExt", "customRoles", "gatePolicy", "projectScripts"}
        unknown = sorted(set(hooks) - allowed)
        if unknown:
            problems.append(f"未知 hooks 类：{unknown}")
        paths = hooks.get("paths")
        if paths is not None:
            if not isinstance(paths, dict):
                problems.append("hooks.paths 必须是对象")
            else:
                extra = sorted(set(paths) - {"topology", "projectExt"})
                if extra:
                    problems.append(f"hooks.paths 未知键：{extra}")
        if hooks.get("roleExt") is not None and not isinstance(hooks.get("roleExt"), dict):
            problems.append("hooks.roleExt 必须是对象")
        if hooks.get("customRoles") is not None and not isinstance(hooks.get("customRoles"), list):
            problems.append("hooks.customRoles 必须是数组")
        if hooks.get("gatePolicy") is not None and not isinstance(hooks.get("gatePolicy"), dict):
            problems.append("hooks.gatePolicy 必须是对象")
        if hooks.get("projectScripts") is not None and not isinstance(hooks.get("projectScripts"), list):
            problems.append("hooks.projectScripts 必须是数组")
        if problems:
            return Result("hooks.schema", "fail", "；".join(problems))
        return Result("hooks.schema", "pass", "五类 hooks schema 合法")

    def check_hooks_gates(self) -> Result:
        config = self.config()
        if config is None:
            return Result("hooks.gates", "fail", self._config_error)
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        policy = hooks.get("gatePolicy")
        if policy is None:
            return Result("hooks.gates", "pass", "未配置项目追加 gate")
        if not isinstance(policy, dict):
            return Result("hooks.gates", "fail", "gatePolicy 必须是对象")
        problems: list[str] = []
        forbidden_keys = {"remove", "delete", "override", "disable", "rewrite"}
        unknown = sorted(set(policy) - {"appendGates", "approvers"})
        if unknown:
            problems.append(f"gatePolicy 未知键：{unknown}")
        for key in forbidden_keys & set(policy):
            problems.append(f"gatePolicy 出现删除/改写核心门字段：{key}")
        append = policy.get("appendGates")
        if append is not None:
            if not isinstance(append, list):
                problems.append("appendGates 必须是数组")
                append = []
            for entry in append:
                if not isinstance(entry, dict):
                    problems.append("appendGates 条目必须是对象")
                    continue
                gate_id = entry.get("id")
                if gate_id in dispatch_lib.CORE_GATE_IDS:
                    problems.append(f"appendGates 不得使用核心 gate id：{gate_id}")
                if not entry.get("description"):
                    problems.append(f"gate {gate_id} 缺 description")
                approver = entry.get("approver")
                if approver and approver not in (CORE_IDS | {"Captain"}):
                    problems.append(f"gate {gate_id} approver 未知：{approver}")
        if problems:
            return Result("hooks.gates", "fail", "；".join(problems))
        return Result("hooks.gates", "pass", "门禁只追加、无核心门改写")

    def check_hooks_scripts(self) -> Result:
        config = self.config()
        if config is None:
            return Result("hooks.scripts", "fail", self._config_error)
        hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
        scripts = hooks.get("projectScripts")
        if scripts is None:
            return Result("hooks.scripts", "pass", "未配置项目脚本")
        if not isinstance(scripts, list):
            return Result("hooks.scripts", "fail", "projectScripts 必须是数组")
        problems: list[str] = []
        warns: list[str] = []
        seen: set[str] = set()
        for entry in scripts:
            if not isinstance(entry, dict):
                problems.append("projectScripts 条目必须是对象")
                continue
            script_id = entry.get("id")
            rel = entry.get("path")
            if not script_id or not rel:
                problems.append("projectScripts 条目缺 id/path")
                continue
            if script_id in seen:
                problems.append(f"projectScripts 重复 id：{script_id}")
            seen.add(script_id)
            path = self.root / str(rel)
            if not path.is_file():
                problems.append(f"项目脚本不存在：{rel}")
            elif not is_inside(path, self.root):
                problems.append(f"项目脚本越界：{rel}")
            if entry.get("readOnly") is False:
                warns.append(f"{script_id} 可写（声明 readOnly=false）")
        if problems:
            return Result("hooks.scripts", "fail", "；".join(problems))
        if warns:
            return Result("hooks.scripts", "warn", "；".join(warns))
        return Result("hooks.scripts", "pass", f"{len(scripts)} 个项目脚本声明合法")

    # ================= H. install / upgrade / skill =================

    def check_install_log(self) -> Result:
        log = self.root / "docs/crew/install-log.jsonl"
        text = read_text(log)
        if text is None:
            return Result("install.log", "warn", "缺少 install-log.jsonl",
                          next="重跑 init 生成安装日志")
        lines = [line for line in text.splitlines() if line.strip()]
        for line_no, line in enumerate(lines, 1):
            try:
                data = json.loads(line)
            except ValueError:
                return Result("install.log", "fail", f"install-log.jsonl:{line_no} JSON 非法",
                              next="保留现场；检查后重跑 init")
            if not isinstance(data, dict) or not data.get("result"):
                return Result("install.log", "fail",
                              f"install-log.jsonl:{line_no} 缺 result")
        return Result("install.log", "pass", f"{len(lines)} 条安装日志")

    def check_install_version(self) -> Result:
        config = self.config()
        if config is None:
            return Result("install.version", "fail", self._config_error)
        config_version = config.get("skillVersion")
        if not isinstance(config_version, str) or not config_version:
            return Result("install.version", "fail", "配置缺 skillVersion")
        source = self.source_root()
        source_version = ""
        if source and (source / "SKILL.md").is_file():
            frontmatter, _ = dispatch_lib.split_frontmatter(
                read_text(source / "SKILL.md") or "")
            if frontmatter:
                source_version = dispatch_lib.scalar(
                    frontmatter["fields"].get("version", ""))
        if not source_version:
            return Result("install.version", "warn",
                          f"技能源版本不可读（source={source}）",
                          next="检查 skillRoot/--check-source，或重跑 upgrade")
        manifest = self.manifest() or {}
        manifest_version = manifest.get("skillVersion")
        problems: list[str] = []
        if config_version != source_version:
            problems.append(f"config skillVersion={config_version} != 源 {source_version}")
        if manifest_version and manifest_version != config_version:
            problems.append(f"manifest skillVersion={manifest_version} != config {config_version}")
        if problems:
            return Result("install.version", "fail", "；".join(problems),
                          next="改 config 或重跑 upgrade")
        return Result("install.version", "pass",
                      f"config/源/manifest 版本一致：{config_version}")

    def check_upgrade_scripts(self) -> Result:
        crew = self.crew()
        if crew is None:
            return Result("upgrade.scripts", "env", self._crew_error)
        backups = crew / "backups"
        dirs = sorted(path for path in backups.glob("*") if path.is_dir()) if backups.is_dir() else []
        if not dirs:
            return Result("upgrade.scripts", "warn", "无升级记录（backups 为空）")
        latest = dirs[-1]
        manifest = latest / "backup-manifest.json"
        text = read_text(manifest)
        if text is None:
            return Result("upgrade.scripts", "fail", f"最新备份缺 backup-manifest.json：{latest}")
        try:
            data = json.loads(text)
        except ValueError as exc:
            return Result("upgrade.scripts", "fail", f"backup-manifest.json 非法：{exc}")
        files = data.get("files") if isinstance(data, dict) else None
        if not isinstance(files, list):
            return Result("upgrade.scripts", "fail", "backup-manifest.json 缺 files 数组")
        for entry in files:
            if not isinstance(entry, dict) or not entry.get("path"):
                return Result("upgrade.scripts", "fail", "backup-manifest files 条目缺 path")
        return Result("upgrade.scripts", "pass",
                      f"最新备份 {latest.name}，{len(files)} 个文件记录")

    def _crew_lint(self) -> Path | None:
        source = self.source_root()
        if not source:
            return None
        lint = source / "scripts/crew-lint.py"
        return lint if lint.is_file() else None

    def check_skill_lint(self) -> Result:
        lint = self._crew_lint()
        source = self.source_root()
        if lint is None or source is None:
            return Result("skill.lint", "skip", "技能源无 crew-lint.py（D 交付前）")
        rc, stdout, stderr = self._run_cli(
            [sys.executable, str(lint), "--root", str(source)], timeout=120)
        if rc == 0:
            return Result("skill.lint", "pass", (stdout.strip().splitlines() or [""])[-1])
        if rc == 3:
            return Result("skill.lint", "env", stderr.strip()[:300])
        return Result("skill.lint", "fail", (stdout + stderr).strip()[:500],
                      next="按 crew-lint 输出修复技能资产")

    def check_skill_purity(self) -> Result:
        lint = self._crew_lint()
        source = self.source_root()
        if lint is None or source is None:
            return Result("skill.purity", "skip", "技能源无 crew-lint.py（D 交付前）")
        rc, stdout, _ = self._run_cli([sys.executable, str(lint), "--help"], timeout=60)
        if "--purity" not in stdout:
            return Result("skill.purity", "skip",
                          "crew-lint 尚无 --purity（D 交付前，E 必须 PASS）")
        rc, stdout, stderr = self._run_cli(
            [sys.executable, str(lint), "--root", str(source), "--purity"], timeout=120)
        if rc == 0:
            return Result("skill.purity", "pass", (stdout.strip().splitlines() or [""])[-1])
        return Result("skill.purity", "fail", (stdout + stderr).strip()[:500])

    # ================= I. playbooks =================

    def check_playbooks_library(self) -> Result:
        skill_root = self.skill_root()
        if skill_root is None:
            return Result("playbooks.library", "fail",
                          self._config_error or "配置缺 skillRoot",
                          next="先跑 python3 scripts/init.py 生成配置")
        layers = (
            ("技能层", skill_root / "templates/playbooks", ("README.md", *PLAYBOOK_SEEDS)),
            ("项目层", self.root / ".planning/playbooks", ("README.md",)),
        )
        lint = Path(__file__).resolve().parent / "plan-status.py"
        problems: list[str] = []
        next_steps: list[str] = []
        summary: list[str] = []
        for layer, directory, required in layers:
            if not directory.is_dir():
                problems.append(f"{layer}剧本库目录缺失：{directory}")
                next_steps.append(
                    "重跑 python3 scripts/init.py 生成 .planning/playbooks/README.md"
                    if layer == "项目层"
                    else "补齐技能源 templates/playbooks/（README + 种子剧本）"
                )
                continue
            missing = [name for name in required if not (directory / name).is_file()]
            if missing:
                problems.append(f"{layer}剧本库缺少：{', '.join(missing)}")
                next_steps.append(
                    "重跑 python3 scripts/init.py 生成项目层 README"
                    if layer == "项目层"
                    else "补齐技能源 templates/playbooks/ 种子剧本"
                )
            files = sorted(path for path in directory.glob("*.md")
                           if path.is_file() and path.name.lower() != "readme.md")
            summary.append(f"{layer} {len(files)} 个剧本")
            if not files:
                continue
            rc, stdout, stderr = self._run_cli(
                [sys.executable, str(lint), "--playbooks", str(directory)], timeout=60)
            if rc == 3:
                problems.append(f"剧本校验不可用：{stderr.strip()[:200]}")
                next_steps.append("确认 scripts/plan-status.py 已随技能安装")
            elif rc != 0:
                detail = (stdout + stderr).strip().replace("\n", "；")[:400]
                problems.append(f"{layer}剧本结构不合法：{detail}")
                next_steps.append(
                    f"按输出修复剧本结构后重跑 python3 scripts/plan-status.py "
                    f"--playbooks {directory}")
        if problems:
            return Result("playbooks.library", "fail", "；".join(problems),
                          evidence={"layers": summary},
                          next="；".join(dict.fromkeys(next_steps)))
        return Result("playbooks.library", "pass",
                      f"两层剧本库结构合法（{'；'.join(summary)}；README 齐全）")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


CHECKS = [
    "manifest.schema", "manifest.hash.file", "manifest.hash.block", "manifest.config",
    "manifest.roleStubs", "manifest.orphan",
    "agents.markers", "agents.block", "agents.duplicate", "agents.pending",
    "docs.duplicate", "docs.pointers", "skill.source", "docs.links",
    "config.schema", "config.paths", "config.identity",
    "role.frontmatter", "role.descriptor", "role.capability", "role.inherit",
    "role.compose", "role.composed", "role.delivery", "role.gates", "role.names",
    "role.discovery.qoder", "role.discovery.opencode", "role.runtime-direct",
    "role.orphan",
    "runtime.dir", "runtime.state", "runtime.names", "runtime.lock", "runtime.worktree",
    "hooks.schema", "hooks.gates", "hooks.scripts",
    "install.log", "install.version", "upgrade.scripts",
    "skill.purity", "skill.lint",
    "playbooks.library",
]
METHODS = {
    "manifest.schema": "check_manifest_schema",
    "manifest.hash.file": "check_manifest_hash_file",
    "manifest.hash.block": "check_manifest_hash_block",
    "manifest.config": "check_manifest_config",
    "manifest.roleStubs": "check_manifest_role_stubs",
    "manifest.orphan": "check_manifest_orphan",
    "agents.markers": "check_agents_markers",
    "agents.block": "check_agents_block",
    "agents.duplicate": "check_agents_duplicate",
    "agents.pending": "check_agents_pending",
    "docs.duplicate": "check_docs_duplicate",
    "docs.pointers": "check_docs_pointers",
    "skill.source": "check_skill_source",
    "docs.links": "check_docs_links",
    "config.schema": "check_config_schema",
    "config.paths": "check_config_paths",
    "config.identity": "check_config_identity",
    "role.frontmatter": "check_role_frontmatter",
    "role.descriptor": "check_role_descriptor",
    "role.capability": "check_role_capability",
    "role.inherit": "check_role_inherit",
    "role.compose": "check_role_compose",
    "role.composed": "check_role_composed",
    "role.delivery": "check_role_delivery",
    "role.gates": "check_role_gates",
    "role.names": "check_role_names",
    "role.discovery.qoder": "check_role_discovery_qoder",
    "role.discovery.opencode": "check_role_discovery_opencode",
    "role.runtime-direct": "check_role_runtime_direct",
    "role.orphan": "check_role_orphan",
    "runtime.dir": "check_runtime_dir",
    "runtime.state": "check_runtime_state",
    "runtime.names": "check_runtime_names",
    "runtime.lock": "check_runtime_lock",
    "runtime.worktree": "check_runtime_worktree",
    "hooks.schema": "check_hooks_schema",
    "hooks.gates": "check_hooks_gates",
    "hooks.scripts": "check_hooks_scripts",
    "install.log": "check_install_log",
    "install.version": "check_install_version",
    "upgrade.scripts": "check_upgrade_scripts",
    "skill.purity": "check_skill_purity",
    "skill.lint": "check_skill_lint",
    "playbooks.library": "check_playbooks_library",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doctor.py", description="agent-crew 只读体检（hash + 语义）")
    parser.add_argument("--root", default=".", help="项目仓根（默认 cwd）")
    parser.add_argument("--profile", default="dev", choices=["dev", "release"])
    parser.add_argument("--strict", action="store_true", help="warn 视为失败")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    parser.add_argument("--out", help="JSON 落盘路径（用户显式指定）")
    parser.add_argument("--only", action="append", default=[], help="只跑指定检查项（可重复）")
    parser.add_argument("--check-source", help="技能源路径覆盖（install.version 用）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"doctor: 项目根不存在：{root}", file=sys.stderr)
        return 1
    doctor = Doctor(root, args.profile, args.strict, args.check_source)
    selected = args.only or CHECKS
    results: list[Result] = []
    for item_id in selected:
        if item_id not in METHODS:
            results.append(Result(item_id, "fail", f"未知检查项：{item_id}",
                                  next=f"可用：{', '.join(CHECKS)}"))
            continue
        method = getattr(doctor, METHODS[item_id])
        try:
            results.append(method())
        except Exception as exc:  # noqa: BLE001 - 单检查异常不得打崩 doctor
            results.append(Result(item_id, "fail", f"检查异常：{type(exc).__name__}: {exc}"))
    counts = {"pass": 0, "warn": 0, "fail": 0, "skip": 0, "pending": 0, "env": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    if counts["fail"]:
        exit_code = 1
    elif counts["env"]:
        exit_code = 3
    elif counts["pending"]:
        exit_code = 2
    elif args.strict and counts["warn"]:
        exit_code = 1
    else:
        exit_code = 0
    config = doctor.config() or {}
    skill_root = doctor.source_root()
    skill_version = ""
    if skill_root and (skill_root / "SKILL.md").is_file():
        frontmatter, _ = dispatch_lib.split_frontmatter(
            read_text(skill_root / "SKILL.md") or "")
        if frontmatter:
            skill_version = dispatch_lib.scalar(frontmatter["fields"].get("version", ""))
    report = {
        "schemaVersion": 1,
        "runId": utc_now().replace(":", "").replace("-", "") + "-" + os.urandom(3).hex(),
        "root": str(root),
        "profile": args.profile,
        "strict": args.strict,
        "skillVersion": skill_version or str(config.get("skillVersion") or ""),
        "configHash": config_hash(config) if config else "",
        "summary": counts,
        "items": [result.as_dict() for result in results],
        "exitCode": exit_code,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        out = Path(args.out).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(payload + "\n", encoding="utf-8")
    if args.json:
        print(payload)
    else:
        for result in results:
            print(f"[{result.status.upper()}] {result.id}：{result.detail}")
            if result.next:
                print(f"  下一步：{result.next}")
        print(f"doctor: {counts}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
