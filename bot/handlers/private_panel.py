from __future__ import annotations

from datetime import datetime, timedelta, timezone
from io import BytesIO
import json

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, CallbackQuery, InlineKeyboardMarkup, Message
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import text

from bot.app_context import AppContext
from bot.database import repositories
from bot.database.session import session_scope
from bot.keyboards.private_panel import (
    back_home_keyboard,
    export_confirm_keyboard,
    export_keyboard,
    group_panel_keyboard,
    groups_keyboard,
    home_keyboard,
    rules_keyboard,
)
from bot.schemas.permissions import PermissionAction
from bot.services.audit_export import export_audit_logs_csv, export_audit_logs_json
from bot.services.management_audit import log_management_event
from bot.services.statistics import build_chat_statistics_report
from bot.utils.permissions import authorize_action, is_owner


router = Router(name="private_panel")
GROUPS_PAGE_SIZE = 8
GROUPS_CACHE_TTL_SECONDS = 90


def _panel_home_text() -> str:
    return (
        "私聊管理控制台\n"
        "- 请选择群组进入控制面板\n"
        "- 按钮说明：\n"
        "  选择群组：进入群管理\n"
        "  运行状态：查看数据库/Redis/API健康状态\n"
        "- 所有操作都会再次校验权限并写入审计日志\n"
        "- 危险操作需要二次确认"
    )


def _groups_cache_key(user_id: int) -> str:
    return f"panel:groups:{user_id}"


def _serialize_groups(items: list[tuple[int, str]]) -> str:
    payload = [{"chat_id": chat_id, "title": title} for chat_id, title in items]
    return json.dumps(payload, ensure_ascii=False)


