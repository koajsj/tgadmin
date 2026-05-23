from __future__ import annotations

from dataclasses import dataclass

from bot.schemas.lexicon import ModerationAction, RiskLevel


@dataclass(frozen=True)
class RuleHit:
    rule_name: str
    reason: str
    score: int
    is_link: bool
    is_keyword: bool
    is_flood: bool
    category: str
    risk_level: RiskLevel
    action: ModerationAction
    trigger: str
    source: str


@dataclass(frozen=True)
class EnforcementDecision:
    action: str
    duration_seconds: int | None
    reason: str
    level: int
