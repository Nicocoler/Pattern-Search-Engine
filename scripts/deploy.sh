#!/usr/bin/env bash
# 服务器端一键部署：compose build/up
# GitHub Actions 会先 rsync 代码再调用本脚本（SKIP_GIT_PULL=1），避免服务器访问 GitHub。
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
  echo "==> skip git pull (code already synced by CI)"
fi

echo "==> docker compose build && up"
docker compose build
docker compose up -d --remove-orphans

echo "==> status"
docker compose ps
echo "OK: deploy finished at $(date -Iseconds)"
