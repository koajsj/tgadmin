from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, BotCommandScopeAllGroupChats, BotCommandScopeAllPrivateChats

from bot.app_context import AppContext
from bot.config import load_settings
from bot.database.session import create_engine, create_redis, create_session_factory, init_schema
from bot.handlers import ALL_ROUTERS
from bot.services.keyword_store import KeywordStore


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


async def setup_commands(bot: Bot) -> None:
    private_commands = [
        BotCommand(command="start", description="打开私聊控制台与主菜单"),
        BotCommand(command="panel", description="进入群组管理面板"),
        BotCommand(command="help", description="查看命令与权限说明"),
        BotCommand(command="settings", description="查看当前群配置"),
        BotCommand(command="status", description="查看运行与配置状态"),
        BotCommand(command="history", description="查询用户处罚历史"),
        BotCommand(command="setlog", description="设置日志群（仅Owner）"),
        BotCommand(command="reloadkeywords", description="刷新关键词词库（仅Owner）"),
        BotCommand(command="lexicon", description="词库查询/导入/导出（仅Owner）"),
        BotCommand(command="template", description="规则模板预览/应用（仅Owner）"),
        BotCommand(command="nightmode", description="夜间策略配置（仅Owner）"),
        BotCommand(command="falsepositive", description="误判标记与回滚（仅Owner）"),
        BotCommand(command="fprules", description="高误判规则统计（仅Owner）"),
    ]
    group_commands = [
        BotCommand(command="help", description="查看命令与权限说明"),
        BotCommand(command="warn", description="警告用户（管理员）"),
        BotCommand(command="mute", description="禁言用户（短禁言管理员可用）"),
        BotCommand(command="ban", description="封禁用户（仅Owner）"),
        BotCommand(command="unban", description="解封用户（仅Owner）"),
        BotCommand(command="history", description="查看处罚历史（管理员）"),
        BotCommand(command="settings", description="查看群规则配置（管理员）"),
    ]
    await bot.set_my_commands(private_commands, scope=BotCommandScopeAllPrivateChats())
    await bot.set_my_commands(group_commands, scope=BotCommandScopeAllGroupChats())


async def run_bot() -> None:
    settings = load_settings()
    setup_logging(settings.log_level)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis = create_redis(settings)
    if settings.auto_init_schema:
        await init_schema(engine)

    keyword_files_dir = Path(__file__).resolve().parent.parent / "data"
    keyword_store = KeywordStore(
        directory_path=keyword_files_dir,
        refresh_seconds=settings.keyword_refresh_seconds,
    )
    app_context = AppContext(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        redis=redis,
        keyword_files_dir=keyword_files_dir,
        keyword_store=keyword_store,
    )

    bot = Bot(token=settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    await setup_commands(bot)
    dispatcher = Dispatcher()
    for router in ALL_ROUTERS:
        dispatcher.include_router(router)

    try:
        await dispatcher.start_polling(bot, app_context=app_context)
    finally:
        await redis.aclose()
        await engine.dispose()
        await bot.session.close()


def main() -> None:
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
