from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
import json
from pathlib import Path
import re
import unicodedata

from bot.schemas.lexicon import LexiconEntry, LexiconKind, LexiconSnapshot, ModerationAction, RegexRule, RiskLevel, RiskPolicy


class LexiconLoadError(RuntimeError):
    """Raised when lexicon files cannot be parsed safely."""


CATEGORY_MAP: dict[str, tuple[str, RiskLevel, LexiconKind]] = {
    "ad_low": ("ad_low", RiskLevel.LOW, LexiconKind.WORD),
    "ad_high": ("ad_high", RiskLevel.HIGH, LexiconKind.WORD),
    "scam": ("scam", RiskLevel.HIGH, LexiconKind.WORD),
    "crypto_scam": ("crypto_scam", RiskLevel.HIGH, LexiconKind.WORD),
    "contact_ads": ("contact_ads", RiskLevel.MEDIUM, LexiconKind.WORD),
    "adult_low": ("adult_low", RiskLevel.LOW, LexiconKind.WORD),
    "adult_high": ("adult_high", RiskLevel.HIGH, LexiconKind.WORD),
    "adult_contact": ("adult_contact", RiskLevel.HIGH, LexiconKind.WORD),
    "adult_resource": ("adult_resource", RiskLevel.HIGH, LexiconKind.WORD),
    "domain_blacklist": ("domain_blacklist", RiskLevel.HIGH, LexiconKind.DOMAIN),
    "domain_whitelist": ("domain_whitelist", RiskLevel.LOW, LexiconKind.DOMAIN),
    "word_whitelist": ("word_whitelist", RiskLevel.LOW, LexiconKind.WORD),
    "域名黑名单": ("domain_blacklist", RiskLevel.HIGH, LexiconKind.DOMAIN),
    "域名白名单": ("domain_whitelist", RiskLevel.LOW, LexiconKind.DOMAIN),
    "脏话白名单": ("word_whitelist", RiskLevel.LOW, LexiconKind.WORD),
    "广告类型": ("ad_low", RiskLevel.LOW, LexiconKind.WORD),
    "博彩赌博": ("scam", RiskLevel.HIGH, LexiconKind.WORD),
    "诈骗广告": ("scam", RiskLevel.HIGH, LexiconKind.WORD),
    "成人内容": ("adult_low", RiskLevel.LOW, LexiconKind.WORD),
    "色情类型": ("adult_high", RiskLevel.HIGH, LexiconKind.WORD),
    "色情词库": ("adult_high", RiskLevel.HIGH, LexiconKind.WORD),
}


DEFAULT_RISK_POLICIES: dict[RiskLevel, RiskPolicy] = {
    RiskLevel.LOW: RiskPolicy(action=ModerationAction.LOG, mute_seconds=None, notify_admin=False, require_confirm=False),
    RiskLevel.MEDIUM: RiskPolicy(action=ModerationAction.WARN, mute_seconds=None, notify_admin=True, require_confirm=False),
    RiskLevel.HIGH: RiskPolicy(action=ModerationAction.MUTE, mute_seconds=600, notify_admin=True, require_confirm=False),
    RiskLevel.SEVERE: RiskPolicy(action=ModerationAction.BAN, mute_seconds=None, notify_admin=True, require_confirm=True),
}


SPACE_PATTERN = re.compile(r"\s+")
ZERO_WIDTH_PATTERN = re.compile(r"[\u200b\u200c\u200d\ufeff]")


@dataclass(frozen=True)
class RawFileEntry:
    value: str
    category: str
    risk_level: RiskLevel
    kind: LexiconKind
    source: str


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    normalized = ZERO_WIDTH_PATTERN.sub("", normalized)
    normalized = SPACE_PATTERN.sub(" ", normalized.strip().lower())
    return normalized


def normalize_domain(domain: str) -> str:
    normalized = normalize_text(domain)
    normalized = normalized.removeprefix("http://").removeprefix("https://")
    normalized = normalized.split("/", 1)[0]
    normalized = normalized.removeprefix("www.")
    return normalized


def _build_entry_id(kind: LexiconKind, category: str, value: str, source: str) -> str:
    raw = f"{kind.value}|{category}|{value}|{source}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _parse_risk_level(raw: str) -> RiskLevel:
    lowered = normalize_text(raw)
    if lowered in {RiskLevel.LOW.value, "低风险"}:
        return RiskLevel.LOW
    if lowered in {RiskLevel.MEDIUM.value, "中风险"}:
        return RiskLevel.MEDIUM
    if lowered in {RiskLevel.HIGH.value, "高风险"}:
        return RiskLevel.HIGH
    if lowered in {RiskLevel.SEVERE.value, "严重违规"}:
        return RiskLevel.SEVERE
    raise LexiconLoadError(f"unsupported risk_level: {raw}")


