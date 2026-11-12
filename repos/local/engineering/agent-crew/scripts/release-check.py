#!/usr/bin/env python3
"""release-check.py — 发布就绪聚合检查（只读，pass/fail/skip 表 + 统一退出码）。

把发布剧本 Phase 12–17 里人工逐条跑的检查汇总为一张表（口径写死在此处）：

- `version.source`：技能源 `SKILL.md` frontmatter `version`、`SKILL.md` 标题版本、
  `CHANGELOG.md` 最新版本、`scripts/init.py` 的 `SKILL_VERSION_FALLBACK` 四处一致；
- `version.project`：项目 `.planning/orchestration.json` 的 `skillVersion` 与源版本一致；
- `version.installed`：用户已装副本（配置 `skillRoot`）的 `SKILL.md` 版本与源版本一致；
- `install.source_user`：技能源树 vs 用户已装副本逐文件字节比较，排除
  `__pycache__`/`.pytest_cache`/`.git` 目录与 `*.pyc`/`*.pyo`；
- `install.project_source`：项目受管脚本 vs 技能源，复用 `upgrade.diff_report`
  的 `sha256_text`（CRLF 归一化 + 补尾换行）口径；源侧缺清单脚本单独报出（会阻断 upgrade）；
- `upgrade`：manifest `skillVersion` 与源版本一致，且受管脚本无待升级项；
- `init`：项目 `scripts/init.py --check`（rc=0 pass；rc=1 fail；rc=2 未安装/待初始化 skip）；
- `doctor`：项目 `scripts/doctor.py --profile release --strict --json`，`summary.fail == 0`
  即 pass（与发布剧本「判定以 fail=0 为准」一致，warn 不阻断）；
- `source.git-clean`：技能源工作区（scoped 到源目录）与 my-skills 仓
  `git status --porcelain` 非空 → fail；my-skills 按 `CREW_MY_SKILLS_ROOT` 或技能源
  git-common-dir 宿主工作区定位，定位不到 → skip 注明；
- `guard.lock`：`<crew-dir>/guard.lock` 的 pid 存活；
- `guard.helm`：运行中 guard 的 `--helm`（`/proc/<pid>/cmdline`）与现役舵一致——
  现役舵取 dispatch registry 最新 helm 条目（按 `startedAt`），无条目回落配置
  `helm.agentName`；进程/名字不可解析 → skip 注明（2.2.15 收口教训：guard 指向
  旧舵名靠人工发现）；
- `guard.doctor`：`doctor --only runtime.guard` 结果；发布期要求 pass，warn 按 fail。

`--dry-run` 是默认且唯一模式：本脚本不写仓、不重启服务；子进程只跑只读命令
（`init --check`、`doctor`）。`--json` 输出结构化结果，便于后续接入。

退出码：0 全 pass（skip 不阻断但会显示）；1 有 fail；2 参数/用法错误；3 环境错误
（root 不存在、显式 `--source` 不可用、crew 目录无法解析）。
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True  # 只读检查：不在源/项目 scripts 留 __pycache__

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import crew_paths  # noqa: E402
import upgrade as upgrade_lib  # noqa: E402

CONFIG_REL = Path(".planning/orchestration.json")
MANIFEST_REL = Path("docs/crew/manifest.json")
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDED_SUFFIXES = (".pyc", ".pyo")
MY_SKILLS_ENV = "CREW_MY_SKILLS_ROOT"
CHANGELOG_VERSION_RE = re.compile(r"^##\s+(\d+\.\d+\.\d+)", re.MULTILINE)
TITLE_VERSION_RE = re.compile(r"（v(\d+\.\d+\.\d+)）")
FALLBACK_VERSION_RE = re.compile(
    r"^SKILL_VERSION_FALLBACK\s*=\s*[\"']([^\"']+)[\"']", re.MULTILINE)
PASS, FAIL, SKIP = "pass", "fail", "skip"


@dataclass
class Check:
    id: str
    status: str
    value: str
    next: str = "-"


@dataclass
class Context:
    root: Path
    source: Path | None
    source_note: str
    crew: Path
    timeout: int
    config: dict | None = None
    config_error: str = ""
    _source_version: str | None = field(default=None, repr=False)

    def source_version(self) -> str | None:
        if self._source_version is None and self.source is not None:
            text = read_text(self.source / "SKILL.md")
            self._source_version = (frontmatter_version(text) if text else None) or ""
        return self._source_version or None

    def guard_start(self) -> str:
        helm, _note = resolve_active_helm(self)
        return (f"python3 scripts/crew-guard.py --helm "
                f"{helm or '<当前舵>'} --interval 120")


# ---------------------------------------------------------------------------
# 读取与解析
# ---------------------------------------------------------------------------

def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def frontmatter_version(text: str) -> str | None:
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end == -1:
        return None
    match = re.search(r"^version:\s*(.+?)\s*$", text[3:end], re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip().strip("'\"") or None


def load_config(root: Path) -> tuple[dict | None, str]:
    path = root / CONFIG_REL
    if not path.is_file():
        return None, f"缺少配置：{CONFIG_REL.as_posix()}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"配置非法：{exc}"
    if not isinstance(data, dict):
        return None, "配置必须是 JSON 对象"
    return data, ""


def load_manifest(root: Path) -> tuple[dict | None, str]:
    path = root / MANIFEST_REL
    if not path.is_file():
        return None, f"缺少 manifest：{MANIFEST_REL.as_posix()}"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"manifest 非法：{exc}"
    if not isinstance(data, dict):
        return None, "manifest 必须是 JSON 对象"
    return data, ""


def skill_root_of(root: Path, config: dict | None) -> Path | None:
    raw = (config or {}).get("skillRoot")
    if not isinstance(raw, str) or not raw.strip():
        return None
    path = Path(raw.strip()).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    return path


def resolve_source(root: Path, explicit: str | None) -> tuple[Path | None, str]:
    """源解析：显式 --source 优先；其次脚本自身所在技能副本；最后配置 skillRoot。"""
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        if not candidate.is_dir():
            raise FileNotFoundError(f"技能源不存在：{candidate}")
        if not (candidate / "SKILL.md").is_file():
            raise FileNotFoundError(f"技能源缺 SKILL.md：{candidate}")
        return candidate, "显式 --source"
    own = SCRIPT_DIR.parent
    if own != root and (own / "SKILL.md").is_file() and (own / "scripts").is_dir():
        return own, "脚本自身所在技能副本"
    config, _ = load_config(root)
    skill_root = skill_root_of(root, config)
    if skill_root is not None and skill_root.is_dir():
        return skill_root, "配置 skillRoot（无独立开发源）"
    return None, "不可用（无 --source 且配置 skillRoot 不可读）"


def tree_files(base: Path) -> dict[str, bytes]:
    """递归收集文件内容：排除缓存目录与字节码后缀；读失败记空字节（按差异处理）。"""
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_DIRS)
        for name in sorted(filenames):
            if name.endswith(EXCLUDED_SUFFIXES):
                continue
            path = Path(dirpath) / name
            rel = path.relative_to(base).as_posix()
            try:
                files[rel] = path.read_bytes()
            except OSError:
                files[rel] = b"<unreadable>"
    return files


def preview(names: list[str], limit: int = 5) -> str:
    out = "、".join(names[:limit])
    return out + ("…" if len(names) > limit else "")


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def lock_pid(text: str) -> int | None:
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        data = None
    if isinstance(data, dict):
        try:
            return int(data.get("pid"))
        except (ValueError, TypeError):
            return None
    try:
        return int(text.strip().split()[0])
    except (ValueError, IndexError):
        return None


def load_dispatch_registry(crew: Path) -> dict:
    """读取 `<crew-dir>/dispatch-agents.json`；缺失/损坏返回空表（fail-soft）。"""
    path = crew / "dispatch-agents.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_active_helm(ctx: Context) -> tuple[str | None, str]:
    """解析现役舵：dispatch registry 最新 helm 条目 → 配置 helm.agentName。

    返回 (舵名, 口径说明)；都不可用返回 (None, 说明)。
    """
    registry = load_dispatch_registry(ctx.crew)
    entries: list[tuple[str, str]] = []
    for name, info in registry.items():
        if not isinstance(info, dict) or info.get("role") != "helm":
            continue
        started_at = str(info.get("startedAt") or "").strip()
        if started_at:
            entries.append((started_at, str(name)))
    if entries:
        started_at, name = max(entries)
        return name, f"dispatch registry 最新 helm（startedAt={started_at}）"
    helm = (ctx.config or {}).get("helm")
    name = str(helm.get("agentName") or "").strip() if isinstance(helm, dict) else ""
    if name:
        return name, "配置 helm.agentName（registry 无 helm 条目）"
    return None, "现役舵不可解析（registry 无 helm 条目且配置缺 helm.agentName）"


def running_guard_helm(pid: int) -> str | None:
    """从 `/proc/<pid>/cmdline` 取运行中 guard 的 `--helm` 值；不是 crew-guard
    进程/不可读/缺参数时返回 None（调用方按 skip 处理）。"""
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    parts = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
    if not any(Path(part).name == "crew-guard.py" for part in parts):
        return None
    try:
        index = parts.index("--helm")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    return parts[index + 1].strip() or None


def git_status(ctx: Context, path: Path, scoped: bool) -> tuple[str | None, str]:
    """`git status --porcelain` 输出；非 git 仓/git 不可用返回 (None, 原因)。"""
    args = ["git", "-C", str(path), "status", "--porcelain"]
    if scoped:
        args += ["--", "."]
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False,
                              timeout=ctx.timeout, encoding="utf-8", errors="replace")
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"git 不可用：{exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return None, f"非 git 工作区或 git 失败（rc={proc.returncode}）：{detail[0] if detail else ''}"
    return proc.stdout, ""


def resolve_my_skills(ctx: Context) -> tuple[Path | None, str]:
    """定位 my-skills 仓：环境变量 → 技能源 git-common-dir 宿主工作区 → 同级目录。"""
    env = os.environ.get(MY_SKILLS_ENV)
    if env:
        path = Path(env).expanduser()
        if path.is_dir():
            return path, f"{MY_SKILLS_ENV}={path}"
        return None, f"{MY_SKILLS_ENV}={path} 不是目录"
    if ctx.source is not None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(ctx.source), "rev-parse", "--git-common-dir"],
                capture_output=True, text=True, check=False, timeout=ctx.timeout,
                encoding="utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError):
            proc = None
        if proc is not None and proc.returncode == 0:
            common = Path(proc.stdout.strip())
            if not common.is_absolute():
                common = (ctx.source / common).resolve()
            host = common.parent
            try:
                same_as_source = host.resolve() == ctx.source.resolve()
            except (OSError, RuntimeError):
                same_as_source = True
            if not same_as_source and host.is_dir() and (host / ".git").exists():
                return host, "技能源 git-common-dir 的宿主工作区"
    for base in (ctx.source, ctx.root):
        if base is None:
            continue
        for ancestor in [base, *base.parents][:8]:
            candidate = ancestor / "my-skills"
            if candidate.is_dir() and (candidate / ".git").exists():
                return candidate, "同级 my-skills 目录"
    return None, f"无法定位 my-skills（可用 {MY_SKILLS_ENV} 指定）"


def run_script(script: Path, args: list[str], cwd: Path,
               timeout: int) -> tuple[int | None, str, str]:
    if not script.is_file():
        return None, "", ""
    try:
        proc = subprocess.run(
            [sys.executable, str(script), *args],
            cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return None, "", f"超时（>{timeout}s）"
    except OSError as exc:
        return None, "", str(exc)


def last_line(text: str) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return lines[-1] if lines else ""


# ---------------------------------------------------------------------------
# 检查项
# ---------------------------------------------------------------------------

def check_version_source(ctx: Context) -> Check:
    if ctx.source is None:
        return Check("version.source", SKIP, ctx.source_note,
                     "传 --source <技能源目录> 或修正配置 skillRoot")
    skill_md = read_text(ctx.source / "SKILL.md")
    if skill_md is None:
        return Check("version.source", FAIL, f"源缺 SKILL.md：{ctx.source}",
                     "修正技能源路径")
    front = frontmatter_version(skill_md) or ""
    title_match = TITLE_VERSION_RE.search(skill_md)
    changelog = read_text(ctx.source / "CHANGELOG.md") or ""
    changelog_match = CHANGELOG_VERSION_RE.search(changelog)
    init_py = read_text(ctx.source / "scripts/init.py") or ""
    fallback_match = FALLBACK_VERSION_RE.search(init_py)
    versions = [
        ("SKILL.md", front),
        ("标题", title_match.group(1) if title_match else ""),
        ("CHANGELOG", changelog_match.group(1) if changelog_match else ""),
        ("init fallback", fallback_match.group(1) if fallback_match else ""),
    ]
    if not front:
        return Check("version.source", FAIL,
                     "源 SKILL.md 缺 frontmatter version",
                     "补 SKILL.md version 后重跑")
    bad = [f"{label}={value or '未识别'}" for label, value in versions
           if value != front]
    if bad:
        return Check("version.source", FAIL,
                     f"SKILL.md={front}；不一致：{'，'.join(bad)}",
                     "对齐 SKILL.md/标题/CHANGELOG.md/init.py fallback 后重跑")
    return Check("version.source", PASS,
                 "；".join(f"{label}={value}" for label, value in versions))


def check_version_project(ctx: Context) -> Check:
    if ctx.config is None:
        status = SKIP if ctx.config_error.startswith("缺少") else FAIL
        return Check("version.project", status, ctx.config_error,
                     "运行 python3 scripts/init.py")
    project_version = ctx.config.get("skillVersion")
    if not isinstance(project_version, str) or not project_version:
        return Check("version.project", FAIL, "配置缺 skillVersion",
                     "修正 .planning/orchestration.json 后重跑 upgrade")
    source_version = ctx.source_version()
    if not source_version:
        return Check("version.project", SKIP, "源版本不可读，无法比较",
                     "传 --source <技能源目录> 后重跑")
    if project_version != source_version:
        return Check("version.project", FAIL,
                     f"项目 skillVersion={project_version} != 源 {source_version}",
                     "python3 scripts/upgrade.py --apply --confirm")
    return Check("version.project", PASS,
                 f"项目 skillVersion=源版本={source_version}")


def check_version_installed(ctx: Context) -> Check:
    if ctx.config is None:
        return Check("version.installed", SKIP, ctx.config_error,
                     "运行 python3 scripts/init.py")
    installed = skill_root_of(ctx.root, ctx.config)
    if installed is None:
        return Check("version.installed", SKIP, "配置缺 skillRoot",
                     "修正 .planning/orchestration.json 的 skillRoot")
    if not installed.is_dir():
        return Check("version.installed", SKIP, f"用户目录不可读：{installed}",
                     "bash update-all.sh --skill agent-crew --agent qoder-cn,claude")
    text = read_text(installed / "SKILL.md")
    if text is None:
        return Check("version.installed", SKIP,
                     f"已装副本缺 SKILL.md：{installed}",
                     "bash update-all.sh --skill agent-crew --agent qoder-cn,claude")
    installed_version = frontmatter_version(text) or ""
    if not installed_version:
        return Check("version.installed", SKIP,
                     f"已装 SKILL.md 版本不可读：{installed}")
    if ctx.source == installed:
        return Check("version.installed", PASS,
                     f"同源（skillRoot={installed_version}），无独立开发源可对照")
    source_version = ctx.source_version()
    if not source_version:
        return Check("version.installed", SKIP, "源版本不可读，无法比较")
    if installed_version != source_version:
        return Check("version.installed", FAIL,
                     f"已装={installed_version} != 源 {source_version}（{installed}）",
                     "bash update-all.sh --skill agent-crew --agent qoder-cn,claude")
    return Check("version.installed", PASS,
                 f"已装=源={installed_version}（{installed}）")


def check_source_git_clean(ctx: Context) -> Check:
    """技能源与 my-skills 工作区必须干净（2.2.15 漏检：dirty-but-copied 不可见）。"""
    problems: list[str] = []
    notes: list[str] = []
    checked = 0
    if ctx.source is None:
        notes.append(f"技能源不可用（{ctx.source_note}）")
    else:
        output, error = git_status(ctx, ctx.source, scoped=True)
        if output is None:
            notes.append(f"技能源 {error}")
        else:
            checked += 1
            dirty = [line for line in output.splitlines() if line.strip()]
            if dirty:
                names = [line[3:].strip() if len(line) > 3 else line for line in dirty]
                problems.append(f"技能源未提交/未跟踪 {len(dirty)} 项（{preview(names, 3)}）")
    my_skills, note = resolve_my_skills(ctx)
    if my_skills is None:
        notes.append(note)
    else:
        output, error = git_status(ctx, my_skills, scoped=False)
        if output is None:
            notes.append(f"my-skills（{note}）{error}")
        else:
            checked += 1
            dirty = [line for line in output.splitlines() if line.strip()]
            if dirty:
                names = [line[3:].strip() if len(line) > 3 else line for line in dirty]
                problems.append(
                    f"my-skills 未提交/未跟踪 {len(dirty)} 项（{preview(names, 3)}）")
    if problems:
        return Check("source.git-clean", FAIL,
                     "；".join(problems) + ("；" + "；".join(notes) if notes else ""),
                     "提交或还原对应仓改动后重跑（发布前允许在途 fail，由提交步骤消除）")
    if my_skills is None:
        return Check("source.git-clean", SKIP,
                     "；".join(["技能源工作区干净" if checked else "技能源不可检查", *notes]),
                     f"用 {MY_SKILLS_ENV} 指定 my-skills 仓后复跑")
    if not checked:
        return Check("source.git-clean", SKIP, "；".join(notes) or "无可检查的 git 工作区")
    detail = "技能源与 my-skills 工作区均干净" if checked == 2 else "已检查工作区干净"
    return Check("source.git-clean", PASS, "；".join([detail, *notes]))


def check_install_source_user(ctx: Context) -> Check:
    if ctx.config is None:
        return Check("install.source_user", SKIP, ctx.config_error,
                     "运行 python3 scripts/init.py")
    installed = skill_root_of(ctx.root, ctx.config)
    if installed is None:
        return Check("install.source_user", SKIP, "配置缺 skillRoot")
    if ctx.source is None:
        return Check("install.source_user", SKIP, ctx.source_note,
                     "传 --source <技能源目录> 后重跑")
    if not installed.is_dir():
        return Check("install.source_user", SKIP, f"用户目录不可读：{installed}",
                     "bash update-all.sh --skill agent-crew --agent qoder-cn,claude")
    if ctx.source == installed:
        return Check("install.source_user", PASS,
                     "同源（skillRoot=脚本来源），无独立开发源可对照")
    source_files = tree_files(ctx.source)
    installed_files = tree_files(installed)
    common = sorted(set(source_files) & set(installed_files))
    changed = [rel for rel in common if source_files[rel] != installed_files[rel]]
    missing = sorted(set(source_files) - set(installed_files))
    extra = sorted(set(installed_files) - set(source_files))
    scope = ("排除 __pycache__/.pytest_cache/.git/*.pyc；"
             f"源 {len(source_files)} 文件")
    if changed or missing or extra:
        detail = [f"changed={len(changed)}", f"missing={len(missing)}",
                  f"extra={len(extra)}"]
        names = changed + missing + extra
        return Check("install.source_user", FAIL,
                     f"源 vs 用户副本不一致：{' '.join(detail)}"
                     f"（{preview(names)}）；{scope}",
                     "bash update-all.sh --skill agent-crew --agent qoder-cn,claude")
    return Check("install.source_user", PASS,
                 f"源 vs 用户副本逐文件一致（{scope}）")


def upgrade_diff(ctx: Context, manifest: dict) -> tuple[list[str], list[str], int]:
    """复用 upgrade.diff_report：返回 (changed, source_missing, 对比项数)。"""
    source_names = upgrade_lib.source_script_manifest(ctx.source)
    source_missing = upgrade_lib.missing_source_scripts(ctx.source, source_names)
    report = upgrade_lib.diff_report(
        ctx.root, manifest, ctx.source_version() or "", ctx.source)
    return list(report["changed"]), source_missing, len(report["scripts"])


def check_install_project_source(ctx: Context) -> Check:
    if ctx.source is None:
        return Check("install.project_source", SKIP, ctx.source_note,
                     "传 --source <技能源目录> 后重跑")
    manifest, error = load_manifest(ctx.root)
    if manifest is None:
        status = SKIP if error.startswith("缺少") else FAIL
        return Check("install.project_source", status, error,
                     "运行 python3 scripts/init.py")
    try:
        changed, source_missing, compared = upgrade_diff(ctx, manifest)
    except Exception as exc:  # noqa: BLE001 - 单检查异常不得打崩聚合器
        return Check("install.project_source", FAIL,
                     f"读取 upgrade 口径失败：{type(exc).__name__}: {exc}",
                     "检查 scripts/upgrade.py 后重跑")
    problems: list[str] = []
    if source_missing:
        problems.append(f"源缺失受管脚本 {len(source_missing)} 项"
                        f"（{preview(source_missing)}）")
    if changed:
        problems.append(f"项目与源不一致 {len(changed)}/{compared} 项"
                        f"（{preview(changed)}）")
    if problems:
        return Check("install.project_source", FAIL,
                     "；".join(problems) + "（sha256_text 口径）",
                     "python3 scripts/upgrade.py --apply --confirm"
                     "（源新增受管脚本时可能需再跑一次）")
    return Check("install.project_source", PASS,
                 f"受管脚本与源一致（{compared} 项，sha256_text 口径）")


def check_upgrade(ctx: Context) -> Check:
    manifest, error = load_manifest(ctx.root)
    if manifest is None:
        status = SKIP if error.startswith("缺少") else FAIL
        return Check("upgrade", status, error, "运行 python3 scripts/init.py")
    if ctx.source is None:
        return Check("upgrade", SKIP, ctx.source_note,
                     "传 --source <技能源目录> 后重跑")
    source_version = ctx.source_version() or ""
    installed_version = manifest.get("skillVersion")
    try:
        changed, _, _ = upgrade_diff(ctx, manifest)
    except Exception as exc:  # noqa: BLE001
        return Check("upgrade", FAIL,
                     f"读取 upgrade 口径失败：{type(exc).__name__}: {exc}",
                     "检查 scripts/upgrade.py 后重跑")
    problems: list[str] = []
    if changed:
        problems.append(f"待升级 {len(changed)} 项（{preview(changed)}）")
    if installed_version != source_version:
        problems.append(f"manifest skillVersion={installed_version} != 源 {source_version}")
    if problems:
        return Check("upgrade", FAIL, "；".join(problems),
                     "python3 scripts/upgrade.py --apply --confirm")
    return Check("upgrade", PASS,
                 f"已是最新（manifest=源={source_version}，无待升级脚本）")


def check_init(ctx: Context) -> Check:
    script = ctx.root / "scripts/init.py"
    if not script.is_file():
        return Check("init", SKIP, "项目缺 scripts/init.py（未安装）",
                     "运行 python3 scripts/init.py --yes")
    rc, stdout, stderr = run_script(
        script, ["--root", str(ctx.root), "--check"], ctx.root, ctx.timeout)
    if rc is None:
        return Check("init", FAIL, f"init --check 执行失败：{stderr.strip()[:200]}",
                     "检查 scripts/init.py 可执行性后重跑")
    detail = last_line(stdout) or last_line(stderr)
    if rc == 0:
        return Check("init", PASS, f"init --check 通过（{detail}）"
                     if detail else "init --check 通过")
    if rc == 2:
        return Check("init", SKIP, f"未安装/待初始化（rc=2）：{detail}",
                     "python3 scripts/init.py --yes")
    return Check("init", FAIL, f"init --check rc={rc}：{detail}",
                 "按 init 输出修复后重跑 python3 scripts/init.py --check")


def _doctor_json(ctx: Context, extra: list[str]) -> tuple[dict | None, str, int | None]:
    script = ctx.root / "scripts/doctor.py"
    if not script.is_file():
        return None, "项目缺 scripts/doctor.py（未安装）", None
    rc, stdout, stderr = run_script(
        script,
        ["--root", str(ctx.root), "--profile", "release", "--strict", "--json", *extra],
        ctx.root, ctx.timeout)
    if rc is None:
        return None, f"doctor 执行失败：{stderr.strip()[:200]}", None
    try:
        data = json.loads(stdout)
    except ValueError:
        tail = (stderr.strip() or stdout.strip())[:200]
        return None, f"doctor 输出非 JSON（rc={rc}）：{tail}", rc
    if not isinstance(data, dict):
        return None, f"doctor 输出非对象（rc={rc}）", rc
    return data, "", rc


def check_doctor(ctx: Context) -> Check:
    data, error, _ = _doctor_json(ctx, [])
    if data is None:
        status = SKIP if "未安装" in error else FAIL
        return Check("doctor", status, error, "运行 python3 scripts/init.py --yes"
                     if status == SKIP else "修复 doctor 后重跑")
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    fail_count = int(summary.get("fail") or 0)
    counts = (f"pass={summary.get('pass', 0)} warn={summary.get('warn', 0)} "
              f"fail={fail_count} skip={summary.get('skip', 0)} "
              f"env={summary.get('env', 0)}")
    if fail_count:
        items = [item for item in data.get("items", [])
                 if isinstance(item, dict) and item.get("status") == "fail"]
        ids = preview([str(item.get("id")) for item in items[:5]])
        return Check("doctor", FAIL, f"fail={fail_count}（{ids}）；{counts}",
                     "python3 scripts/doctor.py --root . --profile release --strict")
    return Check("doctor", PASS, f"fail=0（{counts}）")


def check_guard_lock(ctx: Context) -> Check:
    lock = ctx.crew / "guard.lock"
    text = read_text(lock)
    if text is None:
        reason = "guard.lock 缺失" if not lock.exists() else "guard.lock 无法读取"
        return Check("guard.lock", FAIL,
                     f"guard 未运行（{reason}）；自动叫醒不会发生",
                     f"启动：{ctx.guard_start()}")
    pid = lock_pid(text)
    if pid is None or pid <= 0:
        return Check("guard.lock", FAIL, "guard.lock 无法识别 pid",
                     f"删除 stale 锁后启动：{ctx.guard_start()}")
    if pid_alive(pid):
        return Check("guard.lock", PASS, f"guard pid={pid} 存活（{lock}）")
    return Check("guard.lock", FAIL,
                 f"guard pid={pid} 不活（stale 锁）；自动叫醒不会发生",
                 f"删除 stale 锁后启动：{ctx.guard_start()}")


def check_guard_helm(ctx: Context) -> Check:
    """运行中 guard 的 `--helm` 必须等于现役舵（防 guard 指向旧舵名）。"""
    lock = ctx.crew / "guard.lock"
    text = read_text(lock)
    if text is None:
        return Check("guard.helm", SKIP, "guard.lock 缺失/不可读，无法校验舵名",
                     "启动 guard 后复跑")
    pid = lock_pid(text)
    if pid is None or pid <= 0:
        return Check("guard.helm", SKIP, "guard.lock 无法识别 pid，无法校验舵名",
                     "删除 stale 锁后启动 guard 并复跑")
    if not pid_alive(pid):
        return Check("guard.helm", SKIP, f"guard pid={pid} 不活，无法校验舵名",
                     "启动 guard 后复跑")
    expected, how = resolve_active_helm(ctx)
    if not expected:
        return Check("guard.helm", SKIP, how, "补 dispatch registry 或配置 helm.agentName 后复跑")
    actual = running_guard_helm(pid)
    if actual is None:
        return Check("guard.helm", SKIP,
                     f"pid={pid} 的 --helm 不可解析（/proc 不可读或非 crew-guard 进程）",
                     "确认 guard 进程与 /proc 可读后复跑")
    if actual != expected:
        return Check("guard.helm", FAIL,
                     f"运行中 guard --helm={actual} != 现役舵 {expected}（{how}）",
                     f"以现役舵重启 guard：python3 scripts/crew-guard.py "
                     f"--helm {expected} --interval 120")
    return Check("guard.helm", PASS, f"guard --helm={actual} 与现役舵一致（{how}）")


def check_guard_doctor(ctx: Context) -> Check:
    data, error, rc = _doctor_json(ctx, ["--only", "runtime.guard"])
    if data is None:
        status = SKIP if "未安装" in error else FAIL
        return Check("guard.doctor", status, error,
                     "运行 python3 scripts/init.py --yes" if status == SKIP
                     else "修复 doctor 后重跑")
    items = [item for item in data.get("items", [])
             if isinstance(item, dict) and item.get("id") == "runtime.guard"]
    if not items:
        return Check("guard.doctor", FAIL,
                     f"doctor 未返回 runtime.guard 项（rc={rc}）",
                     "python3 scripts/doctor.py --only runtime.guard 排查")
    item = items[0]
    status = str(item.get("status") or "")
    detail = str(item.get("detail") or "")
    if status == "pass":
        return Check("guard.doctor", PASS, f"runtime.guard pass：{detail}")
    if status in ("skip", "env"):
        return Check("guard.doctor", SKIP,
                     f"runtime.guard {status}：{detail}",
                     "确认 crew 目录/git 状态后重跑")
    return Check("guard.doctor", FAIL,
                 f"runtime.guard {status}：{detail}",
                 f"启动：{ctx.guard_start()}")


CHECKS = [
    ("version.source", check_version_source),
    ("version.project", check_version_project),
    ("version.installed", check_version_installed),
    ("source.git-clean", check_source_git_clean),
    ("install.source_user", check_install_source_user),
    ("install.project_source", check_install_project_source),
    ("upgrade", check_upgrade),
    ("init", check_init),
    ("doctor", check_doctor),
    ("guard.lock", check_guard_lock),
    ("guard.helm", check_guard_helm),
    ("guard.doctor", check_guard_doctor),
]


# ---------------------------------------------------------------------------
# 输出与入口
# ---------------------------------------------------------------------------

def summarize(checks: list[Check]) -> dict[str, int]:
    summary = {"pass": 0, "fail": 0, "skip": 0}
    for check in checks:
        summary[check.status] = summary.get(check.status, 0) + 1
    return summary


def render_table(ctx: Context, checks: list[Check], exit_code: int) -> None:
    print(f"release-check（dry-run，只读）：root={ctx.root} "
          f"source={ctx.source or '不可用'}（{ctx.source_note}）")
    print(f"{'CHECK':<22} {'RESULT':<6} VALUE | NEXT")
    for check in checks:
        print(f"{check.id:<22} {check.status.upper():<6} {check.value} | next={check.next}")
    summary = summarize(checks)
    print(f"summary: pass={summary['pass']} fail={summary['fail']} "
          f"skip={summary['skip']} -> rc={exit_code}")


def build_report(ctx: Context, checks: list[Check], exit_code: int) -> dict:
    return {
        "schemaVersion": 1,
        "root": str(ctx.root),
        "source": str(ctx.source) if ctx.source else None,
        "sourceNote": ctx.source_note,
        "dryRun": True,
        "checks": [
            {"id": check.id, "status": check.status,
             "value": check.value, "next": check.next}
            for check in checks
        ],
        "summary": summarize(checks),
        "exitCode": exit_code,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release-check.py", description="发布就绪聚合检查（只读 pass/fail 表）",
        epilog=(f"my-skills 仓定位顺序：环境变量 {MY_SKILLS_ENV} → 技能源 "
                "git-common-dir 的宿主工作区 → 同级 my-skills 目录；定位不到 skip 注明。"))
    parser.add_argument("--root", default=".", help="项目仓根（默认 cwd）")
    parser.add_argument(
        "--source", help="技能源目录（默认：脚本自身所在技能副本，其次配置 skillRoot）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只读模式（默认行为；兼容发布剧本显式传参）")
    parser.add_argument("--json", action="store_true", help="输出机读 JSON")
    parser.add_argument("--timeout", type=int, default=120,
                        help="init/doctor 子进程超时秒数（默认 120）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"release-check: 项目根不存在：{root}", file=sys.stderr)
        return 3
    try:
        source, source_note = resolve_source(root, args.source)
    except FileNotFoundError as exc:
        print(f"release-check: {exc}", file=sys.stderr)
        return 3
    try:
        crew = crew_paths.crew_dir(root)
    except crew_paths.CrewPathsError as exc:
        print(f"release-check: 无法解析 crew 目录：{exc}", file=sys.stderr)
        return 3
    config, config_error = load_config(root)
    ctx = Context(root=root, source=source, source_note=source_note, crew=crew,
                  timeout=args.timeout, config=config, config_error=config_error)
    checks: list[Check] = []
    for check_id, func in CHECKS:
        try:
            checks.append(func(ctx))
        except Exception as exc:  # noqa: BLE001 - 单检查异常不得打崩聚合器
            checks.append(Check(check_id, FAIL,
                                f"检查异常：{type(exc).__name__}: {exc}"))
    summary = summarize(checks)
    exit_code = 1 if summary["fail"] else 0
    if args.json:
        print(json.dumps(build_report(ctx, checks, exit_code),
                         ensure_ascii=False, indent=2))
    else:
        render_table(ctx, checks, exit_code)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
