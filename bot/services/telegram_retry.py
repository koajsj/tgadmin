from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, TypeVar

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter, TelegramServerError


logger = logging.getLogger(__name__)
T = TypeVar("T")


async def call_telegram_with_retry(
    operation_name: str,
    request_context: dict[str, object],
    retry_attempts: int,
    retry_delay_seconds: float,
    action: Callable[[], Awaitable[T]],
) -> T:
    attempt = 1
    while True:
        try:
            return await action()
        except TelegramRetryAfter as exc:
            if attempt >= retry_attempts:
                raise
            retry_after = float(getattr(exc, "retry_after", retry_delay_seconds))
            sleep_seconds = retry_after if retry_after > retry_delay_seconds else retry_delay_seconds
            logger.warning(
                "telegram_retry_after",
                extra={
                    "operation": operation_name,
                    "attempt": attempt,
                    "retry_attempts": retry_attempts,
                    "sleep_seconds": sleep_seconds,
                    "request_context": request_context,
                },
            )
            await asyncio.sleep(sleep_seconds)
            attempt += 1
        except (TelegramNetworkError, TelegramServerError):
            if attempt >= retry_attempts:
                raise
            logger.warning(
                "telegram_transient_error",
                extra={
                    "operation": operation_name,
                    "attempt": attempt,
                    "retry_attempts": retry_attempts,
                    "sleep_seconds": retry_delay_seconds,
                    "request_context": request_context,
                },
            )
            await asyncio.sleep(retry_delay_seconds)
            attempt += 1
