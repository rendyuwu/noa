"""The PMG inventory surface the whitelist tools read against.

The PMG sibling of `core.servers.whm_repository`, and what it holds is the same pair: a narrow
row view and the repository Protocol over it. The SQL is one generic class in
`core.servers.repository`, the writes are in `core.servers.admin_repository`, and what is left
here is the part that is PMG's alone — an `ssh_host` column where WHM has a `base_url`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar
from uuid import UUID


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
