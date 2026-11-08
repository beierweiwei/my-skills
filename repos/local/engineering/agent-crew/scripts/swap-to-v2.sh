#!/usr/bin/env bash
# agent-crew CASE 替换包（v1 → v2）：默认先备份受管产物，再调用 v2 init.py --force 替换。
# 只覆盖受管路径；项目内非受管文件不删除、不覆盖。失败自动回滚（可用 --no-auto-rollback 关闭）。
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
INIT_PY_DEFAULT="$SKILL_DIR/scripts/init.py"
ROLLBACK_SH="$SCRIPT_DIR/rollback-v1.sh"
SWAP_VERSION="v2"

# 备份范围：受管角色目录 + 受管文件 + init.py SCRIPT_MANIFEST 中的脚本。
SCOPE_DIRS=(
  ".qoder/agents"
  ".opencode/agent"
  ".opencode/skills/firstmate-dispatch"
)
SCOPE_FILES=(
  "AGENTS.md"
  ".planning/orchestration.json"
  "CONTEXT.md"
  "docs/crew-rules.md"
  "docs/crew-acceptance.md"
)

usage() {
  cat <<'EOF'
用法：bash scripts/swap-to-v2.sh <project-root> [选项]

默认行为：先备份受管产物，再调用 v2 init.py --force 替换；失败自动回滚。
只覆盖受管路径；项目内非受管文件（如 process-auditor.md、自定义脚本）不删除、不覆盖。

选项：
  --dry-run            只输出将改动的文件与备份路径，不写任何文件
  --backup-dir DIR     备份目录（默认 $HOME/crew-backups/<项目名>-<UTC时间戳>）
  --init-py PATH       v2 init.py 路径（默认本技能 scripts/init.py）
  --no-auto-rollback   替换失败时不自动回滚（保留现场并输出回滚命令）
  -h, --help           显示本帮助

备份范围（仅这些受管产物）：
  .qoder/agents/  .opencode/agent/  .opencode/skills/firstmate-dispatch/
  scripts/<init.py SCRIPT_MANIFEST 清单>
  AGENTS.md  .planning/orchestration.json  CONTEXT.md
  docs/crew-rules.md  docs/crew-acceptance.md（后三个存在才备份）

退出码：0 成功；1 参数/环境错误；2 init 失败（已回滚或已保留现场）；3 自动回滚失败。
EOF
}

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
sha256_of() { sha256sum -- "$1" | awk '{print $1}'; }

read_script_manifest() {
  python3 - "$1" <<'PY'
import ast
import sys

src = open(sys.argv[1], encoding="utf-8").read()
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign) and any(
        getattr(target, "id", "") == "SCRIPT_MANIFEST" for target in node.targets
    ):
        value = ast.literal_eval(node.value)
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise SystemExit("SCRIPT_MANIFEST 格式异常")
        print("\n".join(value))
        break
else:
    raise SystemExit("init.py 中未找到 SCRIPT_MANIFEST")
PY
}

list_managed_files() {
  local root="$1" dir rel name path
  for dir in "${SCOPE_DIRS[@]}"; do
    [[ -d "$root/$dir" ]] || continue
    while IFS= read -r -d '' path; do
      rel="${path#"$root/"}"
      printf '%s\n' "$rel"
    done < <(find "$root/$dir" -type f -print0)
  done
  for rel in "${SCOPE_FILES[@]}"; do
    [[ -f "$root/$rel" ]] && printf '%s\n' "$rel"
  done
  for name in "${MANAGED_SCRIPTS[@]}"; do
    [[ -f "$root/scripts/$name" ]] && printf '%s\n' "scripts/$name"
  done
}

# reference=替换后（沙箱/期望）树，target=替换前（项目）树。
diff_managed_trees() {
  local reference="$1" target="$2" rel
  while IFS= read -r rel; do
    if [[ ! -e "$target/$rel" && -e "$reference/$rel" ]]; then
      printf '  [新增] %s\n' "$rel"
    elif [[ -e "$target/$rel" && -e "$reference/$rel" ]] && ! cmp -s "$target/$rel" "$reference/$rel"; then
      printf '  [更新] %s\n' "$rel"
    fi
  done < <({ list_managed_files "$target"; list_managed_files "$reference"; } | sort -u)
}

