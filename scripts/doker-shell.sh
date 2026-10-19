#!/usr/bin/env bash
# doker-shell.sh — exec 进主服务容器交互 shell（PTY 给 TTY）
# flow-engine-doker 容器化项目运行环境（002-design-doc.md §1.4）
# 主服务名固定 main（模板保证存在）；进不去时 P4 显式报错。
# 与 engine.py _run_in_env 的区别：本脚本走 TTY（人用），_run_in_env 走 exec -T（agent 用）。
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE_FILE="docker/compose.yaml"
SERVICE="${DOKER_SERVICE:-main}"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "[doker] shell failed: 缺少 $COMPOSE_FILE" >&2
  exit 1
fi

# exec 进主服务 bash（覆盖项目默认登录 shell）。失败由 docker compose 自身报错，shell 透传。
exec docker compose -f "$COMPOSE_FILE" exec "$SERVICE" bash