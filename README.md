# tgadmin（Telegram 群管理机器人）

本项目是在原有雏形上做的增量重构，不是推翻重写。

## 你最关心的结论

- 部署已简化为：**VPS 上执行 `sudo bash scripts/setup_debian.sh`，只输入一次 Bot Token 即可完成**。
- 不需要手动写 `.env`，脚本会自动生成并写入默认配置。
- 更新已简化为：`bash scripts/update_debian.sh`。
- 已内置分级权限：普通群管理员仅可低风险操作，Owner 才可高风险/全局操作。

---

## 1. 当前功能（MVP）

- 基础命令：已实现 `/start /help /warn /mute /ban /unban /history /settings /setlog /reloadkeywords`。
- 私聊面板：支持通过 `/start` 或 `/panel` 打开多级 Inline 管理面板，可在私聊中完成常用管理操作。
- 自动审核能力：已实现关键词过滤、链接过滤、刷屏检测，支持规则命中后自动处置。
- 智能学习能力：基于违规消息、处罚结果、误判反馈与词库命中自动生成候选词/候选规则建议。
- 自动学习巡检：后台定时扫描各群历史违规并自动产出建议，符合阈值的候选会自动进入观察模式（仅日志，不强制处罚）。
- 审核启用机制：新学习词条默认进入候选或观察模式，需 Owner/Admin 审核后才可正式启用。
- 新人管理：支持入群欢迎、新人观察期、观察期内禁链接/禁媒体。
- 阶梯处罚：支持删除 -> 警告 -> 短禁言 -> 长禁言 -> 封禁的升级策略。
- 观察模式：支持只记录/通知不执行处罚，便于上线前调参与误封控制。
- 管理日志：支持日志群推送、快捷处置按钮、审计日志留存。
- 权限体系：支持 Owner 与群管理员分级权限，危险操作默认仅 Owner 可执行。
- 数据与状态：PostgreSQL 存储长期数据，Redis 处理刷屏窗口与短期状态。
- 部署能力：支持 Debian VPS 一键部署与一键更新（Docker Compose）。

---

## 1.1 命令与按钮说明（第二轮）

- 私聊命令：
  - `/start`：打开私聊控制台首页。
  - `/panel`：进入群组选择与管理面板。
  - `/help`：查看命令说明与权限说明。
  - `/status`：查看运行状态与当前配置摘要。
  - `/learn scan <chat_id> <days> <limit>`：生成历史学习建议（仅 Owner）。
  - `/learn list <chat_id> <status|all> <limit>`：查看候选建议（仅 Owner）。
  - `/learn approve <candidate_id> <observe|enable>`：候选审核通过（仅 Owner）。
  - `/learn reject <candidate_id> <reason>`：拒绝候选（仅 Owner）。
  - `/auditexport <chat_id> <json|csv> <days>`：导出审计日志（仅 Owner）。
  - `/groupstats <chat_id> <days>`：群组统计报表（仅 Owner）。
- 群内命令：
  - `/warn <user_id>`：警告目标用户并记录处罚历史（管理员可用）。
  - `/mute <user_id> <10m|1h|1d>`：禁言目标用户（普通管理员受最大禁言时长限制）。
  - `/ban <user_id>`：封禁用户（仅 Owner）。
  - `/unban <user_id>`：解封用户（仅 Owner）。
  - `/history <user_id>`：查看目标用户处罚历史（管理员可用）。
  - `/settings`：查看当前群规则与开关配置（管理员可用）。
  - `/candidate list <status|all> <limit>`：查看本群学习候选（管理员可用）。
  - `/candidate scan <days> <limit>`：按历史违规扫描候选（管理员可用）。
  - `/candidate approve <id> <observe|enable>`：候选审核通过（管理员可用）。
  - `/candidate reject <id> <reason>`：候选拒绝（管理员可用）。
  - `/setlog <log_chat_id>`：设置日志群（仅 Owner）。
  - `/reloadkeywords`：刷新关键词词库（仅 Owner）。
- Inline 按钮（日志群快捷处置）：
  - `警告（记录违规）`：写入处罚记录，不禁言。
  - `禁言10分钟（短期）`：快速短时禁言。
  - `禁言1小时（中期）`：快速中时禁言。
  - `封禁（高风险）`：直接封禁，受权限控制。
  - `忽略（不处罚）`：不执行处罚，仅结束当前处置。
  - `白名单（后续放行）`：加入白名单，降低误杀。
- 私聊面板按钮：
  - 已加入中文短说明文案，例如“运行状态（健康检查）”“学习候选（扫描/审核）”。
  - “数据统计”“审计导出”在私聊面板中为 Owner-only。
  - 所有按钮回调会二次鉴权，不信任 callback_data。

---

## 2. 目录说明（已整理）

