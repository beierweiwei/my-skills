#!/usr/bin/env python3
"""技能资产静态校验：行数预算 / 规则编号引用 / 指针写法 / 跨文档重复句 / 纯净度。

检查项：
  1. budget    行数预算：
               templates/AGENTS-编排节.md ≤150；qoder/opencode helm/firstmate ≤80；
               qoder/opencode worker（builder/planner/reviewer/runner/scout）≤50；
               工单模板 ≤15。
  2. rule-ref  模板/角色里引用的 D/R/W/O/F/P/G/S/X 与 A 编号必须在
               references/crew-rules.md / crew-acceptance.md 中有定义；
               区间（如 D2–D6、A1–A5）逐号校验，含端点与中间号。
  3. pointer   角色模板（templates/agents/**）禁止裸写 references/crew-rules.md /
               crew-acceptance.md，必须使用 {{crew_rules}} / {{crew_acceptance}} token；
               AGENTS 受管块模板相反：只写技能根相对名，禁止
               {{skill_root}} / {{crew_rules}} / {{crew_acceptance}} 等绝对路径 token，
               且必须含技能根锚点 .planning/orchestration.json + skillRoot（防回退）。
  4. duplicate 跨文档按归一化整行比对，≥30 字的重复行报出；
               排除标题、表格分隔、代码块（含围栏与围栏内内容）。
               角色文件之间共用同构骨架（qoder/opencode 镜像与公共段落）不互比；
               角色/模板与文档正文之间的重复仍报出。
  5. purity    --purity：templates/、references/、scripts/ 零绝对路径
               （家目录与 Windows 盘符）；项目名/业务词来自维护者本地未跟踪
               denylist `<root>/purity.denylist`（每行一个词，# 注释），
               denylist 不随技能发布。scripts/tests/ 与 __pycache__ 不扫描。

输出：每个违规一行 `<文件>:<行号>: <规则>: <说明>`；全绿只输出一行摘要。
退出码：0 全绿；1 有违规；3 根目录/规则正文缺失或 SKILL.md 无法解析。
--version 打印 SKILL.md frontmatter 的 version 后退出。

用法：
  python3 scripts/crew-lint.py                 # 校验技能根（脚本所在目录的上一级）
  python3 scripts/crew-lint.py --root <技能根>
  python3 scripts/crew-lint.py --root . --purity   # 只跑纯净度检查
  python3 scripts/crew-lint.py --version       # 输出 SKILL.md 版本
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

SCRIPT_ROOT = Path(__file__).resolve().parent.parent
ROLE_PREFIX = "templates/agents/"

BUDGETS: tuple[tuple[str, int], ...] = (
    ("templates/AGENTS-编排节.md", 150),
    ("templates/agents/qoder/helm.md", 80),
    ("templates/agents/qoder/firstmate.md", 80),
    ("templates/agents/qoder/builder.md", 50),
    ("templates/agents/qoder/planner.md", 50),
    ("templates/agents/qoder/reviewer.md", 50),
    ("templates/agents/qoder/runner.md", 50),
    ("templates/agents/qoder/scout.md", 50),
    ("templates/agents/opencode/helm.md", 80),
    ("templates/agents/opencode/firstmate.md", 80),
    ("templates/agents/opencode/builder.md", 50),
    ("templates/agents/opencode/planner.md", 50),
    ("templates/agents/opencode/reviewer.md", 50),
    ("templates/agents/opencode/runner.md", 50),
    ("templates/agents/opencode/scout.md", 50),
    ("templates/_templates/ticket.md", 15),
)
TEMPLATE_FILES = tuple(rel for rel, _ in BUDGETS)
EXTRA_DOCS = ("references/crew-rules.md", "references/crew-acceptance.md",
              "references/verification-redlines.md", "SKILL.md")
RULES_FILE = "references/crew-rules.md"
ACCEPTANCE_FILE = "references/crew-acceptance.md"

RANGE_RE = re.compile(
    r"([ADRWOFPGSX])(\d+)(?:\.(\d+))?\s*[–—\-]\s*([ADRWOFPGSX])?(\d+)(?:\.(\d+))?"
)
SINGLE_RE = re.compile(r"(?<![A-Za-z0-9])([ADRWOFPGSX])(\d+)(?:\.(\d+))?(?![A-Za-z0-9])")
BARE_POINTER_RE = re.compile(r"references/crew-(?:rules|acceptance)\.md")
AGENTS_TEMPLATE = "templates/AGENTS-编排节.md"
AGENTS_ABS_TOKEN_RE = re.compile(r"\{\{(?:skill_root|crew_rules|crew_acceptance)\}\}")
SKILL_ROOT_ANCHORS = (".planning/orchestration.json", "skillRoot")
BOLD_ID_RE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*([DRWOFPGSX]\d+(?:\.\d+)?)\s", re.MULTILINE)
A_HEADING_RE = re.compile(r"^#{1,6}\s+(A\d+(?:\.\d+)?)\s", re.MULTILINE)
A_BOLD_RE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*(A\d+(?:\.\d+)?)\s", re.MULTILINE)


class Failure(Exception):
    """根目录或规则正文不可用。"""


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise Failure(f"cannot read {path}: {exc}") from exc


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_role(rel: str) -> bool:
    return rel.startswith(ROLE_PREFIX)


def parse_defined_ids(root: Path) -> tuple[set[str], set[str]]:
    rules_path = root / RULES_FILE
    acceptance_path = root / ACCEPTANCE_FILE
    if not rules_path.is_file() or not acceptance_path.is_file():
        missing = [str(p) for p in (rules_path, acceptance_path) if not p.is_file()]
        raise Failure("missing rule source: " + ", ".join(missing))
    rules = set(BOLD_ID_RE.findall(read_text(rules_path)))
    acceptance = set(A_HEADING_RE.findall(read_text(acceptance_path)))
    acceptance.update(A_BOLD_RE.findall(read_text(acceptance_path)))
    return rules, acceptance


def check_budget(root: Path, issues: list[tuple[str, int, str, str]]) -> None:
    for rel, limit in BUDGETS:
        path = root / rel
        if not path.is_file():
            issues.append((rel, 1, "budget", f"missing required file (limit {limit})"))
            continue
        count = len(read_text(path).splitlines())
        if count > limit:
            issues.append((rel, limit + 1, "budget", f"{count} lines > {limit}"))


def expand_range(match: re.Match) -> tuple[list[str], str | None]:
    prefix, start_num, start_sub, end_prefix, end_num, end_sub = match.groups()
    if end_prefix and end_prefix != prefix:
        return [], f"range prefix mismatch: {prefix} vs {end_prefix}"
    start = int(start_num)
    end = int(end_num)
    start_s = int(start_sub) if start_sub else None
    end_s = int(end_sub) if end_sub else start_s
    if start_s is not None and end_s is not None and start == end:
        if end_s < start_s:
            return [], f"descending range {prefix}{start}.{start_s}-{prefix}{end}.{end_s}"
        return [f"{prefix}{start}.{i}" for i in range(start_s, end_s + 1)], None
    if start_s is None and end_s is None:
        if end < start:
            return [], f"descending range {prefix}{start}-{prefix}{end}"
        return [f"{prefix}{i}" for i in range(start, end + 1)], None
    # 跨基数/混合子号：只校验端点
    first = f"{prefix}{start}" + (f".{start_s}" if start_s is not None else "")
    last = f"{prefix}{end}" + (f".{end_s}" if end_s is not None else "")
    return [first, last], None


def check_rule_refs(
    root: Path,
    rules: set[str],
    acceptance: set[str],
    issues: list[tuple[str, int, str, str]],
) -> None:
    for rel in TEMPLATE_FILES:
        path = root / rel
        if not path.is_file():
            continue
        in_code = False
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_code = not in_code
                continue
            if in_code:
                continue
            referenced: dict[str, str] = {}
            for match in RANGE_RE.finditer(line):
                ids, error = expand_range(match)
                if error:
                    issues.append((rel, lineno, "rule-ref", error))
                    continue
                for ident in ids:
                    referenced.setdefault(ident, match.group(0).strip())
            for match in SINGLE_RE.finditer(line):
                ident = match.group(1) + match.group(2)
                if match.group(3):
                    ident += "." + match.group(3)
                referenced.setdefault(ident, ident)
            for ident in sorted(referenced):
                pool = acceptance if ident.startswith("A") else rules
                if ident not in pool:
                    source = ACCEPTANCE_FILE if ident.startswith("A") else RULES_FILE
                    issues.append(
                        (rel, lineno, "rule-ref", f"{ident} not defined in {source}")
                    )


def check_pointers(root: Path, issues: list[tuple[str, int, str, str]]) -> None:
    for rel in TEMPLATE_FILES:
        path = root / rel
        if not path.is_file():
            continue
        text = read_text(path)
        for lineno, line in enumerate(text.splitlines(), 1):
            if is_role(rel):
                if BARE_POINTER_RE.search(line):
                    issues.append(
                        (
                            rel,
                            lineno,
                            "pointer",
                            "bare references path in role template; "
                            "use {{crew_rules}} / {{crew_acceptance}}",
                        )
                    )
            elif rel == AGENTS_TEMPLATE:
                match = AGENTS_ABS_TOKEN_RE.search(line)
                if match:
                    issues.append(
                        (
                            rel,
                            lineno,
                            "pointer",
                            f"absolute skill token {match.group(0)} in AGENTS block; "
                            "use skill-root-relative names",
                        )
                    )
        if rel == AGENTS_TEMPLATE:
            for anchor in SKILL_ROOT_ANCHORS:
                if anchor not in text:
                    issues.append(
                        (
                            rel,
                            1,
                            "pointer",
                            f"missing skill root anchor {anchor}; keep "
                            ".planning/orchestration.json + skillRoot in AGENTS block",
                        )
                    )


def normalize_line(line: str) -> str:
    text = unicodedata.normalize("NFKC", line.strip())
    text = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+|>\s+)+", "", text)
    return re.sub(r"\s+", " ", text).strip()


def check_duplicates(root: Path, issues: list[tuple[str, int, str, str]]) -> None:
    docs = [rel for rel in (*TEMPLATE_FILES, *EXTRA_DOCS) if (root / rel).is_file()]
    occurrences: dict[str, list[tuple[str, int]]] = {}
    for rel in docs:
        in_code = False
        for lineno, line in enumerate(read_text(root / rel).splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                in_code = not in_code
                continue
            if in_code or not stripped or stripped.startswith("#"):
                continue
            if re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
                continue  # 表格分隔线
            normalized = normalize_line(line)
            if len(normalized) < 30:
                continue
            occurrences.setdefault(normalized, []).append((rel, lineno))
    for locations in occurrences.values():
        files = {rel for rel, _ in locations}
        if len(files) < 2:
            continue
        if all(is_role(rel) for rel in files):
            continue  # 角色文件之间共用同构骨架，不互比
        anchor_rel, anchor_line = locations[-1]
        others = ", ".join(
            f"{rel}:{lineno}" for rel, lineno in locations[:-1][:5]
        )
        issues.append(
            (
                anchor_rel,
                anchor_line,
                "duplicate",
                f"identical line (>=30 chars) also at {others}",
            )
        )


def skill_version(root: Path) -> str:
    path = root / "SKILL.md"
    if not path.is_file():
        raise Failure(f"missing {path}")
    lines = read_text(path).splitlines()
    if not lines or lines[0].strip() != "---":
        raise Failure("SKILL.md missing frontmatter")
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.match(r"version:\s*([^\s#]+)", line)
        if match:
            return match.group(1)
    raise Failure("SKILL.md frontmatter missing version")


PURITY_DIRS = ("templates", "references", "scripts")
PURITY_SKIP = {"__pycache__", "tests"}
PURITY_SUFFIXES = {".md", ".py", ".sh", ".json", ".txt"}
_HOME_SEGMENTS = ("home", "Users", "root")
ABS_HOME_RE = re.compile(
    r"(?<![\w.-])/(?:" + "|".join(_HOME_SEGMENTS) + r")/[A-Za-z0-9._-]+"
)
ABS_DRIVE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/][^\s\"'`)]*")
DENYLIST_NAME = "purity.denylist"


def purity_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for name in PURITY_DIRS:
        base = root / name
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in PURITY_SUFFIXES:
                continue
            parts = path.relative_to(root).parts
            if any(part in PURITY_SKIP for part in parts):
                continue
            files.append(path)
    return files


def load_denylist(root: Path) -> list[str]:
    path = root / DENYLIST_NAME
    if not path.is_file():
        return []
    terms: list[str] = []
    for line in read_text(path).splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.append(term)
    return terms


def check_purity(root: Path) -> list[tuple[str, int, str, str]]:
    issues: list[tuple[str, int, str, str]] = []
    terms = [(term, term.casefold()) for term in load_denylist(root)]
    for path in purity_files(root):
        rel = rel_posix(path, root)
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            if ABS_HOME_RE.search(line) or ABS_DRIVE_RE.search(line):
                issues.append((rel, lineno, "purity", "absolute path in skill asset"))
            for term, folded in terms:
                if folded in line.casefold():
                    issues.append(
                        (rel, lineno, "purity", f"forbidden term {term!r} (denylist)")
                    )
    return issues


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="技能资产静态校验：行数预算/规则编号/指针写法/跨文档重复句/纯净度"
    )
    parser.add_argument("--root", help="技能根目录；默认脚本所在目录的上一级")
    parser.add_argument("--version", action="store_true", help="打印 SKILL.md frontmatter 版本")
    parser.add_argument(
        "--purity",
        action="store_true",
        help="只跑纯净度检查（绝对路径 + <root>/purity.denylist 本地词表）",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve() if args.root else SCRIPT_ROOT
    try:
        if args.version:
            print(skill_version(root))
            return 0
        if args.purity:
            purity_issues = check_purity(root)
            if purity_issues:
                for rel, lineno, rule, message in sorted(purity_issues):
                    print(f"{rel}:{lineno}: {rule}: {message}")
                print(f"crew-lint: {len(purity_issues)} purity issue(s)")
                return 1
            print(f"crew-lint: purity ok ({len(purity_files(root))} files)")
            return 0
        rules, acceptance = parse_defined_ids(root)
    except Failure as exc:
        print(f"crew-lint: {exc}", file=sys.stderr)
        return 3

    issues: list[tuple[str, int, str, str]] = []
    check_budget(root, issues)
    check_rule_refs(root, rules, acceptance, issues)
    check_pointers(root, issues)
    check_duplicates(root, issues)

    if issues:
        for rel, lineno, rule, message in sorted(issues):
            print(f"{rel}:{lineno}: {rule}: {message}")
        print(f"crew-lint: {len(issues)} issue(s)")
        return 1

    checked = {
        rel for rel in (*TEMPLATE_FILES, *EXTRA_DOCS) if (root / rel).is_file()
    }
    print(f"crew-lint: ok ({len(checked)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
