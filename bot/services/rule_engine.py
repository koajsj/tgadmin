from __future__ import annotations

from dataclasses import dataclass
import re
import time
import unicodedata

from redis.asyncio import Redis

from bot.schemas.lexicon import LexiconEntry, LexiconKind, LexiconSnapshot, ModerationAction, RiskLevel
from bot.schemas.moderation import RuleHit


LINK_PATTERN = re.compile(r"(https?://|www\.|t\.me/|telegram\.me/)", re.IGNORECASE)
DOMAIN_PATTERN = re.compile(
    r"(?:(?:https?://)?(?:www\.)?)(([a-z0-9-]+\.)+[a-z]{2,})(?:/[^\s]*)?",
    re.IGNORECASE,
)
NON_TOKEN_PATTERN = re.compile(r"[^0-9a-z\u4e00-\u9fff]+")
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]")
SPACE_PATTERN = re.compile(r"\s+")
REPEAT_PATTERN = re.compile(r"(.)\1{2,}")
WORD_TOKEN_PATTERN = re.compile(r"[0-9a-z\u4e00-\u9fff]{2,32}")
DEOBFUSCATION_TABLE = str.maketrans(
    {
        "@": "a",
        "$": "s",
        "0": "o",
        "1": "l",
        "3": "e",
        "4": "a",
        "5": "s",
        "7": "t",
    }
)


@dataclass(frozen=True)
class NormalizedText:
    original_text: str
    basic_normalized: str
    simplified_text: str
    compact_text: str
    compact_simplified_text: str
    compact_deobfuscated_text: str
    compact_simplified_deobfuscated_text: str
    basic_tokens: frozenset[str]
    simplified_tokens: frozenset[str]


@dataclass(frozen=True)
class EntryMatch:
    trigger: str
    mode: str


def _resolve_opencc() -> object:
    try:
        from opencc import OpenCC  # type: ignore
    except ImportError:
        return None
    return OpenCC("t2s")


def _compress_repeated_chars(text: str) -> str:
    return REPEAT_PATTERN.sub(r"\1\1", text)


def normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = ZERO_WIDTH_PATTERN.sub("", normalized)
    normalized = SPACE_PATTERN.sub(" ", normalized.strip().lower())
    normalized = _compress_repeated_chars(normalized)
    return normalized


def compact_text(text: str) -> str:
    return NON_TOKEN_PATTERN.sub("", text.lower())


def _deobfuscate_text(text: str) -> str:
    return text.translate(DEOBFUSCATION_TABLE)


def _extract_word_tokens(text: str) -> frozenset[str]:
    return frozenset(WORD_TOKEN_PATTERN.findall(text))


def normalize_for_detection(text: str) -> NormalizedText:
    basic = normalize_text(text)
    converter = _resolve_opencc()
    if converter is None:
        simplified = basic
    else:
        simplified = normalize_text(converter.convert(basic))
    compact_basic = compact_text(basic)
    compact_simplified = compact_text(simplified)
    compact_deobfuscated = compact_text(_deobfuscate_text(basic))
    compact_simplified_deobfuscated = compact_text(_deobfuscate_text(simplified))
    return NormalizedText(
        original_text=text,
        basic_normalized=basic,
        simplified_text=simplified,
        compact_text=compact_basic,
        compact_simplified_text=compact_simplified,
        compact_deobfuscated_text=compact_deobfuscated,
        compact_simplified_deobfuscated_text=compact_simplified_deobfuscated,
        basic_tokens=_extract_word_tokens(basic),
        simplified_tokens=_extract_word_tokens(simplified),
    )


def contains_link(text: str) -> bool:
    return LINK_PATTERN.search(text) is not None


def extract_domains(text: str) -> list[str]:
    matches = DOMAIN_PATTERN.findall(text)
    domains = [item[0].lower().removeprefix("www.") for item in matches]
    return list(dict.fromkeys(domains))


def contains_keyword(text: str, keywords: list[str]) -> str | None:
    compact = compact_text(text)
    for keyword in keywords:
        normalized_keyword = normalize_text(keyword)
        if normalized_keyword == "":
            continue
        if normalized_keyword in text:
            return normalized_keyword
        compact_keyword = compact_text(normalized_keyword)
        if compact_keyword != "" and compact_keyword in compact:
            return normalized_keyword
    return None


