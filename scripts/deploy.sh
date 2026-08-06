#!/usr/bin/env bash
# 服务器端一键部署：由 GitHub Actions SSH 调用，也可手动执行
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ ! -f .env ]]; then
  echo "ERROR: missing $ROOT/.env — copy .env.example and set DATABASE_URL" >&2
  exit 1
fi

echo "==> git pull (ff-only)"
git fetch origin main
git checkout main
git pull --ff-only origin main

echo "==> docker compose build && up"
docker compose build
docker compose up -d --remove-orphans

echo "==> status"
docker compose ps
echo "OK: deploy finished at $(date -Iseconds)"
