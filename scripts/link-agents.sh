#!/usr/bin/env bash
set -uo pipefail

# Linux/macOS 版：把本库的全局指令（AGENTS.md / CLAUDE.md）链接到各 Agent 的
# 全局位置，所有 Agent 共用同一份源文件，改仓库即全局生效；同时为支持命令式
# 状态栏的 Agent（Claude Code / Qoder / Qoder-CN）写入 statusLine 配置。
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
DRY_RUN=0

usage() {
  cat <<'EOF'
用法: link-agents.sh [选项]

  默认: 把 AGENTS.md / CLAUDE.md 链接到本机已安装 Agent 的全局位置
        （~/.agents、~/.claude、~/.codex、~/.qoder-cn、~/.config/opencode、~/.zcode）；
        并为 Claude Code / Qoder / Qoder-CN 写入 settings.json 的 statusLine
        （指向 scripts/statusline.js）；
        并提示手动安装 JetBrainsMono Nerd Font 的命令（脚本不自动安装）；
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

# ---- 状态栏接入：一份渲染器，Claude Code / Qoder / Qoder-CN 共用 ----
statusline_src="$ROOT/scripts/statusline.js"
statusline_cmd="node \"$statusline_src\""

update_statusline_json() {
  local file="$1" cmd="$2" tmp
  if command -v jq >/dev/null 2>&1; then
    tmp="$(mktemp)"
    if jq --arg cmd "$cmd" '.statusLine = {type: "command", command: $cmd}' "$file" >"$tmp"; then
      mv -- "$tmp" "$file"
    else
      rm -f -- "$tmp"
      return 1
    fi
  elif command -v python3 >/dev/null 2>&1; then
    python3 - "$file" "$cmd" <<'PY'
import json, sys
path, cmd = sys.argv[1], sys.argv[2]
with open(path, encoding="utf-8") as f:
    data = json.load(f)
data["statusLine"] = {"type": "command", "command": cmd}
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
PY
  elif command -v node >/dev/null 2>&1; then
    node - "$file" "$cmd" <<'JS'
const fs = require("fs");
const [file, cmd] = process.argv.slice(2);
const data = JSON.parse(fs.readFileSync(file, "utf8"));
data.statusLine = { type: "command", command: cmd };
fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
JS
  else
    return 1
  fi
}

wire_statusline() {
  local name="$1" dir="$2" file
  if [[ ! -d "$dir" ]]; then
    printf '  - %-10s 未安装（%s 不存在），跳过状态栏\n' "$name" "$dir"
    return 0
  fi
  if [[ ! -e "$statusline_src" ]]; then
    printf 'Error: 仓库源文件缺失: %s\n' "$statusline_src" >&2
    failed+=("$name")
    return 1
  fi
  file="$dir/settings.json"
  if [[ -f "$file" ]] && grep -Fq -- "$statusline_cmd" "$file"; then
    printf '  = %-10s 状态栏已是本仓库脚本\n' "$name"
    return 0
  fi
  if ((DRY_RUN)); then
    printf '  ~ %-10s 将写入状态栏配置: %s\n' "$name" "$file"
    return 0
  fi
  if [[ -f "$file" ]]; then
    cp -- "$file" "$file.bak.$(date +%Y%m%d%H%M%S)"
  else
    printf '{}\n' >"$file"
  fi
  if update_statusline_json "$file" "$statusline_cmd"; then
    printf '  + %-10s 状态栏已接入: %s\n' "$name" "$statusline_cmd"
  else
    printf 'Error: %s 状态栏写入失败（需要 jq / python3 / node 之一）\n' "$name" >&2
    failed+=("$name")
  fi
}

wire_statusline "claude"   "$HOME/.claude"
wire_statusline "qoder"    "$HOME/.qoder"
wire_statusline "qoder-cn" "$HOME/.qoder-cn"


# ---- Nerd Font 提示（只检测并打印安装命令，不自动安装）----
show_nerd_font_hint() {
  local font_dir
  case "$(uname -s)" in
    Darwin) font_dir="$HOME/Library/Fonts" ;;
    Linux)  font_dir="${XDG_DATA_HOME:-$HOME/.local/share}/fonts" ;;
    *)      echo "  - nerd-font  未知系统，跳过"; return 0 ;;
  esac

  if [[ -f "$font_dir/JetBrainsMonoNerdFont-Regular.ttf" ]] ||
     { command -v fc-list >/dev/null 2>&1 && fc-list 2>/dev/null | grep -qiE 'JetBrainsMono (NF|Nerd Font)'; }; then
    echo "  = nerd-font  已安装"
    return 0
  fi

  echo "  ! nerd-font  未安装，状态栏分支图标可能显示为方框。手动安装："
  echo "      curl -fL -o /tmp/JetBrainsMono.zip https://github.com/ryanoasis/nerd-fonts/releases/download/v3.3.0/JetBrainsMono.zip"
  echo "      mkdir -p $font_dir"
  echo "      unzip -o /tmp/JetBrainsMono.zip -d $font_dir"
  echo "      fc-cache -f    # macOS 可省略"
  echo "      然后在终端设置里选用 JetBrainsMono Nerd Font"
}
show_nerd_font_hint

if ((DRY_RUN)); then
  printf '\n预览完成（--dry-run 未实际写入）。\n'
  exit 0
fi
if ((${#failed[@]})); then
  printf '\n完成，失败: %s\n' "${failed[*]}" >&2
  exit 1
fi
printf '\n全局指令已接入。改仓库文件即对所有 Agent 生效。\n'
