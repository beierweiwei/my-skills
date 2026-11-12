#!/usr/bin/env bash
set -uo pipefail

# 一步完成：先更新技能库（update-skills.sh），成功后安装到已装 Agent（install-skills.sh）。
# 更新失败时：带 --skill 且请求技能在本地可用 → 自动回退 scoped install；--offline 显式跳过更新。
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_ARGS=()
INSTALL_ARGS=()
PICK_SKILLS=()
OFFLINE=0

usage() {
  cat <<'EOF'
用法: update-all.sh [update 选项] [install 选项]

先运行 update-skills.sh 更新技能库，成功后运行 install-skills.sh 安装到已装 Agent。

update 选项:
  --proxy URL          git 代理（默认 http://127.0.0.1:7890）
  --no-proxy           直连，不使用代理
  --offline            跳过技能库更新，直接用当前本地库安装（代理不可用且只需本地已有技能时用）

更新失败回退：带 --skill 且所请求技能在本地库可用（repos/local 或既有缓存）时，
自动跳过更新直接做 scoped install，退出码取安装结果；回退使用本地库/缓存，可能
落后远端，网络恢复后建议重跑；不可回退时保持更新失败退出码。

install 选项（原样传给 install-skills.sh）:
  --skill A,B          只安装指定技能
  --layer LAYER        只安装指定层
  --agent A,B          只安装到指定 Agent
  --all                安装除 deprecated 外的全部层
  --list               列出技能与各目标目录的安装状态
  --dest DIR           只安装到指定目录
  --dry-run            只预览安装，不写入
  --skip-setup         跳过技能自带的自举脚本
  -h, --help           显示本帮助

示例:
  bash scripts/update-all.sh
  bash scripts/update-all.sh --no-proxy --agent qoder-cn
  bash scripts/update-all.sh --skip-setup --dry-run
  bash scripts/update-all.sh --offline --skill agent-crew --agent qoder-cn
EOF
}

while (($#)); do
  case "$1" in
    --proxy)
      (($# >= 2)) || { usage >&2; exit 2; }
      UPDATE_ARGS+=("$1" "$2"); shift 2 ;;
    --no-proxy)
      UPDATE_ARGS+=("$1"); shift ;;
    --offline)
      OFFLINE=1; shift ;;
    --skill)
      (($# >= 2)) || { usage >&2; exit 2; }
      INSTALL_ARGS+=("$1" "$2")
      IFS=',' read -r -a _names <<< "$2"
      PICK_SKILLS+=("${_names[@]}")
      shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      INSTALL_ARGS+=("$1"); shift ;;
  esac
done

# 技能在本地库中可用：skills/<layer>/<name>/SKILL.md 经符号链接可解析
skill_available() {
  local name="$1" hit
  hit="$(find -L "$ROOT/skills" -mindepth 3 -maxdepth 3 -name SKILL.md -path "*/$name/SKILL.md" -print -quit 2>/dev/null)"
  [[ -n "$hit" ]]
}

# 仅 scoped（带 --skill）且所请求技能全部本地可解析时才回退
fallback_eligible() {
  ((${#PICK_SKILLS[@]})) || return 1
  local name seen=0
  for name in "${PICK_SKILLS[@]}"; do
    [[ -n "$name" ]] || continue
    seen=1
    skill_available "$name" || return 1
  done
  ((seen))
}

run_install() {
  if ((${#INSTALL_ARGS[@]})); then
    bash "$ROOT/scripts/install-skills.sh" "${INSTALL_ARGS[@]}"
  else
    bash "$ROOT/scripts/install-skills.sh"
  fi
}

if ((OFFLINE)); then
  printf '== 离线模式：跳过技能库更新，直接用当前本地库安装 ==\n'
  run_install
  exit $?
fi

printf '== 1/2 更新技能库 ==\n'
if ((${#UPDATE_ARGS[@]})); then
  bash "$ROOT/scripts/update-skills.sh" "${UPDATE_ARGS[@]}"
else
  bash "$ROOT/scripts/update-skills.sh"
fi
status=$?
if ((status != 0)); then
  if fallback_eligible; then
    printf '\nupdate 失败 → 已回退安装（rc=%d）：所请求技能在本地可用，跳过更新直接安装（使用本地库/缓存，可能落后远端；网络恢复后建议重跑）。\n' "$status" >&2
    run_install
    exit $?
  fi
  printf '\n更新未完全成功（退出码 %d），已跳过安装。\n' "$status" >&2
  printf '下一步：检查代理后重试（--proxy URL 或 --no-proxy）；仅需安装本地已有技能时用: bash scripts/update-all.sh --offline --skill <技能名>\n' >&2
  exit "$status"
fi

printf '\n== 2/2 安装到已装 Agent ==\n'
run_install
