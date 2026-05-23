from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.database import repositories
from bot.services.telegram_retry import call_telegram_with_retry


TELEGRAM_RETRY_ATTEMPTS = 3
TELEGRAM_RETRY_DELAY_SECONDS = 0.6


async def fetch_chat_admins(bot: Bot, chat_id: int) -> list[dict[str, object]]:
    async def _do_fetch() -> object:
        return await bot.get_chat_administrators(chat_id=chat_id)

    try:
        members = await call_telegram_with_retry(
            operation_name="get_chat_administrators",
            request_context={"chat_id": chat_id},
            retry_attempts=TELEGRAM_RETRY_ATTEMPTS,
            retry_delay_seconds=TELEGRAM_RETRY_DELAY_SECONDS,
            action=_do_fetch,
        )
    except (TelegramBadRequest, TelegramForbiddenError):
        return []

    rows: list[dict[str, object]] = []
    for member in members:
        user = member.user
        rows.append(
            {
                "user_id": user.id,
                "status": member.status,
                "can_delete_messages": bool(getattr(member, "can_delete_messages", False)),
                "can_restrict_members": bool(getattr(member, "can_restrict_members", False)),
                "can_promote_members": bool(getattr(member, "can_promote_members", False)),
                "can_manage_chat": bool(getattr(member, "can_manage_chat", False)),
                "username": user.username,
                "full_name": user.full_name,
                "is_bot": user.is_bot,
                "language_code": user.language_code,
            }
        )
    return rows


async def sync_single_chat_admins(bot: Bot, session: AsyncSession, chat_id: int) -> int:
    rows = await fetch_chat_admins(bot, chat_id)
    for item in rows:
        await repositories.ensure_user(
            session=session,
            user_id=int(item["user_id"]),
            username=str(item.get("username") or "") or None,
            full_name=str(item.get("full_name") or "") or None,
            is_bot=bool(item.get("is_bot", False)),
            language_code=str(item.get("language_code") or "") or None,
        )
    return await repositories.upsert_chat_admins(session, chat_id, rows)


async def sync_all_chats_admins(bot: Bot, session: AsyncSession) -> int:
    chat_ids = await repositories.list_all_chat_ids(session)
    total = 0
    for chat_id in chat_ids:
        total += await sync_single_chat_admins(bot, session, chat_id)
    return total
