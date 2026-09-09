"""`/auth` route guards (T8, V6, V7, V8, V9, V79).

No Postgres: `support.auth.auth_harness` swaps the repository and the rate-limit store
for in-memory doubles and leaves everything else — router, error handler, `JWTService`,
`AuthService`, `LoginRateLimiter` — as production code. See that module for why.

Cookie assertions parse the real `Set-Cookie` header rather than trusting call
arguments: V6 is a statement about what the browser receives.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from http.cookies import SimpleCookie
from uuid import uuid4

import jwt
import pytest
from httpx import Response as HttpxResponse

from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    AuthRateLimitedError,
    AuthSessionExpiredError,
    AuthSessionInvalidError,
    LdapUnavailableError,
)
from core.auth.jwt_service import (
    CLAIM_EMAIL,
    CLAIM_EXPIRES_AT,
    CLAIM_ISSUED_AT,
    CLAIM_USER_ID,
)
from core.auth.login_rate_limiter import SCOPE_EMAIL, SCOPE_IP
from core.db.models import ADMIN_ROLE_NAME
from noa_api.api.errors import FALLBACK_STATUS, error_body, status_for
from support.auth import (
    ADMIN_EMAIL,
    COOKIE_NAME,
    JWT_SECRET,
    OPERATOR_DISPLAY_NAME,
    OPERATOR_EMAIL,
    OPERATOR_PASSWORD,
    FakeAuthRepository,
    FakeDirectory,
    auth_harness,
    build_settings,
)
from support.cookies import cookie_shape

WRONG_PASSWORD = "not-the-password"


def set_cookie_header(response: HttpxResponse) -> str:
    """The single `Set-Cookie` header the browser would receive."""
    headers = response.headers.get_list("set-cookie")
    assert len(headers) == 1, f"expected exactly one Set-Cookie, got {headers}"
    return headers[0]


def parse_cookie(response: HttpxResponse) -> SimpleCookie:
    cookie: SimpleCookie = SimpleCookie()
    cookie.load(set_cookie_header(response))
    return cookie


def encode_session(**claim_overrides: object) -> str:
    """A token signed with the harness secret, so only the claims under test differ."""
    now = datetime.now(UTC)
    payload: dict[str, object] = {
        CLAIM_EMAIL: OPERATOR_EMAIL,
        CLAIM_USER_ID: str(uuid4()),
        CLAIM_ISSUED_AT: now,
        CLAIM_EXPIRES_AT: now + timedelta(hours=1),
    }
    payload.update(claim_overrides)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


# --- Login: success + cookie (V6) ---


def test_login_sets_httponly_session_cookie_with_v6_attributes() -> None:
    """V6: httpOnly, SameSite=Lax, `Path=/`, Max-Age matching the token TTL.

    `Domain` is not asserted here — the harness unsets it so `TestClient` will store the
    cookie at all. The `.noa.internal` scope (V40) is covered in `test_jwt_service.py`,
    which reads the header without a client in the way.
    """
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(
        settings=build_settings(auth_jwt_access_token_ttl_seconds=1800),
        repository=repository,
    ) as harness:
        response = harness.login()

        assert response.status_code == 200
        morsel = parse_cookie(response)[COOKIE_NAME]
        assert morsel["httponly"] is True
        assert morsel["samesite"].lower() == "lax"
        assert morsel["path"] == "/"
        assert morsel["max-age"] == "1800"


def test_login_returns_the_user_and_their_roles() -> None:
    """Body shape is `{user: {...}}` for both `/auth/login` and `/auth/me`."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL, roles=("operators",))

    with auth_harness(repository=repository) as harness:
        body = harness.login().json()

    assert body["user"]["email"] == OPERATOR_EMAIL
    assert body["user"]["is_active"] is True
    assert body["user"]["roles"] == ["operators"]


def test_login_refreshes_directory_attributes_on_the_existing_row() -> None:
    """LDAP is the source of truth for the display name and DN (C4)."""
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)
    user.display_name = "Stale Name"

    with auth_harness(repository=repository) as harness:
        harness.login()

    assert repository.users[user.id].display_name == OPERATOR_DISPLAY_NAME
    assert repository.users[user.id].ldap_dn is not None


