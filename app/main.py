from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

from telegram import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Update,
)
from telegram.constants import ChatMemberStatus
from telegram.error import BadRequest, Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .admin_registry import AdminRegistry
from .config import settings, settings_store
from .learning import AdaptiveKeywordLearner
from .rules import RuleEngine, StrikeTracker, scan_static_reasons


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("moderation-bot")


class AdminCache:
    def __init__(self, ttl_seconds: int) -> None:
        self._ttl = ttl_seconds
        self._cache: dict[tuple[int, int], tuple[bool, float]] = {}

    async def is_admin(self, bot: Any, chat_id: int, user_id: int) -> bool:
        key = (chat_id, user_id)
        now = time.time()
        cached = self._cache.get(key)
        if cached and cached[1] > now:
            return cached[0]

        member = await bot.get_chat_member(chat_id, user_id)
        is_admin = member.status in {ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER}
        self._cache[key] = (is_admin, now + self._ttl)
        return is_admin


rule_engine = RuleEngine(settings)
strike_tracker = StrikeTracker(settings.strike_window_seconds)
admin_cache = AdminCache(settings.admin_cache_ttl_seconds)
admin_registry = AdminRegistry(refresh_interval_seconds=86400)
learner = AdaptiveKeywordLearner(settings_store)


def _is_private_chat(update: Update) -> bool:
    chat = update.effective_chat
    return chat is not None and chat.type == "private"


def _is_owner(user_id: int) -> bool:
    return settings_store.is_owner(user_id)