```text
bot/                  # 新架构主代码
  handlers/           # 命令、消息、回调、入群请求
  middlewares/        # 私聊 Owner 守卫等访问控制中间层/守卫
  services/           # 规则、处罚、新人、日志
  tasks/              # 后台任务入口（如管理员同步任务）
  database/           # SQLAlchemy 模型、会话、仓储
  schemas/            # 类型定义
  keyboards/          # Inline 键盘构建
  utils/              # 通用权限与工具函数
  main.py             # Bot 入口

scripts/              # 运维脚本
  setup_debian.sh     # 一键部署（只需 Token）
  update_debian.sh    # 一键更新

docs/                 # 文档
  DEPLOY_DEBIAN.md    # Debian 详细部署与排障

docker-compose.yml
Dockerfile
main.py               # 根入口（转发到 bot.main）
setup_debian.sh       # 根快捷入口（调用 scripts/setup_debian.sh）
update_debian.sh      # 根快捷入口（调用 scripts/update_debian.sh）
```

---

## 3. 从本地推送到 GitHub

在你的本地仓库执行：

```bash
git add .
git commit -m "refactor: simplify debian deployment and improve bot architecture"
git push origin main
```

如果你的默认分支不是 `main`，替换成实际分支名。

---

## 4. VPS 首次部署（只输 Token）

> 适用于 Debian VPS。

### 4.1 前置准备（未安装 Git 时先执行）

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git ca-certificates curl
```

可选：设置时区（避免日志时间不一致）

```bash
sudo timedatectl set-timezone Asia/Shanghai
timedatectl
```

### 4.2 拉取代码

```bash
git clone https://github.com/koajsj/tgadmin.git
cd tgadmin
```

### 4.3 一键部署

```bash
sudo bash scripts/setup_debian.sh
```

脚本会自动完成：

1. 安装 Docker / Docker Compose（若未安装）
2. 自动生成 `.env`（若不存在）
3. 写入你输入的 `BOT_TOKEN`
4. 启动 `postgres` / `redis` / `bot`
5. 自动执行 `alembic upgrade head`
6. 默认不对公网暴露 PostgreSQL/Redis 端口（仅容器内访问）

### 4.4 查看运行状态

```bash
docker compose ps
docker compose logs -f bot
```

---

## 5. VPS 更新流程（以后就这几步）

进入项目目录后执行：

```bash
bash scripts/update_debian.sh
```

更新脚本会自动：

1. `git fetch --all --prune`
2. `git pull --ff-only`
3. `docker compose up -d --build`
4. `alembic upgrade head`

---

## 6. 常用运维命令

```bash
# 实时看机器人日志
docker compose logs -f bot

# 重启机器人
docker compose restart bot

# 停止全部服务
docker compose down

# 启动全部服务
docker compose up -d

# 数据库备份
docker compose exec -T postgres pg_dump -U postgres tgadmin > backup.sql

# 数据库恢复
cat backup.sql | docker compose exec -T postgres psql -U postgres -d tgadmin
```

---

## 7. 环境变量说明（自动生成，可后续再改）

首次部署时脚本会自动写入：

- `BOT_TOKEN`
- `POSTGRES_PASSWORD`
- `BOT_OWNER_IDS`（Owner Telegram ID，逗号分隔）
- `DATABASE_URL`
- `REDIS_URL`
- `REDIS_PASSWORD`
- `LOG_LEVEL`
- `ENVIRONMENT`
- `WEBHOOK_SECRET`

你不需要手工创建配置文件。

自动学习相关参数（可选）：
- `LEARNING_AUTO_SCAN_ENABLED`：是否开启后台自动学习巡检。
- `LEARNING_AUTO_SCAN_INTERVAL_SECONDS`：巡检间隔秒数。
- `LEARNING_AUTO_SCAN_DAYS`：历史回溯天数。
- `LEARNING_AUTO_SCAN_LIMIT`：每次扫描最多处理违规记录数。
- `LEARNING_AUTO_PROMOTE_MIN_CONFIDENCE`：自动转观察所需最小置信分。
- `LEARNING_AUTO_PROMOTE_MIN_EVIDENCE`：自动转观察所需最小证据数。
- `LEARNING_AUTO_PROMOTE_MAX_FP_RATIO_PERCENT`：自动转观察允许的最大误判占比。
- `MUTE_AUTO_RELEASE_ENABLED`：是否开启禁言到期自动解除兜底任务。
- `MUTE_AUTO_RELEASE_INTERVAL_SECONDS`：到期巡检间隔秒数。
- `MUTE_AUTO_RELEASE_LOOKBACK_DAYS`：巡检历史禁言窗口天数。

权限规则：
- 群管理员：`warn`、短时 `mute`（不超过 `GROUP_ADMIN_MAX_MUTE_SECONDS`）、查看配置和历史。
- Owner：`ban/unban`、黑白名单、日志群修改、词库刷新、全局敏感操作。
- 回调按钮：每次点击都会重新鉴权，不能靠伪造 callback_data 越权。

---

## 8. 开发与测试

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖：
- MVP 服务层（阶梯处罚、新人限制、规则命中）
- 权限控制、配置解析、缓存与迁移烟雾测试

---

## 9. 按钮无响应修复与本次命令记录（2026-05-23）

### 9.1 修复内容

- 修复私聊面板部分按钮在“页面内容未变化”场景下的无响应问题。
- 根因：Telegram 对相同内容执行 `edit_text` 会返回 `message is not modified`，导致回调流程中断。
- 处理：在 `bot/handlers/private_panel.py` 新增统一安全编辑函数，捕获并识别该错误，改为给出即时提示 `当前页面已是最新`，保证按钮点击始终有反馈。
- 影响范围：`panel:home`、`panel:groups`、`panel:g:*`、`panel:menu:*`、`panel:toggle:*`、`panel:stats:*`、`panel:expask:*`、`panel:expdo:*` 等私聊面板编辑路径。

### 9.2 本次实际执行命令

```powershell
# 1) 目录与仓库定位
Get-ChildItem -Path C:\Users\Administrator\Desktop -Force -ErrorAction SilentlyContinue | Select-Object Name,Mode,LastWriteTime
Get-ChildItem -Path C:\Users\Administrator\Desktop -Directory -Recurse -ErrorAction SilentlyContinue | Where-Object { Test-Path (Join-Path $_.FullName '.git') } | Select-Object FullName,@{N='LastWrite';E={$_.LastWriteTime}} | Sort-Object LastWrite -Descending | Select-Object -First 20

