from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from redis.asyncio import Redis

from bot.schemas.lexicon import LexiconKind, LexiconSnapshot, RiskLevel
from bot.schemas.moderation import RuleHit
from bot.services import lexicon_admin
from bot.services.keyword_store import KeywordStore


TOKEN_PATTERN = re.compile(r"[0-9a-z\u4e00-\u9fff]{3,32}")
AUTO_LEARN_THRESHOLD = 3
AUTO_LEARN_EXPIRE_SECONDS = 7 * 24 * 3600
AUTO_LEARN_MAX_PROMOTIONS = 3
AUTO_LEARN_SOURCE = "auto:lexicon_learning"


@dataclass(frozen=True)
class LearningResult:
    promoted_tokens: tuple[str, ...]
    inspected_tokens: int


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.lower()
    return normalized


def _extract_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize_text(text)
    items = TOKEN_PATTERN.findall(normalized)
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return tuple(deduped)


def _is_digit_only(token: str) -> bool:
    return all(ch.isdigit() for ch in token)


def _is_eligible_hit(hit: RuleHit) -> bool:
    return hit.risk_level in {RiskLevel.HIGH, RiskLevel.SEVERE}


def _known_word_set(snapshot: LexiconSnapshot) -> frozenset[str]:
    words = {
        item.normalized_value
        for item in snapshot.entries
        if item.kind == LexiconKind.WORD and item.enabled
    }
    return frozenset(words)


async def _increment_token_counter(redis_client: Redis, token: str) -> int:
    key = f"lexicon_auto_learn:token:{token}"
    count = await redis_client.incr(key)
    if int(count) == 1:
        await redis_client.expire(key, AUTO_LEARN_EXPIRE_SECONDS)
    return int(count)


async def learn_from_message(
    redis_client: Redis,
    keyword_store: KeywordStore,
    text: str,
    hits: list[RuleHit],
) -> LearningResult:
    if len(hits) == 0:
        return LearningResult(promoted_tokens=tuple(), inspected_tokens=0)
    if not any(_is_eligible_hit(hit) for hit in hits):
        return LearningResult(promoted_tokens=tuple(), inspected_tokens=0)

    snapshot = keyword_store.get_snapshot()
    known_words = _known_word_set(snapshot)
    tokens = _extract_tokens(text)
    promoted: list[str] = []
    promotions = 0
    inspected = 0

    for token in tokens:
        inspected += 1
        if promotions >= AUTO_LEARN_MAX_PROMOTIONS:
            break
        if _is_digit_only(token):
            continue
        if token in known_words:
            continue
        if token in snapshot.whitelist_words:
            continue
        count = await _increment_token_counter(redis_client, token)
        if count < AUTO_LEARN_THRESHOLD:
            continue
        try:
            lexicon_admin.add_entry(
                directory_path=keyword_store.directory_path,
                kind=LexiconKind.WORD.value,
                category="adaptive_spam",
                risk_level=RiskLevel.MEDIUM.value,
                value=token,
                source=AUTO_LEARN_SOURCE,
                observe_only=True,
                action_override="log",
                mute_seconds_override=None,
            )
        except lexicon_admin.LexiconAdminError as exc:
            if str(exc) != "entry already exists":
                raise
            continue
        promoted.append(token)
        promotions += 1

    if len(promoted) > 0:
        keyword_store.force_reload()

    return LearningResult(promoted_tokens=tuple(promoted), inspected_tokens=inspected)
