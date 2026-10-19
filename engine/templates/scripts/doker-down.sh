#!/usr/bin/env bash
# doker-down.sh — 停止并清理项目容器（-v 连匿名卷一起清 = S5 判据）
# flow-engine-doker 容器化项目运行环境（002-design-doc.md §1.4）
# 契约：set -euo pipefail + 失败 stderr 输出 [doker] <动作> failed: <原因> 后 exit 非零（P4 失败显式）
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE_FILE="docker/compose.yaml"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "[doker] down failed: 缺少 $COMPOSE_FILE" >&2
  exit 1
fi

# -v：连匿名卷一起清（doker_pip_cache 是命名卷，会保留——compose 显式声明的命名卷按设计意图留存）
# --remove-orphans：清理 compose 文件已删但仍在跑的容器
if ! docker compose -f "$COMPOSE_FILE" down -v --remove-orphans; then
  echo "[doker] down failed: docker compose down -v --remove-orphans 返回非零（查上方 docker 输出）" >&2
  exit 1
fi

echo "[doker] down ok"