#!/usr/bin/env bash
# doker-up.sh — 构建并启动项目容器（幂等）
# flow-engine-doker 容器化项目运行环境（002-design-doc.md §1.4）
# 契约：set -euo pipefail + 失败 stderr 输出 [doker] <动作> failed: <原因> 后 exit 非零（P4 失败显式）
set -euo pipefail

# 任意 cwd 可执行：cd 到脚本所在目录的上一级（项目根）
cd "$(dirname "$0")/.."

# 契约：compose 文件统一 -f docker/compose.yaml
COMPOSE_FILE="docker/compose.yaml"

if [ ! -f "$COMPOSE_FILE" ]; then
  echo "[doker] up failed: 缺少 $COMPOSE_FILE（先生成 docker 目录：参考 flow-engine-doker 002 §1.1）" >&2
  exit 1
fi

# 构建镜像 + 启动服务（已运行时为 no-op 更新）
if ! docker compose -f "$COMPOSE_FILE" up --build -d; then
  echo "[doker] up failed: docker compose up --build -d 返回非零（查上方 docker 输出）" >&2
  exit 1
fi

# 成功后输出当前服务列表（便于脚本调用方/CI 解析）
echo "[doker] up ok: $(docker compose -f "$COMPOSE_FILE" ps --services | tr '\n' ' ')"