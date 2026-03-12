from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass

from noa_api.core.auth.errors import (
    AuthConfigurationError,
    AuthError,
    AuthInvalidCredentialsError,
)
from noa_api.core.config import Settings

try:
    import ldap
    import ldap.filter
except ImportError:  # pragma: no cover
    ldap = None


@dataclass
class LdapUser:
    email: str
    dn: str
    display_name: str | None


class LDAPService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def authenticate(self, email: str, password: str) -> LdapUser:
        if not password:
            raise AuthInvalidCredentialsError("Invalid credentials")

        if self._settings.auth_dev_bypass_ldap:
            base_dn = self._settings.ldap_base_dn.strip()
            dn = (
                f"CN={email},OU=DevBypass,{base_dn}"
                if base_dn
                else f"CN={email},OU=DevBypass"
            )
            return LdapUser(email=email, dn=dn, display_name=email)

        return await asyncio.to_thread(self._authenticate_sync, email, password)

    def _authenticate_sync(self, email: str, password: str) -> LdapUser:
        if ldap is None:
            raise AuthConfigurationError("python-ldap dependency is not installed")

        conn = ldap.initialize(self._settings.ldap_server_uri)
        user_conn = None
        conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
        conn.set_option(ldap.OPT_REFERRALS, 0)
        conn.set_option(ldap.OPT_NETWORK_TIMEOUT, self._settings.ldap_timeout_seconds)

        try:
            bind_dn = self._settings.ldap_bind_dn
            bind_password = self._settings.ldap_bind_password.get_secret_value()
            if bind_dn:
                conn.simple_bind_s(bind_dn, bind_password)
            else:
                conn.simple_bind_s()

            escaped_email = ldap.filter.escape_filter_chars(email)
            search_filter = f"(&(objectClass=user){self._settings.ldap_user_filter.format(email=escaped_email)})"
            results = conn.search_s(
                self._settings.ldap_base_dn,
                ldap.SCOPE_SUBTREE,
                search_filter,
                ["displayName"],
            )
            if not results:
                raise AuthInvalidCredentialsError("Invalid credentials")

            user_dn, attrs = results[0]
            if not user_dn:
                raise AuthInvalidCredentialsError("Invalid credentials")

            user_conn = ldap.initialize(self._settings.ldap_server_uri)
            user_conn.set_option(ldap.OPT_PROTOCOL_VERSION, 3)
            user_conn.set_option(ldap.OPT_REFERRALS, 0)
            user_conn.set_option(
                ldap.OPT_NETWORK_TIMEOUT, self._settings.ldap_timeout_seconds
            )
            user_conn.simple_bind_s(user_dn, password)

            return LdapUser(
                email=email,
                dn=str(user_dn),
                display_name=_decode_attr(attrs, "displayName"),
            )
        except AuthInvalidCredentialsError:
            raise
        except ldap.INVALID_CREDENTIALS as exc:
            raise AuthInvalidCredentialsError("Invalid credentials") from exc
        except Exception as exc:
            raise AuthError("LDAP authentication failed") from exc
        finally:
            if user_conn is not None:
                with contextlib.suppress(Exception):
                    user_conn.unbind_s()
            with contextlib.suppress(Exception):
                conn.unbind_s()


def _decode_attr(attributes: dict[str, list[bytes]] | None, key: str) -> str | None:
    if not attributes:
        return None
    values = attributes.get(key)
    if not values:
        return None
    value = values[0]
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)