def test_login_normalizes_the_submitted_email() -> None:
    """One row per operator: `Operator@Example.COM` is not a second account."""
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        response = harness.login(email="  Operator@Example.COM  ")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user.id)
    assert len(repository.users) == 1


def test_login_records_last_login_at() -> None:
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.login()

    assert repository.users[user.id].last_login_at is not None


# --- Login: V7 activation gate ---


def test_first_login_provisions_inactive_user_and_returns_403_pending_approval() -> None:
    """V7: a new LDAP user lands `is_active=False` and waits for an admin."""
    repository = FakeAuthRepository()

    with auth_harness(repository=repository) as harness:
        response = harness.login()

    assert response.status_code == 403
    assert response.json()["error_code"] == AuthPendingApprovalError.error_code

    provisioned = repository.committed_user_by_email(OPERATOR_EMAIL)
    assert provisioned is not None
    assert provisioned.is_active is False


def test_pending_approval_still_commits_the_provisioned_row() -> None:
    """The row an admin has to enable must survive the 403 (V7).

    `noa-old` needed its session dependency to sniff the exception type for this;
    `AuthService` commits before the activation gate instead. Asserting on the
    *committed* snapshot is what makes the difference visible — a double that only
    mutates a dict passes either way.
    """
    repository = FakeAuthRepository()

    with auth_harness(repository=repository) as harness:
        harness.login()

    assert repository.commits >= 1
    assert repository.committed_user_by_email(OPERATOR_EMAIL) is not None


def test_first_login_issues_no_cookie() -> None:
    """A pending operator has no session: nothing to put in a cookie."""
    with auth_harness(repository=FakeAuthRepository()) as harness:
        response = harness.login()

    assert response.status_code == 403
    assert response.headers.get_list("set-cookie") == []


def test_second_login_after_activation_succeeds() -> None:
    """The admin's activation is all that stands between the two attempts."""
    repository = FakeAuthRepository()

    with auth_harness(repository=repository) as harness:
        assert harness.login().status_code == 403

        provisioned = repository.committed_user_by_email(OPERATOR_EMAIL)
        assert provisioned is not None
        repository.users[provisioned.id].is_active = True

        assert harness.login().status_code == 200


def test_bootstrap_admin_activated_with_admin_role_on_first_login() -> None:
    """V7: without this, a fresh deployment has nobody able to activate anybody."""
    repository = FakeAuthRepository()

    with auth_harness(
        settings=build_settings(auth_bootstrap_admin_emails=[ADMIN_EMAIL]),
        repository=repository,
    ) as harness:
        response = harness.login(email=ADMIN_EMAIL)

    assert response.status_code == 200
    assert response.json()["user"]["roles"] == [ADMIN_ROLE_NAME]
    assert response.json()["user"]["is_active"] is True


def test_bootstrap_admin_reactivated_after_being_disabled() -> None:
    """The env var is the deployment's break-glass, so it ⊥ be defeatable in-app."""
    repository = FakeAuthRepository()
    user = repository.add_active_user(ADMIN_EMAIL)
    user.is_active = False

    with auth_harness(
        settings=build_settings(auth_bootstrap_admin_emails=[ADMIN_EMAIL]),
        repository=repository,
    ) as harness:
        response = harness.login(email=ADMIN_EMAIL)

    assert response.status_code == 200
    assert repository.users[user.id].is_active is True


