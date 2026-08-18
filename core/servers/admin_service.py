"""Server-inventory CRUD policy: names, encryption, audit, commit (T54, V8, V14, V100).

Three services, one per table, and they hold every rule the fifteen admin routes have. The
routes (`noa_api.api.routes.admin_servers`) resolve the actor, call one method and shape the
answer — the split `noa_api.api.routes.mcp_tokens` states one file over, and the reason is the
same: these rules are properties of the *row*, so a future CLI, fixture or bootstrap script
gets them too, not only whoever calls the HTTP path.

Four rules, and each is here rather than in a route:

1. **The name is checked before the write, case-insensitively.** `core.servers.reference`
   records why the case matters: Postgres uniqueness is case-sensitive, so `Node1` and `node1`
   can both exist while `NODE1` matches both, which is a permanent `host_ambiguous` for a
   reference an operator will keep typing (V18). The write side is the only place that can
   prevent it. The unique index stays the backstop for the exact-case race.
2. **Secrets are encrypted here and nowhere else on this path.** A service method takes
   **plaintext** in the secret fields of its spec and hands the repository a copy whose secrets
   are `enc:v1:fernet:…` (C7, V48). `core.servers.admin_repository` cannot encrypt — it has no
   cipher — so there is exactly one encryption site, and `test_server_admin_repository.py`
   asserts the stored columns rather than trusting this sentence.
3. **One audit event per mutation** (V14), carrying ids, names, hosts, ports and presence
   booleans. Never a token, never an SSH credential (V8).
4. **`commit()` is the last statement, after every guard** (V100(a)). `noa_api.api.deps`'s
   session dependency does not commit, so a service that only reached `flush()` would answer
   200 over a rollback — B10 on T9's engine and B11 on T10's tokens, and V100(d) makes finding
   one an obligation to sweep the rest. This is the third service on that dependency and it
   lands with the boundary rather than acquiring it later.

Deliberately NOT here: the reachability probe. `POST …/validate` opens a socket to somebody
else's host, and holding a pooled connection across that hop is how a slow server becomes a
database outage (T21's rule). `core.servers.validation` owns it, draws its own short sessions
from the session factory, and can write exactly one column.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any, TypeVar
from uuid import UUID

from core.audit.admin_events import (
    EVENT_PMG_SERVER_CREATED,
    EVENT_PMG_SERVER_DELETED,
    EVENT_PMG_SERVER_UPDATED,
    EVENT_PROXMOX_SERVER_CREATED,
    EVENT_PROXMOX_SERVER_DELETED,
    EVENT_PROXMOX_SERVER_UPDATED,
    EVENT_WHM_SERVER_CREATED,
    EVENT_WHM_SERVER_DELETED,
    EVENT_WHM_SERVER_UPDATED,
    AdminAuditEvent,
    AdminAuditSink,
)
from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.secrets.crypto import SecretCipher
from core.servers.admin_repository import (
    PMGServerAdminRepository,
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerAdminRepository,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    SSHCredentials,
    WHMServerAdminRepository,
    WHMServerCreate,
    WHMServerUpdate,
)
from core.servers.errors import (
    PMGServerNameExistsError,
    PMGServerNotFoundError,
    ProxmoxServerNameExistsError,
    ProxmoxServerNotFoundError,
    WHMServerNameExistsError,
    WHMServerNotFoundError,
)

SSHCredentialsT = TypeVar("SSHCredentialsT", bound=SSHCredentials)


def encrypt_ssh_credentials(values: SSHCredentialsT, *, cipher: SecretCipher) -> SSHCredentialsT:
    """Copy `values` with its three secrets encrypted, everything else untouched.

    `dataclasses.replace` keeps the concrete type, so an `SSHCredentialsPatch` stays a patch
    with its clear flags intact.

    `None` is preserved rather than encrypted, because on an update `None` means "leave the
    stored value alone" — encrypting it would turn every PATCH that does not re-send a
    password into one that wipes it.

    `ssh_host_key_fingerprint` is **not** encrypted, and that is not an oversight: a host key
    fingerprint is a public digest. The whole point of storing it is comparing it to what a
    host presents mid-handshake (V82), and `WHMServer.to_safe_dict` returns it to the panel so
    an operator can read it — a value shown in a UI is not one to hold ciphertext.
    """
    return replace(
        values,
        ssh_password=_maybe_encrypt(values.ssh_password, cipher=cipher),
        ssh_private_key=_maybe_encrypt(values.ssh_private_key, cipher=cipher),
        ssh_private_key_passphrase=_maybe_encrypt(values.ssh_private_key_passphrase, cipher=cipher),
    )


def _maybe_encrypt(plaintext: str | None, *, cipher: SecretCipher) -> str | None:
    """`cipher.encrypt_text(plaintext)`, or `None` for an absent value."""
    if plaintext is None:
        return None
    return cipher.encrypt_text(plaintext)


def _ssh_metadata(server: WHMServer | PMGServer) -> dict[str, Any]:
    """The SSH facts an audit event may carry: shape, never material (V8)."""
    return {
        "ssh_username": server.ssh_username,
        "ssh_port": server.ssh_port,
        "has_ssh_password": bool(server.ssh_password),
        "has_ssh_private_key": bool(server.ssh_private_key),
        "host_key_pinned": bool(server.ssh_host_key_fingerprint),
    }


class WHMServerAdminService:
    """List / create / update / delete for `whm_servers` (T54)."""

    def __init__(
        self,
        *,
        repository: WHMServerAdminRepository,
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._audit = audit_sink

    async def list_servers(self) -> Sequence[WHMServer]:
        """Every WHM server, ordered by name in SQL (see the read repository)."""
        return await self._repository.list_servers()

    async def create(self, spec: WHMServerCreate, *, actor_email: str | None = None) -> WHMServer:
        """Insert one server. `spec`'s secrets are **plaintext** on the way in."""
        if await self._repository.name_taken(spec.name):
            raise WHMServerNameExistsError(f"`whm_servers.name` `{spec.name}` is taken")

        server = await self._repository.create(
            replace(
                spec,
                api_token=self._cipher.encrypt_text(spec.api_token),
                ssh=encrypt_ssh_credentials(spec.ssh, cipher=self._cipher),
            )
        )
        await self._record(EVENT_WHM_SERVER_CREATED, actor_email, server)
        await self._repository.commit()
        return server

    async def update(
        self, server_id: UUID, patch: WHMServerUpdate, *, actor_email: str | None = None
    ) -> WHMServer:
        """Apply a partial edit. Absent row → 404 before the name check, not after."""
        if await self._repository.get_by_id(server_id) is None:
            raise WHMServerNotFoundError(f"no `whm_servers` row `{server_id}`")
        if patch.name is not None and await self._repository.name_taken(
            patch.name, exclude_id=server_id
        ):
            raise WHMServerNameExistsError(f"`whm_servers.name` `{patch.name}` is taken")

        server = await self._repository.update(
            server_id,
            replace(
                patch,
                api_token=(
                    self._cipher.encrypt_text(patch.api_token)
                    if patch.api_token is not None
                    else None
                ),
                ssh=encrypt_ssh_credentials(patch.ssh, cipher=self._cipher),
            ),
        )
        if server is None:
            # Reachable only if the row was deleted between the guard above and here. The
            # answer is still 404, and raising beats returning `None` from a `-> WHMServer`.
            raise WHMServerNotFoundError(f"no `whm_servers` row `{server_id}`")

        await self._record(EVENT_WHM_SERVER_UPDATED, actor_email, server)
        await self._repository.commit()
        return server

    async def delete(self, server_id: UUID, *, actor_email: str | None = None) -> None:
        """Delete one server. Its tool grants are unaffected — grants name tools, not hosts."""
        server = await self._repository.get_by_id(server_id)
        if server is None or not await self._repository.delete(server_id):
            raise WHMServerNotFoundError(f"no `whm_servers` row `{server_id}`")

        await self._record(EVENT_WHM_SERVER_DELETED, actor_email, server)
        await self._repository.commit()

    async def _record(self, event_type: str, actor_email: str | None, server: WHMServer) -> None:
        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=server.name,
                metadata={
                    "server_id": str(server.id),
                    "server_name": server.name,
                    "base_url": server.base_url,
                    "api_username": server.api_username,
                    "verify_ssl": server.verify_ssl,
                    **_ssh_metadata(server),
                },
            )
        )


