from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DEFAULT_BOT_OWNER_ID = 1095020773


class SettingsError(ValueError):
    """Raised when required settings are missing or invalid."""


@dataclass(frozen=True)
class Settings:
    bot_token: str
    database_url: str
    redis_url: str
    log_level: str
    owner_ids: tuple[int, ...]
    default_log_chat_id: int | None
    environment: str
    webhook_url: str | None
    webhook_secret: str | None
    newcomer_watch_seconds: int
    newcomer_allow_links: bool
    newcomer_allow_media: bool
    flood_window_seconds: int
    flood_max_messages: int
    mute_minutes_step3: int
    mute_hours_step4: int
    auto_init_schema: bool
    keyword_refresh_seconds: int
    group_admin_max_mute_seconds: int
    admin_sync_interval_seconds: int


def _read_text(key: str) -> str:
    value = os.getenv(key, "").strip()
    if value == "":
        raise SettingsError(f"Missing required environment variable: {key}")
    return value


def _read_optional_text(key: str) -> str | None:
    value = os.getenv(key, "").strip()
    if value == "":
        return None
    return value


def _read_int(key: str, fallback: int) -> int:
    raw = os.getenv(key, "").strip()
    if raw == "":
        return fallback
    try:
        return int(raw)
    except ValueError as exc:
        raise SettingsError(f"Invalid integer for {key}: {raw}") from exc


def _read_bool(key: str, fallback: bool) -> bool:
    raw = os.getenv(key, "").strip().lower()
    if raw == "":
        return fallback
    if raw in {"1", "true", "yes", "y", "on"}:
        return True
    if raw in {"0", "false", "no", "n", "off"}:
        return False
    raise SettingsError(f"Invalid boolean for {key}: {raw}")


def _read_id_list(key: str) -> tuple[int, ...]:
    raw = os.getenv(key, "").strip()
    if raw == "":
        return tuple()
    parts = [item.strip() for item in raw.split(",") if item.strip()]
    result: list[int] = []
    for part in parts:
        try:
            result.append(int(part))
        except ValueError as exc:
            raise SettingsError(f"Invalid id in {key}: {part}") from exc
    return tuple(result)


def _read_log_level(default: str) -> str:
    value = os.getenv("LOG_LEVEL", default).strip().upper()
    if value == "":
        return default
    return value


def load_settings() -> Settings:
    log_chat_raw = _read_optional_text("DEFAULT_LOG_CHAT_ID")
    if log_chat_raw is None:
        default_log_chat_id = None
    else:
        try:
            default_log_chat_id = int(log_chat_raw)
        except ValueError as exc:
            raise SettingsError(f"Invalid integer for DEFAULT_LOG_CHAT_ID: {log_chat_raw}") from exc

    owner_id_set: set[int] = set()
    for key in ("BOT_OWNER_IDS", "OWNER_IDS", "ADMIN_IDS"):
        owner_id_set.update(_read_id_list(key))
    owner_id_set.add(DEFAULT_BOT_OWNER_ID)
    owner_ids = tuple(sorted(owner_id_set))

    newcomer_watch_seconds = _read_int("NEWCOMER_WATCH_SECONDS", 86400)
    flood_window_seconds = _read_int("FLOOD_WINDOW_SECONDS", 10)
    flood_max_messages = _read_int("FLOOD_MAX_MESSAGES", 5)
    mute_minutes_step3 = _read_int("MUTE_MINUTES_STEP3", 10)
    mute_hours_step4 = _read_int("MUTE_HOURS_STEP4", 24)
    keyword_refresh_seconds = _read_int("KEYWORD_REFRESH_SECONDS", 60)
    group_admin_max_mute_seconds = _read_int("GROUP_ADMIN_MAX_MUTE_SECONDS", 3600)
    admin_sync_interval_seconds = _read_int("ADMIN_SYNC_INTERVAL_SECONDS", 86400)

    if newcomer_watch_seconds < 0:
        raise SettingsError("NEWCOMER_WATCH_SECONDS must be >= 0")
    if flood_window_seconds <= 0:
        raise SettingsError("FLOOD_WINDOW_SECONDS must be > 0")
    if flood_max_messages <= 0:
        raise SettingsError("FLOOD_MAX_MESSAGES must be > 0")
    if mute_minutes_step3 <= 0:
        raise SettingsError("MUTE_MINUTES_STEP3 must be > 0")
    if mute_hours_step4 <= 0:
        raise SettingsError("MUTE_HOURS_STEP4 must be > 0")
    if keyword_refresh_seconds <= 0:
        raise SettingsError("KEYWORD_REFRESH_SECONDS must be > 0")
    if group_admin_max_mute_seconds <= 0:
        raise SettingsError("GROUP_ADMIN_MAX_MUTE_SECONDS must be > 0")
    if admin_sync_interval_seconds <= 0:
        raise SettingsError("ADMIN_SYNC_INTERVAL_SECONDS must be > 0")

    return Settings(
        bot_token=_read_text("BOT_TOKEN"),
        database_url=_read_text("DATABASE_URL"),
        redis_url=_read_text("REDIS_URL"),
        log_level=_read_log_level("INFO"),
        owner_ids=owner_ids,
        default_log_chat_id=default_log_chat_id,
        environment=os.getenv("ENVIRONMENT", "development").strip().lower() or "development",
        webhook_url=_read_optional_text("WEBHOOK_URL"),
        webhook_secret=_read_optional_text("WEBHOOK_SECRET"),
        newcomer_watch_seconds=newcomer_watch_seconds,
        newcomer_allow_links=_read_bool("NEWCOMER_ALLOW_LINKS", False),
        newcomer_allow_media=_read_bool("NEWCOMER_ALLOW_MEDIA", False),
        flood_window_seconds=flood_window_seconds,
        flood_max_messages=flood_max_messages,
        mute_minutes_step3=mute_minutes_step3,
        mute_hours_step4=mute_hours_step4,
        auto_init_schema=_read_bool("AUTO_INIT_SCHEMA", False),
        keyword_refresh_seconds=keyword_refresh_seconds,
        group_admin_max_mute_seconds=group_admin_max_mute_seconds,
        admin_sync_interval_seconds=admin_sync_interval_seconds,
    )
