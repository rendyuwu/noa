"""SQL behind WHM server inventory.

Ported from `noa-old` branch `MCP` (`storage/postgres/whm_servers.py`). One departure, and
it is the same call the earlier ports made: **only the reads are here.**
`create`/`update`/`delete` and the whole `SQLWHMServerTokenRepository` are not ported.

- The write half has exactly one caller in the design — the admin server-CRUD routes — and
  porting it now would land ~150 lines of untested, unreachable code that a reviewer has to
  treat as live (new functionality ships with tests).
- `whm_server_tokens` does not exist in this schema at all. Schema v1 created `whm_servers` only,
  and per-reseller tokens are a spec change rather than a build decision (see
  `docs/integrations/whm.md`, "Not built yet").

`get_by_name` is folded into `resolve_whm_server_ref`'s in-memory matching rather than kept
as a query. `noa-old` had both and used the SQL one nowhere on the tool path: the resolver
already holds the full list to answer the hostname case, and a second round trip that can
disagree with the list it is compared against is a race, not an optimization.

The session belongs to the caller, as everywhere else in `core/` — nothing here commits,
and the two methods are read-only, so a tool that opens a session and never writes closes
it without a transaction to resolve.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import WHMServer


class WHMServerRowLike(Protocol):
    """The `whm_servers` surface the read path needs.

    Narrower than `WHMServerSecretLike` (`core.integrations.whm.ssh`) on purpose: reference
    resolution matches on identity, never on credentials, so a double built for a resolver
    test cannot accidentally be handed to something that would decrypt it.

    `to_safe_dict()` is declared here for the admin view's sake, not this module's: the one
    MCP tool that used to render a row through this Protocol via `to_safe_dict`,
    `whm_list_servers`, answers `core.servers.whm_ref.describe()` now — id, name,
    `base_url`, none of `to_safe_dict`'s admin extras. `WHMServer.to_safe_dict` still
    drops `api_token` and every SSH secret in favour of presence booleans; it is
    just reached directly on the concrete row by the admin routes now
    (`api/routes/admin_servers.py`), not through this Protocol.
    """

    id: UUID
    name: str
    base_url: str

    def to_safe_dict(self) -> dict[str, Any]: ...


# Covariant because both methods return rows and neither accepts one: a repository of
# `WHMServer` is usable wherever a repository of anything `WHMServerRowLike` is wanted.
RowT_co = TypeVar("RowT_co", bound=WHMServerRowLike, covariant=True)


class WHMServerReadRepository(Protocol[RowT_co]):
    """What the server-ref callers need from WHM inventory, parametrised by the row it yields.

    **Generic, and that is what keeps `WHMServerRowLike` narrow**. Reference resolution
    matches on identity and never touches a credential, so it is written against the narrow
    view. But a tool that resolves a server then *calls* it needs the credentials off the row
    it resolved — the row, specifically, and not a second read by id, which could disagree
    with the list a tie was judged against (`whm_ref`).

    The parameter is how both hold at once: `resolve_whm_server_ref` hands back the row type
    the repository yields, so the app wiring can declare `WHMServerReadRepository[WHMServer]`
    and reach the credentials without a cast, while `core/`'s resolution logic still only sees
    id, name and `base_url`. Widening `WHMServerRowLike` instead would let a resolver double
    be handed to something that decrypts.
    """

    async def list_servers(self) -> Sequence[RowT_co]: ...

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...


class SQLWHMServerRepository:
    """`WHMServerReadRepository[WHMServer]` over one `AsyncSession`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_servers(self) -> list[WHMServer]:
        """Every WHM server, ordered by name.

        Ordered in SQL rather than by the caller so the tool result, the admin list and the
        ambiguity `choices` all present the same sequence — an operator picking row three
        out of a candidate list should not find a different row three next time.
        """
        result = await self._session.execute(select(WHMServer).order_by(WHMServer.name.asc()))
        return list(result.scalars().all())

    async def get_by_id(self, server_id: UUID) -> WHMServer | None:
        """One server by primary key, or `None`.

        `None` rather than a raise: the caller is `resolve_whm_server_ref`, and "no server with that
        id" is a `host_not_found` *result* the model can act on, not an exception.
        """
        result = await self._session.execute(select(WHMServer).where(WHMServer.id == server_id))
        return result.scalar_one_or_none()


__all__ = [
    "SQLWHMServerRepository",
    "WHMServerReadRepository",
    "WHMServerRowLike",
]
