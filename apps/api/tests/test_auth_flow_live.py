"""End-to-end `/auth` flow against a real database.

`test_auth_routes.py` proves the policy with in-memory doubles. This file proves the
*wiring*: the real `create_app`, the real lifespan, the real engine, the real
`SQLAuthRepository`, and `get_db_session`'s rollback all in one request path. Only the
directory is faked — there is no LDAP server here, and `AUTH_DEV_BYPASS_LDAP` would
accept every password and erase the failure cases the rate limiter needs.

Two properties can only be shown here, because both are about transactions:

- a first login's provisioned row survives the pending-approval 403, even though the
  request ends in an error and `get_db_session` rolls back;
- a recorded login failure survives the same rollback, without which the rate limiter counts nothing
  and every unit test still passes.

Skipped when Postgres is unreachable, like the other DB-backed files.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine

from core.auth.errors import AuthPendingApprovalError, AuthRateLimitedError
from core.db.models import ADMIN_ROLE_NAME
from noa_api import main
from noa_api.api.deps import get_ldap_service
from support.auth import (
    ADMIN_EMAIL,
    COOKIE_NAME,
    OPERATOR_EMAIL,
    OPERATOR_PASSWORD,
    FakeDirectory,
    build_settings,
)
from support.database import MUTATED_TABLES, migrated_database, truncate

SCRATCH_DB = "noa_auth_flow_test"

WRONG_PASSWORD = "not-the-password"
MAX_ATTEMPTS = 3


@pytest.fixture(scope="module")
def database_url() -> Iterator[str]:
    with migrated_database(SCRATCH_DB) as url:
        yield url


def _scalar(url: str, statement: str, **params: Any) -> Any:
    """One value, over a throwaway engine, from the test's own thread.

    Deliberately separate from the app's engine: `TestClient` runs the app on its own
    event loop, and an asyncpg connection belongs to the loop that opened it.
    """

    async def run() -> Any:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                return (await connection.execute(sa.text(statement), params)).scalar()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _execute(url: str, statement: str, **params: Any) -> None:
    async def run() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.begin() as connection:
                await connection.execute(sa.text(statement), params)
        finally:
            await engine.dispose()

    asyncio.run(run())


def _set_active(url: str, email: str, *, is_active: bool) -> None:
    _execute(
        url,
        "UPDATE users SET is_active = :is_active WHERE email = :email",
        is_active=is_active,
        email=email,
    )


@pytest.fixture
def live_client(
    database_url: str, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, FakeDirectory]]:
    """The real app against the scratch database, with only the directory faked."""
    asyncio.run(truncate(database_url, *MUTATED_TABLES))

    settings = build_settings(
        postgres_url=database_url,
        auth_bootstrap_admin_emails=[ADMIN_EMAIL],
        auth_login_rate_limit_max_attempts=MAX_ATTEMPTS,
        auth_login_rate_limit_block_seconds=600,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    directory = FakeDirectory()
    app = main.create_app()
    app.dependency_overrides[get_ldap_service] = lambda: directory

    with TestClient(app) as client:
        yield client, directory


def login(
    client: TestClient, *, email: str = OPERATOR_EMAIL, password: str = OPERATOR_PASSWORD
) -> Any:
    return client.post("/auth/login", json={"email": email, "password": password})


# --- Provisioning across a real transaction ---


def test_first_login_persists_the_provisioned_row_despite_the_403(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The row an admin has to enable is durable before the error propagates.

    `get_db_session` rolls back on the way out, so this passes only because
    `AuthService` commits before its activation gate.
    """
    client, _ = live_client

    response = login(client)

    assert response.status_code == 403
    assert response.json()["error_code"] == AuthPendingApprovalError.error_code
    assert (
        _scalar(
            database_url,
            "SELECT is_active FROM users WHERE email = :email",
            email=OPERATOR_EMAIL,
        )
        is False
    )


