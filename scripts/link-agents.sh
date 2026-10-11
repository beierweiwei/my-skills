#!/usr/bin/env bash
set -uo pipefail

# Linux/macOS 版：把本库的全局指令（AGENTS.md / CLAUDE.md）链接到各 Agent 的
# 全局位置，所有 Agent 共用同一份源文件，改仓库即全局生效。
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: link-agents.sh [选项]

  默认: 把 AGENTS.md / CLAUDE.md 链接到本机已安装 Agent 的全局位置
        （~/.agents、~/.claude、~/.codex、~/.qoder-cn、~/.config/opencode、~/.zcode）；
        目录不存在视为未安装，跳过；已有实体文件先备份为 *.bak.<时间戳>。

  -n, --dry-run   只预览，不写入
  -h, --help      显示本帮助
EOF
}

while (($#)); do
  case "$1" in
    -n|--dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
done

failed=()

link_one() {
  local name="$1" link="$2" source="$3"
  local dir; dir="$(dirname -- "$link")"
  if [[ ! -d "$dir" ]]; then
    printf '  - %-10s 未安装（%s 不存在），跳过\n' "$name" "$dir"
    return 0
  fi
  if [[ ! -e "$source" ]]; then
    printf 'Error: 仓库源文件缺失: %s\n' "$source" >&2
    failed+=("$name")
    return 1
  fi
  if [[ -L "$link" && "$(readlink -- "$link")" == "$source" ]]; then
    printf '  = %-10s 已是链接\n' "$name"
    return 0
  fi
  if ((DRY_RUN)); then
    printf '  ~ %-10s 将链接: %s -> %s\n' "$name" "$link" "$source"
    return 0
  fi
  if [[ -L "$link" ]]; then
    rm -- "$link"
  elif [[ -e "$link" ]]; then
    local bak="$link.bak.$(date +%Y%m%d%H%M%S)"
    mv -- "$link" "$bak"
    printf '  ! %-10s 原文件已备份: %s\n' "$name" "$bak"
  fi
  ln -s -- "$source" "$link"
  printf '  + %-10s %s -> %s\n' "$name" "$link" "$source"
}

# 常用 Agent 的全局指令位置；AGENTS.md 为共用源，CLAUDE.md 是指向它的指针
link_one "agents"   "$HOME/.agents/AGENTS.md"           "$ROOT/AGENTS.md"
link_one "claude"   "$HOME/.claude/CLAUDE.md"           "$ROOT/CLAUDE.md"
link_one "codex"    "$HOME/.codex/AGENTS.md"            "$ROOT/AGENTS.md"
link_one "qoder-cn" "$HOME/.qoder-cn/AGENTS.md"         "$ROOT/AGENTS.md"
link_one "opencode" "$HOME/.config/opencode/AGENTS.md"  "$ROOT/AGENTS.md"
link_one "zcode"    "$HOME/.zcode/AGENTS.md"            "$ROOT/AGENTS.md"

if ((DRY_RUN)); then
  printf '\n预览完成（--dry-run 未实际写入）。\n'
  exit 0
fi
if ((${#failed[@]})); then
  printf '\n完成，失败: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf '\n全局指令已接入。改仓库文件即对所有 Agent 生效。\n'
