"""Alembic environment. One history for the whole monorepo.

Models live in `core.db` (shared), migrations live here (the API owns the runner).
The URL comes from settings or `-x url=...`, never from `alembic.ini`.
"""

from __future__ import annotations

import asyncio
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# `core` sits at the repo root, two levels above `apps/api`.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.config import get_settings  # noqa: E402  (path setup must precede imports)
from core.db import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# `-x url=...` beats settings, letting CI and one-off checks target a scratch
# database without touching any file or env var.
_url = context.get_x_argument(as_dictionary=True).get("url") or get_settings().postgres_url_str
config.set_main_option("sqlalchemy.url", _url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection (`alembic upgrade --sql`)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations against a live DB over asyncpg."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