def test_activation_then_login_issues_a_working_session(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The whole intended path: provision, admin enables, operator signs in."""
    client, _ = live_client
    assert login(client).status_code == 403

    _set_active(database_url, OPERATOR_EMAIL, is_active=True)

    logged_in = login(client)
    assert logged_in.status_code == 200
    assert COOKIE_NAME in client.cookies

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == OPERATOR_EMAIL


def test_bootstrap_admin_gets_the_admin_role_row(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The bootstrap-admin break-glass, verified as actual `roles` / `user_roles` rows."""
    client, _ = live_client

    response = login(client, email=ADMIN_EMAIL)

    assert response.status_code == 200
    assert response.json()["user"]["roles"] == [ADMIN_ROLE_NAME]
    assigned = _scalar(
        database_url,
        "SELECT count(*) FROM user_roles ur "
        "JOIN roles r ON r.id = ur.role_id "
        "JOIN users u ON u.id = ur.user_id "
        "WHERE u.email = :email AND r.name = :role",
        email=ADMIN_EMAIL,
        role=ADMIN_ROLE_NAME,
    )
    assert assigned == 1


def test_repeat_logins_do_not_duplicate_the_user_or_the_role(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """Every login re-provisions and re-assigns; unique constraints never get tripped."""
    client, _ = live_client

    for _ in range(3):
        assert login(client, email=ADMIN_EMAIL).status_code == 200

    assert _scalar(database_url, "SELECT count(*) FROM users") == 1
    assert _scalar(database_url, "SELECT count(*) FROM user_roles") == 1


# --- The per-request re-read, against real SQL ---


def test_disabling_the_row_ends_the_live_session_on_the_next_request(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The per-request re-read, keystone: the cookie is unchanged and still valid — only
    the row moved.

    Nothing revokes the token, so this re-read is the entire mechanism.
    """
    client, _ = live_client
    login(client)
    _set_active(database_url, OPERATOR_EMAIL, is_active=True)
    assert login(client).status_code == 200
    assert client.get("/auth/me").status_code == 200

    _set_active(database_url, OPERATOR_EMAIL, is_active=False)

    blocked = client.get("/auth/me")
    assert blocked.status_code == 403
    assert blocked.json()["error_code"] == AuthPendingApprovalError.error_code


def test_deleting_the_row_invalidates_the_session(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """A signed cookie for a deleted operator is not a session (401, never 500)."""
    client, _ = live_client
    login(client)
    _set_active(database_url, OPERATOR_EMAIL, is_active=True)
    login(client)

    _execute(database_url, "DELETE FROM users WHERE email = :email", email=OPERATOR_EMAIL)

    assert client.get("/auth/me").status_code == 401


# --- Rate-limit counters across a real rollback ---


def test_recorded_failures_persist_and_block_across_requests(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The defect this guards: a counter written but never committed.

    Each failed login ends in an exception, and `get_db_session` rolls its session back.
    Without the explicit commit in `AuthService`, `login_rate_limits` stays empty, no
    block ever forms, and every in-memory test still passes.
    """
    client, _ = live_client
    _execute(
        database_url,
        "INSERT INTO users (email, is_active) VALUES (:email, true)",
        email=OPERATOR_EMAIL,
    )

    for _ in range(MAX_ATTEMPTS):
        assert login(client, password=WRONG_PASSWORD).status_code == 401

    assert _scalar(database_url, "SELECT count(*) FROM login_rate_limits") == 2

    blocked = login(client, password=WRONG_PASSWORD)
    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == AuthRateLimitedError.error_code
    assert int(blocked.headers["retry-after"]) > 0


def test_successful_login_clears_the_persisted_counters(
    live_client: tuple[TestClient, FakeDirectory], database_url: str
) -> None:
    """The rate limiter counts guesses, so a proven operator leaves no bucket behind."""
    client, _ = live_client
    _execute(
        database_url,
        "INSERT INTO users (email, is_active) VALUES (:email, true)",
        email=OPERATOR_EMAIL,
    )
    assert login(client, password=WRONG_PASSWORD).status_code == 401
    assert _scalar(database_url, "SELECT count(*) FROM login_rate_limits") == 2

    assert login(client).status_code == 200

    assert _scalar(database_url, "SELECT count(*) FROM login_rate_limits") == 0


def test_logout_needs_no_database_round_trip(
    live_client: tuple[TestClient, FakeDirectory],
) -> None:
    """Idempotent, unauthenticated, and no session dependency to fail on."""
    client, _ = live_client

    first = client.post("/auth/logout")
    second = client.post("/auth/logout")

    assert first.status_code == second.status_code == 204
