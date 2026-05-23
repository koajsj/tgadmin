from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError


logger = logging.getLogger(__name__)


def build_action_text(action: str, duration_seconds: int | None) -> str:
    if action == "mute" and duration_seconds is not None:
        return f"mute:{duration_seconds}s"
    return action


async def send_log(bot: Bot, log_chat_id: int | None, chat_id: int, user_id: int, reason: str, score: int, action: str, excerpt: str) -> bool:
    if log_chat_id is None:
        return True
    text = (
        "#moderation\n"
        f"chat_id={chat_id}\n"
        f"user_id={user_id}\n"
        f"score={score}\n"
        f"action={action}\n"
        f"reason={reason}\n"
        f"excerpt={excerpt[:180]}"
    )
    try:
        await bot.send_message(chat_id=log_chat_id, text=text)
        return True
    except (TelegramBadRequest, TelegramForbiddenError):
        logger.warning(
            "failed_to_send_moderation_log",
            extra={
                "chat_id": chat_id,
                "log_chat_id": log_chat_id,
                "user_id": user_id,
                "reason": reason,
                "action": action,
            },
        )
        return False