def _parse_kind(raw: str) -> LexiconKind:
    lowered = normalize_text(raw)
    if lowered == LexiconKind.WORD.value:
        return LexiconKind.WORD
    if lowered == LexiconKind.DOMAIN.value:
        return LexiconKind.DOMAIN
    if lowered == LexiconKind.REGEX.value:
        return LexiconKind.REGEX
    raise LexiconLoadError(f"unsupported kind: {raw}")


def _parse_action(raw: str) -> ModerationAction:
    lowered = normalize_text(raw)
    for action in ModerationAction:
        if lowered == action.value:
            return action
    raise LexiconLoadError(f"unsupported action: {raw}")


def _parse_txt_file(file_path: Path, source_name: str) -> list[RawFileEntry]:
    category_name = file_path.stem
    mapped = CATEGORY_MAP.get(category_name)
    if mapped is None:
        return []

    category, risk_level, kind = mapped
    content = file_path.read_text(encoding="utf-8")
    result: list[RawFileEntry] = []
    for raw_line in content.splitlines():
        line = normalize_text(raw_line)
        if line == "" or line.startswith("#"):
            continue
        value = line if kind != LexiconKind.DOMAIN else normalize_domain(line)
        if value == "":
            continue
        result.append(
            RawFileEntry(
                value=value,
                category=category,
                risk_level=risk_level,
                kind=kind,
                source=source_name,
            )
        )
    return result


def _parse_json_entries(data: object, source_name: str) -> tuple[list[LexiconEntry], list[RegexRule], set[str], set[str]]:
    if not isinstance(data, list):
        raise LexiconLoadError("json lexicon file must be a list")

    entries: list[LexiconEntry] = []
    regex_rules: list[RegexRule] = []
    whitelist_words: set[str] = set()
    whitelist_domains: set[str] = set()

    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise LexiconLoadError(f"json entry must be object: index={index}")
        raw_kind = row.get("kind")
        if not isinstance(raw_kind, str):
            raise LexiconLoadError(f"missing kind at index={index}")
        kind = _parse_kind(raw_kind)

        raw_category = row.get("category")
        if not isinstance(raw_category, str) or normalize_text(raw_category) == "":
            raise LexiconLoadError(f"missing category at index={index}")
        category = normalize_text(raw_category)

        raw_risk = row.get("risk_level")
        if not isinstance(raw_risk, str):
            raise LexiconLoadError(f"missing risk_level at index={index}")
        risk_level = _parse_risk_level(raw_risk)

        value_field = row.get("value")
        if not isinstance(value_field, str) or normalize_text(value_field) == "":
            raise LexiconLoadError(f"missing value at index={index}")

        enabled = bool(row.get("enabled", True))
        observe_only = bool(row.get("observe_only", False))
        source = row.get("source", source_name)
        source_text = source if isinstance(source, str) and source.strip() != "" else source_name
        raw_action_override = row.get("action_override")
        action_override = None
        if isinstance(raw_action_override, str):
            action_override = _parse_action(raw_action_override)

        mute_seconds_override_raw = row.get("mute_seconds_override")
        mute_seconds_override = None
        if isinstance(mute_seconds_override_raw, int):
            mute_seconds_override = mute_seconds_override_raw

        note_raw = row.get("note")
        note = note_raw if isinstance(note_raw, str) else None
        entry_id = str(row.get("entry_id", _build_entry_id(kind, category, value_field, source_text)))

        if category == "word_whitelist":
            whitelist_words.add(normalize_text(value_field))
            continue
        if category == "domain_whitelist":
            whitelist_domains.add(normalize_domain(value_field))
            continue

        if kind == LexiconKind.REGEX:
            flags = row.get("flags", "")
            flags_text = flags if isinstance(flags, str) else ""
            regex_rules.append(
                RegexRule(
                    entry_id=entry_id,
                    pattern=value_field,
                    flags=flags_text,
                    category=category,
                    risk_level=risk_level,
                    enabled=enabled,
                    source=source_text,
                    observe_only=observe_only,
                    action_override=action_override,
                    mute_seconds_override=mute_seconds_override,
                    note=note,
                )
            )
            continue

        normalized_value = normalize_text(value_field)
        if kind == LexiconKind.DOMAIN:
            normalized_value = normalize_domain(value_field)

        entries.append(
            LexiconEntry(
                entry_id=entry_id,
                value=value_field,
                normalized_value=normalized_value,
                kind=kind,
                category=category,
                risk_level=risk_level,
                enabled=enabled,
                source=source_text,
                observe_only=observe_only,
                action_override=action_override,
                mute_seconds_override=mute_seconds_override,
                note=note,
            )
        )

    return entries, regex_rules, whitelist_words, whitelist_domains


