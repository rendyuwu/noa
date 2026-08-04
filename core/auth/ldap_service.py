"""LDAP directory access (T6, C4, I.ext).

Ported from `noa-old` branch `MCP` (`core/auth/ldap_service.py`, C13) with two
additions this repo needs:

- `user_exists_and_enabled(email)` — service-account bind + search, no operator
  password. Drives MCP token revalidation (V4). Directory unreachable raises
  `LdapUnavailableError` so callers fail closed, and it is a *distinct* outcome
  from "user is gone": the latter cascade-revokes tokens, the former must not.
- an injectable `connect` factory, so tests exercise bind/search/error paths
  without a live directory.

LDAP is the source of truth for employment, not merely for login (C4). This module
owns exactly one of the two gates that deny a login:

- employment, here: no directory entry → `AuthInvalidCredentialsError`; entry
  present but flagged disabled → `AuthAccountDisabledError`. No NOA admin action
  overrides either.
- NOA activation, not here: `users.is_active` starts False for every new LDAP user
  and an admin enables it (V7). T8 owns that check and raises
  `AuthPendingApprovalError`. `LDAPService` has no access to the row and no
  opinion about it.

Two binds per authentication, as in `noa-old`: the service account searches for
the DN (operators log in with an email, and DN layout is not derivable from it),
then a second connection binds as that DN with the supplied password. The search
connection is never re-bound as the user — a failed user bind would otherwise
leave a service-account connection in an unknown bind state.

The disabled check runs *after* the user bind, never before: answering "that
account is disabled" to an unauthenticated caller would leak directory state to
anyone who can guess an address. AD normally refuses to bind a disabled account on
its own, so this check is usually redundant — it exists because C4 puts the
employment decision in NOA's code rather than in a directory behaviour NOA does
not control.

`python-ldap` is synchronous C, so every call runs in `asyncio.to_thread`. Filter
inputs pass through `ldap.filter.escape_filter_chars` — an operator-supplied email
reaches a filter string, so injection is a real path (`*)(uid=*` otherwise matches
every entry).

Passwords (V8): never logged, never returned, never placed in a message. On the
credential path the raw `ldap` exception is dropped rather than chained (`from
None`), so a directory that echoes the attempted bind back in its diagnostic
cannot reach a traceback through `__cause__`. Non-credential paths keep the cause
— that detail is network/config, and losing it would make misconfigurations
undebuggable.

Every raise here passes *internal* detail (the `DETAIL_*` constants). The text an
operator reads lives on the exception class in `core.auth.errors` as `message`, so
a handler renders `err.message` + `err.error_code` and these diagnostics stay in
the logs. Each distinguishable outcome has its own class, except wrong-password and
no-such-account, which deliberately share one — see `AuthInvalidCredentialsError`.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Protocol

from core.auth.errors import (
    AuthAccountDisabledError,
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
    LdapUnavailableError,
)
from core.config import Settings

try:  # pragma: no cover - import guard, exercised by the ldap-absent test
    import ldap
    import ldap.filter

    LDAP_AVAILABLE = True
except ImportError:  # pragma: no cover - only on hosts without python-ldap
    ldap = None  # type: ignore[assignment]
    LDAP_AVAILABLE = False

# Active Directory `userAccountControl` bit 0x2 = ACCOUNTDISABLE. Absent attribute
# (OpenLDAP and friends) means "no disable flag here" -> existence = enabled.
AD_ACCOUNTDISABLE_FLAG = 0x2

# As in `noa-old`: the filter is ANDed with this class. Not configurable — the
# directory is fixed infrastructure (I.ext), and a knob here would be one more
# way to misconfigure authentication for zero operational gain.
USER_OBJECT_CLASS = "user"

DISPLAY_NAME_ATTRIBUTE = "displayName"
ACCOUNT_CONTROL_ATTRIBUTE = "userAccountControl"

SEARCH_ATTRIBUTES = [DISPLAY_NAME_ATTRIBUTE, ACCOUNT_CONTROL_ATTRIBUTE]

# Internal diagnostics for the `detail` slot: logs only, never a response body.
# Operator-facing text lives on the exception classes in `core.auth.errors`, so a
# handler renders `err.message` and these strings stay out of the browser.
DETAIL_BLANK_INPUT = "blank email or password; ⊥ bind attempted"
DETAIL_NO_ENTRY = "no directory entry matched the configured filter"
DETAIL_BIND_REJECTED = "directory rejected the user bind"
DETAIL_SERVICE_BIND_REJECTED = "directory rejected the service-account bind (check LDAP_BIND_DN)"
DETAIL_LDAP_MISSING = "python-ldap dependency is not installed"
DETAIL_UNREACHABLE = "LDAP directory is unreachable (connect/timeout)"
DETAIL_OPERATION_FAILED = "LDAP operation failed"


@dataclass(frozen=True)
class LdapUser:
    """Directory facts about one operator. Carries no credential."""

    email: str
    dn: str
    display_name: str | None
    is_enabled: bool = True


class LdapConnection(Protocol):
    """The slice of `ldap.ldapobject.LDAPObject` this service uses.

    `who`/`cred` default to `None` upstream (an anonymous bind), so they are typed
    optional here to match `SimpleLDAPObject.simple_bind_s`.
    """

    def set_option(self, option: int, value: object) -> None: ...

    def simple_bind_s(self, who: str | None = ..., cred: str | None = ...) -> Any: ...

    def search_s(
        self, base: str, scope: int, filterstr: str = ..., attrlist: list[str] | None = ...
    ) -> Any: ...

    def unbind_s(self) -> Any: ...


class ConnectFactory(Protocol):
    """Builds a configured, unbound connection to `uri`."""

    def __call__(self, uri: str, *, timeout_seconds: int) -> LdapConnection: ...


def _default_connect(uri: str, *, timeout_seconds: int) -> LdapConnection:
    """Open an LDAP connection with protocol/referral/timeout options set.

    Referrals off: AD returns referrals that `search_s` would chase into an
    unauthenticated rebind, which then fails in a way that looks like "user not
    found". Timeouts bounded so a hung directory cannot pin a request thread.
    """
    if ldap is None:  # pragma: no cover - guarded by callers
        raise AuthConfigurationError(DETAIL_LDAP_MISSING)

    connection = ldap.initialize(uri)
    connection.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
    connection.set_option(ldap.OPT_REFERRALS, 0)
    connection.set_option(ldap.OPT_NETWORK_TIMEOUT, timeout_seconds)
    connection.set_option(ldap.OPT_TIMEOUT, timeout_seconds)
    return connection


class LDAPService:
    """Directory queries for login (T8) and token revalidation (T11)."""

    def __init__(self, settings: Settings, *, connect: ConnectFactory | None = None) -> None:
        self._settings = settings
        self._connect = connect or _default_connect

    # --- Public API ---

    async def authenticate(self, email: str, password: str) -> LdapUser:
        """Verify `password` for `email`; return directory facts.

        Every returned `LdapUser` is employed: unknown user and wrong password both
        raise `AuthInvalidCredentialsError`, and a directory-disabled account raises
        `AuthAccountDisabledError` (C4). `LdapUnavailableError` when the directory is
        unreachable — a caller must not read that as "credentials rejected".

        NOA-side activation is a separate gate the caller still owes: a fresh LDAP
        user authenticates here and is still `is_active=False` until an admin
        enables the row (V7, T8).
        """
        normalized_email = self._normalize_email(email)
        if not normalized_email or not password:
            raise AuthInvalidCredentialsError(DETAIL_BLANK_INPUT)

        if self._settings.auth_dev_bypass_ldap:
            return self._dev_bypass_user(normalized_email)

        return await asyncio.to_thread(self._authenticate_sync, normalized_email, password)

    async def user_exists_and_enabled(self, email: str) -> bool:
        """True ⟺ directory has `email` and it is not disabled (C4, V4).

        Service-account bind + search only — no operator password, so this is
        callable on a background revalidation path. Unreachable directory raises
        rather than returning False: callers deny the request (fail closed) yet
        must not cascade-revoke tokens on a network blip.
        """
        normalized_email = self._normalize_email(email)
        if not normalized_email:
            return False

        if self._settings.auth_dev_bypass_ldap:
            return True

        return await asyncio.to_thread(self._user_exists_and_enabled_sync, normalized_email)

    # --- Sync internals (run in a worker thread) ---

    def _authenticate_sync(self, email: str, password: str) -> LdapUser:
        self._require_ldap()

        connection = self._service_connection()
        user_connection: LdapConnection | None = None
        try:
            found = self._search_user(connection, email)
            if found is None:
                raise AuthInvalidCredentialsError(DETAIL_NO_ENTRY)

            user_connection = self._connect(
                self._settings.ldap_server_uri,
                timeout_seconds=self._settings.ldap_timeout_seconds,
            )
            try:
                user_connection.simple_bind_s(found.dn, password)
            except ldap.INVALID_CREDENTIALS:
                # `from None`: the cause could quote the attempted bind (V8).
                raise AuthInvalidCredentialsError(DETAIL_BIND_REJECTED) from None

            # Post-bind only: pre-bind, this would answer "disabled?" to anyone
            # able to guess an address.
            if not found.is_enabled:
                raise AuthAccountDisabledError(
                    f"{ACCOUNT_CONTROL_ATTRIBUTE} has ACCOUNTDISABLE set for {found.dn}"
                )

            return found
        except AuthError:
            raise
        except Exception as exc:  # Classified below; raw detail never leaks (V8).
            raise self._classify(exc) from exc
        finally:
            self._close(user_connection)
            self._close(connection)

    def _find_user_sync(self, email: str) -> LdapUser | None:
        """Service-account search for `email`. None when the directory has no entry.

        Password-free, so it serves background revalidation (V4). Not public: a
        caller holding directory facts without a verified bind is one refactor away
        from treating "exists" as "authenticated".
        """
        self._require_ldap()

        connection = self._service_connection()
        try:
            return self._search_user(connection, email)
        except AuthError:
            raise
        except Exception as exc:  # Classified below; raw detail never leaks (V8).
            raise self._classify(exc) from exc
        finally:
            self._close(connection)

    def _user_exists_and_enabled_sync(self, email: str) -> bool:
        found = self._find_user_sync(email)
        return found is not None and found.is_enabled

    def _service_connection(self) -> LdapConnection:
        """Connect and bind as the service account (anonymous when unconfigured).

        A rejected service bind is NOA's misconfiguration, not the operator's, so
        it surfaces as `AuthConfigurationError` — reporting "invalid credentials"
        here would send operators chasing their own passwords.
        """
        connection = self._connect(
            self._settings.ldap_server_uri,
            timeout_seconds=self._settings.ldap_timeout_seconds,
        )

        bind_dn = self._settings.ldap_bind_dn.strip()
        try:
            if bind_dn:
                connection.simple_bind_s(bind_dn, self._settings.ldap_bind_password_value)
            else:
                connection.simple_bind_s()
        except ldap.INVALID_CREDENTIALS:
            self._close(connection)
            # `from None`: the cause could quote the service credential (V8).
            raise AuthConfigurationError(DETAIL_SERVICE_BIND_REJECTED) from None
        except Exception as exc:  # Classified below; raw detail never leaks (V8).
            self._close(connection)
            raise self._classify(exc) from exc

        return connection

    def _search_user(self, connection: LdapConnection, email: str) -> LdapUser | None:
        """Subtree search under the base DN. First entry wins; None when no match."""
        results = connection.search_s(
            self._settings.ldap_base_dn,
            ldap.SCOPE_SUBTREE,
            self._build_filter(email),
            SEARCH_ATTRIBUTES,
        )

        for entry in results or ():
            dn, attributes = entry
            # AD returns referral entries as `(None, [...])`; skip, don't fail.
            if not dn:
                continue
            return LdapUser(
                email=email,
                dn=str(dn),
                display_name=_decode_attribute(attributes, DISPLAY_NAME_ATTRIBUTE),
                is_enabled=_is_account_enabled(attributes),
            )

        return None

    def _build_filter(self, email: str) -> str:
        """Escape the email, then substitute into the configured filter.

        Every `format` failure is a config fault, so all of them raise
        `AuthConfigurationError`. `ValueError` belongs in that set: a stray
        conversion like `{email!x}` raises it, and classifying that as a generic
        `AuthError` would read to the operator as a transient directory fault and
        send them retrying instead of fixing `LDAP_USER_FILTER`.
        """
        escaped_email = ldap.filter.escape_filter_chars(email)
        try:
            user_filter = self._settings.ldap_user_filter.format(email=escaped_email)
        except (IndexError, KeyError, ValueError) as exc:
            raise AuthConfigurationError(
                "ldap_user_filter must contain only the `{email}` placeholder"
            ) from exc

        return f"(&(objectClass={USER_OBJECT_CLASS}){user_filter})"

    # --- Helpers ---

    @staticmethod
    def _normalize_email(email: str) -> str:
        return email.strip().lower()

    def _dev_bypass_user(self, email: str) -> LdapUser:
        """Local-dev identity, no directory involved (C4).

        Reachable only in development/test: `core.config` rejects the flag
        elsewhere, so this cannot become a production authentication path.
        """
        base_dn = self._settings.ldap_base_dn.strip()
        dn = f"CN={email},OU=DevBypass,{base_dn}" if base_dn else f"CN={email},OU=DevBypass"
        return LdapUser(email=email, dn=dn, display_name=email, is_enabled=True)

    def _require_ldap(self) -> None:
        if not LDAP_AVAILABLE:
            raise AuthConfigurationError(DETAIL_LDAP_MISSING)

    @staticmethod
    def _classify(exc: Exception) -> AuthError:
        """Map a raw `ldap` exception onto NOA's taxonomy.

        Unreachable/timed-out directory becomes `LdapUnavailableError` (V4 fail
        closed, no cascade revoke). Anything else is a configuration or protocol
        fault. Either way the raw exception never reaches a caller's message.
        """
        if ldap is not None and isinstance(
            exc, (ldap.SERVER_DOWN, ldap.TIMEOUT, ldap.TIMELIMIT_EXCEEDED, ldap.CONNECT_ERROR)
        ):
            return LdapUnavailableError(DETAIL_UNREACHABLE)
        return AuthError(DETAIL_OPERATION_FAILED)

    @staticmethod
    def _close(connection: LdapConnection | None) -> None:
        if connection is None:
            return
        with contextlib.suppress(Exception):
            connection.unbind_s()


def _decode_attribute(attributes: Any, key: str) -> str | None:
    """First value of `key` as `str`. LDAP hands back `bytes`."""
    if not attributes:
        return None

    values = attributes.get(key) if hasattr(attributes, "get") else None
    if not values:
        return None

    value = values[0]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_account_enabled(attributes: Any) -> bool:
    """Read AD's ACCOUNTDISABLE bit. Attribute absent or unparsable → enabled.

    Defaulting to enabled keeps non-AD directories working, where existence in the
    directory is the only employment signal available. Deactivation there is an
    entry removal, which `_search_user` already reports as "not found".
    """
    raw = _decode_attribute(attributes, ACCOUNT_CONTROL_ATTRIBUTE)
    if raw is None:
        return True

    try:
        flags = int(raw)
    except ValueError:
        return True

    return not flags & AD_ACCOUNTDISABLE_FLAG


__all__ = [
    "AD_ACCOUNTDISABLE_FLAG",
    "LDAP_AVAILABLE",
    "USER_OBJECT_CLASS",
    "LDAPService",
    "LdapUser",
]