def _deserialize_groups(raw: str) -> list[tuple[int, str]] | None:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list):
        return None
    result: list[tuple[int, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            return None
        chat_id = item.get("chat_id")
        title = item.get("title")
        if not isinstance(chat_id, int) or not isinstance(title, str):
            return None
        result.append((chat_id, title))
    return result


async def _load_cached_groups(app_context: AppContext, user_id: int) -> list[tuple[int, str]] | None:
    cache_value = await app_context.redis.get(_groups_cache_key(user_id))
    if cache_value is None:
        return None
    return _deserialize_groups(cache_value)


async def _store_cached_groups(app_context: AppContext, user_id: int, groups: list[tuple[int, str]]) -> None:
    await app_context.redis.set(
        _groups_cache_key(user_id),
        _serialize_groups(groups),
        ex=GROUPS_CACHE_TTL_SECONDS,
    )


async def _groups_for_user_uncached(message: Message, app_context: AppContext) -> list[tuple[int, str]]:
    user = message.from_user
    if user is None:
        return []

    async for session in session_scope(app_context.session_factory):
        chats = await repositories.list_chats_for_panel(session)

    if is_owner(app_context.settings, user.id):
        return [(item.id, item.title or "(无标题群组)") for item in chats]

    result: list[tuple[int, str]] = []
    for item in chats:
        decision = await authorize_action(
            bot=message.bot,
            settings=app_context.settings,
            session_factory=app_context.session_factory,
            user_id=user.id,
            chat_id=item.id,
            action=PermissionAction.VIEW_SETTINGS,
            duration_seconds=None,
        )
        if decision.allowed:
            result.append((item.id, item.title or "(无标题群组)"))
    return result


async def _groups_for_user(message: Message, app_context: AppContext) -> list[tuple[int, str]]:
    user = message.from_user
    if user is None:
        return []
    cached = await _load_cached_groups(app_context, user.id)
    if cached is not None:
        return cached
    groups = await _groups_for_user_uncached(message, app_context)
    await _store_cached_groups(app_context, user.id, groups)
    return groups


async def _show_home(message: Message) -> None:
    await message.answer(_panel_home_text(), reply_markup=home_keyboard())


def _is_message_not_modified_error(error: TelegramBadRequest) -> bool:
    return "message is not modified" in str(error).lower()


async def _edit_panel_text(
    query: CallbackQuery,
    text_value: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    if query.message is None:
        raise RuntimeError("panel callback edit failed: callback message is missing")
    try:
        await query.message.edit_text(text_value, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if _is_message_not_modified_error(exc):
            await query.answer("当前页面已是最新")
            return
        raise
    await query.answer()


def _parse_int_part(parts: list[str], index: int) -> int | None:
    if len(parts) <= index:
        return None
    raw = parts[index].strip()
    if raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


async def _authorize_panel_action(
    query: CallbackQuery,
    app_context: AppContext,
    chat_id: int | None,
    action: PermissionAction,
    duration_seconds: int | None,
    target_user_id: int | None,
    audit_action: str,
) -> bool:
    actor = query.from_user
    if actor is None:
        await query.answer("无权限", show_alert=True)
        return False

    if chat_id is None:
        if is_owner(app_context.settings, actor.id):
            return True
        async for session in session_scope(app_context.session_factory):
            await log_management_event(
                session=session,
                chat_id=None,
                actor_user_id=actor.id,
                target_user_id=target_user_id,
                action=audit_action,
                decision=None,
                detail_json={"status": "denied", "reason": "owner_only_global_panel"},
            )
        await query.answer("无权限", show_alert=True)
        return False

    decision = await authorize_action(
        bot=query.bot,
        settings=app_context.settings,
        session_factory=app_context.session_factory,
        user_id=actor.id,
        chat_id=chat_id,
        action=action,
        duration_seconds=duration_seconds,
    )
    if decision.allowed:
        return True

    async for session in session_scope(app_context.session_factory):
        await log_management_event(
            session=session,
            chat_id=chat_id,
            actor_user_id=actor.id,
            target_user_id=target_user_id,
            action=audit_action,
            decision=decision,
            detail_json={"status": "denied", "callback_data": query.data or ""},
        )
    await query.answer("无权限", show_alert=True)
    return False


@router.message(F.chat.type == "private", Command("start"))
@router.message(F.chat.type == "private", Command("panel"))
async def open_panel(message: Message, app_context: AppContext) -> None:
    await _show_home(message)


@router.callback_query(F.data.startswith("panel:"))
async def panel_callback(query: CallbackQuery, app_context: AppContext) -> None:
    if query.message is None:
        await query.answer("无效消息", show_alert=True)
        return
    if query.message.chat.type != "private":
        await query.answer("仅支持私聊控制台操作", show_alert=True)
        return

    data = query.data or ""
    parts = data.split(":")
    actor = query.from_user

    if len(parts) < 2:
        await query.answer("无效操作", show_alert=True)
        return

    cmd = parts[1]

    if cmd == "home":
        await _edit_panel_text(query, _panel_home_text(), home_keyboard())
        return

    if cmd == "groups":
        page = 0
        if len(parts) >= 3:
            parsed_page = _parse_int_part(parts, 2)
            if parsed_page is None or parsed_page < 0:
                await query.answer("参数错误", show_alert=True)
                return
            page = parsed_page
        await query.answer()
        try:
            groups = await _groups_for_user(query.message, app_context)
        except (RedisError, SQLAlchemyError):
            await query.message.answer("群组列表加载失败，请稍后重试")
            return

        if len(groups) == 0:
            await query.message.edit_text("当前没有可操作群组。", reply_markup=home_keyboard())
            return

        total_pages = (len(groups) + GROUPS_PAGE_SIZE - 1) // GROUPS_PAGE_SIZE
        if page >= total_pages:
            page = 0
        start = page * GROUPS_PAGE_SIZE
        page_groups = groups[start : start + GROUPS_PAGE_SIZE]
        await query.message.edit_text(
            f"请选择群组（第 {page + 1}/{total_pages} 页）",
            reply_markup=groups_keyboard(page_groups, page, total_pages),
        )
        return

    if cmd == "runtime":
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=None,
            action=PermissionAction.GLOBAL_CONFIG,
            duration_seconds=None,
            target_user_id=None,
            audit_action="panel_runtime",
        ):
            return

        db_ok = False
        redis_ok = False
        telegram_ok = False
        recent_failures = 0
        env_name = app_context.settings.environment

        try:
            async for session in session_scope(app_context.session_factory):
                await session.execute(text("SELECT 1"))
                db_ok = True
                since = datetime.now(timezone.utc) - timedelta(days=1)
                rows = await repositories.list_audit_logs(
                    session=session,
                    chat_id=None,
                    actor_user_id=None,
                    action_prefix="",
                    since=since,
                    limit=500,
                )
                recent_failures = sum(
                    1
                    for item in rows
                    if isinstance(item.detail_json, dict) and item.detail_json.get("status") == "failed"
                )
        except SQLAlchemyError:
            db_ok = False

        try:
            await app_context.redis.ping()
            redis_ok = True
        except RedisError:
            redis_ok = False

        try:
            await query.bot.get_me()
            telegram_ok = True
        except TelegramAPIError:
            telegram_ok = False

        runtime_text = (
            "运行状态\n"
            f"environment={env_name}\n"
            f"db={'ok' if db_ok else 'fail'}\n"
            f"redis={'ok' if redis_ok else 'fail'}\n"
            f"telegram_api={'ok' if telegram_ok else 'fail'}\n"
            f"keyword_cache_refresh={app_context.settings.keyword_refresh_seconds}s\n"
            f"recent_failed_ops_24h={recent_failures}\n"
            f"owner_ids={','.join(str(item) for item in app_context.settings.owner_ids) or '(empty)'}"
        )
        await _edit_panel_text(query, runtime_text, home_keyboard())
        return

    if cmd == "g" and len(parts) >= 3:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.VIEW_SETTINGS,
            duration_seconds=None,
            target_user_id=None,
            audit_action="panel_group_open",
        ):
            return
        await _edit_panel_text(
            query,
            f"群组控制台\nchat_id={chat_id}",
            group_panel_keyboard(chat_id),
        )
        return

    if cmd == "menu" and len(parts) >= 4:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        menu = parts[3]
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.VIEW_SETTINGS,
            duration_seconds=None,
            target_user_id=None,
            audit_action=f"panel_menu_{menu}",
        ):
            return

        if menu == "rules":
            async for session in session_scope(app_context.session_factory):
                chat = await repositories.get_chat_settings(session, chat_id)
                if chat is None:
                    await query.answer("群组不存在", show_alert=True)
                    return
                mode = repositories.get_chat_enforcement_mode(chat)
                text_body = (
                    "规则设置\n"
                    "普通管理员可查看，开关修改仅 Owner 可执行。\n"
                    f"当前处罚模式: {'观察模式' if mode == 'observe' else '安全模式(执行)'}"
                )
                await _edit_panel_text(
                    query,
                    text_body,
                    rules_keyboard(
                        chat_id,
                        chat.newcomer_restrict_enabled,
                        chat.keyword_filter_enabled,
                        chat.link_filter_enabled,
                        chat.flood_enabled,
                        mode,
                    ),
                )
            return

        if menu == "keywords":
            keywords = app_context.keyword_store.get_keywords()
            text_body = (
                "关键词/域名管理\n"
                f"关键词总数: {len(keywords)}\n"
                "支持分类词库、风险等级、白名单域名/词。\n"
                "私聊 Owner 命令:\n"
                "/lexicon stats|search|add|del|enable|disable|import|export\n"
                "提示: Owner 可使用 /reloadkeywords 刷新词库。"
            )
            await _edit_panel_text(query, text_body, back_home_keyboard(chat_id))
            return

        if menu == "users":
            text_body = (
                "用户管理\n"
                "私聊执行命令模板：\n"
                "/warn <user_id>\n"
                "/mute <user_id> 10m\n"
                "/ban <user_id> (Owner)\n"
                "说明：所有命令会按分级权限再次校验。"
            )
            await _edit_panel_text(query, text_body, back_home_keyboard(chat_id))
            return

        if menu == "lists":
            text_body = (
                "白名单/黑名单\n"
                "命令模板：\n"
                "/whitelist <user_id> (Owner)\n"
                "/blacklist <user_id> [reason] (Owner)"
            )
            await _edit_panel_text(query, text_body, back_home_keyboard(chat_id))
            return

        if menu == "newcomer":
            async for session in session_scope(app_context.session_factory):
                chat = await repositories.get_chat_settings(session, chat_id)
                if chat is None:
                    await query.answer("群组不存在", show_alert=True)
                    return
                text_body = (
                    "新人限制\n"
                    f"当前状态: {'开启' if chat.newcomer_restrict_enabled else '关闭'}\n"
                    f"观察期(秒): {chat.newcomer_watch_seconds}\n"
                    f"允许链接: {chat.allow_links}\n"
                    f"允许媒体: {chat.allow_media}"
                )
            await _edit_panel_text(query, text_body, back_home_keyboard(chat_id))
            return

        if menu == "export":
            if not await _authorize_panel_action(
                query=query,
                app_context=app_context,
                chat_id=chat_id,
                action=PermissionAction.EXPORT_DATA,
                duration_seconds=None,
                target_user_id=None,
                audit_action="panel_export_open",
            ):
                return
            await _edit_panel_text(query, "审计日志导出（敏感操作）", export_keyboard(chat_id))
            return

        if menu == "learning":
            text_body = (
                "学习候选审核\n"
                "自动学习来源: 历史违规、处罚结果、误判反馈、词库命中。\n"
                "默认仅进入候选/观察，不会直接强制处罚。\n"
                "群内命令（管理员可用）:\n"
                "/candidate list all 20\n"
                "/candidate scan 30 2000\n"
                "/candidate approve <id> observe|enable\n"
                "/candidate reject <id> <reason>"
            )
            await _edit_panel_text(query, text_body, back_home_keyboard(chat_id))
            return

    if cmd == "toggle" and len(parts) >= 4:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        field = parts[3]
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.GLOBAL_CONFIG,
            duration_seconds=None,
            target_user_id=None,
            audit_action=f"panel_toggle_{field}",
        ):
            return

        async for session in session_scope(app_context.session_factory):
            chat = await repositories.get_chat_settings(session, chat_id)
            if chat is None:
                await query.answer("群组不存在", show_alert=True)
                return

            newcomer = chat.newcomer_restrict_enabled
            keyword = chat.keyword_filter_enabled
            link = chat.link_filter_enabled
            flood = chat.flood_enabled
            mode = repositories.get_chat_enforcement_mode(chat)

            if field == "newcomer":
                newcomer = not newcomer
            elif field == "keyword":
                keyword = not keyword
            elif field == "link":
                link = not link
            elif field == "flood":
                flood = not flood
            elif field == "mode":
                mode = "enforce" if mode == "observe" else "observe"
                await repositories.set_chat_enforcement_mode(session, chat_id, mode)
            else:
                await query.answer("未知开关", show_alert=True)
                return

            await repositories.set_chat_switches(
                session=session,
                chat_id=chat_id,
                newcomer_restrict_enabled=newcomer,
                keyword_filter_enabled=keyword,
                link_filter_enabled=link,
                flood_enabled=flood,
            )
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=None,
                action=f"panel_toggle_{field}",
                decision=None,
                detail_json={
                    "status": "success",
                    "newcomer_restrict_enabled": newcomer,
                    "keyword_filter_enabled": keyword,
                    "link_filter_enabled": link,
                    "flood_enabled": flood,
                    "enforcement_mode": mode,
                },
            )

        await _edit_panel_text(query, "规则设置已更新", rules_keyboard(chat_id, newcomer, keyword, link, flood, mode))
        return

    if cmd == "stats" and len(parts) >= 3:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.GLOBAL_CONFIG,
            duration_seconds=None,
            target_user_id=None,
            audit_action="panel_stats_owner",
        ):
            return

        async for session in session_scope(app_context.session_factory):
            report = await build_chat_statistics_report(session, chat_id, 7)
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=None,
                action="panel_stats",
                decision=None,
                detail_json={"status": "success", "window_days": 7},
            )

        await _edit_panel_text(query, report, back_home_keyboard(chat_id))
        return

    if cmd == "expask" and len(parts) >= 4:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        fmt = parts[3]
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.EXPORT_DATA,
            duration_seconds=None,
            target_user_id=None,
            audit_action="panel_export_confirm_prompt",
        ):
            return
        await _edit_panel_text(
            query,
            f"导出审计日志 ({fmt.upper()}) 是敏感操作，是否继续？",
            export_confirm_keyboard(chat_id, fmt),
        )
        return

    if cmd == "expdo" and len(parts) >= 4:
        chat_id = _parse_int_part(parts, 2)
        if chat_id is None:
            await query.answer("参数错误", show_alert=True)
            return
        fmt = parts[3]
        if not await _authorize_panel_action(
            query=query,
            app_context=app_context,
            chat_id=chat_id,
            action=PermissionAction.EXPORT_DATA,
            duration_seconds=None,
            target_user_id=None,
            audit_action="panel_export_execute",
        ):
            return

        async for session in session_scope(app_context.session_factory):
            if fmt == "json":
                payload = await export_audit_logs_json(session, chat_id, None, None, 7, 5000)
                filename = f"audit-{chat_id}.json"
            elif fmt == "csv":
                payload = await export_audit_logs_csv(session, chat_id, None, None, 7, 5000)
                filename = f"audit-{chat_id}.csv"
            else:
                await query.answer("不支持的格式", show_alert=True)
                return

            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=None,
                action="panel_export_execute",
                decision=None,
                detail_json={"status": "success", "format": fmt, "window_days": 7},
            )

        stream = BytesIO(payload)
        document = BufferedInputFile(stream.getvalue(), filename=filename)
        await query.message.answer_document(document=document, caption="审计日志导出")
        await _edit_panel_text(query, "导出完成", group_panel_keyboard(chat_id))
        return

    await query.answer("无效操作", show_alert=True)
