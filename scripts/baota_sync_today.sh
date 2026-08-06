#!/usr/bin/env bash
# 给宝塔「计划任务」用的入口：无论面板以 root 还是其它用户触发，都切到 deploy
set -euo pipefail
exec su - deploy -c '/home/deploy/PSE/scripts/sync_today.sh'
