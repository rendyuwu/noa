"""Doubles and an app builder for the large-READ table surface.

Two halves, matching the two the production code has: the MCP path parks rows
(`FakeToolResultTableWriter`) and the HTTP path reads one back for its requester
(`FakeToolResultTableReader` plus `table_harness`).

The MCP half of the table surface parks rows and answers with an address. What a tool test cares
about is *what was parked* and *what the model was told about it* — not that Postgres accepted the
insert, which `test_result_tables_live.py` covers against a real schema.

The HTTP half mirrors `support.approval_cards`: an app with the `/tables` router, the reader
overridden with a double, and everything else — the router, the error handler, `JWTService`,
the real `AuthService` behind `require_session_user`, the real `ResultTableService` — left as
production code. The double answers `None` for a foreign, deleted-requester or expired row the
way the production `WHERE` does; whether the *statement* does is
`test_result_tables_live.py`'s claim and `test_result_table_read.py`'s, not this file's.

`FakeToolResultTableWriter` records what it was handed, in order, and counts commits
separately from stores: "the rows were written" and "the rows are durable before the address
is handed out" are two claims, and a double that collapsed them could not tell a caller that
forgot to commit from one that did.

It records the rows **as it received them**, already capped and already redacted by
`core.results.tables.park_result_table` — so a test can assert a credential never reached the
writer at all, rather than that it was removed on the way out again — the
question is what the row holds, never what it held.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from core.auth.jwt_service import JWTService
from core.config import Settings
from core.results.tables import ResultTableService, ResultTableView, TableColumn
from noa_api.api.deps import (
    get_result_table_service,
)
from noa_api.api.routes.result_tables import router as result_tables_router
from support.auth import (
    COOKIE_NAME,
    OPERATOR_EMAIL,
    FakeAuthRepository,
    FakeUserRow,
    build_settings,
    session_app,
)


@dataclass(frozen=True)
class StoredTableRecord:
    """One `store` call, exactly as the writer saw it."""

    token: str
    tool_name: str
    requested_by_user_id: UUID
    columns: list[TableColumn]
    rows: list[dict[str, Any]]
    total_rows: int
    truncated: bool
    expires_at: datetime


@dataclass
class FakeToolResultTableWriter:
    """In-memory `ToolResultTableWriter`.

    `fail_with` makes the insert raise, which is how the fail-closed path is exercised: a
    parked table that could not be written must refuse the READ rather than hand out an
    address with nothing behind it.
    """

    stored: list[StoredTableRecord] = field(default_factory=list)
    commits: int = 0
    fail_with: Exception | None = None

    async def store(
        self,
        *,
        token: str,
        tool_name: str,
        requested_by_user_id: UUID,
        columns: Sequence[TableColumn],
        rows: Sequence[Mapping[str, Any]],
        total_rows: int,
        truncated: bool,
        expires_at: datetime,
    ) -> None:
        if self.fail_with is not None:
            raise self.fail_with

        self.stored.append(
            StoredTableRecord(
                token=token,
                tool_name=tool_name,
                requested_by_user_id=requested_by_user_id,
                columns=list(columns),
                rows=[dict(row) for row in rows],
                total_rows=total_rows,
                truncated=truncated,
                expires_at=expires_at,
            )
        )

    async def commit(self) -> None:
        self.commits += 1

    @property
    def only(self) -> StoredTableRecord:
        """The single parked table, or an assertion failure naming how many there were."""
        assert len(self.stored) == 1, f"expected one parked table, got {len(self.stored)}"
        return self.stored[0]


# --------------------------------------------------------------------------------------
# The read half: the surface an operator opens
# --------------------------------------------------------------------------------------

# What a parked WHM account listing looks like. Small on purpose — the counts a test asserts
# are `total_rows` and `truncated`, and those are stored values, not the length of this list.
COLUMNS = [
    TableColumn(key="user", label="Account"),
    TableColumn(key="domain", label="Primary domain"),
]

ROWS: list[dict[str, Any]] = [
    {"user": "acmeco", "domain": "acme.example"},
    {"user": "betaco", "domain": "beta.example"},
]

READ_TOOL = "whm_list_accounts"

# The token these fixtures address a table by. A constant rather than an inline default: a
# parameter called `token` with a string literal default reads to `ruff` as a hardcoded
# credential, and it is neither — the real one is 32 random bytes minted by the writer.
DEFAULT_TOKEN = "table-token-1"

# Fixed, because nothing judges it: pinning it keeps payload equality exact. The
# *deadline* is always offset from the real clock — see `table_view`.
CREATED_AT = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)


def table_view(
    *,
    token: str = DEFAULT_TOKEN,
    tool_name: str = READ_TOOL,
    columns: list[TableColumn] | None = None,
    rows: list[dict[str, Any]] | None = None,
    total_rows: int | None = None,
    truncated: bool = False,
    expires_in_seconds: float = 3600,
    now: datetime | None = None,
) -> ResultTableView:
    """One parked table as the reader returns it.

    `total_rows` defaults to the row count, so an untruncated fixture is self-consistent; a
    test about the row cap passes a larger one with `truncated=True`, which is exactly the pair a
    capped table stores.

    A clock-relative deadline, like `support.approval_cards.card_view`: a fixed one would be in
    the past by the time the suite runs, and every live table would read as expired.
    """
    resolved_rows = ROWS if rows is None else rows
    return ResultTableView(
        token=token,
        tool_name=tool_name,
        columns=COLUMNS if columns is None else columns,
        rows=[dict(row) for row in resolved_rows],
        total_rows=len(resolved_rows) if total_rows is None else total_rows,
        truncated=truncated,
        created_at=CREATED_AT,
        expires_at=(now or datetime.now(UTC)) + timedelta(seconds=expires_in_seconds),
    )


@dataclass
class StoredTable:
    """A view plus the operator it belongs to. `None` stands for a deleted requester."""

    view: ResultTableView
    requester_user_id: UUID | None


class FakeToolResultTableReader:
    """In-memory `ToolResultTableReader`, refusing the four ways production refuses.

    Unknown token, another operator's, one whose requester was deleted, one past its deadline:
    all `None`, because the production statement carries all four predicates in one `WHERE`
    and the surface has one refusal for the lot.

    `fail` exists for the reason its approval-card twin does: a read is the one place on this
    route's path that can raise for reasons nobody predicted, and what reaches the
    operator then is the shared envelope.
    """

    def __init__(self) -> None:
        self.rows: dict[str, StoredTable] = {}
        # One entry per call, so a test can assert *which* requester was asked about — the
        # cookie's identity, never anything off the request.
        self.lookups: list[tuple[str, UUID]] = []
        self.fail: BaseException | None = None

    def add(self, view: ResultTableView, *, requester_user_id: UUID | None) -> ResultTableView:
        self.rows[view.token] = StoredTable(view=view, requester_user_id=requester_user_id)
        return view

    async def get_for_requester(
        self,
        *,
        token: str,
        requester_user_id: UUID,
        now: datetime,
    ) -> ResultTableView | None:
        self.lookups.append((token, requester_user_id))
        if self.fail is not None:
            raise self.fail

        stored = self.rows.get(token)
        if stored is None or stored.requester_user_id != requester_user_id:
            return None
        if stored.view.expires_at <= now:
            # The deadline is part of the same `WHERE` in production, so an expired row is not
            # fetched at all — the double refuses at the same moment rather than one layer up.
            return None
        return stored.view


@dataclass
class TableHarness:
    """Everything a table-route test pokes at, so assertions read off one object."""

    client: TestClient
    app: FastAPI
    settings: Settings
    jwt_service: JWTService
    repository: FakeToolResultTableReader
    auth_repository: FakeAuthRepository
    operator: FakeUserRow

    def sign_in(self, user: FakeUserRow | None = None) -> None:
        """Put a valid session cookie on the client, without going through login."""
        subject = user or self.operator
        issued = self.jwt_service.create_access_token(email=subject.email, user_id=subject.id)
        self.client.cookies.set(COOKIE_NAME, issued.token)

    def sign_out(self) -> None:
        self.client.cookies.delete(COOKIE_NAME)

    def add_operator(self, email: str) -> FakeUserRow:
        """A second active operator, for the requester-match cases."""
        return self.auth_repository.add_active_user(email)

    def add_table(self, **overrides: Any) -> ResultTableView:
        """Seed one table owned by the signed-in operator."""
        return self.add_table_for(self.operator.id, **overrides)

    def add_table_for(self, requester_user_id: UUID | None, **overrides: Any) -> ResultTableView:
        """Seed one table for a given requester. `None` = a deleted one (the FK is `SET NULL`)."""
        view = table_view(**overrides)
        self.repository.add(view, requester_user_id=requester_user_id)
        return view

    def get_table(self, token: str) -> Response:
        return self.client.get(f"/tables/{token}")

    @contextmanager
    def client_that_reports_server_errors(self) -> Iterator[TestClient]:
        """A signed-in client that returns a 500 instead of re-raising.

        Starlette's `ServerErrorMiddleware` re-raises for `TestClient` unless told not to, so
        the shape of an unhandled failure is unassertable through the default client — the
        same reason `support.approval_cards` builds one of these.
        """
        with TestClient(self.app, raise_server_exceptions=False) as client:
            issued = self.jwt_service.create_access_token(
                email=self.operator.email,
                user_id=self.operator.id,
            )
            client.cookies.set(COOKIE_NAME, issued.token)
            yield client


@contextmanager
def table_harness(*, settings: Settings | None = None) -> Iterator[TableHarness]:
    """An app with the `/tables` router, wired to an in-memory reader.

    One dependency override beyond auth: the table service. `AuthService` is overridden with
    `support.auth`'s own factory over a *real* `AuthService`, so the `users.is_active` re-read
    behind `require_session_user` is production code here and the route's 401 path is real.
    """
    resolved_settings = settings or build_settings()
    jwt_service = JWTService(resolved_settings)

    repository = FakeToolResultTableReader()
    auth_repository = FakeAuthRepository()
    operator = auth_repository.add_active_user(OPERATOR_EMAIL)

    app = session_app(
        result_tables_router,
        settings=resolved_settings,
        jwt_service=jwt_service,
        repository=auth_repository,
    )
    app.dependency_overrides[get_result_table_service] = lambda: ResultTableService(
        repository=repository
    )

    with TestClient(app) as client:
        yield TableHarness(
            client=client,
            app=app,
            settings=resolved_settings,
            jwt_service=jwt_service,
            repository=repository,
            auth_repository=auth_repository,
            operator=operator,
        )


__all__ = [
    "COLUMNS",
    "CREATED_AT",
    "READ_TOOL",
    "ROWS",
    "FakeToolResultTableReader",
    "FakeToolResultTableWriter",
    "StoredTable",
    "StoredTableRecord",
    "TableHarness",
    "table_harness",
    "table_view",
]
