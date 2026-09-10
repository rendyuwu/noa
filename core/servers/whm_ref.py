"""Operator word → one WHM server, or a structured refusal.

Ported from `noa-old` branch `MCP` (`whm/server_ref.py`, C13).

**Shrunk 2026-08-15 at T27 to what is WHM's alone.** The policy — id, then name, then host, any
tie is `choices`, a well-formed id stops rather than falling through, case-insensitive fallbacks
— moved to `core.servers.reference`, which the package docstring parked at "the third system
shows whether that difference is two parameters or a third shape". Proxmox is this module's
shape exactly, so it was two parameters. Nothing about behaviour changed: the codes, the
messages and the `choices` fields are byte-identical, which is what `test_whm_server_ref.py`
holds unchanged.

What is left here is what a WHM row is: the host lives inside `base_url` rather than in a column
of its own, and a candidate is recognised by id, name and that URL.

**The resolved row keeps its own type**. Matching only ever reads id, name and `base_url` —
the `WHMServerRowLike` bound — but the tools of T20-T26 need the credentials off the row that was
resolved, and re-reading it by id would let a second query disagree with the list the tie was
judged against. So `resolve_whm_server_ref` is generic in the row: hand it a
`WHMServerReadRepository[WHMServer]` and `resolution.server` is a `WHMServer`, credentials
included, with no cast and without widening the narrow view this module works against.
"""

from __future__ import annotations

from typing import TypeVar

from core.servers.reference import (
    # Re-exported rather than re-declared: these are one contract across systems, and two
    # spellings of `host_ambiguous` is how a caller ends up branching on the one that drifted.
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    ServerRefResolution,
    hostname_of,
    required_message,
    resolve_server_ref,
)
from core.servers.whm_repository import WHMServerReadRepository, WHMServerRowLike

# Invariant: `WHMServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=WHMServerRowLike)

# The noun in every message this resolver produces.
SUBJECT = "WHM"

MESSAGE_REQUIRED = required_message(SUBJECT)

# One name for the shared resolution type. An alias rather than a subclass: callers construct it
# in tests and compare instances, and a subclass would make two types that are equal-looking and
# not equal.
WHMServerRefResolution = ServerRefResolution


def describe(server: WHMServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — the three fields that let an operator recognise a server and then
    name it unambiguously. No credentials and no SSH fields: this text goes into an LLM
    transcript.
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


async def resolve_whm_server_ref(
    server_ref: str, *, repository: WHMServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one WHM server, or refuse with a named code.

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not.
    """
    return await resolve_server_ref(
        server_ref,
        repository=repository,
        subject=SUBJECT,
        host_of=lambda server: hostname_of(server.base_url),
        describe=describe,
    )


__all__ = [
    "ERROR_AMBIGUOUS",
    "ERROR_NOT_FOUND",
    "ERROR_REQUIRED",
    "MAX_CHOICES",
    "MESSAGE_REQUIRED",
    "SUBJECT",
    "WHMServerRefResolution",
    "describe",
    "hostname_of",
    "resolve_whm_server_ref",
]