def _read_policy_file(file_path: Path) -> dict[RiskLevel, RiskPolicy]:
    if not file_path.exists():
        return dict(DEFAULT_RISK_POLICIES)

    raw_payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(raw_payload, dict):
        raise LexiconLoadError("risk_policy json must be object")

    result = dict(DEFAULT_RISK_POLICIES)
    for level_key, config in raw_payload.items():
        if not isinstance(level_key, str):
            raise LexiconLoadError("risk_policy key must be string")
        if not isinstance(config, dict):
            raise LexiconLoadError(f"risk_policy value must be object: {level_key}")
        risk_level = _parse_risk_level(level_key)
        action_raw = config.get("action")
        if not isinstance(action_raw, str):
            raise LexiconLoadError(f"risk_policy.action required: {level_key}")
        action = _parse_action(action_raw)
        mute_seconds = config.get("mute_seconds")
        mute_seconds_value = mute_seconds if isinstance(mute_seconds, int) else None
        notify_admin = bool(config.get("notify_admin", False))
        require_confirm = bool(config.get("require_confirm", False))
        result[risk_level] = RiskPolicy(
            action=action,
            mute_seconds=mute_seconds_value,
            notify_admin=notify_admin,
            require_confirm=require_confirm,
        )
    return result


def load_lexicon_snapshot(directory_path: Path) -> LexiconSnapshot:
    if not directory_path.exists():
        raise LexiconLoadError(f"lexicon directory not found: {directory_path}")

    loaded_entries: list[LexiconEntry] = []
    loaded_regex_rules: list[RegexRule] = []
    whitelist_words: set[str] = set()
    whitelist_domains: set[str] = set()

    txt_files = sorted(directory_path.glob("*.txt"))
    for file_path in txt_files:
        raw_entries = _parse_txt_file(file_path, source_name=f"local:{file_path.name}")
        for item in raw_entries:
            entry_id = _build_entry_id(item.kind, item.category, item.value, item.source)
            loaded_entries.append(
                LexiconEntry(
                    entry_id=entry_id,
                    value=item.value,
                    normalized_value=item.value,
                    kind=item.kind,
                    category=item.category,
                    risk_level=item.risk_level,
                    enabled=True,
                    source=item.source,
                    observe_only=False,
                    action_override=None,
                    mute_seconds_override=None,
                    note=None,
                )
            )

    for json_file in sorted(directory_path.glob("*.json")):
        if json_file.name == "risk_policy.json":
            continue
        parsed = json.loads(json_file.read_text(encoding="utf-8"))
        entries, regex_rules, white_words, white_domains = _parse_json_entries(parsed, source_name=f"local:{json_file.name}")
        loaded_entries.extend(entries)
        loaded_regex_rules.extend(regex_rules)
        whitelist_words.update(white_words)
        whitelist_domains.update(white_domains)

    for item in loaded_entries:
        if item.category == "word_whitelist":
            whitelist_words.add(item.normalized_value)
        if item.category == "domain_whitelist":
            whitelist_domains.add(normalize_domain(item.normalized_value))

    risk_policies = _read_policy_file(directory_path / "risk_policy.json")

    unique_entries = {(entry.kind.value, entry.category, entry.normalized_value): entry for entry in loaded_entries}
    unique_regex_rules = {(item.category, item.pattern, item.flags): item for item in loaded_regex_rules}

    return LexiconSnapshot(
        entries=tuple(unique_entries.values()),
        regex_rules=tuple(unique_regex_rules.values()),
        whitelist_words=frozenset(word for word in whitelist_words if word != ""),
        whitelist_domains=frozenset(domain for domain in whitelist_domains if domain != ""),
        risk_policies=risk_policies,
    )


def load_keywords_from_directory(directory_path: Path) -> list[str]:
    snapshot = load_lexicon_snapshot(directory_path)
    result = [
        entry.normalized_value
        for entry in snapshot.entries
        if entry.kind == LexiconKind.WORD
        and entry.enabled
        and entry.category not in {"word_whitelist"}
        and len(entry.normalized_value) > 0
    ]
    unique = list(dict.fromkeys(result))
    return unique
