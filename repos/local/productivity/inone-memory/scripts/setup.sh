#!/usr/bin/env bash
# inone-memory 技能自举(Linux/macOS):配置凭据 + 装全局命令 inone + 拉取记忆分发。
# 本技能由 my-skills 的 install-skills.sh 安装到位;本脚本只做运行时接线
# (自 inone deploy/12-memory-hub/install/install.sh 的 [3/6][4/6][5/6] 段蒸馏,
# 行为同源):
#   1. 配置:hub 地址 + token(已存 ~/.inone/config.json 且同服务则复用),
#      写 profile 标记块(INONE_MEMORY_URL / INONE_MEMORY_TOKEN / INONE_AGENT_NAME);
#   2. 全局命令:~/.local/bin/inone(sh 包装器)包住本技能的 scripts/inone_memory.py,
#      PATH export 随 profile 标记块写入——任何终端直呼 inone;
#   3. 分发:inone login + init --global 把 MEMORY-CORE.md + topics/ 下到
#      ~/inone-memory-dist,注册每日 pull --global 计划任务(crontab)。
# 铁律:永不触碰任何 agent 的全局指令文件(AGENTS.md/CLAUDE.md 由 my-skills
# link-agents 独家接入)与 auto-memory 目录;token 只进 env/交互输入,
# 永不进 URL。全部幂等,重跑 = 刷新。
#
# 用法:bash setup.sh [--url http://<NAS_LAN_IP>:8095] [选项]
#   --url <地址>          inone 服务地址(缺省 = 已存配置同服务地址,再缺省交互输入)
#   --token <token>       INONE_MEMORY_TOKEN(优先交互输入不回显;留空 = 沿用
#                         已存凭据或服务端未设鉴权)
#   --agent-name <名>     记账归因名(缺省 = 沿用现有 INONE_AGENT_NAME)
#   --dist-clone <路径>   分发目录(缺省 ~/inone-memory-dist)
#   --dist-remote <url>   git 兜底:HTTP 不可用且无本地文件时自动 git clone 的远端地址
#   --no-schedule         不注册每日 pull --global 计划任务(手动刷新:inone pull --global)
#   --smoke               装完调技能 save 推送一条冒烟记忆
#   --no-profile          只导出当前会话,不写 profile(计划任务与
#                         新终端依赖 inone login 存的配置,不受此项影响)
# 前置:bash + curl 或 wget + python 3(技能本体即 python 脚本,零第三方依赖)。

set -euo pipefail

ENV_BEGIN="# >>> inone agent setup >>>"
ENV_END="# <<< inone agent setup <<<"
# 每日分发刷新的 cron 行标记(幂等替换/卸载清理的判据;计划任务调
# inone pull --global——CLI 自身按哈希比对,变了才下载)
CRON_MARKER="# inone:dist-pull"

die() { printf '错误:%s\n' "$*" >&2; exit 2; }
note() { printf '  %s\n' "$*"; }

usage() {
  cat <<'USAGE'
用法:bash setup.sh [--url http://<NAS_LAN_IP>:8095] [选项]

选项:
  --url <地址>          inone 服务地址(缺省 = 已存配置同服务地址,再缺省交互输入)
  --token <token>       INONE_MEMORY_TOKEN(优先交互输入不回显;留空 = 沿用
                        已存凭据或服务端未设鉴权)
  --agent-name <名>     记账归因名(缺省 = 沿用现有 INONE_AGENT_NAME)
  --dist-clone <路径>   分发目录(缺省 ~/inone-memory-dist)
  --dist-remote <url>   git 兜底:HTTP 不可用且无本地文件时自动 git clone 的远端地址
  --no-schedule         不注册每日 pull --global 计划任务(手动刷新:inone pull --global)
  --smoke               装完调技能 save 推送一条冒烟记忆
  --no-profile          只导出当前会话,不写 profile(计划任务与
                        新终端依赖 inone login 存的配置,不受此项影响)
USAGE
}

# ---- 参数 ----
URL=""
TOKEN="${INONE_MEMORY_TOKEN:-}"
AGENT_NAME="${INONE_AGENT_NAME:-}"
DIST_CLONE="${HOME}/inone-memory-dist"
DIST_REMOTE=""
NO_SCHEDULE=0
SMOKE=0
NO_PROFILE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --url) URL="${2-}"; shift 2 ;;
    --token) TOKEN="${2-}"; shift 2 ;;
    --agent-name) AGENT_NAME="${2-}"; shift 2 ;;
    --dist-clone) DIST_CLONE="${2-}"; shift 2 ;;
    --dist-remote) DIST_REMOTE="${2-}"; shift 2 ;;
    --no-schedule) NO_SCHEDULE=1; shift ;;
    --smoke) SMOKE=1; shift ;;
    --no-profile) NO_PROFILE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf '未知参数:%s\n' "$1" >&2; exit 2 ;;
  esac
