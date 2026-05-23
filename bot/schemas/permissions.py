from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ActorRole(str, Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class PermissionAction(str, Enum):
    VIEW_SETTINGS = "view_settings"
    VIEW_HISTORY = "view_history"
    WARN = "warn"
    MUTE_SHORT = "mute_short"
    MUTE_ANY = "mute_any"
    BAN = "ban"
    UNBAN = "unban"
    SET_LOG = "set_log"
    RELOAD_KEYWORDS = "reload_keywords"
    WHITELIST = "whitelist"
    BLACKLIST = "blacklist"
    EXPORT_DATA = "export_data"
    GLOBAL_CONFIG = "global_config"


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    role: ActorRole
    reason: str
