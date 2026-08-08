"""Scratch-database helpers for tests that need real SQL (T8).

Every DB-backed test file creates its own database, migrates it to `head`, and drops it,
skipping rather than failing when Postgres is unreachable so the suite still runs without
Docker. Shared here because two files needed the same forty lines (V66) and because the
skip behaviour must be identical — one file failing where another skips reads as a real
regression.

Separate database per caller, never the dev one: `POSTGRES_URL` supplies host and
credentials, and only the database name is swapped.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings

# Alembic's runner lives with the API app (C12: one history for the monorepo).
API_DIR = Path(__file__).resolve().parents[2]

# Same URL resolution the app and Alembic use (T5).
DEV_URL = get_settings().postgres_url_str

# Tables the auth, RBAC and MCP-token tests write to, truncated between tests. `CASCADE`
# reaches `user_roles`, which has a foreign key into both `users` and `roles`.
# `role_tool_permissions` is named explicitly rather than left to the cascade from `roles`:
# a T9 test that grants tools without creating a user must still start empty. `mcp_tokens`
# for the same reason at T10 — the cascade from `users` would clear it only when a test
# happened to create one.
# `whm_servers` joins the list at T19: `whm_list_servers` and `resolve_whm_server_ref` read
# it, and nothing cascades to it from `users` or `roles`, so a repository test that inserts
# servers has to start from an empty table of its own.
# `tool_runs` joins at T35. `TRUNCATE users CASCADE` would reach it anyway, but only when a
# test happens to create a user — and its `requested_by_user_id` is `SET NULL`, so a run
# left behind by an earlier test survives its requester and would still be counted.
MUTATED_TABLES = (
    "users",
    "roles",
    "role_tool_permissions",
    "login_rate_limits",
    "mcp_tokens",
    "whm_servers",
    "pmg_servers",
    "tool_runs",
)


def swap_database(url: str, database: str) -> str:
    """The same DSN pointed at a different database name."""
    parts = urlsplit(url)
    return urlunsplit(parts._replace(path=f"/{database}"))


async def run_statements(url: str, *statements: str) -> None:
    """Execute autocommit statements (CREATE/DROP DATABASE cannot run in a tx)."""
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.execute(sa.text(statement))
    finally:
        await engine.dispose()


def run_alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    """Invoke Alembic against `url` via `-x url=`, never a file or env var (C11)."""
    return subprocess.run(  # noqa: S603  (fixed argv, no shell)
        [sys.executable, "-m", "alembic", "-x", f"url={url}", *args],
        cwd=API_DIR,
        capture_output=True,
        text=True,
        check=False,
    )


@contextmanager
def migrated_database(name: str) -> Iterator[str]:
    """Create `name`, migrate to `head`, yield its URL, then drop it.

    `pytest.skip` on an unreachable server, but a *failed migration* is an assertion:
    that is a broken migration, not a missing environment.
    """
    admin_url = swap_database(DEV_URL, "postgres")

    try:
        asyncio.run(
            run_statements(
                admin_url,
                f'DROP DATABASE IF EXISTS "{name}"',
                f'CREATE DATABASE "{name}"',
            )
        )
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"Postgres unavailable: {exc}")

    url = swap_database(DEV_URL, name)
    upgrade = run_alembic("upgrade", "head", url=url)
    assert upgrade.returncode == 0, upgrade.stderr

    try:
        yield url
    finally:
        asyncio.run(run_statements(admin_url, f'DROP DATABASE IF EXISTS "{name}"'))


async def truncate(url: str, *tables: str) -> None:
    """Empty `tables` (CASCADE) so each test starts from a known state.

    Truncation rather than a wrapping transaction: these tests assert on commit
    behaviour, and a rollback-per-test would hide exactly that.
    """
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                sa.text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE")
            )
    finally:
        await engine.dispose()


__all__ = [
    "API_DIR",
    "DEV_URL",
    "MUTATED_TABLES",
    "migrated_database",
    "run_alembic",
    "run_statements",
    "swap_database",
    "truncate",
]
