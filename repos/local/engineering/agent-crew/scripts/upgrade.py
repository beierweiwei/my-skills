#!/usr/bin/env python3
"""upgrade.py — v2.1 受管脚本升级 / 回滚（只本地，不做 3-way/baseline）。

范围（arch-proposal-v1 §8.2）：
- 受管脚本集合 = 安装源 `scripts/init.py` 的 SCRIPT_MANIFEST ∪ manifest 中
  kind:file 条目（scripts/*.py|*.sh）：源新增脚本会被安装并补登 manifest entries
  （T34，修复 2.2.14 O1「doctor warn → 只能 upgrade」死路）；不碰 AGENTS 块、
  项目角色文件、项目文案、规则/验收（项目无副本）。
- `--diff`：只读列出脚本内容/版本差异；`--apply --confirm`：加锁 → 备份到
  <common>/crew/backups/<UTC>/ + backup-manifest.json（含本版新增脚本 addedFiles）
  → 原子替换 → 复核受管脚本与安装源一致（不一致 rc=1、不写版本）→ 同步 config 的
  skillVersion（仅此一项）→ 补登/更新 manifest entries 哈希 → 版本 → install-log。
- `--rollback <backup-id>`：按备份 manifest 逐字节还原受管脚本并校验 sha256；
  本版新增（备份中不存在）的脚本在校验未被改动后备份到备份目录 `removed/` 再删除、
  同步移除 manifest 条目；被改动则拒绝回滚（不静默丢文件）。
- 不做 3-way、不做 baseline、不做路径/其他配置迁移；除 skillVersion 外不改 orchestration.json。

退出码：0 成功；1 校验/状态错误；2 待人工（缺 --confirm / 锁）；3 环境缺失。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # 不在项目 scripts/ 留 __pycache__
sys.path.insert(0, str(Path(__file__).resolve().parent))
from crew_paths import CrewPathsError, crew_dir  # noqa: E402

import init as init_lib  # noqa: E402


class UpgradeError(Exception):
    pass


def utc_now() -> str:
    return init_lib.utc_now()


def source_root(root: Path) -> Path:
    return init_lib.resolve_source(root)


def skill_version(root: Path) -> str:
    return init_lib.read_skill_version(source_root(root))


def source_text(name: str, source_dir: Path) -> str | None:
    path = source_dir / "scripts" / name
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def source_script_manifest(source_dir: Path) -> list[str]:
    """安装源声称的受管脚本清单（AST 读取 <source>/scripts/init.py）。

    用项目已安装的 upgrade.py 跑升级时，本进程 import 的 init 是旧清单；源新增的
    受管脚本只有读源清单才能发现（T34）。读取失败或结构不符时回退本地清单。
    """
    try:
        tree = ast.parse(
            (source_dir / "scripts" / "init.py").read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return list(init_lib.SCRIPT_MANIFEST)
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not (isinstance(target, ast.Name) and target.id == "SCRIPT_MANIFEST"):
            continue
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, SyntaxError):
            break
        if isinstance(value, list) and all(isinstance(name, str) for name in value):
            return value
        break
    return list(init_lib.SCRIPT_MANIFEST)


def managed_script_names(manifest: dict, source_names: list[str]) -> list[str]:
    """受管脚本集合 = 源 SCRIPT_MANIFEST ∪ manifest kind:file 条目（去重，源清单在前）。"""
    names: list[str] = []
    seen: set[str] = set()
    for name in source_names:
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    for entry in manifest.get("entries", []):
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            continue
        rel = str(entry.get("path") or "")
        if rel.startswith("scripts/") and rel.endswith((".py", ".sh")):
            name = Path(rel).name
            if name and name not in seen:
                seen.add(name)
                names.append(name)
    return names


def missing_source_scripts(source_dir: Path, source_names: list[str]) -> list[str]:
    """源清单中安装源缺失的受管脚本相对路径（T33 覆盖集缺口）。"""
    return [
        (Path("scripts") / name).as_posix()
        for name in source_names
        if not (source_dir / "scripts" / name).is_file()
    ]


def managed_script_mismatches(root: Path, source_dir: Path,
                              names: list[str]) -> list[str]:
    """给定覆盖集内受管脚本与安装源内容不一致的相对路径（B-1 复核口径）。

    只比较源侧存在的脚本；复制后复核阶段项目侧缺失也计为不一致（漏装/写盘
    异常不应假绿）。比较口径与 manifest 一致（sha256_text：CRLF 归一化）。
    """
    mismatches: list[str] = []
    for name in names:
        rel = Path("scripts") / name
        origin = source_dir / rel
        if not origin.is_file():
            continue
        target = root / rel
        if not target.is_file():
            mismatches.append(rel.as_posix())
            continue
        try:
            same = (init_lib.sha256_text(target.read_text(encoding="utf-8"))
                    == init_lib.sha256_text(origin.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError):
            same = False
        if not same:
            mismatches.append(rel.as_posix())
    return mismatches


def names_preview(names: list[str], limit: int = 5) -> str:
    preview = "、".join(names[:limit])
    return preview + ("…" if len(names) > limit else "")


def diff_report(root: Path, manifest: dict, version: str, source_dir: Path) -> dict:
    installed_version = manifest.get("skillVersion")
    rows: list[dict] = []
    for name in managed_script_names(manifest, source_script_manifest(source_dir)):
        content = source_text(name, source_dir)
        if content is None:
            continue
        rel = Path("scripts") / name
        target = root / rel
        actual = None
        if target.is_file():
            actual = init_lib.sha256_text(target.read_text(encoding="utf-8"))
        expected = init_lib.sha256_text(content)
        status = "missing" if actual is None else ("same" if actual == expected else "changed")
        rows.append({"path": rel.as_posix(), "status": status,
                     "installedSha256": actual, "sourceSha256": expected})
    return {
        "root": str(root),
        "skillVersion": {"installed": installed_version, "source": version},
        "scripts": rows,
        "changed": [row["path"] for row in rows if row["status"] != "same"],
        "managedOnly": True,
    }


def print_diff(report: dict) -> None:
    version = report["skillVersion"]
    print(f"版本：installed={version['installed']} source={version['source']}")
    for row in report["scripts"]:
        mark = {"same": "=", "changed": "~", "missing": "+"}[row["status"]]
        print(f"[{mark}] {row['path']}")
    if not report["changed"]:
        print("受管脚本已是最新。")


def _load_manifest(root: Path) -> dict:
    manifest = init_lib.read_manifest(root)
    if manifest is None:
        raise UpgradeError(f"缺少 {init_lib.MANIFEST_REL.as_posix()}；先跑 init")
    return manifest


def _write_manifest(root: Path, manifest: dict) -> None:
    init_lib.atomic_write_text(
        root / init_lib.MANIFEST_REL,
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    )


def _refresh_manifest_hashes(root: Path, manifest: dict) -> None:
    for entry in manifest.get("entries", []):
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            continue
        target = root / str(entry.get("path") or "")
        if target.is_file():
            entry["sha256"] = init_lib.sha256_text(target.read_text(encoding="utf-8"))


def _sync_manifest_file_entries(root: Path, manifest: dict,
                                source_names: list[str]) -> list[str]:
    """补登源受管清单中项目已存在但 manifest 未登记的 file 条目；返回新增路径。"""
    entries = [entry for entry in manifest.get("entries", [])
               if isinstance(entry, dict)]
    known = {str(entry.get("path") or "") for entry in entries
             if entry.get("kind") == "file"}
    added: list[str] = []
    for name in source_names:
        rel = Path("scripts") / name
        target = root / rel
        if not name or rel.as_posix() in known or not target.is_file():
            continue
        entries.append({"kind": "file", "path": rel.as_posix(),
                        "sha256": init_lib.sha256_text(
                            target.read_text(encoding="utf-8"))})
        known.add(rel.as_posix())
        added.append(rel.as_posix())
    manifest["entries"] = entries
    return added


def _config_skill_version(config_path: Path) -> str | None:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return config.get("skillVersion") if isinstance(config, dict) else None


def apply_upgrade(root: Path, confirm: bool, version: str, source_dir: Path) -> int:
    manifest = _load_manifest(root)
    if not confirm:
        print("upgrade --apply 需要 --confirm（不会自动覆盖受管脚本）。")
        return 2
    source_names = source_script_manifest(source_dir)
    # T33 覆盖集缺口：源缺失受管脚本时不得早退/写版本（否则版本假绿）
    missing = missing_source_scripts(source_dir, source_names)
    if missing:
        print(
            f"upgrade 中止：安装源缺失受管脚本 {len(missing)} 项"
            f"（{names_preview(missing)}）；未写入版本 {version}。请修复技能源后重试。",
            file=sys.stderr)
        return 1
    report = diff_report(root, manifest, version, source_dir)
    config_path = root / init_lib.CONFIG_REL
    config_stale = (
        config_path.is_file() and _config_skill_version(config_path) != version)
    if (not report["changed"] and manifest.get("skillVersion") == version
            and not config_stale):
        print("受管脚本已是最新，无需升级。")
        hint = init_lib.guard_absent_hint(root)
        if hint:
            print(hint)
        return 0
    registered: list[str] = []
    try:
        with init_lib.init_lock(root):
            manifest = _load_manifest(root)
            names = [Path(row["path"]).name for row in report["scripts"]
                     if row["status"] != "same"]
            backup_paths = [root / "scripts" / name for name in names]
            if config_path.is_file():
                backup_paths.append(config_path)
            before: dict[str, str] = {}
            contents: dict[str, str] = {}
            for name in names:
                rel = Path("scripts") / name
                target = root / rel
                before[rel.as_posix()] = (
                    init_lib.sha256_text(target.read_text(encoding="utf-8"))
                    if target.is_file() else ""
                )
                content = source_text(name, source_dir)
                if content is not None:
                    contents[rel.as_posix()] = content
            # 本版新增脚本（升级前不存在）登记进备份，回滚据此清理（T34）
            added_files = [
                {"path": rel, "sha256": init_lib.sha256_text(contents[rel])}
                for rel in sorted(before) if not before[rel] and rel in contents
            ]
            backup_id = init_lib.create_backup(
                root, "upgrade", backup_paths,
                extra={"skillVersionBefore": manifest.get("skillVersion"),
                       "addedFiles": added_files})
            after: dict[str, str] = {}
            for name in names:
                rel = Path("scripts") / name
                target = root / rel
                content = contents.get(rel.as_posix())
                if content is None:
                    continue
                mode = 0o755 if rel.suffix == ".sh" else None
                init_lib.atomic_write_text(target, content, mode=mode)
                after[rel.as_posix()] = init_lib.sha256_text(
                    target.read_text(encoding="utf-8"))
            # T33/B-1 复核：复制后、写 manifest 版本前确认受管脚本已与安装源对齐。
            # 覆盖集=本进程 init 清单 ∪ 源 AST 清单 ∪ manifest entries：项目内旧
            # upgrade 只认本进程清单，源新增脚本必须由源侧清单补核，否则首升假绿。
            local_names = set(init_lib.SCRIPT_MANIFEST)
            mismatches = init_lib.script_source_mismatches(root, source_dir)
            extra_names = [
                name for name in managed_script_names(
                    manifest,
                    list(dict.fromkeys([*source_names, *init_lib.SCRIPT_MANIFEST])))
                if name not in local_names
            ]
            mismatches += managed_script_mismatches(root, source_dir, extra_names)
            if mismatches:
                print(
                    "upgrade 复核失败：受管脚本与安装源仍不一致"
                    f"（{len(mismatches)} 项，如 {'、'.join(mismatches[:3])}）；"
                    f"未写入版本 {version}。请检查安装源/写入权限后重试。",
                    file=sys.stderr)
                return 1
            # 版本戳同步（值更新，不改其他配置）；回滚时按备份逐字节还原
            if config_path.is_file():
                try:
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                except ValueError as exc:
                    raise UpgradeError(f"配置损坏：{exc}") from exc
                if isinstance(config, dict):
                    config["skillVersion"] = version
                    init_lib.atomic_write_text(
                        config_path, json.dumps(config, ensure_ascii=False, indent=2) + "\n")
            if names:
                try:
                    manifest["composedAgents"] = init_lib.regenerate_composed_agents(
                        root, skill_root=source_dir)
                except init_lib.InitError as exc:
                    raise UpgradeError(f"组合基础代理重建失败：{exc}") from exc
            registered = _sync_manifest_file_entries(root, manifest, source_names)
            _refresh_manifest_hashes(root, manifest)
            manifest["skillVersion"] = version
            _write_manifest(root, manifest)
            init_lib.append_install_log(root, {
                "ts": utc_now(),
                "action": "upgrade",
                "operator": os.environ.get("USER") or "unknown",
                "projectId": manifest.get("projectId"),
                "skillVersion": version,
                "root": str(root),
                "backupId": backup_id,
                "files": [
                    {"path": rel, "kind": "file", "status": "updated",
                     "beforeSha": before.get(rel, ""), "afterSha": after.get(rel, "")}
                    for rel in sorted(before)
                ],
                "conflicts": [],
                "result": "ok",
                "next": ("skillVersion 已自动同步为源版本；如需刷新 AGENTS 编排块，"
                         "运行 python3 scripts/init.py --refresh-block --confirm"),
            })
    except init_lib.InitLocked:
        print("upgrade: 另一个 init/upgrade 正在运行（locked）。", file=sys.stderr)
        return 2
    except (OSError, UpgradeError, init_lib.InitError) as exc:
        print(f"upgrade 失败：{exc}", file=sys.stderr)
        return 1
    if report["changed"]:
        extra = f"；补登 {len(registered)} 个受管脚本条目" if registered else ""
        print(f"upgrade 完成：{len(report['changed'])} 个受管脚本更新{extra}；"
              f"backup={backup_id}")
    elif registered:
        print(f"upgrade 完成：补登 {len(registered)} 个受管脚本条目；backup={backup_id}")
    else:
        print(f"upgrade 完成：仅同步版本戳 {version}；backup={backup_id}")
    hint = init_lib.guard_absent_hint(root)
    if hint:
        print(hint)
    return 0


def rollback(root: Path, backup_id: str) -> int:
    manifest = _load_manifest(root)
    try:
        crew = crew_dir(root)
    except CrewPathsError as exc:
        print(f"rollback: {exc}", file=sys.stderr)
        return 3
    directory = crew / "backups" / backup_id
    backup_manifest = directory / "backup-manifest.json"
    if not backup_manifest.is_file():
        print(f"rollback: 备份不存在：{directory}", file=sys.stderr)
        return 1
    try:
        data = json.loads(backup_manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"rollback: 备份 manifest 损坏：{exc}", file=sys.stderr)
        return 1
    files = data.get("files") if isinstance(data.get("files"), list) else []
    restored: list[str] = []
    removed: list[str] = []
    # 本版新增脚本（升级前不存在）：回滚恢复升级前状态时移除；被改动则拒绝（T34）
    added: list[tuple[str, str]] = []
    for item in data.get("addedFiles") if isinstance(data.get("addedFiles"), list) else []:
        if not isinstance(item, dict):
            continue
        rel = str(item.get("path") or "")
        if not (rel.startswith("scripts/") and rel.endswith((".py", ".sh"))) \
                or ".." in Path(rel).parts:
            continue
        added.append((rel, str(item.get("sha256") or "").removeprefix("sha256:")))
    try:
        with init_lib.init_lock(root):
            for rel, expected in added:
                target = root / rel
                if not target.is_file():
                    continue
                try:
                    current = init_lib.sha256_text(target.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError) as exc:
                    raise UpgradeError(f"本版新增脚本读取失败：{rel}（{exc}）") from exc
                if expected and current != expected:
                    raise UpgradeError(
                        f"本版新增脚本已被修改：{rel}；为避免丢失改动，未执行回滚。"
                        "请先备份或移走该文件后重试")
            for entry in files:
                rel = str(entry.get("path") or "")
                if not (rel.startswith("scripts/") or rel == init_lib.CONFIG_REL.as_posix()):
                    continue
                source = directory / rel
                if not source.is_file():
                    raise UpgradeError(f"备份缺文件：{rel}")
                expected = str(entry.get("sha256") or "").removeprefix("sha256:")
                actual = init_lib.sha256_file_raw(source)
                if expected and actual != expected:
                    raise UpgradeError(f"备份文件 hash 不一致：{rel}")
                target = root / rel
                init_lib.atomic_write(target, source.read_bytes(),
                                      mode=0o755 if target.suffix == ".sh" else None)
                restored.append(rel)
            for rel, _expected in added:
                target = root / rel
                if not target.is_file():
                    continue
                # 删除前备份到本次备份目录（X2）：removed/<rel>
                keep = directory / "removed" / rel
                try:
                    init_lib.atomic_write(keep, target.read_bytes())
                    target.unlink()
                except OSError as exc:
                    raise UpgradeError(f"本版新增脚本移除失败：{rel}（{exc}）") from exc
                removed.append(rel)
            if removed:
                entries = [entry for entry in manifest.get("entries", [])
                           if isinstance(entry, dict)]
                manifest["entries"] = [
                    entry for entry in entries
                    if not (entry.get("kind") == "file"
                            and str(entry.get("path") or "") in removed)]
            _refresh_manifest_hashes(root, manifest)
            if data.get("skillVersionBefore"):
                manifest["skillVersion"] = data["skillVersionBefore"]
            _write_manifest(root, manifest)
            init_lib.append_install_log(root, {
                "ts": utc_now(),
                "action": "rollback",
                "operator": os.environ.get("USER") or "unknown",
                "projectId": manifest.get("projectId"),
                "skillVersion": manifest.get("skillVersion"),
                "root": str(root),
                "backupId": backup_id,
                "files": [{"path": rel, "kind": "file", "status": "restored"}
                          for rel in restored]
                         + [{"path": rel, "kind": "file", "status": "removed"}
                            for rel in removed],
                "conflicts": [],
                "result": "ok",
                "next": "",
            })
    except init_lib.InitLocked:
        print("rollback: 另一个 init/upgrade 正在运行（locked）。", file=sys.stderr)
        return 2
    except (OSError, UpgradeError, init_lib.InitError) as exc:
        print(f"rollback 失败：{exc}", file=sys.stderr)
        return 1
    # 逐字节校验
    for entry in files:
        rel = str(entry.get("path") or "")
        if not (rel.startswith("scripts/") or rel == init_lib.CONFIG_REL.as_posix()):
            continue
        target = root / rel
        expected = str(entry.get("sha256") or "").removeprefix("sha256:")
        if expected and init_lib.sha256_file_raw(target) != expected:
            print(f"rollback 校验失败：{rel}", file=sys.stderr)
            return 1
    suffix = (f"、{len(removed)} 个本版新增脚本已移除" if removed else "")
    print(f"rollback 完成：{len(restored)} 个文件逐字节还原{suffix}（backup={backup_id}）")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="agent-crew 受管脚本升级/回滚（v2.1）")
    parser.add_argument("--root", help="目标项目仓根（默认 cwd）")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diff", action="store_true", help="只读列出版本/脚本差异")
    mode.add_argument("--apply", action="store_true", help="替换受管脚本（需 --confirm）")
    mode.add_argument("--rollback", metavar="BACKUP_ID", help="按备份逐字节还原")
    parser.add_argument("--confirm", action="store_true", help="确认写入")
    parser.add_argument("--json", action="store_true", help="JSON 输出")
    parser.add_argument("--out", help="把 JSON 结果写到该路径")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    if not root.is_dir():
        print(f"错误：目标目录不存在：{root}", file=sys.stderr)
        return 1
    source_dir = source_root(root)
    version = skill_version(root)
    try:
        if args.rollback:
            return rollback(root, args.rollback)
        manifest = _load_manifest(root)
        report = diff_report(root, manifest, version, source_dir)
        if args.diff:
            if args.json:
                payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
                if args.out:
                    init_lib.atomic_write_text(Path(args.out), payload)
                print(payload, end="")
            else:
                print_diff(report)
                if args.out:
                    init_lib.atomic_write_text(
                        Path(args.out), json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            return 0
        return apply_upgrade(root, args.confirm, version, source_dir)
    except UpgradeError as exc:
        print(f"upgrade: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
