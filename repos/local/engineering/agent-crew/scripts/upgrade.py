#!/usr/bin/env python3
"""upgrade.py — v2.1 受管脚本升级 / 回滚（只本地，不做 3-way/baseline）。

范围（arch-proposal-v1 §8.2）：
- 只处理 manifest 中 kind:file 的受管脚本（scripts/*.py|*.sh）；不碰 AGENTS 块、
  项目角色文件、项目文案、规则/验收（项目无副本）。
- `--diff`：只读列出脚本内容/版本差异；`--apply --confirm`：加锁 → 备份到
  <common>/crew/backups/<UTC>/ + backup-manifest.json → 原子替换 → 同步 config 的
  skillVersion（仅此一项）→ 更新 manifest 哈希/版本 → install-log。
- `--rollback <backup-id>`：按备份 manifest 逐字节还原受管脚本并校验 sha256。
- 不做 3-way、不做 baseline、不做路径/其他配置迁移；除 skillVersion 外不改 orchestration.json。

退出码：0 成功；1 校验/状态错误；2 待人工（缺 --confirm / 锁）；3 环境缺失。
"""

from __future__ import annotations

import argparse
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


def managed_script_names(manifest: dict) -> list[str]:
    names: list[str] = []
    for entry in manifest.get("entries", []):
        if not isinstance(entry, dict) or entry.get("kind") != "file":
            continue
        rel = str(entry.get("path") or "")
        if rel.startswith("scripts/") and rel.endswith((".py", ".sh")):
            names.append(Path(rel).name)
    return names


def diff_report(root: Path, manifest: dict, version: str, source_dir: Path) -> dict:
    installed_version = manifest.get("skillVersion")
    rows: list[dict] = []
    for name in managed_script_names(manifest):
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


def apply_upgrade(root: Path, confirm: bool, version: str, source_dir: Path) -> int:
    manifest = _load_manifest(root)
    if not confirm:
        print("upgrade --apply 需要 --confirm（不会自动覆盖受管脚本）。")
        return 2
    report = diff_report(root, manifest, version, source_dir)
    if not report["changed"]:
        print("受管脚本已是最新，无需升级。")
        return 0
    try:
        with init_lib.init_lock(root):
            manifest = _load_manifest(root)
            names = [Path(row["path"]).name for row in report["scripts"]
                     if row["status"] != "same"]
            config_path = root / init_lib.CONFIG_REL
            backup_paths = [root / "scripts" / name for name in names]
            if config_path.is_file():
                backup_paths.append(config_path)
            backup_id = init_lib.create_backup(
                root, "upgrade", backup_paths,
                extra={"skillVersionBefore": manifest.get("skillVersion")})
            before: dict[str, str] = {}
            after: dict[str, str] = {}
            for name in names:
                rel = Path("scripts") / name
                target = root / rel
                before[rel.as_posix()] = (
                    init_lib.sha256_text(target.read_text(encoding="utf-8"))
                    if target.is_file() else ""
                )
                content = source_text(name, source_dir)
                if content is None:
                    continue
                mode = 0o755 if rel.suffix == ".sh" else None
                init_lib.atomic_write_text(target, content, mode=mode)
                after[rel.as_posix()] = init_lib.sha256_text(
                    target.read_text(encoding="utf-8"))
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
            try:
                manifest["composedAgents"] = init_lib.regenerate_composed_agents(
                    root, skill_root=source_dir)
            except init_lib.InitError as exc:
                raise UpgradeError(f"组合基础代理重建失败：{exc}") from exc
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
    print(f"upgrade 完成：{len(report['changed'])} 个受管脚本更新；backup={backup_id}")
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
    try:
        with init_lib.init_lock(root):
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
                          for rel in restored],
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
    print(f"rollback 完成：{len(restored)} 个文件逐字节还原（backup={backup_id}）")
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
