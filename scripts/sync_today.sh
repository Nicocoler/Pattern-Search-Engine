#!/usr/bin/env bash
# 宝塔计划任务：每天 16:30 增量同步当日行情
# 推荐以 deploy 用户执行；若用 root 跑宝塔任务，请用：
#   su - deploy -c '/home/deploy/PSE/scripts/sync_today.sh'
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p "$ROOT/logs" 2>/dev/null || true
LOG="$ROOT/logs/cron_sync_$(date +%Y%m%d).log"
if ! touch "$LOG" 2>/dev/null; then
  LOG="/tmp/pse_cron_sync_$(date +%Y%m%d).log"
fi

{
  echo "==== sync start $(date -Iseconds) user=$(id -un) ===="
  docker compose run --rm --no-deps backend \
    python -m backend.app.data_center.sync_daemon
  echo "==== sync end $(date -Iseconds) ===="
} >>"$LOG" 2>&1

echo "OK: wrote $LOG"
