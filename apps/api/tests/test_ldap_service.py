"""LDAP service guards.

No directory needed: `LDAPService` takes an injectable `connect` factory, so these
drive bind/search/error paths against a fake `LDAPObject`. `ldap.*` exception
classes and option constants come from the real `python-ldap` (a pinned dependency
per T3), so the classification under test is the real taxonomy.
"""

from __future__ import annotations

import traceback
from typing import Any

import ldap
import pytest
from pydantic import ValidationError

from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    AuthPendingApprovalError,
    LdapUnavailableError,
)
from core.auth.ldap_service import (
    ACCOUNT_CONTROL_ATTRIBUTE,
    AD_ACCOUNTDISABLE_FLAG,
    USER_OBJECT_CLASS,
    LDAPService,
    LdapUser,
    _default_connect,
)
from core.config import Settings

OPERATOR_EMAIL = "operator@example.com"
OPERATOR_DN = "CN=Operator,OU=Staff,dc=example,dc=com"
OPERATOR_PASSWORD = "operator-password"

SERVICE_DN = "svc-noa@example.com"
SERVICE_PASSWORD = "service-password"

BASE_DN = "dc=example,dc=com"

# `514` = ACCOUNTDISABLE (0x2) | NORMAL_ACCOUNT (0x200), as AD stores a disabled user.
DISABLED_ACCOUNT_CONTROL = str(AD_ACCOUNTDISABLE_FLAG | 0x200).encode()


def build_settings(**overrides: object) -> Settings:
    """Settings from explicit values only — the developer's `.env` cannot leak in."""
    defaults: dict[str, object] = {
        "environment": "test",
        "ldap_server_uri": "ldaps://ldap.example.com:636",
        "ldap_bind_dn": SERVICE_DN,
        "ldap_bind_password": SERVICE_PASSWORD,
        "ldap_base_dn": BASE_DN,
        "ldap_timeout_seconds": 3,
    }
    return Settings(_env_file=None, **{**defaults, **overrides})  # type: ignore[arg-type]


# Sentinel: `entry(account_control=ABSENT)` omits the attribute, while
# `account_control=b""` sets it to an empty value. A falsy check cannot tell those
# apart, and conflating them silently turned an "unparsable" case into a duplicate
# "absent" case in an earlier revision of this file.
ABSENT = object()


def render_traceback(error: BaseException) -> str:
    """Full traceback text, chained causes included.

    V8 assertions need this: `str(err)` alone passes even when the raw directory
    diagnostic survives on `__cause__` and surfaces the moment a handler logs with
    `exc_info=True`.
    """
    return "".join(traceback.format_exception(type(error), error, error.__traceback__))


def entry(
    dn: str | None = OPERATOR_DN,
    *,
    display_name: bytes | None = b"Operator One",
    account_control: object = ABSENT,
) -> tuple[str | None, dict[str, list[Any]]]:
    """One `search_s` result tuple, shaped as `python-ldap` returns it."""
    attributes: dict[str, list[Any]] = {}
    if display_name is not None:
        attributes["displayName"] = [display_name]
    if account_control is not ABSENT:
        attributes["userAccountControl"] = [account_control]
    return dn, attributes


class FakeConnection:
    """Records binds and searches; raises whatever the test plants."""

    def __init__(
        self,
        uri: str,
        *,
        results: list[Any] | None = None,
        bind_error: Exception | None = None,
        search_error: Exception | None = None,
    ) -> None:
        self.uri = uri
        self.results = results if results is not None else []
        self.bind_error = bind_error
        self.search_error = search_error
        self.binds: list[tuple[str | None, str | None]] = []
        self.searches: list[tuple[str, int, str, list[str] | None]] = []
        self.options: dict[int, object] = {}
        self.unbound = False

    def set_option(self, option: int, value: object) -> None:
        self.options[option] = value

    def simple_bind_s(self, who: str | None = None, cred: str | None = None) -> None:
        self.binds.append((who, cred))
        if self.bind_error is not None:
            raise self.bind_error

    def search_s(
        self,
        base: str,
        scope: int,
        filterstr: str = "(objectClass=*)",
        attrlist: list[str] | None = None,
    ) -> list[Any]:
        self.searches.append((base, scope, filterstr, attrlist))
        if self.search_error is not None:
            raise self.search_error
        return self.results

    def unbind_s(self) -> None:
        self.unbound = True


