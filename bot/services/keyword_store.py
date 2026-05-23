from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import time

from bot.schemas.lexicon import LexiconKind, LexiconSnapshot
from bot.services.keywords import LexiconLoadError, load_lexicon_snapshot


logger = logging.getLogger(__name__)


@dataclass
class KeywordStore:
    directory_path: Path
    refresh_seconds: int
    _keywords: list[str] | None = None
    _snapshot: LexiconSnapshot | None = None
    _last_loaded_at: float = 0.0

    def get_keywords(self) -> list[str]:
        now = time.time()
        if self._keywords is None or self._snapshot is None:
            self._snapshot = load_lexicon_snapshot(self.directory_path)
            self._keywords = [
                item.normalized_value
                for item in self._snapshot.entries
                if item.kind == LexiconKind.WORD and item.enabled and item.category != "word_whitelist"
            ]
            self._keywords = list(dict.fromkeys(self._keywords))
            self._last_loaded_at = now
            return self._keywords

        if now - self._last_loaded_at >= self.refresh_seconds:
            self._reload_with_fallback(now)

        return self._keywords

    def get_snapshot(self) -> LexiconSnapshot:
        _ = self.get_keywords()
        if self._snapshot is None:
            raise LexiconLoadError("lexicon snapshot not loaded")
        return self._snapshot

    def force_reload(self) -> list[str]:
        now = time.time()
        self._reload_with_fallback(now)
        if self._keywords is None:
            raise LexiconLoadError("keyword store is empty after reload")
        return self._keywords

    def _reload_with_fallback(self, now: float) -> None:
        previous_keywords = self._keywords
        previous_snapshot = self._snapshot
        try:
            loaded_snapshot = load_lexicon_snapshot(self.directory_path)
            loaded_keywords = [
                item.normalized_value
                for item in loaded_snapshot.entries
                if item.kind == LexiconKind.WORD and item.enabled and item.category != "word_whitelist"
            ]
            loaded_keywords = list(dict.fromkeys(loaded_keywords))
        except Exception as exc:
            logger.error(
                "lexicon_reload_failed",
                extra={
                    "directory_path": str(self.directory_path),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            if previous_keywords is not None and previous_snapshot is not None:
                self._keywords = previous_keywords
                self._snapshot = previous_snapshot
                self._last_loaded_at = now
                return
            raise
        self._snapshot = loaded_snapshot
        self._keywords = loaded_keywords
        self._last_loaded_at = now
