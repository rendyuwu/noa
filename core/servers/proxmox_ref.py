"""Operator word → one Proxmox server, or a structured refusal (T27, V18, V21).

Ported from `noa-old` branch `MCP` (`proxmox/server_ref.py`, C13). Error codes are verbatim
(`host_required`, `host_not_found`, `host_ambiguous`), which is why they live in
`core.servers.reference` and are re-exported here rather than restated.

**This is the module the package docstring was waiting for.** T19 and T31 each ported a
per-system resolver and left the question open: is the third one two parameters or a third
shape? It is two parameters, and Proxmox is `whm_ref`'s shape exactly — the host is parsed out
of `base_url`, and a candidate is recognised by id, name and that URL. So T27 extracted
`core.servers.reference` instead of writing a third copy of ~130 lines (V66), and this file is
what a Proxmox row is and nothing else.

`noa-old`'s own version differed from its WHM sibling in one way that is *not* carried: it
answered a resolution whose `server` was typed `object`, so every caller cast. Here the row keeps
its type (T21) — `resolve_proxmox_server_ref` is generic, so a
`ProxmoxServerReadRepository[ProxmoxServer]` hands back a `ProxmoxServer` with its credentials,
which is what `proxmox_reset_vm_password` then builds a client from without a second read by id
that could disagree with the list a tie was judged against.
"""

from __future__ import annotations

from typing import TypeVar

from core.servers.proxmox_repository import ProxmoxServerReadRepository, ProxmoxServerRowLike
from core.servers.reference import (
    # Re-exported rather than re-declared — see `whm_ref` for why.
    ERROR_AMBIGUOUS,
    ERROR_NOT_FOUND,
    ERROR_REQUIRED,
    MAX_CHOICES,
    ServerRefResolution,
    hostname_of,
    required_message,
    resolve_server_ref,
)

# Invariant: `ProxmoxServerRefResolution` both holds a row and is constructed with one.
RowT = TypeVar("RowT", bound=ProxmoxServerRowLike)

# The noun in every message this resolver produces.
SUBJECT = "Proxmox"

MESSAGE_REQUIRED = required_message(SUBJECT)

# One name for the shared resolution type, as in `whm_ref` and `pmg_ref`.
ProxmoxServerRefResolution = ServerRefResolution


def describe(server: ProxmoxServerRowLike) -> dict[str, str]:
    """One candidate, as a `choices` entry.

    Id, name and `base_url` — enough for an operator to recognise an endpoint and then name it
    unambiguously. No `api_token_id` and no secret: this text goes into an LLM transcript
    (V8, V26).
    """
    return {"id": str(server.id), "name": server.name, "base_url": server.base_url}


async def resolve_proxmox_server_ref(
    server_ref: str, *, repository: ProxmoxServerReadRepository[RowT]
) -> ServerRefResolution[RowT]:
    """Resolve `server_ref` to one Proxmox server, or refuse with a named code (V18, V21).

    Never raises for a bad reference: every outcome is a resolution the tool layer turns into a
    structured result, because "I could not tell which server" is information the model can act
    on, while an exception is not (V19). It matters more here than on a READ path — the caller is
    a CHANGE tool, and a resolver that guessed would open an approval card for a machine the
    operator never named.
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
    "ProxmoxServerRefResolution",
    "describe",
    "resolve_proxmox_server_ref",
]
