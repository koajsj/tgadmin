from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery
from aiogram import Router

from bot.app_context import AppContext
from bot.database import repositories
from bot.database.session import session_scope
from bot.schemas.permissions import PermissionAction
from bot.services import moderation
from bot.services.management_audit import log_management_event
from bot.services.target_guard import ensure_target_action_allowed
from bot.utils.permissions import authorize_action


router = Router(name="callbacks")


class ModerateAction(CallbackData, prefix="md"):
    action: str
    chat_id: int
    user_id: int


CALLBACK_ACTION_MAP: dict[str, PermissionAction] = {
    "warn": PermissionAction.WARN,
    "mute10": PermissionAction.MUTE_ANY,
    "mute60": PermissionAction.MUTE_ANY,
    "ban": PermissionAction.BAN,
    "wl": PermissionAction.WHITELIST,
    "ignore": PermissionAction.WARN,
}


def _callback_duration(action: str) -> int | None:
    if action == "mute10":
        return 600
    if action == "mute60":
        return 3600
    return None


async def _ensure_target_user_exists(app_context: AppContext, user_id: int) -> None:
    async for session in session_scope(app_context.session_factory):
        await repositories.ensure_user(
            session=session,
            user_id=user_id,
            username=None,
            full_name=None,
            is_bot=False,
            language_code=None,
        )


@router.callback_query(ModerateAction.filter())
async def on_moderate_callback(query: CallbackQuery, callback_data: ModerateAction, app_context: AppContext) -> None:
    actor = query.from_user
    permission_action = CALLBACK_ACTION_MAP.get(callback_data.action)
    if permission_action is None:
        await query.answer("不支持的操作", show_alert=True)
        return

    duration_seconds = _callback_duration(callback_data.action)
    decision = await authorize_action(
        bot=query.bot,
        settings=app_context.settings,
        session_factory=app_context.session_factory,
        user_id=actor.id,
        chat_id=callback_data.chat_id,
        action=permission_action,
        duration_seconds=duration_seconds,
    )

    if not decision.allowed:
        async for session in session_scope(app_context.session_factory):
            await log_management_event(
                session=session,
                chat_id=callback_data.chat_id,
                actor_user_id=actor.id,
                target_user_id=callback_data.user_id,
                action=f"cb_{callback_data.action}",
                decision=decision,
                detail_json={"status": "denied", "callback_data": query.data or ""},
            )
        await query.answer("无权限", show_alert=True)
        return

    action = callback_data.action
    chat_id = callback_data.chat_id
    user_id = callback_data.user_id

    if action == "ignore":
        async for session in session_scope(app_context.session_factory):
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=user_id,
                action="cb_ignore",
                decision=decision,
                detail_json={"status": "success"},
            )
        await query.answer("已忽略")
        return

    if action == "warn":
        allowed, reason = await ensure_target_action_allowed(
            bot=query.bot,
            settings=app_context.settings,
            session_factory=app_context.session_factory,
            actor_user_id=actor.id,
            chat_id=chat_id,
            target_user_id=user_id,
        )
        if not allowed:
            await query.answer(reason, show_alert=True)
            return
        await _ensure_target_user_exists(app_context, user_id)
        async for session in session_scope(app_context.session_factory):
            await repositories.create_punishment(
                session=session,
                violation_id=None,
                chat_id=chat_id,
                user_id=user_id,
                action="warn",
                duration_seconds=None,
                reason="inline_warn",
                executed_by=actor.id,
            )
            await repositories.increment_violation_stats(session, chat_id, user_id, "warn")
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=user_id,
                action="cb_warn",
                decision=decision,
                detail_json={"status": "success"},
            )
        await query.answer("已警告")
        return

    if action in {"mute10", "mute60"}:
        if duration_seconds is None:
            await query.answer("参数错误", show_alert=True)
            return
        allowed, reason = await ensure_target_action_allowed(
            bot=query.bot,
            settings=app_context.settings,
            session_factory=app_context.session_factory,
            actor_user_id=actor.id,
            chat_id=chat_id,
            target_user_id=user_id,
        )
        if not allowed:
            await query.answer(reason, show_alert=True)
            return
        await _ensure_target_user_exists(app_context, user_id)
        try:
            await moderation.mute_user(query.bot, chat_id, user_id, duration_seconds)
        except moderation.ModerationActionError as exc:
            async for session in session_scope(app_context.session_factory):
                await log_management_event(
                    session=session,
                    chat_id=chat_id,
                    actor_user_id=actor.id,
                    target_user_id=user_id,
                    action=f"cb_{action}",
                    decision=decision,
                    detail_json={"status": "failed", "error": str(exc), "duration_seconds": duration_seconds},
                )
            await query.answer(f"执行失败: {exc}", show_alert=True)
            return
        async for session in session_scope(app_context.session_factory):
            await repositories.create_punishment(
                session=session,
                violation_id=None,
                chat_id=chat_id,
                user_id=user_id,
                action="mute",
                duration_seconds=duration_seconds,
                reason=f"inline_{action}",
                executed_by=actor.id,
            )
            await repositories.increment_violation_stats(session, chat_id, user_id, "mute")
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=user_id,
                action=f"cb_{action}",
                decision=decision,
                detail_json={"status": "success", "duration_seconds": duration_seconds},
            )
        await query.answer("已禁言")
        return

    if action == "ban":
        allowed, reason = await ensure_target_action_allowed(
            bot=query.bot,
            settings=app_context.settings,
            session_factory=app_context.session_factory,
            actor_user_id=actor.id,
            chat_id=chat_id,
            target_user_id=user_id,
        )
        if not allowed:
            await query.answer(reason, show_alert=True)
            return
        await _ensure_target_user_exists(app_context, user_id)
        try:
            await moderation.ban_user(query.bot, chat_id, user_id)
        except moderation.ModerationActionError as exc:
            async for session in session_scope(app_context.session_factory):
                await log_management_event(
                    session=session,
                    chat_id=chat_id,
                    actor_user_id=actor.id,
                    target_user_id=user_id,
                    action="cb_ban",
                    decision=decision,
                    detail_json={"status": "failed", "error": str(exc)},
                )
            await query.answer(f"执行失败: {exc}", show_alert=True)
            return

        async for session in session_scope(app_context.session_factory):
            await repositories.create_punishment(
                session=session,
                violation_id=None,
                chat_id=chat_id,
                user_id=user_id,
                action="ban",
                duration_seconds=None,
                reason="inline_ban",
                executed_by=actor.id,
            )
            await repositories.increment_violation_stats(session, chat_id, user_id, "ban")
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=user_id,
                action="cb_ban",
                decision=decision,
                detail_json={"status": "success"},
            )
        await query.answer("已封禁")
        return

    if action == "wl":
        async for session in session_scope(app_context.session_factory):
            await repositories.add_whitelist_user(session, chat_id, user_id)
            await log_management_event(
                session=session,
                chat_id=chat_id,
                actor_user_id=actor.id,
                target_user_id=user_id,
                action="cb_whitelist",
                decision=decision,
                detail_json={"status": "success"},
            )
        await query.answer("已加入白名单")
        return

    await query.answer("不支持的操作", show_alert=True)
