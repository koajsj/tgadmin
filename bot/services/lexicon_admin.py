from __future__ import annotations

from hashlib import sha1
import json
from pathlib import Path

from bot.schemas.lexicon import LexiconKind, ModerationAction, RiskLevel
from bot.services.keywords import normalize_domain, normalize_text


class LexiconAdminError(RuntimeError):
    """Raised when lexicon administration operation fails."""


def _custom_file_path(directory_path: Path) -> Path:
    return directory_path / "custom_lexicon.json"


def _ensure_directory(directory_path: Path) -> None:
    directory_path.mkdir(parents=True, exist_ok=True)


def _read_custom_entries(directory_path: Path) -> list[dict[str, object]]:
    _ensure_directory(directory_path)
    file_path = _custom_file_path(directory_path)
    if not file_path.exists():
        return []
    payload = json.loads(file_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise LexiconAdminError("custom_lexicon.json must be a list")
    rows: list[dict[str, object]] = []
    for item in payload:
        if not isinstance(item, dict):
            raise LexiconAdminError("custom_lexicon.json contains non-object item")
        rows.append(dict(item))
    return rows


def _write_custom_entries(directory_path: Path, rows: list[dict[str, object]]) -> None:
    file_path = _custom_file_path(directory_path)
    temp_path = file_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(file_path)


def _next_entry_id(kind: str, category: str, value: str, source: str) -> str:
    raw = f"{kind}|{category}|{value}|{source}"
    return sha1(raw.encode("utf-8")).hexdigest()[:16]


def _validate_kind(kind: str) -> LexiconKind:
    for item in LexiconKind:
        if kind == item.value:
            return item
    raise LexiconAdminError(f"unsupported kind: {kind}")


def _validate_risk_level(risk_level: str) -> RiskLevel:
    for item in RiskLevel:
        if risk_level == item.value:
            return item
    raise LexiconAdminError(f"unsupported risk_level: {risk_level}")


def _normalize_value(kind: LexiconKind, value: str) -> str:
    if kind == LexiconKind.DOMAIN:
        return normalize_domain(value)
    return normalize_text(value)


def add_entry(
    directory_path: Path,
    kind: str,
    category: str,
    risk_level: str,
    value: str,
    source: str,
    observe_only: bool,
    action_override: str | None,
    mute_seconds_override: int | None,
) -> str:
    validated_kind = _validate_kind(kind)
    _ = _validate_risk_level(risk_level)
    normalized_category = normalize_text(category)
    normalized_value = _normalize_value(validated_kind, value)
    if normalized_category == "" or normalized_value == "":
        raise LexiconAdminError("category and value cannot be empty")

    rows = _read_custom_entries(directory_path)
    entry_id = _next_entry_id(validated_kind.value, normalized_category, normalized_value, source)

    for row in rows:
        row_id = str(row.get("entry_id", ""))
        if row_id == entry_id:
            raise LexiconAdminError("entry already exists")

    payload: dict[str, object] = {
        "entry_id": entry_id,
        "kind": validated_kind.value,
        "category": normalized_category,
        "risk_level": risk_level,
        "value": normalized_value,
        "source": source,
        "enabled": True,
        "observe_only": observe_only,
    }
    if action_override is not None and action_override != "":
        override_ok = any(item.value == action_override for item in ModerationAction)
        if not override_ok:
            raise LexiconAdminError(f"unsupported action_override: {action_override}")
        payload["action_override"] = action_override
    if mute_seconds_override is not None:
        payload["mute_seconds_override"] = mute_seconds_override

    rows.append(payload)
    _write_custom_entries(directory_path, rows)
    return entry_id


def delete_entry(directory_path: Path, entry_id: str) -> bool:
    rows = _read_custom_entries(directory_path)
    original_length = len(rows)
    kept = [row for row in rows if str(row.get("entry_id", "")) != entry_id]
    if len(kept) == original_length:
        return False
    _write_custom_entries(directory_path, kept)
    return True


def set_entry_enabled(directory_path: Path, entry_id: str, enabled: bool) -> bool:
    rows = _read_custom_entries(directory_path)
    changed = False
    for row in rows:
        if str(row.get("entry_id", "")) == entry_id:
            row["enabled"] = enabled
            changed = True
            break
    if changed:
        _write_custom_entries(directory_path, rows)
    return changed


def bulk_import(
    directory_path: Path,
    kind: str,
    category: str,
    risk_level: str,
    source: str,
    values: list[str],
    observe_only: bool,
) -> int:
    imported = 0
    for value in values:
        clean = normalize_text(value)
        if clean == "":
            continue
        try:
            add_entry(
                directory_path=directory_path,
                kind=kind,
                category=category,
                risk_level=risk_level,
                value=clean,
                source=source,
                observe_only=observe_only,
                action_override=None,
                mute_seconds_override=None,
            )
        except LexiconAdminError:
            continue
        imported += 1
    return imported


def export_custom_entries(directory_path: Path) -> str:
    rows = _read_custom_entries(directory_path)
    return json.dumps(rows, ensure_ascii=False, indent=2)


def search_custom_entries(directory_path: Path, query_text: str) -> list[dict[str, object]]:
    rows = _read_custom_entries(directory_path)
    query = normalize_text(query_text)
    if query == "":
        return rows
    result: list[dict[str, object]] = []
    for row in rows:
        raw_value = str(row.get("value", ""))
        raw_category = str(row.get("category", ""))
        if query in normalize_text(raw_value) or query in normalize_text(raw_category):
            result.append(row)
    return result