done

# 隐形字符清洗(0.2.5-ku 公司机实锤):复制粘贴会把 BOM/零宽字符带进参数,
# Python urlopen 报 unknown url type——纯 bash 字节序列替换,不依赖 sed 方言
clean_invisible() {
  local s="$1"
  # 清单与 install.sh clean_invisible / CLI _INVISIBLE_CHARS 同口径,改清单三处同步
  s="${s//$'\xef\xbb\xbf'/}"   # U+FEFF BOM
  s="${s//$'\xef\xbf\xbe'/}"   # U+FFFE
  s="${s//$'\xe2\x80\x8b'/}"   # U+200B 零宽空格
  s="${s//$'\xe2\x80\x8e'/}"   # U+200E LRM
  s="${s//$'\xe2\x80\x8f'/}"   # U+200F RLM
  s="${s//$'\xc2\xa0'/}"       # U+00A0 NBSP
  printf '%s' "${s}"$'\n' | sed -e 's/[[:space:]]*$//' -e 's/^[[:space:]]*//'
}
[ -n "$URL" ] && URL="$(clean_invisible "$URL")"
URL="${URL%/}"
TOKEN="$(clean_invisible "$TOKEN")"
AGENT_NAME="$(clean_invisible "$AGENT_NAME")"
DIST_REMOTE="$(clean_invisible "$DIST_REMOTE")"
[ -n "$DIST_CLONE" ] && DIST_CLONE="$(clean_invisible "$DIST_CLONE")"

# CLI = 本技能自带的单文件(scripts/ 同目录);install-skills 装到哪,壳指向哪
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SKILL_CLI="$SCRIPT_DIR/inone_memory.py"
[ -f "$SKILL_CLI" ] || die "找不到 CLI 单文件:$SKILL_CLI(本脚本须在技能的 scripts/ 内运行)"

# 连接 10s/整请求 60s 封顶:WSL2+VPN 等黑洞网络下 curl 缺省无限等,超时后
# 走 die 的人话错误,不再静默挂起
if command -v curl >/dev/null 2>&1; then FETCH="curl -fsSL --connect-timeout 10 --max-time 60"
elif command -v wget >/dev/null 2>&1; then FETCH="wget -q -T 10 -t 2 -O -"
else die "需要 curl 或 wget(两者都未找到)"
fi
# python 解析:按可执行性探测(Windows 的 WindowsApps/python3 是商店
# 占位桩,command -v 存在但运行必败——必须实跑一次甄别)
PY=""
for cand in python3 python; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys' >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
[ -n "$PY" ] || die "需要 python 3(技能本体 scripts/inone_memory.py 依赖)"

