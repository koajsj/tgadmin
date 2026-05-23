from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import ChatPermissions, Message

from bot.schemas.moderation import EnforcementDecision
from bot.services.telegram_retry import call_telegram_with_retry


class ModerationActionError(RuntimeError):
    """Raised when moderation action cannot be executed."""


TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY_SECONDS = 0.6


def decide_escalation(violation_count: int, mute_minutes_step3: int, mute_hours_step4: int) -> EnforcementDecision:
    if violation_count <= 1:
        return EnforcementDecision(action="delete", duration_seconds=None, reason="first_violation_delete_notice", level=1)
    if violation_count == 2:
        return EnforcementDecision(action="warn", duration_seconds=None, reason="second_violation_warn", level=2)
    if violation_count == 3:
        return EnforcementDecision(action="mute", duration_seconds=mute_minutes_step3 * 60, reason="third_violation_short_mute", level=3)
    if violation_count == 4:
        return EnforcementDecision(action="mute", duration_seconds=mute_hours_step4 * 3600, reason="fourth_violation_long_mute", level=4)
    return EnforcementDecision(action="ban", duration_seconds=None, reason="fifth_violation_ban", level=5)


async def try_delete_message(message: Message) -> bool:
    async def _do_delete() -> bool:
        await message.delete()
        return True

    try:
        return await call_telegram_with_retry(
            operation_name="delete_message",
            request_context={"chat_id": message.chat.id if message.chat is not None else None, "message_id": message.message_id},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_delete,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        return False


async def apply_decision(bot: Bot, chat_id: int, user_id: int, decision: EnforcementDecision) -> str:
    if decision.action in {"delete", "warn", "none"}:
        return decision.action

    if decision.action == "mute":
        if decision.duration_seconds is None:
            raise ModerationActionError("mute requires duration_seconds")
        until = datetime.now(timezone.utc) + timedelta(seconds=decision.duration_seconds)
        permissions = ChatPermissions(can_send_messages=False)

        async def _do_mute() -> None:
            await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=permissions, until_date=until)

        try:
            await call_telegram_with_retry(
                operation_name="restrict_chat_member",
                request_context={"chat_id": chat_id, "user_id": user_id, "duration_seconds": decision.duration_seconds},
                retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
                retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
                action=_do_mute,
            )
            return "mute"
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            raise ModerationActionError(f"failed to mute user chat_id={chat_id} user_id={user_id}: {exc}") from exc

    if decision.action == "ban":
        async def _do_ban() -> None:
            await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)

        try:
            await call_telegram_with_retry(
                operation_name="ban_chat_member",
                request_context={"chat_id": chat_id, "user_id": user_id},
                retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
                retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
                action=_do_ban,
            )
            return "ban"
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            raise ModerationActionError(f"failed to ban user chat_id={chat_id} user_id={user_id}: {exc}") from exc

    raise ModerationActionError(f"unsupported action: {decision.action}")


async def unban_user(bot: Bot, chat_id: int, user_id: int) -> None:
    async def _do_unban() -> None:
        await bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=False)

    try:
        await call_telegram_with_retry(
            operation_name="unban_chat_member",
            request_context={"chat_id": chat_id, "user_id": user_id},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_unban,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        raise ModerationActionError(f"failed to unban user chat_id={chat_id} user_id={user_id}: {exc}") from exc


async def mute_user(bot: Bot, chat_id: int, user_id: int, duration_seconds: int) -> None:
    until = datetime.now(timezone.utc) + timedelta(seconds=duration_seconds)
    permissions = ChatPermissions(can_send_messages=False)

    async def _do_mute() -> None:
        await bot.restrict_chat_member(chat_id=chat_id, user_id=user_id, permissions=permissions, until_date=until)

    try:
        await call_telegram_with_retry(
            operation_name="restrict_chat_member",
            request_context={"chat_id": chat_id, "user_id": user_id, "duration_seconds": duration_seconds},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_mute,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        raise ModerationActionError(f"failed to mute user chat_id={chat_id} user_id={user_id}: {exc}") from exc


async def ban_user(bot: Bot, chat_id: int, user_id: int) -> None:
    async def _do_ban() -> None:
        await bot.ban_chat_member(chat_id=chat_id, user_id=user_id)

    try:
        await call_telegram_with_retry(
            operation_name="ban_chat_member",
            request_context={"chat_id": chat_id, "user_id": user_id},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_ban,
        )
    except (TelegramBadRequest, TelegramForbiddenError) as exc:
        raise ModerationActionError(f"failed to ban user chat_id={chat_id} user_id={user_id}: {exc}") from exc
