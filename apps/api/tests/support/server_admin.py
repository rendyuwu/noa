"""Doubles for the server-inventory *write* path.

`support/servers.py` owns the rows and the read repositories the MCP tool path uses. This
module owns the three write repositories and the two host-key-pin repositories the admin
routes use, and it is a separate module for the reason the production split exists: a read
double and a write double are different capabilities, and a test that reaches for the wrong one
should have to say so at the import.

Three decisions carry the weight of what uses this.

**The rows are real mapped instances**, built by `support/servers.py`'s factories. The
no-credential-in-a-response assertion is decided by
`WHMServer.to_safe_dict`. A hand-written row with a hand-written `to_safe_dict` would test the
hand-written one.

**The doubles apply the production field rules.** `apply_ssh_fields_with_pin_rule` and its sibling
live in `core.servers.admin_repository` and are imported here rather than re-implemented — the
clear-flag order and the "a moved host loses its pin" rule are exactly what the service tests
assert, and a double with its own copy would be asserting the copy.

**`commit` is counted, not simulated.** The commit-ordering boundary itself is only observable
against a live database from a second session (`test_server_admin_repository.py`). What a double
*can* show is that the service reached `commit()` at all, and that it reached it **after** its
guards — so `commits` is a counter and `writes` records the order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from uuid import UUID

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.servers.admin_repository import (
    PMGServerCreate,
    PMGServerUpdate,
    ProxmoxServerCreate,
    ProxmoxServerUpdate,
    WHMServerCreate,
    WHMServerUpdate,
    apply_ssh_fields,
    apply_ssh_fields_with_pin_rule,
)
from core.servers.errors import ServerInventoryError
from core.servers.validation import ServerValidationResult
from support.servers import CREATED_AT, pmg_server, proxmox_server, whm_server


@dataclass
class _Journal:
    """What a repository was asked to do, in order.

    An ordered list rather than counters, because what is needed is *ordering*: a
    refused write must persist nothing, so `commit` must never appear before the write it
    commits, and it must not appear at all when a guard raised.
    """

    entries: list[str] = field(default_factory=list)

    def record(self, entry: str) -> None:
        self.entries.append(entry)

    @property
    def commits(self) -> int:
        return self.entries.count("commit")


class FakeWHMServerAdminRepository:
    """In-memory `ServerAdminRepository` for `whm_servers`."""

    def __init__(self, servers: Iterable[WHMServer] = ()) -> None:
        self.servers: list[WHMServer] = list(servers)
        self.journal = _Journal()

    # --- Reads ---

    async def list_servers(self) -> Sequence[WHMServer]:
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> WHMServer | None:
        return next((server for server in self.servers if server.id == server_id), None)

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        """Case-insensitive, matching the `func.lower(...)` comparison in the SQL."""
        return any(
            server.name.lower() == name.lower() and server.id != exclude_id
            for server in self.servers
        )

    # --- Writes ---

    async def create(self, spec: WHMServerCreate) -> WHMServer:
        self.journal.record("create")
        server = whm_server(
            spec.name,
            base_url=spec.base_url,
            api_token=spec.api_token,
            ssh_username=spec.ssh.ssh_username,
            ssh_password=spec.ssh.ssh_password,
            ssh_private_key=spec.ssh.ssh_private_key,
            ssh_host_key_fingerprint=spec.ssh.ssh_host_key_fingerprint,
            is_reseller_credential=spec.is_reseller_credential,
        )
        server.api_username = spec.api_username
        server.verify_ssl = spec.verify_ssl
        server.ssh_port = spec.ssh.ssh_port
        server.ssh_private_key_passphrase = spec.ssh.ssh_private_key_passphrase
        self.servers.append(server)
        return server

    async def update(self, server_id: UUID, patch: WHMServerUpdate) -> WHMServer | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        self.journal.record("update")

        apply_ssh_fields_with_pin_rule(
            server,
            host_changed=patch.base_url is not None and patch.base_url != server.base_url,
            patch=patch.ssh,
        )
        if patch.name is not None:
            server.name = patch.name
        if patch.base_url is not None:
            server.base_url = patch.base_url
        if patch.api_username is not None:
            server.api_username = patch.api_username
        if patch.api_token is not None:
            server.api_token = patch.api_token
        if patch.verify_ssl is not None:
            server.verify_ssl = patch.verify_ssl
        if patch.is_reseller_credential is not None:
            server.is_reseller_credential = patch.is_reseller_credential
        server.updated_at = CREATED_AT
        return server

    async def delete(self, server_id: UUID) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        self.journal.record("delete")
        self.servers.remove(server)
        return True

    async def commit(self) -> None:
        self.journal.record("commit")


class FakeProxmoxServerAdminRepository:
    """In-memory `ServerAdminRepository` for `proxmox_servers`. No SSH block and no pin (I.ext)."""

    def __init__(self, servers: Iterable[ProxmoxServer] = ()) -> None:
        self.servers: list[ProxmoxServer] = list(servers)
        self.journal = _Journal()

    async def list_servers(self) -> Sequence[ProxmoxServer]:
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> ProxmoxServer | None:
        return next((server for server in self.servers if server.id == server_id), None)

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        return any(
            server.name.lower() == name.lower() and server.id != exclude_id
            for server in self.servers
        )

    async def create(self, spec: ProxmoxServerCreate) -> ProxmoxServer:
        self.journal.record("create")
        server = proxmox_server(
            spec.name,
            base_url=spec.base_url,
            api_token_id=spec.api_token_id,
            api_token_secret=spec.api_token_secret,
            verify_ssl=spec.verify_ssl,
        )
        self.servers.append(server)
        return server

    async def update(self, server_id: UUID, patch: ProxmoxServerUpdate) -> ProxmoxServer | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        self.journal.record("update")

        if patch.name is not None:
            server.name = patch.name
        if patch.base_url is not None:
            server.base_url = patch.base_url
        if patch.api_token_id is not None:
            server.api_token_id = patch.api_token_id
        if patch.api_token_secret is not None:
            server.api_token_secret = patch.api_token_secret
        if patch.verify_ssl is not None:
            server.verify_ssl = patch.verify_ssl
        server.updated_at = CREATED_AT
        return server

    async def delete(self, server_id: UUID) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        self.journal.record("delete")
        self.servers.remove(server)
        return True

    async def commit(self) -> None:
        self.journal.record("commit")


class FakePMGServerAdminRepository:
    """In-memory `ServerAdminRepository` for `pmg_servers`."""

    def __init__(self, servers: Iterable[PMGServer] = ()) -> None:
        self.servers: list[PMGServer] = list(servers)
        self.journal = _Journal()

    async def list_servers(self) -> Sequence[PMGServer]:
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> PMGServer | None:
        return next((server for server in self.servers if server.id == server_id), None)

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        return any(
            server.name.lower() == name.lower() and server.id != exclude_id
            for server in self.servers
        )

    async def create(self, spec: PMGServerCreate) -> PMGServer:
        self.journal.record("create")
        server = pmg_server(
            spec.name,
            ssh_host=spec.ssh_host,
            ssh_username=spec.ssh.ssh_username,
            ssh_password=spec.ssh.ssh_password,
            ssh_private_key=spec.ssh.ssh_private_key,
            ssh_host_key_fingerprint=spec.ssh.ssh_host_key_fingerprint,
        )
        # The factory fills the SSH block from its own defaults; re-apply the spec's so a
        # `None` in the request is stored as `None` rather than as the fixture's value.
        server.ssh_username = None
        server.ssh_port = None
        server.ssh_password = None
        server.ssh_private_key = None
        server.ssh_private_key_passphrase = None
        server.ssh_host_key_fingerprint = None
        apply_ssh_fields(server, spec.ssh)
        self.servers.append(server)
        return server

    async def update(self, server_id: UUID, patch: PMGServerUpdate) -> PMGServer | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        self.journal.record("update")

        apply_ssh_fields_with_pin_rule(
            server,
            host_changed=patch.ssh_host is not None and patch.ssh_host != server.ssh_host,
            patch=patch.ssh,
        )
        if patch.name is not None:
            server.name = patch.name
        if patch.ssh_host is not None:
            server.ssh_host = patch.ssh_host
        server.updated_at = CREATED_AT
        return server

    async def delete(self, server_id: UUID) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        self.journal.record("delete")
        self.servers.remove(server)
        return True

    async def commit(self) -> None:
        self.journal.record("commit")


class FakeHostKeyPinRepository:
    """In-memory `HostKeyPinRepository` — one row's pin, and nothing else it can touch.

    Deliberately generic over the row type, unlike the three above: production binds one
    `SQLHostKeyPinRepository` to `whm_servers` and another to `pmg_servers`, the same two
    methods over two tables. There is nothing table-specific left to get wrong, so a shared
    double is not hiding anything — the write it makes is `ssh_host_key_fingerprint = …`.

    `pins` records every value written, in order, so a test can assert that a failed probe wrote
    nothing at all rather than writing and then clearing.
    """

    def __init__(self, servers: Iterable[WHMServer | PMGServer] = ()) -> None:
        self.servers: list[WHMServer | PMGServer] = list(servers)
        self.pins: list[str | None] = []
        self.commits = 0

    async def get_by_id(self, server_id: UUID):  # type: ignore[no-untyped-def]
        return next((server for server in self.servers if server.id == server_id), None)

    async def set_host_key_fingerprint(self, server_id: UUID, fingerprint: str | None) -> bool:
        server = await self.get_by_id(server_id)
        if server is None:
            return False
        self.pins.append(fingerprint)
        server.ssh_host_key_fingerprint = fingerprint
        return True

    async def commit(self) -> None:
        self.commits += 1


@asynccontextmanager
async def _no_session() -> AsyncIterator[None]:
    """A session context manager that yields nothing and opens no connection."""
    yield None


@dataclass
class RecordingSessionFactory:
    """A `SessionFactory` that hands out nothing and counts how often it was asked.

    The validation services take a *factory* rather than a session precisely so each database
    step is its own short transaction and no connection is held across a network hop — the
    account search's rule. That shape is only assertable by counting: `opened == 1` means the
    service read its
    row and did the hops with nothing checked out; `opened == 2` means it also stored a pin.
    """

    opened: int = 0

    def __call__(self) -> AbstractAsyncContextManager[None]:
        self.opened += 1
        return _no_session()


@dataclass
class RecordingValidationService:
    """A stand-in for one of the three validation services, for the *route* tests.

    **Deliberately a stub, and the split is worth stating.** The trust-on-first-use rule is a
    property of a real key exchange, so it is asserted against a real `asyncssh` server
    (`test_server_host_key_validation.py`), and the branch structure and audit event are
    asserted over the real service with doubled probes (`test_server_admin_service.py`). What
    the route tests own is narrower: the 200-with-`ok:false` envelope, the 404 for an absent
    row, and the admin gate. Wiring the real service into an HTTP harness would add a session
    factory and a socket to tests about none of that.

    `not_found` is the production error class, passed in rather than chosen here, so the route
    still answers with the shipped code and the shipped 404 body.
    """

    result: ServerValidationResult
    not_found: type[ServerInventoryError]
    known_ids: list[UUID] = field(default_factory=list)
    calls: list[UUID] = field(default_factory=list)
    actors: list[str | None] = field(default_factory=list)

    async def validate(
        self, server_id: UUID, *, actor_email: str | None = None
    ) -> ServerValidationResult:
        if server_id not in self.known_ids:
            raise self.not_found(f"no row `{server_id}`")
        self.calls.append(server_id)
        self.actors.append(actor_email)
        return self.result


__all__ = [
    "FakeHostKeyPinRepository",
    "FakePMGServerAdminRepository",
    "FakeProxmoxServerAdminRepository",
    "FakeWHMServerAdminRepository",
    "RecordingSessionFactory",
    "RecordingValidationService",
]
