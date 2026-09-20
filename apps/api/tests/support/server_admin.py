"""Doubles for the server-inventory *write* path.

`support/servers.py` owns the rows and the read repositories the MCP tool path uses. This
module owns the one write repository and the host-key-pin repository the admin routes use, and
it is a separate module for the reason the production split exists: a read
double and a write double are different capabilities, and a test that reaches for the wrong one
should have to say so at the import.

Three decisions carry the weight of what uses this.

**The rows are real mapped instances**, built by `support/servers.py`'s factories. The
no-credential-in-a-response assertion is decided by
`WHMServer.to_safe_dict`. A hand-written row with a hand-written `to_safe_dict` would test the
hand-written one.

**The double applies the production field rules.** `apply_ssh_fields_with_pin_rule`, its sibling
and `column_values` live in `core.servers.admin_repository` and are imported here rather than
re-implemented — the clear-flag precedence and the "a moved host loses its pin" rule are exactly
what the service tests assert, and a double with its own copy would be asserting the copy.

**`commit` is counted, not simulated.** The commit-ordering boundary itself is only observable
against a live database from a second session (`test_server_admin_repository.py`). What a double
*can* show is that the service reached `commit()` at all, and that it reached it **after** its
guards — so `commits` is a counter and `writes` records the order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from core.db.models import PMGServer, ProxmoxServer, WHMServer
from core.servers.admin_repository import (
    SSH_VALUE_FIELDS,
    PMGServerCreate,
    ProxmoxServerCreate,
    WHMServerCreate,
    apply_ssh_fields,
    apply_ssh_fields_with_pin_rule,
    column_values,
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


class FakeServerAdminRepository:
    """In-memory `ServerAdminRepository` for one inventory table.

    `create_row` builds the stored row from a create spec, because `support.servers`' factories
    fill columns a spec does not carry. `host_field` is the column a moved host is detected on —
    `None` for Proxmox, which has no SSH block — matching `SQLServerAdminRepository`'s parameter.
    """

    def __init__(
        self,
        servers: Iterable[Any] = (),
        *,
        create_row: Callable[[Any], Any],
        host_field: str | None = None,
    ) -> None:
        self.servers: list[Any] = list(servers)
        self.journal = _Journal()
        self._create_row = create_row
        self._host_field = host_field

    # --- Reads ---

    async def list_servers(self) -> Sequence[Any]:
        return sorted(self.servers, key=lambda server: server.name)

    async def get_by_id(self, server_id: UUID) -> Any | None:
        return next((server for server in self.servers if server.id == server_id), None)

    async def name_taken(self, name: str, *, exclude_id: UUID | None = None) -> bool:
        """Case-insensitive, matching the `func.lower(...)` comparison in the SQL."""
        return any(
            server.name.lower() == name.lower() and server.id != exclude_id
            for server in self.servers
        )

    # --- Writes ---

    async def create(self, spec: Any) -> Any:
        self.journal.record("create")
        server = self._create_row(spec)
        if self._host_field is not None:
            # The factory fills the SSH block from its own defaults; a create stores the spec's,
            # so clear first and let the production rule write them.
            for field_name in SSH_VALUE_FIELDS:
                setattr(server, field_name, None)
            apply_ssh_fields(server, spec.ssh)
        self.servers.append(server)
        return server

    async def update(self, server_id: UUID, patch: Any) -> Any | None:
        server = await self.get_by_id(server_id)
        if server is None:
            return None
        self.journal.record("update")

        if self._host_field is not None:
            new_host = getattr(patch, self._host_field)
            apply_ssh_fields_with_pin_rule(
                server,
                host_changed=new_host is not None and new_host != getattr(server, self._host_field),
                patch=patch.ssh,
            )
        for column, value in column_values(patch, skip_none=True).items():
            setattr(server, column, value)
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


# The non-SSH columns of one create spec. The SSH block is left to `create`, which applies it
# through the production rule rather than through each factory's own defaults.


def _whm_row(spec: WHMServerCreate) -> WHMServer:
    server = whm_server(
        spec.name,
        base_url=spec.base_url,
        api_token=spec.api_token,
        api_username=spec.api_username,
        is_reseller_credential=spec.is_reseller_credential,
    )
    server.verify_ssl = spec.verify_ssl
    return server


def _proxmox_row(spec: ProxmoxServerCreate) -> ProxmoxServer:
    return proxmox_server(
        spec.name,
        base_url=spec.base_url,
        api_token_id=spec.api_token_id,
        api_token_secret=spec.api_token_secret,
        verify_ssl=spec.verify_ssl,
    )


def _pmg_row(spec: PMGServerCreate) -> PMGServer:
    return pmg_server(spec.name, ssh_host=spec.ssh_host)


def whm_admin_repository(servers: Iterable[WHMServer] = ()) -> FakeServerAdminRepository:
    return FakeServerAdminRepository(servers, create_row=_whm_row, host_field="base_url")


def proxmox_admin_repository(servers: Iterable[ProxmoxServer] = ()) -> FakeServerAdminRepository:
    """No `host_field`: Proxmox is an HTTP API and nothing else, so no SSH block and no pin."""
    return FakeServerAdminRepository(servers, create_row=_proxmox_row)


def pmg_admin_repository(servers: Iterable[PMGServer] = ()) -> FakeServerAdminRepository:
    return FakeServerAdminRepository(servers, create_row=_pmg_row, host_field="ssh_host")


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
    "FakeServerAdminRepository",
    "RecordingSessionFactory",
    "RecordingValidationService",
    "pmg_admin_repository",
    "proxmox_admin_repository",
    "whm_admin_repository",
]
