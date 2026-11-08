#!/usr/bin/env python3
"""工单占用、暂存、提交自检与收口核验（decisions §4）。

子命令：
  claim       占用文件/资产；同一文件同时只允许一个 owner，冲突立即失败并列出持有者
  release     释放占用；同 owner 重复 release 无副作用
  stage       只暂存回执/工单声明的文件；清单外有改动即拒绝，绝不使用 git add -A
  precommit   numstat + 未提交改动 + 清单外文件 + 本地 import 闭包提示（提示不阻断）
  close       numstat + git status --short + 证据清单 + 可选 ls-remote（默认关闭）

退出码：0 成功（含幂等空操作）；2 拒绝（占用冲突/清单外文件/缺少声明）；
        3 git 或工件读写失败。
边界：不 commit、不 push（--ls-remote 只读）、不写 .planning、不改他人文件。

存储（与 crew-msg 一致，取 git common dir，worktree 共享）：
  <git-common-dir>/crew/occupancy.json   {<path>: <owner>} 或 {"files": {...}}
  <git-common-dir>/crew/inbox/*.json     回执（task、status、changed_files[]、evidence[]）

声明来源（stage 必须至少一个；precommit/close 可选）：
  --receipt <id|path>  回执的 changed_files[]
  --task <id>          inbox 中该任务所有回执的 changed_files[] 并集
  --ticket <path>      工单 JSON 的 files[] / changed_files[] / 改动文件[]

示例：
  python3 scripts/crew-ticket.py claim --owner builder-1 scripts/a.py scripts/b.py
  python3 scripts/crew-ticket.py release --owner builder-1 scripts/a.py
  python3 scripts/crew-ticket.py stage --task T-1
  python3 scripts/crew-ticket.py precommit --task T-1
  python3 scripts/crew-ticket.py close --task T-1 --evidence "unittest 165 OK"
  python3 scripts/crew-ticket.py close --task T-1 --ls-remote
"""

from __future__ import annotations

import argparse
import ast
import fcntl
import json
import os
import posixpath
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

EXIT_OK, EXIT_REJECT, EXIT_FAIL = 0, 2, 3
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", ".planning"}
HINT_LIMIT = 20


class Rejected(Exception):
    """占用冲突、清单外文件或缺少声明。"""


class Failure(Exception):
    """git 或工件读写失败。"""


def resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    cur = Path.cwd().resolve()
    for cand in (cur, *cur.parents):
        if (cand / ".git").exists():
            return cand
    raise SystemExit("crew-ticket: 未找到 .git，请用 --root <仓根> 指定")


def git_common_dir(root: Path) -> Path:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise Failure(f"git unavailable: {exc}") from exc
    if proc.returncode == 0 and proc.stdout.strip():
        path = Path(proc.stdout.strip())
        if not path.is_absolute():
            path = root / path
        return path.resolve()
    return (root / ".git").resolve()