class FakeDirectory:
    """`connect` factory handing out one `FakeConnection` per call, in order.

    `LDAPService.authenticate` opens two connections: the service-account search
    connection, then the user-bind connection. Separate fakes let a test fail the
    second bind without touching the first.
    """

    def __init__(self, *connections: dict[str, Any]) -> None:
        self.plans = list(connections) or [{}]
        self.opened: list[FakeConnection] = []

    def __call__(self, uri: str, *, timeout_seconds: int) -> FakeConnection:
        plan = self.plans[min(len(self.opened), len(self.plans) - 1)]
        connection = FakeConnection(uri, **plan)
        connection.timeout_seconds = timeout_seconds  # type: ignore[attr-defined]
        self.opened.append(connection)
        return connection

    @property
    def service(self) -> FakeConnection:
        return self.opened[0]

    @property
    def user(self) -> FakeConnection:
        return self.opened[1]


def service_with(
    *connections: dict[str, Any], **setting_overrides: object
) -> tuple[LDAPService, FakeDirectory]:
    directory = FakeDirectory(*connections)
    return LDAPService(build_settings(**setting_overrides), connect=directory), directory


# --- authenticate: happy path ---


async def test_authenticate_binds_service_account_then_user() -> None:
    """C4/I.ext: service-account bind + search for the DN, then bind as the user."""
    service, directory = service_with({"results": [entry()]}, {})

    user = await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert user == LdapUser(
        email=OPERATOR_EMAIL,
        dn=OPERATOR_DN,
        display_name="Operator One",
        is_enabled=True,
    )
    assert directory.service.binds == [(SERVICE_DN, SERVICE_PASSWORD)]
    assert directory.user.binds == [(OPERATOR_DN, OPERATOR_PASSWORD)]


async def test_authenticate_uses_separate_connection_for_user_bind() -> None:
    """A failed user bind must not leave the search connection in an unknown state."""
    service, directory = service_with({"results": [entry()]}, {})

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert len(directory.opened) == 2
    assert directory.service is not directory.user


async def test_authenticate_normalizes_email() -> None:
    """Emails compare case-insensitively; normalize once, at the edge."""
    service, directory = service_with({"results": [entry()]}, {})

    user = await service.authenticate(f"  {OPERATOR_EMAIL.upper()}  ", OPERATOR_PASSWORD)

    assert user.email == OPERATOR_EMAIL
    assert OPERATOR_EMAIL in directory.service.searches[0][2]


async def test_authenticate_closes_every_connection() -> None:
    """Connections are unbound on the way out, success or failure."""
    service, directory = service_with({"results": [entry()]}, {})

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert all(connection.unbound for connection in directory.opened)


async def test_search_scoped_subtree_under_base_dn() -> None:
    service, directory = service_with({"results": [entry()]}, {})

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    base, scope, _, attributes = directory.service.searches[0]
    assert base == BASE_DN
    assert scope == ldap.SCOPE_SUBTREE
    assert attributes == ["displayName", "userAccountControl"]


async def test_referral_entries_are_skipped() -> None:
    """AD returns `(None, [...])` referrals; skip them, don't fail on them."""
    service, _ = service_with({"results": [entry(dn=None), entry()]}, {})

    user = await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert user.dn == OPERATOR_DN


async def test_missing_display_name_is_none() -> None:
    service, _ = service_with({"results": [entry(display_name=None)]}, {})

    user = await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert user.display_name is None


# --- authenticate: credential failures ---


@pytest.mark.parametrize("password", ["", None])
async def test_authenticate_rejects_empty_password_without_touching_directory(
    password: str | None,
) -> None:
    """No password means no bind attempt — an empty bind can be an anonymous bind."""
    service, directory = service_with({"results": [entry()]})

    with pytest.raises(AuthInvalidCredentialsError):
        await service.authenticate(OPERATOR_EMAIL, password or "")

    assert directory.opened == []


