#!/usr/bin/env python3
"""backend=claude 统一执行器（T39；T56 模型传参改 --model）。

所有 `backend=claude` 的外部调用只走本入口：按项目配置解析模型，模型经
`--model <model>` 旗标传递（**不走 env**：`~/.claude/settings.json` 的 env 块会
覆盖子进程传入的 `ANTHROPIC_MODEL`，实测静默失效；开启
`CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY` 后 `--model` 传网关自定义模型 ID
纯文本与带工具调用均正常——T39 时期的 env 方案与「--model 不识别网关模型」的旧
证据已过期；证据 crew-orchestrator `.scratch/model-routing-20261113/{ov,mf,mt}.json`），
默认只读工具白名单 + 超时，报告/stderr/rc/meta 一次落盘，失败快速报并非零退出。

用法：
  python3 scripts/crew-claude.py run --role <role> --prompt <prompt.md> \\
      --out-dir <证据目录> [--name <文件前缀>] [--playbook <剧本>] \\
      [--timeout 1500] [--config <orchestration.json>] [--root <项目根>] \\
      [--claude-bin <claude 可执行文件>]

模型解析优先级：`agents.playbooks.<playbook>.roles.<role>` >
`agents.roles.<role>` > `agents.default`；命中条目的 backend 必须是 claude，否则拒绝
并提示改用对应通道（qoder/opencode 用 `dispatch.py start`）。

产出（`<out-dir>/<name>.{md,stderr,rc,meta.json}`）：
  .md         claude stdout（报告原文）
  .stderr     claude stderr 原文
  .rc         子进程退出码文本
  .meta.json  {role,playbook,backend,model,model_source,rc,timeout,timeout_seconds,
               bytes,stderr_bytes,report_ok,report_lines,report_problems(仅失败),
               duration,prompt,report,stderr_path,rc_path,claude,argv}
stdout 只打印一行摘要（含 report_ok/report_lines/bytes）；诊断写 stderr。

退出码：
  0   报告完整（report_ok=true）且 claude rc=0
  1   本地失败（claude rc=0 但报告空/过短/缺关键段——report_ok=false，可重派；
      报告文件仍落盘供复核，不删）
  2   参数/配置校验失败（backend≠claude、model 非字符串、空 prompt、
      --timeout/--name 非法；模型空白不属此类）
  3   环境缺失（配置/可执行文件/prompt/输出目录不可用）
  124 超时（与 timeout(1) 一致；已杀子进程组）
  其余镜像 claude rc（负信号按 128+n 转 shell 口径）

模型空白＝通道默认：`model=""` 合法，rc=0，不传 `--model`（子进程用
CLI/设置默认模型），不会返回 2。

运行注册（FB-040）：启动 claude 前在 `<crew-dir>/runs/` 原子写
`claude-<pid>-<随机>.json`（pid/role/backend/model/started_at/out_dir/root/command），
`crew_dir` 经 `crew_paths.crew_dir(root)` 解析；正常结束、非零 rc、超时统一删除
（try/finally）。注册失败只 stderr 告警，不阻断 run；`meta.json` 的 `runs_registry`
记注册文件路径（未注册为 null）。

报告完整性（2.2.32/T78/FB-060）：落盘后校验 非空 + 最小非空行数/字节 + 关键段
（含 `## ` 标题或首行行数/字节计法，二选一即过）；不符 → report_ok=false、
退出 1（可重派）、meta.report_problems 列原因，报告文件保留。阈值解析：
CLI `--min-report-lines/--min-report-bytes` 显式值 > 配置
`reportCheck.{minLines,minBytes}` > 常量默认（REPORT_MIN_LINES/REPORT_MIN_BYTES；
0=关闭该项，空报告仍恒拒）。默认依据 FB-060 实测：CASE headless scout 空报告
0–54B vs 正常报告 ≥2KB，错落率 50%。

决策：模型非空一律追加 `--model <model>`，不再判断是否为 CLI 已知别名，也不走
env（settings.json env 会覆盖子进程同名变量；`--model` 对别名与网关模型 ID 均
生效），避免双分支。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir as resolve_crew_dir  # noqa: E402

EXIT_OK = 0
EXIT_LOCAL = 1
EXIT_USAGE = 2
EXIT_ENV = 3
EXIT_TIMEOUT = 124

DEFAULT_TIMEOUT = 1500
ALLOWED_TOOLS = ("Bash", "Read", "Grep", "Glob")

# 报告完整性阈值（FB-060：CASE headless scout 空/仅摘要报告 0–54B vs 正常 ≥2KB）
REPORT_MIN_LINES = 5   # 最小非空行数；0=关闭
REPORT_MIN_BYTES = 200  # 最小字节数；0=关闭
DISALLOWED_TOOLS = ("Edit", "Write", "NotebookEdit")
STDERR_EXCERPT = 800


class UsageError(Exception):
    """参数或配置校验失败（退出码 2）。"""


class EnvError(Exception):
    """环境/IO 不可用（退出码 3）。"""


def resolve_root(root_arg: str | None, config_arg: str | None) -> tuple[Path, Path]:
    """返回 (项目根, 配置文件路径)。--config 优先，其次 --root，再次向上查找。"""
    if config_arg:
        config = Path(config_arg).expanduser().resolve()
        root = config.parent.parent if config.parent.name == ".planning" else config.parent
        return root, config
    if root_arg:
        root = Path(root_arg).expanduser().resolve()
        return root, root / ".planning/orchestration.json"
    start = Path.cwd().resolve()
    for cand in (start, *start.parents):
        config = cand / ".planning/orchestration.json"
        if config.is_file():
            return cand, config
    raise EnvError(
        "未找到 .planning/orchestration.json；请在项目根执行或传 --root/--config")


def load_agents(config: Path) -> dict:
    if not config.is_file():
        raise EnvError(f"配置文件不存在：{config}")
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvError(f"配置文件不可读：{config}（{exc}）") from exc
    agents = data.get("agents") if isinstance(data, dict) else None
    return agents if isinstance(agents, dict) else {}


def resolve_agent(agents: dict, playbook: str | None,
                  role: str) -> tuple[dict, str]:
    """按 剧本级角色 > 全局角色 > default 级联返回 (条目, 来源)。"""
    playbooks = agents.get("playbooks")
    if playbook and isinstance(playbooks, dict):
        entry = playbooks.get(playbook)
        roles = entry.get("roles") if isinstance(entry, dict) else None
        if isinstance(roles, dict) and isinstance(roles.get(role), dict):
            return roles[role], f"playbooks.{playbook}.roles.{role}"
    roles = agents.get("roles")
    if isinstance(roles, dict) and isinstance(roles.get(role), dict):
        return roles[role], f"roles.{role}"
    default = agents.get("default")
    if isinstance(default, dict):
        return default, "default"
    return {}, "none"


def validate_agent(entry: dict, source: str, role: str) -> tuple[str, str]:
    """校验 backend=claude 并返回 (backend, model)。"""
    backend = entry.get("backend")
    backend = backend.strip() if isinstance(backend, str) else ""
    if backend != "claude":
        hint = {
            "qoder": "用 python3 scripts/dispatch.py start --runtime qoder ...",
            "opencode": "用 python3 scripts/dispatch.py start --runtime opencode ...",
        }.get(backend, "改用对应 backend 的通道（dispatch.py start --runtime <runtime>）")
        raise UsageError(
            f"role={role} 命中 {source} 的 backend={backend or '<空>'}，不是 claude；{hint}")
    model = entry.get("model")
    if model is None:
        model = ""
    if not isinstance(model, str):
        raise UsageError(f"{source} 的 model 必须是字符串：{model!r}")
    model = model.strip()
    return backend, model


def display_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return str(path)


def write_bytes_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        raise EnvError(f"写证据失败：{path}（{exc}）") from exc
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except OSError:
            pass


def stderr_excerpt(data: bytes) -> str:
    text = data.decode("utf-8", "replace").strip()
    return text[-STDERR_EXCERPT:] if text else "<空>"


def register_run(root: Path, role: str, backend: str, model: str,
                 out_dir: Path) -> Path | None:
    """在 `<crew-dir>/runs/` 原子写运行注册（FB-040）；失败只告警返回 None。"""
    try:
        runs_dir = resolve_crew_dir(root) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        path = runs_dir / f"claude-{os.getpid()}-{uuid.uuid4().hex[:8]}.json"
        payload = {
            "pid": os.getpid(),
            "role": role,
            "backend": backend,
            "model": model,
            "started_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "out_dir": str(out_dir.resolve()),
            "root": str(root),
            "command": "crew-claude.py run",
        }
        tmp = path.with_name(path.name + ".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
        return path
    except (CrewPathsError, OSError, RuntimeError, ValueError) as exc:
        print(f"crew-claude: warning: 运行注册失败（run 继续，guard 将看不到本次运行）："
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return None


def unregister_run(path: Path | None) -> None:
    """删除自己的运行注册；二次删除静默（FB-040）。"""
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(f"crew-claude: warning: 运行注册清理失败：{path}（{exc}）",
              file=sys.stderr)


@contextmanager
def run_registration(root: Path, role: str, backend: str, model: str,
                     out_dir: Path):
    """包住整次运行的注册生命周期：进入即注册，任何退出路径都清理。"""
    path = register_run(root, role, backend, model, out_dir)
    try:
        yield path
    finally:
        unregister_run(path)


def check_report(stdout: bytes, *, min_lines: int, min_bytes: int
                 ) -> tuple[bool, int, list[str]]:
    """报告完整性校验（纯函数）：非空 + 最小非空行数/字节 + 关键段，返回
    (report_ok, 非空行数, problems)。关键段=含 `## ` 标题或首行带行数/字节计法
    （lines=/bytes=/行数/字节）二选一即过，兼容各角色报告格式；空报告恒拒。"""
    if len(stdout) == 0:
        return False, 0, ["报告为空（stdout 0 字节）"]
    text = stdout.decode("utf-8", errors="replace")
    rows = text.splitlines()
    lines = sum(1 for row in rows if row.strip())
    problems: list[str] = []
    if min_lines and lines < min_lines:
        problems.append(f"报告过短（非空行 {lines} < 最小 {min_lines}）")
    if min_bytes and len(stdout) < min_bytes:
        problems.append(f"报告过短（{len(stdout)} 字节 < 最小 {min_bytes}）")
    first = rows[0] if rows else ""
    has_heading = any(row.lstrip().startswith("## ") for row in rows)
    has_count = any(k in first for k in ("lines=", "bytes=", "行数", "字节"))
    if not (has_heading or has_count):
        problems.append("报告缺关键段（无 `## ` 标题且首行无行数/字节计法）")
    return (not problems), lines, problems


def report_thresholds(args: argparse.Namespace, config_path: Path) -> tuple[int, int]:
    """报告阈值解析：CLI 显式值 > 配置 reportCheck.{minLines,minBytes} > 常量默认。"""
    cfg: dict = {}
    if config_path.is_file():
        cfg = (json.loads(config_path.read_text(encoding="utf-8"))
               .get("reportCheck") or {})

    def pick(cli_value: int | None, key: str, default: int) -> int:
        if cli_value is not None:
            return cli_value
        value = cfg.get(key, default)
        return int(value) if value is not None else default

    return (pick(args.min_report_lines, "minLines", REPORT_MIN_LINES),
            pick(args.min_report_bytes, "minBytes", REPORT_MIN_BYTES))


def cmd_run(args: argparse.Namespace) -> int:
    root, config = resolve_root(args.root, args.config)
    agents = load_agents(config)
    entry, source = resolve_agent(agents, args.playbook, args.role)
    backend, model = validate_agent(entry, source, args.role)

    prompt = Path(args.prompt).expanduser()
    if not prompt.is_file():
        raise EnvError(f"prompt 文件不存在或不可读：{prompt}")
    if prompt.stat().st_size == 0:
        raise UsageError(f"prompt 文件为空：{prompt}")

    if args.timeout is None or args.timeout <= 0:
        raise UsageError(f"--timeout 必须是正数秒：{args.timeout}")

    name = (args.name or prompt.stem).strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise UsageError(f"--name 非法（不得为空或含路径分隔符）：{name!r}")

    out_dir = Path(args.out_dir).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise EnvError(f"输出目录不可用：{out_dir}（{exc}）") from exc

    claude_bin = args.claude_bin or shutil.which("claude")
    if not claude_bin:
        raise EnvError("未找到 claude 可执行文件；安装 Claude Code 或传 --claude-bin")
    if args.claude_bin and not (
            Path(claude_bin).is_file() and os.access(claude_bin, os.X_OK)):
        raise EnvError(f"--claude-bin 不是可执行文件：{claude_bin}")

    argv = [claude_bin, "-p", "--allowedTools", *ALLOWED_TOOLS,
            "--disallowedTools", *DISALLOWED_TOOLS]
    if model:
        argv.extend(["--model", model])

    report_path = out_dir / f"{name}.md"
    stderr_path = out_dir / f"{name}.stderr"
    rc_path = out_dir / f"{name}.rc"
    meta_path = out_dir / f"{name}.meta.json"

    started = time.monotonic()
    timed_out = False
    with run_registration(root, args.role, backend, model, out_dir) as runs_registry:
        with open(prompt, "rb") as stdin:
            try:
                proc = subprocess.Popen(
                    argv, stdin=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    cwd=str(root), start_new_session=True)
            except OSError as exc:
                raise EnvError(f"启动 claude 失败：{exc}") from exc
            try:
                stdout, stderr = proc.communicate(timeout=args.timeout)
                rc = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                kill_group(proc)
                try:
                    stdout, stderr = proc.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    stdout, stderr = b"", b""
                rc = EXIT_TIMEOUT
        duration = time.monotonic() - started
        if rc < 0:
            rc = 128 + (-rc)

        min_lines, min_bytes = report_thresholds(args, config)
        report_ok, report_lines, report_problems = check_report(
            stdout, min_lines=min_lines, min_bytes=min_bytes)

        meta = {
            "role": args.role,
            "playbook": args.playbook or "",
            "backend": backend,
            "model": model,
            "model_source": source,
            "rc": rc,
            "timeout": timed_out,
            "timeout_seconds": args.timeout,
            "bytes": len(stdout),
            "stderr_bytes": len(stderr),
            "report_ok": report_ok,
            "report_lines": report_lines,
            "duration": round(duration, 3),
            "prompt": display_path(prompt, root),
            "report": display_path(report_path, root),
            "stderr_path": display_path(stderr_path, root),
            "rc_path": display_path(rc_path, root),
            "claude": claude_bin,
            "argv": argv,
            "runs_registry": (
                display_path(runs_registry, root) if runs_registry else None),
        }
        if report_problems:
            meta["report_problems"] = report_problems

        # 一次落盘：报告 / stderr / rc / meta 全部在子进程结束后写入
        write_bytes_atomic(report_path, stdout)
        write_bytes_atomic(stderr_path, stderr)
        write_bytes_atomic(rc_path, f"{rc}\n".encode("utf-8"))
        write_bytes_atomic(
            meta_path,
            (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))

        problems: list[str] = []
        if timed_out:
            problems.append(f"超时（>{args.timeout}s），已杀子进程组")
        elif rc != 0:
            problems.append(f"claude rc={rc}；stderr 摘要：{stderr_excerpt(stderr)}")
        problems.extend(report_problems)  # rc≠0/超时不吞主因，报告问题并列

        if timed_out:
            exit_code = EXIT_TIMEOUT
        elif rc != 0:
            exit_code = rc
        elif problems:
            exit_code = EXIT_LOCAL
        else:
            exit_code = EXIT_OK

        status = "ok" if exit_code == EXIT_OK else "failed"
        print(
            f"crew-claude: {status} role={args.role} "
            f"model={model or '<channel-default>'} "
            f"backend={backend} rc={rc} bytes={len(stdout)} "
            f"report_ok={str(report_ok).lower()} report_lines={report_lines} "
            f"duration={duration:.1f}s "
            f"report={display_path(report_path, root)} meta={display_path(meta_path, root)}")
        if problems:
            print(f"crew-claude: 失败：{'；'.join(problems)}", file=sys.stderr)
            print(f"crew-claude: 证据：{display_path(stderr_path, root)} / "
                  f"{display_path(meta_path, root)}", file=sys.stderr)
        return exit_code


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="crew-claude.py",
        description="backend=claude 统一执行器：按 --model（缺省取配置 agents）选择模型、只读+超时、证据一次落盘。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="示例：python3 scripts/crew-claude.py run --role adversary "
               "--prompt .scratch/prompt.md --out-dir .planning/x/evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="执行一次只读 claude 调用并落盘证据")
    run.add_argument("--role", required=True, help="角色名（用于 agents 模型解析）")
    run.add_argument("--prompt", required=True, help="prompt 文件路径（作为 stdin）")
    run.add_argument("--out-dir", required=True, help="证据输出目录")
    run.add_argument("--name", default="", help="证据文件前缀（默认取 prompt 文件名）")
    run.add_argument("--playbook", default="", help="剧本名（用于剧本级模型覆盖）")
    run.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                     help=f"超时秒数（默认 {DEFAULT_TIMEOUT}；超时杀进程组并 rc=124）")
    run.add_argument("--root", default=None, help="项目根（默认从 cwd 向上查找配置）")
    run.add_argument("--config", default=None, help="指定 orchestration.json（测试/诊断用）")
    run.add_argument("--min-report-lines", type=int, default=None,
                     help=f"报告最小非空行数（默认配置 reportCheck.minLines 或 "
                          f"{REPORT_MIN_LINES}；0=关闭该项，空报告仍恒拒）")
    run.add_argument("--min-report-bytes", type=int, default=None,
                     help=f"报告最小字节数（默认配置 reportCheck.minBytes 或 "
                          f"{REPORT_MIN_BYTES}；0=关闭该项）")
    run.add_argument("--claude-bin", default=None, help=argparse.SUPPRESS)
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except UsageError as exc:
        print(f"crew-claude: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except EnvError as exc:
        print(f"crew-claude: {exc}", file=sys.stderr)
        return EXIT_ENV


if __name__ == "__main__":
    sys.exit(main())
