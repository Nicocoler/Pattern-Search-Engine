#!/usr/bin/env bash
# 宝塔计划任务：每天 16:30 增量同步当日行情（在 deploy 用户下执行）
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/logs"
LOG="$ROOT/logs/cron_sync_$(date +%Y%m%d).log"

{
  echo "==== sync start $(date -Iseconds) ===="
  docker compose run --rm --no-deps backend \
    python -m backend.app.data_center.sync_daemon
  echo "==== sync end $(date -Iseconds) ===="
} >>"$LOG" 2>&1
