#!/usr/bin/env bash
# 宝塔计划任务入口。
# 优先：在宝塔里把「执行用户」设为 deploy，脚本填：
#   /home/deploy/PSE/scripts/sync_today.sh
# 若必须用 root 跑任务，再用本脚本（runuser 无需密码）。
set -euo pipefail

SYNC='/home/deploy/PSE/scripts/sync_today.sh'

if [[ "$(id -un)" == "deploy" ]]; then
  exec bash "$SYNC"
fi

if [[ "$(id -u)" -eq 0 ]]; then
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u deploy -- bash "$SYNC"
  fi
  exec sudo -u deploy -n bash "$SYNC"
fi

echo "ERROR: cron user=$(id -un) cannot switch to deploy." >&2
echo "Fix: 宝塔计划任务 → 执行用户 选 deploy，脚本改为: $SYNC" >&2
exit 1
