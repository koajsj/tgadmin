from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bot.config import SettingsError, load_settings
from bot.services.keyword_store import KeywordStore


class ConfigAndCacheTests(unittest.TestCase):
    def test_load_settings_parses_new_fields(self) -> None:
        env = {
            "BOT_TOKEN": "token",
            "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "AUTO_INIT_SCHEMA": "true",
            "KEYWORD_REFRESH_SECONDS": "15",
        }
        with patch.dict(os.environ, env, clear=False):
            settings = load_settings()
        self.assertTrue(settings.auto_init_schema)
        self.assertEqual(settings.keyword_refresh_seconds, 15)

    def test_load_settings_rejects_invalid_default_log_chat_id(self) -> None:
        env = {
            "BOT_TOKEN": "token",
            "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "DEFAULT_LOG_CHAT_ID": "abc",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(SettingsError):
                load_settings()

    def test_load_settings_rejects_invalid_positive_constraints(self) -> None:
        env = {
            "BOT_TOKEN": "token",
            "DATABASE_URL": "postgresql+asyncpg://u:p@h:5432/db",
            "REDIS_URL": "redis://localhost:6379/0",
            "FLOOD_WINDOW_SECONDS": "0",
        }
        with patch.dict(os.environ, env, clear=False):
            with self.assertRaises(SettingsError):
                load_settings()

    def test_keyword_store_cache_and_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            file_path = root / "ad_low.txt"
            file_path.write_text("alpha\n", encoding="utf-8")

            store = KeywordStore(directory_path=root, refresh_seconds=60)
            first = store.get_keywords()
            self.assertEqual(first, ["alpha"])

            file_path.write_text("beta\n", encoding="utf-8")
            second = store.get_keywords()
            self.assertEqual(second, ["alpha"])

            reloaded = store.force_reload()
            self.assertEqual(reloaded, ["beta"])


if __name__ == "__main__":
    unittest.main()