class ProxmoxServerAdminService:
    """List / create / update / delete for `proxmox_servers` (T54).

    No SSH block anywhere: Proxmox is an HTTP API and nothing else (I.ext), so there is one
    secret column and no host key to pin.
    """

    def __init__(
        self,
        *,
        repository: ProxmoxServerAdminRepository,
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._audit = audit_sink

    async def list_servers(self) -> Sequence[ProxmoxServer]:
        return await self._repository.list_servers()

    async def create(
        self, spec: ProxmoxServerCreate, *, actor_email: str | None = None
    ) -> ProxmoxServer:
        """Insert one server. `spec.api_token_secret` is **plaintext** on the way in."""
        if await self._repository.name_taken(spec.name):
            raise ProxmoxServerNameExistsError(f"`proxmox_servers.name` `{spec.name}` is taken")

        server = await self._repository.create(
            replace(spec, api_token_secret=self._cipher.encrypt_text(spec.api_token_secret))
        )
        await self._record(EVENT_PROXMOX_SERVER_CREATED, actor_email, server)
        await self._repository.commit()
        return server

    async def update(
        self, server_id: UUID, patch: ProxmoxServerUpdate, *, actor_email: str | None = None
    ) -> ProxmoxServer:
        if await self._repository.get_by_id(server_id) is None:
            raise ProxmoxServerNotFoundError(f"no `proxmox_servers` row `{server_id}`")
        if patch.name is not None and await self._repository.name_taken(
            patch.name, exclude_id=server_id
        ):
            raise ProxmoxServerNameExistsError(f"`proxmox_servers.name` `{patch.name}` is taken")

        server = await self._repository.update(
            server_id,
            replace(
                patch,
                api_token_secret=(
                    self._cipher.encrypt_text(patch.api_token_secret)
                    if patch.api_token_secret is not None
                    else None
                ),
            ),
        )
        if server is None:
            raise ProxmoxServerNotFoundError(f"no `proxmox_servers` row `{server_id}`")

        await self._record(EVENT_PROXMOX_SERVER_UPDATED, actor_email, server)
        await self._repository.commit()
        return server

    async def delete(self, server_id: UUID, *, actor_email: str | None = None) -> None:
        server = await self._repository.get_by_id(server_id)
        if server is None or not await self._repository.delete(server_id):
            raise ProxmoxServerNotFoundError(f"no `proxmox_servers` row `{server_id}`")

        await self._record(EVENT_PROXMOX_SERVER_DELETED, actor_email, server)
        await self._repository.commit()

    async def _record(
        self, event_type: str, actor_email: str | None, server: ProxmoxServer
    ) -> None:
        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=server.name,
                metadata={
                    "server_id": str(server.id),
                    "server_name": server.name,
                    "base_url": server.base_url,
                    "api_token_id": server.api_token_id,
                    "verify_ssl": server.verify_ssl,
                },
            )
        )


