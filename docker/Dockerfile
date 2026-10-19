# syntax=docker/dockerfile:1.7
# Dockerfile.template — flow-engine-doker 基础镜像（容器化项目运行环境）
# gen_project.py 开项目时按此模板生成 <项目>/docker/Dockerfile；项目可在此基础上叠加自定义层。
# 双运行时基座（Python + Node）覆盖现有多 agent 项目主流需求（charter G1）。
# 重项目用多阶段构建自行裁剪，本模板保持最小可用（charter P3 主机零依赖 + §1.2）。

ARG BASE_IMAGE=ubuntu:24.04
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    nodejs npm \
    git curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN corepack enable || true

# pip cache 走容器内（compose volume 持久化到 .doker-cache，跨 build 复用 — S1/S3 判据）
ENV PIP_CACHE_DIR=/workspace/.doker-cache/pip

WORKDIR /workspace

# keep-alive：dev/test/CLI 一律 docker compose exec 进容器（不直接起服务）
CMD ["sleep", "infinity"]