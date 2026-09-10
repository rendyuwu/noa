"""Alembic migration guards.

Two levels:

- Offline (`--sql`): always runs, no DB. Catches syntax/graph breakage.
- Online: needs Postgres. Creates a scratch DB, runs `upgrade head`, compares the
  live schema against `Base.metadata`, then `downgrade base`. Skipped when no DB
  is reachable, so the suite stays runnable without Docker.

Everything goes over asyncpg — the only driver the project pins. The scratch
DB is created and dropped here; the dev database is never touched.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings
from core.db import Base

API_DIR = Path(__file__).resolve().parents[1]
# Same URL resolution the app and Alembic use.
DEV_URL = get_settings().postgres_url_str
SCRATCH_DB = "noa_migration_test"
# Alembic's own bookkeeping table; excluded when diffing against `Base.metadata`.
ALEMBIC_TABLE = "alembic_version"


def _swap_database(url: str, database: str) -> str:
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603  (fixed argv, no shell)
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


async def _run_statements(url: str, *statements: str) -> None:
    """Execute autocommit statements (CREATE/DROP DATABASE cannot run in a tx)."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.execute(sa.text(statement))
    finally:
        await engine.dispose()


async def _reflect(url: str) -> dict[str, set[str]]:
    """Live schema as `{table: {column, ...}}`, Alembic's own table excluded."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(_reflect_sync)
    finally:
        await engine.dispose()


def _reflect_sync(connection: sa.Connection) -> dict[str, set[str]]:
    inspector = sa.inspect(connection)
    return {
        name: {column["name"] for column in inspector.get_columns(name)}
        for name in inspector.get_table_names()
        if name != ALEMBIC_TABLE
    }


async def _scalar(url: str, statement: str) -> Any:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(sa.text(statement))
            return result.scalar_one()
    finally:
        await engine.dispose()


def test_upgrade_head_offline_emits_all_tables() -> None:
    """Offline render works and covers every schema-v1 table."""
    result = _alembic("upgrade", "head", "--sql", url=DEV_URL)

    assert result.returncode == 0, result.stderr
    for table in Base.metadata.tables:
        assert f"CREATE TABLE {table}" in result.stdout


def test_single_head() -> None:
    """One linear history — a branched head breaks `upgrade head` (C12: one Alembic)."""
    result = _alembic("heads", url=DEV_URL)

    assert result.returncode == 0, result.stderr
    heads = [line for line in result.stdout.splitlines() if "(head)" in line]
    assert len(heads) == 1, result.stdout


@pytest.fixture(scope="module")
def scratch_database() -> Iterator[str]:
    """A throwaway database at schema v1's starting point.

    Skips (never fails) when Postgres is unreachable so the suite runs without
    Docker.
    """
    admin_url = _swap_database(DEV_URL, "postgres")
    create = (
        f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"',
        f'CREATE DATABASE "{SCRATCH_DB}"',
    )

    try:
        asyncio.run(_run_statements(admin_url, *create))
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres unavailable: {exc}")

    try:
        yield _swap_database(DEV_URL, SCRATCH_DB)
    finally:
        asyncio.run(_run_statements(admin_url, f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"'))


def test_upgrade_then_downgrade_round_trip(scratch_database: str) -> None:
    """`upgrade head` builds the schema; `downgrade base` leaves nothing behind."""
    upgrade = _alembic("upgrade", "head", url=scratch_database)
    assert upgrade.returncode == 0, upgrade.stderr

    live = asyncio.run(_reflect(scratch_database))

    # Migration DDL and ORM metadata must not drift (V67 keeps them honest).
    assert set(live) == set(Base.metadata.tables)
    for table_name, table in Base.metadata.tables.items():
        assert live[table_name] == set(table.c.keys()), table_name

    downgrade = _alembic("downgrade", "base", url=scratch_database)
    assert downgrade.returncode == 0, downgrade.stderr

    assert asyncio.run(_reflect(scratch_database)) == {}


def test_gen_random_uuid_available_without_pgcrypto(scratch_database: str) -> None:
    """C3 pins Postgres 16, where `gen_random_uuid()` is core.

    If this ever fails, the migration needs `CREATE EXTENSION pgcrypto`.
    """
    value = asyncio.run(_scalar(scratch_database, "SELECT gen_random_uuid()"))

    assert value is not None
