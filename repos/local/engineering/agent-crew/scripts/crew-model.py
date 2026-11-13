#!/usr/bin/env python3
"""模型切换执行器：settings 层批量切换 + 运行中 pane 批量直切。

来源与适配（2.2.30）：CASE_ZHIWAN set-model.sh / set-pane-model.sh 技能化。
CASE 版写死 qoder 单一假设；本版按三运行时适配：
- qoder：settings 改 `~/.qoder-cn/settings.json` 的 `agents.overrides[*].modelConfig.model`；
  pane 切换命令 `/model <id>`，提示符判 `Type your message`/`➜`/`按 Shift+Tab`（输入框上方
  模式提示，本机 Ready 态实证），回显判 `Model set to <id>` 或状态行 `[<id>]`
  （feedback #35 `Model set to` 有误报实证，状态行为强证据）。
- claude：settings 改 `~/.claude/settings.json` 顶层 `model`（全局默认；per-role 路由在
  orchestration.json agents.roles，本脚本不动）；pane 提示符判 `❯`（本机实证），
  回显判状态行 `[<id>]`（本机实证）或 `Set model to <id>`（未真实取证，mock 覆盖，
  残余风险=实际已切但报失败，失败侧安全可重跑补切）。
- opencode：无 settings 等价、pane `/model` 语义未验证 → 明确拒绝，不做半写。

安全护栏（X2/X3）：
- 默认 dry-run：零写入、零注入；写 settings 需显式 `--apply`（改前时间戳备份，
  每运行时保留最近 5 份 `*.bak-*` 防无界堆积）；
- 目标模型无默认值：缺 `--model` 一律用法错误退出；
- panes 默认只处理本 workspace（`--workspace` 或环境 `HERDR_WORKSPACE_ID`，两者皆无
  则拒绝，防跨项目误伤）；`agent_status=working` 一律跳过（防打断在跑任务，转 idle
  后重跑本命令补切）；未见输入提示符不硬切；切换后回读核验，未确认逐条列明失败；
- 本命令自身所在 pane 不排除：工位 idle 时也会被补切，与 CASE 内核一致。

退出码：0 成功；1 执行失败（含部分失败/拒绝）；2 用法错误。
真实 herdr 驱动一律先 dry-run 审阅计划；测试用 PATH 注入 fake herdr + 临时 HOME。

示例：
  python3 scripts/crew-model.py settings --runtime qoder --model glm-5.3-flash
  python3 scripts/crew-model.py settings --runtime claude --model glm-5.3-flash --apply
  python3 scripts/crew-model.py panes --runtime qoder --model glm-5.3-flash
  python3 scripts/crew-model.py panes --runtime claude --model glm-5.3-flash --apply
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 每运行时保留的备份份数上限（防无界堆积；改前仍逐次备份）
BACKUP_KEEP = 5
# 切换后等待回显刷新秒数；测试用环境变量覆盖
VERIFY_DELAY = float(os.environ.get("CREW_MODEL_VERIFY_DELAY", "2.5"))

SUPPORTED_RUNTIMES = ("qoder", "claude", "opencode")
# herdr pane list 的 agent 字段取值 → 本脚本运行时名
HERDR_AGENT_OF = {"qoder": "qodercli", "claude": "claude"}
# 各运行时输入提示符特征（pane read 末 N 行内出现即视为可注入）
PROMPT_MARKS = {
    "qoder": ("Type your message", "➜", "按 Shift+Tab"),
    "claude": ("❯",),
}
# 各运行时切换生效回显特征；`[<model>]` 为状态行强证据，其余为切换确认文案
SUCCESS_MARKS = {
    "qoder": ("Model set to {model}", "[{model}]"),
    "claude": ("Set model to {model}", "[{model}]"),
}


def run_herdr(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    herdr = shutil.which("herdr")
    if not herdr:
        raise FileNotFoundError("未找到 herdr 可执行文件（PATH 未注入？）")
    return subprocess.run([herdr, *args], capture_output=True, text=True, timeout=timeout)


def fail(msg: str) -> int:
    print(f"错误：{msg}", file=sys.stderr)
    return 1


def reject_runtime(runtime: str, action: str) -> int:
    print(
        f"错误：runtime={runtime} 不支持 {action}。\n"
        f"  原因：opencode 无 settings 模型等价配置，pane `/model` 语义未验证。\n"
        f"  替代：在 opencode 会话内手动 `/model` 切换，或改用 --runtime qoder/claude。",
        file=sys.stderr,
    )
    return 1


def backup_file(path: Path) -> Path:
    """改前时间戳备份并清理旧备份（保留最近 BACKUP_KEEP 份）。

    同秒多次 apply 时文件名递增 -N 后缀，保证第一份（原始内容）备份不被覆盖；
    prune 按 (mtime_ns, name) 排序，不受 copy2 继承源 mtime 影响。
    """
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bak = path.with_name(f"{path.name}.bak-{stamp}")
    n = 0
    while bak.exists():
        n += 1
        bak = path.with_name(f"{path.name}.bak-{stamp}-{n}")
    shutil.copy2(path, bak)
    baks = sorted(path.parent.glob(f"{path.name}.bak-*"),
                  key=lambda p: (p.stat().st_mtime_ns, p.name))
    for old in baks[:-BACKUP_KEEP]:
        old.unlink()
    return bak


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def dump_json(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.replace(tmp, path)  # 原子写（P3）


# ---------- settings 子命令 ----------


def cmd_settings(args: argparse.Namespace) -> int:
    runtime = args.runtime
    if runtime == "opencode":
        return reject_runtime(runtime, "settings 层切换")
    home = Path(os.environ.get("HOME", str(Path.home())))
    if runtime == "qoder":
        settings = home / ".qoder-cn" / "settings.json"
        field = "agents.overrides[*].modelConfig.model"
    else:
        settings = home / ".claude" / "settings.json"
        field = "顶层 model（全局默认；per-role 路由在 orchestration.json agents.roles）"
    if not settings.is_file():
        return fail(f"settings 不存在：{settings}")
    data = load_json(settings)
    if runtime == "qoder":
        overrides = data.get("agents", {}).get("overrides", {})
        if not isinstance(overrides, dict) or not overrides:
            return fail(f"agents.overrides 为空或结构异常，无键可改：{settings}")
        # 结构校验（dry-run/apply 同关口）：值或 modelConfig 非对象（含 null）拒绝，
        # 不静默重建、不 traceback；给出可执行修法
        bad = [name for name, conf in overrides.items()
               if not isinstance(conf, dict)
               or ("modelConfig" in conf and not isinstance(conf["modelConfig"], dict))]
        if bad:
            return fail(
                f"agents.overrides 结构异常（键 {'、'.join(bad)}：值或 modelConfig 为 "
                f"null/非对象），拒绝写入；请先手工修复 {settings}"
                f"（将该键改为 {{\"modelConfig\": {{}}}} 或删除该键）")
        before = {
            name: (conf or {}).get("modelConfig", {}).get("model")
            if isinstance(conf, dict) else None
            for name, conf in overrides.items()
        }
        plan = [f"  {name}: {before[name]} → {args.model}" for name in overrides]
    else:
        before = data.get("model")
        plan = [f"  model: {before} → {args.model}"]
    print(f"[dry-run] 将改 {settings}（{field}）：")
    for line in plan:
        print(line)
    if not args.apply:
        print("dry-run 完成（零写入）。确认后加 --apply 执行。")
        return 0
    bak = backup_file(settings)
    print(f"已备份：{bak}")
    if runtime == "qoder":
        for name, conf in overrides.items():
            if not isinstance(conf, dict):
                overrides[name] = conf = {}
            conf.setdefault("modelConfig", {})["model"] = args.model
        print(f"agents.overrides 全量 {len(overrides)} 键 modelConfig.model → {args.model}")
    else:
        data["model"] = args.model
        print(f"顶层 model → {args.model}（全局默认；per-role 路由在 orchestration.json agents.roles）")
    dump_json(settings, data)
    # 写后回读核验
    after = load_json(settings)
    if runtime == "qoder":
        ok = all(
            (conf or {}).get("modelConfig", {}).get("model") == args.model
            for conf in after.get("agents", {}).get("overrides", {}).values()
        )
    else:
        ok = after.get("model") == args.model
    if not ok:
        return fail(f"写后回读不一致：{settings}（备份在 {bak}）")
    print("写后回读核验通过（幂等，可重复执行覆盖新增键）。")
    return 0


# ---------- panes 子命令 ----------


def list_panes() -> list[dict]:
    proc = run_herdr("pane", "list")
    if proc.returncode != 0:
        raise RuntimeError(f"herdr pane list 失败 rc={proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout)["result"]["panes"]


def read_pane(pane_id: str, lines: int) -> str:
    proc = run_herdr("pane", "read", pane_id, "--lines", str(lines), "--format", "text", timeout=15)
    return proc.stdout if proc.returncode == 0 else ""


def send_switch(pane_id: str, model: str) -> None:
    run_herdr("pane", "send-text", pane_id, f"/model {model}", timeout=15)
    run_herdr("pane", "send-keys", pane_id, "Enter", timeout=15)


def cmd_panes(args: argparse.Namespace) -> int:
    runtime = args.runtime
    if runtime == "opencode":
        return reject_runtime(runtime, "pane 直切")
    workspace = args.workspace or os.environ.get("HERDR_WORKSPACE_ID", "")
    if not workspace:
        return fail(
            "未指定目标 workspace（--workspace）且环境无 HERDR_WORKSPACE_ID。\n"
            "  为防跨项目误伤，panes 默认只处理本 workspace；跨项目请显式 --workspace <id>。"
        )
    herdr_agent = HERDR_AGENT_OF[runtime]
    prompt_marks = PROMPT_MARKS[runtime]
    success_marks = tuple(m.format(model=args.model) for m in SUCCESS_MARKS[runtime])
    try:
        panes = list_panes()
    except (RuntimeError, FileNotFoundError, KeyError, json.JSONDecodeError) as exc:
        return fail(f"pane list 不可用：{exc}")
    targets = [
        p for p in panes
        if p.get("agent") == herdr_agent and p.get("workspace_id") == workspace
    ]
    if not targets:
        print(f"workspace={workspace} 内无 {herdr_agent} pane，无事可做。")
        return 0

    def label(p: dict) -> str:
        title = (p.get("terminal_title_stripped") or "")[:30]
        return f"{p['pane_id']} ({title})"

    switched: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    pending: list[dict] = []
    for pane in targets:
        status = pane.get("agent_status")
        if status == "working":
            skipped.append(f"{label(pane)} working（转 idle 后重跑补切）")
            continue
        pending.append(pane)
    if args.apply:
        for pane in pending:
            pane_id = pane["pane_id"]
            before = read_pane(pane_id, args.read_lines)
            if not any(mark in before for mark in prompt_marks):
                last = before.strip().splitlines()[-1] if before.strip() else "(空)"
                failed.append(f"{label(pane)} 未见输入提示符，疑似非交互/占用态，不硬切；末行：{last}")
                continue
            try:
                send_switch(pane_id, args.model)
            except subprocess.SubprocessError as exc:
                failed.append(f"{label(pane)} 注入失败：{exc}")
                continue
            time.sleep(VERIFY_DELAY)
            out = read_pane(pane_id, args.read_lines + 2)
            if any(mark in out for mark in success_marks):
                switched.append(label(pane))
            else:
                last = out.strip().splitlines()[-1] if out.strip() else "(空)"
                failed.append(f"{label(pane)} 未确认生效；末行回显：{last}")
    else:
        # dry-run：对每个候选做提示符预检（只读），预测结果
        for pane in pending:
            pane_id = pane["pane_id"]
            before = read_pane(pane_id, args.read_lines)
            if any(mark in before for mark in prompt_marks):
                switched.append(f"{label(pane)} [预检通过，apply 时将注入]")
            else:
                last = before.strip().splitlines()[-1] if before.strip() else "(空)"
                failed.append(f"{label(pane)} [预检] 未见输入提示符，apply 时将不硬切；末行：{last}")
    print(
        f"== pane 模型切换（{'apply' if args.apply else 'dry-run'}，目标 {args.model}）："
        f"成功 {len(switched)} / 跳过 {len(skipped)} / 失败 {len(failed)}"
    )
    for line in switched:
        print(f"  [{'将切' if not args.apply else '已切'}] {line}")
    for line in skipped:
        print(f"  [跳过] {line}")
    for line in failed:
        print(f"  [失败] {line}")
    return 1 if failed else 0


# ---------- CLI ----------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="模型切换执行器（settings 层 + 运行中 pane 层；默认 dry-run）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    settings = sub.add_parser("settings", help="settings 层批量切换（qoder/claude）")
    _add_common(settings)
    settings.set_defaults(func=cmd_settings)

    panes = sub.add_parser("panes", help="运行中 pane 批量直切（qoder/claude）")
    _add_common(panes)
    panes.add_argument("--workspace", default="",
                       help="目标 workspace_id（默认环境 HERDR_WORKSPACE_ID；防跨项目误伤）")
    panes.add_argument("--read-lines", type=int, default=8,
                       help="pane read 读取行数（默认 8）")
    panes.set_defaults(func=cmd_panes)
    return parser


def _add_common(sub_parser: argparse.ArgumentParser) -> None:
    sub_parser.add_argument("--runtime", required=True, choices=SUPPORTED_RUNTIMES,
                            help="目标运行时（opencode 一律拒绝）")
    sub_parser.add_argument("--model", required=True,
                            help="目标模型 ID（无默认值，必须显式传）")
    mode = sub_parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="只打印计划，零写入（默认）")
    mode.add_argument("--apply", action="store_true", help="真实写入/注入（必须显式）")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
