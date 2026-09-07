"""Scratch-database helpers for tests that need real SQL (T8).

Every DB-backed test file creates its own database, migrates it to `head`, and drops it,
skipping rather than failing when Postgres is unreachable so the suite still runs without
Docker — **unless `NOA_REQUIRE_POSTGRES` is set, and CI sets it** (V102): the same skip that
keeps a laptop useful is what lets a pipeline read green over the 23 files that need SQL.
Shared here because two files needed the same forty lines (V66) and because the skip
behaviour must be identical — one file failing where another skips reads as a real
regression.

Separate database per caller, never the dev one: `POSTGRES_URL` supplies host and
credentials, and only the database name is swapped.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit, urlunsplit

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings

# Alembic's runner lives with the API app (C12: one history for the monorepo).
API_DIR = Path(__file__).resolve().parents[2]

# Same URL resolution the app and Alembic use (T5).
DEV_URL = get_settings().postgres_url_str

# V102: the skip below is what lets a laptop without Docker still run 92 of the 115 test files,
# and it is also what lets a pipeline exit 0 over the 23 that carry every live auth, RBAC,
# approval-decision, audit, migration and repository behaviour. A suite reporting its own absence
# as success is V86's shape landing in the harness. So the skip is *opt-out*: CI sets this variable
# beside its `services:` entry, and an unreachable server then fails instead of vanishing.
#
# Both halves or neither. The service alone is unverifiable — one that fails to start returns the
# suite to silently-green, which is the state this exists to leave.
REQUIRE_POSTGRES_ENV_VAR = "NOA_REQUIRE_POSTGRES"

# `0`/`false`/`no`/`off` read as "not required" rather than as "set, therefore required": a
# variable pinned off in one job's `variables:` block would otherwise turn that job strict, which
# is the opposite of what writing it there means.
_NEGATIVE = frozenset({"", "0", "false", "no", "off"})


def postgres_required() -> bool:
    """Whether an unreachable Postgres is a failure rather than a skip (V102)."""
    return os.environ.get(REQUIRE_POSTGRES_ENV_VAR, "").strip().lower() not in _NEGATIVE


def unreachable_postgres(exc: BaseException) -> NoReturn:
    """Skip, or fail when the environment says the service was supposed to be there (V102).

    One function rather than a check at each call site, for the reason this module's docstring
    already gives: the skip behaviour must be identical everywhere, because one file failing where
    another skips reads as a real regression (V66).
    """
    message = f"Postgres unavailable: {exc}"
    if postgres_required():
        pytest.fail(f"{message} — {REQUIRE_POSTGRES_ENV_VAR} is set, so this is not a skip")
    pytest.skip(message)


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
# `action_requests` joins at T34 for both of those reasons, and CASCADE from `tool_runs`
# is no help either: its `tool_run_id` is `SET NULL` as well, so a decision row outlives
# every other row a test created.
# `action_receipts` joins at T36. This one *is* reached by the truncate above — its FK to
# `action_requests` cascades — and is named anyway, because that is a property of today's
# schema rather than of this list: a later change to that FK would silently start leaving
# receipts behind, and the failure would land in whichever test happened to count rows.
# `tool_result_tables` joins at T56, for `tool_runs`' reason exactly: its
# `requested_by_user_id` is `SET NULL`, so a parked table left by an earlier test outlives its
# requester and would still be found by a token lookup — or counted by a test that counts rows.
MUTATED_TABLES = (
    "users",
    "roles",
    "role_tool_permissions",
    "login_rate_limits",
    "mcp_tokens",
    "whm_servers",
    "pmg_servers",
    # Added at T54, which is the first task that writes it. Missing until then was harmless
    # only because nothing inserted a Proxmox row; a table absent from this tuple leaks state
    # between tests in the same module.
    "proxmox_servers",
    "tool_runs",
    "action_requests",
    "action_receipts",
    "tool_result_tables",
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

    Unreachable server → `unreachable_postgres` (skip, or fail under `NOA_REQUIRE_POSTGRES`,
    V102). A *failed migration* is an assertion either way: that is a broken migration, not a
    missing environment, so it is never the environment's to excuse.
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
        unreachable_postgres(exc)

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
    "REQUIRE_POSTGRES_ENV_VAR",
    "migrated_database",
    "postgres_required",
    "run_alembic",
    "run_statements",
    "swap_database",
    "truncate",
    "unreachable_postgres",
]