def _score_for_risk(risk_level: RiskLevel) -> int:
    if risk_level == RiskLevel.LOW:
        return 20
    if risk_level == RiskLevel.MEDIUM:
        return 45
    if risk_level == RiskLevel.HIGH:
        return 75
    return 95


def _resolve_action(snapshot: LexiconSnapshot, risk_level: RiskLevel, action_override: ModerationAction | None) -> ModerationAction:
    if action_override is not None:
        return action_override
    policy = snapshot.risk_policies.get(risk_level)
    if policy is None:
        return ModerationAction.LOG
    return policy.action


def _entry_matches(entry: LexiconEntry, normalized: NormalizedText, domains: list[str]) -> EntryMatch | None:
    if entry.kind == LexiconKind.WORD:
        compact_value = compact_text(entry.normalized_value)
        if len(compact_value) == 0:
            return None
        if len(compact_value) <= 3:
            if entry.normalized_value in normalized.basic_tokens:
                return EntryMatch(trigger=entry.normalized_value, mode="token_basic")
            if entry.normalized_value in normalized.simplified_tokens:
                return EntryMatch(trigger=entry.normalized_value, mode="token_simplified")
            return None
        if entry.normalized_value in normalized.basic_normalized:
            return EntryMatch(trigger=entry.normalized_value, mode="basic")
        if entry.normalized_value in normalized.simplified_text:
            return EntryMatch(trigger=entry.normalized_value, mode="simplified")
        if compact_value in normalized.compact_text:
            return EntryMatch(trigger=entry.normalized_value, mode="compact")
        if compact_value in normalized.compact_simplified_text:
            return EntryMatch(trigger=entry.normalized_value, mode="compact_simplified")
        deobfuscated_value = compact_text(_deobfuscate_text(entry.normalized_value))
        if deobfuscated_value != "" and deobfuscated_value in normalized.compact_deobfuscated_text:
            return EntryMatch(trigger=entry.normalized_value, mode="compact_deobfuscated")
        if deobfuscated_value != "" and deobfuscated_value in normalized.compact_simplified_deobfuscated_text:
            return EntryMatch(trigger=entry.normalized_value, mode="compact_simplified_deobfuscated")
        return None

    if entry.kind == LexiconKind.DOMAIN:
        candidate = entry.normalized_value
        for domain in domains:
            if domain == candidate:
                return EntryMatch(trigger=domain, mode="domain_exact")
            if domain.endswith("." + candidate):
                return EntryMatch(trigger=domain, mode="domain_subdomain")
        return None

    return None


def _build_hit_from_entry(snapshot: LexiconSnapshot, entry: LexiconEntry, entry_match: EntryMatch) -> RuleHit:
    action = _resolve_action(snapshot, entry.risk_level, entry.action_override)
    if entry.kind == LexiconKind.WORD and len(entry.normalized_value) <= 2:
        action = ModerationAction.LOG
    return RuleHit(
        rule_name=f"{entry.category}_keyword",
        reason=f"{entry.category}:{entry.normalized_value}",
        score=_score_for_risk(entry.risk_level),
        is_link=entry.kind == LexiconKind.DOMAIN,
        is_keyword=entry.kind == LexiconKind.WORD,
        is_flood=False,
        category=entry.category,
        risk_level=entry.risk_level,
        action=action,
        trigger=f"{entry_match.mode}:{entry_match.trigger}"[:80],
        source=entry.source,
    )


def _regex_flags(flags: str) -> int:
    resolved = 0
    if "i" in flags:
        resolved |= re.IGNORECASE
    if "m" in flags:
        resolved |= re.MULTILINE
    if "s" in flags:
        resolved |= re.DOTALL
    return resolved


def _match_regex_rules(snapshot: LexiconSnapshot, normalized: NormalizedText) -> list[RuleHit]:
    hits: list[RuleHit] = []
    for rule in snapshot.regex_rules:
        if not rule.enabled:
            continue
        compiled = re.compile(rule.pattern, _regex_flags(rule.flags))
        matched = compiled.search(normalized.basic_normalized)
        if matched is None:
            matched = compiled.search(normalized.simplified_text)
        if matched is None:
            continue
        trigger = matched.group(0)[:80]
        action = _resolve_action(snapshot, rule.risk_level, rule.action_override)
        hits.append(
            RuleHit(
                rule_name=f"{rule.category}_regex",
                reason=f"{rule.category}:regex",
                score=_score_for_risk(rule.risk_level),
                is_link=False,
                is_keyword=False,
                is_flood=False,
                category=rule.category,
                risk_level=rule.risk_level,
                action=action,
                trigger=trigger,
                source=rule.source,
            )
        )
    return hits


