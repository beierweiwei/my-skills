#!/usr/bin/env bash
set -uo pipefail

# 一步完成：先更新技能库（update-skills.sh），成功后安装到已装 Agent（install-skills.sh）。
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UPDATE_ARGS=()
INSTALL_ARGS=()

usage() {
  cat <<'EOF'
用法: update-all.sh [update 选项] [install 选项]

先运行 update-skills.sh 更新技能库，成功后运行 install-skills.sh 安装到已装 Agent。

update 选项:
  --proxy URL          git 代理（默认 http://127.0.0.1:7890）
  --no-proxy           直连，不使用代理

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
EOF
}

while (($#)); do
  case "$1" in
    --proxy)
      (($# >= 2)) || { usage >&2; exit 2; }
      UPDATE_ARGS+=("$1" "$2"); shift 2 ;;
    --no-proxy)
      UPDATE_ARGS+=("$1"); shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      INSTALL_ARGS+=("$1"); shift ;;
  esac
done

printf '== 1/2 更新技能库 ==\n'
if ((${#UPDATE_ARGS[@]})); then
  bash "$ROOT/scripts/update-skills.sh" "${UPDATE_ARGS[@]}"
else
  bash "$ROOT/scripts/update-skills.sh"
fi
status=$?
if ((status != 0)); then
  printf '\n更新未完全成功（退出码 %d），已跳过安装。\n' "$status" >&2
  printf '如需使用当前库直接安装，可运行: bash scripts/install-skills.sh\n' >&2
  exit "$status"
fi

printf '\n== 2/2 安装到已装 Agent ==\n'
if ((${#INSTALL_ARGS[@]})); then
  bash "$ROOT/scripts/install-skills.sh" "${INSTALL_ARGS[@]}"
else
  bash "$ROOT/scripts/install-skills.sh"
fi
