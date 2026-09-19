"""The WHM inventory surface reference resolution and the tools read against.

A Protocol pair and no SQL. The `SELECT`s were byte-identical across the three inventory
tables and live in `core.servers.repository` now; the writes are in
`core.servers.admin_repository`. What is per-system is the *row view* — `base_url` here,
`ssh_host` one table over — and that is what stays, because it is what keeps a resolver double
from being handed to something that decrypts.

`whm_server_tokens` does not exist in this schema at all. Schema v1 created `whm_servers` only,
and per-reseller tokens are a spec change rather than a build decision (see
`docs/integrations/whm.md`, "Not built yet").
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol, TypeVar
from uuid import UUID


class WHMServerRowLike(Protocol):
    """The `whm_servers` surface the read path needs.

    Narrower than `WHMServerSecretLike` (`core.integrations.whm.ssh`) on purpose: reference
    resolution matches on identity, never on credentials, so a double built for a resolver
    test cannot accidentally be handed to something that would decrypt it.

    `to_safe_dict()` is declared here for the admin view's sake, not this module's: the one
    MCP tool that used to render a row through this Protocol via `to_safe_dict`,
    `whm_list_servers`, answers `core.servers.reference.describe_whm()` now — id, name,
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
    with the list a tie was judged against (`core.servers.reference`).

    The parameter is how both hold at once: `resolve_whm_server_ref` hands back the row type
    the repository yields, so the app wiring can declare `WHMServerReadRepository[WHMServer]`
    and reach the credentials without a cast, while `core/`'s resolution logic still only sees
    id, name and `base_url`. Widening `WHMServerRowLike` instead would let a resolver double
    be handed to something that decrypts.
    """

    async def list_servers(self) -> Sequence[RowT_co]: ...

    async def get_by_id(self, server_id: UUID) -> RowT_co | None: ...