PROJECT_ROOT=""
DRY_RUN=0
BACKUP_DIR=""
INIT_PY="$INIT_PY_DEFAULT"
AUTO_ROLLBACK=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --backup-dir)
      [[ $# -ge 2 ]] || die "--backup-dir 缺少参数"
      BACKUP_DIR="$2"; shift 2 ;;
    --init-py)
      [[ $# -ge 2 ]] || die "--init-py 缺少参数"
      INIT_PY="$2"; shift 2 ;;
    --no-auto-rollback) AUTO_ROLLBACK=0; shift ;;
    -h|--help) usage; exit 0 ;;
    --*) die "未知选项：$1（见 --help）" ;;
    *)
      [[ -z "$PROJECT_ROOT" ]] || die "多余的参数：$1"
      PROJECT_ROOT="$1"; shift ;;
  esac
done

[[ -n "$PROJECT_ROOT" ]] || die "缺少 <project-root>（见 --help）"
[[ -d "$PROJECT_ROOT" ]] || die "项目目录不存在：$PROJECT_ROOT"
PROJECT_ROOT="$(cd -- "$PROJECT_ROOT" && pwd)"
[[ -f "$INIT_PY" ]] || die "v2 init.py 不存在：$INIT_PY"
[[ -f "$ROLLBACK_SH" ]] || die "rollback-v1.sh 不存在：$ROLLBACK_SH"
INIT_PY="$(cd -- "$(dirname -- "$INIT_PY")" && pwd)/$(basename -- "$INIT_PY")"

MANIFEST_OUTPUT="$(read_script_manifest "$INIT_PY")" \
  || die "无法解析 init.py 的 SCRIPT_MANIFEST：$INIT_PY"
