from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest, TelegramForbiddenError

from bot.config import Settings
from bot.schemas.permissions import ActorRole, PermissionAction, PermissionDecision
from bot.services.telegram_retry import call_telegram_with_retry


TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY_SECONDS = 0.6


async def is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
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


def is_owner(settings: Settings, user_id: int) -> bool:
    return user_id in settings.owner_ids


async def resolve_role(bot: Bot, settings: Settings, user_id: int, chat_id: int | None) -> ActorRole:
    if is_owner(settings, user_id):
        return ActorRole.OWNER
    if chat_id is not None and await is_group_admin(bot, chat_id, user_id):
        return ActorRole.GROUP_ADMIN
    return ActorRole.MEMBER


def _authorize_by_role(role: ActorRole, settings: Settings, action: PermissionAction, duration_seconds: int | None) -> PermissionDecision:
    if role == ActorRole.OWNER:
        return PermissionDecision(allowed=True, role=role, reason="owner_allowed")

    if role == ActorRole.MEMBER:
        return PermissionDecision(allowed=False, role=role, reason="not_admin")

    if action in {PermissionAction.VIEW_SETTINGS, PermissionAction.VIEW_HISTORY, PermissionAction.WARN, PermissionAction.MUTE_SHORT}:
        return PermissionDecision(allowed=True, role=role, reason="group_admin_low_risk_allowed")

    if action == PermissionAction.MUTE_ANY:
        if duration_seconds is None:
            return PermissionDecision(allowed=False, role=role, reason="missing_duration")
        if duration_seconds <= settings.group_admin_max_mute_seconds:
            return PermissionDecision(allowed=True, role=role, reason="group_admin_short_mute_allowed")
        return PermissionDecision(allowed=False, role=role, reason="group_admin_mute_duration_exceeds_limit")

    return PermissionDecision(allowed=False, role=role, reason="owner_only_action")


async def authorize_action(bot: Bot, settings: Settings, user_id: int, chat_id: int | None, action: PermissionAction, duration_seconds: int | None) -> PermissionDecision:
    role = await resolve_role(bot, settings, user_id, chat_id)
    return _authorize_by_role(role, settings, action, duration_seconds)
