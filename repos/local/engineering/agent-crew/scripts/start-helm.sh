#!/usr/bin/env bash
# 一键启动总舵（helm）。重复执行安全：已在运行则提示并退出。
# 本脚本是 dispatch.py 的薄包装：tab create / start / prompt / pane close 全部走 dispatcher，
# 不直接拼 herdr 启动命令或 qodercli 参数。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DISPATCH=(python3 "$ROOT/scripts/dispatch.py" --root "$ROOT")

# 读配置：AGENT_NAME / TAB_LABEL / PORTFOLIO_DIR / DOC_CONTEXT
eval "$(python3 "$ROOT/scripts/helm_agent_name.py" "$ROOT")"

die() { echo "错误：$*" >&2; exit 1; }

# 已在运行：只读检查后提示，不重复启动
if herdr agent list 2>/dev/null | python3 -c '
import json, sys
name = sys.argv[1]
try:
    agents = json.load(sys.stdin)["result"]["agents"]
except Exception:
    sys.exit(1)
sys.exit(0 if any(a.get("name") == name for a in agents) else 1)
' "$AGENT_NAME"; then
  echo "总舵已在运行（agent $AGENT_NAME）。切过去：herdr agent focus $AGENT_NAME"
  exit 0
fi

TAB_JSON="$("${DISPATCH[@]}" tab create --cwd "$ROOT" --label "$TAB_LABEL" --no-focus)" \
  || die "创建 tab 失败（经 dispatch.py tab create）。"
PANE_ID="$(printf '%s' "$TAB_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["pane"])')" \
  || die "解析 pane 失败：$TAB_JSON"
CREATED_PANE="$PANE_ID"

cleanup_tab() {
  [ -n "${CREATED_PANE:-}" ] || return 0
  "${DISPATCH[@]}" pane close --pane "$CREATED_PANE" >/dev/null 2>&1 || true
}
trap cleanup_tab EXIT

"${DISPATCH[@]}" start --role helm --runtime qoder --permission-mode bypass_permissions --pane "$PANE_ID" >/dev/null \
  || die "启动总舵失败（dispatch.py start）。检查 herdr server、qodercli 与命名冲突后重试。"

trap - EXIT
CREATED_PANE=""

"${DISPATCH[@]}" prompt --agent "$AGENT_NAME" --message "接手总舵。按角色文件必读清单只读加载：根 AGENTS.md、${PORTFOLIO_DIR}/ 的 task_plan.md/findings.md/progress.md、各流 plan 的 Next Step/Current Phase/Status、${DOC_CONTEXT}。只读查询用 Read/Grep，非必要不跑 Bash。先给首条汇报：状态 3 行（做了什么/证据路径/变化）+ 待决策 ≤3（问题/为什么/选项与影响/推荐）。" >/dev/null \
  || die "接手指令发送失败（dispatch.py prompt）。到 tab「$TAB_LABEL」手动输入。"

echo "总舵已启动：tab「$TAB_LABEL」，agent $AGENT_NAME，pane $PANE_ID"
echo "切过去：herdr agent focus $AGENT_NAME"
echo "停止：  python3 $ROOT/scripts/dispatch.py --root $ROOT close --agent $AGENT_NAME"
