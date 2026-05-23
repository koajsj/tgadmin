from __future__ import annotations

from dataclasses import dataclass
import json
import os
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
STATE_FILE = DATA_DIR / "state.json"
load_dotenv(BASE_DIR / ".env")


def _getenv_int(key: str, default: int) -> int:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return int(value)


def _getenv_bool(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _getenv_list(key: str) -> list[str]:
    value = os.getenv(key, "")
    if not value.strip():
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _getenv_csv(key: str) -> list[str]:
    value = os.getenv(key, "")
    if not value.strip():
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def _normalize_keyword(value: str) -> str:
    return value.strip().lower()


def _load_keywords(file_path: Path) -> list[str]:
    if not file_path.exists():
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        content = file_path.read_text(encoding="gbk", errors="ignore")
    keywords: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        keywords.append(_normalize_keyword(line))
    return keywords


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def _resolve_keyword_files(base_dir: Path) -> list[Path]:
    files: list[Path] = []

    keywords_file = Path(os.getenv("KEYWORDS_FILE", str(DATA_DIR / "keywords.txt")))
    if not keywords_file.is_absolute():
        keywords_file = base_dir / keywords_file
    files.append(keywords_file)

    for item in _getenv_csv("KEYWORDS_FILES"):
        path = Path(item)
        if not path.is_absolute():
            path = base_dir / path
        files.append(path)

    if _getenv_bool("AUTO_LOAD_TXT", True):
        for folder in [DATA_DIR, base_dir]:
            if not folder.exists():
                continue
            for path in folder.glob("*.txt"):
                if path.name.lower() == "requirements.txt":
                    continue
                files.append(path)

    unique: dict[str, Path] = {}
    for path in files:
        if path.exists():
            unique[str(path.resolve()).lower()] = path
    return list(unique.values())


def _load_keywords_files(files: list[Path]) -> list[str]:
    keywords: list[str] = []
    for file_path in files:
        keywords.extend(_load_keywords(file_path))
    return keywords


def _load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("state.json must be an object")
    return data


def _save_state(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class Settings:
    bot_token: str
    log_chat_id: int | None
    action: str
    mute_duration_seconds: int
    ban_after_strikes: int
    strike_window_seconds: int
    admin_cache_ttl_seconds: int
    owner_user_ids: list[int]
    keywords: list[str]
    learned_keywords: list[str]
    ignored_keywords: list[str]
    learning_enabled: bool
    learning_min_hits: int
    learning_min_unique_users: int
    learning_promote_hits: int
    learning_promote_unique_users: int
    learning_ignore_hits: int
    learning_ignore_unique_users: int
    learning_retire_seconds: int
    learning_window_seconds: int
    rule_enable_link: bool
    rule_enable_keywords: bool
    rule_enable_username: bool
    rule_enable_flood: bool
    rule_enable_repeat: bool
    rule_enable_length: bool
    max_message_length: int
    flood_max_messages: int
    flood_window_seconds: int
    repeat_max_dupes: int
    repeat_window_seconds: int
    delete_score_threshold: int
    mute_score_threshold: int
    ban_score_threshold: int
    link_score: int
    keyword_score: int
    learned_keyword_score: int
    username_score: int
    length_score: int
    flood_score: int
    repeat_score: int
    structure_score: int
    combo_link_keyword_bonus: int
    combo_username_keyword_bonus: int
    combo_flood_repeat_bonus: int
    combo_structure_link_bonus: int


STATE_KEYS = {
    "action",
    "mute_duration_seconds",
    "ban_after_strikes",
    "rule_enable_link",
    "rule_enable_keywords",
    "rule_enable_username",
    "rule_enable_flood",
    "rule_enable_repeat",
    "rule_enable_length",
    "max_message_length",
    "flood_max_messages",
    "flood_window_seconds",
    "repeat_max_dupes",
    "repeat_window_seconds",
    "learning_enabled",
    "learning_min_hits",
    "learning_min_unique_users",
    "learning_promote_hits",
    "learning_promote_unique_users",
    "learning_ignore_hits",
    "learning_ignore_unique_users",
    "learning_retire_seconds",
    "learning_window_seconds",
    "delete_score_threshold",
    "mute_score_threshold",
    "ban_score_threshold",
    "link_score",
    "keyword_score",
    "learned_keyword_score",
    "username_score",
    "length_score",
    "flood_score",
    "repeat_score",
    "structure_score",
    "combo_link_keyword_bonus",
    "combo_username_keyword_bonus",
    "combo_flood_repeat_bonus",
    "combo_structure_link_bonus",
}


def _apply_state(settings: Settings, state: dict[str, Any]) -> None:
    for key in STATE_KEYS:
        if key in state:
            setattr(settings, key, state[key])


def _load_learned_meta(raw: Any) -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, list):
        return meta
    for item in raw:
        if isinstance(item, str):
            keyword = _normalize_keyword(item)
            if keyword:
                meta[keyword] = {
                    "keyword": keyword,
                    "hits": 1,
                    "unique_users": 1,
                    "last_seen": 0.0,
                    "benign_hits": 0,
                    "spam_hits": 0,
                }
            continue
        if not isinstance(item, dict):
            continue
        keyword = _normalize_keyword(str(item.get("keyword", "")))
        if not keyword:
            continue
        hits = item.get("hits", 1)
        unique_users = item.get("unique_users", 1)
        last_seen = item.get("last_seen", 0.0)
        benign_hits = item.get("benign_hits", 0)
        spam_hits = item.get("spam_hits", 0)
        try:
            meta[keyword] = {
                "keyword": keyword,
                "hits": int(hits),
                "unique_users": int(unique_users),
                "last_seen": float(last_seen),
                "benign_hits": int(benign_hits),
                "spam_hits": int(spam_hits),
            }
        except (TypeError, ValueError):
            continue
    return meta


def _serialize_learned_meta(meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = sorted(meta.values(), key=lambda item: float(item.get("last_seen", 0.0)), reverse=True)
    result: list[dict[str, Any]] = []
    for item in items:
        result.append(
            {
                "keyword": str(item.get("keyword", "")).strip().lower(),
                "hits": int(item.get("hits", 0)),
                "unique_users": int(item.get("unique_users", 0)),
                "last_seen": float(item.get("last_seen", 0.0)),
                "benign_hits": int(item.get("benign_hits", 0)),
                "spam_hits": int(item.get("spam_hits", 0)),
            }
        )
    return result


def _load_group_stats(raw: Any) -> dict[str, dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return stats

    for chat_id, item in raw.items():
        if not isinstance(item, dict):
            continue
        key = str(chat_id)
        try:
            stats[key] = {
                "chat_id": key,
                "messages_seen": int(item.get("messages_seen", 0)),
                "spam_messages": int(item.get("spam_messages", 0)),
                "deleted_messages": int(item.get("deleted_messages", 0)),
                "muted_messages": int(item.get("muted_messages", 0)),
                "banned_messages": int(item.get("banned_messages", 0)),
                "last_seen": float(item.get("last_seen", 0.0)),
                "last_user_id": int(item.get("last_user_id", 0)),
                "last_score": int(item.get("last_score", 0)),
            }
        except (TypeError, ValueError):
            continue
    return stats


def _serialize_group_stats(stats: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for chat_id, item in stats.items():
        result[str(chat_id)] = {
            "chat_id": str(item.get("chat_id", chat_id)),
            "messages_seen": int(item.get("messages_seen", 0)),
            "spam_messages": int(item.get("spam_messages", 0)),
            "deleted_messages": int(item.get("deleted_messages", 0)),
            "muted_messages": int(item.get("muted_messages", 0)),
            "banned_messages": int(item.get("banned_messages", 0)),
            "last_seen": float(item.get("last_seen", 0.0)),
            "last_user_id": int(item.get("last_user_id", 0)),
            "last_score": int(item.get("last_score", 0)),
        }
    return result


class SettingsStore:
    def __init__(
        self,
        settings: Settings,
        state_path: Path,
        keyword_files: list[Path],
        inline_keywords: list[str],
        custom_keywords: list[str],
        learned_meta: dict[str, dict[str, Any]],
        ignored_keywords: list[str],
        owner_user_ids: list[int],
        group_stats: dict[str, dict[str, Any]],
    ) -> None:
        self._settings = settings
        self._state_path = state_path
        self._keyword_files = keyword_files
        self._inline_keywords = inline_keywords
        self._custom_keywords = custom_keywords
        self._learned_meta = learned_meta
        self._ignored_keywords = _dedupe(ignored_keywords)
        self._owner_user_ids = owner_user_ids
        self._group_stats = group_stats
        self._settings.owner_user_ids = owner_user_ids
        self._last_file_keyword_count = 0
        self._sync_learned_keywords()
        self.reload_keywords()

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def keyword_files(self) -> list[Path]:
        return self._keyword_files

    @property
    def custom_keywords(self) -> list[str]:
        return self._custom_keywords

    @property
    def learned_keywords(self) -> list[str]:
        return list(self._learned_meta.keys())

    @property
    def ignored_keywords(self) -> list[str]:
        return self._ignored_keywords

    @property
    def owner_user_ids(self) -> list[int]:
        return self._owner_user_ids

    @property
    def group_stats(self) -> dict[str, dict[str, Any]]:
        return self._group_stats

    @property
    def last_file_keyword_count(self) -> int:
        return self._last_file_keyword_count

    @property
    def learned_keyword_count(self) -> int:
        return len(self._learned_meta)

    def learning_stats_snapshot(self) -> dict[str, Any]:
        spam_feedback = sum(int(item.get("spam_hits", 0)) for item in self._learned_meta.values())
        benign_feedback = sum(int(item.get("benign_hits", 0)) for item in self._learned_meta.values())
        return {
            "learned_keywords": len(self._learned_meta),
            "ignored_keywords": len(self._ignored_keywords),
            "spam_feedback": spam_feedback,
            "benign_feedback": benign_feedback,
            "top_keywords": self.learned_keyword_snapshot(5),
        }

    def group_stats_snapshot(self, chat_id: int | None, limit: int) -> dict[str, Any]:
        if chat_id is not None:
            record = self._group_stats.get(str(chat_id), {})
            return {
                "scope": "single",
                "groups": [self._group_stats_item(record, str(chat_id))],
                "totals": self._group_stats_totals(),
            }

        items = sorted(
            self._group_stats.values(),
            key=lambda item: (
                int(item.get("messages_seen", 0)),
                float(item.get("last_seen", 0.0)),
            ),
            reverse=True,
        )
        return {
            "scope": "all",
            "groups": [self._group_stats_item(item, str(item.get("chat_id", ""))) for item in items[:limit]],
            "totals": self._group_stats_totals(),
        }

    def _group_stats_item(self, record: dict[str, Any], chat_id: str) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "messages_seen": int(record.get("messages_seen", 0)),
            "spam_messages": int(record.get("spam_messages", 0)),
            "deleted_messages": int(record.get("deleted_messages", 0)),
            "muted_messages": int(record.get("muted_messages", 0)),
            "banned_messages": int(record.get("banned_messages", 0)),
            "last_seen": float(record.get("last_seen", 0.0)),
            "last_user_id": int(record.get("last_user_id", 0)),
            "last_score": int(record.get("last_score", 0)),
        }

    def _group_stats_totals(self) -> dict[str, int]:
        totals = {
            "groups": len(self._group_stats),
            "messages_seen": 0,
            "spam_messages": 0,
            "deleted_messages": 0,
            "muted_messages": 0,
            "banned_messages": 0,
        }
        for record in self._group_stats.values():
            totals["messages_seen"] += int(record.get("messages_seen", 0))
            totals["spam_messages"] += int(record.get("spam_messages", 0))
            totals["deleted_messages"] += int(record.get("deleted_messages", 0))
            totals["muted_messages"] += int(record.get("muted_messages", 0))
            totals["banned_messages"] += int(record.get("banned_messages", 0))
        return totals

    def learned_keyword_snapshot(self, limit: int) -> list[dict[str, Any]]:
        items = sorted(
            self._learned_meta.values(),
            key=lambda item: (
                int(item.get("hits", 0)),
                float(item.get("last_seen", 0.0)),
            ),
            reverse=True,
        )
        snapshot: list[dict[str, Any]] = []
        for item in items[:limit]:
            snapshot.append(
                {
                    "keyword": str(item.get("keyword", "")),
                    "hits": int(item.get("hits", 0)),
                    "unique_users": int(item.get("unique_users", 0)),
                    "benign_hits": int(item.get("benign_hits", 0)),
                    "spam_hits": int(item.get("spam_hits", 0)),
                }
            )
        return snapshot

    def is_owner(self, user_id: int) -> bool:
        return user_id in self._owner_user_ids

    def ensure_owner(self, user_id: int) -> bool:
        if self._owner_user_ids:
            return False
        self._owner_user_ids = [user_id]
        self._settings.owner_user_ids = self._owner_user_ids
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self.save_state()
        return True

    def _sync_learned_keywords(self) -> None:
        now = time.time()
        retire_seconds = self._settings.learning_retire_seconds
        if retire_seconds > 0:
            active_meta: dict[str, dict[str, Any]] = {}
            for keyword, record in self._learned_meta.items():
                last_seen = float(record.get("last_seen", 0.0))
                if last_seen <= 0.0 or now - last_seen <= retire_seconds:
                    active_meta[keyword] = record
            self._learned_meta = active_meta
        self._settings.learned_keywords = list(self._learned_meta.keys())
        self._settings.ignored_keywords = list(self._ignored_keywords)

    def reload_keywords(self) -> int:
        file_keywords = _load_keywords_files(self._keyword_files)
        self._last_file_keyword_count = len(file_keywords)
        combined = self._inline_keywords + self._custom_keywords + file_keywords
        self._settings.keywords = _dedupe(combined)
        self._sync_learned_keywords()
        return self._last_file_keyword_count

    def add_keyword(self, keyword: str) -> bool:
        normalized = _normalize_keyword(keyword)
        if not normalized:
            return False
        if normalized in self._custom_keywords:
            return False
        self._custom_keywords.append(normalized)
        self._learned_meta.pop(normalized, None)
        self.reload_keywords()
        self.save_state()
        return True

    def add_learned_keyword(self, keyword: str, hits: int, unique_users: int, now: float) -> str:
        normalized = _normalize_keyword(keyword)
        if (
            not normalized
            or normalized in self._ignored_keywords
            or normalized in self._settings.keywords
        ):
            return "ignored"

        record = self._learned_meta.get(
            normalized,
            {
                "keyword": normalized,
                "hits": 0,
                "unique_users": 0,
                "last_seen": now,
                "benign_hits": 0,
                "spam_hits": 0,
            },
        )
        record["hits"] = max(int(record.get("hits", 0)), hits)
        record["unique_users"] = max(int(record.get("unique_users", 0)), unique_users)
        record["last_seen"] = now
        record["spam_hits"] = int(record.get("spam_hits", 0)) + 1

        if (
            record["hits"] >= self._settings.learning_promote_hits
            and record["unique_users"] >= self._settings.learning_promote_unique_users
        ):
            promoted = self.add_keyword(normalized)
            if promoted:
                return "promoted"
            return "ignored"

        self._learned_meta[normalized] = record
        self._sync_learned_keywords()
        self.save_state()
        return "learned"

    def record_learned_feedback(self, keyword: str, is_spam: bool, now: float) -> str:
        normalized = _normalize_keyword(keyword)
        if normalized not in self._learned_meta:
            return "missing"

        record = self._learned_meta[normalized]
        record["last_seen"] = now
        if is_spam:
            record["spam_hits"] = int(record.get("spam_hits", 0)) + 1
        else:
            record["benign_hits"] = int(record.get("benign_hits", 0)) + 1

        if (
            int(record.get("benign_hits", 0)) >= self._settings.learning_ignore_hits
            and int(record.get("unique_users", 0)) >= self._settings.learning_ignore_unique_users
        ):
            self._ignored_keywords.append(normalized)
            self._learned_meta.pop(normalized, None)
            self._sync_learned_keywords()
            self.save_state()
            return "ignored"

        self._sync_learned_keywords()
        self.save_state()
        return "updated"

    def record_group_seen(self, chat_id: int, user_id: int, now: float) -> None:
        key = str(chat_id)
        record = self._group_stats.setdefault(
            key,
            {
                "chat_id": key,
                "messages_seen": 0,
                "spam_messages": 0,
                "deleted_messages": 0,
                "muted_messages": 0,
                "banned_messages": 0,
                "last_seen": now,
                "last_user_id": user_id,
                "last_score": 0,
            },
        )
        record["messages_seen"] = int(record.get("messages_seen", 0)) + 1
        record["last_seen"] = now
        record["last_user_id"] = user_id
        self.save_state()

    def record_group_spam(self, chat_id: int, user_id: int, action: str, score: int, now: float) -> None:
        key = str(chat_id)
        record = self._group_stats.setdefault(
            key,
            {
                "chat_id": key,
                "messages_seen": 0,
                "spam_messages": 0,
                "deleted_messages": 0,
                "muted_messages": 0,
                "banned_messages": 0,
                "last_seen": now,
                "last_user_id": user_id,
                "last_score": score,
            },
        )
        record["spam_messages"] = int(record.get("spam_messages", 0)) + 1
        if action == "deleted":
            record["deleted_messages"] = int(record.get("deleted_messages", 0)) + 1
        elif action == "muted":
            record["muted_messages"] = int(record.get("muted_messages", 0)) + 1
        elif action == "banned":
            record["banned_messages"] = int(record.get("banned_messages", 0)) + 1
        record["last_seen"] = now
        record["last_user_id"] = user_id
        record["last_score"] = score
        self.save_state()

    def touch_learned_keyword(self, keyword: str, now: float) -> None:
        normalized = _normalize_keyword(keyword)
        if normalized not in self._learned_meta:
            return
        self._learned_meta[normalized]["last_seen"] = now
        self._sync_learned_keywords()
        self.save_state()

    def export_keywords_payload(self) -> dict[str, Any]:
        return {
            "custom_keywords": list(self._custom_keywords),
            "learned_keywords": _serialize_learned_meta(self._learned_meta),
            "ignored_keywords": list(self._ignored_keywords),
            "owner_user_ids": list(self._owner_user_ids),
        }

    def import_keywords_payload(self, payload: dict[str, Any]) -> dict[str, int]:
        imported = {"custom": 0, "learned": 0, "ignored": 0}
        raw_custom = payload.get("custom_keywords", [])
        if isinstance(raw_custom, list):
            for item in raw_custom:
                if self.add_keyword(str(item)):
                    imported["custom"] += 1

        raw_learned = payload.get("learned_keywords", [])
        learned_meta = _load_learned_meta(raw_learned)
        for keyword, record in learned_meta.items():
            if keyword in self._ignored_keywords or keyword in self._settings.keywords:
                continue
            current = self._learned_meta.get(keyword)
            if current is None:
                self._learned_meta[keyword] = record
            else:
                current["hits"] = max(int(current.get("hits", 0)), int(record["hits"]))
                current["unique_users"] = max(
                    int(current.get("unique_users", 0)),
                    int(record["unique_users"]),
                )
                current["last_seen"] = max(
                    float(current.get("last_seen", 0.0)),
                    float(record["last_seen"]),
                )
                current["benign_hits"] = max(
                    int(current.get("benign_hits", 0)),
                    int(record["benign_hits"]),
                )
                current["spam_hits"] = max(
                    int(current.get("spam_hits", 0)),
                    int(record["spam_hits"]),
                )
            imported["learned"] += 1

        self._sync_learned_keywords()
        self.save_state()

        raw_ignored = payload.get("ignored_keywords", [])
        if isinstance(raw_ignored, list):
            for item in raw_ignored:
                if self.ignore_keyword(str(item)):
                    imported["ignored"] += 1

        return imported

    def ignore_keyword(self, keyword: str) -> bool:
        normalized = _normalize_keyword(keyword)
        if not normalized or normalized in self._ignored_keywords:
            return False
        self._ignored_keywords.append(normalized)
        self._learned_meta.pop(normalized, None)
        self._sync_learned_keywords()
        self.save_state()
        return True

    def remove_keyword(self, keyword: str) -> bool:
        normalized = _normalize_keyword(keyword)
        if not normalized or normalized not in self._custom_keywords:
            return False
        self._custom_keywords = [item for item in self._custom_keywords if item != normalized]
        self.reload_keywords()
        self.save_state()
        return True

    def toggle(self, field: str) -> bool:
        current = getattr(self._settings, field)
        new_value = not current
        setattr(self._settings, field, new_value)
        self.save_state()
        return new_value

    def set_action(self, action: str) -> None:
        normalized = action.strip().lower()
        if normalized not in {"mute", "ban"}:
            raise ValueError("action must be 'mute' or 'ban'")
        self._settings.action = normalized
        self.save_state()

    def set_mute_duration(self, seconds: int) -> None:
        if seconds < 0:
            raise ValueError("mute duration must be >= 0")
        self._settings.mute_duration_seconds = seconds
        self.save_state()

    def set_flood_rule(self, max_messages: int, window_seconds: int) -> None:
        if max_messages < 1:
            raise ValueError("flood max messages must be >= 1")
        if window_seconds < 1:
            raise ValueError("flood window must be >= 1")
        self._settings.flood_max_messages = max_messages
        self._settings.flood_window_seconds = window_seconds
        self.save_state()

    def save_state(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {key: getattr(self._settings, key) for key in STATE_KEYS}
        data["custom_keywords"] = list(self._custom_keywords)
        data["learned_keywords"] = _serialize_learned_meta(self._learned_meta)
        data["ignored_keywords"] = list(self._ignored_keywords)
        data["owner_user_ids"] = list(self._owner_user_ids)
        data["group_stats"] = _serialize_group_stats(self._group_stats)
        _save_state(self._state_path, data)


def load_settings_store() -> SettingsStore:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise ValueError("BOT_TOKEN is required")

    action = os.getenv("ACTION", "mute").strip().lower()
    if action not in {"mute", "ban"}:
        raise ValueError("ACTION must be 'mute' or 'ban'")

    log_chat_id_raw = os.getenv("LOG_CHAT_ID", "").strip()
    log_chat_id = int(log_chat_id_raw) if log_chat_id_raw else None

    state = _load_state(STATE_FILE)
    custom_keywords = [
        _normalize_keyword(str(item))
        for item in state.get("custom_keywords", [])
        if _normalize_keyword(str(item))
    ]
    learned_meta = _load_learned_meta(state.get("learned_keywords", []))
    ignored_keywords = [
        _normalize_keyword(str(item))
        for item in state.get("ignored_keywords", [])
        if _normalize_keyword(str(item))
    ]
    group_stats = _load_group_stats(state.get("group_stats", {}))

    raw_owner_ids = state.get("owner_user_ids", [])
    owner_user_ids: list[int] = []
    if isinstance(raw_owner_ids, list):
        for item in raw_owner_ids:
            try:
                owner_user_ids.append(int(item))
            except (TypeError, ValueError):
                continue

    settings = Settings(
        bot_token=token,
        log_chat_id=log_chat_id,
        action=action,
        mute_duration_seconds=_getenv_int("MUTE_DURATION_SECONDS", 86400),
        ban_after_strikes=_getenv_int("BAN_AFTER_STRIKES", 2),
        strike_window_seconds=_getenv_int("STRIKE_WINDOW_SECONDS", 86400),
        admin_cache_ttl_seconds=_getenv_int("ADMIN_CACHE_TTL_SECONDS", 300),
        owner_user_ids=owner_user_ids,
        keywords=[],
        learned_keywords=[],
        ignored_keywords=[],
        learning_enabled=_getenv_bool("LEARNING_ENABLED", True),
        learning_min_hits=_getenv_int("LEARNING_MIN_HITS", 3),
        learning_min_unique_users=_getenv_int("LEARNING_MIN_UNIQUE_USERS", 2),
        learning_promote_hits=_getenv_int("LEARNING_PROMOTE_HITS", 6),
        learning_promote_unique_users=_getenv_int("LEARNING_PROMOTE_UNIQUE_USERS", 3),
        learning_ignore_hits=_getenv_int("LEARNING_IGNORE_HITS", 4),
        learning_ignore_unique_users=_getenv_int("LEARNING_IGNORE_UNIQUE_USERS", 2),
        learning_retire_seconds=_getenv_int("LEARNING_RETIRE_SECONDS", 2592000),
        learning_window_seconds=_getenv_int("LEARNING_WINDOW_SECONDS", 86400),
        rule_enable_link=_getenv_bool("RULE_ENABLE_LINK", True),
        rule_enable_keywords=_getenv_bool("RULE_ENABLE_KEYWORDS", True),
        rule_enable_username=_getenv_bool("RULE_ENABLE_USERNAME", True),
        rule_enable_flood=_getenv_bool("RULE_ENABLE_FLOOD", True),
        rule_enable_repeat=_getenv_bool("RULE_ENABLE_REPEAT", True),
        rule_enable_length=_getenv_bool("RULE_ENABLE_LENGTH", True),
        max_message_length=_getenv_int("MAX_MESSAGE_LENGTH", 600),
        flood_max_messages=_getenv_int("FLOOD_MAX_MESSAGES", 6),
        flood_window_seconds=_getenv_int("FLOOD_WINDOW_SECONDS", 10),
        repeat_max_dupes=_getenv_int("REPEAT_MAX_DUPES", 2),
        repeat_window_seconds=_getenv_int("REPEAT_WINDOW_SECONDS", 60),
        delete_score_threshold=_getenv_int("DELETE_SCORE_THRESHOLD", 15),
        mute_score_threshold=_getenv_int("MUTE_SCORE_THRESHOLD", 45),
        ban_score_threshold=_getenv_int("BAN_SCORE_THRESHOLD", 75),
        link_score=_getenv_int("LINK_SCORE", 35),
        keyword_score=_getenv_int("KEYWORD_SCORE", 60),
        learned_keyword_score=_getenv_int("LEARNED_KEYWORD_SCORE", 18),
        username_score=_getenv_int("USERNAME_SCORE", 20),
        length_score=_getenv_int("LENGTH_SCORE", 15),
        flood_score=_getenv_int("FLOOD_SCORE", 35),
        repeat_score=_getenv_int("REPEAT_SCORE", 25),
        structure_score=_getenv_int("STRUCTURE_SCORE", 12),
        combo_link_keyword_bonus=_getenv_int("COMBO_LINK_KEYWORD_BONUS", 15),
        combo_username_keyword_bonus=_getenv_int("COMBO_USERNAME_KEYWORD_BONUS", 10),
        combo_flood_repeat_bonus=_getenv_int("COMBO_FLOOD_REPEAT_BONUS", 10),
        combo_structure_link_bonus=_getenv_int("COMBO_STRUCTURE_LINK_BONUS", 12),
    )

    _apply_state(settings, state)

    keyword_files = _resolve_keyword_files(BASE_DIR)
    inline_keywords = _getenv_list("KEYWORDS")
    return SettingsStore(
        settings,
        STATE_FILE,
        keyword_files,
        inline_keywords,
        custom_keywords,
        learned_meta,
        ignored_keywords,
        owner_user_ids,
        group_stats,
    )


settings_store = load_settings_store()
settings = settings_store.settings
