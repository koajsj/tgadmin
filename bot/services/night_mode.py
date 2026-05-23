from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def is_night_window(runtime_settings: dict[str, object], now_utc: datetime) -> bool:
    night = runtime_settings.get("night_mode")
    if not isinstance(night, dict):
        return False
    if not bool(night.get("enabled", False)):
        return False

    timezone_name = str(night.get("timezone", "UTC"))
    start_hour = int(night.get("start_hour", 0))
    end_hour = int(night.get("end_hour", 0))
    local_now = now_utc.astimezone(ZoneInfo(timezone_name))
    hour = int(local_now.hour)

    if start_hour == end_hour:
        return True
    if start_hour < end_hour:
        return start_hour <= hour < end_hour
    return hour >= start_hour or hour < end_hour
