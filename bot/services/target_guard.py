from __future__ import annotations

from aiogram import Bot
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from bot.config import Settings
from bot.database import repositories
from bot.database.session import session_scope
from bot.utils.permissions import is_admin_for_chat, is_owner


async def ensure_target_action_allowed(
    bot: Bot,
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    actor_user_id: int,
    chat_id: int,
    target_user_id: int,
) -> tuple[bool, str]:
    actor_is_owner = is_owner(settings, actor_user_id)
    target_is_owner = is_owner(settings, target_user_id)
    if target_is_owner:
        return False, "不能操作 Bot Owner"

    target_is_admin = await is_admin_for_chat(
        bot=bot,
        settings=settings,
        session_factory=session_factory,
        user_id=target_user_id,
        chat_id=chat_id,
    )
    if target_is_admin and not actor_is_owner:
        return False, "目标是该群管理员，仅 Bot Owner 可操作"

    async for session in session_scope(session_factory):
        target_whitelisted = await repositories.is_user_whitelisted(session, chat_id, target_user_id)
        if target_whitelisted and not actor_is_owner:
            return False, "目标在白名单，仅 Bot Owner 可操作"
    return True, "ok"