class Context:
    def __init__(self, root: Path):
        self.root = root
        self.crew = git_common_dir(root) / "crew"
        self.occupancy = self.crew / "occupancy.json"
        self.inbox = self.crew / "inbox"
        self.lock = self.crew / "occupancy.lock"

    @contextmanager
    def locked(self):
        self.crew.mkdir(parents=True, exist_ok=True)
        with open(self.lock, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def run_git(ctx: Context, args: list[str], *, binary: bool = False):
    cmd = ["git", "-C", str(ctx.root), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=not binary, check=False)
    except OSError as exc:
        raise Failure(f"git unavailable: {exc}") from exc
    if proc.returncode != 0:
        raw = proc.stderr if binary else proc.stderr
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        detail = next((line.strip() for line in raw.splitlines() if line.strip()), "")
        raise Failure(detail or f"git {' '.join(args)} failed")
    return proc


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def normalize_rel(value: str) -> str:
    raw = value.strip()
    if not raw:
        raise Rejected("empty file path")
    if os.path.isabs(raw) or raw.startswith("~"):
        raise Rejected(f"absolute path not allowed: {value}")
    norm = posixpath.normpath(raw)
    if norm in (".", "..") or norm.startswith("../") or norm.startswith("/"):
        raise Rejected(f"path escapes repo: {value}")
    return norm


def normalize_all(values) -> list[str]:
    return list(dict.fromkeys(normalize_rel(v) for v in values))


def load_occupancy(ctx: Context) -> tuple[dict, dict]:
    path = ctx.occupancy
    if not path.exists():
        empty: dict = {}
        return empty, empty
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Failure(f"occupancy.json unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Failure("occupancy.json is not an object")
    container = data["files"] if isinstance(data.get("files"), dict) else data
    return data, container


def conflict_text(conflicts: list[tuple[str, str]]) -> str:
    return ", ".join(f"{path} (held by {holder})" for path, holder in conflicts)


def cmd_claim(ctx: Context, args) -> int:
    owner = args.owner.strip()
    if not owner:
        raise Rejected("--owner is required")
    files = normalize_all(args.files)
    with ctx.locked():
        raw, occupancy = load_occupancy(ctx)
        conflicts = [(f, occupancy[f]) for f in files if occupancy.get(f) not in (None, owner)]
        if conflicts:
            raise Rejected("occupancy conflict: " + conflict_text(conflicts))
        fresh = [f for f in files if f not in occupancy]
        if not fresh:
            print(f"claim: no-op ({len(files)} file(s) already held by {owner})")
            return EXIT_OK
        for path in fresh:
            occupancy[path] = owner
        write_json_atomic(ctx.occupancy, raw)
    print(f"claimed {len(fresh)} file(s) for {owner}")
    return EXIT_OK


def cmd_release(ctx: Context, args) -> int:
    owner = args.owner.strip()
    if not owner:
        raise Rejected("--owner is required")
    files = normalize_all(args.files)
    with ctx.locked():
        raw, occupancy = load_occupancy(ctx)
        conflicts = [(f, occupancy[f]) for f in files if occupancy.get(f) not in (None, owner)]
        if conflicts:
            raise Rejected("cannot release others' claims: " + conflict_text(conflicts))
        removed = [f for f in files if occupancy.get(f) == owner]
        if not removed:
            print("release: no-op")
            return EXIT_OK
        for path in removed:
            del occupancy[path]
        write_json_atomic(ctx.occupancy, raw)
    print(f"released {len(removed)} file(s) for {owner}")
    return EXIT_OK


def status_entries(ctx: Context) -> list[tuple[str, str]]:
    proc = run_git(
        ctx,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        binary=True,
    )
    tokens = proc.stdout.split(b"\0")
    entries: list[tuple[str, str]] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        index += 1
        if not token:
            continue
        status = token[:2].decode("utf-8", "surrogateescape")
        path = token[3:].decode("utf-8", "surrogateescape")
        entries.append((status, path))
        if "R" in status or "C" in status:
            index += 1  # 跳过 rename/copy 的旧路径
    return entries


def numstat_rows(ctx: Context, cached: bool) -> list[tuple[str, str, str]]:
    args = ["diff", "--numstat", "--no-renames"]
    if cached:
        args.append("--cached")
    rows = []
    for line in run_git(ctx, args).stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3:
            rows.append((parts[0], parts[1], "\t".join(parts[2:])))
    return rows


def read_json_file(path: Path, label: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Failure(f"{label} unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Failure(f"{label} is not an object")
    return data


def extract_files(data: dict, *keys: str) -> list[str]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str) and item.strip()]
    return []


def resolve_input(ctx: Context, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else ctx.root / path


def receipt_paths(ctx: Context, value: str) -> list[Path]:
    direct = [resolve_input(ctx, value), ctx.inbox / f"{value}.json", ctx.inbox / value]
    for path in direct:
        if path.is_file():
            return [path]
    if ctx.inbox.is_dir():
        for path in sorted(ctx.inbox.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and data.get("receipt_id") == value:
                return [path]
    raise Rejected(f"receipt not found: {value}")


def task_receipts(ctx: Context, task: str) -> list[dict]:
    if not ctx.inbox.is_dir():
        raise Rejected(f"no receipt found for task {task}")
    receipts = []
    for path in sorted(ctx.inbox.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("task") == task:
            receipts.append(data)
    if not receipts:
        raise Rejected(f"no receipt found for task {task}")
    return receipts


def ticket_declared_files(ctx: Context, value: str) -> list[str]:
    path = resolve_input(ctx, value)
    if not path.is_file():
        raise Rejected(f"ticket not found: {path}")
    files = extract_files(read_json_file(path, "ticket"), "files", "changed_files", "改动文件")
    if not files:
        raise Rejected(f"ticket declares no files: {path}")
    return files


def collect_scope(ctx: Context, args, *, required: bool) -> list[str] | None:
    provided = any(v is not None for v in (args.receipt, args.task, args.ticket))
    if not provided:
        if required:
            raise Rejected("stage requires --receipt/--task/--ticket (declared files)")
        return None
    files: list[str] = []
    if args.receipt:
        for path in receipt_paths(ctx, args.receipt):
            files += extract_files(read_json_file(path, "receipt"), "changed_files")
    if args.task:
        for receipt in task_receipts(ctx, args.task):
            files += extract_files(receipt, "changed_files")
    if args.ticket:
        files += ticket_declared_files(ctx, args.ticket)
    scope = normalize_all(files)
    if required and not scope:
        raise Rejected("declared file list is empty")
    return scope


def changed_paths(entries: list[tuple[str, str]]) -> set[str]:
    return {path for _, path in entries}


def cmd_stage(ctx: Context, args) -> int:
    scope = collect_scope(ctx, args, required=True)
    entries = status_entries(ctx)
    changed = changed_paths(entries)
    outside = sorted(changed - set(scope))
    if outside:
        raise Rejected("out-of-scope changed files: " + ", ".join(outside))
    to_add = [path for path in scope if path in changed]
    if to_add:
        run_git(ctx, ["--literal-pathspecs", "add", "--", *to_add])
    print(f"staged {len(to_add)} file(s)")
    return EXIT_OK


def python_index(root: Path) -> tuple[dict[str, str], dict[str, list[str]]]:
    by_dotted: dict[str, str] = {}
    by_stem: dict[str, list[str]] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py"):
                continue
            rel = Path(dirpath, name).relative_to(root).as_posix()
            by_dotted.setdefault(rel[:-3].replace("/", "."), rel)
            by_stem.setdefault(name[:-3], []).append(rel)
    return by_dotted, by_stem


def resolve_module(module: str, by_dotted: dict, by_stem: dict) -> list[str]:
    if not module:
        return []
    if module in by_dotted:
        return [by_dotted[module]]
    return by_stem.get(module.rsplit(".", 1)[-1], [])[:3]


def import_hints(root: Path, changed_py: list[str]) -> list[tuple[str, str]]:
    by_dotted, by_stem = python_index(root)
    hints: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rel in sorted(changed_py):
        path = root / rel
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules.add(node.module)
                modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        for module in sorted(modules):
            for target in resolve_module(module, by_dotted, by_stem):
                pair = (rel, target)
                if target != rel and pair not in seen:
                    seen.add(pair)
                    hints.append(pair)
    return hints[:HINT_LIMIT]


def numstat_section(ctx: Context, lines: list[str]) -> None:
    found = False
    for label, cached in (("staged", True), ("unstaged", False)):
        rows = numstat_rows(ctx, cached)
        if rows:
            found = True
            lines.append(f"numstat {label}:")
            lines.extend(f"  {added}\t{deleted}\t{path}" for added, deleted, path in rows)
    if not found:
        lines.append("numstat: (no diff)")


def cmd_precommit(ctx: Context, args) -> int:
    scope = collect_scope(ctx, args, required=False)
    entries = status_entries(ctx)
    changed = changed_paths(entries)
    lines: list[str] = []
    numstat_section(ctx, lines)
    lines.append("uncommitted:")
    if entries:
        lines.extend(f"  {status} {path}" for status, path in entries)
    else:
        lines.append("  (clean)")
    if scope is not None:
        outside = sorted(changed - set(scope))
        lines.append("out-of-scope:")
        lines.extend(f"  {path}" for path in outside) or lines.append("  (none)")
    hints = import_hints(ctx.root, sorted(p for p in changed if p.endswith(".py")))
    if hints:
        lines.append("import hints:")
        lines.extend(f"  {source} -> {target}" for source, target in hints)
    print("\n".join(lines))
    return EXIT_OK


def cmd_close(ctx: Context, args) -> int:
    lines: list[str] = []
    numstat_section(ctx, lines)
    status_text = run_git(ctx, ["status", "--short"]).stdout.rstrip()
    lines.append("git status:")
    if status_text:
        lines.extend(f"  {line}" for line in status_text.splitlines())
    else:
        lines.append("  (clean)")
    evidence: list[str] = list(args.evidence)
    if args.receipt:
        for path in receipt_paths(ctx, args.receipt):
            data = read_json_file(path, "receipt")
            evidence += [item for item in data.get("evidence", []) if isinstance(item, str)]
    if args.task:
        for receipt in task_receipts(ctx, args.task):
            evidence += [item for item in receipt.get("evidence", []) if isinstance(item, str)]
    evidence = list(dict.fromkeys(item for item in evidence if item.strip()))
    lines.append("evidence:")
    if evidence:
        lines.extend(f"  - {item}" for item in evidence)
    else:
        lines.append("  (none)")
    if args.ls_remote:
        remote = run_git(ctx, ["ls-remote", "--heads", "origin"]).stdout.rstrip()
        lines.append("ls-remote:")
        if remote:
            lines.extend(f"  {line}" for line in remote.splitlines())
        else:
            lines.append("  (empty)")
    print("\n".join(lines))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="工单占用/暂存/自检/收口（decisions §4）")
    parser.add_argument("--root", help="仓根（含 .git）；默认从 cwd 向上查找")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_scope_args(cmd) -> None:
        group = cmd.add_mutually_exclusive_group(required=False)
        group.add_argument("--receipt", help="回执 id 或路径（changed_files 作为清单）")
        group.add_argument("--task", help="任务 id；取 inbox 中该任务回执的 changed_files 并集")
        group.add_argument("--ticket", help="工单 JSON 路径（files/changed_files/改动文件）")

    claim = sub.add_parser("claim", help="声明文件占用（单写者，冲突即失败）")
    claim.add_argument("--owner", required=True, help="占用者（工位/agent 名）")
    claim.add_argument("files", nargs="+", help="仓库相对路径")
    claim.set_defaults(func=cmd_claim)

    release = sub.add_parser("release", help="释放占用（重复 release 无副作用）")
    release.add_argument("--owner", required=True)
    release.add_argument("files", nargs="+")
    release.set_defaults(func=cmd_release)

    stage = sub.add_parser("stage", help="只暂存声明清单内的文件；有清单外改动即拒绝")
    add_scope_args(stage)
    stage.set_defaults(func=cmd_stage)

    precommit = sub.add_parser("precommit", help="numstat/未提交/越界/import 提示（不阻断）")
    add_scope_args(precommit)
    precommit.set_defaults(func=cmd_precommit)

    close = sub.add_parser("close", help="收口核验：numstat + status + 证据 + 可选 ls-remote")
    add_scope_args(close)
    close.add_argument("--evidence", action="append", default=[], help="补充证据（可重复）")
    close.add_argument("--ls-remote", action="store_true", help="附加 git ls-remote --heads origin")
    close.set_defaults(func=cmd_close)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ctx = Context(resolve_root(args.root))
    try:
        return args.func(ctx, args)
    except Rejected as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_REJECT
    except Failure as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
