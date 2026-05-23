# Debian VPS 详细部署与更新手册

## A. 目标流程

你只需要记住两条命令：

- 首次部署：`sudo bash scripts/setup_debian.sh`
- 后续更新：`bash scripts/update_debian.sh`

首次部署时，脚本只会询问一次 `BOT_TOKEN`，其余配置自动完成。

---

## B. 首次部署（GitHub -> VPS）

### 1) 登录 VPS，安装 Git

```bash
sudo apt update
sudo apt install -y git
```

### 2) 拉取仓库

```bash
git clone https://github.com/koajsj/tgadmin.git
cd tgadmin
```

### 3) 执行一键部署

```bash
sudo bash scripts/setup_debian.sh
```

脚本会自动：
- 安装 Docker / Docker Compose（若缺失）
- 自动创建 `.env`
- 写入你输入的 `BOT_TOKEN`
- 自动生成并写入 `POSTGRES_PASSWORD`
- 预留 `BOT_OWNER_IDS`（Owner 权限用户，按需填写）
- 自动生成 `WEBHOOK_SECRET`
- 启动 `postgres`、`redis`、`bot`
- 执行数据库迁移 `alembic upgrade head`
- PostgreSQL/Redis 仅在 Docker 内网可访问（默认不暴露公网端口）

### 4) 验证

```bash
docker compose ps
docker compose logs -f bot
```

---

## C. 更新流程（以后固定用法）

每次你本地推送新代码后，在 VPS 执行：

```bash
cd tgadmin
bash scripts/update_debian.sh
```

更新脚本会自动完成：
- 拉取最新代码（`git pull --ff-only`）
- 重建并后台启动容器
- 自动执行数据库迁移

---

## D. 本地推送建议流程

```bash
git add .
git commit -m "your message"
git push origin main
```

如果默认分支不是 `main`，替换成你的实际分支。

---

## E. 常用运维命令

```bash
# 查看机器人日志
docker compose logs -f bot

# 查看数据库日志
docker compose logs -f postgres

# 重启机器人
docker compose restart bot

# 停止全部服务
docker compose down

# 启动全部服务
docker compose up -d
```

---

## F. 数据库备份/恢复

### 备份

```bash
docker compose exec -T postgres pg_dump -U postgres tgadmin > backup.sql
```

### 恢复

```bash
cat backup.sql | docker compose exec -T postgres psql -U postgres -d tgadmin
```

---

## G. 防火墙建议（UFW）

```bash
sudo apt install -y ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

---

## H. Webhook（可选，不是 MVP 必需）

当前默认是 Long Polling，不依赖 Nginx。

如果后续切换 Webhook：
1. 准备域名与 HTTPS
2. 使用 `docker/nginx-webhook.conf` 作为模板
3. 设置 `.env` 中 `WEBHOOK_URL` 与 `WEBHOOK_SECRET`
4. 调用 Telegram `setWebhook`

---

## I. 常见问题排查

- 机器人无响应：
  - `docker compose logs -f bot`
  - 检查 `BOT_TOKEN` 是否正确
- 无法删消息/封禁：
  - 确认机器人在群内是管理员
  - 确认有删除消息、限制成员、封禁成员权限
- 数据库连接失败：
  - `docker compose ps` 看 postgres 是否 healthy/running
- 更新失败：
  - 确认本地已推送到 GitHub
  - VPS 执行 `git branch -vv`、`git pull --ff-only`
