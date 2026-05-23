from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from bot.config import Settings
from bot.database import repositories
from bot.database.session import session_scope
from bot.schemas.permissions import ActorRole, PermissionAction, PermissionDecision
from bot.services.telegram_retry import call_telegram_with_retry


TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY_SECONDS = 0.6


def is_owner(settings: Settings, user_id: int) -> bool:
    return int(user_id) in settings.owner_ids


async def _is_group_admin_live(bot: Bot, chat_id: int, user_id: int) -> bool:
    async def _do_get_member() -> object:
        return await bot.get_chat_member(chat_id=chat_id, user_id=user_id)

    try:
        member = await call_telegram_with_retry(
            operation_name="get_chat_member",
            request_context={"chat_id": chat_id, "user_id": user_id},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_get_member,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        return False
    except TelegramAPIError:
        return False
    return member.status in {"creator", "administrator"}


async def _is_admin_granted(session_factory: async_sessionmaker[AsyncSession], chat_id: int, user_id: int) -> bool:
    async for session in session_scope(session_factory):
        return await repositories.is_admin_granted(session, chat_id, user_id)
    return False


async def is_admin_for_chat(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    chat_id: int,
) -> bool:
    if is_owner(settings, user_id):
        return True
    if await _is_admin_granted(session_factory, chat_id, user_id):
        return True
    return await _is_group_admin_live(bot, chat_id, user_id)


async def resolve_role(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    chat_id: int | None,
) -> ActorRole:
    if is_owner(settings, user_id):
        return ActorRole.OWNER
    if chat_id is not None and await is_admin_for_chat(bot, settings, session_factory, user_id, chat_id):
        return ActorRole.ADMIN
    return ActorRole.MEMBER


def _authorize_by_role(role: ActorRole, settings: Settings, action: PermissionAction, duration_seconds: int | None) -> PermissionDecision:
    _ = settings
    if role == ActorRole.OWNER:
        return PermissionDecision(allowed=True, role=role, reason="owner_allows_all")

    if role == ActorRole.MEMBER:
        return PermissionDecision(allowed=False, role=role, reason="not_group_admin")

    if action in {
        PermissionAction.VIEW_SETTINGS,
        PermissionAction.VIEW_HISTORY,
        PermissionAction.WARN,
        PermissionAction.MUTE_SHORT,
        PermissionAction.MUTE_ANY,
        PermissionAction.BAN,
        PermissionAction.UNBAN,
        PermissionAction.WHITELIST,
        PermissionAction.BLACKLIST,
    }:
        return PermissionDecision(allowed=True, role=role, reason="admin_allowed_group_scope")

    if action in {PermissionAction.EXPORT_DATA, PermissionAction.SET_LOG, PermissionAction.RELOAD_KEYWORDS, PermissionAction.GLOBAL_CONFIG}:
        return PermissionDecision(allowed=False, role=role, reason="owner_only_action")

    return PermissionDecision(allowed=False, role=role, reason="unsupported_action")


async def authorize_action(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    user_id: int,
    chat_id: int | None,
    action: PermissionAction,
    duration_seconds: int | None,
) -> PermissionDecision:
    role = await resolve_role(
        bot=bot,
        settings=settings,
        session_factory=session_factory,
        user_id=user_id,
        chat_id=chat_id,
    )
    if role == ActorRole.ADMIN and action == PermissionAction.MUTE_ANY:
        if duration_seconds is None:
            return PermissionDecision(allowed=False, role=role, reason="missing_duration")
        if duration_seconds > settings.group_admin_max_mute_seconds:
            return PermissionDecision(allowed=False, role=role, reason="mute_duration_exceeds_admin_limit")
    return _authorize_by_role(role, settings, action, duration_seconds)
