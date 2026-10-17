#!/usr/bin/env bash
set -uo pipefail

# Linux/macOS equivalent of install-skills.ps1. Copies selected skills from the
# layered library (skills/<layer>/<skill>) into agent skill directories.
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
SKILLS_DIR="$ROOT/skills"
DEFAULT_LAYERS=(engineering productivity)
NEVER_LAYERS=(deprecated)

# 各 Agent 的技能目录约定（<基础目录>/skills）；检测规则与 link-agents 一致：
# 基础目录存在视为已安装。agents 是跨 Agent 共享目录（ZCode 也扫描这里）。
AGENT_NAMES=(agents claude codex qoder-cn opencode zcode)
AGENT_BASES=("$HOME/.agents" "$HOME/.claude" "$HOME/.codex" "$HOME/.qoder-cn" "$HOME/.config/opencode" "$HOME/.zcode")

LIST=0; ALL=0; DRY_RUN=0; SKIP_SETUP=0
DEST_DIR=""
PICK_SKILLS=(); PICK_LAYERS=(); PICK_AGENTS=()

usage() {
  cat <<'EOF'
用法: install-skills.sh [选项]

  (无选项)            检测本机已装 Agent，安装生产层（engineering、productivity）到全部已装 Agent
  --skill A,B         只安装指定技能（逗号分隔，可重复）
  --layer LAYER       只安装指定层（可重复）
  --agent A,B         只安装到指定 Agent（逗号分隔，可重复；未检测到的警告跳过）
  --all               安装除 deprecated 外的全部层
  --list              列出库中技能与各目标目录的安装状态
  --dest DIR          只安装到指定目录（覆盖 Agent 检测；与 --agent 同给时 --dest 优先）
  --dry-run           只预览，不写入
  --skip-setup        跳过技能自带的自举脚本（如 inone-memory 的 scripts/setup.sh）
  -h, --help          显示本帮助

Agent 检测规则与 link-agents 相同：基础目录（~/.agents、~/.claude、~/.codex、
~/.qoder-cn、~/.config/opencode、~/.zcode）存在视为已装，技能装到其 skills/ 子目录。
首次使用（或新克隆的机器）：先运行 scripts/update-skills.sh 生成 skills/ 下的技能链接，否则库为空。
EOF
}

