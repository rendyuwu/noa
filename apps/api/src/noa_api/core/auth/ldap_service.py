from __future__ import annotations

import asyncio
from dataclasses import dataclass

from noa_api.core.auth.errors import AuthConfigurationError, AuthError, AuthInvalidCredentialsError
from noa_api.core.config import Settings

try:
    from ldap3 import ALL, SUBTREE, Connection, Server
except ImportError:  # pragma: no cover
    ALL = SUBTREE = Connection = Server = None


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
        return await asyncio.to_thread(self._authenticate_sync, email, password)

    def _authenticate_sync(self, email: str, password: str) -> LdapUser:
        if Connection is None or Server is None:
            raise AuthConfigurationError("ldap3 dependency is not installed")

        try:
            server = Server(self._settings.ldap_server_uri, get_info=ALL, connect_timeout=self._settings.ldap_timeout_seconds)
            bind_connection = Connection(
                server,
                user=self._settings.ldap_bind_dn or None,
                password=self._settings.ldap_bind_password.get_secret_value() or None,
                auto_bind=True,
                receive_timeout=self._settings.ldap_timeout_seconds,
            )
            escaped_email = email.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").replace("*", "\\*")
            user_filter = self._settings.ldap_user_filter.format(email=escaped_email)
            bind_connection.search(
                search_base=self._settings.ldap_base_dn,
                search_filter=f"(&(objectClass=user){user_filter})",
                search_scope=SUBTREE,
                attributes=["displayName"],
                size_limit=1,
            )
            if not bind_connection.entries:
                raise AuthInvalidCredentialsError("Invalid credentials")

            entry = bind_connection.entries[0]
            user_dn = str(entry.entry_dn)
            display_name = str(entry.displayName.value) if "displayName" in entry else None

            user_connection = Connection(
                server,
                user=user_dn,
                password=password,
                auto_bind=True,
                receive_timeout=self._settings.ldap_timeout_seconds,
            )
            user_connection.unbind()
            bind_connection.unbind()
            return LdapUser(email=email, dn=user_dn, display_name=display_name)
        except AuthInvalidCredentialsError:
            raise
        except Exception as exc:
            raise AuthError("LDAP authentication failed") from exc