def _is_private_admin(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    return _is_owner(user.id) or admin_registry.is_admin(user.id)


def _format_seconds(seconds: int) -> str:
    if seconds == 0:
        return "仅删除消息"
    if seconds % 86400 == 0:
        return f"{seconds // 86400}天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}小时"
    if seconds % 60 == 0:
        return f"{seconds // 60}分钟"
    return f"{seconds}秒"


def _flag(value: bool) -> str:
    return "开" if value else "关"


def _action_label(action: str) -> str:
    return "封禁" if action == "ban" else "禁言"


def _format_group_stats_line(item: dict[str, Any]) -> str:
    return (
        f"{item['chat_id']} | 消息 {item['messages_seen']} | "
        f"垃圾 {item['spam_messages']} | 删 {item['deleted_messages']} | "
        f"禁言 {item['muted_messages']} | 封禁 {item['banned_messages']}"
    )


async def _reply_text(update: Update, text: str) -> None:
    message = update.effective_message
    if message is not None:
        await message.reply_text(text)


def _status_text(user_id: int | None) -> str:
    learning_stats = settings_store.learning_stats_snapshot()
    group_totals = settings_store.group_stats_snapshot(None, 5)["totals"]
    learned_snapshot = settings_store.learned_keyword_snapshot(5)
    learned_summary = "，".join(
        f"{item['keyword']}({item['hits']})" for item in learned_snapshot
    ) or "暂无"
    user_line = f"你的 Telegram ID：{user_id}\n" if user_id is not None else ""
    return (
        "后台管理\n"
        f"{user_line}"
        f"主人：{', '.join(str(item) for item in settings_store.owner_user_ids) or '未设置'}\n"
        f"处理动作：{_action_label(settings.action)}\n"
        f"分数阈值：删除 {settings.delete_score_threshold} / 禁言 {settings.mute_score_threshold} / 封禁 {settings.ban_score_threshold}\n"
        f"禁言时长：{_format_seconds(settings.mute_duration_seconds)}\n"
        f"高危关键词：{len(settings.keywords)} 个\n"
        f"学习关键词：{settings_store.learned_keyword_count} 个\n"
        f"忽略词：{len(settings_store.ignored_keywords)} 个\n"
        f"学习样本：{learned_summary}\n"
        f"学习统计：样本 {learning_stats['learned_keywords']} / 忽略 {learning_stats['ignored_keywords']} / 垃圾反馈 {learning_stats['spam_feedback']} / 清洁反馈 {learning_stats['benign_feedback']}\n"
        f"群统计：{group_totals['groups']} 群 / 消息 {group_totals['messages_seen']} / 删除 {group_totals['deleted_messages']} / 禁言 {group_totals['muted_messages']} / 封禁 {group_totals['banned_messages']}\n"
        f"自学习：{_flag(settings.learning_enabled)}\n"
        f"刷屏规则：{settings.flood_max_messages} 条 / {settings.flood_window_seconds} 秒\n"
        "规则开关："
        f"链接{_flag(settings.rule_enable_link)}，"
        f"关键词{_flag(settings.rule_enable_keywords)}，"
        f"用户名{_flag(settings.rule_enable_username)}，"
        f"刷屏{_flag(settings.rule_enable_flood)}，"
        f"重复{_flag(settings.rule_enable_repeat)}，"
        f"超长{_flag(settings.rule_enable_length)}\n"
        f"管理员同步：{admin_registry.admin_count} 人，已记录群 {admin_registry.known_chat_count} 个\n\n"
        "常用命令：\n"
        "/status | /learningstats | /groupstats | /reloadkeywords\n"
        "/action mute | /action ban | /mute 2h | /flood 6 10\n"
        "/addkeyword 关键词 | /delkeyword 关键词 | /learn | /exportkeywords | /importkeywords"
    )


def _toggle_button(label: str, field: str, enabled: bool) -> InlineKeyboardButton:
    return InlineKeyboardButton(f"{label}：{_flag(enabled)}", callback_data=f"toggle:{field}")


def _build_keyboard() -> InlineKeyboardMarkup:
    action_target = "ban" if settings.action == "mute" else "mute"
    keyboard = [
        [
            _toggle_button("链接检测", "rule_enable_link", settings.rule_enable_link),
            _toggle_button("关键词过滤", "rule_enable_keywords", settings.rule_enable_keywords),
        ],
        [
            _toggle_button("用户名过滤", "rule_enable_username", settings.rule_enable_username),
            _toggle_button("自学习", "learning_enabled", settings.learning_enabled),
        ],
        [
            _toggle_button("刷屏拦截", "rule_enable_flood", settings.rule_enable_flood),
            _toggle_button("重复消息", "rule_enable_repeat", settings.rule_enable_repeat),
        ],
        [
            _toggle_button("超长消息", "rule_enable_length", settings.rule_enable_length),
            InlineKeyboardButton(f"处理：切换为{_action_label(action_target)}", callback_data=f"action:{action_target}"),
        ],
        [
            InlineKeyboardButton("刷屏 5条/10秒", callback_data="flood:5:10"),
            InlineKeyboardButton("刷屏 8条/10秒", callback_data="flood:8:10"),
        ],
        [
            InlineKeyboardButton("禁言 1小时", callback_data="mute:3600"),
            InlineKeyboardButton("禁言 24小时", callback_data="mute:86400"),
            InlineKeyboardButton("禁言 7天", callback_data="mute:604800"),
        ],
        [
            InlineKeyboardButton("重新载入词库", callback_data="reload:keywords"),
            InlineKeyboardButton("刷新状态", callback_data="status"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _parse_duration(value: str) -> int | None:
    cleaned_value = value.strip().lower()
    if cleaned_value.isdigit():
        return int(cleaned_value)

    match = re.fullmatch(r"(\d+)([smhd])", cleaned_value)
    if match is None:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "s":
        return amount
    if unit == "m":
        return amount * 60
    if unit == "h":
        return amount * 3600
    if unit == "d":
        return amount * 86400
    return None


async def _reject_private_admin(update: Update) -> None:
    await _reply_text(
        update,
        "未授权。你需要是机器人主人或已同步的群管理员。\n"
        "请先在群里发送一条消息，让机器人记录并同步群管理员列表。",
    )


async def _try_claim_owner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private_chat(update):
        return

    user = update.effective_user
    if user is None or settings_store.owner_user_ids:
        return

    await admin_registry.refresh_known_chats(context.bot)
    if not admin_registry.is_admin(user.id):
        return

    if settings_store.ensure_owner(user.id):
        await _reply_text(update, "已将你设为机器人主人。")


async def _require_private_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_private_chat(update):
        return False

    await admin_registry.refresh_known_chats(context.bot)
    await _try_claim_owner(update, context)
    if _is_private_admin(update):
        return True

    await _reject_private_admin(update)
    return False


async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    user_id = update.effective_user.id if update.effective_user is not None else None
    message = update.effective_message
    if message is not None:
        await message.reply_text(_status_text(user_id), reply_markup=_build_keyboard())


async def admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_panel(update, context)


async def admin_reload_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    count = settings_store.reload_keywords()
    await _reply_text(update, f"已重新载入词库，文件关键词 {count} 条。")


async def admin_set_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    if not context.args:
        await _reply_text(update, "用法：/action mute 或 /action ban")
        return

    try:
        settings_store.set_action(context.args[0])
    except ValueError as exc:
        await _reply_text(update, str(exc))
        return

    await _reply_text(update, f"处理动作已更新为：{_action_label(settings.action)}。")


async def admin_set_mute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    if not context.args:
        await _reply_text(update, "用法：/mute 3600、/mute 2h 或 /mute 1d")
        return

    duration = _parse_duration(context.args[0])
    if duration is None:
        await _reply_text(update, "无法解析时长。支持纯秒数，或 30m、2h、1d。")
        return

    settings_store.set_mute_duration(duration)
    await _reply_text(update, f"禁言时长已更新为：{_format_seconds(duration)}。")


async def admin_set_flood(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    if len(context.args) < 2:
        await _reply_text(update, "用法：/flood 6 10，表示 10 秒内超过 6 条消息。")
        return

    try:
        max_messages = int(context.args[0])
        window_seconds = int(context.args[1])
        settings_store.set_flood_rule(max_messages, window_seconds)
    except ValueError as exc:
        await _reply_text(update, f"参数错误：{exc}")
        return

    await _reply_text(update, f"刷屏规则已更新为：{settings.flood_max_messages} 条 / {settings.flood_window_seconds} 秒。")


async def admin_add_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    keyword = " ".join(context.args).strip()
    if not keyword:
        await _reply_text(update, "用法：/addkeyword 关键词")
        return

    added = settings_store.add_keyword(keyword)
    if added:
        await _reply_text(update, f"已添加并生效：{keyword}")
    else:
        await _reply_text(update, "添加失败：关键词为空或已存在。")


async def admin_del_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    keyword = " ".join(context.args).strip()
    if not keyword:
        await _reply_text(update, "用法：/delkeyword 关键词")
        return

    removed = settings_store.remove_keyword(keyword)
    if removed:
        await _reply_text(update, f"已移除并生效：{keyword}")
    else:
        await _reply_text(update, "移除失败：只能移除通过 /addkeyword 添加的自定义关键词。")


async def admin_toggle_learning(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    new_value = settings_store.toggle("learning_enabled")
    await _reply_text(update, f"自学习功能已{_flag(new_value)}。")


async def admin_learning_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    snapshot = settings_store.learning_stats_snapshot()
    top_keywords = snapshot["top_keywords"]
    top_summary = "，".join(
        f"{item['keyword']}({item['hits']})" for item in top_keywords
    ) or "暂无"
    await _reply_text(
        update,
        "学习情况统计\n"
        f"学习词：{snapshot['learned_keywords']} 个\n"
        f"忽略词：{snapshot['ignored_keywords']} 个\n"
        f"垃圾反馈：{snapshot['spam_feedback']} 次\n"
        f"清洁反馈：{snapshot['benign_feedback']} 次\n"
        f"高频学习样本：{top_summary}",
    )


async def admin_group_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return

    if chat.type == "private":
        if not await _require_private_admin(update, context):
            return
        target_chat_id = None
        if context.args:
            try:
                target_chat_id = int(context.args[0])
            except ValueError:
                await _reply_text(update, "用法：/groupstats 或 /groupstats -1001234567890")
                return
        snapshot = settings_store.group_stats_snapshot(target_chat_id, 5)
    else:
        user = update.effective_user
        if user is None:
            return
        if _is_owner(user.id):
            snapshot = settings_store.group_stats_snapshot(chat.id, 5)
        else:
            await admin_registry.refresh_known_chats(context.bot)
            if not await admin_cache.is_admin(context.bot, chat.id, user.id):
                await _reply_text(update, "无权限查看群统计。")
                return
            snapshot = settings_store.group_stats_snapshot(chat.id, 5)

    totals = snapshot["totals"]
    groups = snapshot["groups"]
    group_lines = "\n".join(_format_group_stats_line(item) for item in groups) or "暂无"
    await _reply_text(
        update,
        "群内数据统计\n"
        f"已记录群：{totals['groups']} 个\n"
        f"累计消息：{totals['messages_seen']} 条\n"
        f"垃圾消息：{totals['spam_messages']} 条\n"
        f"已删除：{totals['deleted_messages']} 条\n"
        f"已禁言：{totals['muted_messages']} 条\n"
        f"已封禁：{totals['banned_messages']} 条\n"
        f"最近记录：\n{group_lines}",
    )


async def admin_export_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    payload = settings_store.export_keywords_payload()
    data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buffer = BytesIO(data)
    buffer.name = "telegram-keywords-export.json"
    await message.reply_document(
        document=InputFile(buffer, filename=buffer.name),
        caption="词库导出文件。",
    )


def _parse_import_payload(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    if stripped.startswith("{"):
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("JSON 词库必须是对象")
        return data
    lines = [
        line.strip()
        for line in stripped.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    return {"custom_keywords": lines}


async def admin_import_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_private_admin(update, context):
        return

    message = update.effective_message
    if message is None:
        return

    payload_text = ""
    document = message.document
    if document is None and message.reply_to_message is not None:
        document = message.reply_to_message.document

    try:
        if document is not None:
            file = await context.bot.get_file(document.file_id)
            payload_text = (await file.download_as_bytearray()).decode("utf-8")
        elif context.args:
            payload_text = " ".join(context.args)
        elif message.reply_to_message is not None and message.reply_to_message.text:
            payload_text = message.reply_to_message.text
    except UnicodeDecodeError as exc:
        await _reply_text(update, f"导入失败：词库文件必须是 UTF-8 文本。{exc}")
        return

    if not payload_text.strip():
        await _reply_text(update, "用法：/importkeywords 后附 JSON 文本、纯文本列表，或上传词库文件后回复该命令。")
        return

    try:
        payload = _parse_import_payload(payload_text)
        imported = settings_store.import_keywords_payload(payload)
    except (ValueError, json.JSONDecodeError) as exc:
        await _reply_text(update, f"导入失败：{exc}")
        return

    await _reply_text(
        update,
        "导入完成："
        f"自定义 {imported['custom']}，"
        f"学习词 {imported['learned']}，"
        f"忽略词 {imported['ignored']}。",
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return

    await admin_registry.refresh_known_chats(context.bot)
    if not _is_private_admin(update):
        await query.answer("无权限", show_alert=True)
        return

    data = query.data or ""
    try:
        if data.startswith("toggle:"):
            field = data.split(":", 1)[1]
            if field not in {
                "rule_enable_link",
                "rule_enable_keywords",
                "rule_enable_username",
                "rule_enable_flood",
                "rule_enable_repeat",
                "rule_enable_length",
                "learning_enabled",
            }:
                await query.answer("未知开关", show_alert=True)
                return
            settings_store.toggle(field)
        elif data.startswith("action:"):
            settings_store.set_action(data.split(":", 1)[1])
        elif data.startswith("mute:"):
            settings_store.set_mute_duration(int(data.split(":", 1)[1]))
        elif data.startswith("flood:"):
            _, max_messages, window_seconds = data.split(":", 2)
            settings_store.set_flood_rule(int(max_messages), int(window_seconds))
        elif data == "reload:keywords":
            settings_store.reload_keywords()
        elif data == "status":
            pass
        else:
            await query.answer("未知操作", show_alert=True)
            return
    except ValueError as exc:
        await query.answer(f"参数错误：{exc}", show_alert=True)
        return

    await query.answer("已更新")
    user_id = update.effective_user.id if update.effective_user is not None else None
    await query.edit_message_text(_status_text(user_id), reply_markup=_build_keyboard())


async def _delete_spam_message(update: Update) -> bool:
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return False

    try:
        await message.delete()
    except (BadRequest, Forbidden) as exc:
        logger.warning("Failed to delete message", extra={"chat_id": chat.id, "error": str(exc)})
        return False

    return True


async def _apply_action(update: Update, context: ContextTypes.DEFAULT_TYPE, score: int) -> str:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None:
        return "none"

    should_ban = score >= settings.ban_score_threshold or (
        settings.action == "ban" and score >= settings.mute_score_threshold
    )
    should_mute = score >= settings.mute_score_threshold and not should_ban

    if should_ban:
        try:
            await context.bot.ban_chat_member(chat.id, user.id)
        except (BadRequest, Forbidden) as exc:
            logger.warning(
                "Failed to ban user",
                extra={"chat_id": chat.id, "user_id": user.id, "error": str(exc)},
            )
        return "banned"

    if not should_mute or settings.mute_duration_seconds <= 0:
        return "none"

    until = datetime.now(timezone.utc) + timedelta(seconds=settings.mute_duration_seconds)
    permissions = ChatPermissions(can_send_messages=False)
    try:
        await context.bot.restrict_chat_member(
            chat.id,
            user.id,
            permissions=permissions,
            until_date=until,
        )
    except (BadRequest, Forbidden) as exc:
        logger.warning(
            "Failed to mute user",
            extra={"chat_id": chat.id, "user_id": user.id, "error": str(exc)},
        )
    return "muted"


async def _apply_strike_ban(update: Update, context: ContextTypes.DEFAULT_TYPE, score: int) -> bool:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or settings.ban_after_strikes <= 0:
        return False
    if score < settings.mute_score_threshold:
        return False

    strikes = strike_tracker.add_strike(chat.id, user.id, time.time())
    if strikes < settings.ban_after_strikes:
        return False

    try:
        await context.bot.ban_chat_member(chat.id, user.id)
    except (BadRequest, Forbidden) as exc:
        logger.warning(
            "Failed to ban user after strikes",
            extra={"chat_id": chat.id, "user_id": user.id, "strikes": strikes, "error": str(exc)},
        )
    return True


async def _log_moderation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    reasons: list[str],
    score: int,
    text: str,
    learned_terms: list[str],
) -> None:
    chat = update.effective_chat
    user = update.effective_user
    if chat is None or user is None or settings.log_chat_id is None:
        return

    reason = ", ".join(reasons) if reasons else "rule"
    learned = f"\n新学习：{', '.join(learned_terms)}" if learned_terms else ""
    text_preview = text.replace("\n", " ")[:200]
    log_text = (
        "已处理消息\n"
        f"群：{chat.id}\n"
        f"用户：{user.id}\n"
        f"分数：{score}\n"
        f"原因：{reason}\n"
        f"预览：{text_preview}"
        f"{learned}"
    )

    try:
        await context.bot.send_message(settings.log_chat_id, log_text)
    except (BadRequest, Forbidden) as exc:
        logger.warning(
            "Failed to send moderation log",
            extra={"log_chat_id": settings.log_chat_id, "error": str(exc)},
        )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    if message is None or chat is None or user is None or user.is_bot:
        return

    if chat.type not in {"group", "supergroup"}:
        return

    admin_registry.mark_chat(chat.id)
    await admin_registry.refresh_chat(context.bot, chat.id)

    settings_store.record_group_seen(chat.id, user.id, time.time())

    if _is_owner(user.id):
        return

    if await admin_cache.is_admin(context.bot, chat.id, user.id):
        return

    text = message.text or message.caption
    if not text:
        return

    result = rule_engine.evaluate(text, chat.id, user.id, username=user.username)
    learned_terms = learner.observe(text, user.id, is_spam=result.is_spam, score=result.score)
    if not result.is_spam:
        return

    deleted = await _delete_spam_message(update)
    if not deleted:
        return

    action_taken = await _apply_action(update, context, result.score)
    if await _apply_strike_ban(update, context, result.score):
        action_taken = "banned"

    settings_store.record_group_spam(chat.id, user.id, action_taken, result.score, time.time())

    await _log_moderation(update, context, result.reasons, result.score, text, learned_terms)


async def refresh_admins_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    await admin_registry.refresh_known_chats(context.bot)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    error = context.error
    if isinstance(error, TelegramError):
        logger.warning("Telegram update failed", extra={"error": str(error), "update": str(update)})
        return
    logger.exception("Unhandled update error", extra={"update": str(update)})


async def post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "打开后台管理"),
        BotCommand("admin", "打开后台管理"),
        BotCommand("status", "查看当前规则状态"),
        BotCommand("reloadkeywords", "重新载入词库"),
        BotCommand("mute", "设置禁言时长"),
        BotCommand("flood", "设置刷屏阈值"),
        BotCommand("action", "设置处理动作"),
        BotCommand("addkeyword", "添加自定义关键词"),
        BotCommand("delkeyword", "删除自定义关键词"),
        BotCommand("learn", "切换自学习功能"),
        BotCommand("learningstats", "查看学习情况统计"),
        BotCommand("groupstats", "查看群内数据统计"),
        BotCommand("exportkeywords", "导出自定义和学习词库"),
        BotCommand("importkeywords", "导入词库 JSON 或文本"),
    ]
    await application.bot.set_my_commands(commands, scope=BotCommandScopeAllPrivateChats())


def main() -> None:
    application = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .post_init(post_init)
        .build()
    )
    application.job_queue.run_repeating(refresh_admins_job, interval=86400, first=60)
    application.add_handler(CommandHandler(["start", "admin"], admin_panel))
    application.add_handler(CommandHandler("status", admin_status))
    application.add_handler(CommandHandler("reloadkeywords", admin_reload_keywords))
    application.add_handler(CommandHandler(["action", "setaction"], admin_set_action))
    application.add_handler(CommandHandler(["mute", "setmute"], admin_set_mute))
    application.add_handler(CommandHandler("flood", admin_set_flood))
    application.add_handler(CommandHandler("addkeyword", admin_add_keyword))
    application.add_handler(CommandHandler("delkeyword", admin_del_keyword))
    application.add_handler(CommandHandler(["learn", "togglelearning"], admin_toggle_learning))
    application.add_handler(CommandHandler("learningstats", admin_learning_stats))
    application.add_handler(CommandHandler("groupstats", admin_group_stats))
    application.add_handler(CommandHandler("exportkeywords", admin_export_keywords))
    application.add_handler(CommandHandler("importkeywords", admin_import_keywords))
    application.add_handler(CallbackQueryHandler(admin_callback))
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))
    application.add_error_handler(error_handler)
    application.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
