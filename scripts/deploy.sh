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

echo "==> docker compose build && up"
docker compose build
docker compose up -d --remove-orphans

echo "==> status"
docker compose ps
echo "OK: deploy finished at $(date -Iseconds)"