mapfile -t MANAGED_SCRIPTS <<< "$MANIFEST_OUTPUT"
[[ ${#MANAGED_SCRIPTS[@]} -gt 0 ]] || die "SCRIPT_MANIFEST 为空：$INIT_PY"

PROJECT_NAME="$(basename -- "$PROJECT_ROOT")"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SKILL_REV="$(git -C "$SKILL_DIR" rev-parse --short HEAD 2>/dev/null || true)"
SKILL_REV="${SKILL_REV:-unknown}"
if [[ -z "$BACKUP_DIR" ]]; then
  BACKUP_DIR="$HOME/crew-backups/${PROJECT_NAME}-${TIMESTAMP}"
fi
BACKUP_ABS="$(realpath -m -- "$BACKUP_DIR")"
case "$BACKUP_ABS/" in
  "$PROJECT_ROOT"/*) die "备份目录不能位于项目目录内：$BACKUP_DIR" ;;
esac

if [[ "$DRY_RUN" -eq 1 ]]; then
  echo "dry-run：不写项目目录，也不创建备份目录。"
  sandbox="$(mktemp -d)"
  while IFS= read -r rel; do
    mkdir -p -- "$sandbox/$(dirname -- "$rel")"
    cp -a -- "$PROJECT_ROOT/$rel" "$sandbox/$rel"
  done < <(list_managed_files "$PROJECT_ROOT" | sort)
  if ! python3 "$INIT_PY" --root "$sandbox" --yes --force >"$sandbox/.init.log" 2>&1; then
    echo "错误：v2 init.py 在沙箱渲染失败，无法给出变更清单：" >&2
    tail -n 20 -- "$sandbox/.init.log" >&2 || true
    rm -rf -- "$sandbox"
    exit 2
  fi
  echo "将改动的受管文件："
  mapfile -t CHANGES < <(diff_managed_trees "$sandbox" "$PROJECT_ROOT")
  if [[ ${#CHANGES[@]} -eq 0 ]]; then
    echo "  （受管文件已是 v2 内容；正式执行仍会先备份）"
  else
    printf '%s\n' "${CHANGES[@]}"
  fi
  rm -rf -- "$sandbox"
  echo "备份路径（将创建）：$BACKUP_DIR"
  exit 0
fi

if [[ -e "$BACKUP_DIR" ]]; then
  [[ -d "$BACKUP_DIR" ]] || die "备份路径已存在且不是目录：$BACKUP_DIR"
  [[ -z "$(ls -A -- "$BACKUP_DIR")" ]] || die "备份目录已存在且非空，拒绝覆盖：$BACKUP_DIR"
fi
mkdir -p -- "$BACKUP_DIR/files"

MANIFEST_TMP="$BACKUP_DIR/.manifest.tmp"
{
  echo "agent-crew swap manifest v1"
  echo "swap_version=$SWAP_VERSION"
  echo "project=$PROJECT_NAME"
  echo "project_root=$PROJECT_ROOT"
  echo "skill_dir=$SKILL_DIR"
  echo "skill_rev=$SKILL_REV"
  echo "created_utc=$TIMESTAMP"
  echo "init_py=$INIT_PY"
  for dir in "${SCOPE_DIRS[@]}"; do echo "scope_dir=$dir"; done
  for file in "${SCOPE_FILES[@]}"; do echo "scope_file=$file"; done
  for name in "${MANAGED_SCRIPTS[@]}"; do echo "scope_script=$name"; done
} > "$MANIFEST_TMP"

while IFS= read -r rel; do
  src="$PROJECT_ROOT/$rel"
  dst="$BACKUP_DIR/files/$rel"
  mkdir -p -- "$(dirname -- "$dst")"
  cp -a -- "$src" "$dst"
  hash="$(sha256_of "$src")"
  [[ "$(sha256_of "$dst")" == "$hash" ]] || die "备份校验失败：$rel"
  printf 'FILE\t%s\t%s\n' "$hash" "$rel" >> "$MANIFEST_TMP"
done < <(list_managed_files "$PROJECT_ROOT" | sort)
mv -- "$MANIFEST_TMP" "$BACKUP_DIR/manifest.txt"
echo "备份完成：$BACKUP_DIR"
echo "清单：$BACKUP_DIR/manifest.txt"

set +e
python3 "$INIT_PY" --root "$PROJECT_ROOT" --yes --force 2>&1 | tee "$BACKUP_DIR/init.log"
INIT_RC=${PIPESTATUS[0]}
set -e

if [[ "$INIT_RC" -ne 0 ]]; then
  echo "错误：v2 init.py 执行失败（退出码 $INIT_RC）。" >&2
  if [[ "$AUTO_ROLLBACK" -eq 1 ]]; then
    if bash "$ROLLBACK_SH" "$PROJECT_ROOT" "$BACKUP_DIR"; then
      echo "已自动回滚：受管文件恢复为替换前状态。" >&2
      exit 2
    fi
    echo "严重：自动回滚失败。请人工执行：" >&2
    echo "  bash \"$ROLLBACK_SH\" \"$PROJECT_ROOT\" \"$BACKUP_DIR\"" >&2
    exit 3
  fi
  echo "已按 --no-auto-rollback 保留现场。回滚命令：" >&2
  echo "  bash \"$ROLLBACK_SH\" \"$PROJECT_ROOT\" \"$BACKUP_DIR\"" >&2
  exit 2
fi

echo "替换完成：受管文件已更新为 v2（非受管文件未动）。"
echo "备份路径：$BACKUP_DIR"
echo "变更文件："
declare -A BACKED=()
changed=0
while IFS=$'\t' read -r tag hash rel; do
  [[ "$tag" == "FILE" ]] || continue
  BACKED["$rel"]=1
  if [[ ! -f "$PROJECT_ROOT/$rel" ]]; then
    echo "  [缺失] $rel"
    changed=$((changed + 1))
  elif [[ "$(sha256_of "$PROJECT_ROOT/$rel")" != "$hash" ]]; then
    echo "  [更新] $rel"
    changed=$((changed + 1))
  fi
done < "$BACKUP_DIR/manifest.txt"
while IFS= read -r rel; do
  [[ -n "${BACKED[$rel]:-}" ]] && continue
  echo "  [新增] $rel"
  changed=$((changed + 1))
done < <(list_managed_files "$PROJECT_ROOT" | sort)
if [[ "$changed" -eq 0 ]]; then
  echo "  （受管文件无内容变化）"
fi
echo "回滚命令：bash \"$ROLLBACK_SH\" \"$PROJECT_ROOT\" \"$BACKUP_DIR\""
