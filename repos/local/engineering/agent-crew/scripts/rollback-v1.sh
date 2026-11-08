#!/usr/bin/env bash
# agent-crew 回滚包：按 swap-to-v2.sh 生成的 manifest 校验并恢复受管文件。
# 只恢复备份清单内文件；备份后新建的文件只报告、不删除。
set -euo pipefail

usage() {
  cat <<'EOF'
用法：bash scripts/rollback-v1.sh <project-root> <backup-dir>

按 <backup-dir>/manifest.txt 恢复受管文件：
  1) 先逐文件校验备份存在且 sha256 与清单一致；任一失败即拒绝恢复（不写任何文件）；
  2) 恢复后逐文件复算 sha256，确保逐字节还原；
  3) 报告备份后新建、不在清单内、因此不回滚的文件（不删除）。
退出码：0 成功；1 参数/环境错误；2 manifest/备份校验失败（拒绝恢复）；3 恢复写入失败。
EOF
}

die() { printf '错误：%s\n' "$*" >&2; exit 1; }
sha256_of() { sha256sum -- "$1" | awk '{print $1}'; }

safe_rel() {
  local rel="$1"
  [[ -n "$rel" ]] || return 1
  [[ "$rel" != /* ]] || return 1
  [[ "/$rel/" != *"/../"* ]] || return 1
  [[ "$rel" != *$'\n'* ]] || return 1
  return 0
}

if [[ $# -ge 1 ]]; then
  case "$1" in
    -h|--help) usage; exit 0 ;;
  esac
fi
[[ $# -eq 2 ]] || { usage >&2; die "需要 <project-root> 与 <backup-dir> 两个参数"; }

PROJECT_ROOT="$1"
BACKUP_DIR="$2"
[[ -d "$PROJECT_ROOT" ]] || die "项目目录不存在：$PROJECT_ROOT"
PROJECT_ROOT="$(cd -- "$PROJECT_ROOT" && pwd)"
[[ -d "$BACKUP_DIR" ]] || die "备份目录不存在：$BACKUP_DIR"
MANIFEST="$BACKUP_DIR/manifest.txt"
[[ -f "$MANIFEST" ]] || die "manifest 不存在：$MANIFEST"
FIRST_LINE="$(head -n 1 -- "$MANIFEST")"
[[ "$FIRST_LINE" == "agent-crew swap manifest v1" ]] \
  || die "manifest 版本不支持（首行：$FIRST_LINE）"

SCOPE_DIRS=()
SCOPE_FILES=()
SCOPE_SCRIPTS=()
FILE_ROWS=()
while IFS= read -r line; do
  if [[ "$line" == FILE$'\t'* ]]; then
    FILE_ROWS+=("$line")
  elif [[ "$line" == scope_dir=* ]]; then
    SCOPE_DIRS+=("${line#scope_dir=}")
  elif [[ "$line" == scope_file=* ]]; then
    SCOPE_FILES+=("${line#scope_file=}")
  elif [[ "$line" == scope_script=* ]]; then
    SCOPE_SCRIPTS+=("${line#scope_script=}")
  fi
done < "$MANIFEST"

# —— 校验：任一失败拒绝恢复，零写入 ——
errors=0
for row in "${FILE_ROWS[@]}"; do
  IFS=$'\t' read -r _tag hash rel <<< "$row"
  if ! safe_rel "$rel"; then
    echo "  [校验失败] 非法路径：$rel" >&2
    errors=$((errors + 1))
    continue
  fi
  backup_file="$BACKUP_DIR/files/$rel"
  if [[ ! -f "$backup_file" ]]; then
    echo "  [校验失败] 备份文件缺失：$rel" >&2
    errors=$((errors + 1))
    continue
  fi
  if [[ "$(sha256_of "$backup_file")" != "$hash" ]]; then
    echo "  [校验失败] sha256 不一致：$rel" >&2
    errors=$((errors + 1))
  fi
done
if [[ "$errors" -gt 0 ]]; then
  echo "校验失败：$errors 处问题，拒绝恢复（未写任何文件）。" >&2
  exit 2
fi

# —— 恢复：逐文件复制并复算 sha256 ——
restored=0
declare -A BACKED=()
for row in "${FILE_ROWS[@]}"; do
  IFS=$'\t' read -r _tag hash rel <<< "$row"
  BACKED["$rel"]=1
  target="$PROJECT_ROOT/$rel"
  mkdir -p -- "$(dirname -- "$target")"
  cp -a -- "$BACKUP_DIR/files/$rel" "$target"
  if [[ "$(sha256_of "$target")" != "$hash" ]]; then
    echo "[恢复失败] $rel：sha256 复算不一致（期望 $hash）" >&2
    exit 3
  fi
  restored=$((restored + 1))
done

# —— 报告备份后新建、未回滚的文件（只报告，不删除）——
NEW_FILES=()
report_new() {
  local rel="$1"
  if [[ -n "${BACKED[$rel]:-}" ]]; then
    return 0
  fi
  NEW_FILES+=("$rel")
}

for dir in "${SCOPE_DIRS[@]}"; do
  [[ -d "$PROJECT_ROOT/$dir" ]] || continue
  while IFS= read -r -d '' path; do
    rel="${path#"$PROJECT_ROOT/"}"
    report_new "$rel"
  done < <(find "$PROJECT_ROOT/$dir" -type f -print0)
done
for file in "${SCOPE_FILES[@]}"; do
  [[ -f "$PROJECT_ROOT/$file" ]] && report_new "$file"
done
for name in "${SCOPE_SCRIPTS[@]}"; do
  [[ -f "$PROJECT_ROOT/scripts/$name" ]] && report_new "scripts/$name"
done

echo "回滚完成：已恢复 $restored 个受管文件（sha256 校验通过）。"
if [[ ${#NEW_FILES[@]} -gt 0 ]]; then
  echo "备份后新建且未回滚的文件（共 ${#NEW_FILES[@]} 个）："
  for rel in "${NEW_FILES[@]}"; do
    printf '  - %s（备份后新建，不在清单；未删除）\n' "$rel"
  done
else
  echo "备份后新建且未回滚的文件：无"
fi
echo "备份仍保留：$BACKUP_DIR"