# ~ 展开 + 绝对化(分发目录;兼容 Windows 盘符路径)。
# 注意:case 模式与 ${var#~/} 里的 ~ 都会被 bash 做 tilde 展开(展开成
# $HOME 后既匹配不上也剥不掉),必须用 \~ 转义。
expand_home() {
  local p="$1"
  if [ "$p" = "~" ]; then printf '%s' "$HOME"
  elif [ "${p#\~/}" != "$p" ]; then printf '%s/%s' "$HOME" "${p#\~/}"
  else printf '%s' "$p"
  fi
}
absolutize() {
  if [[ "$1" == /* || "$1" =~ ^[A-Za-z]: ]]; then printf '%s' "$1"
  else printf '%s/%s' "$PWD" "$1"; fi
}
DIST_CLONE="$(absolutize "$(expand_home "$DIST_CLONE")")"

# 交互安全输入(不回显;管道安装时从 /dev/tty 读,stdin 正在被脚本占用)
prompt_secret() {
  local tip="$1" var="$2" tty=/dev/tty
  # 可开性探测:无 controlling terminal 时 /dev/tty 虽存在但打开即 ENXIO
  # (CI/测试进程常态)——直接降级,不在 set -e 下炸脚本
  if ! printf '' > "$tty" 2>/dev/null; then
    note "无终端可交互:$tip 跳过(按未提供继续)"
    return 1
  fi
  printf '%s' "$tip" > "$tty"
  IFS= read -rs "$var" < "$tty" || true
  printf '\n' > "$tty"
  return 0
}

# ---- 1) 凭据解析(三态:显式 token → 已存凭据复用 → 交互输入)----
printf '[1/3] 配置凭据…\n'
# 已存登录凭据检测(CLI 同口径:INONE_CONFIG 覆盖,缺省 ~/.inone/config.json)
CLI_CONFIG="${INONE_CONFIG:-$HOME/.inone/config.json}"
STORED_CREDS=0
STORED_SERVER="$("$PY" -c '
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    sys.exit(0)
if d.get("server") and d.get("token"):
    print(str(d["server"]).rstrip("/"))
' "$CLI_CONFIG" 2>/dev/null || true)"
if [ -z "$URL" ] && [ -n "$STORED_SERVER" ]; then
  URL="$STORED_SERVER"
  note "未传 --url,沿用已存凭据的服务地址:$URL"
fi
if [ -z "$URL" ]; then
  if ! printf '' > /dev/tty 2>/dev/null; then
    die "缺 --url 且无已存凭据(无人值守环境不交互);示例:bash setup.sh --url http://<NAS_LAN_IP>:8095"
  fi
  printf 'inone 服务地址(如 http://192.168.2.25:8095):' > /dev/tty
  IFS= read -r URL < /dev/tty
  URL="$(clean_invisible "$URL")"; URL="${URL%/}"
fi
if [ -n "$STORED_SERVER" ]; then
  a="$(printf '%s' "${STORED_SERVER%/}" | tr '[:upper:]' '[:lower:]')"
  b="$(printf '%s' "${URL%/}" | tr '[:upper:]' '[:lower:]')"
  if [ "$a" = "$b" ]; then
    STORED_CREDS=1
  else
    note "已存凭据指向 ${STORED_SERVER%/},与本次地址(${URL%/})不一致:不沿用"
    note "(服务迁移场景:带新 token 重跑即覆盖)"
  fi
fi
if [ -z "$TOKEN" ] && [ "$STORED_CREDS" -eq 0 ]; then
  if prompt_secret "INONE_MEMORY_TOKEN(输入不回显;直接回车 = 服务端未设鉴权,分发走 git 兜底):" TOKEN; then
    TOKEN="$(clean_invisible "$TOKEN")"
  fi
elif [ -z "$TOKEN" ]; then
  note "已检测到 inone login 存的凭据($CLI_CONFIG):跳过 token 输入,分发直接复用"
fi
[ -n "$AGENT_NAME" ] || AGENT_NAME="${INONE_AGENT_NAME:-}"
export INONE_MEMORY_URL="$URL"
[ -n "$AGENT_NAME" ] && export INONE_AGENT_NAME="$AGENT_NAME"
if [ -n "$TOKEN" ]; then export INONE_MEMORY_TOKEN="$TOKEN"; fi

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

update_marked_block() {
  # $1=目标文件 $2=BEGIN $3=END $4=块内容文件;已有标记 → 替换,否则追加
  local file="$1" begin="$2" end="$3" block="$4"
  mkdir -p "$(dirname "$file")"
  [ -e "$file" ] || : > "$file"
  if grep -qF "$begin" "$file" && grep -qF "$end" "$file"; then
    awk -v b="$begin" -v e="$end" -v blk="$block" '
      function emit() { while ((getline l < blk) > 0) print l }
      { line = $0; sub(/\r$/, "", line) }  # 兼容 CRLF(ps1 写过的文件重跑)
      line == b { emit(); skip = 1; next }
      line == e { skip = 0; next }
      !skip { print }
    ' "$file" > "$file.tmp"
    mv "$file.tmp" "$file"
  else
    if [ -n "$(tail -c 1 "$file")" ]; then printf '\n' >> "$file"; fi  # 确保行尾换行
    printf '\n' >> "$file"
    cat "$block" >> "$file"
  fi
}

ENV_BLOCK="$WORK_DIR/env-block"
if [ "$NO_PROFILE" -eq 1 ]; then
  note "跳过 profile(--no-profile;仅当前会话生效)"
else
  {
    printf '# %s\n' "$ENV_BEGIN"
    printf 'export INONE_MEMORY_URL="%s"\n' "$URL"
    if [ -n "$AGENT_NAME" ]; then
      printf 'export INONE_AGENT_NAME="%s"\n' "$AGENT_NAME"
    fi
    if [ -n "$TOKEN" ]; then
      printf 'export INONE_MEMORY_TOKEN="%s"\n' "$TOKEN"
    else
      printf '# INONE_MEMORY_TOKEN 未写入(服务端未设鉴权;需要时改用\n#   inone login 或补一行 export)\n'
    fi
    # 全局命令目录上 PATH(标记块内,重跑幂等替换;多列一次 PATH 无害)
    printf 'export PATH="$HOME/.local/bin:$PATH"\n'
    printf '# %s\n' "$ENV_END"
  } > "$ENV_BLOCK"
  # bash 系:~/.bash_profile 存在则优先(登录 shell 只读它),否则 ~/.profile;
  # zsh 系(macOS 缺省):SHELL 是 zsh 或 ~/.zprofile 已存在时兼写一份——
  # 两处都是标记块,互不冲突(每个 shell 只读自己那份)
  PROFILE="$HOME/.profile"
  if [ -f "$HOME/.bash_profile" ]; then PROFILE="$HOME/.bash_profile"; fi
  update_marked_block "$PROFILE" "$ENV_BEGIN" "$ENV_END" "$ENV_BLOCK"
  note "$PROFILE 标记块已写入(重跑替换)"
  if [[ "${SHELL:-}" == *zsh ]] || [ -f "$HOME/.zprofile" ]; then
    update_marked_block "$HOME/.zprofile" "$ENV_BEGIN" "$ENV_END" "$ENV_BLOCK"
    note "$HOME/.zprofile 标记块已写入(zsh 兼写,重跑替换)"
  fi
fi

# ---- 2) 全局命令 inone(~/.local/bin)----
# sh 包装器物化绝对路径(python 换位/升级后重跑本脚本即重写);
# PATH export 已随 env 标记块写 profile。
printf '[2/3] 安装全局命令 inone…\n'
INONE_BIN_DIR="$HOME/.local/bin"
mkdir -p "$INONE_BIN_DIR"
PY_ABS="$(command -v "$PY")"
WRAPPER="$INONE_BIN_DIR/inone"
{
  printf '#!/bin/sh\n'
  printf '# inone CLI 包装器(setup.sh 生成;重跑即重写)\n'
  printf 'exec "%s" "%s" "$@"\n' "$PY_ABS" "$SKILL_CLI"
} > "$WRAPPER"
chmod 755 "$WRAPPER"
export PATH="$INONE_BIN_DIR:$PATH"
note "$WRAPPER 就绪(本会话已可用;新终端随 profile 标记块的 PATH export 生效)"

# ---- 3) 分发初始化(HTTP 主路在 CLI,本段只接线)----
# 凭据三态:显式 token(login 落盘)→ 已存凭据(直接用)→ 都没有(git 兜底)。
# CREDS_DURABLE=1 = CLI 本机配置里有可用凭据——cron 环境没有 profile env,
# 凭据全靠配置文件;login 未通过时不注册 cron(免得每天空跑失败)。
printf '[3/3] 分发初始化…\n'
DIST_HTTP_OK=0
CREDS_DURABLE=0
if [ -n "$TOKEN" ]; then
  # a. 引导 inone login:服务地址+token 存 CLI 本机配置(~/.inone/config.json,
  #    INONE_CONFIG 可覆盖);值经 stdin 管道传入,不进命令行参数/历史
  LOGIN_OK=0
  LOGIN_OUT="$(printf '%s\n%s\n' "$URL" "$TOKEN" | "$PY" "$SKILL_CLI" login 2>&1)" && LOGIN_OK=1
  if [ "$LOGIN_OK" -eq 1 ]; then
    note "inone login 成功(服务地址+token 已存 CLI 本机配置,计划任务可用)"
  else
    note "⚠ inone login 未通过:$LOGIN_OUT(继续尝试 init;服务端可能未开鉴权)"
  fi
  # b. inone init --global:HTTP 下载 MEMORY-CORE.md + topics/ 到分发目录
  #    (--no-domain:无人值守管道不弹领域交互;逐文件校验,失败自动留旧)
  if "$PY" "$SKILL_CLI" init --global --no-domain --dest "$DIST_CLONE"; then
    DIST_HTTP_OK=1
    [ "$LOGIN_OK" -eq 1 ] && CREDS_DURABLE=1
  else
    note "⚠ HTTP 分发初始化失败(服务端 /dist 未就绪或 token 无效)——转 git 兜底"
  fi
elif [ "$STORED_CREDS" -eq 1 ]; then
  # 已存凭据:不重新 login,init 直接复用配置(计划任务凭据同源)
  note "复用已存凭据(跳过 login):inone init --global 直连…"
  if "$PY" "$SKILL_CLI" init --global --no-domain --dest "$DIST_CLONE"; then
    DIST_HTTP_OK=1
    CREDS_DURABLE=1
  else
    note "⚠ HTTP 分发初始化失败(凭据可能已失效——重跑带新 token 重新 login)"
  fi
else
  note "未提供 token:HTTP 分发主路需要 token(溯源页「凭证管理」可签发)"
  note "——本次走 git 兜底,带 token 重跑本脚本即切主路"
fi

if [ "$DIST_HTTP_OK" -eq 1 ] && [ "$CREDS_DURABLE" -eq 1 ]; then
  # c. 每日增量刷新计划任务:inone pull --global(CLI 自身哈希比对,变了才
  #    下载,失败留旧文件);cron 环境无 profile env,凭据来自 login 存的
  #    配置——所以 login 在 init 之前。幂等:同标记旧行整行替换。
  if [ "$NO_SCHEDULE" -eq 1 ]; then
    note "跳过每日刷新注册(--no-schedule;手动刷新:inone pull --global)"
  elif ! command -v crontab >/dev/null 2>&1; then
    note "⚠ 本机无 crontab,跳过每日刷新注册;手动刷新:inone pull --global"
  else
    CRON_LINE="17 9 * * * \"$PY_ABS\" \"$SKILL_CLI\" pull --global --dest \"$DIST_CLONE\" >> \"\$HOME/.inone/dist-pull.log\" 2>&1 $CRON_MARKER"
    CRON_TMP="$WORK_DIR/crontab"
    crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" > "$CRON_TMP" || true
    printf '%s\n' "$CRON_LINE" >> "$CRON_TMP"
    if crontab "$CRON_TMP"; then
      note "每日分发刷新已注册(crontab 09:17 inone pull --global;日志 ~/.inone/dist-pull.log;"
      note "卸载:crontab -l | grep -vF '$CRON_MARKER' | crontab -)"
    else
      note "⚠ crontab 注册失败(权限?);手动刷新:inone pull --global"
    fi
  fi
elif [ "$DIST_HTTP_OK" -eq 1 ]; then
  note "⚠ init 成功但 login 配置未落盘:暂不注册每日刷新(空跑无益);"
  note "  服务可达后带 token 重跑本脚本即补上"
elif [ -d "$DIST_CLONE/.git" ]; then
  note "分发克隆就位(git 兜底形态):$DIST_CLONE——日常更新 git -C 手动 pull,"
  note "或带 token 重跑本脚本切到 HTTP 主路(每日自动刷新)"
elif [ -n "$DIST_REMOTE" ]; then
  printf '  分发目录缺失,git 兜底自动 clone %s …\n' "$DIST_REMOTE"
  command -v git >/dev/null 2>&1 || die "git 兜底 clone 需要 git(或带 token 重跑走 HTTP 主路)"
  git clone "$DIST_REMOTE" "$DIST_CLONE" || die "git clone 失败(可手工重试,或带 token 重跑走 HTTP 主路)"
else
  note "⚠ 分发目录缺失:$DIST_CLONE(本次未初始化)"
  note "  主路:带 token 重跑本脚本(inone init --global 经 HTTP 下载,零 git 零 SSH)"
  note "  git 兜底:重跑时加 --dist-remote <git地址> 自动 clone"
fi

# ---- 冒烟(可选):推一条记忆验证端到端 ----
if [ "$SMOKE" -eq 1 ]; then
  printf '[冒烟] 推送验证…\n'
  if "$PY" "$SKILL_CLI" save \
      --statement "冒烟:inone-memory setup 通道可用(可归档)" \
      --kind semantic \
      --evidence "setup.sh --smoke,时间 $(date '+%Y-%m-%d %H:%M')"; then
    note "冒烟成功:溯源页 $URL/ 记忆 tab 审核子视图应立即可见"
  else
    printf '  ⚠ 冒烟推送失败(检查 --url/--token;技能与配置已就位,不影响安装)\n'
  fi
fi

printf '完成:inone-memory 运行时就绪。重跑本脚本 = 刷新;全局指令(AGENTS.md)由 my-skills link-agents 接入。\n'