class PMGServerAdminService:
    """List / create / update / delete for `pmg_servers` (T54).

    SSH is not optional here, unlike WHM: PMG is reached over `pmgsh` over SSH and nothing
    else (V58, I.ext), so a row with no credentials is a row no tool can use. It is still
    *creatable* — an admin filling in a host before the key arrives is a legitimate order of
    operations, and `resolve_pmg_ssh_config` answers `ssh_not_configured` in the meantime,
    which names the remedy. Refusing here would add a rule no invariant asks for; the same
    call `core.auth.mcp_token_service` makes about minting for an inactive operator.
    """

    def __init__(
        self,
        *,
        repository: PMGServerAdminRepository,
        cipher: SecretCipher,
        audit_sink: AdminAuditSink,
    ) -> None:
        self._repository = repository
        self._cipher = cipher
        self._audit = audit_sink

    async def list_servers(self) -> Sequence[PMGServer]:
        return await self._repository.list_servers()

    async def create(self, spec: PMGServerCreate, *, actor_email: str | None = None) -> PMGServer:
        """Insert one server. `spec.ssh` carries **plaintext** secrets on the way in."""
        if await self._repository.name_taken(spec.name):
            raise PMGServerNameExistsError(f"`pmg_servers.name` `{spec.name}` is taken")

        server = await self._repository.create(
            replace(spec, ssh=encrypt_ssh_credentials(spec.ssh, cipher=self._cipher))
        )
        await self._record(EVENT_PMG_SERVER_CREATED, actor_email, server)
        await self._repository.commit()
        return server

    async def update(
        self, server_id: UUID, patch: PMGServerUpdate, *, actor_email: str | None = None
    ) -> PMGServer:
        if await self._repository.get_by_id(server_id) is None:
            raise PMGServerNotFoundError(f"no `pmg_servers` row `{server_id}`")
        if patch.name is not None and await self._repository.name_taken(
            patch.name, exclude_id=server_id
        ):
            raise PMGServerNameExistsError(f"`pmg_servers.name` `{patch.name}` is taken")

        server = await self._repository.update(
            server_id,
            replace(patch, ssh=encrypt_ssh_credentials(patch.ssh, cipher=self._cipher)),
        )
        if server is None:
            raise PMGServerNotFoundError(f"no `pmg_servers` row `{server_id}`")

        await self._record(EVENT_PMG_SERVER_UPDATED, actor_email, server)
        await self._repository.commit()
        return server

    async def delete(self, server_id: UUID, *, actor_email: str | None = None) -> None:
        server = await self._repository.get_by_id(server_id)
        if server is None or not await self._repository.delete(server_id):
            raise PMGServerNotFoundError(f"no `pmg_servers` row `{server_id}`")

        await self._record(EVENT_PMG_SERVER_DELETED, actor_email, server)
        await self._repository.commit()

    async def _record(self, event_type: str, actor_email: str | None, server: PMGServer) -> None:
        await self._audit.record(
            AdminAuditEvent(
                event_type=event_type,
                actor_email=actor_email,
                target=server.name,
                metadata={
                    "server_id": str(server.id),
                    "server_name": server.name,
                    "ssh_host": server.ssh_host,
                    **_ssh_metadata(server),
                },
            )
        )


__all__ = [
    "PMGServerAdminService",
    "ProxmoxServerAdminService",
    "WHMServerAdminService",
    "encrypt_ssh_credentials",
]
