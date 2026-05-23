from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class LexiconKind(str, Enum):
    WORD = "word"
    DOMAIN = "domain"
    REGEX = "regex"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    SEVERE = "severe"


class ModerationAction(str, Enum):
    LOG = "log"
    NOTIFY = "notify"
    DELETE = "delete"
    WARN = "warn"
    MUTE = "mute"
    BAN = "ban"
    KICK = "kick"
    NONE = "none"


@dataclass(frozen=True)
class RiskPolicy:
    action: ModerationAction
    mute_seconds: int | None
    notify_admin: bool
    require_confirm: bool


@dataclass(frozen=True)
class LexiconEntry:
    entry_id: str
    value: str
    normalized_value: str
    kind: LexiconKind
    category: str
    risk_level: RiskLevel
    enabled: bool
    source: str
    observe_only: bool
    action_override: ModerationAction | None
    mute_seconds_override: int | None
    note: str | None


@dataclass(frozen=True)
class RegexRule:
    entry_id: str
    pattern: str
    flags: str
    category: str
    risk_level: RiskLevel
    enabled: bool
    source: str
    observe_only: bool
    action_override: ModerationAction | None
    mute_seconds_override: int | None
    note: str | None


@dataclass(frozen=True)
class LexiconSnapshot:
    entries: tuple[LexiconEntry, ...]
    regex_rules: tuple[RegexRule, ...]
    whitelist_words: frozenset[str]
    whitelist_domains: frozenset[str]
    risk_policies: dict[RiskLevel, RiskPolicy]

