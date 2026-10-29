#!/usr/bin/env bash
# 统一启动 agent（Captain 2026-10-29 指示：任何 worker / 副舵一律 YOLO 模式，避免忘带）
# 用法: scripts/start-agent.sh <agent-name> <pane-id> <qoder-agent-role> [extra args...]
# 例:   scripts/start-agent.sh demo-firstmate wX:p3P firstmate
#       scripts/start-agent.sh demo-builder    wX:p40 builder
# 边界: YOLO 仅限隔离 worktree 与明确任务边界；合并/推送/清理与数据安全门禁不因 YOLO 放宽。
set -euo pipefail
NAME="${1:?用法: start-agent.sh <agent-name> <pane-id> <role> [extra...]}"
PANE="${2:?缺 pane-id}"
ROLE="${3:?缺 qoder agent 角色名}"
shift 3
exec herdr agent start "$NAME" --kind qodercli --pane "$PANE" -- --agent "$ROLE" --permission-mode bypass_permissions "$@"
