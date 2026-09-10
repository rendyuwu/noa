"""SQL behind Proxmox server inventory.

The third of these, after `whm_repository.py` and `pmg_repository.py`, and written
the same way for the same reasons — so the note here is only about what differs.

**Reads only.** `create`/`update`/`delete` belong to the admin server-CRUD routes and are not
ported; landing them now would be untested, unreachable code a reviewer has to treat as live.

**A narrower row than WHM's.** Proxmox is an HTTP API and nothing else (I.ext), so
`ProxmoxServerRowLike` carries `id`, `name` and `base_url` — the three fields reference
resolution matches on — and no SSH surface exists on the table to leave out.

`get_by_name` is not a query here either: `resolve_proxmox_server_ref` already holds the full
list to answer the hostname pass, and a second round trip that can disagree with the list a tie
was judged against is a race rather than an optimization (`whm_repository`'s note).

The session belongs to the caller, as everywhere in `core/` — nothing here commits, and both
methods are `SELECT`s, so a tool that opens a session and never writes closes it with no
transaction to resolve.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import ProxmoxServer


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


class SQLProxmoxServerRepository:
    """`ProxmoxServerReadRepository[ProxmoxServer]` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_servers(self) -> list[ProxmoxServer]:
        """Every Proxmox server, ordered by name.

        Ordered in SQL rather than by the caller so the tool result, the admin list and the
        ambiguity `choices` all present one sequence — an operator picking row three out of a
        candidate list should not find a different row three next time.
        """
        result = await self._session.execute(
            select(ProxmoxServer).order_by(ProxmoxServer.name.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, server_id: UUID) -> ProxmoxServer | None:
        """One server by primary key, or `None`.

        `None` rather than a raise: the caller is `resolve_proxmox_server_ref`, and "no server with
        that id" is a `host_not_found` *result* the model can act on, not an exception.
        """
        result = await self._session.execute(
            select(ProxmoxServer).where(ProxmoxServer.id == server_id)
        )
        return result.scalar_one_or_none()


__all__ = [
    "ProxmoxServerReadRepository",
    "ProxmoxServerRowLike",
    "SQLProxmoxServerRepository",
]