# 2) 项目文件与按钮回调排查
Get-ChildItem -Path C:\Users\Administrator\Desktop\admin -Force | Select-Object Name,Mode,LastWriteTime
Get-Content -Path C:\Users\Administrator\Desktop\admin\README.md -TotalCount 260
rg -n "callback|button|inline|Keyboard|callback_query|CallbackQuery|on_click|router|command" C:\Users\Administrator\Desktop\admin\bot C:\Users\Administrator\Desktop\admin\main.py C:\Users\Administrator\Desktop\admin\docs -S
Get-Content C:\Users\Administrator\Desktop\admin\bot\handlers\private_panel.py -TotalCount 360
Get-Content C:\Users\Administrator\Desktop\admin\bot\handlers\callback.py -TotalCount 360
Get-Content C:\Users\Administrator\Desktop\admin\bot\keyboards\private_panel.py -TotalCount 260
(Get-Content C:\Users\Administrator\Desktop\admin\bot\handlers\private_panel.py | Measure-Object -Line).Lines
Get-Content C:\Users\Administrator\Desktop\admin\bot\handlers\private_panel.py

# 3) 测试验证
python -m unittest discover -s tests -v   # 失败：系统 python.exe 不可访问
C:\Users\Administrator\Desktop\admin\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 4) 更新命令核对
bash scripts/update_debian.sh

# 5) 变更核对
git -c safe.directory=C:/Users/Administrator/Desktop/admin -C C:/Users/Administrator/Desktop/admin --no-pager diff -- bot/handlers/private_panel.py README.md
git -c safe.directory=C:/Users/Administrator/Desktop/admin -C C:/Users/Administrator/Desktop/admin status --short --branch
```

### 9.3 测试结果

- `unittest` 共执行 16 项，全部通过（`OK`）。

---

### 9.4 本次补充核对

```powershell
# 检查旧部署命令是否还有残留
rg -n "sudo bash setup_debian.sh|sudo bash update_debian.sh|bash setup_debian.sh|bash update_debian.sh" C:\Users\Administrator\Desktop\admin -S

# 本机环境检查 bash 是否可用
Get-Command bash

# 查看当前工作区状态
git -c safe.directory=C:/Users/Administrator/Desktop/admin -C C:/Users/Administrator/Desktop/admin status --short --branch
```

- 结果：旧的部署命令引用已清理完毕。
- 结果：当前 Windows 本机环境未安装 `bash`，因此无法在本机直接做 bash 语法检查。

---

### 9.5 选择群组按钮修复

- 修复私聊面板首页 `选择群组（进入群管理）` 按钮在群列表加载阶段无即时反馈的问题。
- 处理：点击后先立即 `answerCallbackQuery`，再加载群列表；如果 Redis 或数据库读取失败，会直接提示 `群组列表加载失败，请稍后重试`。
- 目标：避免用户在群列表加载稍慢时误以为按钮没有响应。

---

## 10. 代码仓库清理约束

- `logs/`、`__pycache__/`、`.venv/`、`*.sqlite3` 已加入 `.gitignore`。
- 不提交缓存文件、临时文件、运行时日志。
- 任何文件删除前需先确认未被引用；重命名/移动后必须同步更新 import。

---

## 9. 生产部署建议

当前推荐：
- `Long Polling + Docker Compose`（简单稳定）

后续如果要高并发再升级：
- `Webhook + Nginx + HTTPS`

详细排障与进阶部署见：
- `docs/DEPLOY_DEBIAN.md`
