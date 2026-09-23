#!/usr/bin/env bash
set -uo pipefail

# Linux/macOS equivalent of install-skills.ps1. Copies selected skills from the
# layered library (skills/<layer>/<skill>) into an agent skill directory.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$ROOT/skills"
DEST_DIR="$HOME/.agents/skills"
DEFAULT_LAYERS=(engineering productivity)
NEVER_LAYERS=(deprecated)

LIST=0; ALL=0; DRY_RUN=0
PICK_SKILLS=(); PICK_LAYERS=()

usage() {
  cat <<'EOF'
用法: install-skills.sh [选项]

  (无选项)            安装生产层（engineering、productivity）全部技能
  --skill A,B         只安装指定技能（逗号分隔，可重复）
  --layer LAYER       只安装指定层（可重复）
  --all               安装除 deprecated 外的全部层
  --list              列出库中技能与安装状态
  --dest DIR          目标目录（默认 ~/.agents/skills）
  --dry-run           只预览，不写入
  -h, --help          显示本帮助
EOF
}

while (($#)); do
  case "$1" in
    --skill) (($# >= 2)) || { usage >&2; exit 2; }; IFS=',' read -r -a _s <<< "$2"; PICK_SKILLS+=("${_s[@]}"); shift 2 ;;
    --layer) (($# >= 2)) || { usage >&2; exit 2; }; PICK_LAYERS+=("$2"); shift 2 ;;
    --all) ALL=1; shift ;;
    --list) LIST=1; shift ;;
    --dest) (($# >= 2)) || { usage >&2; exit 2; }; DEST_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

contains() { local needle="$1"; shift; local x; for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done; return 1; }

LAYERS=(); NAMES=(); PATHS=(); DESCS=()
while IFS= read -r skill_md; do
  dir="$(dirname -- "$skill_md")"
  layer="$(basename -- "$(dirname -- "$dir")")"
  name="$(basename -- "$dir")"
  desc="$(sed -n 's/^description:[[:space:]]*//p' "$skill_md" | head -n 1 | cut -c1-64)"
  LAYERS+=("$layer"); NAMES+=("$name"); PATHS+=("$dir"); DESCS+=("$desc")
done < <(find -L "$SKILLS_DIR" -mindepth 3 -maxdepth 3 -name SKILL.md | sort)

if ((LIST)); then
  printf '目标目录: %s\n' "$DEST_DIR"
  for i in "${!NAMES[@]}"; do
    mark='[    ]'; [[ -e "$DEST_DIR/${NAMES[$i]}" ]] && mark='[已装]'
    printf '  %s %-14s %-34s  %s\n' "$mark" "${LAYERS[$i]}" "${NAMES[$i]}" "${DESCS[$i]}"
  done
  exit 0
fi

for n in "${PICK_SKILLS[@]:-}"; do
  [[ -n "$n" ]] || continue
  contains "$n" "${NAMES[@]:-}" || { printf 'Warning: 库中不存在，跳过: %s\n' "$n" >&2; }
done
for l in "${PICK_LAYERS[@]:-}"; do
  [[ -n "$l" ]] || continue
  if contains "$l" "${NEVER_LAYERS[@]}"; then printf 'Error: 层 %s 已淘汰，永不安装。\n' "$l" >&2; exit 2; fi
  contains "$l" "${LAYERS[@]}" || { printf 'Error: 层 %s 不存在。\n' "$l" >&2; exit 2; }
done

selected=()
for i in "${!NAMES[@]}"; do
  take=0
  if ((ALL)); then contains "${LAYERS[$i]}" "${NEVER_LAYERS[@]}" || take=1; fi
  if ((${#PICK_LAYERS[@]})) && contains "${LAYERS[$i]}" "${PICK_LAYERS[@]}"; then take=1; fi
  if ((${#PICK_SKILLS[@]})) && contains "${NAMES[$i]}" "${PICK_SKILLS[@]}"; then take=1; fi
  if ((!ALL && !${#PICK_LAYERS[@]} && !${#PICK_SKILLS[@]})); then contains "${LAYERS[$i]}" "${DEFAULT_LAYERS[@]}" && take=1; fi
  if contains "${LAYERS[$i]}" "${NEVER_LAYERS[@]}"; then take=0; fi
  ((take)) && selected+=("$i")
done

if ((${#selected[@]} == 0)); then
  printf '没有选中任何技能。\n' >&2
  exit 0
fi

installed=0; failed=()
for i in "${selected[@]}"; do
  target="$DEST_DIR/${NAMES[$i]}"
  if ((DRY_RUN)); then
    printf '  ~ %s/%s -> %s\n' "${LAYERS[$i]}" "${NAMES[$i]}" "$target"
    continue
  fi
  mkdir -p "$DEST_DIR"
  [[ -e "$target" || -L "$target" ]] && rm -rf -- "$target"
  if cp -R -- "${PATHS[$i]}" "$target"; then
    printf '  + %s/%s\n' "${LAYERS[$i]}" "${NAMES[$i]}"
    installed=$((installed + 1))
  else
    printf 'Warning: 安装失败 %s\n' "${NAMES[$i]}" >&2
    failed+=("${NAMES[$i]}")
  fi
done

printf '\n'
if ((DRY_RUN)); then
  printf '预览：将安装 %d 个技能到 %s（--dry-run 未实际写入）\n' "${#selected[@]}" "$DEST_DIR"
elif ((${#failed[@]})); then
  printf '完成 %d/%d，失败: %s\n' "$installed" "${#selected[@]}" "${failed[*]}" >&2
  exit 1
else
  printf '已安装 %d 个技能到 %s\n' "$installed" "$DEST_DIR"
fi