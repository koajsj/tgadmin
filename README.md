# Telegram 群管理机器人

这是一个面向 Telegram 群组的自动管理机器人。它会把链接、关键词、学习词、用户名、刷屏、重复消息、超长消息和结构化引流特征合并为风险分，再按分数决定观察、删除、禁言或封禁，尽量降低单个宽泛词造成的误伤。

## 功能

- 分数化处罚：低风险消息先不过度处罚，高分消息才禁言或封禁
- 高危词与学习词分层：新词先进入低权重学习池，再按阈值自动升级
- 自学习自愈：学习词在干净上下文反复出现后会自动沉入忽略词
- 自动白名单：常见正常词在多用户清洁样本中会自动进入忽略词
- 绕过识别：会归一化零宽字符、全角字符、空格和符号插入
- 结构特征：识别联系方式、收益诱导、拉群、博彩、成人引流等组合信号
- 词库管理：支持 `.txt` 词库、自定义词、学习词、忽略词导入导出
- 私聊后台：规则开关、处罚动作、刷屏阈值、禁言时长和学习状态可查看
- 统计面板：可查看学习情况统计和群内数据统计
- 管理员保护：同步群管理员，避免误处理管理消息
- 可选日志群：记录已处理消息的原因、分数和摘要

## Debian VPS 部署

1. 在 BotFather 创建机器人并取得 `BOT_TOKEN`。
2. 把机器人拉进目标群。
3. 给机器人授予删除消息、封禁用户、限制发言权限。
4. 在 Debian 服务器执行：

```bash
git clone https://github.com/koajsj/tgadmin.git telegram-moderation-bot
cd telegram-moderation-bot
sudo bash setup_debian.sh
```

脚本会自动创建项目内 `.venv`、安装依赖、写入 `.env`、创建并启动 `tgadmin.service`。第一次执行只需要输入一次 `Telegram bot token`，不需要手工复制配置文件。

## 管理入口

先让机器人在群里看到一条消息，再私聊它：

```text
/admin
```

若还没有机器人主人，第一位已同步的群管理员打开后台时会自动成为主人。

常用命令：

```text
/status
/reloadkeywords
/action mute
/action ban
/mute 2h
/flood 6 10
/addkeyword 关键词
/delkeyword 关键词
/learn
/learningstats
/groupstats
/exportkeywords
/importkeywords
```

`/status` 会显示风险阈值、学习词数量、忽略词数量、学习统计和群统计总览。`/learningstats` 会单独列出学习情况统计；`/groupstats` 会显示当前群或私聊里指定群的消息统计。`/exportkeywords` 会导出自定义词、学习词和忽略词 JSON；`/importkeywords` 支持 JSON、纯文本词表，或回复一个上传的词库文件导入。

## 学习机制

机器人只会从已触发风险的消息中学习可疑新词。新词需要达到多次命中和多用户阈值后才进入低权重学习池；学习词仍然需要和其它风险信号叠加才更容易触发处罚。

学习词会自动收敛：

- 长期不再出现会退休
- 在干净消息里多次出现会自动降到忽略词
- 常见正常词在多用户清洁样本中会自动进入忽略词
- 达到更高命中阈值后才会升级到高危自定义词

这样可以减少人工维护，也能避免自学习把普通聊天词直接写进高危词库。

## 统计

学习统计和群内数据统计会保存在 `data/state.json`，重启后仍然保留。

- 学习统计：学习词数量、忽略词数量、垃圾反馈次数、清洁反馈次数、当前高频样本
- 群内数据统计：每个群的累计消息、垃圾消息、删除、禁言、封禁次数

## 词库

默认自动加载：

- `data/keywords.txt`
- `data/` 下的所有 `.txt`
- 项目根目录下的所有 `.txt`，但跳过 `requirements.txt`

每行一个关键词；空行和以 `#` 开头的行会被忽略。修改文件词库后，发送 `/reloadkeywords` 或重启服务生效。

运行时状态保存在 `data/state.json`，包括：

- 自定义高危词
- 学习词及命中统计
- 自动忽略词
- 机器人主人 ID

该文件已被 `.gitignore` 排除。

## 配置

`setup_debian.sh` 会自动写入基础 `.env`。如果要精细调参，只需要在 VPS 上编辑 `.env`，不需要重新部署。

关键配置：

- `DELETE_SCORE_THRESHOLD`：达到这个分数才删除消息
- `MUTE_SCORE_THRESHOLD`：达到这个分数才禁言
- `BAN_SCORE_THRESHOLD`：达到这个分数直接封禁
- `BAN_AFTER_STRIKES`：同一用户重复触发高风险消息后的累计封禁阈值
- `LEARNING_MIN_*`：进入学习池前的可疑样本阈值
- `LEARNING_PROMOTE_*`：学习词升级为高危词前的阈值
- `LEARNING_IGNORE_*`：自动忽略正常词的阈值
- `LEARNING_RETIRE_SECONDS`：学习词长期不再出现后的退休时间
- `STRUCTURE_SCORE`：结构化引流特征的基础分

## 运维

```bash
sudo systemctl status tgadmin --no-pager
sudo journalctl -u tgadmin -f
sudo systemctl restart tgadmin
```

更新代码后：

```bash
git pull
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart tgadmin
```

## 卸载删除

如果要删除整个机器人任务和项目文件，在 VPS 上执行：

```bash
sudo systemctl disable --now tgadmin
sudo rm -f /etc/systemd/system/tgadmin.service
sudo systemctl daemon-reload
rm -rf /home/admin/telegram-moderation-bot
```

如果项目目录不在 `/home/admin/telegram-moderation-bot`，把最后一行改成你的实际路径。需要一起清理配置时，再删除项目目录里的 `.env` 和 `.venv`。

## 常见问题

如果创建虚拟环境时提示 `ensurepip is not available`，先安装对应版本的 venv 包，再删除失败的 `.venv` 重新部署：

```bash
sudo apt install python3-venv
rm -rf .venv
sudo bash setup_debian.sh
```

如果报错里明确写的是版本号，比如 `python3.13-venv`，就安装那个版本对应的包。

## 测试

```bash
python -m unittest discover -s tests -v
```

当前测试覆盖自学习、自动忽略、风险评分、结构特征和绕过归一化。
