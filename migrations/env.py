from __future__ import annotations

import asyncio
from logging.config import fileConfig
import os

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from bot.database.models import Base


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


target_metadata = Base.metadata


def get_url() -> str:
    env_url = os.getenv("DATABASE_URL", "").strip()
    if env_url != "":
        return env_url
    configured = config.get_main_option("sqlalchemy.url")
    return configured


def run_migrations_offline() -> None:
    url = get_url()
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations_with_connection(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations(section: dict[str, str]) -> None:
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(_run_migrations_with_connection)
    await connectable.dispose()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section)
    if section is None:
        raise RuntimeError("alembic config section missing")
    section["sqlalchemy.url"] = get_url()

    url = section["sqlalchemy.url"]
    if "+asyncpg" in url:
        asyncio.run(_run_async_migrations(section))
        return

    connectable = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        _run_migrations_with_connection(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
