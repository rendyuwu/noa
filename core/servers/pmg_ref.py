"""Operator word → one PMG server, or a structured refusal.

Ported from `noa-old` branch `MCP` (`pmg/server_ref.py`) — port, never rewrite.

**Shrunk 2026-08-15**, the same move `whm_ref` made in the same change and for the reason
that module's docstring gives: the policy lives in `core.servers.reference` now, and what is left
here is what a PMG row is. Behaviour is unchanged — codes, messages and `choices` fields are
byte-identical, which is what `test_pmg_server_ref.py` holds unchanged.

PMG is SSH-only (the external interface contract), so the host is the bare `ssh_host` column rather
than something parsed out of a URL, and that column is what a candidate is recognised by. That
difference — one accessor and one `choices` field — is the whole of what made three copies look
necessary.

**The resolved row keeps its own type**, as the account search established: matching only ever reads
id, name and `ssh_host` — the `PMGServerRowLike` bound — but the tool then has to *connect* with the
row it resolved, and re-reading it by id would let a second query disagree with the list the tie was
judged against. So `resolve_pmg_server_ref` is generic in the row.
"""

from __future__ import annotations

from typing import TypeVar

from core.servers.pmg_repository import PMGServerReadRepository, PMGServerRowLike
from core.servers.reference import (
    # Re-exported rather than re-declared — see `whm_ref` for why.
    ERROR_AMBIGUOUS as ERROR_AMBIGUOUS,
)
from core.servers.reference import (
    ERROR_NOT_FOUND as ERROR_NOT_FOUND,
)
from core.servers.reference import (
    ERROR_REQUIRED as ERROR_REQUIRED,
)
from core.servers.reference import (
    MAX_CHOICES as MAX_CHOICES,
)
from core.servers.reference import (
    ServerRefResolution,
    required_message,
    resolve_server_ref,
)

# Invariant: `PMGServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=PMGServerRowLike)

# The noun in every message this resolver produces.
SUBJECT = "PMG"

MESSAGE_REQUIRED = required_message(SUBJECT)

# One name for the shared resolution type, as in `whm_ref`.
PMGServerRefResolution = ServerRefResolution


def describe(server: PMGServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `ssh_host` — what lets an operator recognise a node and then name it
    unambiguously. No credentials: this text goes into an LLM transcript.
    """
    return {"id": str(server.id), "name": server.name, "ssh_host": server.ssh_host}


async def resolve_pmg_server_ref(
    server_ref: str, *, repository: PMGServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one PMG server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not.
    """
    return await resolve_server_ref(
        server_ref,
        repository=repository,
        subject=SUBJECT,
        host_of=lambda server: server.ssh_host,
        describe=describe,
    )
