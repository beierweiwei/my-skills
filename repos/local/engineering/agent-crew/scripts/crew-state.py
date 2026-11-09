#!/usr/bin/env python3
"""轻量编排状态机：任务/工位状态 + 事件日志（仅审计与恢复）。

任务：backlog -> ready -> dispatched -> running -> review -> accepted -> archived；
      running <-> blocked；任意状态 -> cancelled。
工位：starting -> working -> blocked/done -> closed；
      blocked <-> working；idle ≠ done；done 需回执；只有 closed 才删注册表记录。
真源：plan 仍是任务状态真源；本脚本只校验门禁、追加事件、重放派生状态，不写 .planning。
事件：<git-common-dir>/crew/events.jsonl，一行一事件；应用状态可完全由日志重放重建。
幂等：幂等键 = <kind>:<id>:<目标状态>:<revision>；同键重复 transition 返回 0 且不追加事件。
并发：flock 排他锁 + 单次写入 + fsync；崩溃留下的末尾半行/损坏行由 recover 清理。
门禁：工单/回执/占用/评审/抽验/归档工件由脚本检查；正常路径低输出。
退出码：0 成功（含幂等重复）；2 非法迁移或门禁不满足；3 事件日志损坏（先 recover）。

工件约定（均在 <git-common-dir>/crew/，由 crew_paths.crew_dir 解析）：
  tickets/<id>.json   工单：goal|目标、acceptance|验收断言、boundary|边界规则（非空）
  inbox/*.json        回执：task、status(done|blocked|captain|ack)、changed_files[]、
                      evidence[]、work_slot
  occupancy.json      文件占用：{<path>: <slot>} 或 {"files": {...}}
  registry.json       工位路由瘦表：{<slot>: {...}} 或 {"slots": {...}}

用法：
  python3 scripts/crew-state.py transition task T-1 ready --revision 1
  python3 scripts/crew-state.py transition task T-1 dispatched --revision 1 --slot builder-1
  python3 scripts/crew-state.py transition task T-1 running --revision 1 [--herdr-working]
  python3 scripts/crew-state.py transition task T-1 blocked --revision 1
  python3 scripts/crew-state.py transition task T-1 review --revision 1
  python3 scripts/crew-state.py transition task T-1 accepted --revision 1 \\
      --review review.md --spot-check spot.md --plan-updated
  python3 scripts/crew-state.py transition task T-1 archived --revision 1 \\
      --evidence-archive archive.md --plan-closed
  python3 scripts/crew-state.py transition task T-1 cancelled --revision 1 --note 原因
  python3 scripts/crew-state.py transition slot builder-1 working --revision 1
  python3 scripts/crew-state.py transition slot builder-1 done --revision 1
  python3 scripts/crew-state.py transition slot builder-1 closed --revision 1
  python3 scripts/crew-state.py status|derive|history|validate|recover
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402

TASK_FLOW = {
    "backlog": {"ready", "cancelled"},
    "ready": {"dispatched", "cancelled"},
    "dispatched": {"running", "cancelled"},
    "running": {"review", "blocked", "cancelled"},
    "blocked": {"running", "cancelled"},
    "review": {"accepted", "cancelled"},
    "accepted": {"archived", "cancelled"},
    "archived": {"cancelled"},
    "cancelled": set(),
}
SLOT_FLOW = {
    "starting": {"working"},
    "working": {"blocked", "done"},
    "blocked": {"working"},
    "done": {"closed"},
    "closed": set(),
}
TASK_STATES = set(TASK_FLOW)
SLOT_STATES = set(SLOT_FLOW)
EVENT_KEYS = ("seq", "ts", "kind", "id", "action", "from", "to", "key", "rev")

EXIT_OK, EXIT_REJECT, EXIT_CORRUPT = 0, 2, 3


class Rejected(Exception):
    """非法迁移、门禁不满足或工件缺失。"""


class Corrupt(Exception):
    """事件日志存在损坏行或末尾半行，需先 recover。"""


def now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_root(value: str | None) -> Path:
    if value:
        return Path(value).expanduser().resolve()
    cur = Path.cwd().resolve()
    for cand in (cur, *cur.parents):
        if (cand / ".git").exists():
            return cand
    return cur  # 非 git：crew_paths 回退 <root>/.crew


class Crew:
    def __init__(self, root: Path):
        self.root = root
        try:
            self.dir = crew_dir(root)
        except CrewPathsError as exc:
            raise SystemExit(f"crew-state: 运行时目录解析失败：{exc}") from exc
        self.log = self.dir / "events.jsonl"
        self.lock = self.dir / "lock"

    @contextmanager
    def locked(self, exclusive: bool):
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.lock, "a+", encoding="utf-8") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def event_error(obj) -> str | None:
    if not isinstance(obj, dict):
        return "not an object"
    for key in EVENT_KEYS:
        if key not in obj:
            return f"missing {key}"
    if obj["kind"] not in ("task", "slot"):
        return "bad kind"
    states = TASK_STATES if obj["kind"] == "task" else SLOT_STATES
    if obj["from"] not in states or obj["to"] not in states:
        return "bad state"
    if not isinstance(obj["seq"], int):
        return "bad seq"
    return None


def scan_events(crew: Crew) -> tuple[list[dict], list[int], bool]:
    """宽容扫描：返回 (合法事件, 损坏行号, 是否有末尾半行)。"""
    if not crew.log.exists():
        return [], [], False
    raw = crew.log.read_bytes()
    if not raw:
        return [], [], False
    partial = not raw.endswith(b"\n")
    chunks = raw.split(b"\n")
    if partial:
        chunks.pop()
    else:
        chunks.pop()  # 末尾换行后的空串
    events: list[dict] = []
    bad: list[int] = []
    for lineno, chunk in enumerate(chunks, 1):
        if not chunk.strip():
            bad.append(lineno)
            continue
        try:
            obj = json.loads(chunk.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            bad.append(lineno)
            continue
        if event_error(obj):
            bad.append(lineno)
            continue
        events.append(obj)
    return events, bad, partial


def read_events(crew: Crew) -> list[dict]:
    events, bad, partial = scan_events(crew)
    if bad or partial:
        parts = []
        if bad:
            parts.append("corrupt line(s) " + ",".join(str(n) for n in bad))
        if partial:
            parts.append("partial trailing line")
        raise Corrupt("; ".join(parts))
    return events


def derive_state(events: list[dict]) -> dict:
    tasks: dict[str, dict] = {}
    slots: dict[str, dict] = {}
    for ev in events:
        bucket = tasks if ev["kind"] == "task" else slots
        rec = bucket.setdefault(ev["id"], {"state": None, "updated": None, "last_key": None})
        rec["state"] = ev["to"]
        rec["updated"] = ev["ts"]
        rec["last_key"] = ev["key"]
    return {"tasks": tasks, "slots": slots}


def append_event(crew: Crew, event: dict) -> None:
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
    with open(crew.log, "a", encoding="utf-8") as fh:
        fh.write(line)
        fh.flush()
        os.fsync(fh.fileno())


def rewrite_log(crew: Crew, events: list[dict]) -> None:
    crew.dir.mkdir(parents=True, exist_ok=True)
    tmp = crew.log.with_name(crew.log.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        for ev in events:
            fh.write(json.dumps(ev, ensure_ascii=False, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, crew.log)


def write_json_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_json_map(path: Path, label: str, nested_key: str) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rejected(f"{label} unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected(f"{label} is not an object")
    inner = data.get(nested_key)
    return inner if isinstance(inner, dict) else data


def resolve_path(crew: Crew, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else crew.root / path


def require_file(crew: Crew, value: str | None, label: str) -> Path:
    if not value:
        raise Rejected(f"{label} is required")
    path = resolve_path(crew, value)
    if not path.is_file() or path.stat().st_size == 0:
        raise Rejected(f"{label} missing or empty: {path}")
    return path


def ticket_gate(crew: Crew, ident: str, ticket_file: str | None) -> None:
    path = resolve_path(crew, ticket_file) if ticket_file else crew.dir / "tickets" / f"{ident}.json"
    if not path.is_file():
        raise Rejected(f"ticket missing: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rejected(f"ticket unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected("ticket is not an object")
    for keys, label in (
        (("goal", "目标"), "goal/目标"),
        (("acceptance", "验收断言"), "acceptance/验收断言"),
        (("boundary", "边界规则"), "boundary/边界规则"),
    ):
        value = next((data[k] for k in keys if k in data), None)
        if value is None or (isinstance(value, (str, list, dict)) and not value):
            raise Rejected(f"ticket missing {label}")


def candidate_receipts(crew: Crew, receipt: str | None) -> list[dict]:
    if receipt:
        path = resolve_path(crew, receipt)
        if not path.is_file():
            raise Rejected(f"receipt missing: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise Rejected(f"receipt unreadable: {exc}") from exc
        return [data] if isinstance(data, dict) else []
    inbox = crew.dir / "inbox"
    if not inbox.is_dir():
        return []
    receipts = []
    for path in sorted(inbox.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            receipts.append(data)
    return receipts


def find_receipt(
    crew: Crew,
    receipt: str | None,
    *,
    task: str | None = None,
    slot: str | None = None,
    statuses: set[str] | None = None,
    require_done_payload: bool = False,
) -> bool:
    for data in candidate_receipts(crew, receipt):
        if task is not None and data.get("task") != task:
            continue
        if slot is not None and data.get("work_slot") != slot:
            continue
        if statuses is not None and data.get("status") not in statuses:
            continue
        if require_done_payload and not (data.get("changed_files") and data.get("evidence")):
            continue
        return True
    return False


def split_files(values: list[str]) -> list[str]:
    files = []
    for chunk in values:
        files.extend(part.strip() for part in chunk.split(",") if part.strip())
    return files


def check_gate(crew: Crew, kind: str, ident: str, frm: str, to: str, args, state: dict) -> None:
    if kind == "task":
        if to == "cancelled":
            return
        if (frm, to) == ("backlog", "ready"):
            ticket_gate(crew, ident, args.ticket_file)
        elif (frm, to) == ("ready", "dispatched"):
            slot = (args.slot or "").strip()
            if not slot:
                raise Rejected("ready->dispatched requires --slot (registered worker)")
            if state["slots"].get(slot, {}).get("state") == "closed":
                raise Rejected(f"worker {slot} is closed")
            occupancy = load_json_map(crew.dir / "occupancy.json", "occupancy.json", "files")
            conflicts = [
                f for f in split_files(args.files)
                if occupancy.get(f) not in (None, slot)
            ]
            if conflicts:
                raise Rejected("occupancy conflict: " + ", ".join(conflicts))
        elif (frm, to) == ("dispatched", "running"):
            if not args.herdr_working and not find_receipt(crew, args.receipt, task=ident):
                raise Rejected("dispatched->running requires receipt or --herdr-working")
        elif (frm, to) == ("running", "blocked"):
            if not find_receipt(
                crew, args.receipt, task=ident, statuses={"blocked", "captain"}
            ):
                raise Rejected("running->blocked requires blocked/captain receipt")
        elif (frm, to) == ("running", "review"):
            if not find_receipt(
                crew,
                args.receipt,
                task=ident,
                statuses={"done"},
                require_done_payload=True,
            ):
                raise Rejected("running->review requires done receipt with changed_files and evidence")
        elif (frm, to) == ("review", "accepted"):
            review = require_file(crew, args.review, "--review (reviewer PASS)")
            if "PASS" not in review.read_text(encoding="utf-8", errors="replace"):
                raise Rejected("--review does not contain PASS")
            require_file(crew, args.spot_check, "--spot-check (逐节点抽验记录)")
            if not args.plan_updated:
                raise Rejected("review->accepted requires --plan-updated")
        elif (frm, to) == ("accepted", "archived"):
            require_file(crew, args.evidence_archive, "--evidence-archive")
            if not args.plan_closed:
                raise Rejected("accepted->archived requires --plan-closed")
    else:
        if (frm, to) == ("working", "done"):
            if not find_receipt(crew, args.receipt, slot=ident, statuses={"done"}):
                raise Rejected("slot done requires done receipt with matching work_slot")


def drop_registry_entry(crew: Crew, ident: str) -> None:
    path = crew.dir / "registry.json"
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Rejected(f"registry.json unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise Rejected("registry.json is not an object")
    container = data.get("slots") if isinstance(data.get("slots"), dict) else data
    if ident in container:
        del container[ident]
        write_json_atomic(path, data)


def cmd_transition(crew: Crew, args) -> int:
    kind, ident, to = args.kind, args.ident, args.to
    flow = TASK_FLOW if kind == "task" else SLOT_FLOW
    if to not in flow:
        raise Rejected(f"unknown {kind} state: {to}")
    with crew.locked(exclusive=True):
        events = read_events(crew)
        key = f"{kind}:{ident}:{to}:{args.revision}"
        if any(ev["key"] == key for ev in events):
            if kind == "slot" and to == "closed":
                drop_registry_entry(crew, ident)
            print(f"duplicate: {key} (no-op)")
            return EXIT_OK
        state = derive_state(events)
        if kind == "task":
            frm = state["tasks"].get(ident, {}).get("state", "backlog")
        else:
            frm = state["slots"].get(ident, {}).get("state", "starting")
        if args.expect and args.expect != frm:
            raise Rejected(f"expect {args.expect}, actual {frm}")
        if to not in flow.get(frm, set()):
            raise Rejected(f"illegal transition: {kind} {ident} {frm}->{to}")
        check_gate(crew, kind, ident, frm, to, args, state)
        event = {
            "seq": max((ev["seq"] for ev in events), default=0) + 1,
            "ts": now_ts(),
            "kind": kind,
            "id": ident,
            "action": to,
            "from": frm,
            "to": to,
            "key": key,
            "rev": args.revision,
            "actor": args.actor,
            "note": args.note,
        }
        append_event(crew, event)
        if kind == "slot" and to == "closed":
            drop_registry_entry(crew, ident)
    print(f"{kind} {ident}: {frm} -> {to}")
    return EXIT_OK


def cmd_status(crew: Crew, args) -> int:
    with crew.locked(exclusive=False):
        events = read_events(crew)
    state = derive_state(events)
    if args.json:
        payload = {**state, "events": len(events)}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK
    lines = []
    if state["tasks"]:
        lines.append("tasks:")
        for ident in sorted(state["tasks"]):
            rec = state["tasks"][ident]
            lines.append(f"  {ident}  {rec['state']}  {rec['updated']}")
    if state["slots"]:
        lines.append("slots:")
        for ident in sorted(state["slots"]):
            rec = state["slots"][ident]
            lines.append(f"  {ident}  {rec['state']}  {rec['updated']}")
    print("\n".join(lines) if lines else "no records")
    return EXIT_OK


def cmd_derive(crew: Crew, args) -> int:
    with crew.locked(exclusive=False):
        events = read_events(crew)
    payload = {**derive_state(events), "events": len(events)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_history(crew: Crew, args) -> int:
    with crew.locked(exclusive=False):
        events = read_events(crew)
    if args.task:
        events = [ev for ev in events if ev["kind"] == "task" and ev["id"] == args.task]
    elif args.slot:
        events = [ev for ev in events if ev["kind"] == "slot" and ev["id"] == args.slot]
    if args.json:
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return EXIT_OK
    for ev in events:
        note = f"  # {ev['note']}" if ev.get("note") else ""
        print(
            f"{ev['seq']}\t{ev['ts']}\t{ev['kind']}\t{ev['id']}\t"
            f"{ev['from']}->{ev['to']}\t{ev['key']}{note}"
        )
    if not events:
        print("no records")
    return EXIT_OK


def cmd_validate(crew: Crew, args) -> int:
    with crew.locked(exclusive=False):
        events = read_events(crew)
    issues = []
    seen_keys: set[str] = set()
    tasks: dict[str, str] = {}
    slots: dict[str, str] = {}
    for ev in events:
        key = ev["key"]
        if key in seen_keys:
            issues.append(f"seq {ev['seq']}: duplicate key {key}")
        seen_keys.add(key)
        bucket = tasks if ev["kind"] == "task" else slots
        flow = TASK_FLOW if ev["kind"] == "task" else SLOT_FLOW
        initial = "backlog" if ev["kind"] == "task" else "starting"
        current = bucket.get(ev["id"], initial)
        if ev["from"] != current:
            issues.append(
                f"seq {ev['seq']}: {ev['kind']} {ev['id']} from={ev['from']} but state={current}"
            )
        if ev["to"] not in flow.get(ev["from"], set()):
            issues.append(
                f"seq {ev['seq']}: illegal {ev['kind']} {ev['id']} {ev['from']}->{ev['to']}"
            )
        bucket[ev["id"]] = ev["to"]
    if issues:
        for issue in issues:
            print(f"invalid: {issue}", file=sys.stderr)
        return EXIT_CORRUPT
    print(f"ok: {len(events)} events, {len(tasks)} tasks, {len(slots)} slots")
    return EXIT_OK


def cmd_recover(crew: Crew, args) -> int:
    with crew.locked(exclusive=True):
        if not crew.log.exists():
            print("recover: no events log; nothing to do")
            return EXIT_OK
        events, bad, partial = scan_events(crew)
        if bad or partial:
            rewrite_log(crew, events)
        state = derive_state(events)
    parts = [f"recover: kept {len(events)} event(s)"]
    if bad:
        parts.append(f"dropped {len(bad)} corrupt line(s) at {','.join(map(str, bad))}")
    if partial:
        parts.append("truncated partial trailing line")
    if not bad and not partial:
        parts.append("no repairs needed")
    parts.append(f"tasks={len(state['tasks'])} slots={len(state['slots'])}")
    print("; ".join(parts))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="轻量编排状态机（任务/工位 + 事件日志）")
    parser.add_argument("--root", help="仓根（含 .git）；默认从 cwd 向上查找")
    sub = parser.add_subparsers(dest="command", required=True)

    transition = sub.add_parser("transition", help="执行一次合法迁移（幂等）")
    transition.add_argument("kind", choices=["task", "slot"])
    transition.add_argument("ident")
    transition.add_argument("to")
    transition.add_argument("--revision", required=True, help="幂等键的一部分")
    transition.add_argument("--expect", help="仅当当前状态匹配时才执行")
    transition.add_argument("--actor", default=os.environ.get("USER", ""))
    transition.add_argument("--note", default="")
    transition.add_argument("--ticket-file", help="工单 JSON（默认 tickets/<id>.json）")
    transition.add_argument("--slot", default="", help="ready->dispatched 的工位名")
    transition.add_argument("--files", action="append", default=[], help="占用文件（可逗号分隔/重复）")
    transition.add_argument("--receipt", help="指定回执文件；默认扫描 inbox/*.json")
    transition.add_argument("--herdr-working", action="store_true", help="Herdr 显示 working 的自证")
    transition.add_argument("--review", help="review->accepted：reviewer PASS 文件")
    transition.add_argument("--spot-check", help="review->accepted：逐节点抽验记录")
    transition.add_argument("--plan-updated", action="store_true", help="review->accepted：plan 已更新")
    transition.add_argument("--evidence-archive", help="accepted->archived：证据归档文件")
    transition.add_argument("--plan-closed", action="store_true", help="accepted->archived：plan 已收口")
    transition.set_defaults(func=cmd_transition)

    status = sub.add_parser("status", help="一屏查看当前任务/工位状态")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    derive = sub.add_parser("derive", help="从事件日志重放并输出应用状态（JSON）")
    derive.set_defaults(func=cmd_derive)

    history = sub.add_parser("history", help="查看事件历史")
    filt = history.add_mutually_exclusive_group()
    filt.add_argument("--task")
    filt.add_argument("--slot")
    history.add_argument("--json", action="store_true")
    history.set_defaults(func=cmd_history)

    validate = sub.add_parser("validate", help="校验日志与迁移语义")
    validate.set_defaults(func=cmd_validate)

    recover = sub.add_parser("recover", help="清理末尾半行/损坏行并从日志重建状态")
    recover.set_defaults(func=cmd_recover)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    crew = Crew(resolve_root(args.root))
    try:
        return args.func(crew, args)
    except Rejected as exc:
        print(f"rejected: {exc}", file=sys.stderr)
        return EXIT_REJECT
    except Corrupt as exc:
        print(f"corrupt: {exc}; run recover", file=sys.stderr)
        return EXIT_CORRUPT


if __name__ == "__main__":
    sys.exit(main())