async def test_authenticate_rejects_blank_email_without_touching_directory() -> None:
    service, directory = service_with({"results": [entry()]})

    with pytest.raises(AuthInvalidCredentialsError):
        await service.authenticate("   ", OPERATOR_PASSWORD)

    assert directory.opened == []


async def test_unknown_user_and_wrong_password_share_one_error_shape() -> None:
    """⊥ enumeration oracle: identical class, `error_code`, and operator message.

    Asserts on what reaches the browser, not on `str(exc)`: the internal `detail`
    differs by design (next test), and only the rendered surface has to match.
    """
    unknown_service, _ = service_with({"results": []})
    with pytest.raises(AuthInvalidCredentialsError) as unknown:
        await unknown_service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    wrong_service, _ = service_with(
        {"results": [entry()]},
        {"bind_error": ldap.INVALID_CREDENTIALS("bad password")},
    )
    with pytest.raises(AuthInvalidCredentialsError) as wrong:
        await wrong_service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert type(unknown.value) is type(wrong.value)
    assert unknown.value.error_code == wrong.value.error_code
    assert unknown.value.message == wrong.value.message


async def test_unknown_user_and_wrong_password_keep_distinct_log_detail() -> None:
    """One message for operators, two diagnostics for whoever reads the logs.

    Without this, an admin debugging failed logins cannot tell a filter misconfig
    (nothing matched) from genuine bad passwords.
    """
    unknown_service, _ = service_with({"results": []})
    with pytest.raises(AuthInvalidCredentialsError) as unknown:
        await unknown_service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    wrong_service, _ = service_with(
        {"results": [entry()]},
        {"bind_error": ldap.INVALID_CREDENTIALS("bad password")},
    )
    with pytest.raises(AuthInvalidCredentialsError) as wrong:
        await wrong_service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert unknown.value.detail != wrong.value.detail
    assert unknown.value.detail not in unknown.value.message
    assert wrong.value.detail not in wrong.value.message


