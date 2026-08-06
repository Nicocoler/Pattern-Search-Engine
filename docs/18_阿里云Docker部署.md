# 阿里云 Docker 部署手册（PSE）

与 grilling 确认方案一致：Compose（backend + frontend）+ 外挂 PG + 宝塔反代公网 IP + GitHub Actions SSH + 每日 16:30 同步。

## 0. 前置

- 服务器已装 Docker、Docker Compose 插件、宝塔、PostgreSQL 容器（数据已迁）
- 代码在 GitHub；本地 Windows 上的 `PSE_DailyDataSync` 计划任务请停用

## 1. 首次 bootstrap（SSH，root 一次）

```bash
# 建部署用户
useradd -m -s /bin/bash deploy
usermod -aG docker deploy

# 给 deploy 配 SSH 公钥（本机生成的 ed25519 公钥）
mkdir -p /home/deploy/.ssh
# 将公钥写入 /home/deploy/.ssh/authorized_keys
chown -R deploy:deploy /home/deploy/.ssh
chmod 700 /home/deploy/.ssh
chmod 600 /home/deploy/.ssh/authorized_keys
```

以 `deploy` 登录后：

```bash
cd /home/deploy
git clone git@github.com:<YOUR_ORG_OR_USER>/PSE.git PSE
# 若用 HTTPS，需另配 credential；推荐 deploy 专用 deploy key（只读）拉代码

cd /home/deploy/PSE
cp .env.example .env
# 编辑 .env：DATABASE_URL 指向现有 PG
# 容器访问宿主机端口示例：
# DATABASE_URL=postgresql://USER:PASSWORD@host.docker.internal:5432/DBNAME

chmod +x scripts/deploy.sh scripts/sync_today.sh
bash scripts/deploy.sh
```

确认：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/docs
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/
```

若 backend 连不上 PG：检查 PG 是否监听 `0.0.0.0:5432`（或 docker 端口映射），以及 `pg_hba.conf` 是否允许来自 Docker 网桥的连接。

## 2. 宝塔 Nginx

1. 新建站点，域名可填公网 IP 或 `_`
2. 配置文件参考仓库 `deploy/nginx-baota.conf.example`
3. 阿里云安全组 + 宝塔防火墙：80 仅对你的出口 IP 放行；不要对 `0.0.0.0/0` 开放 8000/8080/5432

## 3. GitHub Actions Secrets

仓库 Settings → Secrets and variables → Actions：

| Secret | 含义 |
|--------|------|
| `DEPLOY_HOST` | 服务器公网 IP |
| `DEPLOY_USER` | `deploy` |
| `DEPLOY_SSH_KEY` | 对应 `deploy` 的**私钥**全文 |
| `DEPLOY_PORT` | 可选，默认 22 |

`main` 分支 push 后执行 `.github/workflows/deploy.yml`。也可在 Actions 页手动 `workflow_dispatch`。

## 4. 宝塔计划任务（16:30）

- 类型：Shell 脚本
- 周期：每天 16:30
- 执行用户：`deploy`（若面板只能 root，则 `su - deploy -c '...'`）
- 脚本：

```bash
/home/deploy/PSE/scripts/sync_today.sh
```

日志：`/home/deploy/PSE/logs/cron_sync_YYYYMMDD.log` 与容器内 `logs/sync_daemon.log`。

首次若库为空，可手动全量一次：

```bash
cd /home/deploy/PSE
docker compose run --rm --no-deps backend \
  python -m backend.app.data_center.sync_daemon --full
```

## 5. 验收清单

- [ ] 浏览器（在你的出口 IP 下）打开 `http://公网IP/` 能进前端
- [ ] 前端能拉到模板/布林编排等数据（同域 `/api`）
- [ ] `main` 推送后 Actions 成功，服务器容器已更新
- [ ] 16:30 任务试跑一次成功
- [ ] 本机 Windows 同步计划任务已禁用
