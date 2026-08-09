"""`tool_result_tables` table (T56, V64, V85).

Two levels, for two different claims — the split `test_action_receipts_schema.py` draws:

- **Metadata** — the column set, the unique constraint, the index set, the absences this
  design turns on. No DB needed.
- **Live** — that Postgres refuses a second row under one token, that deleting the requester
  leaves the table behind with a NULL owner, and that the payload columns really do refuse an
  omitted value. None of those is provable from `Base.metadata`: an FK's `ondelete` is a
  string until a real `DELETE` runs against it, and a `UniqueConstraint` in metadata says
  nothing about what the migration built. Skipped (never failed) when Postgres is unreachable.

The `SET NULL` is the one worth reading twice. Every other user FK since T35 is `SET NULL`
because the row still describes something without its subject, and here the reason is
narrower and sharper: the reader matches on that column, so NULL has to mean *nobody* rather
than *anybody* (V27). The live half asserts that a deleted operator's table is still on disk
and no longer matches its old owner.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.db import Base
from core.db.models import ToolResultTable, User
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_tool_result_tables_schema_test"

TOOL_RESULT_TABLES = Base.metadata.tables["tool_result_tables"]

# §T.56's column list. A column added without a task to specify it is a guess; one removed
# takes a V64 or V85 property with it.
T56_COLUMNS = {
    "id",
    "token",
    "requested_by_user_id",
    "tool_name",
    "column_labels",
    "rows",
    "total_rows",
    "truncated",
    "created_at",
    "expires_at",
}

# What this table refuses, each named so a failure says which boundary was crossed.
FORBIDDEN_COLUMNS = {
    # C8, V43: the reason is operator-typed on an approval card and lives on
    # `action_requests`. A READ has no reason at all — nothing was authorised.
    "reason": "C8, V43 — one reason, and a READ has none",
    # V27 matches on `requested_by_user_id`. A second identity column would be a second
    # answer to who may read the table.
    "requested_by_email": "V27 — one requester column, and it is the FK",
    # A parked table is written once and read many times. There is no second moment to
    # stamp, and a `status` would be a lifecycle nothing drives (T34's third-truth rule).
    "updated_at": "written once; there is no second moment to stamp",
    "status": "nothing transitions here — the deadline is the whole lifecycle",
    # The audit row for the same call lives in `tool_runs` (T73). A link would be a second
    # record of one moment, and nothing reads it.
    "tool_run_id": "T73 owns the audit row; a link here would be a second record",
}


# --------------------------------------------------------------------------------------
# Metadata: shape, uniqueness, indexes
# --------------------------------------------------------------------------------------


def test_column_set_is_exactly_what_t56_specifies() -> None:
    """No extra columns, no missing ones."""
    assert set(TOOL_RESULT_TABLES.c.keys()) == T56_COLUMNS


@pytest.mark.parametrize(("column", "why"), sorted(FORBIDDEN_COLUMNS.items()))
def test_the_columns_this_design_refuses_are_absent(column: str, why: str) -> None:
    """Each absence is load-bearing, not an oversight — the reason is the test id."""
    assert column not in TOOL_RESULT_TABLES.c, why


def test_one_row_per_token_is_a_constraint_not_a_convention() -> None:
    """The token names one table. Two rows under it would make "the table" mean "some table".

    Also the lookup index: every reader arrives holding a token, which is why there is no
    second index here (T36's discipline).
    """
    unique = {
        tuple(column.name for column in constraint.columns)
        for constraint in TOOL_RESULT_TABLES.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }

    assert ("token",) in unique
    assert {index.name for index in TOOL_RESULT_TABLES.indexes} == set()


def test_the_requester_link_fails_closed() -> None:
    """V27: nullable with `SET NULL`, so a deleted operator's table matches nobody.

    The nullability is what makes the refusal possible at all — a NOT NULL column with
    `CASCADE` would delete the audit-adjacent row instead, and one with `RESTRICT` would make
    deleting an operator fail once they had run a listing.
    """
    requested_by_user_id = TOOL_RESULT_TABLES.c.requested_by_user_id

    assert requested_by_user_id.nullable is True
    (fk,) = requested_by_user_id.foreign_keys
    assert fk.column is Base.metadata.tables["users"].c.id
    assert fk.ondelete == "SET NULL"


@pytest.mark.parametrize("column", ["column_labels", "rows"])
def test_the_payload_columns_are_required_and_have_no_default(column: str) -> None:
    """V64: a table with no columns and no rows is a failed insert, not a parked page.

    Deliberately unlike `tool_runs.args` (`'{}'` there so "took no arguments" and "not
    recorded" stay distinguishable) and exactly like `approval_context` and `receipt_data`.
    """
    payload = TOOL_RESULT_TABLES.c[column]

    assert payload.nullable is False
    assert payload.server_default is None


@pytest.mark.parametrize("column", ["total_rows", "truncated"])
def test_the_bound_is_stored_not_derived(column: str) -> None:
    """V85: the count before the cut and whether there was one, both NOT NULL.

    Stored rather than computed from `rows`, because `len(rows)` is precisely the number a
    capped table must not report as its total.
    """
    assert TOOL_RESULT_TABLES.c[column].nullable is False


def test_the_deadline_is_required_and_aware() -> None:
    """T34's rule one table over: a row without a deadline cannot expire."""
    expires_at = TOOL_RESULT_TABLES.c.expires_at

    assert expires_at.nullable is False
    assert expires_at.type.timezone is True


# --------------------------------------------------------------------------------------
# Live: what only a real Postgres can answer
# --------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    """A scratch database at `head`. Skips when Postgres is unreachable."""
    with migrated_database(SCRATCH_DB) as url:
        yield url


@pytest.fixture
async def session(database_url: str) -> AsyncIterator[AsyncSession]:
    """Per-test engine and session, mutated tables emptied first."""
    await truncate(database_url, *MUTATED_TABLES)
    engine = create_async_engine(database_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as opened:
            yield opened
    finally:
        await engine.dispose()


COLUMN_LABELS: list[dict[str, Any]] = [
    {"key": "user", "label": "Account"},
    {"key": "domain", "label": "Primary domain"},
]

ROWS: list[dict[str, Any]] = [{"user": "acmeco", "domain": "acme.example"}]


async def add_operator(session: AsyncSession, email: str = "operator@example.com") -> User:
    user = User(email=email, ldap_dn=f"CN={email}", display_name="Operator", is_active=True)
    session.add(user)
    await session.commit()
    return user


def parked_table(*, token: str, requester: User | None) -> ToolResultTable:
    return ToolResultTable(
        token=token,
        requested_by_user_id=None if requester is None else requester.id,
        tool_name="whm_list_accounts",
        column_labels=COLUMN_LABELS,
        rows=ROWS,
        total_rows=len(ROWS),
        truncated=False,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


async def test_postgres_refuses_a_second_row_under_one_token(session: AsyncSession) -> None:
    """The unique constraint, as the migration built it rather than as metadata declares it."""
    operator = await add_operator(session)
    session.add(parked_table(token="shared-token", requester=operator))
    await session.commit()

    session.add(parked_table(token="shared-token", requester=operator))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


async def test_two_tokens_are_two_rows(session: AsyncSession) -> None:
    """The negative control (V87).

    "Postgres refuses a second row under one token" is a claim about nothing if the table
    refuses every second row — which is exactly what a unique constraint on the wrong column
    would produce.
    """
    operator = await add_operator(session)
    session.add(parked_table(token="first-token", requester=operator))
    session.add(parked_table(token="second-token", requester=operator))
    await session.commit()

    count = await session.scalar(sa.select(sa.func.count()).select_from(ToolResultTable))
    assert count == 2


async def test_deleting_the_operator_leaves_the_table_owned_by_nobody(
    session: AsyncSession,
) -> None:
    """V27, fail-closed: `SET NULL` proven by a real `DELETE`, not by reading `ondelete`.

    The row survives — deleting an operator does not erase what NOA did — and it stops
    matching the id it used to belong to, which is what makes an abandoned table unreadable
    rather than world-readable.
    """
    operator = await add_operator(session)
    session.add(parked_table(token="orphan-token", requester=operator))
    await session.commit()

    await session.delete(operator)
    await session.commit()

    row = await session.scalar(
        sa.select(ToolResultTable).where(ToolResultTable.token == "orphan-token")
    )
    assert row is not None
    assert row.requested_by_user_id is None


@pytest.mark.parametrize("column", ["column_labels", "rows"])
async def test_an_insert_that_omits_a_payload_column_fails(
    session: AsyncSession, column: str
) -> None:
    """No server default: an empty parked page is not a state this table can hold.

    A Core `INSERT` with the column left out, rather than an ORM instance with the attribute
    set to `None` — measured, because those are different statements and only this one asks
    the question. SQLAlchemy's JSON types serialise a Python `None` as JSON `null`, which is a
    *value* and satisfies `NOT NULL`; the first version of this test set the attribute and
    passed against a column with a server default it never had.
    """
    operator = await add_operator(session)
    values: dict[str, Any] = {
        "token": "incomplete-token",
        "requested_by_user_id": operator.id,
        "tool_name": "whm_list_accounts",
        "column_labels": COLUMN_LABELS,
        "rows": ROWS,
        "total_rows": len(ROWS),
        "truncated": False,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    del values[column]

    with pytest.raises(IntegrityError):
        await session.execute(sa.insert(ToolResultTable).values(**values))
    await session.rollback()


async def test_the_payload_round_trips_as_json_native_values(session: AsyncSession) -> None:
    """JSONB in, ordinary Python out — including the column *order*, which is part of the
    table that was rendered and would be lost by a mapping."""
    operator = await add_operator(session)
    session.add(parked_table(token="roundtrip-token", requester=operator))
    await session.commit()
    session.expunge_all()

    row = await session.scalar(
        sa.select(ToolResultTable).where(ToolResultTable.token == "roundtrip-token")
    )
    assert row is not None
    assert row.column_labels == COLUMN_LABELS
    assert row.rows == ROWS