async def test_password_never_appears_in_error() -> None:
    """V8: credentials stay out of messages AND out of the exception chain.

    A directory that quotes the attempted bind in its diagnostic would otherwise
    reach a traceback via `__cause__` the moment any handler logs `exc_info`. The
    credential path drops the cause (`from None`) for exactly that reason.
    """
    service, _ = service_with(
        {"results": [entry()]},
        {"bind_error": ldap.INVALID_CREDENTIALS(f"rejected {OPERATOR_PASSWORD}")},
    )

    with pytest.raises(AuthInvalidCredentialsError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert OPERATOR_PASSWORD not in str(raised.value)
    assert OPERATOR_PASSWORD not in render_traceback(raised.value)


async def test_service_password_never_appears_in_error() -> None:
    """V8 covers the service credential too, on the same reasoning."""
    service, _ = service_with(
        {"bind_error": ldap.INVALID_CREDENTIALS(f"rejected {SERVICE_PASSWORD}")}
    )

    with pytest.raises(AuthConfigurationError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert SERVICE_PASSWORD not in render_traceback(raised.value)


async def test_non_credential_failures_keep_their_cause() -> None:
    """Dropping the cause everywhere would make misconfigs undebuggable.

    Network and config faults carry no credential, so the chain stays intact.
    """
    service, _ = service_with({"bind_error": ldap.SERVER_DOWN("no route to host")})

    with pytest.raises(LdapUnavailableError) as raised:
        await service.user_exists_and_enabled(OPERATOR_EMAIL)

    assert isinstance(raised.value.__cause__, ldap.SERVER_DOWN)


# --- Error classification ---


@pytest.mark.parametrize(
    "error",
    [
        ldap.SERVER_DOWN("no route"),
        ldap.TIMEOUT("timed out"),
        ldap.CONNECT_ERROR("tls failed"),
    ],
)
async def test_unreachable_directory_raises_ldap_unavailable(error: Exception) -> None:
    """V4: unreachable ≠ user gone. Callers fail closed, ⊥ cascade-revoke."""
    service, _ = service_with({"bind_error": error})

    with pytest.raises(LdapUnavailableError):
        await service.user_exists_and_enabled(OPERATOR_EMAIL)


async def test_unavailable_is_not_an_invalid_credentials_error() -> None:
    """The distinction has to survive `except AuthError` handlers upstream."""
    service, _ = service_with({"bind_error": ldap.SERVER_DOWN("no route")})

    with pytest.raises(LdapUnavailableError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert not isinstance(raised.value, AuthInvalidCredentialsError)
    assert isinstance(raised.value, AuthError)


async def test_rejected_service_bind_is_a_configuration_error() -> None:
    """NOA's misconfiguration, ⊥ the operator's password."""
    service, _ = service_with({"bind_error": ldap.INVALID_CREDENTIALS("svc rejected")})

    with pytest.raises(AuthConfigurationError):
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)


async def test_search_failure_is_sanitized() -> None:
    """V8/V19 discipline: raw directory detail never rides out on the message."""
    service, _ = service_with({"search_error": ldap.FILTER_ERROR("bad filter at offset 12")})

    with pytest.raises(AuthError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert "offset 12" not in str(raised.value)


async def test_bad_user_filter_placeholder_is_a_configuration_error() -> None:
    service, _ = service_with({"results": [entry()]}, ldap_user_filter="(mail={mail})")

    with pytest.raises(AuthConfigurationError):
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)


@pytest.mark.parametrize(
    "bad_filter",
    [
        "(mail={mail})",  # KeyError: wrong placeholder name
        "(mail={0})",  # IndexError: positional placeholder
        "(mail={email!x})",  # ValueError: bogus conversion
        "(mail={email:{}})",  # ValueError: nested format spec
    ],
)
async def test_every_malformed_filter_is_a_configuration_error(bad_filter: str) -> None:
    """A config fault must never read as a transient directory fault.

    `ValueError` cases used to fall through to `AuthError("LDAP operation failed")`,
    which tells the operator to retry when the fix is `LDAP_USER_FILTER`.
    """
    service, _ = service_with({"results": [entry()]}, ldap_user_filter=bad_filter)

    with pytest.raises(AuthConfigurationError, match="ldap_user_filter"):
        await service.user_exists_and_enabled(OPERATOR_EMAIL)


# --- Filter construction ---


async def test_filter_escapes_injection_characters() -> None:
    """An operator-supplied email reaches a filter string; `*)(uid=*` must not run."""
    service, directory = service_with({"results": []})

    with pytest.raises(AuthInvalidCredentialsError):
        await service.authenticate("evil*)(uid=*", OPERATOR_PASSWORD)

    filter_string = directory.service.searches[0][2]
    assert "evil*)(uid=*" not in filter_string
    assert r"\2a" in filter_string


async def test_filter_ands_the_user_object_class() -> None:
    service, directory = service_with({"results": [entry()]})

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert directory.service.searches[0][2].startswith(f"(&(objectClass={USER_OBJECT_CLASS})")


async def test_configured_filter_is_wrapped_not_replaced() -> None:
    """`LDAP_USER_FILTER` composes with the object class; it cannot drop it."""
    service, directory = service_with(
        {"results": [entry()]},
        ldap_user_filter="(mail={email})",
    )

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    expected = f"(&(objectClass={USER_OBJECT_CLASS})(mail={OPERATOR_EMAIL}))"
    assert directory.service.searches[0][2] == expected


# --- user_exists_and_enabled ---


async def test_user_exists_and_enabled_true_for_present_account() -> None:
    service, directory = service_with({"results": [entry()]})

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is True
    assert directory.service.binds == [(SERVICE_DN, SERVICE_PASSWORD)]


async def test_user_exists_and_enabled_needs_no_operator_password() -> None:
    """Revalidation runs in the background — only the service account binds."""
    service, directory = service_with({"results": [entry()]})

    await service.user_exists_and_enabled(OPERATOR_EMAIL)

    assert len(directory.opened) == 1
    assert [bind[0] for bind in directory.service.binds] == [SERVICE_DN]


async def test_user_exists_and_enabled_false_when_absent() -> None:
    service, _ = service_with({"results": []})

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is False


async def test_user_exists_and_enabled_false_for_disabled_ad_account() -> None:
    """AD keeps disabled accounts in the directory; the flag is the signal."""
    service, _ = service_with(
        {"results": [entry(account_control=str(AD_ACCOUNTDISABLE_FLAG | 0x200).encode())]}
    )

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is False


async def test_user_exists_and_enabled_true_for_normal_ad_account() -> None:
    service, _ = service_with({"results": [entry(account_control=b"512")]})

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is True


@pytest.mark.parametrize("raw", [b"not-a-number", b"", b"  "])
async def test_unparsable_account_control_defaults_to_enabled(raw: bytes) -> None:
    """Existence is the employment signal when the flag cannot be read.

    `b""` is a *present but empty* attribute, distinct from the absent case above.
    """
    service, _ = service_with({"results": [entry(account_control=raw)]})

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is True


async def test_absent_account_control_defaults_to_enabled() -> None:
    """Non-AD directories carry no disable flag: presence in the tree = employed."""
    service, _ = service_with({"results": [entry(account_control=ABSENT)]})

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is True


async def test_user_exists_and_enabled_false_for_blank_email() -> None:
    service, directory = service_with({"results": [entry()]})

    assert await service.user_exists_and_enabled("  ") is False
    assert directory.opened == []


async def test_anonymous_bind_when_no_service_account_configured() -> None:
    service, directory = service_with({"results": [entry()]}, ldap_bind_dn="")

    await service.user_exists_and_enabled(OPERATOR_EMAIL)

    assert directory.service.binds == [(None, None)]


# --- Operator-facing messages (the text a human actually reads) ---


ALL_AUTH_ERRORS = [
    AuthInvalidCredentialsError,
    AuthAccountDisabledError,
    AuthPendingApprovalError,
    AuthConfigurationError,
    LdapUnavailableError,
]


@pytest.mark.parametrize("error_class", ALL_AUTH_ERRORS)
def test_every_error_has_a_code_and_a_message(error_class: type[AuthError]) -> None:
    """V73 wants a machine code; a human wants prose. Both, always."""
    assert error_class.error_code
    assert error_class.error_code != AuthError.error_code
    assert error_class.message
    assert error_class.message != AuthError.message
    assert error_class.message.endswith(".")


def test_error_codes_are_unique() -> None:
    """Codes are the branch key for clients; a collision merges two outcomes."""
    codes = [error_class.error_code for error_class in ALL_AUTH_ERRORS]
    assert len(codes) == len(set(codes))


def test_distinguishable_outcomes_have_distinct_messages() -> None:
    """Wrong password, disabled, pending, misconfigured, unreachable: five texts.

    An operator has to be able to tell "fix your password" from "ask an admin" from
    "wait and retry" without reading a log.
    """
    messages = [error_class.message for error_class in ALL_AUTH_ERRORS]
    assert len(messages) == len(set(messages))


@pytest.mark.parametrize("error_class", ALL_AUTH_ERRORS)
def test_messages_carry_no_credentials_or_internals(error_class: type[AuthError]) -> None:
    """V8: the rendered text is safe by construction, ⊥ by handler discipline."""
    message = error_class.message.lower()

    for leak in (SERVICE_DN.lower(), SERVICE_PASSWORD, OPERATOR_PASSWORD, BASE_DN.lower()):
        assert leak not in message
    # Directory/config internals belong in `detail`, not in front of an operator.
    for internal in ("ldap_bind_dn", "ldap_user_filter", "objectclass", "traceback"):
        assert internal not in message


def test_message_names_who_can_fix_each_recoverable_case() -> None:
    """A message that omits the next step just generates a support ticket."""
    assert "admin" in AuthPendingApprovalError.message.lower()
    assert "it" in AuthAccountDisabledError.message.lower()
    assert "administrator" in AuthConfigurationError.message.lower()
    assert "again" in LdapUnavailableError.message.lower()


def test_misconfiguration_message_absolves_the_operator() -> None:
    """⊥ send someone hunting their own password over a NOA-side fault."""
    assert "credentials are fine" in AuthConfigurationError.message


def test_invalid_credentials_message_names_neither_half() -> None:
    """ "Email or password" — ⊥ "no such user", ⊥ "wrong password"."""
    message = AuthInvalidCredentialsError.message.lower()

    assert "email or password" in message
    for oracle in ("no such", "not found", "does not exist", "unknown user"):
        assert oracle not in message


def test_detail_defaults_to_message_and_is_separate_when_given() -> None:
    """`str(exc)` is for logs; `.message` is for people."""
    default = AuthInvalidCredentialsError()
    assert default.detail == default.message
    assert str(default) == default.message

    explicit = AuthInvalidCredentialsError("bind rejected for CN=x")
    assert explicit.detail == "bind rejected for CN=x"
    assert str(explicit) == "bind rejected for CN=x"
    # The operator-facing text is class-level, so `detail` cannot overwrite it.
    assert explicit.message == AuthInvalidCredentialsError.message


async def test_raised_errors_expose_a_renderable_message() -> None:
    """What a handler needs at the catch site: `error_code` + `message`."""
    service, _ = service_with({"results": []})

    with pytest.raises(AuthInvalidCredentialsError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert raised.value.error_code == "invalid_credentials"
    assert raised.value.message == "Email or password is incorrect."


async def test_disabled_account_message_reaches_the_caller() -> None:
    service, _ = service_with(
        {"results": [entry(account_control=DISABLED_ACCOUNT_CONTROL)]},
        {},
    )

    with pytest.raises(AuthAccountDisabledError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert raised.value.error_code == "ldap_account_disabled"
    assert "disabled in the company directory" in raised.value.message
    # The AD flag detail is diagnostic, ⊥ operator-facing.
    assert ACCOUNT_CONTROL_ATTRIBUTE in raised.value.detail
    assert ACCOUNT_CONTROL_ATTRIBUTE not in raised.value.message


async def test_unreachable_directory_message_invites_a_retry() -> None:
    """Unlike the other denials, this one usually clears on its own."""
    service, _ = service_with({"bind_error": ldap.SERVER_DOWN("no route")})

    with pytest.raises(LdapUnavailableError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert raised.value.error_code == "ldap_unavailable"
    assert "try again" in raised.value.message.lower()


async def test_service_bind_detail_points_at_the_config_without_leaking_it() -> None:
    """Admin reading logs gets the knob name; the operator gets none of it."""
    service, _ = service_with({"bind_error": ldap.INVALID_CREDENTIALS("svc rejected")})

    with pytest.raises(AuthConfigurationError) as raised:
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert "LDAP_BIND_DN" in raised.value.detail
    assert "LDAP_BIND_DN" not in raised.value.message


# --- Disabled directory accounts ---


async def test_authenticate_rejects_directory_disabled_account() -> None:
    """C4: employment ended ⇒ login denied even though the password verified."""
    service, _ = service_with(
        {"results": [entry(account_control=DISABLED_ACCOUNT_CONTROL)]},
        {},
    )

    with pytest.raises(AuthAccountDisabledError):
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)


async def test_disabled_check_runs_after_the_user_bind() -> None:
    """Pre-bind it would answer "is this account disabled?" to any anonymous caller."""
    service, directory = service_with(
        {"results": [entry(account_control=DISABLED_ACCOUNT_CONTROL)]},
        {},
    )

    with pytest.raises(AuthAccountDisabledError):
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert directory.user.binds == [(OPERATOR_DN, OPERATOR_PASSWORD)]


async def test_wrong_password_on_disabled_account_reports_invalid_credentials() -> None:
    """Bad password wins: a wrong guess ⊥ learn that the account is disabled."""
    service, _ = service_with(
        {"results": [entry(account_control=DISABLED_ACCOUNT_CONTROL)]},
        {"bind_error": ldap.INVALID_CREDENTIALS("nope")},
    )

    with pytest.raises(AuthInvalidCredentialsError):
        await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)


async def test_disabled_is_distinct_from_pending_approval() -> None:
    """Two different gates: directory employment vs NOA activation.

    Neither may be caught by a handler meaning the other — an admin enabling a NOA
    row must never resurrect an ex-employee's access.
    """
    assert not issubclass(AuthAccountDisabledError, AuthPendingApprovalError)
    assert not issubclass(AuthPendingApprovalError, AuthAccountDisabledError)
    assert AuthAccountDisabledError.error_code != AuthPendingApprovalError.error_code


async def test_ldap_service_never_raises_pending_approval() -> None:
    """V7 is NOA-side state: T8 owns it, T6 has no access to `users.is_active`."""
    service, _ = service_with({"results": [entry()]}, {})

    user = await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    # A fresh operator authenticates fine here and is still inactive in NOA.
    assert user.is_enabled is True


# --- Dev bypass ---


def build_bypass_service() -> tuple[LDAPService, FakeDirectory]:
    directory = FakeDirectory()
    settings = build_settings(auth_dev_bypass_ldap=True)
    return LDAPService(settings, connect=directory), directory


async def test_dev_bypass_authenticates_without_directory() -> None:
    service, directory = build_bypass_service()

    user = await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert user.email == OPERATOR_EMAIL
    assert user.dn == f"CN={OPERATOR_EMAIL},OU=DevBypass,{BASE_DN}"
    assert user.is_enabled is True
    assert directory.opened == []


async def test_dev_bypass_still_requires_a_password() -> None:
    """Bypass skips the directory, ⊥ the notion of supplying a credential."""
    service, _ = build_bypass_service()

    with pytest.raises(AuthInvalidCredentialsError):
        await service.authenticate(OPERATOR_EMAIL, "")


async def test_dev_bypass_reports_every_user_enabled() -> None:
    service, directory = build_bypass_service()

    assert await service.user_exists_and_enabled(OPERATOR_EMAIL) is True
    assert directory.opened == []


async def test_dev_bypass_rejected_outside_development() -> None:
    """C4: the bypass cannot become a production auth path — config blocks it.

    Pinned to `ValidationError`: a bare `Exception` match would pass on an
    unrelated `TypeError` and quietly stop testing the guard.
    """
    with pytest.raises(ValidationError, match="auth_dev_bypass_ldap"):
        build_settings(environment="production", auth_dev_bypass_ldap=True)


# --- python-ldap absent ---


async def test_missing_python_ldap_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hosts without the C extension get a clear config error, ⊥ an AttributeError."""
    monkeypatch.setattr("core.auth.ldap_service.LDAP_AVAILABLE", False)
    service, _ = service_with({"results": [entry()]})

    with pytest.raises(AuthConfigurationError, match="python-ldap"):
        await service.user_exists_and_enabled(OPERATOR_EMAIL)


# --- _default_connect: the factory that actually runs in production ---


def test_default_connect_sets_protocol_referrals_and_timeouts() -> None:
    """Every test above injects a fake, so assert on the real factory here.

    `ldap.initialize` does not dial on construction, so this stays offline. The
    options matter: referrals off (AD chases them into an anonymous rebind that
    looks like "user not found") and both timeouts bounded (a hung directory must
    not pin a worker thread indefinitely).
    """
    connection = _default_connect("ldaps://ldap.example.com:636", timeout_seconds=7)

    try:
        assert connection.get_option(ldap.OPT_PROTOCOL_VERSION) == 3
        assert connection.get_option(ldap.OPT_REFERRALS) == 0
        assert connection.get_option(ldap.OPT_NETWORK_TIMEOUT) == 7
        assert connection.get_option(ldap.OPT_TIMEOUT) == 7
    finally:
        del connection


async def test_service_passes_configured_timeout_to_the_factory() -> None:
    """`LDAP_TIMEOUT_SECONDS` reaches the connection, ⊥ silently defaulted."""
    service, directory = service_with({"results": [entry()]}, {}, ldap_timeout_seconds=11)

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert [connection.timeout_seconds for connection in directory.opened] == [11, 11]


async def test_service_connects_to_the_configured_uri() -> None:
    service, directory = service_with(
        {"results": [entry()]},
        {},
        ldap_server_uri="ldaps://directory.example.net:636",
    )

    await service.authenticate(OPERATOR_EMAIL, OPERATOR_PASSWORD)

    assert {connection.uri for connection in directory.opened} == {
        "ldaps://directory.example.net:636"
    }
