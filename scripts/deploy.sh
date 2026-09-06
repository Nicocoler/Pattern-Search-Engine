#!/usr/bin/env bash
# 服务器端一键部署：git pull + compose build/up
# GitHub Actions 与 SSH 手动部署均直接调用本脚本。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: missing $ROOT/.env — copy .env.example and set DATABASE_URL" >&2
  exit 1
fi

if [[ "${SKIP_GIT_PULL:-0}" != "1" ]]; then
  echo "==> git pull (ff-only)"
  git fetch origin main
  git checkout main
  git pull --ff-only origin main
else
  echo "==> skip git pull (SKIP_GIT_PULL=1)"
fi

# 小规格云主机：并行 build 会把 1～2 核打满并拖慢已在跑的容器。
# 默认串行（frontend → backend）+ nice；DEPLOY_PARALLEL=1 可恢复并行。
NICE_CMD=(nice -n "${DEPLOY_NICE:-10}")
if ! command -v nice >/dev/null 2>&1; then
  NICE_CMD=()
fi

echo "==> docker compose build && up"
if [[ "${DEPLOY_PARALLEL:-0}" == "1" ]]; then
  echo "    (parallel build; DEPLOY_PARALLEL=1)"
  "${NICE_CMD[@]}" docker compose build
else
  echo "    (serial: frontend, then backend)"
  "${NICE_CMD[@]}" docker compose build frontend
  "${NICE_CMD[@]}" docker compose build backend
fi
docker compose up -d --remove-orphans

echo "==> status"
docker compose ps
echo "OK: deploy finished at $(date -Iseconds)"
