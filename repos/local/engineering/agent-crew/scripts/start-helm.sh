#!/usr/bin/env bash
# 一键启动总舵（helm）。重复执行安全：已在运行则直接切到该 tab。
# 配置：读 <仓根>/.planning/orchestration.json（可选）——
#   helm.agentName（herdr agent 实例名，同机全局唯一；默认派生 helm-<projectName slug>）、
#   helm.tabLabel（默认「总舵」）、helm.expectedModel（默认空=只提示实测模型值，不做断言）、
#   portfolioDir（默认 .planning/portfolio）、docNames.context（默认 CONTEXT.md）。
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WS_LABEL="$(basename "$PROJECT_ROOT")"
ROLE_NAME="helm"  # qodercli 角色名（.qoder/agents/helm.md），与 herdr agent 实例名相互独立

# 读配置 + 派生 agent 实例名（helm_agent_name.py；配置缺失/损坏走内置默认并告警）
eval "$(python3 "$(dirname "${BASH_SOURCE[0]}")/helm_agent_name.py" "$PROJECT_ROOT")"

# 启动失败不留空 tab：tab 建成后到 agent 启动成功之间任何退出都回收本次 tab
CREATED_PANE=""
cleanup_tab() {
  [ -n "$CREATED_PANE" ] || return 0
  herdr pane close "$CREATED_PANE" >/dev/null 2>&1 || true
}
trap cleanup_tab EXIT

die() { echo "错误：$*" >&2; exit 1; }

command -v herdr >/dev/null 2>&1 || die "未找到 herdr，请先安装 Herdr。"
herdr status --json 2>/dev/null | grep -q '"running":true' \
  || die "Herdr server 未运行。先在终端执行 herdr 打开 TUI（server 随 TUI 启动），再重跑本脚本。"

WS_ID="$(herdr workspace list | python3 -c '
import json, sys
label = sys.argv[1]
ws = json.load(sys.stdin)["result"]["workspaces"]
match = [w["workspace_id"] for w in ws if w["label"] == label]
if not match:
    focused = [w for w in ws if w.get("focused")]
    if focused:
        match = [focused[0]["workspace_id"]]
        name = focused[0].get("label") or focused[0]["workspace_id"]
        print("警告：未找到同名 workspace（%s），将使用当前聚焦的 %s——若不对请先在 Herdr 切换后再运行本脚本。" % (label, name), file=sys.stderr)
print(match[0] if match else "")
' "$WS_LABEL" || true)"
[ -n "$WS_ID" ] || die "未找到本项目（标签 $WS_LABEL）的 Herdr workspace。先执行 herdr，在该项目目录下建 workspace 后重试。"

EXISTING_PANE="$(herdr agent list | python3 -c '
import json, sys
ws, name = sys.argv[1], sys.argv[2]
for a in json.load(sys.stdin)["result"]["agents"]:
    if a.get("name") == name and a.get("workspace_id") == ws:
        print(a["pane_id"]); break
' "$WS_ID" "$AGENT_NAME" || true)"

if [ -n "$EXISTING_PANE" ]; then
  trap - EXIT
  herdr agent focus "$AGENT_NAME" >/dev/null
  echo "总舵已在运行（agent $AGENT_NAME，pane $EXISTING_PANE），已切到该 tab。"
  exit 0
fi

TAB_JSON="$(herdr tab create --workspace "$WS_ID" --cwd "$PROJECT_ROOT" --label "$TAB_LABEL" --no-focus)" \
  || die "创建 tab 失败。"
PANE_ID="$(printf '%s' "$TAB_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])')" \
  || die "解析 pane id 失败：$TAB_JSON"
CREATED_PANE="$PANE_ID"

herdr agent start "$AGENT_NAME" --kind qodercli --pane "$PANE_ID" -- --agent "$ROLE_NAME" >/dev/null \
  || {
    # herdr agent 名同机全局唯一：被占用时给可执行下一步（工单 D-helm-fix）
    TAKEN="$(herdr agent list | python3 -c '
import json, sys
name = sys.argv[1]
for a in json.load(sys.stdin)["result"]["agents"]:
    if a.get("name") == name:
        print("%s（workspace %s）" % (a.get("pane_id") or "?", a.get("workspace_id") or "?")); break
' "$AGENT_NAME" || true)"
    if [ -n "$TAKEN" ]; then
      die "总舵 agent 名 $AGENT_NAME 已被另一项目占用（pane $TAKEN）。二选一：关闭旧的总舵 agent（herdr pane close <其 pane id>）后重跑，或改 .planning/orchestration.json 的 helm.agentName 后重跑。"
    fi
    die "启动 qodercli 失败（agent $AGENT_NAME，pane $PANE_ID）。"
  }

trap - EXIT
CREATED_PANE=""

sleep 2
MODEL="$(herdr agent read "$AGENT_NAME" --source visible --lines 15 2>/dev/null | grep -o '\[[^]]*\] ctx' | tail -1 | sed 's/ ctx$//' || true)"

herdr agent prompt "$AGENT_NAME" "接手总舵。按 .qoder/agents/helm.md 必读清单只读加载：根 AGENTS.md、${PORTFOLIO_DIR}/ 的 task_plan.md/findings.md/progress.md、各流 plan 的 Next Step/Current Phase/Status、${DOC_CONTEXT}。只读查询用 Read/Grep，非必要不跑 Bash。先给首条汇报：状态 3 行（做了什么/证据路径/变化）+ 待决策 ≤3（问题/为什么/选项与影响/推荐）。" >/dev/null \
  || die "接手指令发送失败，请到 tab「$TAB_LABEL」手动输入。"

echo "总舵已启动：tab「$TAB_LABEL」，agent $AGENT_NAME，pane $PANE_ID，模型 ${MODEL:-未知}"
if [ -n "$EXPECTED_MODEL" ]; then
  case "$MODEL" in
    *"$EXPECTED_MODEL"*) ;;
    *) echo "提示：状态栏模型与配置 helm.expectedModel（$EXPECTED_MODEL）不符，检查运行时 agent 模型配置。" >&2 ;;
  esac
else
  echo "提示：未配置 helm.expectedModel，跳过模型核对（可在 .planning/orchestration.json 配置后启用）。"
fi
echo "切过去：herdr agent focus $AGENT_NAME"
echo "停止：  herdr pane close $PANE_ID"