while (($#)); do
  case "$1" in
    --skill) (($# >= 2)) || { usage >&2; exit 2; }; IFS=',' read -r -a _s <<< "$2"; PICK_SKILLS+=("${_s[@]}"); shift 2 ;;
    --layer) (($# >= 2)) || { usage >&2; exit 2; }; PICK_LAYERS+=("$2"); shift 2 ;;
    --agent) (($# >= 2)) || { usage >&2; exit 2; }; IFS=',' read -r -a _a <<< "$2"; PICK_AGENTS+=("${_a[@]}"); shift 2 ;;
    --all) ALL=1; shift ;;
    --list) LIST=1; shift ;;
    --dest) (($# >= 2)) || { usage >&2; exit 2; }; DEST_DIR="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --skip-setup) SKIP_SETUP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

contains() { local needle="$1"; shift; local x; for x in "$@"; do [[ "$x" == "$needle" ]] && return 0; done; return 1; }

# 解析安装目标目录：--dest 显式指定优先；否则按 Agent 检测（显式 --agent 只看指定项）
DESTS=()
resolve_dests() {
  if [[ -n "$DEST_DIR" ]]; then DESTS=("$DEST_DIR"); return; fi
  local wanted=() n i found
  if ((${#PICK_AGENTS[@]})); then wanted=("${PICK_AGENTS[@]}"); else wanted=("${AGENT_NAMES[@]}"); fi
  for n in "${wanted[@]}"; do
    found=0
    for i in "${!AGENT_NAMES[@]}"; do
      [[ "${AGENT_NAMES[$i]}" == "$n" ]] || continue
      found=1
      if [[ -d "${AGENT_BASES[$i]}" ]]; then
        DESTS+=("${AGENT_BASES[$i]}/skills")
      elif ((${#PICK_AGENTS[@]})); then
        printf 'Warning: Agent %s 未检测到（%s 不存在），跳过\n' "$n" "${AGENT_BASES[$i]}" >&2
      fi
      break
    done
    if (( ! found )); then
      printf 'Error: 未知 Agent: %s（可选: %s）\n' "$n" "${AGENT_NAMES[*]}" >&2
      exit 2
    fi
  done
  if ((${#DESTS[@]} == 0)); then
    if ((${#PICK_AGENTS[@]})); then
      printf 'Error: 指定的 Agent 均未检测到，无安装目标。\n' >&2
    else
      printf 'Error: 未检测到任何已安装的 Agent（基础目录: %s）。\n' "${AGENT_BASES[*]}" >&2
    fi
    exit 1
  fi
}
resolve_dests

LAYERS=(); NAMES=(); PATHS=(); DESCS=()
while IFS= read -r skill_md; do
  dir="$(dirname -- "$skill_md")"
  layer="$(basename -- "$(dirname -- "$dir")")"
  name="$(basename -- "$dir")"
  desc="$(sed -n 's/^description:[[:space:]]*//p' "$skill_md" | head -n 1 | cut -c1-64)"
  LAYERS+=("$layer"); NAMES+=("$name"); PATHS+=("$dir"); DESCS+=("$desc")
done < <(find -L "$SKILLS_DIR" -mindepth 3 -maxdepth 3 -name SKILL.md | sort)

if ((${#NAMES[@]} == 0)); then
  printf 'Error: 技能库为空——skills/ 下没有任何技能。\n' >&2
  printf '技能条目（自研 + 上游）由 update-skills 脚本生成的链接提供；首次使用（或新克隆的机器）请先运行：\n' >&2
  printf '  bash scripts/update-skills.sh [--proxy URL]\n' >&2
  exit 1
fi

if ((LIST)); then
  printf '目标目录:\n'
  for d in "${DESTS[@]}"; do printf '  %s\n' "$d"; done
  for i in "${!NAMES[@]}"; do
    count=0
    for d in "${DESTS[@]}"; do [[ -e "$d/${NAMES[$i]}" ]] && count=$((count + 1)); done
    if ((${#DESTS[@]} == 1)); then
      mark='[    ]'; ((count)) && mark='[已装]'
    else
      mark="[$count/${#DESTS[@]}]"
    fi
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
declare -A setup_seen=()
for dest in "${DESTS[@]}"; do
  printf '→ %s\n' "$dest"
  for i in "${selected[@]}"; do
    target="$dest/${NAMES[$i]}"
    if ((DRY_RUN)); then
      printf '  ~ %s/%s\n' "${LAYERS[$i]}" "${NAMES[$i]}"
      if [[ -f "${PATHS[$i]}/scripts/setup.sh" && -z "${setup_seen[${NAMES[$i]}]:-}" ]]; then
        printf '    ~ 自举: %s/scripts/setup.sh 将运行\n' "${NAMES[$i]}"
      fi
      continue
    fi
    mkdir -p "$dest"
    [[ -e "$target" || -L "$target" ]] && rm -rf -- "$target"
    # -L：源是 skills/ 下的符号链接，必须解引用后复制实体，安装结果不能依赖本仓库
    if cp -RL -- "${PATHS[$i]}" "$target"; then
      printf '  + %s/%s\n' "${LAYERS[$i]}" "${NAMES[$i]}"
      installed=$((installed + 1))
      # 自举按技能名去重：装多份只跑一次（配置的是全局环境）
      if [[ -f "$target/scripts/setup.sh" && -z "${setup_seen[${NAMES[$i]}]:-}" ]]; then
        setup_seen[${NAMES[$i]}]="$target"
      fi
    else
      printf 'Warning: 安装失败 %s\n' "$dest/${NAMES[$i]}" >&2
      failed+=("$dest/${NAMES[$i]}")
    fi
  done
done

# 技能自带自举脚本：安装完成后逐个运行（如 inone-memory 配置凭据/装全局
# 命令/拉取记忆）。setup 失败只警告不中断——技能本体已装好，可事后手动重跑
if (( ! DRY_RUN && ! SKIP_SETUP && ${#setup_seen[@]} )); then
  for target in "${setup_seen[@]}"; do
    printf '\n运行自举: %s/scripts/setup.sh …\n' "$(basename -- "$target")"
    if ! bash "$target/scripts/setup.sh"; then
      printf 'Warning: 自举失败(%s)——技能本体已装好，可事后手动重跑 scripts/setup.sh\n' "$(basename -- "$target")" >&2
    fi
  done
fi

printf '\n'
if ((DRY_RUN)); then
  printf '预览：将安装 %d 个技能 × %d 个目标目录（--dry-run 未实际写入）\n' "${#selected[@]}" "${#DESTS[@]}"
elif ((${#failed[@]})); then
  printf '完成 %d/%d，失败: %s\n' "$installed" "$(( ${#selected[@]} * ${#DESTS[@]} ))" "${failed[*]}" >&2
  exit 1
else
  printf '已安装 %d 个技能 × %d 个目标目录\n' "${#selected[@]}" "${#DESTS[@]}"
fi
