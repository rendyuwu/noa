"""SQL behind WHM server inventory (T19, C13).

Ported from `noa-old` branch `MCP` (`storage/postgres/whm_servers.py`). One departure, and
it is the same call T16 (e) and T17 (a) made: **only the reads are here.**
`create`/`update`/`delete` and the whole `SQLWHMServerTokenRepository` are not ported.

- The write half has exactly one caller in the design — the admin routes of T54 — and
  porting it now would land ~150 lines of untested, unreachable code that a reviewer has to
  treat as live (V67: tests for new functionality).
- `whm_server_tokens` does not exist in this schema at all. T4 created `whm_servers` only,
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
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.db.models import WHMServer


class WHMServerRowLike(Protocol):
    """The `whm_servers` surface the read path needs.

    Narrower than `WHMServerSecretLike` (`core.integrations.whm.ssh`) on purpose: reference
    resolution matches on identity, never on credentials, so a double built for a resolver
    test cannot accidentally be handed to something that would decrypt it.

    `to_safe_dict()` is included because it is the *only* sanctioned way to render a row
    outward: `WHMServer.to_safe_dict` drops `api_token` and every SSH secret, replacing them
    with presence booleans (V2, V8). Requiring it here means a tool cannot serialize a row
    any other way without changing this Protocol first.
    """

    id: UUID
    name: str
    base_url: str

    def to_safe_dict(self) -> dict[str, Any]: ...


class WHMServerReadRepository(Protocol):
    """What T19's callers need from WHM inventory."""

    async def list_servers(self) -> Sequence[WHMServerRowLike]: ...

    async def get_by_id(self, server_id: UUID) -> WHMServerRowLike | None: ...


class SQLWHMServerRepository:
    """`WHMServerReadRepository` over one `AsyncSession`."""

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

        `None` rather than a raise: the caller is `resolve_whm_server_ref`, and "no server
        with that id" is a `host_not_found` *result* the model can act on (V18), not an
        exception.
        """
        result = await self._session.execute(select(WHMServer).where(WHMServer.id == server_id))
        return result.scalar_one_or_none()


__all__ = [
    "SQLWHMServerRepository",
    "WHMServerReadRepository",
    "WHMServerRowLike",
]
