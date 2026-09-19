"""The Proxmox inventory surface the password-reset and NIC tools read against.

The third of these, after `whm_repository.py` and `pmg_repository.py`, holding the same pair
for the same reasons — a narrow row view and the repository Protocol over it, with the SQL in
`core.servers.repository` and the writes in `core.servers.admin_repository` — so the note here
is only about what differs.

**A narrower row than WHM's.** Proxmox is an HTTP API and nothing else (I.ext), so
`ProxmoxServerRowLike` carries `id`, `name` and `base_url` — the three fields reference
resolution matches on — and no SSH surface exists on the table to leave out.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar
from uuid import UUID


class ProxmoxServerRowLike(Protocol):
    """The `proxmox_servers` surface the read path needs.

    Narrower than `ProxmoxServerSecretLike` (`core.integrations.proxmox.client`) on purpose, the
    way `WHMServerRowLike` is narrower than its credential twin: reference resolution matches on
    identity and never on credentials, so a double built for a resolver test cannot accidentally
    be handed to something that would decrypt it.

    `to_safe_dict()` is required because it is the only sanctioned way to render a row outward —
    `ProxmoxServer.to_safe_dict` drops `api_token_secret` and replaces it with a presence boolean —
    identifiers render, credentials never. Requiring it here means a tool cannot serialize a row
    another way without changing this Protocol first.
    """

    id: UUID
    name: str
    base_url: str

    def to_safe_dict(self) -> dict[str, Any]: ...


# Covariant for `whm_repository`'s reason: both methods return rows and neither accepts one.
RowT_co = TypeVar("RowT_co", bound=ProxmoxServerRowLike, covariant=True)


class ProxmoxServerReadRepository(Protocol[RowT_co]):
    """What the password-reset callers need from Proxmox inventory, parametrised by the row it
    yields.

    Generic for the reason its WHM twin is: resolution is written against the narrow view
    above, while a tool that resolves a server then *calls* it needs the credentials off the row
    it resolved — that row, and not a second read by id which could disagree with the list a tie
    was judged against. The parameter holds both at once.
    """

    async def list_servers(self) -> Sequence[RowT_co]: ...

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...