async def check_flood(redis_client: Redis, chat_id: int, user_id: int, window_seconds: int, max_messages: int) -> bool:
    now = time.time()
    key = f"flood:{chat_id}:{user_id}"
    min_score = now - float(window_seconds)
    member = f"{now:.6f}-{user_id}"
    await redis_client.zadd(key, {member: now})
    await redis_client.zremrangebyscore(key, 0, min_score)
    count = await redis_client.zcard(key)
    await redis_client.expire(key, window_seconds * 3)
    return int(count) > max_messages


async def check_repeat_message(
    redis_client: Redis,
    chat_id: int,
    user_id: int,
    text: str,
    window_seconds: int,
    max_repeats: int,
) -> bool:
    normalized = compact_text(normalize_text(text))
    if normalized == "":
        return False
    key = f"repeat:{chat_id}:{user_id}:{normalized[:48]}"
    count = await redis_client.incr(key)
    await redis_client.expire(key, window_seconds)
    return int(count) >= max_repeats


def mention_count(text: str) -> int:
    return text.count("@")


def _evaluate_lexicon_hits(text: str, snapshot: LexiconSnapshot) -> list[RuleHit]:
    normalized = normalize_for_detection(text)
    domains = extract_domains(normalized.basic_normalized)
    if len(domains) == 0:
        domains = extract_domains(normalized.original_text.lower())

    if any(domain in snapshot.whitelist_domains for domain in domains):
        domains = [domain for domain in domains if domain not in snapshot.whitelist_domains]

    hits: list[RuleHit] = []
    for entry in snapshot.entries:
        if not entry.enabled:
            continue
        if entry.kind == LexiconKind.WORD and entry.normalized_value in snapshot.whitelist_words:
            continue
        if entry.kind == LexiconKind.DOMAIN and entry.normalized_value in snapshot.whitelist_domains:
            continue
        matched = _entry_matches(entry, normalized, domains)
        if matched is not None:
            hits.append(_build_hit_from_entry(snapshot, entry, matched))

    hits.extend(_match_regex_rules(snapshot, normalized))
    return hits


async def evaluate_message(
    redis_client: Redis,
    chat_id: int,
    user_id: int,
    text: str,
    keywords: list[str],
    keyword_score: int,
    link_score: int,
    flood_score: int,
    flood_window_seconds: int,
    flood_max_messages: int,
    snapshot: LexiconSnapshot | None = None,
) -> list[RuleHit]:
    hits: list[RuleHit] = []
    normalized = normalize_text(text)

    if snapshot is not None:
        hits.extend(_evaluate_lexicon_hits(text, snapshot))
    else:
        keyword = contains_keyword(normalized, keywords)
        if keyword is not None:
            hits.append(
                RuleHit(
                    rule_name="keyword_blacklist",
                    reason=f"keyword:{keyword}",
                    score=keyword_score,
                    is_link=False,
                    is_keyword=True,
                    is_flood=False,
                    category="keyword_blacklist",
                    risk_level=RiskLevel.MEDIUM,
                    action=ModerationAction.DELETE,
                    trigger=keyword,
                    source="legacy_keywords",
                )
            )

    if contains_link(normalized):
        hits.append(
            RuleHit(
                rule_name="link_filter",
                reason="contains_link",
                score=link_score,
                is_link=True,
                is_keyword=False,
                is_flood=False,
                category="link_filter",
                risk_level=RiskLevel.MEDIUM,
                action=ModerationAction.DELETE,
                trigger="link",
                source="legacy_link_filter",
            )
        )

    is_flood = await check_flood(redis_client, chat_id, user_id, flood_window_seconds, flood_max_messages)
    if is_flood:
        hits.append(
            RuleHit(
                rule_name="flood_detected",
                reason=f"flood:{flood_max_messages}/{flood_window_seconds}s",
                score=flood_score,
                is_link=False,
                is_keyword=False,
                is_flood=True,
                category="flood",
                risk_level=RiskLevel.HIGH,
                action=ModerationAction.MUTE,
                trigger="flood",
                source="legacy_flood_filter",
            )
        )

    return hits