# --- Login: directory failures ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (None, 401),  # wrong password, raised by the fake directory itself
        (AuthAccountDisabledError("directory disabled the account"), 403),
        (LdapUnavailableError("directory unreachable"), 503),
        (AuthConfigurationError("bad LDAP_BIND_DN"), 500),
        (AuthError("unclassified"), FALLBACK_STATUS),
    ],
)
def test_directory_failures_map_to_their_status(
    error: AuthError | None, expected_status: int
) -> None:
    """One handler owns the mapping, so no route can disagree with another."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository, directory=FakeDirectory(error=error)) as harness:
        password = WRONG_PASSWORD if error is None else OPERATOR_PASSWORD
        response = harness.login(password=password)

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    "payload",
    [{"email": "", "password": OPERATOR_PASSWORD}, {"email": OPERATOR_EMAIL, "password": ""}],
)
def test_blank_credentials_rejected_without_touching_the_directory(payload: dict[str, str]) -> None:
    """An empty submit guesses nothing, so it ⊥ reach LDAP and ⊥ spend block budget."""
    directory = FakeDirectory()

    with auth_harness(directory=directory) as harness:
        response = harness.client.post("/auth/login", json=payload)

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthInvalidCredentialsError.error_code
    assert directory.calls == []


# --- V8: no credential material in a response ---


def test_login_error_response_carries_no_password_and_no_detail() -> None:
    """V8: body is `error_code` + `message` + `request_id`. `detail` names internals.

    `request_id` joined the set with T64 (V73). It is the one addition V8 admits: a
    per-request opaque id, minted by NOA, that names the log line rather than anything in it.
    """
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        response = harness.login(password=WRONG_PASSWORD)

    assert set(response.json()) == {"error_code", "message", "request_id"}
    assert WRONG_PASSWORD not in response.text


def test_login_response_carries_token_only_in_cookie() -> None:
    """V8/V6: the session token is httpOnly, so it ⊥ also appear in the body."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        response = harness.login()

    token = parse_cookie(response)[COOKIE_NAME].value
    assert token
    assert token not in response.text
    assert set(response.json()["user"]) == {"id", "email", "display_name", "is_active", "roles"}


def test_failed_login_logs_no_password(caplog: pytest.LogCaptureFixture) -> None:
    """V8: no password, and no fragment of one, reaches a log record.

    Nothing on this path logs today, so the assertion is trivially true right now. It is
    here as the tripwire for when login auditing arrives: the natural first draft of an
    `auth_login_rejected` record includes the whole request payload.
    """
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    caplog.set_level(logging.DEBUG)

    with auth_harness(repository=repository) as harness:
        harness.login(password=WRONG_PASSWORD)

    assert WRONG_PASSWORD not in caplog.text


def test_successful_login_logs_no_session_token(caplog: pytest.LogCaptureFixture) -> None:
    """V8: the minted token is a credential, so it ⊥ be logged either."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    caplog.set_level(logging.DEBUG)

    with auth_harness(repository=repository) as harness:
        response = harness.login()

    token = parse_cookie(response)[COOKIE_NAME].value
    assert token not in caplog.text
    assert OPERATOR_PASSWORD not in caplog.text


# --- V9: rate limiting ---


def test_login_blocked_after_max_failures_returns_429_with_retry_after() -> None:
    """V9: block past the configured max, and say when to come back."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    settings = build_settings(
        auth_login_rate_limit_max_attempts=3,
        auth_login_rate_limit_block_seconds=600,
    )

    with auth_harness(settings=settings, repository=repository) as harness:
        for _ in range(3):
            assert harness.login(password=WRONG_PASSWORD).status_code == 401

        blocked = harness.login(password=WRONG_PASSWORD)

    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == AuthRateLimitedError.error_code
    assert 0 < int(blocked.headers["retry-after"]) <= 600


def test_rate_limit_blocks_the_correct_password_too() -> None:
    """A block is a block: guessing right on attempt six ⊥ get you in."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    settings = build_settings(auth_login_rate_limit_max_attempts=2)

    with auth_harness(settings=settings, repository=repository) as harness:
        for _ in range(2):
            harness.login(password=WRONG_PASSWORD)

        response = harness.login()

    assert response.status_code == 429


def test_failed_login_counts_against_both_ip_and_email_scopes() -> None:
    """Either scope alone leaves a hole (see `LoginRateLimit`), so both must move."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.login(password=WRONG_PASSWORD)
        scopes = {scope for scope, _ in harness.rate_limits.buckets}

    assert scopes == {SCOPE_IP, SCOPE_EMAIL}


