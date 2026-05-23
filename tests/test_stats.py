from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest

from app.config import Settings, SettingsStore


def make_settings() -> Settings:
    return Settings(
        bot_token="test-token",
        log_chat_id=None,
        action="mute",
        mute_duration_seconds=86400,
        ban_after_strikes=0,
        strike_window_seconds=86400,
        admin_cache_ttl_seconds=300,
        owner_user_ids=[],
        keywords=[],
        learned_keywords=[],
        ignored_keywords=[],
        learning_enabled=True,
        learning_min_hits=3,
        learning_min_unique_users=2,
        learning_promote_hits=999,
        learning_promote_unique_users=999,
        learning_ignore_hits=4,
        learning_ignore_unique_users=2,
        learning_retire_seconds=2592000,
        learning_window_seconds=86400,
        rule_enable_link=True,
        rule_enable_keywords=True,
        rule_enable_username=True,
        rule_enable_flood=True,
        rule_enable_repeat=True,
        rule_enable_length=True,
        max_message_length=600,
        flood_max_messages=6,
        flood_window_seconds=10,
        repeat_max_dupes=2,
        repeat_window_seconds=60,
        delete_score_threshold=20,
        mute_score_threshold=60,
        ban_score_threshold=100,
        link_score=35,
        keyword_score=60,
        learned_keyword_score=18,
        username_score=20,
        length_score=15,
        flood_score=35,
        repeat_score=25,
        structure_score=12,
        combo_link_keyword_bonus=15,
        combo_username_keyword_bonus=10,
        combo_flood_repeat_bonus=10,
        combo_structure_link_bonus=12,
    )


class StatsTests(unittest.TestCase):
    def test_learning_and_group_stats_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            store = SettingsStore(
                make_settings(),
                state_path,
                [],
                [],
                [],
                {},
                [],
                [],
                {},
            )

            now = time.time()
            self.assertEqual(store.add_learned_keyword("alpha", 3, 2, now), "learned")
            store.record_learned_feedback("alpha", True, now + 1.0)
            store.record_learned_feedback("alpha", False, now + 2.0)
            store.record_group_seen(100, 200, now + 3.0)
            store.record_group_spam(100, 200, "muted", 40, now + 4.0)

            learning_snapshot = store.learning_stats_snapshot()
            group_snapshot = store.group_stats_snapshot(100, 5)

            self.assertEqual(learning_snapshot["learned_keywords"], 1)
            self.assertEqual(learning_snapshot["benign_feedback"], 1)
            self.assertEqual(learning_snapshot["spam_feedback"], 2)
            self.assertEqual(group_snapshot["totals"]["groups"], 1)
            self.assertEqual(group_snapshot["totals"]["messages_seen"], 1)
            self.assertEqual(group_snapshot["totals"]["muted_messages"], 1)


if __name__ == "__main__":
    unittest.main()
