from __future__ import annotations

from dataclasses import dataclass
import unittest

from bot.config import Settings
from bot.schemas.permissions import ActorRole, PermissionAction
from bot.utils.permissions import authorize_action


@dataclass
class FakeMember:
    status: str


class FakeBot:
    def __init__(self, statuses: dict[tuple[int, int], str]) -> None:
        self._statuses = statuses

    async def get_chat_member(self, chat_id: int, user_id: int) -> FakeMember:
        status = self._statuses.get((chat_id, user_id), "member")
        return FakeMember(status=status)


class PermissionTests(unittest.IsolatedAsyncioTestCase):
    def _settings(self) -> Settings:
        return Settings(
            bot_token="token",
            database_url="postgresql+asyncpg://u:p@h:5432/db",
            redis_url="redis://localhost:6379/0",
            log_level="INFO",
            owner_ids=(1000,),
            default_log_chat_id=None,
            environment="test",
            webhook_url=None,
            webhook_secret=None,
            newcomer_watch_seconds=86400,
            newcomer_allow_links=False,
            newcomer_allow_media=False,
            flood_window_seconds=10,
            flood_max_messages=5,
            mute_minutes_step3=10,
            mute_hours_step4=24,
            auto_init_schema=False,
            keyword_refresh_seconds=60,
            group_admin_max_mute_seconds=3600,
        )

    async def test_owner_can_ban(self) -> None:
        bot = FakeBot({})
        decision = await authorize_action(bot, self._settings(), 1000, -1001, PermissionAction.BAN, None)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.role, ActorRole.OWNER)

    async def test_group_admin_cannot_ban(self) -> None:
        bot = FakeBot({(-1001, 2000): "administrator"})
        decision = await authorize_action(bot, self._settings(), 2000, -1001, PermissionAction.BAN, None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.role, ActorRole.GROUP_ADMIN)

    async def test_group_admin_can_short_mute(self) -> None:
        bot = FakeBot({(-1001, 2000): "administrator"})
        decision = await authorize_action(bot, self._settings(), 2000, -1001, PermissionAction.MUTE_ANY, 1800)
        self.assertTrue(decision.allowed)

    async def test_group_admin_cannot_long_mute(self) -> None:
        bot = FakeBot({(-1001, 2000): "administrator"})
        decision = await authorize_action(bot, self._settings(), 2000, -1001, PermissionAction.MUTE_ANY, 86400)
        self.assertFalse(decision.allowed)

    async def test_member_denied_warn(self) -> None:
        bot = FakeBot({})
        decision = await authorize_action(bot, self._settings(), 3000, -1001, PermissionAction.WARN, None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.role, ActorRole.MEMBER)

    async def test_group_admin_cannot_export_data(self) -> None:
        bot = FakeBot({(-1001, 2000): "administrator"})
        decision = await authorize_action(bot, self._settings(), 2000, -1001, PermissionAction.EXPORT_DATA, None)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.role, ActorRole.GROUP_ADMIN)


if __name__ == "__main__":
    unittest.main()
