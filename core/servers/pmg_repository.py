"""SQL behind PMG server inventory.

The PMG sibling of `core.servers.whm_repository`, and it makes the same two calls that module
made, for the same reasons.

**Reads only.** `noa-old`'s `storage/postgres/pmg_servers.py` also had `create`/`update`/
`delete`, and their only caller in this design is the admin server-CRUD routes. Porting them now
would land ~130 lines of unreachable code a reviewer has to treat as live.

**`get_by_name` is not a query.** `resolve_pmg_server_ref` already holds the whole list to
answer the host case, and a second round trip that can disagree with the list a tie was judged
against is a race rather than an optimization.

The session belongs to the caller, as everywhere else in `core/`: nothing here commits, and
both methods are read-only, so a tool that opens a session and never writes closes it without
a transaction to resolve.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import PMGServer


class PMGServerRowLike(Protocol):
    """The `pmg_servers` surface reference resolution needs.

    Three columns, and deliberately no credentials: matching is on identity, so a double built
    for a resolver test cannot be handed to something that would decrypt it. That is
    `WHMServerRowLike`'s reasoning, one system over.

    No `to_safe_dict()` either, unlike the WHM view. That method is on the WHM row because
    `whm_list_servers` renders rows into a transcript and needs exactly one sanctioned way to
    do it. Nothing exposed over MCP renders a PMG row — `pmg_list_servers` is an
    internal function (I.mcp) and the whitelist tools answer about CIDRs — so requiring it here
    would be a rule with no rule-breaker to catch.
    """

    id: UUID
    name: str
    ssh_host: str


# Covariant for the reason the WHM one is: both methods return rows and neither accepts one.
RowT_co = TypeVar("RowT_co", bound=PMGServerRowLike, covariant=True)


class PMGServerReadRepository(Protocol[RowT_co]):
    """What the PMG tools need from inventory, parametrised by the row it yields.

    Generic, and that is what keeps `PMGServerRowLike` narrow (the same construction the WHM account
    search gave `WHMServerReadRepository`). Resolution matches on identity and never touches a
    credential, so it is written against the narrow view; a tool that resolves a server then
    *connects* to it needs the SSH columns off the row it resolved — that row, not a second read by
    id, which could disagree with the list the tie was judged against.

    The parameter is how both hold at once: `resolve_pmg_server_ref` hands back the row type the
    repository yields, so the app wiring declares `PMGServerReadRepository[PMGServer]` and reaches
    the credentials with no cast, while `core/`'s resolution logic still sees three columns.
    """

    async def list_servers(self) -> Sequence[RowT_co]: ...

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...


class SQLPMGServerRepository:
    """`PMGServerReadRepository[PMGServer]` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_servers(self) -> list[PMGServer]:
        """Every PMG server, ordered by name.

        Ordered in SQL rather than by the caller so the ambiguity `choices` an operator picks
        from present the same sequence every time — "the second one" should mean the same row
        on the next call.
        """
        result = await self._session.execute(select(PMGServer).order_by(PMGServer.name.asc()))
        return list(result.scalars().all())

    async def get_by_id(self, server_id: UUID) -> PMGServer | None:
        """One server by primary key, or `None`.

        `None` rather than a raise: "no server with that id" is a `host_not_found` *result* the
        model can act on, not an exception.
        """
        result = await self._session.execute(select(PMGServer).where(PMGServer.id == server_id))
        return result.scalar_one_or_none()


__all__ = [
    "PMGServerReadRepository",
    "PMGServerRowLike",
    "SQLPMGServerRepository",
]
