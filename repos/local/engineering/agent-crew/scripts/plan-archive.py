#!/usr/bin/env python3
"""plan 段级归档执行器：流 task_plan/progress 超限段归档（默认 dry-run）。

来源与适配（2.2.30）：CASE_ZHIWAN plan-archive.py 技能化。与 CASE 的口径 diff 及
取舍（依据 plan-discipline §5.2 正文，非照搬）：
- 归档对象：CASE 一次处理 task_plan+progress（CASE 模板段名）；本版 `--task <slug>`
  指定流，处理 `.planning/<task>/{task_plan,progress}.md`；portfolio progress 仍归
  `plan-status.py --rotate`（§5.2：portfolio 由总舵归档，--rotate 已覆盖），两锁并存
  （`.plan-archive.lock` / `.plan-rotate.lock`）互不冲突。
- progress 阈值：CASE >400 行（HARD_CAP）；本版按 §5.2 保留粒度 ≥40KB 或 ≥100 条目
  （与 plan-status PROGRESS_WARN 同口径），400 行口径弃用；保留最近 `--keep` 条
  （默认 10，同 --rotate --keep 口径）。
- task_plan：Next Step 已完成（`- [x]`）>10 → 最旧迁出归档（未完成全留）；「最近完成」
  条目 >10 → 最旧迁出（§5.1.1/§5.2）。已完成行**迁入「## 最近完成」**仍由 owner 手工
  做（SOP 语义不变），本脚本只做超限裁剪；Decisions/Errors/Backlog/Phase 一律不动；
  findings 不归档（§5.2）。
- CASE 特有逻辑弃用：里程表 markdown 表格行列解析、待 Captain 行清单（CASE 模板段）；
  保留：保护最近 2 个有记录自然日、交接段及之后不归档、整段不可切分（归档后可低于
  目标，note 说明）。
- 归档落点：`.planning/_archive/<task>-<file>-<YYYYMMDD>.md`（§5.2 禁跨 task 合并）；
  同日多次运行在文件内 `---` 追加分段，不覆盖。
- 安全（X2/X3 与工单口径）：默认 dry-run 零写入；`--apply` 必须显式 `--task`+`--owner`，
  `--all --apply` 拒绝；flock 非阻塞占用即拒绝（写者活跃禁止 apply，§5.2）；读入后
  二次 sha256 校验防 TOCTOU，异常 abort 且 live 不改；先写归档 fsync 再原子重写 live
  （tmp+rename）；归档头含来源/日期/截止边界/before-after/源 sha256/操作者；归档文件
  与 live 改动同一 commit 可 git revert，不提供 restore（§5.2）。

progress 两种形态（技能流实际存在）：
- 里程节式：`## 自动驾驶里程` 节内 `- YYYY-MM-DD` 条目块（含缩进续行），遇下一 `## `
  停止；无该节时全文件按日期前缀兜底（同 plan-status --rotate 口径）。
- 日期段式：`## YYYY-MM-DD` 段整段迁移（旧格式流，如历史流 plan）。

退出码：0 成功（含「无可归档」）；1 拒绝/失败（锁占用、结构异常、守恒失败、冲突）；
2 用法错误。

示例：
  python3 scripts/plan-archive.py --task patch-2230                # dry-run
  python3 scripts/plan-archive.py --task patch-2230 --apply --owner patch-2230-firstmate
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# —— 阈值（§5.2 口径；与 plan-status PROGRESS_WARN 一致）——
PROGRESS_MAX_BYTES = 40960   # progress ≥40KB 触发
PROGRESS_MAX_ENTRIES = 100   # progress 条目 ≥100 触发
RECENT_KEEP = 10             # task_plan：Next Step 已完成/最近完成 保留上限（§5.1.1）
DEFAULT_KEEP = 10            # progress 保留最近条目数（同 --rotate --keep）

ARCHIVE_DIRNAME = "_archive"
LOCK_REL = ".planning/.plan-archive.lock"
POINTER_PREFIX = "归档记录："
BODY_START = "<!-- archive-body:start -->"
BODY_END = "<!-- archive-body:end -->"
DATE_LEAD_RE = re.compile(r"^##\s+(\d{4}-\d{2}-\d{2})\b")
ENTRY_RE = re.compile(r"^- ")
DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]")
UNDONE_RE = re.compile(r"^\s*-\s*\[\s\]")
HANDOFF_RE = re.compile(r"交接|接手")


def fail(msg: str) -> int:
    print(f"错误：{msg}", file=sys.stderr)
    return 1


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)  # 原子写（P3）
    dir_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def split_sections(lines: list[str]) -> list[tuple[str | None, list[str]]]:
    """按 `## ` 标题切段；返回 [(heading 或 None, 段行)]，前置头段 heading=None。"""
    sections: list[tuple[str | None, list[str]]] = [(None, [])]
    for line in lines:
        if line.startswith("## "):
            sections.append((line.strip(), [line]))
        else:
            sections[-1][1].append(line)
    return sections


# ---------- task_plan 规划 ----------


def _find_section(sections, keyword: str) -> int | None:
    return next((i for i, (h, _) in enumerate(sections)
                 if h is not None and keyword in h), None)


def plan_task_plan(rel: str, lines: list[str]) -> dict | None:
    """返回 {'move': [(节名, [行])], 'notes', 'before', 'after', 'keep'}；结构异常标 reject。"""
    sections = split_sections(lines)
    notes: list[str] = []
    move: list[tuple[str, list[str]]] = []
    remove_idx: set[int] = set()  # 全文件行下标（1:1 映射 lines）

    ns_i = _find_section(sections, "Next Step")
    rc_i = _find_section(sections, "最近完成")
    if ns_i is None and rc_i is None:
        notes.append("缺「Next Step」与「最近完成」节，结构异常，本文件拒绝归档")
        return {"move": [], "notes": notes, "reject": True,
                "before": len(lines), "after": len(lines)}

    # 段 → 全局行号区间（段下标 ≠ 行下标，须重建映射）
    sec_ranges: dict[int, tuple[int, int]] = {}
    pos = 0
    for i, (_, sec_lines) in enumerate(sections):
        sec_ranges[i] = (pos, pos + len(sec_lines))
        pos += len(sec_lines)

    for sec_i, keyword, kind in ((ns_i, "Next Step", "done"), (rc_i, "最近完成", "entry")):
        if sec_i is None:
            notes.append(f"缺「{keyword}」节，跳过该节裁剪")
            continue
        start, end = sec_ranges[sec_i]
        _, sec_lines = sections[sec_i]
        # 节体 = 标题行之后（全局下标 start+1 起与 sec_lines[1:] 一一对应）
        targets = [(start + 1 + k, ln) for k, ln in enumerate(sec_lines[1:])
                   if (DONE_RE if kind == "done" else ENTRY_RE).match(ln)]
        excess = len(targets) - RECENT_KEEP
        if excess > 0:
            drop = targets[:excess]  # 文件序=最旧在前
            for idx, _ in drop:
                remove_idx.add(idx)
            label = ("Next Step：已完成" if kind == "done" else "最近完成：最旧")
            move.append((f"{label} {excess} 条（超出保留 {RECENT_KEEP}）",
                         [ln for _, ln in drop]))
    keep = [ln for i, ln in enumerate(lines) if i not in remove_idx]
    return {"move": move, "notes": notes, "reject": False,
            "before": len(lines), "after": len(keep), "keep": keep}


# ---------- progress 规划 ----------


def _entry_blocks(lines: list[str]) -> list[tuple[int, int]]:
    """里程节式条目块 [(start,end))：`## 自动驾驶里程` 节内 `- ` 条目行起 + 缩进续行；
    无该节时全文件兜底，遇 `## ` 停止。与 plan-status _entry_blocks 同口径。"""
    has_section = any(ln.strip().startswith("## ") and "自动驾驶里程" in ln for ln in lines)
    blocks, i = [], 0
    in_section = not has_section
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("## "):
            if has_section:
                in_section = "自动驾驶里程" in stripped
            else:
                break
            i += 1
            continue
        if in_section and ENTRY_RE.match(lines[i]):
            j = i + 1
            while j < len(lines) and lines[j][:1] in (" ", "\t"):
                j += 1
            blocks.append((i, j))
            i = j
        else:
            i += 1
    return blocks


def plan_progress(rel: str, lines: list[str], keep: int) -> dict:
    notes: list[str] = []
    size = len("\n".join(lines).encode("utf-8"))
    sections = split_sections(lines)
    remove_idx: set[int] = set()
    move: list[tuple[str, list[str]]] = []

    handoff_i = next((i for i, (h, _) in enumerate(sections)
                      if h is not None and HANDOFF_RE.search(h)), None)
    if handoff_i is not None:
        notes.append(f"保护交接段及之后：「{sections[handoff_i][0]}」")

    blocks = _entry_blocks(lines)
    entries = len(blocks)
    over_bytes = size >= PROGRESS_MAX_BYTES
    over_entries = entries >= PROGRESS_MAX_ENTRIES
    has_date_secs = any(h is not None and DATE_LEAD_RE.match(h) for h, _ in sections)

    if has_date_secs:
        # 形态 B：日期段式（旧格式）——超阈值时整段迁移最旧一段，保护最近 2 个有记录日。
        # 优先于条目块兜底（日期段式的 `- ` 条目行会被 ENTRY_RE 误中，整段迁移语义优先）
        days = sorted({DATE_LEAD_RE.match(h).group(1) for h, _ in sections
                       if h is not None and DATE_LEAD_RE.match(h)})
        protected = set(days[-2:])
        notes.append(f"保护最近 2 个有记录自然日：{'/'.join(days[-2:])}")
        if not (over_bytes or over_entries):
            notes.append(f"字节 {size} < 40KB 且条目 {entries} < 100，不归档日期段")
        else:
            chosen: int | None = None
            for i, (h, _) in enumerate(sections):
                if h is None or not DATE_LEAD_RE.match(h):
                    continue
                if DATE_LEAD_RE.match(h).group(1) in protected:
                    continue
                if handoff_i is not None and i >= handoff_i:
                    continue
                chosen = i  # 只取最旧一段（整段不可切分），剩余下轮
                break
            if chosen is None:
                notes.append("超阈值但无可归档段（保护日/交接段占用），note 记录不归档")
            else:
                # 按段边界精确切片（防重复行误配）
                pos = 0
                for i, (h, sec_lines) in enumerate(sections):
                    end = pos + len(sec_lines)
                    if i == chosen:
                        for j in range(pos, end):
                            remove_idx.add(j)
                        move.append((f"progress：{h}", lines[pos:end]))
                    pos = end
                notes.append("整段不可切分，一轮只归档最旧一段；仍超限时下轮再归")
    elif blocks:
        # 形态 A：里程节式/兜底条目块——entries > keep 即裁到 keep（同 --rotate 口径）
        if entries > keep:
            drop = blocks[:entries - keep]  # 文件序=最旧在前
            for s, e in drop:
                for i in range(s, e):
                    remove_idx.add(i)
            move.append((f"progress：最旧 {len(drop)} 条（超出保留 {keep}）",
                         [ln for s, e in drop for ln in lines[s:e]]))
            if over_bytes:
                notes.append(f"裁到 {keep} 条后体量仍 ≥40KB（单条超长），keep 硬语义优先")
        elif over_bytes or over_entries:
            notes.append(f"条目 {entries} ≤ keep {keep} 但字节/条目超限（单条超长），"
                         "无可裁条目，note 记录")
        else:
            notes.append(f"条目 {entries} ≤ keep {keep}，不归档")
    else:
        notes.append(f"条目 {entries}、字节 {size}，无条目块/日期段，未达可归档形态")

    keep_lines = [ln for i, ln in enumerate(lines) if i not in remove_idx]
    return {"move": move, "notes": notes, "reject": False,
            "before": len(lines), "after": len(keep_lines), "keep": keep_lines}


# ---------- 归档文件 ----------


def archive_path(root: Path, task: str, filename: str, now: datetime) -> Path:
    day = now.strftime("%Y%m%d")
    stem = Path(filename).stem
    return root / ".planning" / ARCHIVE_DIRNAME / f"{task}-{stem}-{day}.md"


def append_archive(path: Path, header_lines: list[str], body: list[str]) -> None:
    """不存在则带头创建；存在则校验 BODY 标记后在 end 前追加分段。"""
    seg = ["", "---", ""] + header_lines + ["", BODY_START] + body + [BODY_END, ""]
    if not path.exists():
        content = (f"# plan 段级归档（{path.stem}）\n\n"
                   + "\n".join(seg[3:]).lstrip("\n") + "\n")
        atomic_write(path, content.encode("utf-8"))
        return
    text = path.read_text(encoding="utf-8")
    if BODY_START not in text or BODY_END not in text:
        raise ValueError(f"归档文件缺 BODY 标记，不可安全追加：{path}")
    idx = text.index(BODY_END)
    new = text[:idx] + "\n".join(seg).lstrip("\n") + "\n" + text[idx:]
    atomic_write(path, new.encode("utf-8"))


def seg_header(now: datetime, rel: str, owner: str, before: int, after: int,
               sha: str, moves: list[tuple[str, list[str]]]) -> list[str]:
    rows = sum(len(body) for _, body in moves)
    return [f"## {now:%Y-%m-%d %H:%M} 归档（{Path(rel).name}）",
            f"- 来源：`{rel}`",
            f"- 操作者：{owner}",
            f"- 截止边界：{'；'.join(name for name, _ in moves)}",
            f"- before：{before} 行（源 sha256 `{sha[:16]}…`）；after（live）：{after} 行；本段归档 {rows} 行"]


def process_file(root: Path, task: str, filename: str, owner: str, now: datetime,
                 keep: int, apply: bool) -> tuple[bool, list[str]]:
    """处理单个 plan 文件；返回 (是否有归档动作, 输出行)。异常抛 ValueError（拒绝）。"""
    rel = f".planning/{task}/{filename}"
    path = root / rel
    out: list[str] = []
    if not path.is_file():
        raise ValueError(f"文件不存在：{rel}")
    raw_before = read_bytes(path)
    sha_before = sha256_bytes(raw_before)
    text = raw_before.decode("utf-8")
    lines = text.splitlines()
    if filename == "task_plan.md":
        plan = plan_task_plan(rel, lines)
    else:
        plan = plan_progress(rel, lines, keep)
    for note in plan["notes"]:
        out.append(f"  [note] {note}")
    if plan.get("reject"):
        raise ValueError(f"{rel} 结构异常，拒绝归档（live 未改）")
    if not plan["move"]:
        out.append(f"  无可归档段（{Path(rel).name} {plan['before']} 行，达标）")
        return False, out
    for name, body in plan["move"]:
        out.append(f"  将归档 {Path(rel).name}：{name}（{len(body)} 行）")
    # 指针行口径（§5.2「live 指针：尾部留一行」）：末行已是指针行时原地更新、不再追加，
    # 否则二次归档对账按 +1 必误报且指针行逐次累积
    tail_is_pointer = bool(plan["keep"]) and plan["keep"][-1].startswith(POINTER_PREFIX)
    after_with_pointer = plan["after"] + (0 if tail_is_pointer else 1)
    out.append(f"  {Path(rel).name}：{plan['before']} 行 → {after_with_pointer} 行"
               f"（含归档指针{'（原地更新）' if tail_is_pointer else ''}；before {len(raw_before)}B）")
    if not apply:
        return True, out

    # TOCTOU 复核：规划后重读源，sha 变了则 abort
    raw_re = read_bytes(path)
    if sha256_bytes(raw_re) != sha_before:
        raise ValueError(f"{rel} 在规划后被外部改动，abort（live 未改）")
    target = archive_path(root, task, filename, now)
    target.parent.mkdir(parents=True, exist_ok=True)
    header = seg_header(now, rel, owner, plan["before"], after_with_pointer,
                        sha_before, plan["move"])
    append_archive(target, header, [ln for _, body in plan["move"] for ln in body])
    pointer = (f"{POINTER_PREFIX}{now:%Y-%m-%d %H:%M} 移出 "
               f"{sum(len(b) for _, b in plan['move'])} 行 → `{target.relative_to(root)}`")
    keep_lines = plan["keep"][:-1] if tail_is_pointer else plan["keep"]
    new_text = "\n".join(keep_lines) + ("\n" if text.endswith("\n") or keep_lines else "")
    new_text += pointer + "\n"
    atomic_write(path, new_text.encode("utf-8"))
    raw_after = read_bytes(path)
    kept_sha = sha256_bytes(raw_after)
    # 守恒对账：写盘后复读 live 行数须等于规划 after（含指针行）；归档行+live 行 == 原行
    after_lines = len(raw_after.decode("utf-8").splitlines())
    archived_rows = sum(len(b) for _, b in plan["move"])
    if after_lines != after_with_pointer:
        raise ValueError(f"{rel} 写盘后行数 {after_lines} ≠ 规划 {after_with_pointer}，须人工核对")
    ok = plan["after"] + archived_rows == plan["before"]
    out.append(f"  已归档 → `{target.relative_to(root)}`（本段 {archived_rows} 行）")
    out.append(f"  守恒：live {plan['after']} 行(+指针) + 归档 {archived_rows} 行 "
               f"{'=' if ok else '≠'} 原 {plan['before']} 行；live sha `{kept_sha[:16]}…`")
    if not ok:
        raise ValueError(f"{rel} 守恒对账失败（归档已写，live 已改写，须人工核对）")
    return True, out


def run(args: argparse.Namespace) -> int:
    root = Path(args.root).resolve()
    planning = root / ".planning"
    if not planning.is_dir():
        return fail(f"未找到 .planning：{root}")
    if args.apply and (not args.task or not args.owner):
        return fail("--apply 必须显式 --task <slug> 与 --owner <名>")
    if args.apply and args.task == "all":
        return fail("拒绝 --task all --apply（禁止无差别全量归档）")
    task_dir = planning / args.task
    if not task_dir.is_dir():
        return fail(f"流目录不存在：{task_dir}")
    now = datetime.now()
    mode = "APPLY" if args.apply else "dry-run"
    print(f"[{mode}] task={args.task} keep={args.keep} root={root}")

    lock_path = root / LOCK_REL
    if args.apply:
        # 锁释放采用 unlink（不残留，§5.2）：unlink 与持锁释放间存在竞态窗口
        # （后到者可能对 unlink 后新建的同名 inode 拿锁，而本进程尚在返回路径），
        # 但本脚本所有写盘动作在持锁段内已完成，窗口内无写盘，风险可接受（CASE 版同款口径）。
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            lock_fh = open(lock_path, "w")
            fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            return fail(f"锁 {LOCK_REL} 被占用（写者活跃禁止 apply，§5.2）：{exc}")
    try:
        targets = [f for f in ("task_plan.md", "progress.md") if (task_dir / f).is_file()]
        if args.task == "portfolio":
            # §5.2 归属：portfolio progress 归 plan-status --rotate（条目最新在顶部，
            # 与本脚本「最新在底部」裁剪方向不符，防裁错方向不处理）
            targets = [f for f in targets if f != "progress.md"]
            if not targets:
                return fail("portfolio progress 归 plan-status.py --rotate（§5.2），"
                            "plan-archive 只处理 portfolio task_plan.md")
            print("[note] portfolio progress 跳过（归 plan-status.py --rotate，§5.2）")
        if not targets:
            return fail(f"{task_dir} 内无 task_plan.md / progress.md")
        any_moved = False
        for filename in targets:
            print(f"== {filename}")
            try:
                moved, out = process_file(root, args.task, filename, args.owner,
                                          now, args.keep, args.apply)
            except ValueError as exc:
                print(f"  [拒绝] {exc}", file=sys.stderr)
                return 1
            for line in out:
                print(line)
            any_moved = any_moved or moved
        if not args.apply:
            print("dry-run 完成（零写入）。确认后加 --apply --owner <名> 执行。")
        elif not any_moved:
            print("无可归档段，零改动（幂等）。")
        return 0
    finally:
        if args.apply and lock_path.exists():
            try:
                lock_fh.close()
                lock_path.unlink()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="plan 段级归档执行器（流 task_plan/progress；默认 dry-run）")
    parser.add_argument("--task", required=True,
                        help="流 slug（.planning/<slug>/）；禁止 all")
    parser.add_argument("--owner", default="",
                        help="操作者（--apply 必填，写归档头）")
    parser.add_argument("--apply", action="store_true",
                        help="真实归档（必须显式；默认 dry-run 零写入）")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP,
                        help=f"progress 保留最近条目数（默认 {DEFAULT_KEEP}）")
    parser.add_argument("--root", default=".",
                        help="仓根（默认 cwd 向上语义：直接指定含 .planning 的根）")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.keep < 1:
        return fail("--keep 须 ≥1")
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
