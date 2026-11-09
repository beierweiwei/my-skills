#!/usr/bin/env bash
# 薄包装：所有 Agent 生命周期只走 scripts/dispatch.py（不要再直接拼 herdr/qodercli 参数）。
# 用法: scripts/start-agent.sh <role> [pane-id] [--instance N] [--permission-mode ...]
# 例:   scripts/start-agent.sh builder wX:p40
#       scripts/start-agent.sh reviewer wX:p41 --instance 2
# 边界: YOLO 仅限隔离 worktree 与明确任务边界；合并/推送/清理与数据安全门禁不因 YOLO 放宽。
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROLE="${1:?用法: start-agent.sh <role> [pane-id] [extra...]}"
shift

PANE=""
if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
  PANE="$1"
  shift
fi

ARGS=(start --role "$ROLE" --runtime qoder --permission-mode bypass_permissions)
if [ -n "$PANE" ]; then
  ARGS+=(--pane "$PANE")
fi
exec python3 "$ROOT/scripts/dispatch.py" --root "$ROOT" "${ARGS[@]}" "$@"