def test_recorded_failure_is_committed_not_left_pending() -> None:
    """A rolled-back counter is a limiter that never limits.

    The request's error path rolls its session back, so `record_failure` has to commit
    or V9 becomes decorative — the kind of defect that passes every unit test.
    """
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.login(password=WRONG_PASSWORD)

    assert repository.commits >= 1


def test_successful_login_clears_failure_counters() -> None:
    """V9 counts guesses; a proven operator is not guessing."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.login(password=WRONG_PASSWORD)
        assert harness.rate_limits.buckets

        assert harness.login().status_code == 200

        assert harness.rate_limits.buckets == {}
        assert {scope for scope, _ in harness.rate_limits.cleared} == {SCOPE_IP, SCOPE_EMAIL}


def test_directory_outage_does_not_consume_rate_limit_budget() -> None:
    """Departure from `noa-old`: an outage ⊥ amplify into a lockout.

    `noa-old` called `record_failure()` for every `AuthError` LDAP raised, so a few
    minutes of `LdapUnavailableError` counted as wrong passwords and locked every
    operator out for the full block duration.
    """
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    settings = build_settings(auth_login_rate_limit_max_attempts=2)
    directory = FakeDirectory(error=LdapUnavailableError("directory unreachable"))

    with auth_harness(settings=settings, repository=repository, directory=directory) as harness:
        for _ in range(5):
            assert harness.login().status_code == 503

        assert harness.rate_limits.buckets == {}

        # The directory comes back; nothing is blocked.
        harness.directory.error = None
        assert harness.login().status_code == 200


def test_disabled_directory_account_does_not_consume_rate_limit_budget() -> None:
    """Post-bind, so the password was right — counting it would limit nothing."""
    repository = FakeAuthRepository()
    repository.add_active_user(OPERATOR_EMAIL)
    directory = FakeDirectory(error=AuthAccountDisabledError("employment ended"))

    with auth_harness(repository=repository, directory=directory) as harness:
        assert harness.login().status_code == 403

    assert harness.rate_limits.buckets == {}


# --- /auth/me: the V6 re-read ---


def test_me_returns_the_current_user_for_a_valid_session() -> None:
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL, roles=("operators",))

    with auth_harness(repository=repository) as harness:
        harness.set_session_cookie_for(user.id, OPERATOR_EMAIL)
        response = harness.client.get("/auth/me")

    assert response.status_code == 200
    assert response.json()["user"]["id"] == str(user.id)
    assert response.json()["user"]["roles"] == ["operators"]


def test_me_rejects_when_user_disabled_after_cookie_issued() -> None:
    """V6 keystone: the session JWT has no revocation path before `exp`.

    No `jti`, no denylist, and V4's cascade revoke covers `mcp_tokens` only — so this
    per-request row re-read is the ONLY thing bounding a disabled operator's live
    session. Cache it or trust a claim and the window becomes the full token TTL.
    """
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.set_session_cookie_for(user.id, OPERATOR_EMAIL)
        assert harness.client.get("/auth/me").status_code == 200

        repository.users[user.id].is_active = False

        response = harness.client.get("/auth/me")

    assert response.status_code == 403
    assert response.json()["error_code"] == AuthPendingApprovalError.error_code


def test_me_rejects_when_user_row_deleted() -> None:
    """A correctly signed cookie for a row that no longer exists is not a session."""
    with auth_harness(repository=FakeAuthRepository()) as harness:
        harness.set_session_cookie_for(uuid4(), OPERATOR_EMAIL)
        response = harness.client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthSessionInvalidError.error_code


def test_me_requires_a_cookie() -> None:
    with auth_harness() as harness:
        response = harness.client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthSessionInvalidError.error_code


def test_me_rejects_a_foreign_signature() -> None:
    """The cookie is a credential, so its signature is checked, not just parsed."""
    forged = jwt.encode(
        {
            CLAIM_EMAIL: OPERATOR_EMAIL,
            CLAIM_USER_ID: str(uuid4()),
            CLAIM_ISSUED_AT: datetime.now(UTC),
            CLAIM_EXPIRES_AT: datetime.now(UTC) + timedelta(hours=1),
        },
        "a-different-secret-that-is-long-enough-for-hs256",
        algorithm="HS256",
    )

    with auth_harness() as harness:
        harness.client.cookies.set(COOKIE_NAME, forged)
        response = harness.client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthSessionInvalidError.error_code


# --- V79: zero clock leeway ---


def test_me_rejects_session_one_second_past_exp() -> None:
    """V79: leeway is 0, so `exp` one second ago is expired — not "close enough"."""
    now = datetime.now(UTC)
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        harness.client.cookies.set(
            COOKIE_NAME,
            encode_session(
                **{
                    CLAIM_USER_ID: str(user.id),
                    CLAIM_ISSUED_AT: now - timedelta(hours=1),
                    CLAIM_EXPIRES_AT: now - timedelta(seconds=1),
                }
            ),
        )
        response = harness.client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthSessionExpiredError.error_code


def freeze_verification_clock(monkeypatch: pytest.MonkeyPatch, moment: datetime) -> None:
    """Pin the instant PyJWT compares `iat` and `exp` against.

    NOA offers no seam to inject here and this does not add one: `JWTService.decode_token` hands
    the claim checks to `jwt.decode`, which reads the wall clock itself
    (`jwt.api_jwt._validate_claims`, at the `PyJWT==2.13.0` pinned in `apps/api/pyproject.toml`).
    So the freeze is test-local and patches the library's own name — which fails loudly on a bump
    that moves it, rather than quietly ceasing to freeze anything.

    Only the *verification* clock moves, and a caller has to mint its tokens **before** calling
    this: the patched name is the one `jwt.encode` tests its `iat`/`exp` claims against
    (`isinstance(value, datetime)`), so a `datetime` claim handed to it while the freeze is on is
    left unconverted and fails to serialise. That is a sharp edge worth keeping rather than
    designing around — it is the same fact from the other side, that the freeze reaches
    everything in the library reading that name, not only the comparison under test.
    """

    class Frozen(datetime):
        @classmethod
        def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
            return moment

    monkeypatch.setattr(jwt.api_jwt, "datetime", Frozen)


def test_me_rejects_session_with_future_dated_iat(monkeypatch: pytest.MonkeyPatch) -> None:
    """V79: `iat > now + leeway` rejects, and leeway is 0 (`PyJWT==2.13.0`, R24).

    Matters when a second API replica appears: the *minting* side breaks first, so a
    login would hand back a token its own verifier refuses.

    **The clock is frozen, and the offset is not widened** — the two are not interchangeable. One
    second is the smallest future `iat` the encoding can express, because PyJWT truncates the
    claim to whole seconds, so it is also the tightest available statement of "leeway is zero":
    any leeway of a second or more makes this token verify and this test fail. A wider offset
    would remove the same race and be rejected by a verifier with leeway too, which is the
    regression the case exists to catch.

    Unfrozen it raced the wall clock. `iat` was read a few milliseconds before the request and
    truncated down to its own second, so the token stopped being future-dated as soon as the
    clock crossed into the next one and the assertion held only when the request landed inside
    the same second (V87, B4: a compare that eats a clock-stamped byte).

    The control below is what keeps the 401 attributable. Frozen at the same instant, a token
    issued *at* it verifies — so the refusal above is the future `iat` and not the freeze, and
    not the `exp` arithmetic either.
    """
    moment = datetime.now(UTC).replace(microsecond=0)
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)
    # Minted before the freeze — see `freeze_verification_clock`, which the encoder reads too.
    future_dated = encode_session(
        **{
            CLAIM_USER_ID: str(user.id),
            CLAIM_ISSUED_AT: moment + timedelta(seconds=1),
            CLAIM_EXPIRES_AT: moment + timedelta(hours=1),
        }
    )
    issued_at_the_instant = encode_session(
        **{
            CLAIM_USER_ID: str(user.id),
            CLAIM_ISSUED_AT: moment,
            CLAIM_EXPIRES_AT: moment + timedelta(hours=1),
        }
    )

    freeze_verification_clock(monkeypatch, moment)

    with auth_harness(repository=repository) as harness:
        harness.client.cookies.set(COOKIE_NAME, future_dated)
        response = harness.client.get("/auth/me")

        harness.client.cookies.set(COOKIE_NAME, issued_at_the_instant)
        issued_now = harness.client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error_code"] == AuthSessionInvalidError.error_code
    assert issued_now.status_code == 200


# --- Logout (V6) ---


def test_logout_clears_cookie_without_authentication() -> None:
    """V6: an operator whose token already expired most needs the stale cookie gone."""
    with auth_harness() as harness:
        response = harness.client.post("/auth/logout")

    assert response.status_code == 204
    cleared = parse_cookie(response)[COOKIE_NAME]
    assert cleared["max-age"] == "0"
    assert not cleared.value


def test_logout_is_idempotent() -> None:
    """V6: a second logout is indistinguishable from the first.

    Compared through `cookie_shape`, not the raw header: `delete_cookie` stamps `Expires`
    from the clock, so two POSTs that straddle a second boundary emit different header
    strings while clearing the very same cookie (B4).
    """
    with auth_harness() as harness:
        first = harness.client.post("/auth/logout")
        second = harness.client.post("/auth/logout")

    assert first.status_code == second.status_code == 204
    assert cookie_shape(set_cookie_header(first)) == cookie_shape(set_cookie_header(second))


def test_logout_reaches_no_database() -> None:
    """No session dependency, so logout works with Postgres down.

    The harness leaves `app.state.session_factory` as `None`; a DB-touching logout would
    fail here rather than in production at the worst moment.
    """
    with auth_harness(repository=FakeAuthRepository()) as harness:
        response = harness.client.post("/auth/logout")

    assert response.status_code == 204
    assert harness.repository.commits == 0


def test_session_survives_logout_until_exp() -> None:
    """V6 states this openly: logout kills the *cookie*, ⊥ the token.

    No `jti` and no denylist means a captured copy keeps verifying. Pinned as a test so
    the deviation stays a known one rather than becoming a surprise, and so the reason
    every route re-reads `users.is_active` remains visible.
    """
    repository = FakeAuthRepository()
    user = repository.add_active_user(OPERATOR_EMAIL)

    with auth_harness(repository=repository) as harness:
        token = harness.set_session_cookie_for(user.id, OPERATOR_EMAIL)
        harness.client.post("/auth/logout")

        # A holder of the captured token replays it.
        harness.client.cookies.set(COOKIE_NAME, token)
        replayed = harness.client.get("/auth/me")

    assert replayed.status_code == 200


# --- Error handler mapping ---


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (AuthInvalidCredentialsError(), 401),
        (AuthSessionExpiredError(), 401),
        (AuthSessionInvalidError(), 401),
        (AuthPendingApprovalError(), 403),
        (AuthAccountDisabledError(), 403),
        (AuthRateLimitedError(30), 429),
        (AuthConfigurationError(), 500),
        (LdapUnavailableError(), 503),
        (AuthError(), FALLBACK_STATUS),
    ],
)
def test_status_for_every_auth_error(error: AuthError, expected_status: int) -> None:
    assert status_for(error) == expected_status


def test_status_for_unmapped_subclass_inherits_its_parent() -> None:
    """An unmapped subclass ⊥ silently become a 503."""

    class TokenLooksTamperedError(AuthSessionInvalidError):
        pass

    assert status_for(TokenLooksTamperedError()) == 401


def test_error_body_omits_internal_detail() -> None:
    """V8: `detail` can name configuration faults and directory internals."""
    error = AuthConfigurationError("directory rejected the service-account bind")

    body = error_body(error)

    assert set(body) == {"error_code", "message"}
    assert "service-account" not in body["message"]
